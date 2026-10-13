# -*- coding: utf-8 -*-
"""工作流主窗口：声笺风格工作台（侧栏 + 中央内容）。

布局遵循 UI_STYLE.md：
    ┌──────────┬──────────────────────────────┐
    │ 品牌     │ 当前条目（紧凑标题行）        │
    │ 添加入口 │ 工具栏（单行按钮）            │
    │ 任务队列 │ 步骤卡片 / 内容列表          │
    │          │ 运行日志（自适应铺满剩余高度）│
    └──────────┴──────────────────────────────┘

底部操作栏已在 v2.2.3 按反馈移除：当前任务在标题行、进度在步骤卡、停止在工具栏，
底栏信息全部重复。

由 workflow_gui.py 拆分为包结构：本模块定义主窗口类与界面骨架，
各功能方法按职责拆分到 mixin 模块（工具栏/配置/批量/任务/步骤/重命名）。
"""
import os
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QScrollArea, QTextEdit, QVBoxLayout, QWidget
)

from ..config import WorkflowConfig
from ..ui_theme import LOG_COLORS, SIDEBAR_PADDING, SIDEBAR_WIDTH, WheelScrollGuard
from ..version import APP_VERSION
from ..task_manager import TaskQueue, TaskGroup
from ..widgets import StepPanel, TaskListWidget, ToastPopup
from ..widgets.common import ElidedLabel
from .toolbar_mixin import ToolbarMixin
from .config_mixin import ConfigDialogMixin
from .batch_mixin import BatchMixin
from .task_mixin import TaskMixin
from .step_mixin import StepMixin
from .rename_mixin import RenameMixin


