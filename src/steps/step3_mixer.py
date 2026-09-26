# -*- coding: utf-8 -*-
"""步骤3：音频混音。

在 QThread 中执行完整的混音流程（提取音轨→加载配音→混音→导出）。

支持两种模式：
    - MixerWorker: 单任务串行混音
    - MixerBatchWorker: 多任务并行混音（ThreadPoolExecutor）
"""
import os
import wave
import audioop
import threading
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
    export_audio_ffmpeg,
    resample_audio,
    clear_mix_cache,
)
from .audio_utils.ffmpeg_utils import probe_audio
from .stream_mixer import mix_streaming_task


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts"}


# ---------- 模块级混音核心逻辑 ----------
# 这些函数与 QThread 无关，可被 MixerWorker 和 MixerBatchWorker 共用，
# 也能在 ThreadPoolExecutor 的多个工作线程中并行调用。


def _export_wav_24bit(audio, output_path, stop_check=None):
    """导出真正的 24bit WAV。

    本版 pydub 无法在内部表示 24bit：set_sample_width(3) 会被构造函数
    提升为 32bit 存储，直接 export 只会得到 32bit 文件。
    直接用 audioop.lin2lin(4,3) 取回高 3 字节，避免先 32→24→32
    的整段往返复制，经 stdlib wave 写出经典 PCM 头的
    24bit WAV。不走 ffmpeg 的原因：ffmpeg 对 pcm_s24le 一律写
    WAVE_FORMAT_EXTENSIBLE(0xFFFE) 头，stdlib wave 及部分老旧工具不认。
    """
    if audio.sample_width != 4:
        audio = audio.set_sample_width(4)
    # 每次处理约 10 秒数据：兼顾 stop 响应与转换速度
    chunk_bytes = audio.frame_rate * audio.channels * 4 * 10
    with wave.open(output_path, "wb") as w:
        w.setnchannels(audio.channels)
        w.setsampwidth(3)
        w.setframerate(audio.frame_rate)
        raw = audio.raw_data
        for off in range(0, len(raw), chunk_bytes):
            if stop_check is not None and stop_check():
                raise RuntimeError("用户已停止处理")
            w.writeframes(audioop.lin2lin(raw[off:off + chunk_bytes], 4, 3))


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
    # 导出目录优先级：
    # 1. 自定义目录（output_folder）
    # 2. 文件夹导入 + 输出前缀配置：父目录/双语-<源文件夹名>（输出直接在该目录根，
    #    不嵌套"双语"子目录；源文件夹本身已带"双语-"前缀时直接输出到其根目录）
    # 3. 默认：源文件同目录下的"双语"子文件夹
    custom_output = cfg.get("output_folder", "").strip()
    if custom_output:
        output_folder = custom_output
    elif task.from_folder and cfg.get("output_folder_prefix", False):
        src_dir = os.path.dirname(original_path)
        parent = os.path.dirname(src_dir)
        dir_name = os.path.basename(src_dir)
        if dir_name.startswith("双语-"):
            # 源文件夹已是双语- 前缀（重跑/已重命名过），直接输出到其根目录
            output_folder = src_dir
        else:
            output_folder = os.path.join(parent, f"双语-{dir_name}")
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
    if cfg.get("skip_existing", True) and not task.force_remix and os.path.exists(final_output):
        _log(f"[音频混音] 已存在，跳过: {final_output}")
        _progress(100)
        return True, final_output

    # 长音频避免 AudioSegment.from_file 一次解码整段并物化多个全长数组。
    threshold = max(0, int(cfg.get("streaming_threshold_minutes", 20)))
    audio_info = probe_audio(original_path)
    if audio_info and audio_info[1] >= threshold * 60:
        return mix_streaming_task(
            task, cfg, final_output, is_video, audio_info,
            log_callback=_log, progress_callback=_progress, stop_check=stop_check,
        )

    # 1. 加载原始音频
    temp_audio_path = None
    export_output = None
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
                with open(temp_audio_path, "rb") as audio_file:
                    original_audio = AudioSegment.from_file(audio_file)
            except Exception:
                _cleanup(temp_audio_path)
                raise
        else:
            with open(original_path, "rb") as audio_file:
                original_audio = AudioSegment.from_file(audio_file)

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
            # 左/右指配音摆放的声相（θ<45°→偏左，θ>135°→偏右），并非原人声所在声道。
            # 增益 left=cos(θ/2)、right=sin(θ/2)。channel_map 默认把原音在左→配音放右(155°)、
            # 原音在右→配音放左(25°)，故统计的“偏左”通常对应原音偏右。
            pan_left = sum(1 for a in per_file_angles.values() if a < 45)
            pan_right = sum(1 for a in per_file_angles.values() if a > 135)
            pan_center = len(per_file_angles) - pan_left - pan_right
            _log(
                f"[音频混音] 配音声相: 偏左{pan_left} 居中{pan_center} 偏右{pan_right}"
                f" (共{len(per_file_angles)}，原音一般放对侧)"
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
                with open(audio_path, "rb") as audio_file:
                    mix_segment = AudioSegment.from_file(audio_file)
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
        # 高位深 WAV 在混音前扩展精度，避免先量化到 16bit 再补零导出。
        if output_format == "wav" and int(cfg.get("wav_bit_depth", 16)) > 16:
            original_audio = original_audio.set_sample_width(4)
        align_onset = cfg.get("align_onset", False)
        content_alignment = cfg.get("content_alignment", False)
        _log(
            f"[音频混音] 参数: 音量={cfg.get('volume_db', 0.0)}dB, "
            f"模式={cfg.get('auto_volume', 'off')}, 起始对齐={align_onset}, "
            f"内容对齐={content_alignment}, 峰值保护={cfg.get('peak_mode', 'peak')}, "
            f"高质量重采样={cfg.get('high_quality_resample', True)}"
        )
        original_audio = mix_with_numpy(
            original_audio, mix_items,
            volume_db=cfg.get("volume_db", 0.0),
            auto_volume=cfg.get("auto_volume", "off"),
            align_onset=align_onset,
            content_alignment=content_alignment,
            peak_mode=cfg.get("peak_mode", "peak"),
            high_quality=cfg.get("high_quality_resample", True),
            stop_check=stop_check,
        )
        _progress(85)

        # 6. 导出
        _log("[音频混音] 导出中...")
        # 导出音频质量参数
        bitrate = cfg.get("audio_bitrate", "192k")
        sample_rate = int(cfg.get("audio_sample_rate", 44100))
        channels = int(cfg.get("audio_channels", 2))
        wav_bit_depth = int(cfg.get("wav_bit_depth", 16))
        high_quality = cfg.get("high_quality_resample", True)
        # 先导出同目录临时文件；取消/失败不留下会被 skip_existing 当作成功的残片。
        with tempfile.NamedTemporaryFile(dir=output_folder, prefix=".mix-",
                                         suffix="." + output_format, delete=False) as tmp:
            export_output = tmp.name
        if is_video and output_format == "mp4":
            replace_audio_in_video(
                original_path, original_audio, export_output,
                bitrate=bitrate, sample_rate=sample_rate, channels=channels,
                stop_check=stop_check, high_quality=high_quality,
            )
        elif output_format == "wav":
            original_audio = resample_audio(original_audio, sample_rate, stop_check, high_quality)
            original_audio = original_audio.set_channels(channels)
            if wav_bit_depth == 24:
                _export_wav_24bit(original_audio, export_output, stop_check=stop_check)
            else:
                width = max(1, min(4, wav_bit_depth // 8))
                with open(export_output, "wb") as wav_file:
                    original_audio.set_sample_width(width).export(wav_file, format="wav")
        else:
            export_audio_ffmpeg(
                original_audio, export_output, output_format,
                bitrate=bitrate, sample_rate=sample_rate, channels=channels,
                stop_check=stop_check, high_quality=high_quality,
            )
        _cleanup(temp_audio_path)
        if _stopped():
            _cleanup(export_output)
            return False, "用户中止"
        os.replace(export_output, final_output)
        export_output = None
        task.force_remix = False

        _progress(100)
        _log(f"[音频混音] 完成: {final_output}")
        return True, final_output
    except Exception as e:
        _cleanup(temp_audio_path, export_output)
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
        # 同步结果：(success, output_path_or_error)，供流水线模式 wait() 后读取
        self.result = (False, "未执行")

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
        self.result = (ok, msg)
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
    status_signal = pyqtSignal(str)                  # 状态文本（当前混音任务等）
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
        lock = threading.Lock()
        # future -> 进度槽：pct=任务内进度 0-100，last=上次发状态文本的进度
        slots = {}

        def _avg_pct():
            # 总进度 = 所有任务内部进度的平均值（结束的任务保持 100），
            # 这样并行混音时进度条随任务内部阶段平滑前进
            if not slots:
                return 0
            return int(sum(s["pct"] for s in slots.values()) / len(slots))

        def _on_task_progress(slot, pct):
            pct = max(0, min(100, int(pct)))
            status = None
            with lock:
                slot["pct"] = pct
                if pct - slot.get("last", -1) >= 5:  # 节流：每 5% 更新一次状态文本
                    slot["last"] = pct
                    status = f"混音中 {completed}/{total} · 当前 {slot['name']}"
                avg = _avg_pct()
            self.progress_signal.emit(min(avg, 99))
            if status:
                self.status_signal.emit(status)

        def _make_progress(slot):
            def _p(v):
                _on_task_progress(slot, v)
            return _p

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                self._executor = executor

                def _make_log(task_name):
                    def _log(msg):
                        self.log_signal.emit(f"[{task_name}] {msg}")
                    return _log

                future_to_task = {}
                for task in self.tasks:
                    slot = {"pct": 0, "last": -1, "name": task.source_name}
                    fut = executor.submit(
                        mix_single_task,
                        task, self.config,
                        _make_log(task.source_name),
                        _make_progress(slot),
                        self._check_stop,
                    )
                    slots[fut] = slot
                    future_to_task[fut] = task

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

                    # 任务结束即占满份额，平均进度随之推进
                    with lock:
                        slots[future]["pct"] = 100
                    self.task_result_signal.emit(task.task_id, ok, msg)

                    completed += 1
                    self.progress_signal.emit(min(_avg_pct(), 99))
                    self.status_signal.emit(
                        f"混音 {completed}/{total} · {task.source_name}"
                    )
        finally:
            self._executor = None

        if self._stop_flag:
            self.log_signal.emit("[音频混音批量] 已中止")

        self.progress_signal.emit(100)
        self.log_signal.emit(
            f"[音频混音批量] 完成: 成功 {success_count}, 失败 {fail_count}"
        )
        clear_mix_cache()
        self.finished_signal.emit(success_count, fail_count)
