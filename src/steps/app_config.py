# -*- coding: utf-8 -*-
import os
import sys
import subprocess
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


_NVENC_CHECKED = False
_NVENC_AVAILABLE = False
_AAC_NVENC_AVAILABLE = False


def is_nvenc_available():
    global _NVENC_CHECKED, _NVENC_AVAILABLE, _AAC_NVENC_AVAILABLE
    if not _NVENC_CHECKED:
        _NVENC_AVAILABLE, _AAC_NVENC_AVAILABLE = _check_ffmpeg_capabilities()
        _NVENC_CHECKED = True
    return _NVENC_AVAILABLE


def is_aac_nvenc_available():
    global _NVENC_CHECKED, _NVENC_AVAILABLE, _AAC_NVENC_AVAILABLE
    if not _NVENC_CHECKED:
        _NVENC_AVAILABLE, _AAC_NVENC_AVAILABLE = _check_ffmpeg_capabilities()
        _NVENC_CHECKED = True
    return _AAC_NVENC_AVAILABLE


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


