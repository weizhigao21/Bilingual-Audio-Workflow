# -*- coding: utf-8 -*-
"""左侧任务列表（树形）组件。"""
import os
from PyQt6.QtWidgets import (
    QTreeWidget, QTreeWidgetItem, QMenu, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction

from ..task_manager import (
    TaskInfo, TaskGroup, STEP_DONE, STEP_FAILED, STEP_RUNNING, STEP_SKIPPED,
)
from .common import _natural_sort_key


class TaskListWidget(QTreeWidget):
    """左侧任务列表（树形）。

    文件夹组下按源文件相对路径显示任意层级的目录与音频任务。
    支持拖拽添加视频/字幕/配音文件。
    """
    task_selected = pyqtSignal(str)   # task_id
    task_remove_requested = pyqtSignal(str)
    task_rerun_requested = pyqtSignal(str, int)  # (task_id, step)
    metadata_edit_requested = pyqtSignal(str)  # task_id
    group_selected = pyqtSignal(str)  # group_id
    group_remove_requested = pyqtSignal(str)
    group_rerun_requested = pyqtSignal(str, int)  # (group_id, step)
    folder_selected = pyqtSignal(str, str)  # (group_id, relative_folder)
    folder_remove_requested = pyqtSignal(str, str)
    folder_rerun_requested = pyqtSignal(str, str, int)

    # 源视频/音频文件（创建新任务）
    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts",
                  ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
    # 字幕文件（添加到当前任务，跳过步骤1）
    SUBTITLE_EXTS = {".lrc", ".vtt", ".srt"}
    # 配音文件（添加到当前任务，跳过步骤2）
    MIX_AUDIO_EXTS = {".wav"}

    # 节点 UserRole 数据
    ROLE_KIND = Qt.ItemDataRole.UserRole          # "group" / "task"
    ROLE_ID = Qt.ItemDataRole.UserRole + 1        # group_id / task_id
    ROLE_NAME = Qt.ItemDataRole.UserRole + 2      # 显示名（用于排序）
    ROLE_FOLDER_REL = Qt.ItemDataRole.UserRole + 3  # 相对组根目录的子目录路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.currentItemChanged.connect(self._on_item_changed)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._task_queue = None  # 由主窗口设置引用
        self._group_items = {}
        self._folder_items = {}
        self._task_items = {}
        self._task_objects = {}

    def set_task_queue(self, task_queue):
        """主窗口设置 task_queue 引用，用于反查任务与分组。"""
        self._task_queue = task_queue

    # ---------- 节点查找 ----------
    def _find_group_item(self, group_id: str):
        return self._group_items.get(group_id)

    def _find_task_item(self, task_id: str):
        return self._task_items.get(task_id)

    @staticmethod
    def _folder_key(group_id: str, relative_folder: str):
        return group_id, os.path.normcase(os.path.normpath(relative_folder))

    def _find_folder_item(self, group_id: str, relative_folder: str):
        return self._folder_items.get(self._folder_key(group_id, relative_folder))

    @staticmethod
    def _task_relative_folder(task: TaskInfo, group: TaskGroup) -> str:
        root = os.path.abspath(group.folder_path)
        source_dir = os.path.abspath(os.path.dirname(task.source_path))
        try:
            if os.path.normcase(os.path.commonpath([root, source_dir])) != os.path.normcase(root):
                return ""
            relative = os.path.relpath(source_dir, root)
            return "" if relative == "." else relative
        except ValueError:
            return ""

    def _top_level_insert_index(self, name: str) -> int:
        """顶层（组与散任务混排）按名称自然排序的插入位置。"""
        new_key = _natural_sort_key(name)
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            top_name = top.data(0, self.ROLE_NAME) or ""
            if _natural_sort_key(top_name) > new_key:
                return i
        return self.topLevelItemCount()

    def _child_insert_index(self, parent, name: str, kind: str) -> int:
        """目录优先，其次按名称自然排序。"""
        new_key = (0 if kind == "folder" else 1, _natural_sort_key(name))
        for i in range(parent.childCount()):
            child = parent.child(i)
            child_name = child.data(0, self.ROLE_NAME) or ""
            child_kind = child.data(0, self.ROLE_KIND)
            old_key = (0 if child_kind == "folder" else 1,
                       _natural_sort_key(child_name))
            if old_key > new_key:
                return i
        return parent.childCount()

    def _ensure_folder_item(self, group: TaskGroup, relative_folder: str):
        parent = self._find_group_item(group.group_id)
        current = ""
        for part in relative_folder.split(os.sep):
            current = os.path.join(current, part) if current else part
            folder_item = self._find_folder_item(group.group_id, current)
            if folder_item is None:
                folder_item = QTreeWidgetItem()
                folder_item.setData(0, self.ROLE_KIND, "folder")
                folder_item.setData(0, self.ROLE_ID, group.group_id)
                folder_item.setData(0, self.ROLE_NAME, part)
                folder_item.setData(0, self.ROLE_FOLDER_REL, current)
                folder_item.setText(0, f"0% ○ [目录] {part} (0/0)")
                parent.insertChild(self._child_insert_index(parent, part, "folder"), folder_item)
                self._folder_items[self._folder_key(group.group_id, current)] = folder_item
            parent = folder_item
        return parent

    # ---------- 添加节点 ----------
    def add_group_item(self, group: TaskGroup):
        item = QTreeWidgetItem()
        item.setData(0, self.ROLE_KIND, "group")
        item.setData(0, self.ROLE_ID, group.group_id)
        self._update_group_text(item, group)
        self.insertTopLevelItem(
            self._top_level_insert_index(group.group_name), item
        )
        self._group_items[group.group_id] = item
        self.expandItem(item)

    def add_task_item(self, task: TaskInfo):
        item = QTreeWidgetItem()
        item.setData(0, self.ROLE_KIND, "task")
        item.setData(0, self.ROLE_ID, task.task_id)
        self._update_item_text(item, task)
        self._task_objects[task.task_id] = task

        if task.group_id and self._task_queue:
            group = self._task_queue.get_group(task.group_id)
            group_item = self._find_group_item(task.group_id)
            if group and group_item:
                relative_folder = self._task_relative_folder(task, group)
                parent = (self._ensure_folder_item(group, relative_folder)
                          if relative_folder else group_item)
                parent.insertChild(
                    self._child_insert_index(parent, task.source_name, "task"), item
                )
                self._task_items[task.task_id] = item
                self._refresh_ancestors(parent)
                self.setCurrentItem(item)
                return

        # 散任务：顶层排序插入
        self.insertTopLevelItem(
            self._top_level_insert_index(task.source_name), item
        )
        self._task_items[task.task_id] = item
        self.setCurrentItem(item)

    # ---------- 更新节点 ----------
    def update_task_item(self, task: TaskInfo):
        item = self._find_task_item(task.task_id)
        if not item:
            return
        self._task_objects[task.task_id] = task
        self._update_item_text(item, task)
        self._refresh_ancestors(item.parent())

    def update_group_item(self, group: TaskGroup):
        item = self._find_group_item(group.group_id)
        if item:
            self._update_group_text(item, group)

    def remove_task_item(self, task_id: str):
        item = self._task_items.pop(task_id, None)
        self._task_objects.pop(task_id, None)
        if not item:
            return
        parent = item.parent()
        if parent:
            parent.takeChild(parent.indexOfChild(item))
            while (parent and parent.data(0, self.ROLE_KIND) == "folder"
                   and parent.childCount() == 0):
                grandparent = parent.parent()
                key = self._folder_key(parent.data(0, self.ROLE_ID),
                                       parent.data(0, self.ROLE_FOLDER_REL))
                self._folder_items.pop(key, None)
                grandparent.takeChild(grandparent.indexOfChild(parent))
                parent = grandparent
            self._refresh_ancestors(parent)
        else:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))

    def remove_group_item(self, group_id: str):
        item = self._group_items.pop(group_id, None)
        if item:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))
            self._folder_items = {
                key: value for key, value in self._folder_items.items()
                if key[0] != group_id
            }

    def select_group_item(self, group_id: str):
        item = self._find_group_item(group_id)
        if item:
            self.setCurrentItem(item)

    def select_folder_item(self, group_id: str, relative_folder: str):
        item = self._find_folder_item(group_id, relative_folder)
        if item:
            self.setCurrentItem(item)

    def _descendant_tasks(self, item):
        tasks = []
        for index in range(item.childCount()):
            child = item.child(index)
            if child.data(0, self.ROLE_KIND) == "task":
                task = self._task_objects.get(child.data(0, self.ROLE_ID))
                if task:
                    tasks.append(task)
            else:
                tasks.extend(self._descendant_tasks(child))
        return tasks

    def _refresh_ancestors(self, item):
        while item is not None and self._task_queue:
            kind = item.data(0, self.ROLE_KIND)
            group = self._task_queue.get_group(item.data(0, self.ROLE_ID))
            if kind == "folder" and group:
                self._update_folder_text(item, group)
            elif kind == "group" and group:
                self._update_group_text(item, group)
            item = item.parent()

    # ---------- 文本 ----------
    @staticmethod
    def _status_icon(status: str) -> str:
        return {"done": "✓", "running": "▶", "failed": "✗", "pending": "○"}.get(
            status, "○"
        )

    def _update_item_text(self, item: QTreeWidgetItem, task: TaskInfo):
        progress = int(task.overall_progress() * 100)
        icon = self._status_icon(self._overall_status(task))
        item.setText(0, f"{progress}% {icon} {task.source_name}")
        item.setData(0, self.ROLE_NAME, task.source_name)

    def _update_group_text(self, item: QTreeWidgetItem, group: TaskGroup):
        progress = int(group.progress() * 100)
        icon = self._status_icon(group.overall_status())
        done = group.done_count()
        total = len(group.tasks)
        item.setText(
            0,
            f"{progress}% {icon} [文件夹] {group.group_name} ({done}/{total})"
        )
        item.setData(0, self.ROLE_NAME, group.group_name)

    def _update_folder_text(self, item: QTreeWidgetItem, root_group: TaskGroup):
        tasks = self._descendant_tasks(item)
        name = os.path.basename(item.data(0, self.ROLE_FOLDER_REL))
        folder = TaskGroup(root_group.group_id, name, "", tasks)
        progress = int(folder.progress() * 100)
        icon = self._status_icon(folder.overall_status())
        item.setText(
            0, f"{progress}% {icon} [目录] {name} ({folder.done_count()}/{len(tasks)})"
        )

    @staticmethod
    def _overall_status(task: TaskInfo) -> str:
        if task.step3_status == STEP_DONE:
            return "done"
        if task.step1_status == STEP_FAILED or task.step2_status == STEP_FAILED or task.step3_status == STEP_FAILED:
            return "failed"
        if task.step1_status == STEP_RUNNING or task.step2_status == STEP_RUNNING or task.step3_status == STEP_RUNNING:
            return "running"
        return "pending"

    # ---------- 选中与右键 ----------
    def _on_item_changed(self, current, previous):
        if not current:
            return
        kind = current.data(0, self.ROLE_KIND)
        item_id = current.data(0, self.ROLE_ID)
        if kind == "task":
            self.task_selected.emit(item_id)
        elif kind == "group":
            self.group_selected.emit(item_id)
        elif kind == "folder":
            self.folder_selected.emit(item_id, current.data(0, self.ROLE_FOLDER_REL))

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        kind = item.data(0, self.ROLE_KIND)
        item_id = item.data(0, self.ROLE_ID)
        menu = QMenu(self)

        if kind == "group":
            act_remove = QAction("移除整个文件夹组", self)
            act_remove.triggered.connect(
                lambda: self.group_remove_requested.emit(item_id)
            )
            menu.addAction(act_remove)
            menu.addSeparator()
            for step, label in ((1, "步骤1(字幕)"), (2, "步骤2(配音)"), (3, "步骤3(混音)")):
                act = QAction(f"重跑组内全部 {label}", self)
                act.triggered.connect(
                    lambda checked=False, s=step, gid=item_id:
                        self.group_rerun_requested.emit(gid, s)
                )
                menu.addAction(act)
        elif kind == "folder":
            relative_folder = item.data(0, self.ROLE_FOLDER_REL)
            count = len(self._descendant_tasks(item))
            act_remove = QAction(f"移除该目录下全部 {count} 个任务", self)
            act_remove.triggered.connect(
                lambda: self.folder_remove_requested.emit(item_id, relative_folder)
            )
            menu.addAction(act_remove)
            menu.addSeparator()
            for step, label in ((1, "步骤1(字幕)"), (2, "步骤2(配音)"), (3, "步骤3(混音)")):
                act = QAction(f"重跑此目录全部 {label}", self)
                act.triggered.connect(
                    lambda checked=False, s=step, gid=item_id, rel=relative_folder:
                        self.folder_rerun_requested.emit(gid, rel, s)
                )
                menu.addAction(act)
        else:
            task_id = item_id
            task = self._task_queue.get_task(task_id) if self._task_queue else None
            if (task and task.step3_status in (STEP_DONE, STEP_SKIPPED)
                    and os.path.isfile(task.step3_output)):
                act_metadata = QAction("编辑混音成品信息…", self)
                act_metadata.triggered.connect(
                    lambda: self.metadata_edit_requested.emit(task_id)
                )
                menu.addAction(act_metadata)
                menu.addSeparator()
            act_remove = QAction("移除任务", self)
            act_remove.triggered.connect(
                lambda: self.task_remove_requested.emit(task_id)
            )
            menu.addAction(act_remove)
            menu.addSeparator()
            for step, label in ((1, "步骤1(字幕)"), (2, "步骤2(配音)"), (3, "步骤3(混音)")):
                act = QAction(f"重跑 {label}", self)
                act.triggered.connect(
                    lambda checked=False, s=step, tid=task_id:
                        self.task_rerun_requested.emit(tid, s)
                )
                menu.addAction(act)
        menu.exec(self.viewport().mapToGlobal(pos))

    # 拖拽支持
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        files = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                files.append(path)
        if files:
            # 主窗口根据文件类型分发：视频→新任务，字幕→跳过步骤1，wav/目录→跳过步骤2
            self.window().on_files_dropped(files)
        event.acceptProposedAction()
