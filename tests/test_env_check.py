# -*- coding: utf-8 -*-
"""P1-4 启动环境自检守卫。

红线：
1. 文案全人话，0 配置键名（whisper_dir/workspace_dir 等不得出现在提示里）；
2. 用户主动跳过字幕提取（whisper_dir 为空）时不提示 infer.exe，避免每次启动骚扰；
3. 磁盘用量读不到（网络盘/权限目录）静默跳过，绝不抛异常；
4. 自检不阻断启动：collect_warnings 永不抛异常。
"""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import env_check


def _config(whisper_dir="", workspace_dir=""):
    return SimpleNamespace(whisper_dir=whisper_dir, workspace_dir=workspace_dir)


class CollectWarningsTests(unittest.TestCase):
    def _collect(self, cfg, which_result="C:/bin/tool.exe", free=100 * 1024 ** 3,
                 disk_error=None):
        def fake_disk_usage(_path):
            if disk_error is not None:
                raise disk_error
            return SimpleNamespace(free=free)

        with patch("src.env_check.shutil.which", return_value=which_result), \
             patch("src.env_check.shutil.disk_usage", side_effect=fake_disk_usage):
            return env_check.collect_warnings(cfg)

    def test_all_clear_returns_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "infer.exe"), "wb").close()
            warnings = self._collect(_config(whisper_dir=folder, workspace_dir=folder))
        self.assertEqual(warnings, [])

    def test_missing_tools_warn_with_human_text(self):
        warnings = self._collect(
            _config(whisper_dir="C:/nope", workspace_dir="C:/ws"),
            which_result=None)
        joined = "\n".join(warnings)
        self.assertIn("ffmpeg", joined)
        self.assertIn("ffprobe", joined)
        self.assertIn("infer.exe", joined)
        for key in ("whisper_dir", "workspace_dir", "disk_usage", "shutil"):
            self.assertNotIn(key, joined)

    def test_skipped_setup_stays_silent_about_infer(self):
        # whisper_dir 为空 = Setup 主动跳过字幕提取，不得提示
        warnings = self._collect(_config(whisper_dir="", workspace_dir="C:/ws"))
        self.assertEqual(warnings, [])

    def test_low_space_warns_human(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "infer.exe"), "wb").close()
            warnings = self._collect(
                _config(whisper_dir=folder, workspace_dir=folder),
                free=1 * 1024 ** 3)
        joined = "\n".join(warnings)
        self.assertIn("空间不足", joined)
        self.assertNotIn("workspace_dir", joined)

    def test_disk_usage_error_is_silent(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "infer.exe"), "wb").close()
            warnings = self._collect(
                _config(whisper_dir=folder, workspace_dir=folder),
                disk_error=OSError("network path"))
        self.assertEqual(warnings, [])

    def test_collect_never_raises(self):
        # 即便 config 是个什么都不剩的空对象也不抛异常
        warnings = env_check.collect_warnings(SimpleNamespace())
        self.assertEqual(warnings, [])


class WindowHookTests(unittest.TestCase):
    def test_main_window_exposes_env_check_channel(self):
        from src.workflow_gui.window import WorkflowMainWindow
        self.assertTrue(hasattr(WorkflowMainWindow, "_env_check_done"))
        self.assertTrue(callable(getattr(WorkflowMainWindow, "_run_env_check", None)))


if __name__ == "__main__":
    unittest.main()
