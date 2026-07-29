import os
import re
import time
import ctypes
import hashlib
import requests
import shutil
import asyncio
import edge_tts
from concurrent.futures import ThreadPoolExecutor, as_completed
from .tts_logger import logger

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

# 复用HTTP会话
_session = requests.Session()


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


def generate_filename(index, timestamp, text, save_dir):
    safe_text = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text)
    short_text = safe_text[:2] if safe_text else "无"
    safe_timestamp = timestamp.replace(":", "-")
    file_name = f"{index:04d}_{safe_timestamp}_{short_text}.wav"
    return os.path.join(save_dir, file_name)


def tts_task(index, timestamp, text, api_url, model_name, save_dir, audio_cache, source_mtime=0):
    save_path = generate_filename(index, timestamp, text, save_dir)
    file_name = os.path.basename(save_path)

    source_version = str(int(source_mtime))
    cached_path = audio_cache.get_cached_audio(text, model_name, source_version)
    if cached_path:
        try:
            shutil.copy2(cached_path, save_path)
            logger.debug(f"缓存复用: {file_name}")
            return True, f"缓存复用: {file_name}"
        except Exception as e:
            logger.warning(f"缓存复制失败: {file_name} - {e}")

    api_base_url = api_url.rstrip("/")
    api_endpoint = f"{api_base_url}/infer_single"

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
        resp = _session.post(api_endpoint, json=payload, timeout=60)
        resp.raise_for_status()
        res_data = resp.json()
        if "audio_url" in res_data:
            audio_url = res_data["audio_url"]
            audio_resp = _session.get(audio_url)
            with open(save_path, "wb") as f:
                f.write(audio_resp.content)

            audio_cache.save_audio_cache(text, save_path, model_name, api_url, source_version=source_version)
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


def _download_audio(audio_url, save_path, timeout=60):
    audio_resp = _session.get(audio_url, timeout=timeout)
    audio_resp.raise_for_status()
    with open(save_path, "wb") as f:
        f.write(audio_resp.content)


def tts_bulk_task(tasks, api_url, model_name, audio_cache):
    results = [None] * len(tasks)

    uncached_tasks = []
    uncached_indices = []

    for i, (idx, timestamp, text, save_dir, file_mtime) in enumerate(tasks):
        save_path = generate_filename(idx, timestamp, text, save_dir)
        file_name = os.path.basename(save_path)

        source_version = str(int(file_mtime))
        cached_path = audio_cache.get_cached_audio(text, model_name, source_version)
        if cached_path:
            try:
                shutil.copy2(cached_path, save_path)
                results[i] = (True, f"缓存复用: {file_name}")
                continue
            except Exception as e:
                logger.warning(f"缓存复制失败: {file_name} - {e}")

        uncached_tasks.append(tasks[i])
        uncached_indices.append(i)

    if not uncached_tasks:
        return results

    api_base_url = api_url.rstrip("/")
    api_endpoint = f"{api_base_url}/infer_bulk"

    texts = [t[2] for t in uncached_tasks]

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
        resp = _session.post(api_endpoint, json=payload, timeout=300)
        resp.raise_for_status()
        res_data = resp.json()

        if "audio_urls" in res_data:
            audio_urls = res_data["audio_urls"]
            # 并行下载音频
            download_futures = {}
            with ThreadPoolExecutor(max_workers=5) as executor:
                for i, (task, audio_url) in enumerate(zip(uncached_tasks, audio_urls)):
                    idx, timestamp, text, save_dir = task
                    save_path = generate_filename(idx, timestamp, text, save_dir)
                    future = executor.submit(_download_audio, audio_url, save_path)
                    download_futures[future] = (i, save_path, text)

                for future in as_completed(download_futures):
                    i, save_path, text = download_futures[future]
                    idx, timestamp, _, save_dir, file_mtime = uncached_tasks[i]
                    file_name = os.path.basename(save_path)
                    source_version = str(int(file_mtime))
                    try:
                        future.result()
                        audio_cache.save_audio_cache(text, save_path, model_name, api_url, source_version=source_version)
                        results[uncached_indices[i]] = (True, f"批量完成: {file_name}")
                    except Exception as e:
                        logger.error(f"下载音频失败: {file_name} - {e}")
                        results[uncached_indices[i]] = (False, f"下载异常: {file_name} - {str(e)}")
        else:
            error_msg = res_data.get('msg', '无返回URL')
            logger.warning(f"批量API无返回URL: {error_msg}, 回退到逐条模式")
            for i in uncached_indices:
                idx, timestamp, text, save_dir = tasks[i]
                success, msg = tts_task(idx, timestamp, text, api_url, model_name, save_dir, audio_cache)
                results[i] = (success, msg)
    except requests.exceptions.ConnectionError:
        logger.error(f"批量API连接失败: {api_endpoint}, 回退到逐条模式")
        for i, task in zip(uncached_indices, uncached_tasks):
            idx, timestamp, text, save_dir = task
            success, msg = tts_task(idx, timestamp, text, api_url, model_name, save_dir, audio_cache)
            results[i] = (success, msg)
    except Exception as e:
        logger.error(f"批量API异常: {e}", exc_info=True)
        for i, task in zip(uncached_indices, uncached_tasks):
            idx, timestamp, text, save_dir = task
            save_path = generate_filename(idx, timestamp, text, save_dir)
            file_name = os.path.basename(save_path)
            results[i] = (False, f"批量异常: {file_name} - {str(e)}")

    return results


def calculate_task_id(lrc_files):
    file_names = sorted([os.path.basename(f) for f in lrc_files])
    md5_hash = hashlib.md5("".join(file_names).encode("utf-8")).hexdigest()
    return md5_hash[:8]


async def _edge_tts_generate(text, voice, rate, volume, save_path):
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(save_path)


def edge_tts_task(index, timestamp, text, voice, rate, volume, save_dir, audio_cache, source_mtime=0, max_retries=3):
    save_path = generate_filename(index, timestamp, text, save_dir)
    file_name = os.path.basename(save_path)

    source_version = str(int(source_mtime))
    cache_key = f"{text}|{voice}"
    cached_path = audio_cache.get_cached_audio(cache_key, "edge_tts", source_version)
    if cached_path:
        try:
            shutil.copy2(cached_path, save_path)
            logger.debug(f"Edge TTS缓存复用: {file_name}")
            return True, f"缓存复用: {file_name}"
        except Exception as e:
            logger.warning(f"Edge TTS缓存复制失败: {file_name} - {e}")

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(f"Edge TTS生成 (尝试 {attempt}/{max_retries}): 声音={voice}, 文本={text[:20]}...")
            temp_path = save_path.replace(".wav", ".mp3")
            asyncio.run(asyncio.wait_for(
                _edge_tts_generate(text, voice, rate, volume, temp_path),
                timeout=15
            ))
            audio_cache.save_audio_cache(cache_key, temp_path, "edge_tts", "edge_tts", ext=".mp3", source_version=source_version)
            if temp_path != save_path:
                shutil.copy2(temp_path, save_path)
                os.unlink(temp_path)
            logger.info(f"Edge TTS完成: {file_name}")
            return True, f"完成: {file_name}"
        except asyncio.TimeoutError:
            last_error = "超时"
            logger.warning(f"Edge TTS超时 (尝试 {attempt}/{max_retries}): {file_name}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Edge TTS异常 (尝试 {attempt}/{max_retries}): {file_name} - {e}")
        if attempt < max_retries:
            time.sleep(1)

    logger.error(f"Edge TTS失败 (已重试{max_retries}次): {file_name} - {last_error}")
    return False, f"失败(已重试{max_retries}次): {last_error}"
