import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.steps.step1_whisper import WhisperBatchWorker, WhisperWorker
from src.task_manager import TaskInfo


class WhisperSelectedImportTests(unittest.TestCase):
    def test_complete_flat_folder_uses_original_directory_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            whisper = root / "whisper"
            whisper.mkdir()
            (whisper / "infer.exe").write_bytes(b"test")
            sources = []
            for name in ("01.mp3", "02.mp3"):
                path = root / name
                path.write_bytes(b"audio")
                sources.append(path)
            tasks = [TaskInfo(f"task-{i}", str(path), path.stem, str(root / "workspace"),
                              from_folder=True, import_folder=str(root))
                     for i, path in enumerate(sources)]
            worker = WhisperBatchWorker(tasks, SimpleNamespace(
                whisper_dir=str(whisper), whisper_cfg={"enable_batching": True}
            ))
            inputs = []

            class FakeProcess:
                def __init__(self, cmd, **kwargs):
                    inputs.append((cmd[-1], "--enable_batching" in cmd))
                    out_dir = Path(cmd[cmd.index("--output_dir") + 1])
                    for task in tasks:
                        (out_dir / f"{task.source_name}.lrc").write_text("subtitle", encoding="utf-8")
                    self.stdout = []
                    self.returncode = 0

                def wait(self):
                    return self.returncode

            with patch("src.steps.step1_whisper.subprocess.Popen", FakeProcess):
                worker.run()
            self.assertEqual(inputs, [(os.path.normcase(str(root)), True)])
            self.assertFalse(list(root.glob(".whisper-selected-*")))

    def test_nested_unique_names_run_in_one_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            whisper = root / "whisper"
            whisper.mkdir()
            (whisper / "infer.exe").write_bytes(b"test")
            selected = []
            for relative in ("A/01.mp3", "B/02.mp3"):
                path = root / relative
                path.parent.mkdir()
                path.write_bytes(b"audio")
                selected.append(path)
            (root / "A" / "unselected.mp3").write_bytes(b"audio")
            tasks = [TaskInfo(f"task-{i}", str(path), path.stem, str(root / "workspace"),
                              from_folder=True, import_folder=str(root))
                     for i, path in enumerate(selected)]
            config = SimpleNamespace(whisper_dir=str(whisper), whisper_cfg={})
            worker = WhisperBatchWorker(tasks, config)
            calls = []

            class FakeProcess:
                def __init__(self, cmd, **kwargs):
                    names = sorted(p.name for p in Path(cmd[-1]).iterdir())
                    calls.append((names, "--enable_batching" in cmd))
                    out_dir = Path(cmd[cmd.index("--output_dir") + 1])
                    for name in names:
                        (out_dir / f"{Path(name).stem}.lrc").write_text("subtitle", encoding="utf-8")
                    self.stdout = []
                    self.returncode = 0

                def wait(self):
                    return self.returncode

            with patch("src.steps.step1_whisper.subprocess.Popen", FakeProcess):
                worker.run()
            self.assertEqual(calls, [(["01.mp3", "02.mp3"], False)])
            self.assertTrue((root / "A" / "01.lrc").exists())
            self.assertTrue((root / "B" / "02.lrc").exists())
            self.assertFalse((root / "A" / "unselected.lrc").exists())

    def test_batch_only_exposes_selected_files_and_keeps_same_names_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            whisper = root / "whisper"
            whisper.mkdir()
            (whisper / "infer.exe").write_bytes(b"test")
            sources = []
            for relative in ("A/01.mp3", "A/02.mp3", "B/01.mp3"):
                path = root / relative
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"audio")
                sources.append(path)
            selected = [sources[0], sources[2]]
            tasks = [TaskInfo(f"task-{i}", str(path), path.stem, str(root / "workspace"),
                              from_folder=True, import_folder=str(root))
                     for i, path in enumerate(selected)]
            config = SimpleNamespace(whisper_dir=str(whisper), whisper_cfg={})
            worker = WhisperBatchWorker(tasks, config)
            seen_inputs = []
            results = []
            worker.task_result_signal.connect(lambda task_id, ok, path:
                                              results.append((task_id, ok, path)))

            class FakeProcess:
                def __init__(self, cmd, **kwargs):
                    input_dir = Path(cmd[-1])
                    names = sorted(path.name for path in input_dir.iterdir())
                    seen_inputs.append(names)
                    out_dir = Path(cmd[cmd.index("--output_dir") + 1])
                    for name in names:
                        (out_dir / f"{Path(name).stem}.lrc").write_text("subtitle", encoding="utf-8")
                    self.stdout = []
                    self.returncode = 0

                def wait(self):
                    return self.returncode

            with patch("src.steps.step1_whisper.subprocess.Popen", FakeProcess):
                worker.run()

            self.assertEqual(seen_inputs, [["01.mp3"], ["01.mp3"]])
            self.assertEqual(len(results), 2)
            self.assertTrue(all(ok for _, ok, _ in results))
            self.assertEqual({path for _, _, path in results},
                             {str(root / "A" / "01.lrc"), str(root / "B" / "01.lrc")})
            self.assertFalse((root / "A" / "02.lrc").exists())
            self.assertFalse(list(root.rglob(".whisper-selected-*")))

    def test_hardlink_failure_falls_back_to_selected_files_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "infer.exe").write_bytes(b"test")
            source = root / "selected.mp3"
            source.write_bytes(b"audio")
            (root / "unselected.mp3").write_bytes(b"audio")
            task = TaskInfo("selected", str(source), "selected", str(root / "workspace"))
            config = SimpleNamespace(whisper_dir=str(root), whisper_cfg={})
            worker = WhisperBatchWorker([task], config)
            results = []
            worker.task_result_signal.connect(lambda task_id, ok, path:
                                              results.append((task_id, ok, path)))

            def fake_single_run(single):
                single.result = (True, str(root / "selected.lrc"))

            with patch("src.steps.step1_whisper.os.link", side_effect=OSError("unsupported")), \
                 patch.object(WhisperWorker, "run", fake_single_run):
                worker.run()
            self.assertEqual(results, [("selected", True, str(root / "selected.lrc"))])
            self.assertFalse(list(root.glob(".whisper-selected-*")))


if __name__ == "__main__":
    unittest.main()
