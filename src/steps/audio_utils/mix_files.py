# -*- coding: utf-8 -*-
"""混音文件列表缓存与文件名解析。"""
import os
import re

_mix_files_cache = {}


def parse_filename(filename):
    pattern = r"^(\d{4})_(\d{2})-(\d{2})-(\d{2})\.(\d{2})_(.+)\.wav$"
    match = re.match(pattern, filename)
    if not match:
        return None

    seq = int(match.group(1))
    hours = int(match.group(2))
    minutes = int(match.group(3))
    seconds = int(match.group(4))
    centiseconds = int(match.group(5))
    text = match.group(6)

    timestamp_ms = (
        (hours * 3600000) + (minutes * 60000) + (seconds * 1000) + (centiseconds * 10)
    )

    return {
        "sequence": seq,
        "timestamp_ms": timestamp_ms,
        "text": text,
        "filename": filename,
    }


def get_mix_audio_files(mix_folder):
    if mix_folder in _mix_files_cache:
        return _mix_files_cache[mix_folder]

    audio_files = []
    for f in os.listdir(mix_folder):
        if f.endswith(".wav"):
            parsed = parse_filename(f)
            if parsed:
                audio_files.append(parsed)

    audio_files.sort(key=lambda x: x["sequence"])
    _mix_files_cache[mix_folder] = audio_files
    return audio_files


def clear_mix_cache():
    _mix_files_cache.clear()
