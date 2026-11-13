import os
import re
import time
import ctypes
import random
import requests
import shutil
import asyncio
import threading
import tempfile
import edge_tts
from concurrent.futures import (
    FIRST_COMPLETED, Future, ThreadPoolExecutor, wait,
)
from . import perf
from .tts_logger import logger
from .tts_profile import api_cache_model
from .local_voice_client import is_local_service, generate_local_tasks

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# 复用HTTP会话（线程本地）：requests.Session 并非线程安全，
# 批量下载（5 线程）与多 API worker 并发共用同一 Session 会竞争
# 底层连接池，高并发下可能出现连接被并发使用导致的异常。
_thread_local = threading.local()


def _get_session() -> requests.Session:
    """返回当前线程专属的 requests.Session（惰性创建）。"""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


# ---- 同键 in-flight 合并（P1-3）-------------------------------------------
# 同一批次内相同缓存键（文本+声音+参数）并发请求时只发一次，后来者等
# 先行者落缓存后直接复用。先行者异常只结束等待、不传播给等待者——等待者
# 自行走完整生成路径，保证失败不互相拖死。
_inflight_lock = threading.Lock()
_inflight = {}  # inflight_key -> Future


def _inflight_enter(key):
    """登记 in-flight 键。返回 (future, is_owner)。"""
    with _inflight_lock:
        fut = _inflight.get(key)
        if fut is not None:
            return fut, False
        fut = Future()
        _inflight[key] = fut
        return fut, True


def _inflight_exit(key, fut):
    """先行者结束：摘除登记并唤醒等待者（无论成败都不抛异常）。"""
    with _inflight_lock:
        _inflight.pop(key, None)
    if not fut.done():
        fut.set_result(None)


def _inflight_wait(key, fut, cache_probe):
    """等待先行者完成后查询其缓存产物。

    返回缓存路径表示可复用；None 表示需自行生成
    （先行者失败 / 等待超时 / 缓存未落盘）。
    """
    try:
        fut.result(timeout=120)
    except Exception:
        pass
    perf.COUNTERS.incr("dedup_merged")
    return cache_probe()


def _retry_backoff(attempt):
    """重试退避 0.5/1/2 秒 + 25% 抖动，避免限流时多线程同步撞车。"""
    delay = min(0.5 * (2 ** (attempt - 1)), 2.0)
    return delay + random.uniform(0.0, delay * 0.25)


def _safe_unlink(path):
    """尽力删除临时文件，失败仅记日志不抛出。"""
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception as e:
        logger.warning(f"临时文件删除失败: {path} - {e}")


# ---------- Edge TTS 全局并发限流 ----------
# 多个任务并行合成时（如流水线模式），每个 TTSWorker 内部又有多个线程并发，
# 总并发可能叠加到几十路请求，触发微软服务限流
# （Cannot connect to host speech.platform.bing.com:443）。
# 用条件变量把全局并发请求数限制在上限之内。
_edge_lock = threading.Lock()
_edge_cond = threading.Condition(_edge_lock)
_edge_active = 0
_edge_max_concurrent = 8


def set_edge_max_concurrent(n):
    """设置 Edge TTS 全局并发上限（由 TTSWorker 每次运行前调用）。"""
    global _edge_max_concurrent
    _edge_max_concurrent = max(1, int(n))


def _edge_acquire():
    global _edge_active
    with _edge_cond:
        while _edge_active >= _edge_max_concurrent:
            _edge_cond.wait()
        _edge_active += 1


def _edge_release():
    global _edge_active
    with _edge_cond:
        _edge_active -= 1
        _edge_cond.notify_all()


# Edge TTS 全局共享线程池：容量 = 全局并发上限。
# 与声道检测 _get_pan_detect_executor 同理：流水线/多音频并行时，
# 若每个音频各建一个 max_workers=edge_threads 的线程池，线程数会相乘叠加
# （音频数 × 线程数），而真正并发又被全局上限卡死，造成大量线程空转。
# 改为所有音频共用同一个池，池容量即并发上限，无叠加、负载天然均衡。
_edge_executor = None
_edge_executor_workers = 0


def get_edge_executor(max_workers):
    """返回按容量复用的共享线程池；容量变化时销毁重建（不等待旧任务）。"""
    global _edge_executor, _edge_executor_workers
    workers = max(1, int(max_workers))
    if _edge_executor is None or _edge_executor_workers != workers:
        if _edge_executor is not None:
            _edge_executor.shutdown(wait=False)
        _edge_executor = ThreadPoolExecutor(max_workers=workers)
        _edge_executor_workers = workers
    return _edge_executor


