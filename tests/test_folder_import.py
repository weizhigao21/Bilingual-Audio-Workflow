import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.folder_import import build_folder_import_plan, find_matching_subtitle
from src.steps.output_paths import planned_mix_output
from src.steps.step3_mixer import mix_single_task
from src.task_manager import TaskInfo, TaskQueue
from src.widgets.folder_import_dialog import FolderImportPreviewDialog
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

    def test_dialog_folder_selection_and_output_collision(self):
        self.make("A/one.mp3")
        self.make("A/sub/two.mp3")
        plan = self.plan()
        dialog = FolderImportPreviewDialog(str(self.root), plan)
        self.addCleanup(dialog.close)
        self.assertEqual(len(dialog.selected_candidates()), 2)
        parent = dialog.tree.topLevelItem(0)
        parent.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(dialog.selected_candidates(), [])
        self.assertFalse(dialog.import_button.isEnabled())
        parent.setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(len(dialog.selected_candidates()), 2)

        self.make("B/one.mp3")
        conflict_dialog = FolderImportPreviewDialog(str(self.root), self.plan())
        self.addCleanup(conflict_dialog.close)
        self.assertFalse(conflict_dialog.import_button.isEnabled())
        self.assertIn("冲突", conflict_dialog.summary.text())

    def test_folder_button_and_drop_use_preview_selection(self):
        included = self.make("A/one.mp3")
        self.make("B/two.mp3")
        config = SimpleNamespace(
            workspace_dir=str(Path(self.temp.name) / "tasks"),
            tts_cfg={}, mixer_cfg=self.cfg,
        )
        window = WorkflowMainWindow(config)
        self.addCleanup(window.close)
        candidates = self.plan()
        chosen = [c for c in candidates if c.source_path == str(included)]
        with patch("src.workflow_gui.toolbar_mixin.FolderImportPreviewDialog") as preview:
            preview.return_value.exec.return_value = preview.return_value.DialogCode.Accepted
            preview.return_value.selected_candidates.return_value = chosen
            window._scan_folder_for_tasks(str(self.root))
            self.assertEqual(len(window.task_queue.tasks), 1)
            self.assertEqual(window.task_queue.tasks[0].source_path, str(included))
            window.on_files_dropped([str(self.root)])
            self.assertEqual(preview.call_count, 2)
            second_plan = preview.call_args_list[1].args[1]
            self.assertTrue(next(c for c in second_plan if c.source_path == str(included)).locked)


if __name__ == "__main__":
    unittest.main()
