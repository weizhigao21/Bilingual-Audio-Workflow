# -*- coding: utf-8 -*-
"""左侧任务列表（树形）组件。"""
from PyQt6.QtWidgets import (
    QTreeWidget, QTreeWidgetItem, QMenu, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction

from ..task_manager import TaskInfo, TaskGroup, STEP_DONE, STEP_FAILED, STEP_RUNNING
from .common import _natural_sort_key


class TaskListWidget(QTreeWidget):
    """左侧任务列表（树形）。

    两层结构：
      - 顶层：文件夹任务组（TaskGroup）节点 与 散任务（未分组）节点
      - 子层：组内的音频文件任务（TaskInfo）
    支持拖拽添加视频/字幕/配音文件。
    """
    task_selected = pyqtSignal(str)   # task_id
    task_remove_requested = pyqtSignal(str)
    task_rerun_requested = pyqtSignal(str, int)  # (task_id, step)
    group_selected = pyqtSignal(str)  # group_id
    group_remove_requested = pyqtSignal(str)
    group_rerun_requested = pyqtSignal(str, int)  # (group_id, step)

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.currentItemChanged.connect(self._on_item_changed)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._task_queue = None  # 由主窗口设置引用

    def set_task_queue(self, task_queue):
        """主窗口设置 task_queue 引用，用于反查任务与分组。"""
        self._task_queue = task_queue

    # ---------- 节点查找 ----------
    def _find_group_item(self, group_id: str):
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if (item.data(0, self.ROLE_KIND) == "group"
                    and item.data(0, self.ROLE_ID) == group_id):
                return item
        return None

    def _find_task_item(self, task_id: str):
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            if (top.data(0, self.ROLE_KIND) == "task"
                    and top.data(0, self.ROLE_ID) == task_id):
                return top
            if top.data(0, self.ROLE_KIND) == "group":
                for j in range(top.childCount()):
                    child = top.child(j)
                    if (child.data(0, self.ROLE_KIND) == "task"
                            and child.data(0, self.ROLE_ID) == task_id):
                        return child
        return None

    def _top_level_insert_index(self, name: str) -> int:
        """顶层（组与散任务混排）按名称自然排序的插入位置。"""
        new_key = _natural_sort_key(name)
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            top_name = top.data(0, self.ROLE_NAME) or ""
            if _natural_sort_key(top_name) > new_key:
                return i
        return self.topLevelItemCount()

    def _child_insert_index(self, group_item, name: str) -> int:
        """组内按名称自然排序的插入位置。"""
        new_key = _natural_sort_key(name)
        for i in range(group_item.childCount()):
            child = group_item.child(i)
            child_name = child.data(0, self.ROLE_NAME) or ""
            if _natural_sort_key(child_name) > new_key:
                return i
        return group_item.childCount()

    # ---------- 添加节点 ----------
    def add_group_item(self, group: TaskGroup):
        item = QTreeWidgetItem()
        item.setData(0, self.ROLE_KIND, "group")
        item.setData(0, self.ROLE_ID, group.group_id)
        self._update_group_text(item, group)
        self.insertTopLevelItem(
            self._top_level_insert_index(group.group_name), item
        )
        self.expandItem(item)

    def add_task_item(self, task: TaskInfo):
        item = QTreeWidgetItem()
        item.setData(0, self.ROLE_KIND, "task")
        item.setData(0, self.ROLE_ID, task.task_id)
        self._update_item_text(item, task)

        if task.group_id and self._task_queue:
            group_item = self._find_group_item(task.group_id)
            if group_item:
                group_item.insertChild(
                    self._child_insert_index(group_item, task.source_name), item
                )
                group = self._task_queue.get_group(task.group_id)
                if group:
                    self._update_group_text(group_item, group)
                self.setCurrentItem(item)
                return

        # 散任务：顶层排序插入
        self.insertTopLevelItem(
            self._top_level_insert_index(task.source_name), item
        )
        self.setCurrentItem(item)

    # ---------- 更新节点 ----------
    def update_task_item(self, task: TaskInfo):
        item = self._find_task_item(task.task_id)
        if not item:
            return
        self._update_item_text(item, task)
        # 同步刷新所属组的汇总文本
        if task.group_id and self._task_queue:
            group = self._task_queue.get_group(task.group_id)
            group_item = self._find_group_item(task.group_id)
            if group and group_item:
                self._update_group_text(group_item, group)

    def update_group_item(self, group: TaskGroup):
        item = self._find_group_item(group.group_id)
        if item:
            self._update_group_text(item, group)

    def remove_task_item(self, task_id: str):
        item = self._find_task_item(task_id)
        if not item:
            return
        parent = item.parent()
        if parent:
            parent.takeChild(parent.indexOfChild(item))
            # 组内任务被移除后刷新组节点文本
            if self._task_queue:
                gid = parent.data(0, self.ROLE_ID)
                group = self._task_queue.get_group(gid) if gid else None
                if group:
                    self._update_group_text(parent, group)
        else:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))

    def remove_group_item(self, group_id: str):
        item = self._find_group_item(group_id)
        if item:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))

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
        else:
            task_id = item_id
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
