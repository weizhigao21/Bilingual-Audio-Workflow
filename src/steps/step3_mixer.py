# -*- coding: utf-8 -*-
"""步骤3：音频混音。

在 QThread 中执行完整的混音流程（提取音轨→加载配音→混音→导出）。

支持两种模式：
    - MixerWorker: 单任务串行混音
    - MixerBatchWorker: 多任务并行混音（ThreadPoolExecutor）
"""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

from ..config import WorkflowConfig
from ..task_manager import TaskInfo
from pydub import AudioSegment
from .audio_utils import (
    get_mix_audio_files,
    detect_angles_parallel,
    mix_with_numpy,
    extract_audio_from_video,
    replace_audio_in_video,
    export_with_nvenc,
    clear_mix_cache,
    clear_voice_cache,
)
from .app_config import is_nvenc_available, is_cuda_available


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts"}


# ---------- 模块级混音核心逻辑 ----------
# 这些函数与 QThread 无关，可被 MixerWorker 和 MixerBatchWorker 共用，
# 也能在 ThreadPoolExecutor 的多个工作线程中并行调用。


def _cleanup(*paths):
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.unlink(p)
            except Exception:
                pass


def mix_single_task(task: TaskInfo, config: WorkflowConfig,
                    log_callback=None, progress_callback=None,
                    stop_check=None):
    """对单个任务执行完整混音流程。

    Args:
        task: 任务对象
        config: 工作流配置
        log_callback: 可选的日志回调 fn(str)
        progress_callback: 可选的进度回调 fn(int 0-100)
        stop_check: 可选的停止检查 fn() -> bool

    Returns:
        (success: bool, output_path_or_error: str)
    """
    def _log(msg):
        if log_callback:
            log_callback(msg)

    def _progress(pct):
        if progress_callback:
            progress_callback(pct)

    def _stopped():
        return bool(stop_check and stop_check())

    original_path = task.source_path
    mix_folder = task.step2_output
    if not mix_folder or not os.path.isdir(mix_folder):
        return False, f"配音目录不存在: {mix_folder}"

    cfg = config.mixer_cfg
    # 导出目录：有自定义目录用自定义的，否则在源文件同目录下创建"双语"子文件夹
    custom_output = cfg.get("output_folder", "").strip()
    if custom_output:
        output_folder = custom_output
    else:
        output_folder = os.path.join(os.path.dirname(original_path), "双语")
    os.makedirs(output_folder, exist_ok=True)

    is_video = os.path.splitext(original_path)[1].lower() in VIDEO_EXTENSIONS
    output_format = cfg.get("output_format", "mp4" if is_video else "mp3")
    suffix = "_mixed" if cfg.get("add_suffix", True) else ""
    final_output = os.path.join(
        output_folder, f"{task.source_name}{suffix}.{output_format}"
    )

    _log(f"[音频混音] 启动: {os.path.basename(original_path)}")
    _log(f"[音频混音] 配音目录: {mix_folder}")
    _log(f"[音频混音] 导出目录: {output_folder}")

    # 跳过已存在
    if cfg.get("skip_existing", True) and os.path.exists(final_output):
        _log(f"[音频混音] 已存在，跳过: {final_output}")
        _progress(100)
        return True, final_output

    # 1. 加载原始音频
    temp_audio_path = None
    try:
        if is_video:
            _log("[音频混音] 提取视频音轨...")
            _progress(5)
            temp_audio_path = extract_audio_from_video(
                original_path, stop_check=stop_check
            )
            if _stopped():
                _cleanup(temp_audio_path)
                return False, "用户中止"
            try:
                original_audio = AudioSegment.from_file(temp_audio_path)
            except Exception:
                _cleanup(temp_audio_path)
                raise
        else:
            original_audio = AudioSegment.from_file(original_path)

        _progress(15)

        # 2. 获取配音文件列表
        audio_files = get_mix_audio_files(mix_folder)
        if not audio_files:
            _cleanup(temp_audio_path)
            return False, f"配音目录无有效文件: {mix_folder}"

        _log(f"[音频混音] 发现 {len(audio_files)} 个配音片段")

        # 3. 声道检测（使用配置的线程数）
        per_file_angles = {}
        if cfg.get("channel_detect", True):
            _log("[音频混音] 检测声道位置...")
            _progress(20)
            channel_map = cfg.get(
                "channel_map", {"left": 155, "right": 25, "both": 135}
            )
            per_file_angles = detect_angles_parallel(
                original_audio, audio_files, channel_map,
                max_workers=cfg.get("thread_count", 4)
            )
            left = sum(1 for a in per_file_angles.values() if a < 45)
            right = sum(1 for a in per_file_angles.values() if a > 135)
            center = len(per_file_angles) - left - right
            _log(
                f"[音频混音] 声道检测: 左{left} 中{center} 右{right}"
                f" (共{len(per_file_angles)})"
            )
            _progress(30)

        if not per_file_angles:
            per_file_angles = {af["filename"]: 180 for af in audio_files}

        # 4. 加载配音片段，组装 mix_items
        _log("[音频混音] 加载配音片段...")
        mix_items = []
        total = len(audio_files)
        for i, audio_info in enumerate(audio_files):
            if _stopped():
                _cleanup(temp_audio_path)
                return False, "用户中止"

            audio_path = os.path.join(mix_folder, audio_info["filename"])
            try:
                mix_segment = AudioSegment.from_file(audio_path)
                angle = per_file_angles.get(audio_info["filename"], 180)
                mix_items.append((mix_segment, audio_info["timestamp_ms"], angle))
            except Exception as e:
                _log(f"[音频混音] 加载失败 {audio_info['filename']}: {e}")

            if total > 0:
                _progress(min(30 + int((i + 1) / total * 40), 70))

        # 5. 混音合成
        if not mix_items:
            _cleanup(temp_audio_path)
            return False, "无可用配音片段"

        _log("[音频混音] 合成中...")
        _progress(75)
        align_onset = cfg.get("align_onset", False)
        content_alignment = cfg.get("content_alignment", False)
        _log(
            f"[音频混音] 参数: 音量={cfg.get('volume_db', 0.0)}dB, "
            f"模式={cfg.get('auto_volume', 'off')}, 起始对齐={align_onset}, "
            f"内容对齐={content_alignment}"
        )
        original_audio = mix_with_numpy(
            original_audio, mix_items,
            volume_db=cfg.get("volume_db", 0.0),
            auto_volume=cfg.get("auto_volume", "off"),
            align_onset=align_onset,
            content_alignment=content_alignment,
        )
        _progress(85)

        # 6. 导出
        _log("[音频混音] 导出中...")
        # 导出音频质量参数
        bitrate = cfg.get("audio_bitrate", "192k")
        sample_rate = int(cfg.get("audio_sample_rate", 44100))
        channels = int(cfg.get("audio_channels", 2))
        wav_bit_depth = int(cfg.get("wav_bit_depth", 16))
        if is_video:
            # 视频模式：导出临时 wav → 替换视频音轨
            temp_mixed = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            temp_mixed.close()
            try:
                original_audio.export(temp_mixed.name, format="wav")
                replace_audio_in_video(
                    original_path, temp_mixed.name, final_output,
                    bitrate=bitrate, sample_rate=sample_rate,
                    channels=channels,
                    stop_check=stop_check,
                )
            finally:
                _cleanup(temp_mixed.name)
        else:
            use_gpu = (
                cfg.get("use_gpu", True)
                and is_nvenc_available() and is_cuda_available()
                and output_format in ("mp3", "m4a", "aac")
            )
            if use_gpu:
                export_with_nvenc(
                    original_audio, final_output, output_format,
                    bitrate=bitrate, sample_rate=sample_rate,
                    channels=channels,
                    stop_check=stop_check,
                )
            else:
                export_params = {}
                if output_format == "mp3":
                    export_params = {"format": "mp3", "bitrate": bitrate}
                elif output_format == "ogg":
                    export_params = {"format": "ogg"}
                elif output_format == "m4a":
                    export_params = {"format": "ipod"}
                elif output_format == "wav":
                    export_params = {"format": "wav", "bit_depth": wav_bit_depth}
                else:
                    export_params = {"format": output_format}
                # 采样率/声道与目标不一致时先转换
                if original_audio.frame_rate != sample_rate:
                    original_audio = original_audio.set_frame_rate(sample_rate)
                if original_audio.channels != channels:
                    original_audio = original_audio.set_channels(channels)
                original_audio.export(final_output, **export_params)

        _cleanup(temp_audio_path)

        if _stopped():
            return False, "用户中止"

        _progress(100)
        _log(f"[音频混音] 完成: {final_output}")
        return True, final_output
    except Exception as e:
        _cleanup(temp_audio_path)
        _log(f"[音频混音] 异常: {e}")
        return False, str(e)


