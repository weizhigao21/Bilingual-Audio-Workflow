# -*- coding: utf-8 -*-
"""片段快速解码（P1-1）：优先 soundfile 进程内解码，失败回退 pydub/ffmpeg。

背景：``AudioSegment.from_file(文件对象)`` 不带 format 时，pydub 对**任意**
格式都会起 ffprobe + ffmpeg 子进程（实测约 103ms/次，300 片段 ≈ 33s），
其中绝大部分是进程启动开销。soundfile（libsndfile 1.2.2）进程内解码
同一批 wav/mp3 实测约 0.3ms/次。

策略：常见格式（wav/flac/ogg/mp3，含「mp3 内容 .wav 命名」的 Edge 缓存
文件——libsndfile 按文件头嗅探，不认扩展名）走 soundfile；不支持的格式
（m4a/aac/wma 等）或解码异常时回退到旧的 pydub 路径，行为与改造前一致。

返回值是 pydub AudioSegment（int16/原采样率/原声道数），与旧路径同构，
下游（set_channels / resample_audio / detect_voice_onset / len）零改动。
"""
import os

from pydub import AudioSegment

try:
    import soundfile as _sf
except Exception:  # pragma: no cover - 依赖缺失时静默回退
    _sf = None

from .common import logger
from .. import perf


def soundfile_available():
    return _sf is not None


def load_audio_segment(path):
    """解码音频片段为 AudioSegment（16 位 PCM、原采样率、原声道数）。

    优先 soundfile 进程内解码；失败时回退 AudioSegment.from_file（子进程）。
    两条路径都失败时抛出异常，由调用方决定跳过或中止。
    """
    if _sf is not None:
        try:
            data, rate = _sf.read(os.fspath(path), dtype="int16", always_2d=True)
            if len(data) == 0:
                raise ValueError("空音频")
            channels = data.shape[1]
            raw = data.reshape(-1).tobytes()
            perf.COUNTERS.incr("clip_decode_fast")
            return AudioSegment(data=raw, sample_width=2,
                                frame_rate=int(rate), channels=channels)
        except Exception as exc:
            logger.debug(f"[解码] soundfile 不可用，回退 ffmpeg: {path} - {exc}")

    with open(path, "rb") as clip_file:
        seg = AudioSegment.from_file(clip_file)
    perf.COUNTERS.incr("clip_decode_fallback")
    return seg
