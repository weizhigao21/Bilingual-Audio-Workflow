# -*- coding: utf-8 -*-
"""混音健壮性守卫（2026-10-10 复检修复项）。

覆盖三点：
1. 长音频路径下（mix_items 传片段路径）单个损坏/为空片段只跳过，
   不拖垮整段混音——与短音频路径 step3_mixer 的行为对齐；
2. get_mix_audio_files 过滤 0 字节片段；
3. 内容对齐的短时能量计算：与逐帧循环数值等价，且跨分块边界正确。
"""
import os
import tempfile
import unittest

import numpy as np
from pydub import AudioSegment

from src.steps.audio_utils import detection
from src.steps.audio_utils.mix_files import clear_mix_cache, get_mix_audio_files
from src.steps.audio_utils.mixing import mix_with_numpy


SR = 8000


def _audio(samples, sr=SR, width=2):
    samples = np.asarray(samples)
    channels = samples.shape[1] if samples.ndim == 2 else 1
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[width]
    return AudioSegment(samples.astype(dtype).tobytes(),
                        sample_width=width, frame_rate=sr, channels=channels)


def _write_wav(path, samples, sr=SR):
    _audio(samples, sr).export(path, format="wav")


class ClipRobustnessTests(unittest.TestCase):
    """坏片段不得让整段混音失败。"""

    def tearDown(self):
        clear_mix_cache()

    def test_corrupt_clip_is_skipped_instead_of_aborting(self):
        original = _audio(np.zeros(SR // 2, dtype=np.int16))
        with tempfile.TemporaryDirectory() as folder:
            bad = os.path.join(folder, "0001_00-00-00.00_坏.wav")
            with open(bad, "wb") as handle:
                handle.write(b"this is definitely not audio data")
            good = os.path.join(folder, "0002_00-00-00.00_好.wav")
            _write_wav(good, np.full(SR // 4, 6000, dtype=np.int16))

            progress = []
            output = mix_with_numpy(
                original,
                [(bad, 0, 180), (good, 0, 180)],
                item_callback=lambda done, total: progress.append((done, total)),
            )

        self.assertEqual(len(output), len(original))
        # 被跳过的片段也要推进进度，否则进度条会卡住
        self.assertEqual(progress[-1], (2, 2))
        # 完好片段确实被混入（原音频是静音）
        mixed = np.frombuffer(output.raw_data, dtype=np.int16)
        self.assertGreater(int(np.abs(mixed).max()), 0)

    def test_only_corrupt_clips_leave_original_intact(self):
        base = (np.arange(SR) % 100).astype(np.int16)
        original = _audio(np.tile(base[:, None], (1, 2)))     # 立体声
        with tempfile.TemporaryDirectory() as folder:
            empty = os.path.join(folder, "0001_00-00-00.00_空.wav")
            open(empty, "wb").close()                          # 0 字节
            output = mix_with_numpy(original, [(empty, 0, 180)])

        self.assertEqual(output.raw_data, original.raw_data)

    def test_zero_byte_clip_filtered_from_mix_list(self):
        with tempfile.TemporaryDirectory() as folder:
            _write_wav(os.path.join(folder, "0001_00-00-00.00_ab.wav"),
                       np.zeros(64, dtype=np.int16))
            open(os.path.join(folder, "0002_00-00-00.00_cd.wav"), "wb").close()
            clear_mix_cache()
            names = [item["filename"] for item in get_mix_audio_files(folder)]

        self.assertEqual(names, ["0001_00-00-00.00_ab.wav"])


def _legacy_frame_rms_energy(segment, frame_len, hop_len):
    """修复前的逐帧实现，作为数值基准。"""
    num_frames = max(1, (len(segment) - frame_len) // hop_len + 1)
    energy = np.zeros(num_frames)
    for i in range(num_frames):
        start = i * hop_len
        end = min(start + frame_len, len(segment))
        if end > start:
            energy[i] = np.sqrt(np.mean(segment[start:end] ** 2))
    return energy


class OnsetEnergyTests(unittest.TestCase):
    """内容对齐的能量计算：等价性与分块边界。"""

    FRAME = 1024
    HOP = 512

    def test_energy_matches_per_frame_loop(self):
        rng = np.random.default_rng(20261010)
        lengths = (self.FRAME, self.FRAME + self.HOP, 5000,
                   self.FRAME + self.HOP * 400 + 7)
        for length in lengths:
            with self.subTest(length=length):
                segment = rng.random(length).astype(np.float32)
                fast = detection._frame_rms_energy(segment, self.FRAME, self.HOP)
                slow = _legacy_frame_rms_energy(segment, self.FRAME, self.HOP)
                self.assertEqual(len(fast), len(slow))
                # 基准实现用 float32 累加（np.mean 作用于 float32），新实现用
                # float64 累积和，因此存在 ~1e-7 量级差异；新实现精度更高。
                np.testing.assert_allclose(fast, slow, rtol=1e-6, atol=1e-6)

    def test_energy_correct_across_block_boundaries(self):
        """跨多个 128 帧分块时，能量峰必须落在脉冲真实位置。"""
        length = 200_000
        spike_frame = 150_000
        signal = np.zeros(length, dtype=np.float64)
        signal[spike_frame:spike_frame + 4000] = 1.0

        energy = detection._frame_rms_energy(signal, self.FRAME, self.HOP)
        peak_start = int(np.argmax(energy)) * self.HOP

        self.assertLess(abs(peak_start - spike_frame), self.FRAME)

    def test_empty_segment_returns_empty_energy(self):
        self.assertEqual(len(detection._frame_rms_energy(
            np.zeros(0), self.FRAME, self.HOP)), 0)


if __name__ == "__main__":
    unittest.main()
