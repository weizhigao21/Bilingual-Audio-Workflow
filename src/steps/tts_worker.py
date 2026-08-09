import os
import re
import queue
import threading
import time
import hashlib

from PyQt6.QtCore import QThread, pyqtSignal

from .tts_cache import AudioCache
from .tts_logger import logger
from .tts_utils import (
    set_sleep_mode,
    generate_filename,
    tts_task,
    tts_bulk_task,
    edge_tts_task,
)


def format_eta(seconds):
    if seconds <= 0:
        return "计算中..."
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}小时{minutes}分{secs}秒"
    elif minutes > 0:
        return f"{minutes}分{secs}秒"
    else:
        return f"{secs}秒"


class TTSWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    total_tasks_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool)
    eta_signal = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.paused = False
        self.stop_flag = False
        self.pause_lock = threading.Lock()
        self.audio_cache = AudioCache()

    def pause(self):
        with self.pause_lock:
            self.paused = True
            self.log_signal.emit("合成已暂停")

    def resume(self):
        with self.pause_lock:
            self.paused = False
            self.log_signal.emit("合成已恢复")

    def stop(self):
        self.stop_flag = True
        self.resume()

    def is_paused(self):
        with self.pause_lock:
            return self.paused

    def run(self):
        prevent_sleep = self.config.get("prevent_sleep", True)
        if prevent_sleep:
            set_sleep_mode(True)
            self.log_signal.emit("防休眠已启用")

        try:
            lrc_files = self.config["lrc_files"]
            base_output = os.path.abspath(self.config["output_dir"])
            all_tasks = []

            # 用字幕文件MD5作为文件夹名
            subtitle_md5 = self.config.get("subtitle_md5", "")
            if not subtitle_md5:
                # 兼容旧配置
                source_name = self.config.get("source_name", "")
                subtitle_md5 = hashlib.md5(source_name.encode("utf-8")).hexdigest()[:8]
            task_dir = os.path.join(base_output, subtitle_md5)
            os.makedirs(task_dir, exist_ok=True)
            self.log_signal.emit(f"字幕MD5: {subtitle_md5}")
            self.log_signal.emit(f"语音输出：{task_dir}")

            for lrc_path in lrc_files:
                save_dir = task_dir
                try:
                    file_mtime = os.path.getmtime(lrc_path)
                except Exception:
                    file_mtime = 0

                try:
                    with open(lrc_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                except:
                    try:
                        with open(lrc_path, "r", encoding="gbk") as f:
                            lines = f.readlines()
                    except Exception as e:
                        self.log_signal.emit(f"读取文件失败 {lrc_path}: {e}")
                        continue

                idx = 1
                file_ext = os.path.splitext(lrc_path)[1].lower()

                if file_ext == ".lrc":
                    lrc_pattern_full = re.compile(
                        r"\[(\d{2}):(\d{2}):(\d{2})\.(\d{2})\](.*)"
                    )
                    lrc_pattern_min = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2})\](.*)")
                    for line in lines:
                        match_full = lrc_pattern_full.match(line.strip())
                        if match_full:
                            hours = match_full.group(1)
                            minutes = match_full.group(2)
                            seconds = match_full.group(3)
                            milliseconds = match_full.group(4)
                            timestamp = f"{hours}:{minutes}:{seconds}.{milliseconds}"
                            text = match_full.group(5).strip()
                            if text:
                                all_tasks.append((idx, timestamp, text, save_dir, file_mtime))
                                idx += 1
                            continue
                        match_min = lrc_pattern_min.match(line.strip())
                        if match_min:
                            minutes = match_min.group(1)
                            seconds = match_min.group(2)
                            milliseconds = match_min.group(3)
                            timestamp = f"00:{minutes}:{seconds}.{milliseconds}"
                            text = match_min.group(4).strip()
                            if text:
                                all_tasks.append((idx, timestamp, text, save_dir, file_mtime))
                                idx += 1
                elif file_ext == ".vtt":
                    is_content = False
                    current_text = []
                    current_timestamp = None

                    vtt_time_pattern = re.compile(
                        r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3}) -->"
                    )

                    for line in lines:
                        line = line.strip()

                        if line == "WEBVTT" or line.startswith("NOTE"):
                            continue

                        if not line:
                            if current_timestamp and current_text:
                                text = " ".join(current_text).strip()
                                if text:
                                    all_tasks.append(
                                        (idx, current_timestamp, text, save_dir, file_mtime)
                                    )
                                    idx += 1
                                current_text = []
                                current_timestamp = None
                            continue

                        time_match = vtt_time_pattern.match(line)
                        if time_match:
                            hours = time_match.group(1)
                            minutes = time_match.group(2)
                            seconds = time_match.group(3)
                            milliseconds = time_match.group(4)[:2]

                            timestamp_str = (
                                f"{hours}:{minutes}:{seconds}.{milliseconds}"
                            )
                            current_timestamp = timestamp_str
                            continue

                        if current_timestamp:
                            current_text.append(line)

                    if current_timestamp and current_text:
                        text = " ".join(current_text).strip()
                        if text:
                            all_tasks.append(
                                (idx, current_timestamp, text, save_dir, file_mtime)
                            )
                            idx += 1
                elif file_ext == ".srt":
                    current_text = []
                    current_timestamp = None

                    srt_time_pattern = re.compile(
                        r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) -->"
                    )

                    for line in lines:
                        line = line.strip()

                        if not line:
                            if current_timestamp and current_text:
                                text = " ".join(current_text).strip()
                                if text:
                                    all_tasks.append(
                                        (idx, current_timestamp, text, save_dir, file_mtime)
                                    )
                                    idx += 1
                                current_text = []
                                current_timestamp = None
                            continue

                        if line.isdigit():
                            continue

                        time_match = srt_time_pattern.match(line)
                        if time_match:
                            hours = time_match.group(1)
                            minutes = time_match.group(2)
                            seconds = time_match.group(3)
                            milliseconds = time_match.group(4)[:2]

                            timestamp_str = (
                                f"{hours}:{minutes}:{seconds}.{milliseconds}"
                            )
                            current_timestamp = timestamp_str
                            continue

                        if current_timestamp:
                            current_text.append(line)

                    if current_timestamp and current_text:
                        text = " ".join(current_text).strip()
                        if text:
                            all_tasks.append(
                                (idx, current_timestamp, text, save_dir, file_mtime)
                            )
                            idx += 1
                elif file_ext == ".txt":
                    for line in lines:
                        text = line.strip()
                        if text:
                            timestamp = "00:00:00.00"
                            all_tasks.append((idx, timestamp, text, save_dir, file_mtime))
                            idx += 1

            if not all_tasks:
                self.log_signal.emit("未找到有效歌词")
                self.finished_signal.emit(False)
                return

            total = len(all_tasks)
            self.total_tasks_signal.emit(total)
            completed = 0

            pending_tasks = []
            for task in all_tasks:
                idx, timestamp, text, save_dir, file_mtime = task
                file_path = generate_filename(idx, timestamp, text, save_dir)
                if os.path.exists(file_path):
                    completed += 1
                    self.progress_signal.emit(completed)
                    file_name = os.path.basename(file_path)
                    self.log_signal.emit(f"跳过已存在文件: {file_name}")
                else:
                    pending_tasks.append(task)

            start_time = time.time()
            pending_completed = 0
            pending_total = len(pending_tasks)

            skipped_count = total - len(pending_tasks)
            if skipped_count > 0:
                self.log_signal.emit(f"发现 {skipped_count} 个文件已存在，已跳过")

            cache_stats = self.audio_cache.get_cache_stats()
            self.log_signal.emit(
                f"缓存统计: {cache_stats['total_count']} 条记录, "
                f"总大小 {cache_stats['total_size'] / 1024 / 1024:.2f} MB, "
                f"累计复用 {cache_stats['total_reuse']} 次"
            )

            self.log_signal.emit(f"剩余 {len(pending_tasks)} 个任务待处理")

            tts_mode = self.config.get("tts_mode", "api")

            if tts_mode == "edge":
                if len(pending_tasks) == 0:
                    self.log_signal.emit("所有文件已存在，无需处理")
                    self.finished_signal.emit(True)
                    return
                self._run_edge_tts(pending_tasks, completed, start_time, subtitle_md5, task_dir)
            else:
                available_apis = self._get_available_apis()
                if self.config.get("use_multi_api", False):
                    self.log_signal.emit(
                        f"使用多API模式，共 {len(available_apis)} 个在线API"
                    )
                else:
                    self.log_signal.emit("使用单API模式")

                if not available_apis:
                    self.log_signal.emit("错误：没有可用的API配置")
                    self.finished_signal.emit(False)
                    return

                if len(pending_tasks) == 0:
                    self.log_signal.emit("所有文件已存在，无需处理")
                    self.finished_signal.emit(True)
                    return

                self._run_api_tts(pending_tasks, completed, start_time, subtitle_md5, task_dir)

        except Exception as e:
            self.log_signal.emit(f"处理出错: {e}")
            self.finished_signal.emit(False)
        finally:
            if prevent_sleep:
                set_sleep_mode(False)
                self.log_signal.emit("防休眠已关闭")
            self.audio_cache.close()

    def _get_available_apis(self):
        """获取可用 API 配置列表。"""
        use_multi = self.config.get("use_multi_api", False)
        api_configs = self.config.get("api_configs", [])
        if use_multi:
            available_apis = [
                api for api in api_configs
                if api.get("status") == "success"
            ]
            if not available_apis:
                current_idx = self.config.get("current_api_index", 0)
                if current_idx < len(api_configs):
                    available_apis = [api_configs[current_idx]]
                else:
                    available_apis = api_configs[:1]
        else:
            current_idx = self.config.get("current_api_index", 0)
            if current_idx < len(api_configs):
                available_apis = [api_configs[current_idx]]
            else:
                available_apis = api_configs[:1]
        return available_apis

    def _run_edge_tts(self, pending_tasks, completed, start_time, task_id, task_dir):
        import concurrent.futures

        voice = self.config.get("edge_voice", "zh-CN-XiaoxiaoNeural")
        rate = self.config.get("edge_rate", "+0%")
        volume = self.config.get("edge_volume", "+0%")
        max_workers = self.config.get("edge_threads", 3)

        self.log_signal.emit(f"使用Edge TTS模式，声音: {voice}，线程数: {max_workers}")

        total = len(pending_tasks)
        pending_completed = 0
        progress_lock = threading.Lock()

        def process_task(task):
            idx, timestamp, text, save_dir, file_mtime = task
            return edge_tts_task(
                idx, timestamp, text, voice, rate, volume, save_dir, self.audio_cache, file_mtime
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for task in pending_tasks:
                if self.stop_flag:
                    break

                while self.is_paused():
                    if self.stop_flag:
                        break
                    time.sleep(0.1)

                if self.stop_flag:
                    break

                future = executor.submit(process_task, task)
                futures[future] = task

            for future in concurrent.futures.as_completed(futures):
                if self.stop_flag:
                    break

                try:
                    success, msg = future.result()
                    self.log_signal.emit(msg)
                except Exception as e:
                    task = futures[future]
                    self.log_signal.emit(f"异常: {task[2][:10]}... - {e}")

                with progress_lock:
                    completed += 1
                    pending_completed += 1
                    self.progress_signal.emit(completed)

                    elapsed = time.time() - start_time
                    if pending_completed > 0:
                        avg_time = elapsed / pending_completed
                        remaining = avg_time * (total - pending_completed)
                        self.eta_signal.emit(format_eta(remaining))

        self.log_signal.emit("全部任务完成")
        self.log_signal.emit(f"任务ID: {task_id}")
        self.log_signal.emit(f"任务文件保存位置: {task_dir}")

        final_stats = self.audio_cache.get_cache_stats()
        self.log_signal.emit(
            f"最终缓存统计: {final_stats['total_count']} 条记录, "
            f"累计复用 {final_stats['total_reuse']} 次"
        )

        self.finished_signal.emit(True)

    def _run_api_tts(self, pending_tasks, completed, start_time, task_id, task_dir):
        use_bulk = self.config.get("use_bulk_api", True)
        bulk_batch_size = self.config.get("bulk_batch_size", 5)

        available_apis = self._get_available_apis()

        if not available_apis:
            self.log_signal.emit("错误：没有可用的API配置")
            self.finished_signal.emit(False)
            return

        if use_bulk:
            self.log_signal.emit(f"批量推理已启用，微批大小: {bulk_batch_size}")
        else:
            self.log_signal.emit("使用逐条推理模式")

        task_queue = queue.Queue()
        for task in pending_tasks:
            task_queue.put(task)

        total = len(pending_tasks)
        pending_completed = 0
        progress_lock = threading.Lock()

        def collect_micro_batch():
            batch = []
            try:
                task = task_queue.get(timeout=1)
                batch.append(task)
            except queue.Empty:
                return batch

            deadline = time.time() + 0.3
            while len(batch) < bulk_batch_size:
                if self.stop_flag:
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    task = task_queue.get(timeout=min(remaining, 0.05))
                    batch.append(task)
                except queue.Empty:
                    break
            return batch

        def worker(api_config):
            nonlocal completed, pending_completed
            while True:
                if self.stop_flag:
                    break

                if self.is_paused():
                    time.sleep(0.1)
                    continue

                if self.stop_flag:
                    break

                batch = collect_micro_batch()
                if not batch:
                    break

                batch_done = False
                try:
                    if self.stop_flag:
                        break

                    while self.is_paused():
                        if self.stop_flag:
                            break
                        time.sleep(0.1)

                    if self.stop_flag:
                        break

                    if use_bulk and len(batch) > 1:
                        try:
                            results = tts_bulk_task(
                                batch,
                                api_config["url"],
                                api_config["model"],
                                self.audio_cache,
                            )
                        except Exception as e:
                            results = [(False, f"批量异常: {e}") for _ in batch]
                        for result in results:
                            if result is None:
                                success, msg = False, "服务器未返回音频"
                            else:
                                success, msg = result
                            self.log_signal.emit(f"[{api_config['name']}] {msg}")
                    else:
                        for task in batch:
                            if self.stop_flag:
                                break
                            idx, timestamp, text, save_dir, file_mtime = task
                            success, msg = tts_task(
                                idx,
                                timestamp,
                                text,
                                api_config["url"],
                                api_config["model"],
                                save_dir,
                                self.audio_cache,
                                file_mtime,
                            )
                            self.log_signal.emit(f"[{api_config['name']}] {msg}")

                    with progress_lock:
                        completed += len(batch)
                        pending_completed += len(batch)
                        self.progress_signal.emit(completed)
                        elapsed = time.time() - start_time
                        if pending_completed > 0 and total > 0:
                            avg_time = elapsed / pending_completed
                            remaining = avg_time * (total - pending_completed)
                            self.eta_signal.emit(format_eta(remaining))
                        else:
                            self.eta_signal.emit("计算中...")

                    batch_done = True
                finally:
                    for _ in batch:
                        task_queue.task_done()
                    if not batch_done:
                        break

        threads = []
        for api_config in available_apis:
            thread = threading.Thread(target=worker, args=(api_config,))
            thread.daemon = True
            threads.append(thread)
            thread.start()

        task_queue.join()

        for thread in threads:
            thread.join(timeout=1)

        self.log_signal.emit("全部任务完成")
        self.log_signal.emit(f"任务ID: {task_id}")
        self.log_signal.emit(f"任务文件保存位置: {task_dir}")

        final_stats = self.audio_cache.get_cache_stats()
        self.log_signal.emit(
            f"最终缓存统计: {final_stats['total_count']} 条记录, "
            f"累计复用 {final_stats['total_reuse']} 次"
        )

        self.finished_signal.emit(True)
