# -*- coding: utf-8 -*-
"""主窗口 Mixin：混音完成后源文件夹的"双语-"前缀重命名。"""
import os
import time

from PyQt6.QtCore import QTimer

from ..task_manager import TaskInfo, STEP_DONE, STEP_FAILED, STEP_SKIPPED


class RenameMixin:
    """文件夹重命名相关方法。"""

    def _relocate_task_paths(self, old_folder: str, new_folder: str):
        """源目录移动后同步持久化任务中的同目录路径。"""
        path_fields = ("source_path", "import_folder", "custom_subtitle",
                       "custom_mix_folder", "step1_output", "step2_output",
                       "step3_output")
        for task in self.task_queue.tasks:
            changed = False
            for name in path_fields:
                path = getattr(task, name)
                if not path:
                    continue
                try:
                    if os.path.commonpath([path, old_folder]) != old_folder:
                        continue
                    setattr(task, name, os.path.join(new_folder, os.path.relpath(path, old_folder)))
                    changed = True
                except ValueError:
                    continue
            if changed:
                task.save()
        for group in self.task_queue.groups:
            if group.folder_path == old_folder:
                group.folder_path = new_folder
                group.group_name = os.path.basename(new_folder)

    def _rename_source_folder(self, task: TaskInfo):
        """混音完成后，将拖入的源文件夹重命名，添加\"双语-\"前缀。
        只有当同文件夹所有任务混音都完成后才执行重命名。
        """
        try:
            self._do_rename_source_folder(task)
        except Exception:
            pass

    def _resolve_source_folder(self, task: TaskInfo) -> str:
        """返回任务当前应重命名的源文件夹（磁盘上真实存在的那个）。

        import_folder 记录拖入时的原始路径；若文件夹已被重命名，import_folder
        仍是旧路径（磁盘上不存在），此时回退到 source_path 所在目录
        （source_path 每次都被 _resolve_source_path 自动修正到当前有效路径）。
        """
        import_folder = task.import_folder
        if import_folder and os.path.isdir(import_folder):
            return import_folder
        src_dir = os.path.dirname(task.source_path)
        if os.path.isdir(src_dir):
            return src_dir
        return import_folder or src_dir

    def _do_rename_source_folder(self, task: TaskInfo):
        source_dir = self._resolve_source_folder(task)
        # 检查同文件夹的其他任务是否都已完成混音
        for t in self.task_queue.tasks:
            if t is task:
                continue
            t_dir = self._resolve_source_folder(t)
            if t_dir == source_dir and t.step3_status not in (STEP_DONE, STEP_FAILED, STEP_SKIPPED):
                return  # 还有未完成的任务，等最后一个再重命名
        parent = os.path.dirname(source_dir)
        dir_name = os.path.basename(source_dir)
        # 已有前缀则跳过
        if dir_name.startswith("双语-"):
            return
        new_name = f"双语-{dir_name}"
        new_path = os.path.join(parent, new_name)
        if os.path.exists(new_path):
            self._append_log(f"[重命名] 目标已存在，跳过: {new_name}")
            return

        # 重试最多3次，避免文件句柄未释放导致失败
        renamed = False
        for attempt in range(1, 4):
            try:
                os.rename(source_dir, new_path)
                renamed = True
                break
            except OSError:
                if attempt < 3:
                    time.sleep(0.3)

        if not renamed:
            self._append_log(f"[重命名] 失败 (已重试3次): {dir_name}")
            return

        self._relocate_task_paths(source_dir, new_path)
        self._append_log(f"[重命名] 文件夹已重命名: {dir_name} → {new_name}")

    def _rename_batch_folders(self):
        """批量完成后，延迟半秒再统一重命名所有文件夹导入的源文件夹。
        延迟是为了让所有线程/子进程释放文件句柄。
        """
        self._rename_retry_count = 0
        QTimer.singleShot(500, self._do_rename_batch_folders)

    def _do_rename_batch_folders(self):
        """执行批量重命名逻辑。

        收集条件与单任务模式保持一致：已完成(STEP_DONE) 或 已跳过
        (STEP_SKIPPED，如自动检测到已有双语输出) 的文件夹导入任务。
        若有重命名失败（文件句柄未释放等），稍后整体重试（幂等：已加前缀的跳过）。
        """
        try:
            # 收集需要重命名的文件夹（去重）
            folders_to_rename = set()
            for t in self.task_queue.tasks:
                if t.from_folder and t.step3_status in (STEP_DONE, STEP_SKIPPED):
                    source_dir = self._resolve_source_folder(t)
                    dir_name = os.path.basename(source_dir)
                    if not dir_name.startswith("双语-"):
                        folders_to_rename.add(source_dir)

            if not folders_to_rename:
                return

            any_failed = False
            for folder in sorted(folders_to_rename):
                parent = os.path.dirname(folder)
                dir_name = os.path.basename(folder)
                new_name = f"双语-{dir_name}"
                new_path = os.path.join(parent, new_name)
                if os.path.exists(new_path):
                    self._append_log(f"[重命名] 目标已存在，跳过: {new_name}")
                    continue
                renamed = False
                for attempt in range(1, 4):
                    try:
                        os.rename(folder, new_path)
                        renamed = True
                        break
                    except OSError:
                        if attempt < 3:
                            time.sleep(0.5)
                if not renamed:
                    any_failed = True
                    self._append_log(f"[重命名] 失败 (已重试3次): {dir_name}")
                    continue
                self._relocate_task_paths(folder, new_path)
                self._append_log(f"[重命名] 文件夹已重命名: {dir_name} → {new_name}")

            # 有失败的，稍后整体重试（最多 3 次；已成功的不受影响）
            if any_failed:
                self._rename_retry_count = getattr(self, "_rename_retry_count", 0) + 1
                if self._rename_retry_count <= 3:
                    self._append_log(f"[重命名] 有文件夹未重命名，1.5 秒后重试 "
                                     f"({self._rename_retry_count}/3)")
                    QTimer.singleShot(1500, self._do_rename_batch_folders)
        except Exception:
            pass