# ---------- Edge TTS 合成端点兼容 ----------
# 2026-10 起微软拒绝 speech.platform.bing.com 上的 WSS 合成请求
# （403 Invalid response status），Edge 浏览器改走 api.msedgeservices.com
# （edge-tts 官方修复 rany2/edge-tts#491 尚未发布新版本）。
# 官方修复落地前运行时改指向新主机；检测到库已在使用新主机时不覆盖，
# 官方版本发布后本补丁自动失效，无需改动。
_EDGE_WSS_LEGACY_HOST = "speech.platform.bing.com"
_EDGE_WSS_NEW_HOST = "api.msedgeservices.com"


def patch_edge_wss_endpoint():
    """把 edge-tts 的 WSS 合成端点改指向微软新主机；幂等，可重复调用。"""
    import edge_tts.communicate as edge_communicate
    from edge_tts.constants import TRUSTED_CLIENT_TOKEN
    current = getattr(edge_communicate, "WSS_URL", "")
    if _EDGE_WSS_LEGACY_HOST in current:
        edge_communicate.WSS_URL = (
            f"wss://{_EDGE_WSS_NEW_HOST}/tts/cognitiveservices/websocket/v1"
            f"?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}"
        )
    return edge_communicate.WSS_URL


patch_edge_wss_endpoint()


def set_sleep_mode(prevent=True):
    if os.name == "nt":
        try:
            if prevent:
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                )
                logger.info("防休眠已启用")
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                logger.info("防休眠已关闭")
        except Exception as e:
            logger.error(f"防休眠设置失败: {e}")


# 片段文件扩展名白名单。Edge 产物是 MP3 内容 → 新命名 .mp3；
# 存量与 API 路径保留 .wav 命名（见 P1-2 格式收口）。
CLIP_EXTS = (".wav", ".mp3")


def generate_filename(index, timestamp, text, save_dir, ext=".wav"):
    if ext not in CLIP_EXTS:
        ext = ".wav"
    safe_text = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text)
    short_text = safe_text[:2] if safe_text else "无"
    safe_timestamp = timestamp.replace(":", "-")
    file_name = f"{index:04d}_{safe_timestamp}_{short_text}{ext}"
    return os.path.join(save_dir, file_name)


def find_existing_clip(save_path):
    """返回同名 stem 下任一扩展名已存在且非空的片段路径，都没有则 None。

    跳过「已存在文件」时兼容存量混杂期：旧命名 .wav（实为 MP3）与新命名
    .mp3 指向同一片段，命中任一都应跳过，避免重跑生成重复片段。
    """
    stem = save_path
    for ext in CLIP_EXTS:
        if stem.endswith(ext):
            stem = stem[:-len(ext)]
            break
    for ext in CLIP_EXTS:
        candidate = stem + ext
        try:
            if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                return candidate
        except OSError:
            continue
    return None


