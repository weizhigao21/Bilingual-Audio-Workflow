# -*- coding: utf-8 -*-
"""混音文件列表缓存与文件名解析。"""
import os
import re

_mix_files_cache = {}

# 片段名 = 序号_时-分-秒.厘秒_文本.扩展名。扩展名接受 .wav/.mp3 两种：
# 存量是 .wav 命名（实为 MP3），P1-2 收口后 Edge 新产物按真实格式命名 .mp3。
# 文本段不含点号（generate_filename 已过滤），后缀锚定不会误切文本。
_CLIP_RE = re.compile(r"^(\d{4})_(\d{2})-(\d{2})-(\d{2})\.(\d{2})_(.+)\.(wav|mp3)$")


def parse_filename(filename):
    match = _CLIP_RE.match(filename)
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
        if not f.endswith((".wav", ".mp3")):
            continue
        parsed = parse_filename(f)
        if not parsed:
            continue
        # 跳过 0 字节片段：历史上出现过合成中断/下载失败留下的空文件，
        # 它们对混音没有贡献，长音频路径还会因解码失败拖垮整段处理。
        try:
            if os.path.getsize(os.path.join(mix_folder, f)) == 0:
                continue
        except OSError:
            continue
        audio_files.append(parsed)

    # 同序号新旧命名并存（存量 .wav + 新生成 .mp3）时只取一份，否则同一条
    # 字幕会被混音两遍。取修改时间较新者；copy2 保留时间戳导致并列时
    # 优先 .mp3（新命名代表最近一次生成）。
    best = {}
    for parsed in audio_files:
        try:
            mtime = os.path.getmtime(os.path.join(mix_folder, parsed["filename"]))
        except OSError:
            mtime = 0.0
        rank = (mtime, parsed["filename"].endswith(".mp3"))
        seq = parsed["sequence"]
        if seq not in best or rank > best[seq][0]:
            best[seq] = (rank, parsed)
    audio_files = [item[1] for item in best.values()]

    audio_files.sort(key=lambda x: x["sequence"])
    _mix_files_cache[mix_folder] = audio_files
    return audio_files


def clear_mix_cache():
    _mix_files_cache.clear()
