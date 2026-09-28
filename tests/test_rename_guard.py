# -*- coding: utf-8 -*-
"""源文件夹"双语-"前缀重命名的防重复命名守卫。

回归的是这条真实故障链：
  _relocate_task_paths 把与文件夹自身相等的字段（import_folder）写成 "新目录\\."
  → import_folder 的 basename 变成 "."
  → "已带双语-前缀则跳过"的幂等判断失真
  → 每次批量都把已重命名的文件夹再当一次重命名对象，目标名 "双语-." 非法，
     内层重试 3 次失败 + 外层整批再重试 3 次，日志刷屏 "失败 (已重试3次): ."
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.task_manager import TaskQueue, STEP_DONE, STEP_SKIPPED
from src.workflow_gui import rename_mixin
from src.workflow_gui.rename_mixin import (RenameMixin, normalize_folder,
                                           rename_target_name)


class Renamer(RenameMixin):
    """最小宿主：只提供 task_queue 与日志收集。"""

    def __init__(self, task_queue):
        self.task_queue = task_queue
        self.logs = []

    def _append_log(self, message):
        self.logs.append(message)


class RenameGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _make_folder_task(self, folder_name="album", step3_status=STEP_DONE,
                          with_full_pipeline=False):
        source_dir = self.root / folder_name
        source_dir.mkdir(exist_ok=True)
        source = source_dir / "track.wav"
        source.write_bytes(b"audio")
        subtitle_path = ""
        mix_folder = ""
        if with_full_pipeline:
            # 让恢复时 step1/step2/step3 都能保持 DONE（否则会被重置为 PENDING）
            subtitle = source_dir / "track.lrc"
            subtitle.write_bytes(b"lyric")
            subtitle_path = str(subtitle)
            clips = source_dir / "clips"
            clips.mkdir(exist_ok=True)
            (clips / "0001_00-00-00.00.wav").write_bytes(b"clip")
            mix_folder = str(clips)
        queue = TaskQueue(str(self.root / "workspace"))
        group = queue.create_group(str(source_dir))
        task = queue.add_task(str(source), subtitle_path=subtitle_path,
                              mix_folder=mix_folder, group_id=group.group_id)
        task.step3_status = step3_status
        if with_full_pipeline:
            mixed_dir = source_dir / "双语"
            mixed_dir.mkdir(exist_ok=True)
            mixed = mixed_dir / "track_mixed.mp3"
            mixed.write_bytes(b"mixed")
            task.step3_output = str(mixed)
        queue.update_task(task)
        return queue, group, task, source_dir

    def _names(self, parent):
        return sorted(p.name for p in parent.iterdir())

    # ---------- 纯函数守卫 ----------

    def test_rename_target_name_rejects_pseudo_and_prefixed(self):
        self.assertEqual(rename_target_name(""), "")
        self.assertEqual(rename_target_name("."), "")
        self.assertEqual(rename_target_name(".."), "")
        # "X\\." 先被归一化成 "X"，因此这里返回真实文件夹名（自愈后仍可重命名）
        self.assertEqual(rename_target_name(str(self.root / ".")), self.root.name)
        self.assertEqual(rename_target_name(str(self.root / "双语-album")), "")
        self.assertEqual(rename_target_name(str(self.root / "album.")), "")
        self.assertEqual(rename_target_name(str(self.root / "album ")), "")
        self.assertEqual(rename_target_name(str(Path(self.root.anchor))), "")
        self.assertEqual(rename_target_name(str(self.root / "album")), "album")

    def test_normalize_folder_keeps_empty_and_strips_dot_suffix(self):
        self.assertEqual(normalize_folder(""), "")
        self.assertEqual(normalize_folder(str(self.root / "album" / ".")),
                         str(self.root / "album"))
        self.assertEqual(normalize_folder(str(self.root / "album")),
                         str(self.root / "album"))

    # ---------- 写回路径：不得再产生 "X\\." ----------

    def test_relocate_never_writes_dot_suffix(self):
        queue, group, task, source_dir = self._make_folder_task()
        renamer = Renamer(queue)
        renamer._do_rename_source_folder(task)

        new_dir = self.root / "双语-album"
        self.assertTrue(new_dir.is_dir())
        self.assertEqual(normalize_folder(task.import_folder), str(new_dir))
        self.assertEqual(task.import_folder, str(new_dir))
        self.assertFalse(task.import_folder.endswith(os.sep + "."))
        self.assertEqual(task.source_path, str(new_dir / "track.wav"))
        self.assertEqual(normalize_folder(task.step3_output),
                         task.step3_output)
        self.assertNotIn(os.sep + ".", task.step3_output)
        self.assertEqual(group.folder_path, str(new_dir))

    # ---------- 核心回归：已重命名的文件夹不得被重复命名 ----------

    def test_polluted_import_folder_does_not_trigger_duplicate_rename(self):
        """已重命名的文件夹 + 历史脏 import_folder（末尾 "\\." ）→ 不许再动它。"""
        queue, group, task, source_dir = self._make_folder_task()
        renamer = Renamer(queue)
        renamer._do_rename_source_folder(task)
        new_dir = self.root / "双语-album"
        before = self._names(self.root)

        # 模拟旧版本写坏的历史数据
        task.import_folder = str(new_dir) + os.sep + "."
        queue.update_task(task)
        renamer.logs.clear()

        with mock.patch.object(rename_mixin, "QTimer") as fake_timer:
            renamer._do_rename_batch_folders()

        self.assertEqual(
            [m for m in renamer.logs if "失败" in m], [],
            "已带前缀的文件夹被重复当作重命名对象")
        self.assertEqual(
            [m for m in renamer.logs if "重试" in m], [],
            "不该为伪目录/已重命名文件夹安排重试")
        fake_timer.singleShot.assert_not_called()
        self.assertEqual(self._names(self.root), before,
                         "重命名目标名非法时不得产生任何新目录")
        self.assertTrue(new_dir.is_dir())
        self.assertFalse(any(n in ("双语-", "双语-.") for n in self._names(self.root)))

    def test_single_task_rename_skips_already_prefixed(self):
        queue, group, task, source_dir = self._make_folder_task(folder_name="双语-album")
        task.import_folder = str(source_dir)
        queue.update_task(task)
        renamer = Renamer(queue)
        renamer._do_rename_source_folder(task)
        self.assertTrue(source_dir.is_dir())
        self.assertFalse((self.root / "双语-双语-album").exists())
        self.assertEqual([m for m in renamer.logs if "失败" in m], [])

    # ---------- 脏数据自愈 + 恢复原有能力 ----------

    def test_dirty_task_json_self_heals_on_restore(self):
        queue, group, task, source_dir = self._make_folder_task(with_full_pipeline=True)
        state = Path(task.task_json_path)
        data = json.loads(state.read_text(encoding="utf-8"))
        data["import_folder"] = str(source_dir) + os.sep + "."
        data["custom_mix_folder"] = str(source_dir) + os.sep + "."
        state.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding="utf-8")

        restored = TaskQueue(str(self.root / "workspace"))
        self.assertEqual(restored.restore_tasks(), (1, 0))
        recovered = restored.tasks[0]
        self.assertEqual(recovered.step3_status, STEP_DONE)
        self.assertEqual(recovered.import_folder, str(source_dir))
        self.assertEqual(recovered.custom_mix_folder, str(source_dir))
        self.assertEqual(
            json.loads(state.read_text(encoding="utf-8"))["import_folder"],
            str(source_dir), "修复后的路径应落盘")
        self.assertEqual(restored.groups[0].folder_path, str(source_dir))

        # 自愈之后，未加前缀的真实文件夹仍能被正常重命名（功能没被修坏）
        Renamer(restored)._do_rename_batch_folders()
        self.assertTrue((self.root / "双语-album").is_dir())

    def test_batch_renames_once_and_dedupes_same_folder(self):
        """同一文件夹的多个任务只重命名一次，成功后不再安排重试。"""
        source_dir = self.root / "album"
        source_dir.mkdir()
        queue = TaskQueue(str(self.root / "workspace"))
        group = queue.create_group(str(source_dir))
        tasks = []
        for index in range(3):
            src = source_dir / f"track{index}.wav"
            src.write_bytes(b"audio")
            task = queue.add_task(str(src), group_id=group.group_id)
            task.step3_status = STEP_DONE
            queue.update_task(task)
            tasks.append(task)

        renamer = Renamer(queue)
        with mock.patch.object(rename_mixin, "QTimer") as fake_timer:
            renamer._do_rename_batch_folders()

        new_dir = self.root / "双语-album"
        self.assertTrue(new_dir.is_dir())
        self.assertEqual(len([m for m in renamer.logs if "文件夹已重命名" in m]), 1)
        self.assertEqual([m for m in renamer.logs if "失败" in m], [])
        fake_timer.singleShot.assert_not_called()
        for task in tasks:
            self.assertEqual(task.import_folder, str(new_dir))
            self.assertEqual(task.source_path, str(new_dir / task.source_path.split(os.sep)[-1]))

    def test_skipped_status_folders_are_renamed(self):
        """STEP_SKIPPED（检测到已有双语输出）同样纳入批量重命名。"""
        queue, group, task, source_dir = self._make_folder_task(step3_status=STEP_SKIPPED)
        renamer = Renamer(queue)
        with mock.patch.object(rename_mixin, "QTimer") as fake_timer:
            renamer._do_rename_batch_folders()
        self.assertTrue((self.root / "双语-album").is_dir())
        fake_timer.singleShot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
