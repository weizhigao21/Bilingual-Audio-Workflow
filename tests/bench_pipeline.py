# -*- coding: utf-8 -*-
"""离线基准脚本（《优化方案-易用性与速度》P0-1 第 3 条）。

固定 N 个合成片段，跑三种场景并输出 JSON（stdout 为纯 JSON，
日志走 stderr）：
    1. short   短音频路径（全内存混音）
    2. stream  磁盘缓冲路径（streaming_threshold_minutes=0 强制）
    3. cache   全缓存命中（AudioCache 查询 + 复制）

用法（项目根目录）：
    python tests/bench_pipeline.py [--clips 300] [--out result.json]

不被 pytest 收集（文件名不匹配 test_*）；无网络、无外部服务依赖，
只需 ffmpeg/pydub/numpy（与测试套件同套依赖）。
"""
import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from pydub import AudioSegment  # noqa: E402

from src.steps import tts_cache  # noqa: E402
from src.steps.audio_utils import clear_mix_cache  # noqa: E402
from src.steps.step3_mixer import mix_single_task  # noqa: E402
from src.task_manager import TaskInfo  # noqa: E402


def _seg(samples, sr):
    return AudioSegment(np.asarray(samples, dtype=np.int16).tobytes(),
                        sample_width=2, frame_rate=sr, channels=1)


def _timestamp(i, step_ms=250):
    ms = i * step_ms
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}-{m:02d}-{s:02d}.{ms // 10:02d}"


def build_material(folder, n_clips, base_seconds=90.0):
    """生成基准素材：1 条基音 + n_clips 个 24kHz 片段（合法命名）。"""
    sr = 44100
    t = np.arange(int(base_seconds * sr)) / sr
    base = _seg((np.sin(2 * np.pi * 220 * t) * 9000).astype(np.int16), sr)
    source = os.path.join(folder, "source.wav")
    base.export(source, format="wav").close()

    clips = Path(folder) / "clips"
    clips.mkdir()
    clip_sr = 24000
    ct = np.arange(6000) / clip_sr
    clip = _seg((np.sin(2 * np.pi * 440 * ct) * 7000).astype(np.int16), clip_sr)
    for i in range(n_clips):
        clip.export(str(clips / f"{i + 1:04d}_{_timestamp(i)}_词.wav"),
                    format="wav").close()
    return source, str(clips)


def _cfg(folder, stream=False):
    return SimpleNamespace(mixer_cfg={
        "output_folder": folder,
        "output_format": "mp3",
        "skip_existing": False,
        "channel_detect": True,
        "auto_volume": "off",
        "thread_count": 4,
        "streaming_threshold_minutes": 0 if stream else 20,
    })


def run_mix(source, clips, folder, stream):
    task = TaskInfo(f"bench-{'stream' if stream else 'short'}",
                    source, "source", folder, step2_output=clips)
    clear_mix_cache()
    tracemalloc.start()
    t0 = time.perf_counter()
    ok, result = mix_single_task(task, _cfg(folder, stream=stream))
    wall = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"ok": bool(ok), "wall_s": round(wall, 3),
            "peak_mb": round(peak / 1048576, 1),
            "result": str(result)[:120]}


def run_cache_hits(folder, n):
    with patch.object(tts_cache, "CACHE_DIR", folder):
        cache = tts_cache.AudioCache(str(Path(folder) / "cache.db"))
        dest = Path(folder) / "dest"
        dest.mkdir()
        payload = b"RIFFxxxxWAVEdata"
        src = Path(folder) / "seed.bin"
        src.write_bytes(payload)
        for i in range(n):
            cache.save_audio_cache(f"seed-{i}", str(src))
        tracemalloc.start()
        t0 = time.perf_counter()
        hits = 0
        for i in range(n):
            got = cache.get_cached_audio(f"seed-{i}")
            if got:
                shutil.copy2(got, dest / f"{i:04d}.wav")
                hits += 1
        wall = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        cache.close()
        return {"ok": hits == n, "hits": hits, "wall_s": round(wall, 3),
                "us_per_hit": round(wall / max(1, n) * 1e6, 1),
                "peak_mb": round(peak / 1048576, 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips", type=int, default=300)
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    # PERF 日志保持在 stderr，stdout 只输出结果 JSON
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    result = {"clips": args.clips, "scenarios": {}}
    with tempfile.TemporaryDirectory() as folder:
        source, clips = build_material(folder, args.clips)
        result["scenarios"]["short"] = run_mix(source, clips, folder, stream=False)
        result["scenarios"]["stream"] = run_mix(source, clips, folder, stream=True)
        result["scenarios"]["cache"] = run_cache_hits(
            str(Path(folder) / "cache"), args.clips)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
