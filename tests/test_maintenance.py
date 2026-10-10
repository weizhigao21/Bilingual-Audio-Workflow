# -*- coding: utf-8 -*-
"""配置与日志维护守卫（2026-10-10 复检修复项）。

覆盖两点：
1. 已废弃的配置键在加载时被剔除，且迁移结果落盘；
   媒体后缀的 legacy → 新版升级同样要落盘（否则每次启动都要重新升级）；
   无迁移时不得重写配置文件。
2. 历史日志按保留期清理，且不误删非本程序文件。
"""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from src.config import (
    LEGACY_WHISPER_MEDIA_SUFFIXES, WHISPER_MEDIA_SUFFIXES, WorkflowConfig,
)
from src.steps import tts_logger


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigMigrationTests(unittest.TestCase):
    def _load_with(self, saved):
        """在临时目录里伪造配置文件，返回 (config, 落盘后的 json)。"""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "workflow_config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(saved, handle, ensure_ascii=False)
            with patch("src.config.get_config_path", return_value=path):
                config = WorkflowConfig()
            with open(path, encoding="utf-8") as handle:
                return config, json.load(handle)

    def test_deprecated_keys_removed_and_persisted(self):
        config, on_disk = self._load_with({
            "mixer": {"use_gpu": True, "pipeline_mix_workers": 1, "volume_db": 3.0},
        })

        self.assertNotIn("use_gpu", config.mixer_cfg)
        self.assertNotIn("pipeline_mix_workers", config.mixer_cfg)
        self.assertEqual(config.mixer_cfg["volume_db"], 3.0)   # 有效键必须保留
        self.assertNotIn("use_gpu", on_disk["mixer"])          # 已回写磁盘
        self.assertNotIn("pipeline_mix_workers", on_disk["mixer"])

    def test_media_suffix_upgrade_is_persisted(self):
        config, on_disk = self._load_with({
            "whisper": {"audio_suffixes": LEGACY_WHISPER_MEDIA_SUFFIXES},
        })

        self.assertEqual(config.whisper_cfg["audio_suffixes"], WHISPER_MEDIA_SUFFIXES)
        self.assertEqual(on_disk["whisper"]["audio_suffixes"], WHISPER_MEDIA_SUFFIXES)

    def test_no_migration_keeps_file_untouched(self):
        saved = {
            "mixer": {"volume_db": 1.5},
            "whisper": {"audio_suffixes": WHISPER_MEDIA_SUFFIXES},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "workflow_config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(saved, handle, ensure_ascii=False)
            before = os.stat(path).st_mtime_ns
            with patch("src.config.get_config_path", return_value=path):
                config = WorkflowConfig()
            after = os.stat(path).st_mtime_ns

        self.assertEqual(config.mixer_cfg["volume_db"], 1.5)
        self.assertEqual(before, after)


class LogPurgeTests(unittest.TestCase):
    def test_old_logs_purged_and_recent_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            old = os.path.join(folder, "tts_20200101.log")
            crash_old = os.path.join(folder, "crash_20200101.log")
            recent = os.path.join(folder, "tts_20990101.log")
            foreign = os.path.join(folder, "keep_me.log")
            for path in (old, crash_old, recent, foreign):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            stale = time.time() - 40 * 86400
            os.utime(old, (stale, stale))
            os.utime(crash_old, (stale, stale))

            removed = tts_logger.purge_old_logs(days=30, log_dir=folder)

            self.assertEqual(removed, 2)
            self.assertFalse(os.path.exists(old))
            self.assertFalse(os.path.exists(crash_old))
            self.assertTrue(os.path.exists(recent))    # 未过期
            self.assertTrue(os.path.exists(foreign))   # 非本程序日志不动

    def test_purge_tolerates_missing_dir_and_disabled(self):
        self.assertEqual(
            tts_logger.purge_old_logs(days=30, log_dir="Z:/definitely-missing"), 0)
        self.assertEqual(tts_logger.purge_old_logs(days=0), 0)

    def test_startup_wires_purge(self):
        with open(os.path.join(_PROJECT_ROOT, "main.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("purge_old_logs()", source)


class DeadCodeRemovalTests(unittest.TestCase):
    """复检确认无调用的实现不应再对外导出。"""

    def test_removed_helpers_are_gone(self):
        from src.steps import audio_utils
        from src.steps.audio_utils import ffmpeg_utils, mixing

        self.assertFalse(hasattr(mixing, "overlay_with_pan"))
        self.assertFalse(hasattr(ffmpeg_utils, "export_with_nvenc"))
        self.assertFalse(hasattr(audio_utils, "overlay_with_pan"))
        self.assertFalse(hasattr(audio_utils, "export_with_nvenc"))


class BatchMixinSelectionTests(unittest.TestCase):
    def test_batch_executor_clears_mix_cache(self):
        """批量执行收尾必须清理片段列表缓存（by_task/by_step 走 MixerWorker）。"""
        path = os.path.join(_PROJECT_ROOT, "src", "steps", "batch_executor",
                            "executor.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("clear_mix_cache()", source)


if __name__ == "__main__":
    unittest.main()
