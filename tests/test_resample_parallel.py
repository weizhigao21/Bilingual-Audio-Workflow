# -*- coding: utf-8 -*-
"""守卫 P2-1：片段准备（解码+单声道+重采样）并发化，输出与串行逐位一致。

语义红线：
- 消费顺序、进度回调、跳过（解码失败/空片段）语义与旧串行路径一致；
- 重采样/起始检测失败 = 整段混音失败（不许被并发吞成"跳过"）；
- 取消（stop_check）仍能及时中止。
"""
import math
import os
import struct
import tempfile
import threading
import unittest

from pydub import AudioSegment

from src.steps import perf
from src.steps.audio_utils import mixing


def _tone(sr, seconds=0.25, freq=440, amp=7000):
    n = int(sr * seconds)
    frames = bytearray()
    for i in range(n):
        frames += struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / sr)))
    return bytes(frames)


def _seg(sr, seconds=0.25, channels=1):
    raw = _tone(sr, seconds)
    if channels == 2:
        raw = b"".join(raw[i:i + 2] * 2 for i in range(0, len(raw), 2))
    return AudioSegment(raw, sample_width=2, frame_rate=sr, channels=channels)


class PreparePipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.original = _seg(44100, seconds=2.0, channels=2)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_clip(self, name, sr):
        path = os.path.join(self.dir, name)
        _seg(sr).export(path, format="wav").close()
        return path

    def _items(self, rates):
        """按给定采样率序列造片段路径条目（混有需/不需重采样的）。"""
        items = []
        for i, sr in enumerate(rates):
            path = self._write_clip(f"{i:04d}_{i * 250:05d}_词.wav", sr)
            items.append((path, i * 250, 90))
        return items

    def _mix(self, items, **kwargs):
        kwargs.setdefault("max_workers", 4)
        return mixing.mix_with_numpy(self.original, items, **kwargs)

    def test_parallel_output_identical_to_sequential(self):
        rates = [24000, 44100, 24000, 48000, 24000, 44100] * 2
        items = self._items(rates)
        seq = self._mix(items, max_workers=1)
        par = self._mix(items, max_workers=8)
        self.assertEqual(seq.raw_data, par.raw_data,
                         "并发准备必须与串行输出逐位一致")

    def test_progress_callback_keeps_original_order(self):
        items = self._items([24000] * 6)
        calls = []
        self._mix(items, item_callback=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls, [(i, 6) for i in range(1, 7)])

    def test_bad_clips_are_skipped_not_fatal(self):
        good1 = self._write_clip("0000_00000_好.wav", 24000)
        good2 = self._write_clip("0002_00500_好.wav", 24000)
        missing = os.path.join(self.dir, "0001_00250_缺.wav")
        with open(os.path.join(self.dir, "0003_00750_坏.wav"), "wb") as f:
            f.write(b"not audio at all")
        items = [
            (missing, 0, 90),
            (good1, 250, 90),
            (os.path.join(self.dir, "0003_00750_坏.wav"), 750, 90),
            (good2, 500, 90),
        ]
        calls = []
        result = self._mix(items, item_callback=lambda d, t: calls.append(d))
        self.assertIsNotNone(result)
        self.assertEqual(sorted(calls), [1, 2, 3, 4])

    def test_fatal_resample_error_is_not_swallowed(self):
        items = self._items([24000])  # 需要重采样 → 会走 resample_audio

        def _boom(*args, **kwargs):
            raise RuntimeError("重采样失败")

        original_fn = mixing.resample_audio
        mixing.resample_audio = _boom
        try:
            with self.assertRaises(RuntimeError):
                self._mix(items)
        finally:
            mixing.resample_audio = original_fn

    def test_stop_check_aborts_promptly(self):
        items = self._items([24000] * 30)
        state = {"n": 0}

        def stop_after_a_while():
            state["n"] += 1
            return state["n"] > 20

        with self.assertRaises(RuntimeError):
            self._mix(items, stop_check=stop_after_a_while)

    def test_counters_show_resample_only_for_mismatched_rates(self):
        items = self._items([24000, 24000, 44100, 44100, 48000])
        before = perf.COUNTERS.snapshot()
        self._mix(items, max_workers=3)
        after = perf.COUNTERS.snapshot()
        self.assertEqual(after.get("clip_decode_fast", 0)
                         - before.get("clip_decode_fast", 0), 5)
        # 24k×2 + 48k×1 需重采样（44.1k 目标），44.1k×2 不需要
        self.assertEqual(after.get("ffmpeg_procs", 0)
                         - before.get("ffmpeg_procs", 0), 3)

    def test_preloaded_segments_also_parallel(self):
        """step3 旧路径传 AudioSegment（非路径），并发准备同样成立。"""
        items = [(_seg(24000), i * 250, 90) for i in range(6)]
        seq = self._mix(items, max_workers=1)
        par = self._mix(items, max_workers=4)
        self.assertEqual(seq.raw_data, par.raw_data)

    def test_thread_safety_of_concurrent_prepare(self):
        """多 worker 压测：无竞态、结果确定（连跑两次一致）。"""
        items = self._items([24000, 48000, 44100, 24000] * 3)
        a = self._mix(items, max_workers=8)
        b = self._mix(items, max_workers=8)
        self.assertEqual(a.raw_data, b.raw_data)


if __name__ == "__main__":
    unittest.main()
