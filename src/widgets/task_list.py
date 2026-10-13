# -*- coding: utf-8 -*-
"""左侧任务列表（树形）组件（声笺风格紧凑两行行）。"""
import os
from PyQt6.QtCore import QPointF, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QStyledItemDelegate, QStyle, QTreeWidget, QTreeWidgetItem, QMenu,
    QAbstractItemView
)

from ..task_manager import (
    TaskInfo, TaskGroup, STEP_DONE, STEP_FAILED, STEP_RUNNING, STEP_SKIPPED,
)
from ..ui_theme import PALETTE
from .common import _natural_sort_key, STATUS_TEXT

# 状态圆点颜色（取自主题色板，避免同一语义出现两种绿/橙）
_STATUS_DOT_COLORS = {
    "pending": "#c9c9cc",
    "running": PALETTE["accent"],
    "done": PALETTE["ready"],
    "failed": PALETTE["error"],
    "skipped": "#85858a",
}
_ROW_HEIGHT = 50          # 紧凑列表行高（UI_STYLE：模型/音频列表行高基准）
_DOT_SIZE = 8
_TEXT_COLOR = QColor("#18181a")
_SUBTITLE_COLOR = QColor("#6b6b6e")
_META_COLOR = QColor("#85858a")
_SELECTED_BG = QColor("#f1f1f1")
_HOVER_BG = QColor("#fafafa")
# 层级连接线（├ / └ 肘线 + 祖先竖线）与展开箭头
_GUIDE_COLOR = QColor("#d2d2d7")   # 原 #e4e4e7 太淡，看不出从属关系
_CHEVRON_COLOR = QColor("#a2a2a8")
_CHEVRON_SIZE = 8


