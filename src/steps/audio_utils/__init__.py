# -*- coding: utf-8 -*-
"""混音工具库。

由 audio_utils.py 拆分为包结构，本模块保持原有导入接口不变：
    from .audio_utils import get_mix_audio_files, mix_with_numpy, ...
"""
from .common import HAS_NUMPY, np, logger, _get_pan_detect_executor
from .mix_files import (
    _mix_files_cache, parse_filename, get_mix_audio_files, clear_mix_cache,
)
from .detection import (
    _detect_voice_channel_from_samples, _analyze_channel_energy,
    detect_voice_onset, find_nearest_onset, detect_angles_parallel,
)
from .rms import (
    compute_rms_db, _mono_float_from_frames,
    compute_rms_envelope, interpolate_envelope,
)
from .mixing import overlay_with_pan, mix_with_numpy
from .ffmpeg_utils import (
    export_audio_ffmpeg, resample_audio,
    export_with_nvenc, extract_audio_from_video,
    replace_audio_in_video, _run_ffmpeg,
)

__all__ = [
    "HAS_NUMPY", "np", "logger", "_get_pan_detect_executor",
    "_mix_files_cache", "parse_filename", "get_mix_audio_files", "clear_mix_cache",
    "_detect_voice_channel_from_samples", "_analyze_channel_energy",
    "detect_voice_onset", "find_nearest_onset", "detect_angles_parallel",
    "compute_rms_db", "_mono_float_from_frames",
    "compute_rms_envelope", "interpolate_envelope",
    "overlay_with_pan", "mix_with_numpy",
    "export_with_nvenc", "extract_audio_from_video",
    "export_audio_ffmpeg", "resample_audio",
    "replace_audio_in_video", "_run_ffmpeg",
]
