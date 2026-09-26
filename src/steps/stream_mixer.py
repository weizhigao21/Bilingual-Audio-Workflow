# -*- coding: utf-8 -*-
"""长音频磁盘映射混音：整段 PCM 与累加缓冲不进入常驻 Python 堆。"""
import mmap
import os
import tempfile

from pydub import AudioSegment

from .audio_utils import detect_angles_parallel, get_mix_audio_files, mix_with_numpy
from .audio_utils.ffmpeg_utils import (
    decode_audio_to_pcm, export_pcm_file, export_pcm_wav,
)


def mix_streaming_task(task, cfg, final_output, is_video, audio_info,
                       log_callback=None, progress_callback=None, stop_check=None):
    """将整段处理落到临时磁盘文件，成功后原子替换成品。"""
    def log(message):
        if log_callback:
            log_callback(message)

    def progress(value):
        if progress_callback:
            progress_callback(value)

    def check_stop():
        if stop_check is not None and stop_check():
            raise RuntimeError("用户已停止处理")

    source_rate, duration = audio_info
    clips = get_mix_audio_files(task.step2_output)
    if not clips:
        return False, f"配音目录无有效文件: {task.step2_output}"
    output_folder = os.path.dirname(final_output)
    output_format = os.path.splitext(final_output)[1].lstrip('.').lower()
    export_path = None
    try:
        log(f"[音频混音] 启用长音频分块处理: {duration / 60:.1f} 分钟")
        with tempfile.TemporaryDirectory(dir=output_folder, prefix=".mix-work-") as work_dir:
            source_pcm = os.path.join(work_dir, "source.raw")
            accumulator = os.path.join(work_dir, "accumulator.raw")
            mixed_pcm = os.path.join(work_dir, "mixed.raw")
            progress(5)
            decode_audio_to_pcm(task.source_path, source_pcm, stop_check=stop_check)
            check_stop()
            if os.path.getsize(source_pcm) == 0:
                raise ValueError("源文件没有可解码的音频")
            progress(15)
            with open(source_pcm, "rb") as source_file:
                mapped = mmap.mmap(source_file.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    original = AudioSegment(mapped, sample_width=4,
                                            frame_rate=source_rate, channels=2)
                    angles = {}
                    if cfg.get("channel_detect", True):
                        log("[音频混音] 检测声道位置...")
                        angles = detect_angles_parallel(
                            original, clips,
                            cfg.get("channel_map", {"left": 155, "right": 25, "both": 135}),
                            max_workers=cfg.get("thread_count", 4),
                        )
                    progress(25)
                    mix_items = [
                        (os.path.join(task.step2_output, clip["filename"]),
                         clip["timestamp_ms"], angles.get(clip["filename"], 180))
                        for clip in clips
                    ]
                    log(f"[音频混音] 分块合成 {len(mix_items)} 个配音片段...")
                    mix_with_numpy(
                        original, mix_items,
                        volume_db=cfg.get("volume_db", 0.0),
                        auto_volume=cfg.get("auto_volume", "off"),
                        align_onset=cfg.get("align_onset", False),
                        content_alignment=cfg.get("content_alignment", False),
                        peak_mode=cfg.get("peak_mode", "peak"),
                        high_quality=cfg.get("high_quality_resample", True),
                        stop_check=stop_check,
                        output_path=mixed_pcm,
                        accumulator_path=accumulator,
                        item_callback=lambda done, total: progress(25 + int(done * 45 / total)),
                    )
                    del original
                finally:
                    mapped.close()
            check_stop()
            progress(85)
            with tempfile.NamedTemporaryFile(dir=output_folder, prefix=".mix-",
                                             suffix="." + output_format, delete=False) as tmp:
                export_path = tmp.name
            bitrate = cfg.get("audio_bitrate", "192k")
            output_rate = int(cfg.get("audio_sample_rate", 44100))
            channels = int(cfg.get("audio_channels", 2))
            high_quality = cfg.get("high_quality_resample", True)
            log("[音频混音] 分块导出中...")
            if output_format == "wav":
                export_pcm_wav(
                    mixed_pcm, export_path, source_rate, output_rate, channels,
                    int(cfg.get("wav_bit_depth", 16)), work_dir,
                    high_quality=high_quality, stop_check=stop_check,
                )
            else:
                export_pcm_file(
                    mixed_pcm, task.source_path if is_video else None,
                    export_path, output_format, source_rate, bitrate, output_rate,
                    channels, high_quality=high_quality, stop_check=stop_check,
                )
            check_stop()
            os.replace(export_path, final_output)
            export_path = None
            task.force_remix = False
            progress(100)
            log(f"[音频混音] 完成: {final_output}")
            return True, final_output
    except Exception as exc:
        log(f"[音频混音] 分块处理失败: {exc}")
        return False, str(exc)
    finally:
        if export_path and os.path.exists(export_path):
            os.unlink(export_path)