class WorkflowMainWindow(
    ToolbarMixin, ConfigDialogMixin, BatchMixin, TaskMixin, StepMixin, RenameMixin,
    QMainWindow
):
    """工作流主窗口（声笺风格工作台）。"""

    def __init__(self, config: WorkflowConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle(f"双语音声工作流 {APP_VERSION}")
        self.resize(1380, 900)
        self.setMinimumSize(1080, 720)

        # 任务队列
        self.task_queue = TaskQueue(config.workspace_dir, config.tts_cfg)
        # 当前活动的 worker（按步骤号 1/2/3 索引）
        self._workers = {1: None, 2: None, 3: None}
        # TTS 总任务数（用于进度计算）
        self._tts_total = 0
        # 批量执行器
        self._batch_executor = None
        # 当前选中的文件夹组（选中组节点时非 None）
        self._current_group: TaskGroup = None
        self._selected_folder = None  # (group_id, 相对子目录) 或 None
        # 滚轮守卫：数值框/下拉/滑条上的滚轮滚动页面，不误改参数
        self._wheel_guard = WheelScrollGuard(self)

        self._build_ui()
        self._connect_signals()
        restored, ignored = self.task_queue.restore_tasks()
        if restored or ignored:
            self._append_log(f"[任务恢复] 恢复 {restored} 个任务，忽略 {ignored} 条无效记录")

    # ========== 界面骨架 ==========
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("shell")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_center(), 1)
        outer.addLayout(body, 1)

    # ---------- 左侧：品牌 + 导航 + 任务队列 ----------
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(SIDEBAR_PADDING, 16, SIDEBAR_PADDING, 12)
        layout.setSpacing(6)

        brand = QLabel("双语音声工作流")
        brand.setProperty("role", "brand")
        layout.addWidget(brand)
        version = QLabel(f"{APP_VERSION} · 三步配音工作流")
        version.setProperty("role", "muted")
        layout.addWidget(version)

        layout.addSpacing(6)
        # 统一添加入口：文件与文件夹合并成一个按钮，点开先选类型再浏览
        self.add_media_btn = QPushButton("＋ 添加素材")
        self.add_media_btn.setProperty("kind", "primary")
        self.add_media_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_media_btn.setToolTip(
            "添加视频/音频文件，或整个文件夹（文件夹会按同名规则自动配对字幕）")
        self.add_media_btn.clicked.connect(self._on_add_media)
        layout.addWidget(self.add_media_btn)

        layout.addSpacing(10)
        nav = QLabel("任务队列")
        nav.setProperty("role", "muted")
        layout.addWidget(nav)
        layout.addSpacing(2)

        self.task_list = TaskListWidget()
        self.task_list.set_task_queue(self.task_queue)
        layout.addWidget(self.task_list, 1)

        hint = QLabel("可拖入视频/音频或文件夹")
        hint.setProperty("role", "muted")
        hint.setWordWrap(False)
        layout.addWidget(hint)

        layout.addSpacing(4)
        self.workspace_label = ElidedLabel()
        self.workspace_label.setProperty("role", "muted")
        self.workspace_label.set_elide_mode(Qt.TextElideMode.ElideMiddle)
        workspace = os.path.basename(self.config.workspace_dir.rstrip("/\\")) or "—"
        self.workspace_label.setText("工作区：" + workspace)
        layout.addWidget(self.workspace_label)

        # 侧栏中的数值控件（无）也纳入滚轮守卫管辖范围
        self._wheel_guard.guard(sidebar)
        return sidebar

    # ---------- 中央：当前条目标题 + 工具栏 + 步骤卡片 + 日志 ----------
    def _build_center(self) -> QWidget:
        center = QWidget()
        center.setObjectName("centerPane")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(20, 14, 20, 10)
        layout.setSpacing(10)

        # 标题行：当前条目名称（14px）+ 右对齐来源（12px，中间省略 + 全路径 tooltip）
        header = QHBoxLayout()
        header.setSpacing(10)
        self.current_task_label = QLabel("未选择任务")
        self.current_task_label.setProperty("role", "section")
        self.current_task_label.setMinimumWidth(1)
        header.addWidget(self.current_task_label, 0)
        self.current_task_subtitle = ElidedLabel(
            "选择左侧任务，或添加视频/音频文件开始")
        self.current_task_subtitle.setProperty("role", "muted")
        self.current_task_subtitle.set_elide_mode(Qt.TextElideMode.ElideMiddle)
        self.current_task_subtitle.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.current_task_subtitle, 1)
        layout.addLayout(header)

        # 工具栏：单行按钮（旧版形态：导入区 + 条目级操作 + 执行区）
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        for button in self._build_toolbar_buttons():
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        # 滚动内容区：步骤卡片 + 日志卡片（页面整体滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("centerScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 2, 0, 2)
        content_layout.setSpacing(10)

        # 三个步骤面板（白色卡片）
        self.step_panels = {}
        step_titles = {1: "字幕提取", 2: "语音生成", 3: "音频混音"}
        for step in (1, 2, 3):
            panel = StepPanel(step, step_titles[step])
            panel.start_requested.connect(self._on_start_step)
            panel.stop_requested.connect(self._on_stop_step)
            content_layout.addWidget(panel)
            self.step_panels[step] = panel

        # 条目级入口下沉到对应步骤卡：添加字幕 = 跳过步骤1，添加配音 = 跳过步骤2
        self.add_subtitle_btn = self.step_panels[1].add_extra_button(
            "＋ 添加字幕", kind=None)
        self.add_subtitle_btn.setToolTip("为当前任务添加字幕文件（跳过字幕提取）")
        self.add_subtitle_btn.clicked.connect(self._on_add_subtitle)

        self.add_mix_btn = self.step_panels[2].add_extra_button(
            "＋ 添加配音", kind=None)
        self.add_mix_btn.setToolTip("为当前任务添加配音目录（跳过语音生成）")
        self.add_mix_btn.clicked.connect(self._on_add_mix_folder)

        # 各步骤卡的配置入口
        whisper_config_btn = self.step_panels[1].add_extra_button("字幕配置…")
        whisper_config_btn.setToolTip("设置设备、VAD、字幕格式、段落合并等参数")
        whisper_config_btn.clicked.connect(self._on_open_whisper_config)
        tts_config_btn = self.step_panels[2].add_extra_button("TTS 配置…")
        tts_config_btn.setToolTip("设置语音合成模式（Edge/API）、声音、语速等参数")
        tts_config_btn.clicked.connect(self._on_open_tts_config)
        mixer_config_btn = self.step_panels[3].add_extra_button("混音配置…")
        mixer_config_btn.setToolTip("设置导出格式、音量、声道检测等参数")
        mixer_config_btn.clicked.connect(self._on_open_mixer_config)

        # 日志卡给 1 权重：吸收中央区剩余高度，避免底部留下大片空白
        content_layout.addWidget(self._build_log_card(), 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self._refresh_entry_buttons()
        return center

    def _build_toolbar_buttons(self) -> list:
        """工具栏按钮：工作区/配置 + 执行区（主按钮唯一）。

        添加媒体/文件夹已合并为侧栏的「＋ 添加素材」；添加字幕/配音已下沉到
        对应的步骤卡（见 _build_center）。
        """
        divider = QWidget()
        divider.setObjectName("toolbarDivider")
        divider.setFixedWidth(1)
        divider.setFixedHeight(22)

        open_ws_btn = QPushButton("打开工作区")
        open_ws_btn.setProperty("kind", "text")
        open_ws_btn.clicked.connect(self._on_open_workspace)

        config_btn = QPushButton("配置")
        config_btn.setProperty("kind", "text")
        config_btn.setToolTip("全局配置：字幕提取路径、工作区与各步骤参数")
        config_btn.clicked.connect(self._on_config)

        self.batch_btn = QPushButton("批量执行")
        self.batch_btn.setProperty("kind", "primary")
        self.batch_btn.setToolTip("自动遍历所有任务，按选定步骤批量执行")
        self.batch_btn.clicked.connect(self._on_batch_execute)

        self.batch_stop_btn = QPushButton("停止批量")
        self.batch_stop_btn.setToolTip("停止正在进行的批量执行")
        self.batch_stop_btn.setEnabled(False)
        self.batch_stop_btn.clicked.connect(self._on_batch_stop)

        return [open_ws_btn, config_btn, divider,
                self.batch_btn, self.batch_stop_btn]

    def _build_log_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("logCard")
        card.setProperty("card", "true")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("运行日志")
        title.setProperty("role", "section")
        header.addWidget(title)
        header.addStretch()
        self.log_toggle_btn = QPushButton("收起")
        self.log_toggle_btn.setProperty("kind", "text")
        self.log_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_toggle_btn.clicked.connect(self._on_toggle_log)
        header.addWidget(self.log_toggle_btn)
        layout.addLayout(header)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        # 只设下限：日志卡在外层拿到 1 权重，QTextEdit 纵向 Expanding 会吸收
        # 中央区剩余高度，不再出现"卡片下方一整片空白"
        self.log_view.setMinimumHeight(120)
        # 最多保留 5000 行，超出自动丢弃最旧日志，防止长时间运行无限增长
        self.log_view.document().setMaximumBlockCount(5000)
        layout.addWidget(self.log_view)
        return card

    def _on_toggle_log(self):
        visible = self.log_view.isVisible()
        self.log_view.setVisible(not visible)
        self.log_toggle_btn.setText("展开" if visible else "收起")

    # ---------- 统一添加入口 ----------
    def _on_add_media(self):
        """「＋ 添加素材」：先选文件还是文件夹，再进对应的浏览对话框。"""
        dialog = AddMediaDialog(self)
        choice = dialog.choose()
        if choice == AddMediaDialog.FILES:
            self._on_add_files()
        elif choice == AddMediaDialog.FOLDER:
            self._on_add_folder()

    def _connect_signals(self):
        self.task_list.task_selected.connect(self._on_task_selected)
        self.task_list.task_remove_requested.connect(self._on_task_remove)
        self.task_list.task_rerun_requested.connect(self._on_task_rerun)
        self.task_list.metadata_edit_requested.connect(self._on_edit_metadata)
        self.task_list.group_selected.connect(self._on_group_selected)
        self.task_list.group_remove_requested.connect(self._on_group_remove)
        self.task_list.group_rerun_requested.connect(self._on_group_rerun)
        self.task_list.folder_selected.connect(self._on_folder_selected)
        self.task_list.folder_remove_requested.connect(self._on_folder_remove)
        self.task_list.folder_rerun_requested.connect(self._on_folder_rerun)
        self.task_queue.task_added.connect(self.task_list.add_task_item)
        self.task_queue.task_removed.connect(self.task_list.remove_task_item)
        self.task_queue.task_removed.connect(self._on_task_removed)
        self.task_queue.task_updated.connect(self._on_task_updated)
        self.task_queue.current_changed.connect(self._on_current_changed)
        self.task_queue.group_added.connect(self.task_list.add_group_item)
        self.task_queue.group_removed.connect(self.task_list.remove_group_item)

    # ========== 浮动提示 ==========
    def _show_toast(self, message: str):
        """轻量操作结果的短暂浮动提示（不打断当前操作）。"""
        toast = getattr(self, "_toast", None)
        if toast is None:
            toast = ToastPopup(self.centralWidget())
            self._toast = toast
        toast.show_message(message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        toast = getattr(self, "_toast", None)
        if toast is not None and toast.isVisible():
            toast._reposition()

    # ========== 日志 ==========
    def _append_log(self, msg: str):
        """追加一条日志：带时间戳，按消息特征着色（成功绿/失败红/警告橙）。"""
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"

        # 按内容特征推断级别；颜色集中定义在 ui_theme.LOG_COLORS
        level = "info"
        low = msg.lower()
        if any(k in low for k in ("失败", "错误", "异常", "中止", "不存在", "error", "failed")):
            level = "error"
        elif any(k in low for k in ("警告", "warn")):
            level = "warn"
        elif any(k in low for k in ("完成", "成功", "done", "success")):
            level = "success"

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(LOG_COLORS[level]))
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)
        self.log_view.setTextCursor(cursor)
        # 自动滚动到底部
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())