# ---------- 单任务 Worker ----------

class MixerWorker(QThread):
    """音频混音工作线程（单任务）。"""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)         # 0-100
    finished_signal = pyqtSignal(bool, str)   # (success, output_path_or_error)

    def __init__(self, task: TaskInfo, config: WorkflowConfig):
        super().__init__()
        self.task = task
        self.config = config
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def _check_stop(self) -> bool:
        return self._stop_flag

    def run(self):
        ok, msg = mix_single_task(
            self.task, self.config,
            log_callback=self.log_signal.emit,
            progress_callback=self.progress_signal.emit,
            stop_check=self._check_stop,
        )
        self.finished_signal.emit(ok, msg)


# ---------- 批量并行 Worker ----------

class MixerBatchWorker(QThread):
    """音频混音批量并行工作线程。

    用 ThreadPoolExecutor 同时处理多个混音任务。
    每个任务在独立线程中调用 mix_single_task。

    信号:
        log_signal: 日志
        progress_signal: 总进度 0-100
        task_result_signal: (task_id, success, output_path_or_error)
        finished_signal: (success_count, fail_count)
    """
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    task_result_signal = pyqtSignal(str, bool, str)  # task_id, ok, msg
    finished_signal = pyqtSignal(int, int)            # success_count, fail_count

    def __init__(self, tasks, config: WorkflowConfig, parent=None):
        super().__init__(parent)
        self.tasks = tasks  # List[TaskInfo]
        self.config = config
        self._stop_flag = False
        self._executor = None

    def stop(self):
        self._stop_flag = True

    def _check_stop(self) -> bool:
        return self._stop_flag

    def run(self):
        cfg = self.config.mixer_cfg
        max_workers = max(1, int(cfg.get("thread_count", 4)))
        max_workers = min(max_workers, len(self.tasks))  # 不会超过任务数

        self.log_signal.emit(
            f"[音频混音批量] 启动: {len(self.tasks)} 个任务, "
            f"并行线程数={max_workers}"
        )

        success_count = 0
        fail_count = 0
        completed = 0
        total = len(self.tasks)

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                self._executor = executor

                def _make_log(task_name):
                    def _log(msg):
                        self.log_signal.emit(f"[{task_name}] {msg}")
                    return _log

                future_to_task = {
                    executor.submit(
                        mix_single_task,
                        task, self.config,
                        _make_log(task.source_name), None, self._check_stop,
                    ): task for task in self.tasks
                }

                for future in as_completed(future_to_task):
                    if self._stop_flag:
                        # 取消尚未开始的任务
                        for f in future_to_task:
                            f.cancel()
                        break

                    task = future_to_task[future]
                    try:
                        ok, msg = future.result()
                    except Exception as e:
                        ok, msg = False, str(e)

                    if ok:
                        success_count += 1
                        self.log_signal.emit(
                            f"[音频混音批量] [{task.source_name}] 完成: {msg}"
                        )
                    else:
                        fail_count += 1
                        self.log_signal.emit(
                            f"[音频混音批量] [{task.source_name}] 失败: {msg}"
                        )

                    self.task_result_signal.emit(task.task_id, ok, msg)

                    completed += 1
                    pct = int(completed * 100 / total) if total > 0 else 100
                    self.progress_signal.emit(min(pct, 99))
        finally:
            self._executor = None

        if self._stop_flag:
            self.log_signal.emit("[音频混音批量] 已中止")

        self.progress_signal.emit(100)
        self.log_signal.emit(
            f"[音频混音批量] 完成: 成功 {success_count}, 失败 {fail_count}"
        )
        clear_mix_cache()
        clear_voice_cache()
        self.finished_signal.emit(success_count, fail_count)
