# -*- coding: utf-8 -*-
"""P2-4 输出定位 + 禁用原因可见化守卫。

红线：
1. 输出行提供「打开文件夹」直达链接（1 次点击），走 explorer /select 高亮文件；
2. 占位行（无输出）不带链接，卡片高度不受影响（布局守卫在 test_ui_theme）；
3. 所有禁用态按钮 tooltip 说明原因；启用后还原按钮自带的功能提示。
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from src.task_manager import STEP_DONE, TaskQueue
from src.widgets.step_panel import StepPanel
from src.workflow_gui.task_mixin import TaskMixin


class _Host(TaskMixin, QWidget):
    def __init__(self, queue):
        super().__init__()
        self.task_queue = queue
        self.config = SimpleNamespace(is_configured=lambda: True,
                                      mixer_cfg={"folder_prefix": False})
        self._workers = {1: None, 2: None, 3: None}
        self._batch_executor = None
        self._current_group = None
        self._selected_folder = None
        self.current_task_label = QLabel(self)
        self.current_task_subtitle = QLabel(self)
        self.step_panels = {s: StepPanel(s, str(s), self) for s in (1, 2, 3)}
        self.add_subtitle_btn = QPushButton(self)
        self.add_subtitle_btn.setToolTip("为当前任务添加字幕文件（跳过字幕提取）")
        self.add_mix_btn = QPushButton(self)
        self.add_mix_btn.setToolTip("为当前任务添加配音目录（跳过配音）")


class OutputLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = StepPanel(3, "混音")

    def test_output_row_has_folder_and_file_links(self):
        self.panel.set_output("C:/work/out.mp4", "成品")
        text = self.panel.output_label.text()
        self.assertIn("打开文件夹", text)
        self.assertIn('href="folder:', text)
        self.assertIn('href="file:///', text)

    def test_placeholder_has_no_link(self):
        self.panel.set_output("")
        self.assertNotIn("href", self.panel.output_label.text())

    def test_folder_link_invokes_explorer_select(self):
        calls = []
        with patch("src.widgets.step_panel.subprocess.Popen",
                   side_effect=lambda args, **k: calls.append(args)):
            self.panel._reveal_in_explorer("C:/work/out.mp4")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "explorer")
        self.assertIn("/select,", calls[0])

    def test_reveal_falls_back_to_open_folder(self):
        with patch("src.widgets.step_panel.subprocess.Popen",
                   side_effect=OSError("no explorer")), \
             patch("src.widgets.step_panel.os.startfile") as startfile:
            self.panel._reveal_in_explorer("C:/work/out.mp4")
        startfile.assert_called_once()

    def test_link_dispatch_routes_correctly(self):
        with patch.object(StepPanel, "_reveal_in_explorer") as reveal, \
             patch("src.widgets.step_panel.QDesktopServices.openUrl") as open_url:
            self.panel._on_output_link("folder:C:/work/out.mp4")
            reveal.assert_called_once_with("C:/work/out.mp4")
            self.panel._on_output_link("file:///C:/work/out.mp4")
            open_url.assert_called_once()


class DisabledReasonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"source")
        self.queue = TaskQueue(str(self.root / "workspace"))
        self.host = _Host(self.queue)

    def _add_task(self):
        return self.queue.add_task(str(self.source))

    def test_no_selection_buttons_explain_why(self):
        self.host._refresh_entry_buttons()
        self.assertFalse(self.host.add_subtitle_btn.isEnabled())
        self.assertIn("选择任务", self.host.add_subtitle_btn.toolTip())
        self.assertFalse(self.host.add_mix_btn.isEnabled())

    def test_step2_start_disabled_explains_prerequisite(self):
        task = self._add_task()
        self.host._refresh_step_panels(task)
        btn = self.host.step_panels[2].start_btn
        self.assertFalse(btn.isEnabled())
        self.assertIn("步骤 1", btn.toolTip())

    def test_step3_start_disabled_explains_prerequisite(self):
        task = self._add_task()
        self.host._refresh_step_panels(task)
        btn = self.host.step_panels[3].start_btn
        self.assertFalse(btn.isEnabled())
        self.assertIn("步骤 2", btn.toolTip())

    def test_enabled_button_restores_default_tooltip(self):
        task = self._add_task()
        task.set_step_status(1, STEP_DONE)
        self.queue.update_task(task)
        self.queue.set_current(task.task_id)
        self.host._refresh_step_panels(task)
        btn = self.host.add_subtitle_btn
        self.host._refresh_entry_buttons()
        self.assertTrue(btn.isEnabled())
        self.assertEqual(btn.toolTip(), "为当前任务添加字幕文件（跳过字幕提取）")

    def test_stop_button_disabled_says_idle(self):
        task = self._add_task()
        self.host._refresh_step_panels(task)
        btn = self.host.step_panels[1].stop_btn
        self.assertFalse(btn.isEnabled())
        self.assertIn("没有正在运行", btn.toolTip())


if __name__ == "__main__":
    unittest.main()
