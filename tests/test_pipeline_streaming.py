import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QThread, Qt, pyqtSignal

from src.steps.batch_executor import BatchExecutor
from src.task_manager import TaskInfo, STEP_DONE


class PipelineStreamingTests(unittest.TestCase):
    def test_qt_subtitle_results_update_tasks_and_dispatch_before_batch_finishes(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        first_mixed = threading.Event()
        overlap = []
        states = []
        results = []

        class SubtitleWorker(QThread):
            log_signal = pyqtSignal(str)
            progress_signal = pyqtSignal(int)
            status_signal = pyqtSignal(str)
            task_result_signal = pyqtSignal(str, bool, str)
            finished_signal = pyqtSignal(int, int)

            def __init__(self, tasks, config):
                super().__init__()
                self.tasks = tasks

            def run(self):
                self.task_result_signal.emit(self.tasks[0].task_id, True, 'first.lrc')
                overlap.append(first_mixed.wait(5))
                self.task_result_signal.emit(self.tasks[1].task_id, False, 'missing subtitle')
                self.finished_signal.emit(1, 1)

        def run_step(executor, task, step, *args, progress_callback=None):
            task.set_step_status(step, STEP_DONE)
            task.set_step_output(step, 'output')
            progress_callback(100)
            if step == 3:
                first_mixed.set()
            return True, 'output'

        with tempfile.TemporaryDirectory() as folder:
            tasks = []
            for task_id in ('first', 'second'):
                source = Path(folder) / f'{task_id}.wav'
                source.write_bytes(b'audio')
                tasks.append(TaskInfo(task_id, str(source), task_id, folder))
            config = SimpleNamespace(tts_cfg={'pipeline_tts_workers': 2},
                                     mixer_cfg={'thread_count': 2})
            executor = BatchExecutor(tasks, [1, 2, 3], config, order='pipeline')
            executor.task_finished.connect(
                lambda task_id, ok: states.append((task_id, ok, tasks[0].step1_status)),
                Qt.ConnectionType.DirectConnection,
            )
            executor.finished_signal.connect(
                lambda *counts: results.append(counts), Qt.ConnectionType.DirectConnection
            )
            with patch('src.steps.batch_executor.by_step_mixin.WhisperBatchWorker',
                       SubtitleWorker), patch.object(BatchExecutor, '_run_single_step_sync',
                                                    run_step):
                executor.start()
                self.assertTrue(executor.wait(8000), '流水线未能结束')

        self.assertEqual(overlap, [True], 'Qt 字幕结果未实时派发给语音/混音队列')
        self.assertIn(('first', True, STEP_DONE), states)
        self.assertEqual(results, [(1, 1, 0)])
        self.assertIsNotNone(app)


if __name__ == '__main__':
    unittest.main()
