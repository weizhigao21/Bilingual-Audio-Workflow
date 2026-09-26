# -*- coding: utf-8 -*-
"""单个步骤的面板组件。"""
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor

from ..task_manager import STEP_PENDING
from .common import STATUS_TEXT, STATUS_COLOR


class StepPanel(QFrame):
    """单个步骤的面板：标题+状态+进度+输出+按钮。"""

    start_requested = pyqtSignal(int)   # step_number
    stop_requested = pyqtSignal(int)

    def __init__(self, step_number: int, title: str, parent=None):
        super().__init__()
        self.step_number = step_number
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._build_ui(title)

    def _build_ui(self, title: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        # 标题行
        header = QHBoxLayout()
        title_label = QLabel(f"<b>步骤 {self.step_number}：{title}</b>")
        header.addWidget(title_label)
        header.addStretch()
        self.status_label = QLabel(STATUS_TEXT[STEP_PENDING])
        self.status_label.setStyleSheet("color: gray;")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        layout.addWidget(self.progress)

        # 输出路径
        self.output_label = QLabel("输出：—")
        self.output_label.setStyleSheet("color: #666; font-size: 11px;")
        self.output_label.setWordWrap(True)
        layout.addWidget(self.output_label)

        # 按钮
        self.btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始")
        self.start_btn.clicked.connect(lambda: self.start_requested.emit(self.step_number))
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.stop_requested.emit(self.step_number))
        self.btn_row.addWidget(self.start_btn)
        self.btn_row.addWidget(self.stop_btn)
        self.btn_row.addStretch()
        layout.addLayout(self.btn_row)

    def add_extra_button(self, text: str) -> QPushButton:
        """在按钮行右侧添加一个额外按钮，返回按钮实例（外部连接信号）。"""
        btn = QPushButton(text)
        self.btn_row.addWidget(btn)
        return btn

    def set_status(self, status: str):
        self.status_label.setText(STATUS_TEXT.get(status, status))
        self.status_label.setStyleSheet(f"color: {STATUS_COLOR.get(status, QColor(0,0,0)).name()};")

    def set_progress(self, value: int, maximum: int = 100):
        self.progress.setRange(0, maximum)
        self.progress.setValue(value)

    def set_output(self, path: str):
        if path:
            display = path if len(path) < 80 else "..." + path[-77:]
            self.output_label.setText(f"输出：<a href='file:///{path}'>{display}</a>")
            self.output_label.setOpenExternalLinks(True)
        else:
            self.output_label.setText("输出：—")

    def set_running(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def reset(self):
        self.set_status(STEP_PENDING)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.set_output("")
        self.set_running(False)
