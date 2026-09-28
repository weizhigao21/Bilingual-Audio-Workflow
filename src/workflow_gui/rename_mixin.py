# -*- coding: utf-8 -*-
"""主窗口 Mixin：混音完成后源文件夹的"双语-"前缀重命名。"""
import os
import time

from PyQt6.QtCore import QTimer

from ..task_manager import (TaskInfo, TASK_PATH_FIELDS,
                            STEP_DONE, STEP_FAILED, STEP_SKIPPED)


def normalize_folder(path: str) -> str:
    """归一化文件夹路径：去掉 "X\\."、"X\\" 之类的冗余写法（"" 仍返回 ""）。

    os.path.normpath("") 会得到 "."，所以空值必须先短路。
    """
    if not path:
        return ""
    return os.path.normpath(path)


def rename_target_name(source_dir: str) -> str:
    """返回该文件夹可用的重命名目标名；不该重命名时返回空串。

    排除四类：
    1. 空路径、"."".." 这类伪目录（basename 为 "." 时拼出 "双语-."，
       Windows 会直接报错，且让"已带前缀则跳过"的幂等判断彻底失效）；
    2. 已经带 "双语-" 前缀的文件夹（幂等）；
    3. 以点或空格结尾的名字（Windows 不允许）；
    4. 盘符根目录（basename 为空）。
    """
    dir_name = os.path.basename(normalize_folder(source_dir))
    if dir_name in ("", os.curdir, os.pardir):
        return ""
    if dir_name.startswith("双语-"):
        return ""
    if dir_name != dir_name.rstrip(" ."):
        return ""
    return dir_name


class RenameMixin:
    """文件夹重命名相关方法。"""

    @staticmethod
    def _relocated_path(old_folder: str, new_folder: str, path: str) -> str:
        """把 old_folder 下的路径改写为 new_folder 下的对应路径。

        path 与 old_folder 本身相同时（import_folder / custom_mix_folder 就是
        被重命名的文件夹），relpath 得到 "."，若直接 os.path.join 就会写出
        "新目录\\."——这种路径 basename 是 "."，会让后续"已带双语-前缀则跳过"
        的判断失效，从而反复把已重命名的文件夹再当一次重命名对象（重复命名）。
        """
        rel = os.path.relpath(path, old_folder)
        if rel == os.curdir:
            return new_folder
        return os.path.normpath(os.path.join(new_folder, rel))

    def _relocate_task_paths(self, old_folder: str, new_folder: str):
        """源目录移动后同步持久化任务中的同目录路径。"""
        old_folder = normalize_folder(old_folder)
        new_folder = normalize_folder(new_folder)
        for task in self.task_queue.tasks:
            changed = False
            for name in TASK_PATH_FIELDS:
                path = getattr(task, name)
                if not path:
                    continue
                try:
                    if os.path.commonpath([path, old_folder]) != old_folder:
                        continue
                except ValueError:
                    continue
                new_value = self._relocated_path(old_folder, new_folder, path)
                if new_value == path:
                    continue
                setattr(task, name, new_value)
                changed = True
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

        返回前统一归一化：历史数据里出现过 "E:\\...\\album\\."（末尾多一个 "."）
        的写法，basename 会变成 "."，导致同名判断和前缀判断全部失真。
        """
        import_folder = normalize_folder(task.import_folder)
        if import_folder and os.path.isdir(import_folder):
            return import_folder
        src_dir = os.path.dirname(normalize_folder(task.source_path))
        if src_dir and os.path.isdir(src_dir):
            return src_dir
        return import_folder or src_dir

    def _do_rename_source_folder(self, task: TaskInfo):
        source_dir = self._resolve_source_folder(task)
        if not source_dir or not os.path.isdir(source_dir):
            return
        # 检查同文件夹的其他任务是否都已完成混音
        for t in self.task_queue.tasks:
            if t is task:
                continue
            t_dir = self._resolve_source_folder(t)
            if t_dir == source_dir and t.step3_status not in (STEP_DONE, STEP_FAILED, STEP_SKIPPED):
                return  # 还有未完成的任务，等最后一个再重命名
        # 已有前缀 / 伪目录（"."、盘符根）等一律跳过，避免无效重试
        dir_name = rename_target_name(source_dir)
        if not dir_name:
            return
        parent = os.path.dirname(source_dir)
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
            # 收集需要重命名的文件夹（按归一化路径去重，避免同一文件夹重复入队）
            folders_to_rename = {}
            for t in self.task_queue.tasks:
                if not (t.from_folder and t.step3_status in (STEP_DONE, STEP_SKIPPED)):
                    continue
                source_dir = self._resolve_source_folder(t)
                if not source_dir or not os.path.isdir(source_dir):
                    continue
                if not rename_target_name(source_dir):
                    continue
                folders_to_rename.setdefault(os.path.normcase(source_dir), source_dir)

            if not folders_to_rename:
                return

            any_failed = False
            for folder in sorted(folders_to_rename.values()):
                parent = os.path.dirname(folder)
                dir_name = rename_target_name(folder)
                if not dir_name:
                    continue
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