class TaskRowDelegate(QStyledItemDelegate):
    """两行紧凑行：名称（13px）+ 状态说明（12px），左侧状态点，右侧进度。

    数据来自行的自定义角色；文本保持 item.text(0) 旧格式不变
    （回归测试与树逻辑依赖），渲染完全由本委托负责。

    缩进语义（Qt 实测）：option.rect.left() 已含 indentation×(depth+1)
    的层级缩进，委托绝不能再手工加缩进（会双重缩进）。缩进导引线与
    展开箭头由本委托按 depth 绘制，原生 drawBranches 已在
    TaskListWidget 中置空。选中/悬停背景铺满整行（含缩进区），
    避免原生选中色在分支区残留。
    """

    def __init__(self, tree: "TaskListWidget", parent=None):
        super().__init__(parent)
        self.tree = tree

    def sizeHint(self, option, index):
        return QSize(-1, _ROW_HEIGHT)

    @staticmethod
    def _depth(index) -> int:
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()
        return depth

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect
        # 背景铺满整行（x=0 起，覆盖缩进导引区）
        if selected or hovered:
            bg = _SELECTED_BG if selected else _HOVER_BG
            painter.fillRect(
                QRect(0, rect.top(), self.tree.viewport().width(), rect.height()),
                bg)

        item = self.tree.itemFromIndex(index) if self.tree else None
        if item is None:
            painter.restore()
            return

        indent = self.tree.indentation()
        depth = self._depth(index)
        left = rect.left()          # 已含层级缩进，直接作为内容起点
        right = rect.right() - 10
        top = rect.top() + 7

        self._draw_structure(painter, rect, item, depth, indent, left)

        name = item.data(0, self.tree.ROLE_NAME) or item.text(0)
        subtitle = item.data(0, self.tree.ROLE_SUBTITLE) or ""
        status = item.data(0, self.tree.ROLE_STATUS) or "pending"
        progress = item.data(0, self.tree.ROLE_PROGRESS)

        # 状态点
        dot_color = QColor(_STATUS_DOT_COLORS.get(status, "#c9c9cc"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)
        dot_y = top + 4
        painter.drawEllipse(left, dot_y, _DOT_SIZE, _DOT_SIZE)
        text_left = left + _DOT_SIZE + 8

        # 名称（第一行）
        name_font = QFont(option.font)
        name_font.setPixelSize(13)
        painter.setFont(name_font)
        painter.setPen(_TEXT_COLOR)
        name_rect = QRect(text_left, top, right - text_left - 48, 20)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(
                             name, Qt.TextElideMode.ElideRight,
                             name_rect.width()))

        # 状态说明（第二行）
        if subtitle:
            sub_font = QFont(option.font)
            sub_font.setPixelSize(12)
            painter.setFont(sub_font)
            painter.setPen(_SUBTITLE_COLOR)
            sub_rect = QRect(text_left, top + 20, right - text_left - 48, 16)
            painter.drawText(sub_rect, Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter,
                             option.fontMetrics.elidedText(
                                 subtitle, Qt.TextElideMode.ElideRight,
                                 sub_rect.width()))

        # 右侧进度
        if progress is not None:
            meta_font = QFont(option.font)
            meta_font.setPixelSize(12)
            painter.setFont(meta_font)
            painter.setPen(_META_COLOR)
            painter.drawText(QRect(right - 44, top, 44, 36),
                             Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter,
                             f"{progress}%")
        painter.restore()

    @staticmethod
    def _is_last_child(item) -> bool:
        """是否为同级最后一个子节点（决定该画 ├ 还是 └）。"""
        parent = item.parent()
        if parent is None:
            return False
        return parent.indexOfChild(item) == parent.childCount() - 1

    def _draw_structure(self, painter, rect, item, depth: int,
                        indent: int, content_left: int):
        """绘制层级连接线（Qt 语义的 ├ / └ + 祖先竖线）与展开箭头。

        与 QTreeView 原生 drawBranch 的语义一致：

        - **自身列**（第 ``depth-1`` 列，即 ``content_left - indent/2``）：
          末子画 └（竖线止于肘点），非末子画 ├（竖线贯穿整行，与下一行
          接续）；再从肘点向右引一条横线连到节点——这就是"级下的文件
          用一条横线挂到竖线上"的那条横线。横线终点按本行有无展开箭头
          分两种：有箭头时止于箭头左缘（横线画到底会从箭头右侧露出一
          截 5px 线头，截图反馈过），无箭头时接到内容起点；
        - **祖先列**：仅当本行在该列上的祖先"还有后续兄弟"时才续画竖线
          （即 Qt 的 ``State_Sibling``）；末子之后不再有线，否则会出现
          一条挂不到任何节点的悬空长线；
        - **父节点的下垂线**：有子节点且已展开的节点，从自身中心向下补
          一段竖线。子级各行的竖线因此首尾相接，形成从父节点中心一路
          到末子中心的一条连续直线，而不是每行一小段（旧实现只画半行，
          看着像断开的虚线）。

        缩进语义：``content_left`` 即 ``option.rect.left()``，已含
        ``indentation×(depth+1)``，这里绝不能再叠加缩进。
        """
        if depth > 0:
            pen = QPen(_GUIDE_COLOR, 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            cy = rect.center().y()          # 与 Qt 的 QRect.center() 对齐
            # chain[i] 的深度为 depth-i：chain[0] 是本行，之后逐级向上
            chain = [item]
            node = item
            while node.parent() is not None:
                node = node.parent()
                chain.append(node)

            # 横线终点：本行要画展开箭头时止于箭头左缘。箭头画在
            # content_left - indent/2 处、横跨 ±_CHEVRON_SIZE/2，横线若
            # 一路画到 content_left-2，末端就会从箭头右侧露出一截线头
            # （截图反馈的多余线头就是这个）；无箭头时直接接到内容起点。
            if item.childCount() > 0:
                line_end = content_left - indent // 2 - _CHEVRON_SIZE // 4
            else:
                line_end = content_left - 2

            for level in range(depth):
                x = level * indent + indent // 2
                # 第 level 列服务"深度 level 的节点"的子级，其下挂在
                # 本行这一支的节点是 chain[depth-level-1]
                child = chain[depth - level - 1]
                is_last = self._is_last_child(child)
                if level == depth - 1:
                    # 自身列：末子 └ 止于肘点，非末子 ├ 贯穿整行
                    painter.drawLine(
                        x, rect.top(),
                        x, cy if is_last else rect.bottom() + 1)
                    painter.drawLine(x, cy, line_end, cy)
                elif not is_last:
                    # 祖先列：只有"后面还有兄弟"时才需要继续延伸
                    painter.drawLine(x, rect.top(), x, rect.bottom() + 1)

        if item.childCount() > 0:
            cx = content_left - indent // 2
            cy = rect.center().y()
            half = _CHEVRON_SIZE // 2
            expanded = self.tree.isExpanded(self.tree.indexFromItem(item))
            if expanded:
                # 下垂线：从箭头尖端接到行底，与子级竖线连成一条直线
                painter.setPen(QPen(_GUIDE_COLOR, 1))
                painter.drawLine(cx, cy + half, cx, rect.bottom() + 1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_CHEVRON_COLOR)
            points = []
            if expanded:
                points = [QPointF(cx - half, cy - half / 2),
                          QPointF(cx + half, cy - half / 2),
                          QPointF(cx, cy + half)]
            else:
                points = [QPointF(cx - half / 2, cy - half),
                          QPointF(cx + half / 2, cy),
                          QPointF(cx - half / 2, cy + half)]
            painter.drawPolygon(QPolygonF(points))


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
    ROLE_STATUS = Qt.ItemDataRole.UserRole + 4    # 状态键（委托画状态点）
    ROLE_SUBTITLE = Qt.ItemDataRole.UserRole + 5  # 第二行说明文字
    ROLE_PROGRESS = Qt.ItemDataRole.UserRole + 6  # 右侧进度百分比

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setIndentation(14)
        self.setUniformRowHeights(True)
        self.setMouseTracking(True)
        self.setItemDelegate(TaskRowDelegate(self))
        self.currentItemChanged.connect(self._on_item_changed)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._task_queue = None  # 由主窗口设置引用
        self._group_items = {}
        self._folder_items = {}
        self._task_items = {}
        self._task_objects = {}

    def drawBranches(self, painter, rect, index):
        """禁用原生分支绘制：导引线与展开箭头由 TaskRowDelegate 统一绘制。

        原生绘制会把默认雪佛龙画在分支列，与委托自绘的箭头重叠；
        且原生选中色只覆盖分支区外的行内，置空后由委托铺满整行。
        注意：只置空"绘制"，Qt 原生的分支区点击命中（点击自身分支列
        切换展开/收起且不改选中）不受影响，无需自行实现 mousePressEvent。
        """
        return

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
                folder_item.setData(0, self.ROLE_STATUS, "pending")
                folder_item.setData(0, self.ROLE_SUBTITLE, "")
                folder_item.setData(0, self.ROLE_PROGRESS, 0)
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
        status = self._overall_status(task)
        icon = self._status_icon(status)
        # text(0) 保持旧格式（树逻辑与回归测试依赖），渲染由委托按角色绘制
        item.setText(0, f"{progress}% {icon} {task.source_name}")
        item.setData(0, self.ROLE_NAME, task.source_name)
        item.setData(0, self.ROLE_STATUS, status)
        item.setData(0, self.ROLE_SUBTITLE, self._task_subtitle(task))
        item.setData(0, self.ROLE_PROGRESS, progress)
        # 侧栏 208px 第二行只放得下状态词；步骤明细与完整路径进悬停提示
        item.setToolTip(0, f"{task.source_name}\n{STATUS_TEXT.get(status, '')} · "
                           f"步骤 {' '.join(self._step_marks(task))}"
                           f"{self._error_hint(task)}\n{task.source_path}")

    @staticmethod
    def _count_subtitle(done: int, total: int) -> str:
        """组/目录第二行：紧凑计数 done/total。

        原格式 "N 项 · M 完成" 实测 120–156px，连最上层 95px 都放不下、
        必然被省略；压缩成 "5/12"（48–60px）后各层级都能完整显示，
        "N 项 · M 完成" 的完整说法保留在悬停提示里。
        """
        return f"{done}/{total}"

    @staticmethod
    def _step_marks(task: TaskInfo) -> list:
        """三步状态标记（✓ 完成 / – 跳过 / ✗ 失败 / ▶ 进行中 / ○ 等待）。"""
        marks = {"done": "✓", "skipped": "–", "failed": "✗", "running": "▶"}
        return [
            f"{step}{marks.get(task.step_status(step), '○')}"
            for step in (1, 2, 3)
        ]

    def _task_subtitle(self, task: TaskInfo) -> str:
        """第二行：仅状态词，不放步骤号。

        208px 栏宽下第二行可用宽度实测为 95/81/67px（层级 0/1/2，
        indentation=14，右侧为进度预留 48px）。"进行中 · 步骤2" 实测
        108px、"失败·步骤2" 72px，均超过最深层 67px 会被省略号截断；
        而层级更深的目录下任务还会更窄。因此这里只留状态词（最宽
        "等待中/进行中/已完成/已跳过" 36px），定位到的步骤与三步标记
        统一放进悬停提示（见 _update_item_text / _step_marks）。
        """
        return STATUS_TEXT.get(self._overall_status(task), "")

    @staticmethod
    def _error_hint(task: TaskInfo) -> str:
        """失败原因（首行、截断）附在 tooltip 中，便于快速定位。"""
        for step in (1, 2, 3):
            if task.step_status(step) == STEP_FAILED:
                error = getattr(task, f"step{step}_error", "")
                if error:
                    return "\n" + error.splitlines()[0][:120]
        return ""

    def _update_group_text(self, item: QTreeWidgetItem, group: TaskGroup):
        progress = int(group.progress() * 100)
        status = group.overall_status()
        icon = self._status_icon(status)
        done = group.done_count()
        total = len(group.tasks)
        item.setText(
            0,
            f"{progress}% {icon} [文件夹] {group.group_name} ({done}/{total})"
        )
        item.setData(0, self.ROLE_NAME, group.group_name)
        item.setData(0, self.ROLE_STATUS, status)
        item.setData(0, self.ROLE_SUBTITLE, self._count_subtitle(done, total))
        item.setData(0, self.ROLE_PROGRESS, progress)
        item.setToolTip(0, f"{group.group_name}\n{STATUS_TEXT.get(status, '')} · "
                           f"{total} 项 · {done} 完成\n{group.folder_path}")

    def _update_folder_text(self, item: QTreeWidgetItem, root_group: TaskGroup):
        tasks = self._descendant_tasks(item)
        name = os.path.basename(item.data(0, self.ROLE_FOLDER_REL))
        folder = TaskGroup(root_group.group_id, name, "", tasks)
        progress = int(folder.progress() * 100)
        status = folder.overall_status()
        icon = self._status_icon(status)
        done = folder.done_count()
        item.setText(
            0, f"{progress}% {icon} [目录] {name} ({done}/{len(tasks)})"
        )
        item.setData(0, self.ROLE_NAME, name)
        item.setData(0, self.ROLE_STATUS, status)
        item.setData(0, self.ROLE_SUBTITLE, self._count_subtitle(done, len(tasks)))
        item.setData(0, self.ROLE_PROGRESS, progress)
        item.setToolTip(0, f"{name}\n{STATUS_TEXT.get(status, '')} · "
                           f"{len(tasks)} 项 · {done} 完成")

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
