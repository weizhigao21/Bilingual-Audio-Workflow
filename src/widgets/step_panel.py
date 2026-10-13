# -*- coding: utf-8 -*-
"""单个步骤的面板组件（声笺风格白色卡片）。

标题行：步骤徽标 + 标题 + 状态药丸；下方 3px 进度条、细节说明、
等宽输出路径（RichText 渲染为可点击链接，单行省略 + 悬停完整内容），
底部按钮行（主按钮唯一）。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy,
    QVBoxLayout
)

from ..task_manager import STEP_PENDING
from ..ui_theme import repolish
from .common import STATUS_TEXT

# 输出行前缀（未显式传入 set_output 时按步骤取）
_DEFAULT_PREFIX = {1: "字幕", 2: "配音", 3: "成品"}


def _html_escape(text: str, quote: bool = False) -> str:
    """转义 HTML 特殊字符，防止路径中的 & < > 破坏 RichText 标记。"""
    escaped = (text.replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;"))
    if quote:
        escaped = escaped.replace('"', "&quot;")
    return escaped


class StepPanel(QFrame):
    """单个步骤的面板：标题+状态+进度+输出+按钮。"""

    start_requested = pyqtSignal(int)   # step_number
    stop_requested = pyqtSignal(int)

    def __init__(self, step_number: int, title: str, parent=None):
        super().__init__(parent)
        self.step_number = step_number
        self.setProperty("card", "true")
        self._build_ui(title)

    def _build_ui(self, title: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 标题行：步骤徽标 + 标题 + 状态药丸
        header = QHBoxLayout()
        header.setSpacing(8)
        self.badge = QLabel(str(self.step_number))
        self.badge.setObjectName("stepBadge")
        self.badge.setFixedSize(22, 22)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.badge)
        title_label = QLabel(title)
        title_label.setProperty("role", "section")
        header.addWidget(title_label)
        header.addStretch()
        self.status_label = QLabel(STATUS_TEXT[STEP_PENDING])
        self.status_label.setProperty("pill", "true")
        self.status_label.setProperty("status", STEP_PENDING)
        header.addWidget(self.status_label)
        layout.addLayout(header)

        # 进度条（3px 细条，百分比文字外置）
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        layout.addWidget(self.progress)

        # 语音片段/当前混音任务等实时细节（进度条下方）
        self.detail_label = QLabel("")
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setProperty("role", "secondary")
        self.detail_label.setWordWrap(True)
        # 自动换行标签必须让出宽度主导权（Ignored）：否则长文本会反向
        # 撑宽卡片，并在宽窄之间来回触发二次布局
        self.detail_label.setMinimumWidth(1)
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        self.detail_label.hide()
        layout.addWidget(self.detail_label)

        # 输出路径：等宽、单行省略、悬停完整内容；RichText 渲染为可点击链接。
        # 这一行恒定占位（无输出时显示"尚未生成"）：显隐会让卡片高矮差
        # 26px，切换任务时中央整列跟着跳（用户反馈的"UI 有跳动"）。
        self._output_path = ""
        self._output_prefix = _DEFAULT_PREFIX.get(self.step_number, "输出")
        self.output_label = QLabel(self._placeholder_text())
        self.output_label.setTextFormat(Qt.TextFormat.RichText)
        self.output_label.setProperty("role", "mono")
        self.output_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        self.output_label.setOpenExternalLinks(True)
        # 长路径不得反向撑宽卡片（否则窄窗口下被裁切，还会引发二次布局）
        self.output_label.setMinimumWidth(1)
        self.output_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        layout.addWidget(self.output_label)

        # 按钮行：主按钮（开始）唯一，停止/配置为次要操作
        self.btn_row = QHBoxLayout()
        self.btn_row.setSpacing(6)
        self.start_btn = QPushButton("开始")
        self.start_btn.setProperty("kind", "primary")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(
            lambda: self.start_requested.emit(self.step_number))
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.clicked.connect(
            lambda: self.stop_requested.emit(self.step_number))
        self.btn_row.addWidget(self.start_btn)
        self.btn_row.addWidget(self.stop_btn)
        self.btn_row.addStretch()
        layout.addLayout(self.btn_row)

    def add_extra_button(self, text: str, kind: str = "text") -> QPushButton:
        """在按钮行右侧添加一个次要操作按钮，返回实例。

        默认 kind=text（如"…配置…"）：卡片内主按钮只有"开始"一个，配置类
        入口应弱于同行的"停止"，避免按钮行出现三个同权重的白底按钮。
        传 ``kind=None`` 则用默认按钮外观——用于"＋ 添加字幕/配音"这类
        真正带动作的入口，需要看得出是个按钮。
        """
        btn = QPushButton(text)
        if kind:
            btn.setProperty("kind", kind)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_row.addWidget(btn)
        return btn

    def set_status(self, status: str):
        self.status_label.setText(STATUS_TEXT.get(status, status))
        self.status_label.setProperty("status", status)
        # 动态属性变化后需要重新求值 QSS
        repolish(self.status_label)

    def set_progress(self, value: int, maximum: int = 100):
        self.progress.setRange(0, maximum)
        self.progress.setValue(value)

    def set_detail(self, text: str):
        self.detail_label.setText(text)
        self.detail_label.setVisible(bool(text))

    def _placeholder_text(self) -> str:
        return f"{self._output_prefix}：尚未生成"

    def set_output(self, path: str, prefix: str = ""):
        """显示输出路径：单行省略，悬停显示完整内容；可点击打开。

        必须用 RichText：PlainText 会把 <a href> 标签按纯文本裸显
        （截图事故：输出行直接露出 file:/// 原始 HTML）。省略按像素宽度
        做（resizeEvent 重算），而不是按字符数截断——卡片随窗口变窄时
        字符截断不会自适应。

        ``prefix`` 留空表示沿用当前前缀（清空输出时不该把"字幕"退回
        "输出"）。没有输出时该行显示占位文案而不是隐藏：卡片高度因此
        恒定，切换任务时中央区不会整体跳动。
        """
        self._output_path = path or ""
        if prefix:
            self._output_prefix = prefix
        if not self._output_path:
            self.output_label.setToolTip("")
            self._refresh_output()
            return
        self.output_label.setToolTip(
            f"{self._output_prefix}：{self._output_path}")
        self._refresh_output()

    def _refresh_output(self):
        """按当前可用宽度重排输出行（保留文件名：中间省略）。"""
        path = getattr(self, "_output_path", "")
        prefix = getattr(self, "_output_prefix", "输出")
        if not path:
            self.output_label.setText(self._placeholder_text())
            return
        head = f"{prefix}："
        metrics = QFontMetrics(self.output_label.font())
        width = self.output_label.width()
        if width < 80:
            # 尚未完成首次布局：先放完整路径，后续 resizeEvent 会收敛
            shown = path
        else:
            avail = max(60, width - metrics.horizontalAdvance(head) - 4)
            shown = metrics.elidedText(path, Qt.TextElideMode.ElideMiddle, avail)
        self.output_label.setText(
            f'{head}<a href="file:///{_html_escape(path, quote=True)}">'
            f'{_html_escape(shown)}</a>')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_output()

    def set_running(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def reset(self):
        self.set_status(STEP_PENDING)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.set_detail("")
        self.set_output("")
        self.set_running(False)
