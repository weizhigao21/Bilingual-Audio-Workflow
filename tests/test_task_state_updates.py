import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from src.task_manager import (
    TaskGroup, TaskInfo, TaskQueue, STEP_DONE, STEP_FAILED,
    STEP_PENDING, STEP_RUNNING, STEP_SKIPPED,
)
from src.steps.batch_executor import BatchExecutor
from src.steps.step2_tts import TTSBridgeWorker
from src.steps.step3_mixer import mix_single_task
from src.widgets.step_panel import StepPanel
from src.widgets.task_list import TaskListWidget
from src.workflow_gui.batch_mixin import BatchMixin
from src.workflow_gui.step_mixin import StepMixin
from src.workflow_gui.task_mixin import TaskMixin


class ExecutionHost(TaskMixin, StepMixin, BatchMixin, QWidget):
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
        self._append_log = MagicMock()
        queue.task_added.connect(self.task_list.add_task_item)
        queue.current_changed.connect(self._on_current_changed)
        queue.task_updated.connect(self._on_task_updated)


class TaskStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"source")
        self.subtitle = self.root / "source.lrc"
        self.subtitle.write_text("[00:00.00]old", encoding="utf-8")
        self.new_subtitle = self.root / "new.lrc"
        self.new_subtitle.write_text("[00:00.00]new", encoding="utf-8")
        self.clips = self.root / "clips"
        self.clips.mkdir()
        (self.clips / "0001.wav").write_bytes(b"clip")
        self.mixed = self.root / "双语" / "source_mixed.mp3"
        self.mixed.parent.mkdir()
        self.mixed.write_bytes(b"old mix")
        self.queue = TaskQueue(str(self.root / "workspace"))

    def completed_task(self, custom_clips=False):
        task = self.queue.add_task(str(self.source), subtitle_path=str(self.subtitle),
                                   mix_folder=str(self.clips) if custom_clips else "")
        task.step2_status = task.step3_status = STEP_DONE
        task.step2_output = str(self.clips)
        task.step3_output = str(self.mixed)
        self.queue.update_task(task)
        return task

    def host(self):
        host = ExecutionHost(self.queue)
        self.addCleanup(host.deleteLater)
        return host

    def test_detected_outputs_count_as_completed_in_task_and_group(self):
        task = self.queue.add_task(str(self.source), subtitle_path=str(self.subtitle),
                                   mix_folder=str(self.clips))
        group = TaskGroup("g", "group", str(self.root), [task])
        self.assertEqual(task.step3_status, STEP_SKIPPED)
        self.assertEqual(TaskListWidget._overall_status(task), STEP_DONE)
        self.assertEqual(group.overall_status(), STEP_DONE)
        self.assertEqual(group.done_count(), 1)
        self.assertEqual(group.progress(), 1)

    def test_explicit_clips_can_finish_without_subtitle_extraction(self):
        task = self.queue.add_task(str(self.source), mix_folder=str(self.clips))
        self.assertEqual(task.step1_status, STEP_PENDING)
        self.assertEqual(task.overall_status(), STEP_DONE)
        self.assertEqual(task.overall_progress(), 1)

    def test_replace_subtitle_invalidates_generated_voice_and_mix_durably(self):
        task = self.completed_task()
        task.step2_error = task.step3_error = "old error"
        self.queue.set_task_custom_subtitle(task.task_id, str(self.new_subtitle))
        self.assertEqual(task.step1_output, str(self.new_subtitle))
        self.assertEqual([task.step_status(s) for s in (1, 2, 3)],
                         [STEP_SKIPPED, STEP_PENDING, STEP_PENDING])
        self.assertEqual((task.step2_output, task.step3_output,
                          task.step2_error, task.step3_error), ("", "", "", ""))
        self.assertTrue(task.force_remix)
        restored = TaskQueue(self.queue.workspace_root)
        restored.restore_tasks()
        self.assertEqual(restored.tasks[0].step3_status, STEP_PENDING)
        self.assertTrue(restored.tasks[0].force_remix)
        self.assertTrue(self.mixed.exists())
        self.assertTrue((self.clips / "0001.wav").exists())

    def test_replace_subtitle_keeps_explicit_clips_but_not_old_mix(self):
        task = self.completed_task(custom_clips=True)
        self.queue.set_task_custom_subtitle(task.task_id, str(self.new_subtitle))
        self.assertEqual(task.step2_status, STEP_SKIPPED)
        self.assertEqual(task.step2_output, str(self.clips))
        self.assertEqual(task.step3_status, STEP_PENDING)
        self.assertTrue(task.force_remix)

    def test_matching_cached_voice_does_not_revalidate_old_mix(self):
        task = self.completed_task()
        with patch.object(TaskInfo, "_detect_tts_by_subtitle_md5",
                          return_value=str(self.clips)):
            self.queue.set_task_custom_subtitle(task.task_id, str(self.new_subtitle))
        self.assertEqual(task.step2_status, STEP_SKIPPED)
        self.assertEqual(task.step3_status, STEP_PENDING)
        self.assertTrue(task.force_remix)

    def test_replace_and_clear_custom_clips_invalidates_completed_mix(self):
        task = self.completed_task(custom_clips=True)
        replacement = self.root / "replacement"
        replacement.mkdir()
        self.queue.set_task_custom_mix_folder(task.task_id, str(replacement))
        self.assertEqual(task.step2_output, str(replacement))
        self.assertEqual(task.step2_status, STEP_SKIPPED)
        self.assertEqual(task.step3_status, STEP_PENDING)
        self.queue.set_task_custom_mix_folder(task.task_id, "")
        self.assertEqual(task.step2_status, STEP_PENDING)
        self.assertEqual(task.step2_output, "")
        self.assertEqual(task.step3_output, "")
        self.assertTrue(task.force_remix)

    def test_clear_subtitle_leaves_no_completed_step_without_output(self):
        task = self.completed_task()
        self.queue.set_task_custom_subtitle(task.task_id, "")
        self.assertEqual([task.step_status(s) for s in (1, 2, 3)], [STEP_PENDING] * 3)
        self.assertEqual(task.step1_output, "")

    def test_running_task_rejects_input_changes(self):
        task = self.completed_task()
        task.step3_status = STEP_RUNNING
        before = task.to_dict()
        self.assertFalse(self.queue.set_task_custom_subtitle(task.task_id, str(self.new_subtitle)))
        self.assertFalse(self.queue.set_task_custom_mix_folder(task.task_id, ""))
        self.assertEqual(task.to_dict(), before)

    def test_retry_clears_old_errors_and_failed_output(self):
        task = self.completed_task()
        task.step2_error = "old failure"
        task.finish_step(2, True, str(self.clips))
        self.assertEqual(task.step2_error, "")
        task.finish_step(2, False, "cancelled", cancelled=True)
        self.assertEqual(task.step2_status, STEP_PENDING)
        self.assertEqual(task.step2_output, "")

    def test_start_updates_sidebar_and_saved_record_and_blocks_duplicate(self):
        host = self.host()
        task = self.completed_task()
        self.queue.set_current(task.task_id)
        worker = MagicMock()
        worker._stop_flag = False
        with patch("src.steps.step2_tts.TTSBridgeWorker", return_value=worker):
            host._on_start_step(2)
        self.assertEqual(task.step2_status, STEP_RUNNING)
        self.assertEqual(task.step3_status, STEP_PENDING)
        item = host.task_list._find_task_item(task.task_id)
        self.assertEqual(item.data(0, host.task_list.ROLE_STATUS), STEP_RUNNING)
        record = json.loads(Path(task.task_json_path).read_text(encoding="utf-8"))
        self.assertEqual(record["step2_status"], STEP_RUNNING)
        with patch("src.workflow_gui.step_mixin.QMessageBox.warning") as warning:
            host._on_start_step(2)
        warning.assert_called_once()
        worker.start.assert_called_once()

    def test_background_progress_and_completion_preserve_selected_task(self):
        host = self.host()
        first = self.completed_task()
        second = self.queue.add_task(str(self.source))
        self.queue.set_current(first.task_id)
        worker = MagicMock()
        worker._stop_flag = False
        with patch("src.steps.step2_tts.TTSBridgeWorker", return_value=worker):
            host._on_start_step(2)
        self.queue.set_current(second.task_id)
        host._on_step_progress(2, 63, first.task_id)
        host._on_step_status(2, "first is running", first.task_id)
        self.assertEqual(host.step_panels[2].progress.value(), 0)
        self.assertEqual(host.step_panels[2].detail_label.text(), "")
        self.queue.set_current(first.task_id)
        self.assertEqual(host.step_panels[2].progress.value(), 63)
        self.assertEqual(host.step_panels[2].detail_label.text(), "first is running")
        self.queue.set_current(second.task_id)
        host._on_step_finished(2, first, True, str(self.clips))
        self.assertEqual(self.queue.current.task_id, second.task_id)
        self.assertEqual(host.step_panels[2].property("state"), STEP_PENDING)
        self.assertEqual(host.step_panels[2].progress.value(), 0)
        self.assertEqual(first.step2_status, STEP_DONE)

    def test_single_and_group_rerun_force_mix_and_clear_downstream_errors(self):
        host = self.host()
        task = self.completed_task()
        task.step3_error = "old failure"
        with patch.object(host, "_on_start_step"):
            host._on_task_rerun(task.task_id, 3)
        self.assertTrue(task.force_remix)
        self.assertEqual(task.step3_status, STEP_PENDING)
        self.assertEqual(task.step3_error, "")
        task.force_remix = False
        group = TaskGroup("g", "group", str(self.root), [task])
        with patch.object(host, "_start_group_step"):
            host._rerun_group_tasks(group, 2)
        self.assertTrue(task.force_remix)
        self.assertEqual(task.step2_status, STEP_PENDING)

    def test_rerun_mix_bypasses_existing_file_shortcut(self):
        host = self.host()
        task = self.completed_task()
        config = SimpleNamespace(mixer_cfg={"output_format": "mp3", "skip_existing": True})
        with patch("src.steps.step3_mixer.probe_audio", return_value=(44100, 2000, 2)) as probe, \
             patch("src.steps.step3_mixer.mix_streaming_task", return_value=(True, str(self.mixed))) as mixer:
            self.assertEqual(mix_single_task(task, config), (True, str(self.mixed)))
            probe.assert_not_called()
            with patch.object(host, "_on_start_step"):
                host._on_task_rerun(task.task_id, 3)
            self.assertEqual(mix_single_task(task, config), (True, str(self.mixed)))
            mixer.assert_called_once()

    def test_cancel_tts_before_start_does_not_contact_service(self):
        task = self.completed_task()
        worker = TTSBridgeWorker(task, SimpleNamespace())
        worker.stop()
        with patch("src.steps.step2_tts.prepare_workflow_config") as prepare:
            worker.run()
        self.assertFalse(worker.result[0])
        self.assertIn("取消", worker.result[1])
        prepare.assert_not_called()

    def test_cancelled_single_step_is_ready_for_retry(self):
        host = self.host()
        task = self.completed_task()
        self.queue.set_current(task.task_id)
        worker = MagicMock()
        worker._stop_flag = True
        host._workers[3] = worker
        task.step3_status = STEP_RUNNING
        host._on_step_finished(3, task, False, "cancelled")
        self.assertEqual(task.step3_status, STEP_PENDING)
        self.assertTrue(host.step_panels[3].start_btn.isEnabled())
        self.assertFalse(host.step_panels[3].stop_btn.isEnabled())

    def test_group_progress_is_calculated_per_step(self):
        host = self.host()
        task = self.queue.add_task(str(self.source), subtitle_path=str(self.subtitle))
        group = TaskGroup("g", "group", str(self.root), [task])
        host._show_group_summary(group)
        self.assertEqual([host.step_panels[s].progress.value() for s in (1, 2, 3)],
                         [100, 0, 0])

    def test_batch_update_preserves_selection_and_disables_manual_start(self):
        host = self.host()
        first = self.completed_task()
        second = self.queue.add_task(str(self.source))
        self.queue.set_current(second.task_id)
        host._batch_executor = object()
        first.step2_status = STEP_RUNNING
        host._on_batch_task_started(first.task_id)
        self.assertEqual(self.queue.current.task_id, second.task_id)
        self.queue.set_current(first.task_id)
        self.assertTrue(all(not p.start_btn.isEnabled() for p in host.step_panels.values()))
        self.assertFalse(host.add_subtitle_btn.isEnabled())

    def test_cancel_parallel_mix_reports_every_task_and_preserves_success(self):
        tasks = [self.completed_task(custom_clips=True) for _ in range(3)]
        for task in tasks:
            task.reset_from(3)
        config = SimpleNamespace(mixer_cfg={"enable_batch_parallel": True, "thread_count": 1})
        executor = BatchExecutor(tasks, [3], config, order="by_step")
        first_reported = threading.Event()
        blocked = threading.Event()
        counts, reported, progress = [], [], []

        def mix(task, config, log, on_progress, stop_check):
            if task is tasks[0]:
                return True, str(self.mixed)
            blocked.set()
            deadline = time.monotonic() + 4
            while not stop_check() and time.monotonic() < deadline:
                time.sleep(0.005)
            return False, "cancelled"

        def finished(task_id, ok):
            reported.append(task_id)
            if task_id == tasks[0].task_id:
                first_reported.set()

        executor.task_finished.connect(finished, Qt.ConnectionType.DirectConnection)
        executor.finished_signal.connect(lambda *v: counts.append(v), Qt.ConnectionType.DirectConnection)
        executor.step_progress_signal.connect(lambda *v: progress.append(v), Qt.ConnectionType.DirectConnection)
        with patch("src.steps.step3_mixer.mix_single_task", side_effect=mix):
            executor.start()
            try:
                self.assertTrue(first_reported.wait(4))
                self.assertTrue(blocked.wait(4))
            finally:
                executor.stop()
                self.assertTrue(executor.wait(6000))
        self.assertCountEqual(reported, [task.task_id for task in tasks])
        self.assertEqual([task.step3_status for task in tasks], [STEP_DONE, STEP_PENDING, STEP_PENDING])
        self.assertEqual(counts, [(1, 0, 2)])
        self.assertNotIn((3, 100), progress)

    def test_all_batch_modes_publish_intermediate_step_states(self):
        class Worker(QThread):
            log_signal = pyqtSignal(str)
            progress_signal = pyqtSignal(int)
            finished_signal = pyqtSignal(bool, str)

            def __init__(self, output):
                super().__init__()
                self.result = (True, output)

            def run(self):
                self.progress_signal.emit(50)
                self.finished_signal.emit(*self.result)

        for order in ("by_task", "by_step", "pipeline"):
            with self.subTest(order=order):
                task = self.completed_task()
                task.reset_from(2)
                config = SimpleNamespace(tts_cfg={"pipeline_tts_workers": 1},
                                         mixer_cfg={"enable_batch_parallel": False, "thread_count": 1})
                executor = BatchExecutor([task], [2, 3], config, order=order)
                states, results = [], []
                executor.task_updated.connect(
                    lambda _: states.append((task.step2_status, task.step3_status)),
                    Qt.ConnectionType.DirectConnection)
                executor.finished_signal.connect(lambda *v: results.append(v),
                                                  Qt.ConnectionType.DirectConnection)
                with patch.object(executor, "_create_worker",
                                  side_effect=lambda t, s: Worker(str(self.clips) if s == 2 else str(self.mixed))):
                    executor.start()
                    self.assertTrue(executor.wait(6000))
                for state in ((STEP_RUNNING, STEP_PENDING), (STEP_DONE, STEP_PENDING),
                              (STEP_DONE, STEP_RUNNING), (STEP_DONE, STEP_DONE)):
                    self.assertIn(state, states)
                self.assertEqual(results, [(1, 0, 0)])

    def test_batch_exception_finishes_active_state_and_emits_summary(self):
        task = self.completed_task()
        task.reset_from(3)
        executor = BatchExecutor([task], [3], SimpleNamespace(), order="by_task")
        results = []
        executor.finished_signal.connect(lambda *v: results.append(v),
                                          Qt.ConnectionType.DirectConnection)

        def crash(total):
            task.step3_status = STEP_RUNNING
            raise RuntimeError("test failure")

        with patch.object(executor, "_run_by_task", side_effect=crash):
            executor.run()
        self.assertEqual(task.step3_status, STEP_FAILED)
        self.assertIn("test failure", task.step3_error)
        self.assertEqual(results, [(0, 1, 0)])


if __name__ == "__main__":
    unittest.main()
