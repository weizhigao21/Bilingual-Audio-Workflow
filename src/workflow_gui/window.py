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
import threading
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTextEdit, QVBoxLayout, QWidget
)

from ..config import WorkflowConfig
from ..env_check import collect_warnings
from ..ui_theme import (LOG_COLORS, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH,
                        SIDEBAR_PADDING, SIDEBAR_WIDTH, WheelScrollGuard)
from ..version import APP_VERSION
from ..task_manager import TaskQueue, TaskGroup
from ..widgets import StepPanel, TaskListWidget, ToastPopup
from ..widgets.common import ElidedLabel
from ..widgets.settings_group import SegmentedControl
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

    # 环境自检结果从后台线程回主线程的通道（P1-4）
    _env_check_done = pyqtSignal(str)

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
        # 恢复后按真实队列重置空态与主按钮（启动时队列还是空的）
        self._on_current_changed(self.task_queue.current)
        self._update_queue_count()

        # 启动环境自检：后台轻量检查（P1-4），异常才提示，全部正常保持沉默。
        # 结果经 Qt 信号回主线程再碰 UI；自检绝不阻断启动。
        self._env_check_done.connect(self._append_log)
        threading.Thread(target=self._run_env_check, daemon=True).start()

    def _run_env_check(self):
        """后台线程：收集环境警告并一次性提示。"""
        try:
            warnings = collect_warnings(self.config)
        except Exception:
            return  # 自检自身出错不影响启动
        if warnings:
            try:
                self._env_check_done.emit(
                    "[环境自检] 警告：" + "\n  · ".join(warnings))
            except RuntimeError:
                # 测试或退出时窗口已被销毁，自检结果丢弃即可
                return

    # ========== 界面骨架 ==========
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("shell")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 侧栏宽度可拖：旧版固定 208px，二层目录第二行只剩 67px，
        # 长任务名全被省略号吃掉；现在默认 240px 且可拖到 340px。
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("shellSplitter")
        self.splitter.setChildrenCollapsible(False)
        sidebar = self._build_sidebar()
        sidebar.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        sidebar.setMaximumWidth(SIDEBAR_MAX_WIDTH)
        self.splitter.addWidget(sidebar)
        self.splitter.addWidget(self._build_center())
        saved_width = SIDEBAR_WIDTH
        root_cfg = getattr(self.config, "config", None)
        if isinstance(root_cfg, dict):
            try:
                saved_width = int(root_cfg.get("ui", {}).get(
                    "sidebar_width", SIDEBAR_WIDTH))
            except (TypeError, ValueError):
                saved_width = SIDEBAR_WIDTH
        saved_width = max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, saved_width))
        self.splitter.setSizes([saved_width, 1380 - saved_width])
        self.splitter.splitterMoved.connect(self._on_sidebar_resized)
        outer.addWidget(self.splitter, 1)

    def _on_sidebar_resized(self):
        """拖动分隔条后把宽度落盘（防抖，避免拖动过程中频繁写配置）。"""
        timer = getattr(self, "_sidebar_save_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(400)
            timer.timeout.connect(self._save_sidebar_width)
            self._sidebar_save_timer = timer
        timer.start()

    def _save_sidebar_width(self):
        width = self.splitter.sizes()[0]
        root_cfg = getattr(self.config, "config", None)
        if not isinstance(root_cfg, dict):
            return
        try:
            root_cfg.setdefault("ui", {})["sidebar_width"] = int(width)
            self.config.save()
        except (OSError, TypeError, ValueError, AttributeError):
            pass

    # ---------- 左侧：品牌 + 导航 + 任务队列 ----------
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        sidebar.setMaximumWidth(SIDEBAR_MAX_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(SIDEBAR_PADDING, 16, SIDEBAR_PADDING, 10)
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

        layout.addSpacing(8)
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        nav = QLabel("任务队列")
        nav.setProperty("role", "muted")
        nav_row.addWidget(nav)
        nav_row.addStretch(1)
        self.queue_count_label = QLabel("0 项")
        self.queue_count_label.setProperty("role", "muted")
        nav_row.addWidget(self.queue_count_label)
        layout.addLayout(nav_row)
        layout.addSpacing(2)

        # 队列搜索：任务多了以后只能靠目录层级肉眼找，侧栏可拖宽才有意义
        self.task_search = QLineEdit()
        self.task_search.setObjectName("taskSearch")
        self.task_search.setPlaceholderText("搜索任务或文件夹…")
        self.task_search.setClearButtonEnabled(True)
        self.task_search.textChanged.connect(self._on_task_search)
        layout.addWidget(self.task_search)

        self.failed_only_check = QCheckBox("只看失败")
        self.failed_only_check.setProperty("role", "muted")
        self.failed_only_check.setToolTip("筛选出至少有一个步骤失败的任务")
        self.failed_only_check.toggled.connect(self._on_failed_only)
        layout.addWidget(self.failed_only_check)

        self.task_list = TaskListWidget()
        self.task_list.set_task_queue(self.task_queue)
        layout.addWidget(self.task_list, 1)

        hint = QLabel("可拖入视频/音频或文件夹")
        hint.setProperty("role", "muted")
        hint.setWordWrap(False)
        layout.addWidget(hint)

        # 低频操作沉到侧栏底部，不与主任务动作争视觉权重
        utility = QHBoxLayout()
        utility.setSpacing(2)
        open_ws_btn = QPushButton("打开工作区")
        open_ws_btn.setProperty("kind", "text")
        open_ws_btn.setToolTip("在资源管理器中打开任务工作区")
        open_ws_btn.clicked.connect(self._on_open_workspace)
        utility.addWidget(open_ws_btn)
        config_btn = QPushButton("配置")
        config_btn.setProperty("kind", "text")
        config_btn.setToolTip("全局配置：字幕提取路径、工作区与各步骤参数")
        config_btn.clicked.connect(self._on_config)
        utility.addWidget(config_btn)
        self.clear_all_btn = QPushButton("清空队列")
        self.clear_all_btn.setProperty("kind", "text")
        self.clear_all_btn.setToolTip("清空任务列表，可选择是否同时删除中间文件")
        self.clear_all_btn.clicked.connect(self._on_clear_all)
        utility.addWidget(self.clear_all_btn)
        utility.addStretch(1)
        layout.addLayout(utility)

        # 侧栏中的数值控件（无）也纳入滚轮守卫管辖范围
        self._wheel_guard.guard(sidebar)
        return sidebar

    def _on_task_search(self, text: str):
        self.task_list.set_filter(text)
        self._update_queue_count()

    def _on_failed_only(self, checked: bool):
        self.task_list.set_failed_only(checked)
        self._update_queue_count()

    def _update_queue_count(self):
        total = len(self.task_queue.tasks)
        shown = self.task_list.visible_task_count()
        if shown == total:
            self.queue_count_label.setText(f"{total} 项")
        else:
            self.queue_count_label.setText(f"{shown}/{total} 项")
        clear_btn = getattr(self, "clear_all_btn", None)
        if clear_btn is not None:
            clear_btn.setEnabled(bool(total))

    # ---------- 中央：任务头 + 步骤卡 + 底部日志抽屉 ----------
    def _build_center(self) -> QWidget:
        center = QWidget()
        center.setObjectName("centerPane")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(20, 14, 20, 10)
        layout.setSpacing(8)

        # 第一行：当前条目 + 作用域 + 唯一主按钮。全局只保留一个黑色主按钮。
        header = QHBoxLayout()
        header.setSpacing(10)
        self.current_task_label = QLabel("未选择任务")
        self.current_task_label.setProperty("role", "taskTitle")
        self.current_task_label.setMinimumWidth(1)
        self.current_task_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        header.addWidget(self.current_task_label)
        self.scope_label = QLabel("")
        self.scope_label.setProperty("role", "muted")
        header.addWidget(self.scope_label)
        header.addStretch(1)

        self.batch_btn = QPushButton("批量执行…")
        self.batch_btn.setProperty("kind", "text")
        self.batch_btn.setToolTip("选择范围与步骤，批量执行多个任务")
        self.batch_btn.clicked.connect(self._on_batch_execute)
        header.addWidget(self.batch_btn)
        self.run_task_btn = QPushButton("执行本任务")
        self.run_task_btn.setProperty("kind", "primary")
        self.run_task_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_task_btn.setToolTip("按 1→2→3 依次执行当前作用域内尚未完成的步骤")
        self.run_task_btn.clicked.connect(self._on_run_task)
        header.addWidget(self.run_task_btn)
        # 兼容旧 mixin：不用第二个"停止批量"按钮，停止统一走 run_task_btn
        self.batch_stop_btn = QPushButton("停止批量")
        self.batch_stop_btn.setVisible(False)
        self.batch_stop_btn.setEnabled(False)
        self.batch_stop_btn.clicked.connect(self._on_batch_stop)
        layout.addLayout(header)

        # 第二行：来源路径 + 三步状态点 + 总进度。路径不再抢占首行。
        meta = QHBoxLayout()
        meta.setSpacing(10)
        self.current_task_subtitle = ElidedLabel(
            "选择左侧任务，或添加视频/音频文件开始")
        self.current_task_subtitle.setProperty("role", "muted")
        self.current_task_subtitle.set_elide_mode(Qt.TextElideMode.ElideMiddle)
        self.current_task_subtitle.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        meta.addWidget(self.current_task_subtitle, 1)
        self.step_dot_labels = {}
        for step, name in ((1, "字幕"), (2, "配音"), (3, "混音")):
            dot = QLabel(f"● {name}")
            dot.setObjectName("stepDot")
            dot.setProperty("status", "pending")
            dot.setToolTip(f"{step} · {name}：等待中")
            self.step_dot_labels[step] = dot
            meta.addWidget(dot)
        self.overall_label = QLabel("")
        self.overall_label.setProperty("role", "secondary")
        self.overall_label.setFixedWidth(40)
        self.overall_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        meta.addWidget(self.overall_label)
        layout.addLayout(meta)

        # 页头分隔线：区隔"固定的标题/执行区"与"可滚动的卡片区"。
        header_rule = QWidget()
        header_rule.setObjectName("headerRule")
        header_rule.setFixedHeight(1)
        layout.addWidget(header_rule)

        # 滚动内容区：空态引导 + 步骤卡片（日志改到底部抽屉，不再占卡片高度）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("centerScrollContent")
        content_layout = QVBoxLayout(content)
        # 卡片间距略放大（10 → 12）：灰底露出更多，卡片之间的"缝"才看得清
        content_layout.setContentsMargins(0, 4, 0, 4)
        content_layout.setSpacing(12)

        self.empty_card = self._build_empty_state()
        content_layout.addWidget(self.empty_card)

        # 三个步骤面板（白色卡片）
        self.step_panels = {}
        step_titles = {1: "字幕提取", 2: "配音", 3: "混音"}
        for step in (1, 2, 3):
            panel = StepPanel(step, step_titles[step])
            panel.start_requested.connect(self._on_start_step)
            panel.stop_requested.connect(self._on_stop_step)
            panel.rerun_requested.connect(self._on_rerun_step)
            panel.hide()
            content_layout.addWidget(panel)
            self.step_panels[step] = panel

        # 条目级入口下沉到对应步骤卡：添加字幕 = 跳过步骤1，添加配音 = 跳过步骤2
        self.add_subtitle_btn = self.step_panels[1].add_extra_button(
            "＋ 添加字幕", kind=None)
        self.add_subtitle_btn.setToolTip("为当前任务添加字幕文件（跳过字幕提取）")
        self.add_subtitle_btn.clicked.connect(self._on_add_subtitle)

        self.add_mix_btn = self.step_panels[2].add_extra_button(
            "＋ 添加配音", kind=None)
        self.add_mix_btn.setToolTip("为当前任务添加配音目录（跳过配音）")
        self.add_mix_btn.clicked.connect(self._on_add_mix_folder)

        # 各步骤卡的配置入口
        whisper_config_btn = self.step_panels[1].add_extra_button("字幕配置…")
        whisper_config_btn.setToolTip("设置设备、VAD、字幕格式、段落合并等参数")
        whisper_config_btn.clicked.connect(self._on_open_whisper_config)
        tts_config_btn = self.step_panels[2].add_extra_button("配音配置…")
        tts_config_btn.setToolTip("设置配音模式（Edge/API）、声音、语速等参数")
        tts_config_btn.clicked.connect(self._on_open_tts_config)
        mixer_config_btn = self.step_panels[3].add_extra_button("混音配置…")
        mixer_config_btn.setToolTip("设置导出格式、音量、声道检测等参数")
        mixer_config_btn.clicked.connect(self._on_open_mixer_config)

        content_layout.addStretch(0)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        self.result_bar = self._build_result_bar()
        self.result_bar.hide()
        layout.addWidget(self.result_bar)

        self.log_drawer = self._build_log_drawer()
        layout.addWidget(self.log_drawer)

        self._refresh_entry_buttons()
        self._update_queue_count()
        self._on_current_changed(self.task_queue.current)
        return center

    def _build_result_bar(self) -> QWidget:
        """批量/组执行结束后的一条结果带：失败数 + 重跑入口。"""
        bar = QWidget()
        bar.setObjectName("resultBar")
        bar.setProperty("state", "error")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 7, 10, 7)
        layout.setSpacing(8)
        self.result_label = QLabel("")
        self.result_label.setProperty("role", "secondary")
        layout.addWidget(self.result_label, 1)
        self.result_retry_btn = QPushButton("重跑失败项")
        self.result_retry_btn.setProperty("kind", "text")
        self.result_retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.result_retry_btn.clicked.connect(self._on_retry_failed)
        layout.addWidget(self.result_retry_btn)
        dismiss = QPushButton("关闭")
        dismiss.setProperty("kind", "text")
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.clicked.connect(self._on_dismiss_result)
        layout.addWidget(dismiss)
        return bar

    def _build_empty_state(self) -> QWidget:
        """空态引导：没有任务时说明接下来做什么，不留三张锁死的步骤卡。"""
        card = QFrame()
        card.setObjectName("emptyCard")
        card.setProperty("card", "true")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 26, 24, 26)
        layout.setSpacing(6)
        title = QLabel("还没有任务")
        title.setProperty("role", "section")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title)
        self.empty_title = title
        desc = QLabel("添加素材后，这里按「字幕 → 配音 → 混音」三步推进；"
                      "顶部「执行本任务」会一次跑完剩余步骤。")
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(desc)
        self.empty_desc = desc
        flow = QLabel("1 提取字幕  →  2 生成配音  →  3 合成混音")
        flow.setProperty("role", "secondary")
        flow.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(flow)
        layout.addSpacing(6)
        add_btn = QPushButton("添加素材")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._on_add_media)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(add_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _build_log_drawer(self) -> QWidget:
        """底部日志抽屉：默认折叠成一行，出错时自动展开。"""
        drawer = QWidget()
        drawer.setObjectName("logDrawer")
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(20, 6, 20, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("运行日志")
        title.setProperty("role", "section")
        header.addWidget(title)
        self.log_summary_label = QLabel("无记录")
        self.log_summary_label.setProperty("role", "muted")
        header.addWidget(self.log_summary_label)
        header.addStretch(1)
        self.log_filter = SegmentedControl([
            ("全部", "all"), ("警告", "warn"), ("错误", "error")])
        self.log_filter.setToolTip("按级别筛选日志")
        self.log_filter.current_changed.connect(self._on_log_filter_changed)
        header.addWidget(self.log_filter)
        self.log_copy_btn = QPushButton("复制")
        self.log_copy_btn.setProperty("kind", "text")
        self.log_copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_copy_btn.setToolTip("复制当前筛选结果")
        self.log_copy_btn.clicked.connect(self._on_copy_log)
        header.addWidget(self.log_copy_btn)
        self.log_toggle_btn = QPushButton("展开")
        self.log_toggle_btn.setObjectName("logToggle")
        self.log_toggle_btn.setProperty("kind", "text")
        self.log_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_toggle_btn.clicked.connect(self._on_toggle_log)
        header.addWidget(self.log_toggle_btn)
        layout.addLayout(header)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(160)
        # 最多保留 5000 行，超出自动丢弃最旧日志，防止长时间运行无限增长
        self.log_view.document().setMaximumBlockCount(5000)
        self.log_view.hide()
        layout.addWidget(self.log_view)
        self._log_lines = []
        self._log_filter_level = "all"
        return drawer

    def _on_toggle_log(self):
        self._set_log_expanded(not self.log_view.isVisible())

    def _set_log_expanded(self, expanded: bool):
        self.log_view.setVisible(expanded)
        self.log_toggle_btn.setText("收起" if expanded else "展开")
        if expanded:
            sb = self.log_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_log_filter_changed(self, level):
        self._log_filter_level = level
        self._render_logs()

    def _log_matches(self, level: str) -> bool:
        current = self._log_filter_level
        if current == "all":
            return True
        if current == "warn":
            return level in ("warn", "error")
        return level == "error"

    def _render_logs(self):
        self.log_view.clear()
        for level, line in self._log_lines:
            if self._log_matches(level):
                self._insert_log_line(level, line)
        self._update_log_summary()

    def _insert_log_line(self, level: str, line: str):
        formats = getattr(self, "_log_formats", None)
        if formats is None:
            formats = self._log_formats = {}
        fmt = formats.get(level)
        if fmt is None:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(LOG_COLORS[level]))
            formats[level] = fmt
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)
        self.log_view.setTextCursor(cursor)

    def _update_log_summary(self):
        total = len(self._log_lines)
        errors = sum(1 for level, _ in self._log_lines if level == "error")
        if not total:
            self.log_summary_label.setText("无记录")
        elif errors:
            self.log_summary_label.setText(f"{total} 条 · {errors} 个错误")
        else:
            self.log_summary_label.setText(f"{total} 条")

    def _on_copy_log(self):
        text = self.log_view.toPlainText()
        if text.strip():
            QApplication.clipboard().setText(text)
            self._show_toast("日志已复制")

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
        self.task_queue.group_removed.connect(self._on_group_removed)
        self.task_queue.task_added.connect(self._on_task_added)

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
        """追加一条日志：带时间戳，按消息特征着色（成功绿/失败红/警告橙）。

        日志分两处存：``_log_lines`` 供级别筛选重建，``log_view`` 供即时显示。
        出错时若抽屉是收起的，自动展开——失败不该藏在折叠区里。
        """
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

        self._log_lines.append((level, line))
        if len(self._log_lines) > 5000:
            self._log_lines.pop(0)

        if level == "error" and not self.log_view.isVisible():
            self._set_log_expanded(True)

        if self._log_matches(level):
            self._insert_log_line(level, line)
            # 自动滚动到底部
            sb = self.log_view.verticalScrollBar()
            sb.setValue(sb.maximum())
        self._update_log_summary()


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
