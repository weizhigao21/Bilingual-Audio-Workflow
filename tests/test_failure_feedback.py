# -*- coding: utf-8 -*-
"""P0-2 失败原因直达守卫（《优化方案-易用性与速度》批次 0）。

失败后原因必须在步骤卡 detail（首行、错误色）与任务行 tooltip（两行、
不硬截 120 字）里直接可见；detail 被常规状态复用时错误色必须退回。
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from src.task_manager import (
    TaskInfo, TaskQueue, STEP_FAILED, STEP_PENDING,
)
from src.widgets.step_panel import StepPanel
from src.widgets.task_list import TaskListWidget
from src.workflow_gui.task_mixin import TaskMixin

ERROR_TEXT = "ffmpeg 失败（1）: Invalid data found when processing input\n详细上下文：source.wav 第 3 段"


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
        self.add_mix_btn = QPushButton(self)
        self.task_list = TaskListWidget(self)
        self.task_list.set_task_queue(queue)
        queue.task_added.connect(self.task_list.add_task_item)
        queue.current_changed.connect(self._on_current_changed)
        queue.task_updated.connect(self._on_task_updated)


class FailureFeedbackTests(unittest.TestCase):
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
        self.addCleanup(self.host.deleteLater)

    def _failed_task(self, error=ERROR_TEXT, step=2):
        task = self.queue.add_task(str(self.source))
        task.set_step_status(step, STEP_FAILED)
        task.set_step_error(step, error)
        self.queue.update_task(task)
        return task

    def test_failed_step_shows_error_on_card(self):
        task = self._failed_task()
        self.host._refresh_step_panels(task)
        panel = self.host.step_panels[2]
        self.assertTrue(panel.detail_label.isVisibleTo(panel))
        self.assertEqual(panel.detail_label.text(),
                         ERROR_TEXT.splitlines()[0])
        self.assertEqual(panel.detail_label.property("role"), "error")
        self.assertEqual(panel.detail_label.toolTip(), ERROR_TEXT)

    def test_error_first_line_is_truncated_not_wrapped_forever(self):
        long_error = "错" * 300 + "\n次行"
        task = self._failed_task(error=long_error)
        self.host._refresh_step_panels(task)
        detail = self.host.step_panels[2].detail_label
        self.assertLessEqual(len(detail.text()), 73)
        self.assertTrue(detail.text().endswith("…"))
        self.assertEqual(detail.toolTip(), long_error)

    def test_detail_reset_clears_error_role_and_tooltip(self):
        panel = self.host.step_panels[2]
        panel.set_error(ERROR_TEXT)
        panel.set_detail("片段 3/300")
        self.assertEqual(panel.detail_label.property("role"), "secondary")
        self.assertEqual(panel.detail_label.toolTip(), "")

    def test_empty_error_falls_back_to_plain_detail(self):
        panel = self.host.step_panels[2]
        panel.set_error("")
        self.assertFalse(panel.detail_label.isVisibleTo(panel))
        self.assertEqual(panel.detail_label.property("role"), "secondary")

    def test_retry_reset_clears_error_display(self):
        task = self._failed_task()
        self.host._refresh_step_panels(task)
        task.set_step_status(2, STEP_PENDING)
        task.set_step_error(2, "")
        self.host._refresh_step_panels(task)
        self.assertFalse(
            self.host.step_panels[2].detail_label.isVisibleTo(
                self.host.step_panels[2]))

    def test_error_hint_keeps_two_lines_without_120_cut(self):
        first = "x" * 150
        task = self._failed_task(error=first + "\nsecond line context")
        hint = TaskListWidget._error_hint(task)
        self.assertIn(first, hint, "首行 150 字不应再被 120 字硬截")
        self.assertIn("second line context", hint)

    def test_error_hint_covers_all_steps(self):
        for step in (1, 2, 3):
            task = self._failed_task(step=step, error=f"step{step} 出错")
            self.assertIn(f"step{step} 出错", TaskListWidget._error_hint(task))


if __name__ == "__main__":
    unittest.main()
