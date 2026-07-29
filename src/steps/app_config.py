# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import hashlib
from pathlib import Path


def get_base_path():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent


def _check_ffmpeg_capabilities():
    """一次 ffmpeg -encoders 调用，同时检测 h264_nvenc 和 aac_nvenc。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = result.stdout
        return "h264_nvenc" in output, "aac_nvenc" in output
    except Exception:
        return False, False


NVENC_AVAILABLE, AAC_NVENC_AVAILABLE = _check_ffmpeg_capabilities()


def _check_cuda():
    """延迟检测 CUDA 可用性。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.returncode == 0 and "NVIDIA" in result.stdout
    except Exception:
        return False


_CUDA_CHECKED = False
_CUDA_AVAILABLE = False


def is_cuda_available():
    global _CUDA_CHECKED, _CUDA_AVAILABLE
    if not _CUDA_CHECKED:
        _CUDA_AVAILABLE = _check_cuda()
        _CUDA_CHECKED = True
    return _CUDA_AVAILABLE

VIDEO_EXTENSIONS = {"mp4", "mkv", "avi", "mov", "flv", "wmv", "webm", "ts"}
AUDIO_EXTENSIONS = {"wav", "mp3", "flac", "ogg", "m4a", "aac", "wma"}
ALL_MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


def get_file_checksum(file_path):
    try:
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return md5.hexdigest()
    except Exception:
        return None
