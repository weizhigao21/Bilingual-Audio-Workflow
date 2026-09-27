import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QThread, Qt, pyqtSignal

from src.steps.step2_tts import TTSBridgeWorker
from src.steps.batch_executor.single_step_mixin import SingleStepMixin
from src.task_manager import TaskInfo


class ImmediateTTSWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    total_tasks_signal = pyqtSignal(int)
    weight_progress_signal = pyqtSignal(int)
    total_weight_signal = pyqtSignal(int)
    eta_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        self.total_tasks_signal.emit(2)
        self.total_weight_signal.emit(10)
        self.log_signal.emit("片段开始")
        self.progress_signal.emit(1)
        self.weight_progress_signal.emit(5)
        self.eta_signal.emit("1 秒")
        output = Path(self.config["output_dir"]) / self.config["subtitle_md5"]
        output.mkdir(parents=True, exist_ok=True)
        (output / "001.wav").write_bytes(b"audio")
        self.progress_signal.emit(2)
        self.weight_progress_signal.emit(10)
        self.finished_signal.emit(True)


class TTSBridgeSignalTests(unittest.TestCase):
    def test_fast_worker_delivers_progress_and_finishes(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as folder:
            subtitle = Path(folder) / "source.lrc"
            subtitle.write_text("[00:00.00] 测试", encoding="utf-8")
            task = TaskInfo("id", str(Path(folder) / "source.wav"), "source", folder)
            task.step1_output = str(subtitle)
            config = SimpleNamespace(tts_cfg={"tts_mode": "edge"})
            logs, percents, statuses = [], [], []
            with patch("src.steps.step2_tts.TTSWorker", ImmediateTTSWorker):
                bridge = TTSBridgeWorker(task, config)
                bridge.log_signal.connect(logs.append, Qt.ConnectionType.DirectConnection)
                bridge.progress_signal.connect(percents.append, Qt.ConnectionType.DirectConnection)
                bridge.status_signal.connect(statuses.append, Qt.ConnectionType.DirectConnection)
                bridge.start()
                self.assertTrue(bridge.wait(5000), "语音线程未能及时结束")

            self.assertTrue(bridge.result[0], bridge.result[1])
            self.assertTrue(os.path.isdir(bridge.result[1]))
            self.assertIn("片段开始", logs)
            self.assertIn(50, percents)
            self.assertIn(100, percents)
            self.assertTrue(any("片段 2/2" in status for status in statuses))
            self.assertIsNotNone(app)

    def test_pipeline_python_thread_forwards_worker_signals(self):
        class SignalSink:
            def __init__(self):
                self.values = []

            def emit(self, *args):
                self.values.append(args)

        class FastWorker(QThread):
            log_signal = pyqtSignal(str)
            progress_signal = pyqtSignal(int)
            status_signal = pyqtSignal(str)

            def __init__(self):
                super().__init__()
                self.result = (True, "output")

            def run(self):
                self.log_signal.emit("正在合成")
                self.progress_signal.emit(50)
                self.status_signal.emit("片段 1/2")

        class Executor(SingleStepMixin):
            def __init__(self):
                self.log_signal = SignalSink()
                self.step_progress_signal = SignalSink()
                self.step_status_signal = SignalSink()

            def _create_worker(self, task, step):
                return FastWorker()

            def _track_worker(self, worker, active):
                pass

        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as folder:
            task = TaskInfo("id", str(Path(folder) / "source.wav"), "source", folder)
            executor = Executor()
            result = []
            thread = threading.Thread(
                target=lambda: result.append(executor._run_single_step_sync(task, 2))
            )
            thread.start()
            thread.join(5)
            self.assertFalse(thread.is_alive(), "流水线语音线程未能结束")
            self.assertEqual(result, [(True, "output")])
            self.assertIn(("[source] 正在合成",), executor.log_signal.values)
            self.assertIn((2, 50), executor.step_progress_signal.values)
            self.assertIn((2, "source · 片段 1/2"), executor.step_status_signal.values)
            self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
