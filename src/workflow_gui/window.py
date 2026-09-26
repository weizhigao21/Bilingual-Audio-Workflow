# -*- coding: utf-8 -*-
"""工作流主窗口：左右分栏（任务队列 + 三步面板）。

由 workflow_gui.py 拆分为包结构：本模块定义主窗口类与界面骨架，
各功能方法按职责拆分到 mixin 模块（工具栏/配置/批量/任务/步骤/重命名）。
"""
import time

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QTextEdit, QLabel, QSplitter, QGroupBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor

from ..config import WorkflowConfig
from ..version import APP_VERSION
from ..task_manager import TaskQueue, TaskGroup
from ..widgets import StepPanel, TaskListWidget
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
    """工作流主窗口。"""

    def __init__(self, config: WorkflowConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle(f"双语音声工作流 {APP_VERSION}")
        self.setMinimumSize(1100, 720)

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

        self._build_ui()
        self._connect_signals()
        restored, ignored = self.task_queue.restore_tasks()
        if restored or ignored:
            self._append_log(f"[任务恢复] 恢复 {restored} 个任务，忽略 {ignored} 条无效记录")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # 工具栏
        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ 添加视频/音频")
        add_btn.clicked.connect(self._on_add_files)
        toolbar.addWidget(add_btn)

        add_folder_btn = QPushButton("+ 添加文件夹")
        add_folder_btn.setToolTip("导入文件夹，自动识别音频和字幕文件并配对创建任务")
        add_folder_btn.clicked.connect(self._on_add_folder)
        toolbar.addWidget(add_folder_btn)

        add_sub_btn = QPushButton("+ 添加字幕")
        add_sub_btn.setToolTip("为当前任务添加字幕文件（跳过字幕提取）")
        add_sub_btn.clicked.connect(self._on_add_subtitle)
        toolbar.addWidget(add_sub_btn)

        add_mix_btn = QPushButton("+ 添加配音")
        add_mix_btn.setToolTip("为当前任务添加配音目录（跳过语音生成）")
        add_mix_btn.clicked.connect(self._on_add_mix_folder)
        toolbar.addWidget(add_mix_btn)

        open_ws_btn = QPushButton("打开工作区")
        open_ws_btn.clicked.connect(self._on_open_workspace)
        toolbar.addWidget(open_ws_btn)

        config_btn = QPushButton("配置")
        config_btn.clicked.connect(self._on_config)
        toolbar.addWidget(config_btn)

        clear_btn = QPushButton("全部清空")
        clear_btn.setToolTip("清空所有任务（可选择是否删除已生成的文件）")
        clear_btn.setStyleSheet("color: #c00;")
        clear_btn.clicked.connect(self._on_clear_all)
        toolbar.addWidget(clear_btn)

        self.batch_btn = QPushButton("批量执行")
        self.batch_btn.setToolTip("自动遍历所有任务，按选定步骤批量执行")
        self.batch_btn.setStyleSheet("font-weight: bold; color: #0066cc;")
        self.batch_btn.clicked.connect(self._on_batch_execute)
        toolbar.addWidget(self.batch_btn)

        self.batch_stop_btn = QPushButton("停止批量")
        self.batch_stop_btn.setToolTip("停止正在进行的批量执行")
        self.batch_stop_btn.setEnabled(False)
        self.batch_stop_btn.clicked.connect(self._on_batch_stop)
        toolbar.addWidget(self.batch_stop_btn)

        toolbar.addStretch()
        self.status_bar_label = QLabel("就绪")
        toolbar.addWidget(self.status_bar_label)
        main_layout.addLayout(toolbar)

        # 左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：任务列表
        left_group = QGroupBox("任务队列")
        left_layout = QVBoxLayout(left_group)
        self.task_list = TaskListWidget()
        self.task_list.set_task_queue(self.task_queue)
        left_layout.addWidget(self.task_list)
        hint = QLabel("提示：可拖拽视频/音频文件或文件夹到此处；文件夹将作为分组节点展示内部音频树")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        left_layout.addWidget(hint)
        splitter.addWidget(left_group)

        # 右侧：三步面板 + 日志
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.current_task_label = QLabel("未选择任务")
        self.current_task_label.setStyleSheet("font-weight: bold; padding: 4px;")
        right_layout.addWidget(self.current_task_label)

        # 三个步骤面板
        self.step_panels = {}
        step_titles = {1: "字幕提取", 2: "语音生成", 3: "音频混音"}
        for step in (1, 2, 3):
            panel = StepPanel(step, step_titles[step])
            panel.start_requested.connect(self._on_start_step)
            panel.stop_requested.connect(self._on_stop_step)
            right_layout.addWidget(panel)
            self.step_panels[step] = panel
            # 步骤2面板内加"TTS配置"按钮
            if step == 2:
                tts_config_btn = panel.add_extra_button("TTS 配置...")
                tts_config_btn.setToolTip("设置语音合成模式（Edge/API）、声音、语速等参数")
                tts_config_btn.clicked.connect(self._on_open_tts_config)
            # 步骤3面板内加"混音配置"按钮
            if step == 3:
                mixer_config_btn = panel.add_extra_button("混音配置...")
                mixer_config_btn.setToolTip("设置导出格式、音量、声道检测等参数")
                mixer_config_btn.clicked.connect(self._on_open_mixer_config)
            # 步骤1面板内加"字幕配置"按钮
            if step == 1:
                whisper_config_btn = panel.add_extra_button("字幕配置...")
                whisper_config_btn.setToolTip("设置设备、VAD、字幕格式、段落合并等参数")
                whisper_config_btn.clicked.connect(self._on_open_whisper_config)

        # 全局日志区
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        # 最多保留 5000 行，超出自动丢弃最旧日志，防止长时间运行无限增长
        self.log_view.document().setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_view)
        right_layout.addWidget(log_group, 1)

        splitter.addWidget(right_widget)
        splitter.setSizes([300, 800])
        main_layout.addWidget(splitter, 1)

    def _connect_signals(self):
        self.task_list.task_selected.connect(self._on_task_selected)
        self.task_list.task_remove_requested.connect(self._on_task_remove)
        self.task_list.task_rerun_requested.connect(self._on_task_rerun)
        self.task_list.group_selected.connect(self._on_group_selected)
        self.task_list.group_remove_requested.connect(self._on_group_remove)
        self.task_list.group_rerun_requested.connect(self._on_group_rerun)
        self.task_queue.task_added.connect(self.task_list.add_task_item)
        self.task_queue.task_removed.connect(self.task_list.remove_task_item)
        self.task_queue.task_updated.connect(self._on_task_updated)
        self.task_queue.current_changed.connect(self._on_current_changed)
        self.task_queue.group_added.connect(self.task_list.add_group_item)
        self.task_queue.group_removed.connect(self.task_list.remove_group_item)

    # ========== 日志 ==========
    def _append_log(self, msg: str):
        """追加一条日志：带时间戳，按消息特征着色（成功绿/失败红/警告橙）。"""
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"

        # 按内容特征推断级别并着色
        color = QColor("#0C447C")  # 默认：深蓝
        low = msg.lower()
        if any(k in low for k in ("失败", "错误", "异常", "中止", "不存在", "error", "failed")):
            color = QColor("#A32D2D")  # 红
        elif any(k in low for k in ("警告", "warn")):
            color = QColor("#854F0B")  # 橙
        elif any(k in low for k in ("完成", "成功", "done", "success")):
            color = QColor("#0F6E56")  # 绿

        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)
        self.log_view.setTextCursor(cursor)
        # 自动滚动到底部
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.status_bar_label.setText(msg[:60] + "..." if len(msg) > 60 else msg)
