import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.steps.step1_whisper import WhisperBatchWorker
from src.config import (WorkflowConfig, LEGACY_WHISPER_MEDIA_SUFFIXES,
                        WHISPER_MEDIA_SUFFIXES)
from src.task_manager import TaskInfo


def make_tasks(root, paths):
    return [
        TaskInfo(f"task-{i}", str(path), path.stem, str(root / "workspace"),
                 from_folder=True, import_folder=str(root))
        for i, path in enumerate(paths)
    ]


class WhisperSelectedImportTests(unittest.TestCase):
    def test_legacy_default_formats_are_upgraded(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "workflow.json"
            config_path.write_text(json.dumps({
                "workspace_dir": str(Path(temp) / "workspace"),
                "whisper": {"audio_suffixes": LEGACY_WHISPER_MEDIA_SUFFIXES},
            }), encoding="utf-8")
            with patch("src.config.get_config_path", return_value=str(config_path)):
                config = WorkflowConfig()
            self.assertEqual(config.whisper_cfg["audio_suffixes"],
                             WHISPER_MEDIA_SUFFIXES)

    def _run_fake(self, root, tasks, cfg=None):
        whisper = root / "whisper"
        whisper.mkdir()
        (whisper / "infer.exe").write_bytes(b"test")
        worker = WhisperBatchWorker(
            tasks, SimpleNamespace(whisper_dir=str(whisper), whisper_cfg=cfg or {})
        )
        calls = []
        results = []
        worker.task_result_signal.connect(
            lambda task_id, ok, path: results.append((task_id, ok, path))
        )

        class FakeProcess:
            def __init__(self, cmd, **kwargs):
                calls.append(cmd)
                self.stdout = []
                self.returncode = 0
                for value in cmd:
                    path = Path(value)
                    if path.is_dir() and path != whisper:
                        files = path.rglob("*.mp3")
                    elif path.is_file() and path.suffix == ".mp3":
                        files = [path]
                    else:
                        continue
                    for audio in files:
                        audio.with_suffix(".lrc").write_text("subtitle", encoding="utf-8")

            def wait(self, timeout=None):
                return self.returncode

        with patch("src.steps.step1_whisper.subprocess.Popen", FakeProcess):
            worker.run()
        return calls, results

    def test_complete_nested_folder_passes_original_directory_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for relative in ("A/01.mp3", "B/02.mp3"):
                path = root / relative
                path.parent.mkdir()
                path.write_bytes(b"audio")
                paths.append(path)
            calls, results = self._run_fake(root, make_tasks(root, paths),
                                            {"enable_batching": True})
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-1], os.path.normcase(str(root)))
            self.assertIn("--enable_batching", calls[0])
            self.assertNotIn("--output_dir", calls[0])
            self.assertTrue(all(ok for _, ok, _ in results))
            self.assertTrue((root / "A/01.lrc").exists())
            self.assertTrue((root / "B/02.lrc").exists())

    def test_partial_selection_passes_files_in_one_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for relative in ("A/01.mp3", "B/02.mp3", "C/03.mp3"):
                path = root / relative
                path.parent.mkdir()
                path.write_bytes(b"audio")
                paths.append(path)
            calls, results = self._run_fake(root, make_tasks(root, paths[:2]))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-2:], [str(path) for path in paths[:2]])
            self.assertNotIn(str(root), calls[0])
            self.assertTrue(all(ok for _, ok, _ in results))
            self.assertFalse(paths[2].with_suffix(".lrc").exists())

    def test_same_stem_in_different_folders_does_not_restart_or_cross_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for relative in ("A/01.mp3", "B/01.mp3"):
                path = root / relative
                path.parent.mkdir()
                path.write_bytes(b"audio")
                paths.append(path)
            calls, results = self._run_fake(root, make_tasks(root, paths))
            self.assertEqual(len(calls), 1)
            self.assertEqual({path for _, ok, path in results if ok},
                             {str(path.with_suffix(".lrc")) for path in paths})

    def test_missing_subtitle_does_not_use_another_files_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            whisper = root / "whisper"
            whisper.mkdir()
            (whisper / "infer.exe").write_bytes(b"test")
            audio = root / "missing.mp3"
            audio.write_bytes(b"audio")
            (root / "other.lrc").write_text("other", encoding="utf-8")
            worker = WhisperBatchWorker(make_tasks(root, [audio]),
                SimpleNamespace(whisper_dir=str(whisper), whisper_cfg={}))
            results = []
            worker.task_result_signal.connect(
                lambda task_id, ok, path: results.append((task_id, ok, path))
            )

            class FakeProcess:
                stdout = []
                returncode = 0
                def __init__(self, cmd, **kwargs):
                    pass
                def wait(self, timeout=None):
                    return 0

            with patch("src.steps.step1_whisper.subprocess.Popen", FakeProcess):
                worker.run()
            self.assertFalse(results[0][1])


if __name__ == "__main__":
    unittest.main()