def tts_task(index, timestamp, text, api_url, model_name, save_dir, audio_cache, source_mtime=0, model_tag="", cancel_check=None, workflow_context=None):
    save_path = generate_filename(index, timestamp, text, save_dir)
    file_name = os.path.basename(save_path)

    source_version = str(int(source_mtime))
    # 缓存键带上模型标签：同一 URL 上换了模型时，靠 model_tag 区分，
    # 否则第二个模型会直接命中第一个模型的缓存音频
    cache_model = api_cache_model(api_url, model_name, model_tag)
    cached_path = audio_cache.get_cached_audio(text, cache_model, source_version)
    if cached_path:
        try:
            with perf.span("缓存命中"):
                shutil.copy2(cached_path, save_path)
            logger.debug(f"缓存复用: {file_name}")
            return True, f"缓存复用: {file_name}"
        except Exception as e:
            logger.warning(f"缓存复制失败: {file_name} - {e}")

    if is_local_service(_get_session(), api_url):
        task = (index, timestamp, text, save_dir, source_mtime)
        return generate_local_tasks(_get_session(), api_url, model_name, [task],
                                    audio_cache, cache_model, generate_filename, _download_audio,
                                    cancel_check=cancel_check, workflow_context=workflow_context)[0]

    api_base_url = api_url.rstrip("/")
    api_endpoint = f"{api_base_url}/infer_single"

    # 同键 in-flight 合并（P1-3）：等先行者产出缓存后直接复用；
    # 先行者失败时重新竞争，同键补偿请求同一时刻最多 1 个
    inflight_key = f"api|{cache_model}|{source_version}|{text}"
    while True:
        future, owner = _inflight_enter(inflight_key)
        if owner:
            break
        cached_path = _inflight_wait(inflight_key, future, lambda: audio_cache.get_cached_audio(text, cache_model, source_version))
        if cached_path:
            try:
                with perf.span("缓存命中"):
                    shutil.copy2(cached_path, save_path)
                logger.debug(f"同键合并复用: {file_name}")
                return True, f"缓存复用: {file_name}"
            except Exception as e:
                logger.warning(f"缓存复制失败: {file_name} - {e}")

    payload = {
        "batch_size": 10,
        "batch_threshold": 0.75,
        "dl_url": api_base_url,
        "emotion": "默认",
        "fragment_interval": 0.3,
        "if_sr": False,
        "media_type": "wav",
        "model_name": model_name,
        "parallel_infer": True,
        "prompt_text_lang": "中文",
        "repetition_penalty": 1.35,
        "sample_steps": 16,
        "seed": -1,
        "speed_facter": 1,
        "split_bucket": True,
        "text": text,
        "text_lang": "中文",
        "text_split_method": "按标点符号切",
        "top_k": 10,
        "top_p": 1,
        "version": "v4",
    }

    try:
        logger.debug(f"调用API: {api_endpoint}, 文本: {text[:20]}...")
        perf.COUNTERS.incr("api_requests")
        with perf.span("TTS请求"):
            resp = _get_session().post(api_endpoint, json=payload, timeout=60)
            resp.raise_for_status()
            res_data = resp.json()
        if "audio_url" in res_data:
            audio_url = res_data["audio_url"]
            _download_audio(audio_url, save_path)
            audio_cache.save_audio_cache(text, save_path, cache_model, api_url, source_version=source_version)
            logger.info(f"完成: {file_name}")
            return True, f"完成: {file_name}"
        logger.warning(f"API返回错误: {res_data.get('msg', '无返回URL')}")
        return False, f"错误: {res_data.get('msg', '无返回URL')}"
    except requests.exceptions.Timeout:
        logger.error(f"API请求超时: {file_name}")
        return False, f"超时: {file_name}"
    except requests.exceptions.ConnectionError:
        logger.error(f"API连接失败: {api_endpoint}")
        return False, f"连接失败: {api_endpoint}"
    except Exception as e:
        logger.error(f"API调用异常: {file_name} - {e}", exc_info=True)
        return False, f"异常: {str(e)}"
    finally:
        if owner:
            _inflight_exit(inflight_key, future)


def _download_audio(audio_url, save_path, timeout=60):
    temp_path = None
    try:
        with perf.span("TTS请求"):
            with _get_session().get(audio_url, timeout=timeout, stream=True) as audio_resp:
                audio_resp.raise_for_status()
                with tempfile.NamedTemporaryFile(dir=os.path.dirname(save_path),
                                                 prefix=".tts-", suffix=".wav", delete=False) as tmp:
                    temp_path = tmp.name
                    for chunk in audio_resp.iter_content(chunk_size=262144):
                        if chunk:
                            tmp.write(chunk)
        if os.path.getsize(temp_path) == 0:
            raise ValueError("服务器返回空音频")
        os.replace(temp_path, save_path)
    finally:
        _safe_unlink(temp_path)


def test_api_server(url, timeout=5):
    """测试 API 服务器是否可用。返回 (ok, msg)。"""
    api_base_url = url.rstrip("/")
    try:
        resp = _get_session().get(api_base_url, timeout=timeout)
        if resp.status_code == 200:
            return True, "连接成功"
        return False, f"HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return False, "连接超时"
    except requests.exceptions.ConnectionError:
        return False, "连接失败"
    except Exception as e:
        return False, f"异常: {e}"


class BulkRequestCancelled(Exception):
    """批量请求被用户停止（结果语义 = 已停止，不是失败）。"""


