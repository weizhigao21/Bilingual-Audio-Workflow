# -*- coding: utf-8 -*-
"""P1-2 格式收口守卫。

红线：
1. Edge 新产物按真实格式命名 .mp3，不残留 .part 临时文件；
2. 存量 .wav 与新命名 .mp3 混杂期：解析/扫描双扩展名放行、
   同序号去重（否则同一条字幕被混音两遍）、跳过已存在跨扩展名命中；
3. 存量扫描器只读，绝不改动文件。
"""
import os
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

from src.steps.tts_utils import generate_filename, find_existing_clip, edge_tts_task
from src.steps.audio_utils.mix_files import (
    parse_filename, get_mix_audio_files, clear_mix_cache,
)
from src.steps.audio_utils import clip_scan


def _write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def _write_wav(path, frames=160):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x01" * frames)


MP3_BYTES = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 64


class NamingTests(unittest.TestCase):
    def test_generate_filename_default_is_wav(self):
        path = generate_filename(1, "00:00:01.00", "你好世界", "/x")
        self.assertTrue(path.endswith("0001_00-00-01.00_你好.wav"))

    def test_generate_filename_mp3_ext(self):
        path = generate_filename(1, "00:00:01.00", "你好世界", "/x", ext=".mp3")
        self.assertTrue(path.endswith("0001_00-00-01.00_你好.mp3"))

    def test_generate_filename_invalid_ext_falls_back_to_wav(self):
        path = generate_filename(1, "00:00:01.00", "你好", "/x", ext=".flac")
        self.assertTrue(path.endswith(".wav"))

    def test_find_existing_clip_finds_alternate_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            wav_path = generate_filename(1, "00:00:01.00", "你好", folder)
            _write_bytes(wav_path, b"RIFF....WAVE" + b"\x00" * 8)
            mp3_path = generate_filename(1, "00:00:01.00", "你好", folder, ext=".mp3")
            # 存量 .wav 在场：查 .mp3 名应命中 .wav
            self.assertEqual(find_existing_clip(mp3_path), wav_path)
            # 反向：.mp3 在场查 .wav 名
            os.remove(wav_path)
            _write_bytes(mp3_path, MP3_BYTES)
            self.assertEqual(find_existing_clip(wav_path), mp3_path)

    def test_find_existing_clip_ignores_zero_byte_and_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            wav_path = generate_filename(1, "00:00:01.00", "你好", folder)
            _write_bytes(wav_path, b"")
            self.assertIsNone(find_existing_clip(wav_path))
            self.assertIsNone(find_existing_clip(
                generate_filename(2, "00:00:02.00", "你好", folder)))


class ParseTests(unittest.TestCase):
    def test_parse_accepts_both_extensions(self):
        base = parse_filename("0001_00-00-01.00_你好.wav")
        mp3 = parse_filename("0001_00-00-01.00_你好.mp3")
        self.assertEqual(base["sequence"], mp3["sequence"])
        self.assertEqual(base["timestamp_ms"], mp3["timestamp_ms"])
        self.assertEqual(mp3["filename"], "0001_00-00-01.00_你好.mp3")

    def test_parse_rejects_temp_and_unknown(self):
        for name in ("0001_00-00-01.00_你好.mp3.part", "0001_00-00-01.00_你好.txt",
                     "0001_00-00-01.00_你好.wav.bak", ".tts-0001_00-00-01.00_你好.mp3"):
            self.assertIsNone(parse_filename(name), name)