class AddMediaDialog(QDialog):
    """统一添加入口：先选「文件」还是「文件夹」，再进对应浏览对话框。

    点任一选项即关闭本窗；调用方用 :meth:`choose` 取值。取值刻意避开
    ``QDialog.DialogCode``（Accepted=1 / Rejected=0），以免语义混淆。
    """

    FILES = 10
    FOLDER = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加素材")
        self.setObjectName("addMediaDialog")
        self.result_choice = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QLabel("添加素材")
        title.setProperty("role", "section")
        layout.addWidget(title)
        hint = QLabel("选择要导入的类型，接下来会打开对应的浏览窗口。")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(4)

        self.files_btn = QPushButton(
            "视频 / 音频文件\n导入单个或多个媒体，自动识别同名字幕")
        self.files_btn.setProperty("kind", "option")
        self.files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.files_btn.clicked.connect(lambda: self._choose(self.FILES))
        layout.addWidget(self.files_btn)

        self.folder_btn = QPushButton(
            "文件夹\n按目录批量导入，自动配对音频与字幕")
        self.folder_btn.setProperty("kind", "option")
        self.folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_btn.clicked.connect(lambda: self._choose(self.FOLDER))
        layout.addWidget(self.folder_btn)

        layout.addSpacing(4)
        cancel_btn = QPushButton("取消")
        cancel_btn.setProperty("kind", "text")
        cancel_btn.clicked.connect(self.reject)
        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        cancel_row.addWidget(cancel_btn)
        layout.addLayout(cancel_row)

        self.setMinimumWidth(360)

    def _choose(self, choice: int):
        self.result_choice = choice
        self.accept()

    def choose(self) -> int:
        """模态显示，返回 FILES / FOLDER / 0（取消）。"""
        super().exec()
        return self.result_choice
