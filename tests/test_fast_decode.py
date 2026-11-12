# -*- coding: utf-8 -*-
"""守卫 P1-1：片段解码优先 soundfile 进程内路径，回退 pydub 行为不变。

背景（2026-11-12 实测）：AudioSegment.from_file(文件对象) 对 wav 也要
~103ms/次（pydub 起 ffprobe+ffmpeg 子进程），soundfile 同文件 ~0.3ms/次。
「mp3 内容 .wav 命名」是 Edge 缓存文件的真实形态，必须能正确解码。
"""
import math
import os
import struct
import tempfile
import unittest
import wave

from pydub import AudioSegment

from src.steps import perf
from src.steps.audio_utils import decode


def _write_wav(path, sr=24000, channels=1, seconds=0.25, freq=440):
    n = int(sr * seconds)
    frames = bytearray()
    for i in range(n):
        v = int(7000 * math.sin(2 * math.pi * freq * i / sr))
        frames += struct.pack("<h", v) * channels
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return n / sr * 1000.0  # 预期毫秒时长


def _counter_delta(before, name):
    after = perf.COUNTERS.snapshot()
    return after.get(name, 0) - before.get(name, 0)


class LoadAudioSegmentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_wav_goes_through_fast_path(self):
        path = os.path.join(self.dir, "clip.wav")
        expected_ms = _write_wav(path)
        before = perf.COUNTERS.snapshot()
        seg = decode.load_audio_segment(path)
        self.assertEqual(seg.frame_rate, 24000)
        self.assertEqual(seg.channels, 1)
        self.assertAlmostEqual(len(seg), expected_ms, delta=1)
        self.assertEqual(_counter_delta(before, "clip_decode_fast"), 1)
        self.assertEqual(_counter_delta(before, "clip_decode_fallback"), 0)

    def test_parity_with_pydub_decode(self):
        path = os.path.join(self.dir, "clip.wav")
        _write_wav(path, sr=44100, channels=2)
        fast = decode.load_audio_segment(path)
        with open(path, "rb") as f:
            legacy = AudioSegment.from_file(f)
        self.assertEqual(fast.frame_rate, legacy.frame_rate)
        self.assertEqual(fast.channels, legacy.channels)
        self.assertAlmostEqual(len(fast), len(legacy), delta=1)
        diff = max(abs(a - b) for a, b in zip(
            struct.unpack(f"<{len(fast.raw_data)//2}h", fast.raw_data),
            struct.unpack(f"<{len(legacy.raw_data)//2}h", legacy.raw_data)))
        self.assertLessEqual(diff, 1, "与 pydub 解码结果应一致（允许 1 LSB）")

    def test_mp3_content_named_wav_is_decoded(self):
        """Edge 缓存文件是 mp3 内容顶着 .wav 名，不能被解错或解失败。"""
        try:
            import soundfile as sf
            import numpy as np
        except Exception:
            self.skipTest("soundfile/numpy 不可用")
        path = os.path.join(self.dir, "edge_cache.wav")
        try:
            sr = 24000
            data = (np.sin(2 * np.pi * 440 * np.arange(sr) / sr) * 0.3).astype("float32")
            sf.write(path, data, sr, format="MP3")
        except Exception as exc:
            self.skipTest(f"当前 libsndfile 不支持写 mp3: {exc}")
        before = perf.COUNTERS.snapshot()
        seg = decode.load_audio_segment(path)
        self.assertAlmostEqual(len(seg), 1000, delta=50)
        self.assertEqual(seg.frame_rate, 24000)
        self.assertEqual(_counter_delta(before, "clip_decode_fast")
                         + _counter_delta(before, "clip_decode_fallback"), 1)

    def test_falls_back_when_soundfile_unavailable(self):
        path = os.path.join(self.dir, "clip.wav")
        _write_wav(path)
        before = perf.COUNTERS.snapshot()
        original = decode._sf
        decode._sf = None
        try:
            seg = decode.load_audio_segment(path)
        finally:
            decode._sf = original
        self.assertAlmostEqual(len(seg), 250, delta=1)
        self.assertEqual(_counter_delta(before, "clip_decode_fallback"), 1)
        self.assertEqual(_counter_delta(before, "clip_decode_fast"), 0)

    def test_falls_back_when_soundfile_read_fails(self):
        """格式不支持（sf.read 抛异常）时必须回退旧路径，而不是失败。"""
        path = os.path.join(self.dir, "clip.wav")
        _write_wav(path)
        before = perf.COUNTERS.snapshot()

        def _boom(*args, **kwargs):
            raise RuntimeError("unsupported format")

        original = decode._sf
        decode._sf = type("FakeSF", (), {"read": staticmethod(_boom)})()
        try:
            seg = decode.load_audio_segment(path)
        finally:
            decode._sf = original
        self.assertAlmostEqual(len(seg), 250, delta=1)
        self.assertEqual(_counter_delta(before, "clip_decode_fallback"), 1)

    def test_unreadable_file_raises(self):
        path = os.path.join(self.dir, "empty.wav")
        with open(path, "wb"):
            pass
        with self.assertRaises(Exception):
            decode.load_audio_segment(path)


if __name__ == "__main__":
    unittest.main()
