import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from src.task_manager import TaskQueue, STEP_DONE
from src.widgets.task_list import TaskListWidget
from src.workflow_gui.window import WorkflowMainWindow


class FolderTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media_root = self.root / "book"
        self.media_root.mkdir()
        self.workspace = self.root / "workspace"
        self.queue = TaskQueue(str(self.workspace))
        self.tree = TaskListWidget()
        self.tree.set_task_queue(self.queue)
        self.queue.group_added.connect(self.tree.add_group_item)
        self.queue.group_removed.connect(self.tree.remove_group_item)
        self.queue.task_added.connect(self.tree.add_task_item)
        self.queue.task_removed.connect(self.tree.remove_task_item)
        self.queue.task_updated.connect(self.tree.update_task_item)
        self.group = self.queue.create_group(str(self.media_root))

    def add_audio(self, relative_path):
        source = self.media_root / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"source")
        task = self.queue.add_task(str(source), group_id=self.group.group_id)
        return task

    def test_nested_layout_and_precise_subtree_removal(self):
        root_audio = self.add_audio("intro.wav")
        chap1 = self.add_audio("Part/Chap1/01.wav")
        chap1_second = self.add_audio("Part/Chap1/02.wav")
        chap2 = self.add_audio("Part/Chap2/01.wav")
        similar = self.add_audio("Part2/01.wav")

        gid = self.group.group_id
        root_item = self.tree._find_group_item(gid)
        part_item = self.tree._find_folder_item(gid, "Part")
        chap1_item = self.tree._find_folder_item(gid, os.path.join("Part", "Chap1"))
        self.assertIsNotNone(part_item)
        self.assertIs(chap1_item.parent(), part_item)
        self.assertEqual(chap1_item.childCount(), 2)
        self.assertIs(self.tree._find_task_item(chap2.task_id).parent(),
                      self.tree._find_folder_item(gid, os.path.join("Part", "Chap2")))
        self.assertEqual(root_item.child(0).data(0, self.tree.ROLE_KIND), "folder")
        self.assertEqual([t.task_id for t in self.queue.tasks_in_group_folder(gid, "Part")],
                         [chap1.task_id, chap1_second.task_id, chap2.task_id])
        self.assertEqual(self.queue.tasks_in_group_folder(gid, ".."), [])

        for step in (1, 2, 3):
            chap1.set_step_status(step, STEP_DONE)
        self.queue.update_task(chap1)
        self.assertIn("50%", chap1_item.text(0))
        self.assertIn("(1/2)", chap1_item.text(0))

        old_records = [Path(t.task_json_path) for t in (chap1, chap1_second, chap2)]
        self.assertEqual(self.queue.remove_group_folder(gid, "Part"), 3)
        self.assertIsNone(self.tree._find_folder_item(gid, "Part"))
        self.assertEqual({t.task_id for t in self.group.tasks},
                         {root_audio.task_id, similar.task_id})
        self.assertEqual(root_item.childCount(), 2)
        self.assertTrue(all(Path(t.source_path).is_file() for t in (chap1, chap1_second, chap2)))
        self.assertTrue(all(not path.exists() for path in old_records))

    def test_restart_rebuilds_same_hierarchy(self):
        self.add_audio("Disc1/Track01.wav")
        self.add_audio("Disc1/Bonus/Track02.wav")
        self.add_audio("Disc2/Track01.wav")
        restored = TaskQueue(str(self.workspace))
        tree = TaskListWidget()
        tree.set_task_queue(restored)
        restored.group_added.connect(tree.add_group_item)
        restored.task_added.connect(tree.add_task_item)
        self.assertEqual(restored.restore_tasks(), (3, 0))
        gid = restored.groups[0].group_id
        self.assertIsNotNone(tree._find_folder_item(gid, "Disc1"))
        self.assertIsNotNone(tree._find_folder_item(gid, os.path.join("Disc1", "Bonus")))
        self.assertEqual(tree._find_folder_item(gid, "Disc2").childCount(), 1)

    def test_subfolder_selection_and_removal_in_main_window(self):
        config = SimpleNamespace(workspace_dir=str(self.root / "other-workspace"), tts_cfg={})
        window = WorkflowMainWindow(config)
        self.addCleanup(window.close)
        group = window.task_queue.create_group(str(self.media_root))
        files = []
        for relative in ("A/one.wav", "A/sub/two.wav", "B/three.wav"):
            source = self.media_root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"source")
            files.append(source)
            window.task_queue.add_task(str(source), group_id=group.group_id)
        folder_item = window.task_list._find_folder_item(group.group_id, "A")
        window.task_list.setCurrentItem(folder_item)
        self.assertEqual(len(window._current_group.tasks), 2)
        self.assertEqual(window._current_group.group_name, "A")
        selected = []
        with patch.object(window, "_start_group_step", side_effect=lambda group, step: selected.extend(group.tasks)):
            window._on_start_step(1)
        self.assertEqual(len(selected), 2)
        removed_one = window._current_group.tasks[0]
        window.task_queue.remove_task(removed_one.task_id)
        if window._selected_folder:
            self.assertEqual(len(window._current_group.tasks), 1)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window._on_folder_remove(group.group_id, "A")
        self.assertEqual(len(window.task_queue.tasks), 1)
        self.assertEqual(window.task_queue.tasks[0].source_path, str(files[2]))
        self.assertTrue(all(source.exists() for source in files))
        self.assertIsNone(window.task_list._find_folder_item(group.group_id, "A"))
        window.task_queue.clear_all()
        self.assertIsNone(window._current_group)
        self.assertEqual(window.current_task_label.text(), "未选择任务")

    def test_group_subtitles_use_batch_mode(self):
        config = SimpleNamespace(workspace_dir=str(self.root / "batch-workspace"),
                                 tts_cfg={}, whisper_cfg={"enable_batching": False},
                                 mixer_cfg={}, is_configured=lambda: True)
        window = WorkflowMainWindow(config)
        self.addCleanup(window.close)
        group = window.task_queue.create_group(str(self.media_root))
        source = self.media_root / "chapter.wav"
        source.write_bytes(b"audio")
        window.task_queue.add_task(str(source), group_id=group.group_id)
        with patch("src.steps.batch_executor.BatchExecutor.start"):
            window._start_group_step(group, 1)
        self.assertEqual(window._batch_executor.order, "by_step")
        window._batch_executor = None


if __name__ == "__main__":
    unittest.main()