class MixFilesMixedPeriodTests(unittest.TestCase):
    def setUp(self):
        clear_mix_cache()

    def tearDown(self):
        clear_mix_cache()

    def test_mixed_period_lists_both_extensions(self):
        with tempfile.TemporaryDirectory() as folder:
            _write_bytes(os.path.join(folder, "0001_00-00-01.00_甲.wav"), MP3_BYTES)
            _write_bytes(os.path.join(folder, "0002_00-00-02.00_乙.mp3"), MP3_BYTES)
            names = [i["filename"] for i in get_mix_audio_files(folder)]
            self.assertEqual(names, ["0001_00-00-01.00_甲.wav", "0002_00-00-02.00_乙.mp3"])

    def test_duplicate_sequence_keeps_newest(self):
        with tempfile.TemporaryDirectory() as folder:
            old = os.path.join(folder, "0001_00-00-01.00_甲.wav")
            new = os.path.join(folder, "0001_00-00-01.00_甲.mp3")
            _write_bytes(old, MP3_BYTES)
            _write_bytes(new, MP3_BYTES)
            os.utime(old, (100, 100))
            os.utime(new, (200, 200))
            items = get_mix_audio_files(folder)
            self.assertEqual([i["filename"] for i in items], [os.path.basename(new)])

    def test_duplicate_sequence_tie_prefers_mp3(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = os.path.join(folder, "0001_00-00-01.00_甲.wav")
            mp3 = os.path.join(folder, "0001_00-00-01.00_甲.mp3")
            _write_bytes(wav, MP3_BYTES)
            _write_bytes(mp3, MP3_BYTES)
            os.utime(wav, (100, 100))
            os.utime(mp3, (100, 100))
            items = get_mix_audio_files(folder)
            self.assertEqual([i["filename"] for i in items], [os.path.basename(mp3)])


class EdgeNamingTests(unittest.TestCase):
    def test_edge_output_uses_mp3_name_and_no_part_residue(self):
        async def fake_generate(text, voice, rate, volume, save_path):
            _write_bytes(save_path, MP3_BYTES)

        cache = SimpleNamespace(
            get_cached_audio=lambda *a, **k: None,
            save_audio_cache=lambda *a, **k: True)
        with tempfile.TemporaryDirectory() as folder, \
                patch("src.steps.tts_utils._edge_tts_generate", fake_generate):
            ok, _msg = edge_tts_task(1, "00:00.00", "测试", "zh-CN-YunxiNeural",
                                     "+0%", "+0%", folder, cache, max_retries=1)
            self.assertTrue(ok)
            names = sorted(os.listdir(folder))
        self.assertEqual(names, ["0001_00-00.00_测试.mp3"])


class ScanReadOnlyTests(unittest.TestCase):
    def test_sniff_formats(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = os.path.join(folder, "a.wav")
            mp3_id3 = os.path.join(folder, "b.mp3")
            mp3_sync = os.path.join(folder, "c.bin")
            junk = os.path.join(folder, "d.dat")
            _write_wav(wav)
            _write_bytes(mp3_id3, MP3_BYTES)
            _write_bytes(mp3_sync, b"\xff\xfb\x90\x00" + b"\x00" * 32)
            _write_bytes(junk, b"\x01\x02\x03\x04" * 8)
            self.assertEqual(clip_scan.sniff_format(wav), "wav")
            self.assertEqual(clip_scan.sniff_format(mp3_id3), "mp3")
            self.assertEqual(clip_scan.sniff_format(mp3_sync), "mp3")
            self.assertEqual(clip_scan.sniff_format(junk), "other")

    def test_scan_classifies_and_never_modifies(self):
        with tempfile.TemporaryDirectory() as folder:
            files = {
                "0001_00-00-01.00_甲.wav": MP3_BYTES,      # 待迁移：mp3 顶 .wav 名
                "0002_00-00-02.00_乙.mp3": MP3_BYTES,      # 名实相符
                "0003_00-00-03.00_丙.wav": None,           # 真 wav
                "0004_00-00-04.00_丁.mp3": None,           # 反向错名：wav 顶 .mp3 名
            }
            paths = {}
            for name, payload in files.items():
                path = os.path.join(folder, name)
                if payload is None:
                    _write_wav(path)
                else:
                    _write_bytes(path, payload)
                paths[name] = path
            before = {n: (os.path.getmtime(p), os.path.getsize(p))
                      for n, p in paths.items()}

            report = clip_scan.scan_dir(folder)

            self.assertEqual(report["counts"]["wav_named_mp3"], 1)
            self.assertEqual(report["counts"]["mp3_ok"], 1)
            self.assertEqual(report["counts"]["wav_ok"], 1)
            self.assertEqual(report["counts"]["mp3_named_wav"], 1)
            self.assertEqual(report["misnamed_count"], 2)
            self.assertEqual(report["total_files"], 4)
            after = {n: (os.path.getmtime(p), os.path.getsize(p))
                     for n, p in paths.items()}
            self.assertEqual(before, after, "扫描器必须只读")


if __name__ == "__main__":
    unittest.main()
