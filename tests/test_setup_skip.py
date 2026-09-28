import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel, QWidget

from src.config import SetupDialog, WorkflowConfig
from src.task_manager import TaskInfo, STEP_DONE, STEP_SKIPPED
from src.workflow_gui.batch_mixin import BatchMixin
from src.workflow_gui.step_mixin import StepMixin
from src.workflow_gui.task_mixin import TaskMixin


class ExecutionHost(BatchMixin, StepMixin, TaskMixin, QWidget):
    def __init__(self, config, task):
        super().__init__()
        self.config = config
        self.task_queue = SimpleNamespace(tasks=[task], current=task,
                                          get_task=lambda task_id: task)
        self._workers = {1: None, 2: None, 3: None}
        self._current_group = None
        self._batch_executor = None
        self.step_panels = {step: MagicMock() for step in (1, 2, 3)}
        self.batch_btn = MagicMock()
        self.batch_stop_btn = MagicMock()
        self._connect_batch_signals = MagicMock()
        self._append_log = MagicMock()


class SetupSkipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps({
            "workspace_dir": str(self.root / "workspace")
        }), encoding="utf-8")
        config_path_patch = patch("src.config.get_config_path", return_value=str(self.config_path))
        config_path_patch.start()
        self.addCleanup(config_path_patch.stop)
        self.config = WorkflowConfig()

    def make_host(self, subtitle=False):
        source = self.root / "test.wav"
        source.write_bytes(b"test")
        task = TaskInfo("id", str(source), "test", self.config.workspace_dir)
        if subtitle:
            path = source.with_suffix(".lrc")
            path.write_text("[00:00.00]test", encoding="utf-8")
            task.custom_subtitle = str(path)
            task.step1_output = str(path)
            task.step1_status = STEP_SKIPPED
        host = ExecutionHost(self.config, task)
        self.addCleanup(host.deleteLater)
        return host, task

    def test_skip_persists_without_validating_whisper_and_uses_default_workspace(self):
        self.assertTrue(self.config.needs_setup())
        dialog = SetupDialog(self.config)
        self.addCleanup(dialog.deleteLater)
        dialog.whisper_edit.setText(str(self.root / "not-installed"))
        dialog.workspace_edit.clear()
        dialog.skip_btn.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        restored = WorkflowConfig()
        self.assertFalse(restored.needs_setup())
        self.assertFalse(restored.is_configured())
        self.assertEqual(restored.whisper_dir, "")
        self.assertTrue(Path(restored.workspace_dir).is_dir())

    def test_cancel_does_not_mark_setup_completed(self):
        dialog = SetupDialog(self.config)
        self.addCleanup(dialog.deleteLater)
        dialog.reject()
        self.assertTrue(WorkflowConfig().needs_setup())

    def test_path_requires_executable_and_valid_legacy_config_needs_no_setup(self):
        whisper = self.root / "whisper"
        whisper.mkdir()
        self.config.config["subprojects"]["whisper_dir"] = str(whisper)
        self.assertFalse(self.config.is_configured())
        (whisper / "infer.exe").mkdir()
        self.assertFalse(self.config.is_configured())
        (whisper / "infer.exe").rmdir()
        (whisper / "infer.exe").write_bytes(b"test")
        self.assertTrue(self.config.is_configured())
        self.assertFalse(self.config.needs_setup())

    def test_valid_path_can_be_saved_after_skip(self):
        dialog = SetupDialog(self.config)
        self.addCleanup(dialog.deleteLater)
        dialog._on_skip()
        whisper = self.root / "whisper"
        whisper.mkdir()
        (whisper / "infer.exe").write_bytes(b"test")
        later = SetupDialog(self.config)
        self.addCleanup(later.deleteLater)
        later.whisper_edit.setText(str(whisper))
        later._on_accept()
        self.assertEqual(later.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(WorkflowConfig().is_configured())

    def test_skip_save_failure_does_not_enter_main_window(self):
        previous = copy.deepcopy(self.config.config)
        dialog = SetupDialog(self.config)
        self.addCleanup(dialog.deleteLater)
        with patch.object(self.config, "save", side_effect=OSError("cannot save")), \
             patch("src.config.QMessageBox.warning") as warning:
            dialog._on_skip()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(self.config.config, previous)
        warning.assert_called_once()

    def test_batch_missing_subtitles_is_blocked_with_names_and_path_notice(self):
        host, task = self.make_host()

        def accept(dialog):
            checks = dialog.findChildren(QCheckBox)
            self.assertFalse(checks[0].isEnabled())
            self.assertFalse(checks[0].isChecked())
            self.assertTrue(any("完整自动流水线不可用" in label.text()
                                for label in dialog.findChildren(QLabel)))
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", accept), \
             patch("src.workflow_gui.step_mixin.QMessageBox.warning") as warning:
            host._on_batch_execute()
        self.assertIsNone(host._batch_executor)
        self.assertIn(task.source_name, warning.call_args.args[2])
        self.assertIn("自行准备字幕", warning.call_args.args[1])

    def test_existing_subtitles_can_run_pipeline_without_whisper(self):
        host, task = self.make_host(subtitle=True)
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch("src.steps.batch_executor.BatchExecutor") as executor:
            host._on_batch_execute()
        self.assertEqual(executor.call_args.args[1], [2, 3])
        self.assertEqual(executor.call_args.kwargs["order"], "pipeline")
        executor.return_value.start.assert_called_once()
        Path(task.step1_output).unlink()
        with patch("src.workflow_gui.step_mixin.QMessageBox.warning"):
            self.assertFalse(host._check_subtitles_available([task], [2, 3]))
        task.step2_status = STEP_DONE
        self.assertTrue(host._check_subtitles_available([task], [2, 3]))
        self.assertTrue(host._check_subtitles_available([task], [3]))

    def test_unavailable_step1_single_group_and_rerun_preserve_results(self):
        host, task = self.make_host(subtitle=True)
        previous = task.to_dict()
        with patch("src.workflow_gui.step_mixin.QMessageBox.warning") as warning:
            host._on_start_step(1)
            host._start_group_step(SimpleNamespace(tasks=[task]), 1)
            host._on_task_rerun(task.task_id, 1)
        self.assertEqual(warning.call_count, 3)
        self.assertEqual(task.to_dict(), previous)
        self.assertIsNone(host._workers[1])
        self.assertIsNone(host._batch_executor)


if __name__ == "__main__":
    unittest.main()