def _post_bulk_with_cancel(endpoint, payload, cancel_check=None, timeout=300, poll=0.25):
    """带取消缝的批量 POST（P2-2）。

    远程 /infer_bulk 无取消端点，在途请求无法撤回：与其等满 timeout，
    不如把在途请求丢给守护线程随进程消亡，调用方按 poll 粒度检测停止
    信号后立即返回「已停止」。无半成品缓存写入风险——响应被废弃，
    缓存写入只发生在正常返回路径。
    """
    if cancel_check is not None and cancel_check():
        raise BulkRequestCancelled()

    box = {}
    done = threading.Event()

    def _run():
        try:
            box["resp"] = _get_session().post(endpoint, json=payload, timeout=timeout)
        except Exception as exc:
            box["exc"] = exc
        finally:
            done.set()

    runner = threading.Thread(target=_run, daemon=True)
    runner.start()
    while not done.wait(poll):
        if cancel_check is not None and cancel_check():
            raise BulkRequestCancelled()
    if "exc" in box:
        raise box["exc"]
    return box["resp"]


def tts_bulk_task(tasks, api_url, model_name, audio_cache, model_tag="", cancel_check=None, workflow_context=None):
    results = [None] * len(tasks)

    uncached_tasks = []
    uncached_indices = []

    cache_model = api_cache_model(api_url, model_name, model_tag)

    for i, (idx, timestamp, text, save_dir, file_mtime) in enumerate(tasks):
        save_path = generate_filename(idx, timestamp, text, save_dir)
        file_name = os.path.basename(save_path)

        source_version = str(int(file_mtime))
        cached_path = audio_cache.get_cached_audio(text, cache_model, source_version)
        if cached_path:
            try:
                with perf.span("缓存命中"):
                    shutil.copy2(cached_path, save_path)
                results[i] = (True, f"缓存复用: {file_name}")
                continue
            except Exception as e:
                logger.warning(f"缓存复制失败: {file_name} - {e}")

        uncached_tasks.append(tasks[i])
        uncached_indices.append(i)

    if not uncached_tasks:
        return results

    if is_local_service(_get_session(), api_url):
        local_results = generate_local_tasks(_get_session(), api_url, model_name, uncached_tasks,
                                             audio_cache, cache_model, generate_filename, _download_audio,
                                             cancel_check=cancel_check, workflow_context=workflow_context)
        for index, result in zip(uncached_indices, local_results):
            results[index] = result
        return results

    api_base_url = api_url.rstrip("/")
    api_endpoint = f"{api_base_url}/infer_bulk"

    # 同键合并（P1-3）：同批相同文本+版本只发一次请求，其余复用下载结果
    unique_first = []   # 每个唯一键在 uncached_tasks 中的首个下标
    dup_groups = {}     # 首个下标 -> 其余同键下标
    seen_keys = {}
    for i, task in enumerate(uncached_tasks):
        dedup_key = (task[2], str(int(task[4])))
        first = seen_keys.get(dedup_key)
        if first is None:
            seen_keys[dedup_key] = i
            unique_first.append(i)
        else:
            dup_groups.setdefault(first, []).append(i)
            perf.COUNTERS.incr("dedup_merged")
    if len(unique_first) < len(uncached_tasks):
        logger.info(f"批量同键合并: {len(uncached_tasks)} 条文本 -> {len(unique_first)} 条请求")

    texts = [uncached_tasks[i][2] for i in unique_first]

    payload = {
        "batch_size": 30,
        "batch_threshold": 0.75,
        "dl_url": api_base_url,
        "emotion": "默认",
        "fragment_interval": 0.3,
        "if_sr": False,
        "media_type": "wav",
        "model_name": model_name,
        "parallel_infer": True,
        "prompt_text_lang": "中文",
        "repetition_penalty": 1.35,
        "sample_steps": 16,
        "seed": -1,
        "speed_facter": 1,
        "split_bucket": True,
        "texts": texts,
        "text_lang": "中文",
        "text_split_method": "按标点符号切",
        "top_k": 10,
        "top_p": 1,
        "version": "v4",
    }

    try:
        logger.debug(f"批量API请求: {api_endpoint}, 文本数: {len(texts)}")
        perf.COUNTERS.incr("api_requests")
        with perf.span("TTS请求"):
            resp = _post_bulk_with_cancel(api_endpoint, payload, cancel_check)
            resp.raise_for_status()
            res_data = resp.json()

        if "audio_urls" in res_data:
            audio_urls = res_data["audio_urls"]
            # 并行下载音频（可取消：停止后不再等在途下载排空）
            download_futures = {}
            executor = ThreadPoolExecutor(max_workers=5)
            cancelled_during_download = False
            try:
                for i, audio_url in zip(unique_first, audio_urls):
                    idx, timestamp, text, save_dir, _ = uncached_tasks[i]
                    save_path = generate_filename(idx, timestamp, text, save_dir)
                    # wrap 把当前线程的 PerfTimer 带进下载线程，TTS请求耗时不丢账
                    future = executor.submit(perf.wrap(_download_audio), audio_url, save_path)
                    download_futures[future] = (i, save_path, text)

                pending_futures = set(download_futures)
                while pending_futures:
                    # wait 超时轮询：全部下载都在途时也能按 poll 粒度看到停止信号，
                    # 不像 as_completed 要等首个完成才回到循环头。
                    if cancel_check is not None and cancel_check():
                        cancelled_during_download = True
                        for pending in pending_futures:
                            pending.cancel()
                        break
                    done_set, pending_futures = wait(
                        pending_futures, timeout=0.25,
                        return_when=FIRST_COMPLETED)
                    for future in done_set:
                        i, save_path, text = download_futures[future]
                        idx, timestamp, _, save_dir, file_mtime = uncached_tasks[i]
                        file_name = os.path.basename(save_path)
                        source_version = str(int(file_mtime))
                        try:
                            future.result()
                            audio_cache.save_audio_cache(text, save_path, cache_model, api_url, source_version=source_version)
                            results[uncached_indices[i]] = (True, f"批量完成: {file_name}")
                            # 同键组复用同一份下载产物
                            for j in dup_groups.get(i, ()):
                                dup_task = uncached_tasks[j]
                                dup_path = generate_filename(dup_task[0], dup_task[1], dup_task[2], dup_task[3])
                                try:
                                    shutil.copy2(save_path, dup_path)
                                    results[uncached_indices[j]] = (True, f"批量完成: {os.path.basename(dup_path)}")
                                except Exception as e:
                                    logger.error(f"同键复制失败: {dup_path} - {e}")
                                    results[uncached_indices[j]] = (False, f"复制异常: {os.path.basename(dup_path)} - {str(e)}")
                        except Exception as e:
                            logger.error(f"下载音频失败: {file_name} - {e}")
                            results[uncached_indices[i]] = (False, f"下载异常: {file_name} - {str(e)}")
                            for j in dup_groups.get(i, ()):
                                results[uncached_indices[j]] = (False, f"下载异常: {file_name} - {str(e)}")
            finally:
                executor.shutdown(wait=not cancelled_during_download,
                                  cancel_futures=cancelled_during_download)

            if cancelled_during_download:
                logger.info("批量下载已停止")
                for i in uncached_indices:
                    if results[i] is None:
                        results[i] = (False, "已停止")
                return results

            # 服务器未返回音频/未处理的同键任务补标记，避免 results 中出现 None
            for i in range(len(uncached_tasks)):
                if results[uncached_indices[i]] is None:
                    results[uncached_indices[i]] = (False, "服务器未返回音频")
        else:
            error_msg = res_data.get('msg', '无返回URL')
            logger.warning(f"批量API无返回URL: {error_msg}, 回退到逐条模式")
            for i in uncached_indices:
                idx, timestamp, text, save_dir, file_mtime = tasks[i]
                success, msg = tts_task(idx, timestamp, text, api_url, model_name, save_dir, audio_cache, int(file_mtime), model_tag)
                results[i] = (success, msg)
    except BulkRequestCancelled:
        logger.info("批量API请求已停止")
        for i in uncached_indices:
            if results[i] is None:
                results[i] = (False, "已停止")
        return results
    except requests.exceptions.ConnectionError:
        logger.error(f"批量API连接失败: {api_endpoint}, 回退到逐条模式")
        for i, task in zip(uncached_indices, uncached_tasks):
            idx, timestamp, text, save_dir, file_mtime = task
            success, msg = tts_task(idx, timestamp, text, api_url, model_name, save_dir, audio_cache, int(file_mtime), model_tag)
            results[i] = (success, msg)
    except Exception as e:
        logger.error(f"批量API异常: {e}", exc_info=True)
        for i, task in zip(uncached_indices, uncached_tasks):
            idx, timestamp, text, save_dir, file_mtime = task
            save_path = generate_filename(idx, timestamp, text, save_dir)
            file_name = os.path.basename(save_path)
            results[i] = (False, f"批量异常: {file_name} - {str(e)}")

    return results


