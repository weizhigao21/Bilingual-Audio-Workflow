import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.folder_import import build_folder_import_plan, find_matching_subtitle
from src.steps.output_paths import planned_mix_output
from src.steps.step3_mixer import mix_single_task
from src.task_manager import TaskInfo, TaskQueue
from src.workflow_gui.window import WorkflowMainWindow


class FolderImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "book"
        self.root.mkdir()
        self.workspace = self.root / "workspace"
        self.output = self.root / "exports"
        self.cfg = {
            "output_folder": str(self.output), "output_format": "mp3",
            "add_suffix": True, "skip_existing": True,
        }

    def make(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
        return path

    def plan(self, existing=()):
        return build_folder_import_plan(
            str(self.root), self.cfg, existing, str(self.workspace)
        )

    def test_preview_defaults_exclude_generated_workspace_and_existing_tasks(self):
        source = self.make("Disc1/Track01.mp3")
        subtitle = self.make("Disc1/Track01.srt")
        duplicate = self.make("Disc2/Track02.wav")
        self.make("双语/Track01_mixed.mp3")
        self.make("双语-Disc1/Track03.mp3")
        self.make("Disc1/Track01_mixed.mp3")
        self.make(".whisper-selected-old/temporary.mp3")
        custom_output = self.make("exports/Track04.mp3")
        workspace_audio = self.make("workspace/voice.wav")

        plan = self.plan(existing=[duplicate])
        by_rel = {c.relative_path.replace(os.sep, "/"): c for c in plan}
        self.assertEqual(len(plan), 8)
        self.assertEqual(by_rel["Disc1/Track01.mp3"].subtitle_path, str(subtitle))
        self.assertEqual(by_rel["Disc1/Track01.mp3"].output_path,
                         planned_mix_output(str(source), self.cfg, True))
        self.assertTrue(by_rel["Disc1/Track01.mp3"].default_selected)
        self.assertTrue(by_rel["Disc2/Track02.wav"].locked)
        self.assertFalse(by_rel["Disc2/Track02.wav"].default_selected)
        for rel in ("双语/Track01_mixed.mp3", "双语-Disc1/Track03.mp3",
                    "Disc1/Track01_mixed.mp3", "exports/Track04.mp3",
                    "workspace/voice.wav", ".whisper-selected-old/temporary.mp3"):
            self.assertFalse(by_rel[rel].default_selected, rel)
        self.assertIn("自定义导出目录", by_rel["exports/Track04.mp3"].reason)
        self.assertIn("工作区", by_rel["workspace/voice.wav"].reason)
        self.assertEqual(TaskQueue.scan_folder_media(str(self.root),
                         custom_output=str(self.output)),
                         [str(source), str(duplicate), str(workspace_audio)])
        self.assertTrue(custom_output.is_file())
        self.assertTrue(workspace_audio.is_file())

    def test_subtitle_lookup_reuses_directory_listing_and_prefers_exact(self):
        source = self.make("A/Video.mp3")
        exact = self.make("A/video.SRT")
        self.make("A/Video.zh-CN.lrc")
        cache = {}
        with patch("src.folder_import.os.listdir", wraps=os.listdir) as listing:
            self.assertEqual(find_matching_subtitle(str(source), cache), str(exact))
            self.assertEqual(find_matching_subtitle(str(source), cache), str(exact))
        self.assertEqual(listing.call_count, 1)

    def test_sidecar_and_source_overwrite_are_excluded(self):
        sidecar_output = self.make("A/finished.mp3")
        self.make("A/finished.mp3.mix.json")
        source = self.make("A/plain.mp3")
        by_path = {c.source_path: c for c in self.plan()}
        self.assertIn("制作记录", by_path[str(sidecar_output)].reason)
        cfg = {"output_folder": str(source.parent), "output_format": "mp3",
               "add_suffix": False}
        plan = build_folder_import_plan(str(self.root), cfg)
        by_path = {c.source_path: c for c in plan}
        self.assertTrue(by_path[str(source)].locked)
        self.assertIn("覆盖源文件", by_path[str(source)].reason)

    def test_output_path_preview_matches_folder_rules(self):
        source = self.make("Disc/track.wav")
        cfg = {"output_format": "m4a", "add_suffix": True,
               "output_folder_prefix": True}
        self.assertEqual(planned_mix_output(str(source), cfg, True),
                         str(self.root / "双语-Disc" / "track_mixed.m4a"))
        cfg["output_folder_prefix"] = False
        self.assertEqual(planned_mix_output(str(source), cfg, True),
                         str(self.root / "Disc" / "双语" / "track_mixed.m4a"))

    def test_mixer_rejects_source_overwrite_without_preview(self):
        source = self.make("source.mp3")
        clips = self.root / "clips"
        clips.mkdir()
        task = TaskInfo("safe-mix", str(source), "source", str(self.workspace),
                        step2_output=str(clips))
        config = SimpleNamespace(mixer_cfg={
            "output_folder": str(self.root), "output_format": "mp3",
            "add_suffix": False,
        })
        before = source.read_bytes()
        ok, message = mix_single_task(task, config)
        self.assertFalse(ok)
        self.assertIn("源文件相同", message)
        self.assertEqual(source.read_bytes(), before)

    def test_dropped_subtitle_mismatch_requires_confirmation(self):
        media = self.make("OP.mp3")
        subtitle = self.make("other-name.lrc")
        config = SimpleNamespace(
            workspace_dir=str(Path(self.temp.name) / "tasks-mismatch"),
            tts_cfg={}, mixer_cfg={"folder_prefix": False},
            config={"workspace_dir": str(Path(self.temp.name) / "tasks-mismatch")},
            save=lambda: None,
        )
        window = WorkflowMainWindow(config)
        self.addCleanup(window.close)
        task = window.task_queue.add_task(str(media))
        window.task_queue.set_current(task.task_id)

        with patch.object(window, "_ask_subtitle_mismatch", return_value=False):
            window.on_files_dropped([str(subtitle)])
        self.assertFalse(task.custom_subtitle, "用户拒绝后不得应用到当前任务")

        with patch.object(window, "_ask_subtitle_mismatch", return_value=True):
            window.on_files_dropped([str(subtitle)])
        self.assertEqual(task.custom_subtitle, str(subtitle.resolve()))

    def test_folder_import_and_drop_do_not_open_preview(self):
        included = self.make("A/one.mp3")
        second = self.make("B/two.mp3")
        self.make("双语/old_mixed.mp3")
        config = SimpleNamespace(
            workspace_dir=str(Path(self.temp.name) / "tasks"),
            tts_cfg={}, mixer_cfg=self.cfg,
        )
        window = WorkflowMainWindow(config)
        self.addCleanup(window.close)
        with patch("src.workflow_gui.toolbar_mixin.QMessageBox") as message_box:
            window._scan_folder_for_tasks(str(self.root))
            self.assertEqual({t.source_path for t in window.task_queue.tasks},
                             {str(included), str(second)})
            window.on_files_dropped([str(self.root)])
            self.assertEqual(len(window.task_queue.tasks), 2)
            message_box.assert_not_called()
        # 导入结束后应停在文件夹组，而不是最后一个子任务
        self.assertIsNotNone(window._current_group)
        self.assertIsNone(window.task_queue.current)
        self.assertEqual(window.current_task_label.text(),
                         window._current_group.group_name)

    def test_multi_file_import_clears_last_task_selection(self):
        first = self.make("first.mp3")
        second = self.make("second.mp3")
        config = SimpleNamespace(
            workspace_dir=str(Path(self.temp.name) / "tasks-multi"),
            tts_cfg={}, mixer_cfg={"folder_prefix": False},
        )
        window = WorkflowMainWindow(config)
        self.addCleanup(window.close)
        window.on_files_dropped([str(first), str(second)])
        self.assertEqual(len(window.task_queue.tasks), 2)
        self.assertIsNone(window.task_queue.current)
        self.assertTrue(window.batch_btn.isEnabled())
        # 页头直接汇总整个队列，主按钮变成「全部执行」而不是禁用
        self.assertEqual(window.current_task_label.text(), "已识别 2 个任务")
        self.assertIn("2 个未完成", window.scope_label.text())
        self.assertEqual(window.run_task_btn.text(), "全部执行（2）")
        self.assertTrue(window.run_task_btn.isEnabled())
        with patch.object(window, "_start_scope_pipeline") as start:
            window._on_run_task()
        start.assert_called_once()
        self.assertEqual(len(start.call_args.args[0]), 2)

    def test_output_collision_auto_skips_duplicate_target(self):
        first = self.make("A/one.mp3")
        self.make("B/one.mp3")
        config = SimpleNamespace(
            workspace_dir=str(Path(self.temp.name) / "tasks"),
            tts_cfg={}, mixer_cfg=self.cfg,
        )
        window = WorkflowMainWindow(config)
        self.addCleanup(window.close)
        window._scan_folder_for_tasks(str(self.root))
        self.assertEqual(len(window.task_queue.tasks), 1)
        self.assertEqual(window.task_queue.tasks[0].source_path, str(first))


if __name__ == "__main__":
    unittest.main()
