import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.steps.step1_whisper import WhisperBatchWorker, WhisperWorker
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
    def test_batch_progress_counts_audio_inside_folder_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "group" / "01.mp3"
            second = root / "group" / "02.mp3"
            third = root / "other" / "03.mp3"
            for path in (first, second, third):
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"audio")
            tasks = make_tasks(root, [first, second, third])
            self.assertEqual(
                WhisperBatchWorker._batch_task_ids([str(first.parent)], tasks),
                {"task-0", "task-1"},
            )
            self.assertEqual(
                WhisperBatchWorker._batch_task_ids([str(third)], tasks),
                {"task-2"},
            )

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

    def _run_fake(self, root, tasks, cfg=None, exit_code=0, generated=None):
        whisper = root / "whisper"
        whisper.mkdir()
        (whisper / "infer.exe").write_bytes(b"test")
        worker = WhisperBatchWorker(
            tasks, SimpleNamespace(whisper_dir=str(whisper), whisper_cfg=cfg or {})
        )
        calls = []
        results = []
        selected_outputs = ({os.path.normcase(os.path.abspath(path)) for path in generated}
                            if generated is not None else None)
        worker.task_result_signal.connect(
            lambda task_id, ok, path: results.append((task_id, ok, path))
        )

        class FakeProcess:
            def __init__(self, cmd, **kwargs):
                calls.append(cmd)
                self.stdout = []
                self.returncode = exit_code
                for value in cmd:
                    path = Path(value)
                    if path.is_dir() and path != whisper:
                        files = path.rglob("*.mp3")
                    elif path.is_file() and path.suffix == ".mp3":
                        files = [path]
                    else:
                        continue
                    for audio in files:
                        if (selected_outputs is None or
                                os.path.normcase(os.path.abspath(audio)) in selected_outputs):
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

    def test_native_exit_after_writing_subtitles_preserves_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = [root / "01.mp3", root / "02.mp3"]
            for path in paths:
                path.write_bytes(b"audio")
            calls, results = self._run_fake(
                root, make_tasks(root, paths), exit_code=3221226505,
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual({task_id for task_id, ok, _ in results if ok},
                             {"task-0", "task-1"})

    def test_native_exit_reports_only_missing_subtitles_as_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = [root / "01.mp3", root / "02.mp3"]
            for path in paths:
                path.write_bytes(b"audio")
            _, results = self._run_fake(
                root, make_tasks(root, paths), exit_code=3221226505,
                generated={str(paths[0])},
            )
            by_id = {task_id: (ok, message) for task_id, ok, message in results}
            self.assertTrue(by_id["task-0"][0])
            self.assertFalse(by_id["task-1"][0])
            self.assertIn("3221226505", by_id["task-1"][1])

    def test_overwrite_requires_fresh_subtitle_after_native_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "01.mp3"
            audio.write_bytes(b"audio")
            audio.with_suffix(".lrc").write_text("old", encoding="utf-8")
            _, results = self._run_fake(
                root, make_tasks(root, [audio]), cfg={"overwrite": True},
                exit_code=3221226505, generated=set(),
            )
            self.assertFalse(results[0][1])

    def test_single_worker_keeps_written_subtitle_on_native_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "infer.exe").write_bytes(b"test")
            audio = root / "01.mp3"
            audio.write_bytes(b"audio")
            task = make_tasks(root, [audio])[0]
            worker = WhisperWorker(task, SimpleNamespace(
                whisper_dir=str(root), whisper_cfg={}
            ))

            class FakeProcess:
                stdout = []
                returncode = 3221226505
                def __init__(self, cmd, **kwargs):
                    audio.with_suffix(".lrc").write_text("subtitle", encoding="utf-8")
                def wait(self, timeout=None):
                    return self.returncode

            with patch("src.steps.step1_whisper.subprocess.Popen", FakeProcess):
                worker.run()
            self.assertEqual(worker.result, (True, str(audio.with_suffix(".lrc"))))

    def test_existing_subtitles_recover_without_starting_infer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "01.mp3"
            audio.write_bytes(b"audio")
            subtitle = audio.with_suffix(".lrc")
            subtitle.write_text("subtitle", encoding="utf-8")
            task = make_tasks(root, [audio])[0]
            config = SimpleNamespace(whisper_dir=str(root), whisper_cfg={})
            batch = WhisperBatchWorker([task], config)
            results = []
            batch.task_result_signal.connect(
                lambda task_id, ok, path: results.append((task_id, ok, path))
            )
            single = WhisperWorker(task, config)
            with patch("src.steps.step1_whisper.subprocess.Popen",
                       side_effect=AssertionError("infer must not start")):
                batch.run()
                single.run()
            self.assertEqual(results, [(task.task_id, True, str(subtitle))])
            self.assertEqual(single.result, (True, str(subtitle)))

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