async def _edge_tts_generate(text, voice, rate, volume, save_path):
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(save_path)


def edge_tts_task(index, timestamp, text, voice, rate, volume, save_dir, audio_cache, source_mtime=0, max_retries=3):
    # Edge 产物是 MP3 内容，按真实格式命名为 .mp3（P1-2 格式收口）
    save_path = generate_filename(index, timestamp, text, save_dir, ext=".mp3")
    file_name = os.path.basename(save_path)

    source_version = str(int(source_mtime))
    cache_key = f"{text}|{voice}|{rate}|{volume}"
    cached_path = audio_cache.get_cached_audio(cache_key, "edge_tts", source_version)
    if cached_path:
        try:
            with perf.span("缓存命中"):
                shutil.copy2(cached_path, save_path)
            logger.debug(f"Edge TTS缓存复用: {file_name}")
            return True, f"缓存复用: {file_name}"
        except Exception as e:
            logger.warning(f"Edge TTS缓存复制失败: {file_name} - {e}")

    # 同键 in-flight 合并（P1-3）：等先行者产出缓存后直接复用；
    # 先行者失败时重新竞争而不是一拥而上，同键补偿请求同一时刻最多 1 个
    inflight_key = f"edge|{source_version}|{cache_key}"
    while True:
        future, owner = _inflight_enter(inflight_key)
        if owner:
            break
        cached_path = _inflight_wait(inflight_key, future, lambda: audio_cache.get_cached_audio(cache_key, "edge_tts", source_version))
        if cached_path:
            try:
                shutil.copy2(cached_path, save_path)
                logger.debug(f"Edge TTS同键合并复用: {file_name}")
                return True, f"缓存复用: {file_name}"
            except Exception as e:
                logger.warning(f"Edge TTS缓存复制失败: {file_name} - {e}")

    last_error = None
    # 占用全局并发额度（等待直到并发低于上限），整个片段（含重试）占用一个额度
    _edge_acquire()
    try:
        for attempt in range(1, max_retries + 1):
            # 写入 .part 临时名再原子改名：中途失败的残留文件不会被
            # mix 扫描（parse_filename）误认为正式片段。
            temp_path = save_path + ".part"
            try:
                logger.debug(f"Edge TTS生成 (尝试 {attempt}/{max_retries}): 声音={voice}, 文本={text[:20]}...")
                perf.COUNTERS.incr("wss_requests")
                with perf.span("TTS请求"):
                    asyncio.run(asyncio.wait_for(
                        _edge_tts_generate(text, voice, rate, volume, temp_path),
                        timeout=15
                    ))
                audio_cache.save_audio_cache(cache_key, temp_path, "edge_tts", "edge_tts", ext=".mp3", source_version=source_version)
                os.replace(temp_path, save_path)
                logger.info(f"Edge TTS完成: {file_name}")
                return True, f"完成: {file_name}"
            except asyncio.TimeoutError:
                last_error = "超时"
                logger.warning(f"Edge TTS超时 (尝试 {attempt}/{max_retries}): {file_name}")
                # 超时取消时 save() 可能已创建部分文件，清理后重试
                _safe_unlink(temp_path)
            except edge_tts.exceptions.NoAudioReceived:
                last_error = (f"微软声音 {voice} 未返回音频（NoAudioReceived），"
                              "请确认声音可用；可降低并发后重试")
                logger.warning(f"Edge TTS无音频 (尝试 {attempt}/{max_retries}): {file_name} - {last_error}")
                _safe_unlink(temp_path)
            except Exception as e:
                last_error = str(e)
                if "403" in last_error or "Invalid response status" in last_error:
                    last_error += ("（微软合成端点拒绝请求：请确认系统时钟准确，"
                                   "或更换网络环境/代理后重试；持续失败请更新本程序）")
                logger.warning(f"Edge TTS异常 (尝试 {attempt}/{max_retries}): {file_name} - {last_error}")
                _safe_unlink(temp_path)
            if attempt < max_retries:
                time.sleep(_retry_backoff(attempt))

        logger.error(f"Edge TTS失败 (已重试{max_retries}次): {file_name} - {last_error}")
        return False, f"失败(已重试{max_retries}次): {last_error}"
    finally:
        _edge_release()
        if owner:
            _inflight_exit(inflight_key, future)
