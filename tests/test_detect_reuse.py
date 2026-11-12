# -*- coding: utf-8 -*-
"""P2-3 声道检测窗口复用 + ffprobe 短路守卫（2026-11-12）。

覆盖：全局能量谱路径与旧逐窗口路径**结论一致**（随机场景对照）、
对齐偏移下能量逐位可复现、单声道短路、稀疏片段退回旧路径、
成品未生成不起 ffprobe。
"""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from src.steps import audio_metadata
from src.steps.audio_utils.detection import (
    _frame_params, _voice_band_energy_profile, _voice_band_frame_energies,
    detect_angles_parallel, detect_voice_channel,
)

SR = 44100
CHANNEL_MAP = {"left": 0, "right": 180, "both": 90}  # 三标签角度互异，角度可反推标签


def _seg(stereo_int16: np.ndarray, sr=SR, channels=2, sample_width=2):
    """最小 AudioSegment 兼容对象（detect_angles_parallel 只用这四个属性）。"""
    return SimpleNamespace(
        raw_data=stereo_int16.astype(np.int16).tobytes(),
        frame_rate=sr, channels=channels, sample_width=sample_width)


def _speech(n, amp=0.5, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    return amp * (np.sin(2 * np.pi * 300 * t) + np.sin(2 * np.pi * 1200 * t)
                  + 0.1 * rng.standard_normal(n))


def _legacy_detect(original, audio_files, channel_map):
    """旧逐窗口实现的对照复刻（窗口起点自锚 + detect_voice_channel）。"""
    sr = original.frame_rate
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map.get(original.sample_width, np.int16)
    channels = original.channels
    frame_bytes = original.sample_width * channels
    raw = original.raw_data
    total_frames = len(raw) // frame_bytes
    min_segment_samples = int(2 * sr)
    window_frames = int(10000 * sr / 1000)

    results = {}
    for af in audio_files:
        start_frame = int(af["timestamp_ms"] * sr / 1000)
        end_frame = min(start_frame + window_frames, total_frames)
        voice_channel, confidence = 'both', 0.0
        if end_frame - start_frame >= min_segment_samples:
            chunk = np.frombuffer(
                raw[start_frame * frame_bytes:end_frame * frame_bytes], dtype=dtype)
            if channels == 2:
                segment = chunk.reshape(-1, 2).astype(np.float32)
            else:
                segment = np.column_stack([chunk, chunk]).astype(np.float32)
            voice_channel, confidence = detect_voice_channel(segment, sr)
        results[af["filename"]] = channel_map.get(voice_channel, 180)
    return results


def _build_scenario(kind, seconds, seed):
    """kind: left / right / both / noise"""
    n = int(seconds * SR)
    rng = np.random.default_rng(seed)
    if kind == "noise":
        left = 0.3 * rng.standard_normal(n)
        right = 0.3 * rng.standard_normal(n)
    else:
        speech = _speech(n, seed=seed)
        if kind == "left":
            left, right = speech, np.zeros(n)
        elif kind == "right":
            left, right = np.zeros(n), speech
        else:  # both
            left = speech + 0.05 * rng.standard_normal(n)
            right = speech + 0.05 * rng.standard_normal(n)
    stereo = np.column_stack([left, right])
    stereo = np.clip(stereo, -1.0, 1.0)
    return _seg((stereo * 32767).astype(np.int16))


class DetectReuseTests(unittest.TestCase):
    def test_profile_path_conclusions_match_legacy(self):
        """随机场景对照：全局谱路径与旧逐窗口路径角度结论一致。"""
        rng = np.random.default_rng(42)
        for round_no in range(12):
            kind = ("left", "right", "both", "noise")[round_no % 4]
            seconds = 30 + round_no
            original = _build_scenario(kind, seconds, seed=round_no)
            # 12~36 个片段，强制走全局谱路径（片段多于谱帧数才划算）
            n_clips = 12 + (round_no % 3) * 12
            audio_files = []
            for i in range(n_clips):
                ts = int(rng.uniform(0, max(1, seconds * 1000 - 11000)))
                audio_files.append({"filename": f"{i:04d}.mp3", "timestamp_ms": ts})
            expected = _legacy_detect(original, audio_files, CHANNEL_MAP)
            got = detect_angles_parallel(original, audio_files, CHANNEL_MAP, max_workers=1)
            self.assertEqual(got, expected,
                             f"round {round_no} kind={kind} 结论与旧实现不一致")

    def test_profile_path_label_matches_on_clear_pans(self):
        for kind, expect_angle in (("left", 0), ("right", 180), ("both", 90)):
            original = _build_scenario(kind, 30, seed=7)
            audio_files = [{"filename": "a.mp3", "timestamp_ms": 3000}]
            got = detect_angles_parallel(original, audio_files, CHANNEL_MAP, max_workers=1)
            self.assertEqual(got["a.mp3"], expect_angle, kind)

    def test_sparse_clips_fall_back_to_legacy_and_agree(self):
        """片段稀疏时退回逐窗口路径，结论仍与旧实现一致。"""
        original = _build_scenario("left", 120, seed=3)  # 谱帧数 > 3×869
        audio_files = [{"filename": f"{i}.mp3", "timestamp_ms": ts}
                       for i, ts in enumerate((1000, 40000, 80000))]
        expected = _legacy_detect(original, audio_files, CHANNEL_MAP)
        got = detect_angles_parallel(original, audio_files, CHANNEL_MAP, max_workers=1)
        self.assertEqual(got, expected)

    def test_aligned_offsets_reproduce_energies_exactly(self):
        """对齐 hop 的偏移下：全局谱区间和 == 整窗逐帧能量和。"""
        original = _build_scenario("both", 30, seed=5)
        raw = np.frombuffer(original.raw_data, dtype=np.int16).reshape(-1, 2)
        left = raw[:, 0].astype(np.float32)
        frame_len, hop = _frame_params(SR)
        profile = _voice_band_energy_profile(left, SR)
        start = 100 * hop                      # 对齐 hop
        window = left[start:start + 8 * hop]
        per_frame = _voice_band_frame_energies(window, SR)
        k0 = start // hop
        k1 = k0 + len(per_frame) - 1
        self.assertAlmostEqual(
            float(profile[k0:k1 + 1].sum()), float(per_frame.sum()), places=4)

    def test_mono_shortcut_returns_both_without_profile(self):
        mono = (np.column_stack([_speech(int(30 * SR), seed=1)]).ravel() * 32767)
        original = SimpleNamespace(
            raw_data=mono.astype(np.int16).tobytes(),
            frame_rate=SR, channels=1, sample_width=2)
        audio_files = [{"filename": "m.mp3", "timestamp_ms": 3000}]
        got = detect_angles_parallel(original, audio_files, CHANNEL_MAP, max_workers=1)
        self.assertEqual(got["m.mp3"], CHANNEL_MAP["both"])

    def test_short_window_returns_both(self):
        original = _build_scenario("left", 10, seed=2)
        audio_files = [{"filename": "s.mp3", "timestamp_ms": 8500}]  # 窗口 < 2s
        got = detect_angles_parallel(original, audio_files, CHANNEL_MAP, max_workers=1)
        self.assertEqual(got["s.mp3"], CHANNEL_MAP["both"])


class ProbeShortCircuitTests(unittest.TestCase):
    def test_probe_embedded_tags_skips_missing_file(self):
        ext = next(iter(audio_metadata.EMBEDDED_FORMATS))
        missing = os.path.join(tempfile.gettempdir(), f"no_such_output.{ext}")
        self.assertTrue(audio_metadata.supports_embedded_tags(missing))
        with patch("src.steps.audio_metadata.subprocess.run",
                   side_effect=AssertionError("不应起 ffprobe")) as run_mock, \
             patch("src.steps.audio_metadata.perf.COUNTERS") as counters:
            result = audio_metadata._probe_embedded_tags(missing)
        self.assertEqual(result, {})
        run_mock.assert_not_called()
        counters.incr.assert_not_called()

    def test_probe_embedded_tags_still_runs_on_existing_file(self):
        ext = next(iter(audio_metadata.EMBEDDED_FORMATS))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, f"real.{ext}")
            with open(path, "wb") as f:
                f.write(b"x")
            class _Proc:
                returncode = 1
                stdout = b""
            with patch("src.steps.audio_metadata.subprocess.run",
                       return_value=_Proc()) as run_mock:
                audio_metadata._probe_embedded_tags(path)
            run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
