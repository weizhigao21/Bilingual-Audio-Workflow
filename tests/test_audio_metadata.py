import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from pydub import AudioSegment

from src.steps.audio_metadata import (
    edit_output_metadata, read_editable_tags, sidecar_path,
)
from src.steps.step3_mixer import mix_single_task
from src.task_manager import TaskInfo
from src.version import APP_VERSION


def make_wav(path, seconds, channels=2):
    rate = 24000
    t = np.arange(round(seconds * rate)) / rate
    mono = (4000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    samples = np.column_stack([mono, mono]) if channels == 2 else mono
    AudioSegment(samples.tobytes(), sample_width=2, frame_rate=rate,
                 channels=channels).export(str(path), format="wav").close()


def file_tags(path):
    result = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path),
    ])
    return {key.lower(): value for key, value in json.loads(result)["format"].get("tags", {}).items()}


def packet_hash(path):
    result = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-c", "copy", "-f", "hash", "-hash", "sha256", "-",
    ])
    return result.strip()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AudioMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.wav"
        make_wav(self.source, 2)
        self.clips = self.root / "clips"
        self.clips.mkdir()
        make_wav(self.clips / "0001_00-00-00.20_a.wav", 0.5, channels=1)
        self.task = TaskInfo("metadata-test", str(self.source), "source",
                             str(self.root), step2_output=str(self.clips))
        self.cfg = SimpleNamespace(mixer_cfg={
            "output_folder": str(self.root), "output_format": "mp3",
            "wav_bit_depth": 16, "audio_sample_rate": 24000,
            "audio_channels": 2, "skip_existing": False,
            "channel_detect": False, "align_onset": False,
            "content_alignment": False, "auto_volume": "off",
            "peak_mode": "peak", "streaming_threshold_minutes": 1000,
        })

    def _mix(self, fmt="mp3", streaming=False):
        self.cfg.mixer_cfg["output_format"] = fmt
        self.cfg.mixer_cfg["streaming_threshold_minutes"] = 0 if streaming else 1000
        ok, result = mix_single_task(self.task, self.cfg)
        self.assertTrue(ok, result)
        return Path(result)

    def test_automatic_version_tags_and_provenance_in_both_mixers(self):
        for streaming, fmt in ((False, "mp3"), (True, "m4a")):
            with self.subTest(streaming=streaming, format=fmt):
                output = self._mix(fmt, streaming)
                tags = file_tags(output)
                self.assertEqual(tags["title"], "source")
                self.assertIn(APP_VERSION, tags["comment"])
                record = json.loads(Path(sidecar_path(output)).read_text(encoding="utf-8"))
                self.assertEqual(record["app_version"], APP_VERSION)
                self.assertEqual(record["metadata"]["title"], "source")
                self.assertEqual(record["source_file"], "source.wav")
                self.assertNotIn(str(self.root), json.dumps(record))

    def test_edit_embedded_tags_without_reencoding(self):
        output = self._mix()
        original_hash = packet_hash(output)
        tags = {"title": "新标题", "artist": "歌手", "album": "专辑", "comment": "试听版"}
        edit_output_metadata(str(output), tags, self.task)
        self.assertEqual(packet_hash(output), original_hash)
        embedded = file_tags(output)
        self.assertEqual(embedded["title"], tags["title"])
        self.assertEqual(embedded["artist"], tags["artist"])
        self.assertEqual(embedded["album"], tags["album"])
        self.assertIn("试听版", embedded["comment"])
        self.assertIn(APP_VERSION, embedded["comment"])
        self.assertEqual(read_editable_tags(str(output)), tags)
        record = json.loads(Path(sidecar_path(output)).read_text(encoding="utf-8"))
        self.assertEqual(record["metadata"], tags)
        self.assertIn("metadata_updated_at_utc", record)
        # 重新混音沿用用户编辑过的标题等字段。
        self._mix()
        self.assertEqual(file_tags(output)["title"], tags["title"])

    def test_export_metadata_can_be_disabled_in_both_mixers(self):
        self.cfg.mixer_cfg["metadata_enabled"] = False
        for streaming, fmt in ((False, "mp3"), (True, "m4a")):
            with self.subTest(streaming=streaming):
                output = self._mix(fmt, streaming)
                tags = file_tags(output)
                self.assertNotIn("title", tags)
                self.assertNotIn(APP_VERSION, tags.get("comment", ""))
                record = json.loads(Path(sidecar_path(output)).read_text(encoding="utf-8"))
                self.assertEqual(record["app_version"], APP_VERSION)
                self.assertEqual(record["metadata"], {
                    "title": "", "artist": "", "album": "", "comment": "",
                })

    def test_selected_export_fields_and_values(self):
        self.cfg.mixer_cfg["metadata_fields"] = {
            "title": True, "artist": True, "album": False,
            "comment": False, "version": False,
        }
        self.cfg.mixer_cfg["metadata_values"] = {
            "title": "双语 {文件名}", "artist": "示例作者",
            "album": "不应写入", "comment": "不应写入",
        }
        for streaming, fmt in ((False, "mp3"), (True, "m4a")):
            with self.subTest(streaming=streaming):
                output = self._mix(fmt, streaming)
                tags = file_tags(output)
                self.assertEqual(tags["title"], "双语 source")
                self.assertEqual(tags["artist"], "示例作者")
                self.assertNotIn("album", tags)
                self.assertNotIn(APP_VERSION, tags.get("comment", ""))

    def test_wav_and_aac_keep_information_in_sidecar(self):
        tags = {"title": "母版", "artist": "", "album": "", "comment": "请保留"}
        for fmt in ("wav", "aac"):
            with self.subTest(format=fmt):
                output = self._mix(fmt)
                before = output.read_bytes()
                edit_output_metadata(str(output), tags, self.task)
                self.assertEqual(output.read_bytes(), before)
                self.assertEqual(read_editable_tags(str(output)), tags)

    def test_remux_failure_keeps_original_and_cleans_temp(self):
        output = self._mix()
        before = output.read_bytes()
        sidecar_before = Path(sidecar_path(output)).read_bytes()
        with patch("src.steps.audio_metadata._run_ffmpeg", side_effect=RuntimeError("mux failure")):
            with self.assertRaisesRegex(RuntimeError, "mux failure"):
                edit_output_metadata(str(output), {"title": "new"}, self.task)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(Path(sidecar_path(output)).read_bytes(), sidecar_before)
        self.assertEqual(list(self.root.glob(".mix-tags-*")), [])

    def test_sidecar_failure_reports_warning_but_keeps_embedded_edit(self):
        output = self._mix()
        original_hash = packet_hash(output)
        with patch("src.steps.audio_metadata.write_provenance",
                   side_effect=OSError("disk full")):
            warning = edit_output_metadata(
                str(output), {"title": "已更新", "comment": "新备注"}, self.task
            )
        self.assertIn("制作记录写入失败", warning)
        self.assertEqual(packet_hash(output), original_hash)
        self.assertEqual(read_editable_tags(str(output))["title"], "已更新")

    def test_cancelled_edit_keeps_original(self):
        output = self._mix()
        before = output.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "停止"):
            edit_output_metadata(str(output), {"title": "cancelled"},
                                 self.task, stop_check=lambda: True)
        self.assertEqual(output.read_bytes(), before)

    def test_editing_legacy_output_does_not_claim_it_was_mixed_now(self):
        output = self.root / "legacy.mp3"
        with open(self.source, "rb") as source:
            AudioSegment.from_file(source).export(str(output), format="mp3").close()
        edit_output_metadata(str(output), {"title": "旧成品"}, self.task)
        embedded = file_tags(output)
        self.assertIn("信息编辑", embedded["comment"])
        self.assertNotIn("混音：", embedded["comment"])
        record = json.loads(Path(sidecar_path(output)).read_text(encoding="utf-8"))
        self.assertNotIn("app_version", record)
        self.assertEqual(record["metadata_editor_version"], APP_VERSION)


if __name__ == "__main__":
    unittest.main()
