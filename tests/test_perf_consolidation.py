# -*- coding: utf-8 -*-
"""P2-5 并发参数整合守卫（2026-11-12）。

覆盖：四个并发参数集中在「常规 → 性能」一区、有效并发实时预览、
保存写回配置键、配音/混音面板不再各自留一份、面板保存链路无回归。
"""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from src.widgets.mixer_panel import MixerConfigPanel
from src.widgets.tts_panel import TTSConfigPanel
from src.workflow_gui.config_mixin import GeneralSettingsPage


class _PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_config(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SimpleNamespace(
            whisper_dir="",
            workspace_dir=tmp.name,
            config={"subprojects": {"whisper_dir": ""}, "workspace_dir": tmp.name},
            tts_cfg={"edge_threads": 8, "edge_max_concurrent": 8,
                     "pipeline_tts_workers": 2},
            mixer_cfg={"thread_count": 4},
            save=lambda: None,
        )

    def test_general_page_holds_all_concurrency_params(self):
        page = GeneralSettingsPage(self._make_config())
        for attr in ("threads_spin", "max_concurrent_spin",
                     "pipeline_tts_spin", "mixer_threads_spin"):
            self.assertTrue(hasattr(page, attr), attr)

    def test_preview_shows_effective_concurrency(self):
        page = GeneralSettingsPage(self._make_config())
        self.assertIn("有效配音并发 = 8 路", page.perf_preview.text())
        self.assertIn("混音并行 4 线程", page.perf_preview.text())

    def test_preview_updates_live_and_saves_keys(self):
        config = self._make_config()
        page = GeneralSettingsPage(config)
        page.threads_spin.setValue(12)          # 池 12 / 闸门 8 -> 有效 8
        self.assertIn("有效配音并发 = 8 路", page.perf_preview.text())
        page.max_concurrent_spin.setValue(6)    # 池 12 / 闸门 6 -> 有效 6
        self.assertIn("有效配音并发 = 6 路", page.perf_preview.text())
        self.assertEqual(config.tts_cfg["edge_threads"], 12)
        self.assertEqual(config.tts_cfg["edge_max_concurrent"], 6)
        page.mixer_threads_spin.setValue(2)
        self.assertEqual(config.mixer_cfg["thread_count"], 2)
        self.assertIn("混音并行 2 线程", page.perf_preview.text())

    def test_tts_panel_no_longer_carries_concurrency_spins(self):
        config = SimpleNamespace(
            tts_cfg={"tts_mode": "edge", "edge_voice": "zh-CN-XiaoxiaoNeural"},
            save=lambda: None)
        with patch.object(TTSConfigPanel, "_refresh_cache_info"):
            panel = TTSConfigPanel(config)
        for attr in ("threads_spin", "max_concurrent_spin", "pipeline_tts_spin"):
            self.assertFalse(hasattr(panel, attr), attr)
        # 面板保存链路仍完整（不因拆走控件而 AttributeError）
        panel._save()
        self.assertEqual(config.tts_cfg["edge_voice"], "zh-CN-XiaoxiaoNeural")

    def test_mixer_panel_no_longer_carries_thread_spin(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = SimpleNamespace(
            mixer_cfg={"thread_count": 4}, config={"mixer": {}},
            workspace_dir=tmp.name, save=lambda: None)
        panel = MixerConfigPanel(config)
        self.assertFalse(hasattr(panel, "thread_spin"))
        panel._save()  # 无 AttributeError 即通过
        self.assertEqual(config.mixer_cfg["thread_count"], 4)  # 面板不再覆写


if __name__ == "__main__":
    unittest.main()
