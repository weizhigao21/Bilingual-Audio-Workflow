# -*- coding: utf-8 -*-
"""单个步骤的面板组件（声笺风格白色卡片）。

状态驱动形态：
- 运行中：完整展开，进度条 + 百分比 + 当前片段，主按钮换成「停止」；
- 已完成 / 已跳过：折叠成一行摘要，只保留「打开」与「重跑」，把空间让给
  还需要操作的步骤；
- 未就绪：不显示可点的灰色「开始」，而是直接写明被哪一步锁住；
- 失败：展开显示错误首行，主按钮写「重试」而不是「开始」。
"""
import os
import subprocess

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFontMetrics
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy,
    QVBoxLayout
)

from ..task_manager import (STEP_DONE, STEP_FAILED, STEP_PENDING,
                            STEP_RUNNING, STEP_SKIPPED)
from ..ui_theme import PROGRESS_HEIGHT, repolish
from .common import STATUS_TEXT

# 输出行前缀（未显式传入 set_output 时按步骤取）
_DEFAULT_PREFIX = {1: "字幕", 2: "配音", 3: "成品"}
_COMPACT_STATES = (STEP_DONE, STEP_SKIPPED)
_MAX_EXPANDED = 16777215


def _html_escape(text: str, quote: bool = False) -> str:
    """转义 HTML 特殊字符，防止路径中的 & < > 破坏 RichText 标记。"""
    escaped = (text.replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;"))
    if quote:
        escaped = escaped.replace('"', "&quot;")
    return escaped


class StepPanel(QFrame):
    """单个步骤的面板：标题+状态+进度+输出+状态化按钮。"""

    start_requested = pyqtSignal(int)   # step_number
    stop_requested = pyqtSignal(int)
    rerun_requested = pyqtSignal(int)

    def __init__(self, step_number: int, title: str, parent=None):
        super().__init__(parent)
        self.step_number = step_number
        self._status = STEP_PENDING
        self._ready = True
        self._busy = False
        self._compact = False
        self._extra_buttons = []
        self.setProperty("card", "true")
        # 左缘状态色条由 QSS 按 state 属性着色（见 ui_theme：QFrame[card][state=...]）
        self.setProperty("state", STEP_PENDING)
        self._build_ui(title)

    def _build_ui(self, title: str):
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 11, 16, 11)
        self._layout.setSpacing(7)

        # 标题行：步骤徽标 + 标题 + 状态摘要 + 状态药丸
        self._header = QHBoxLayout()
        self._header.setSpacing(8)
        self.badge = QLabel(str(self.step_number))
        self.badge.setObjectName("stepBadge")
        self.badge.setFixedSize(22, 22)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._header.addWidget(self.badge)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "section")
        self._header.addWidget(self.title_label)
        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "secondary")
        self.summary_label.setMinimumWidth(1)
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Preferred)
        self.summary_label.hide()
        self._header.addWidget(self.summary_label, 1)
        self._header.addStretch(0)
        self.status_label = QLabel(STATUS_TEXT[STEP_PENDING])
        self.status_label.setProperty("pill", "true")
        self.status_label.setProperty("status", STEP_PENDING)
        self._header.addWidget(self.status_label)
        self._layout.addLayout(self._header)

        # 进度条 + 外置百分比（3px 细条，百分比在文字上与条同高）
        self._progress_row = QHBoxLayout()
        self._progress_row.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(PROGRESS_HEIGHT)
        self._progress_row.addWidget(self.progress, 1)
        self.percent_label = QLabel("")
        self.percent_label.setProperty("role", "secondary")
        self.percent_label.setFixedWidth(34)
        self.percent_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percent_label.hide()
        self._progress_row.addWidget(self.percent_label)
        self._layout.addLayout(self._progress_row)

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
        self._layout.addWidget(self.detail_label)

        # 输出行：只显示产物文件名（中间省略），完整路径进 tooltip。
        # 打开/定位做成独立按钮，不再把两段蓝色 URL 拼进文本里。
        self._output_path = ""
        self._output_prefix = _DEFAULT_PREFIX.get(self.step_number, "输出")
        self.output_label = QLabel(self._placeholder_text())
        self.output_label.setTextFormat(Qt.TextFormat.PlainText)
        self.output_label.setProperty("role", "mono")
        # 长路径不得反向撑宽卡片（否则窄窗口下被裁切，还会引发二次布局）
        self.output_label.setMinimumWidth(1)
        self.output_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        self._layout.addWidget(self.output_label)

        # 按钮行：一个主动作（开始/停止/重试）+ 右侧次要动作
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
        self.stop_btn.hide()
        self.btn_row.addWidget(self.start_btn)
        self.btn_row.addWidget(self.stop_btn)
        self.btn_row.addStretch()
        self.open_btn = QPushButton("打开")
        self.open_btn.setProperty("kind", "text")
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_output)
        self.open_btn.hide()
        self.btn_row.addWidget(self.open_btn)
        self.reveal_btn = QPushButton("定位")
        self.reveal_btn.setProperty("kind", "text")
        self.reveal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_btn.setToolTip("在资源管理器中定位该文件")
        self.reveal_btn.clicked.connect(
            lambda: self._reveal_in_explorer(self._output_path))
        self.reveal_btn.hide()
        self.btn_row.addWidget(self.reveal_btn)
        self.rerun_btn = QPushButton("重新生成")
        self.rerun_btn.setProperty("kind", "text")
        self.rerun_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rerun_btn.clicked.connect(
            lambda: self.rerun_requested.emit(self.step_number))
        self.rerun_btn.hide()
        self.btn_row.addWidget(self.rerun_btn)
        self._layout.addLayout(self.btn_row)

    def add_extra_button(self, text: str, kind: str = "text") -> QPushButton:
        """在按钮行右侧添加一个次要操作按钮，返回实例。

        默认 kind=text（如"…配置…"）：卡片内主按钮只有一个，配置类
        入口应弱于同行的停止，避免按钮行出现三个同权重的白底按钮。
        传 ``kind=None`` 则用默认按钮外观——用于"＋ 添加字幕/配音"这类
        真正带动作的入口，需要看得出是个按钮。
        """
        btn = QPushButton(text)
        if kind:
            btn.setProperty("kind", kind)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_row.addWidget(btn)
        self._extra_buttons.append(btn)
        return btn

    # ---------- 状态 ----------
    def set_status(self, status: str):
        """只改状态外观（状态条、药丸、折叠与否）。"""
        self._status = status
        self.status_label.setText(STATUS_TEXT.get(status, status))
        self.status_label.setProperty("status", status)
        # 动态属性变化后需要重新求值 QSS
        repolish(self.status_label)
        # 卡片左缘状态色条同步（同样需要 repolish，否则 QSS 不会重算）
        self.setProperty("state", status)
        repolish(self)
        self._apply_shape(status)
        self._apply_buttons()

    def apply_state(self, status: str, ready: bool = True, busy: bool = False,
                    reason: str = "", output_path: str = "",
                    prefix: str = "") -> None:
        """主窗口刷新时调用：一次性同步状态、可用性、锁定原因与输出。

        ``reason`` 用于未就绪状态——卡片直接写"需先完成…"，不依赖点击后弹窗。
        """
        self._ready = bool(ready)
        self._busy = bool(busy)
        if output_path or prefix:
            self.set_output(output_path, prefix)
        self.set_status(status)
        if reason and status == STEP_PENDING and not ready:
            self.summary_label.setText(reason)
            self.summary_label.setToolTip(reason)
            self.summary_label.show()
        self._apply_buttons()

    def set_progress(self, value: int, maximum: int = 100):
        self.progress.setRange(0, maximum)
        self.progress.setValue(value)
        if maximum > 0:
            self.percent_label.setText(f"{round(value * 100 / maximum)}%")
        else:
            self.percent_label.setText("")
        self.percent_label.setVisible(self._status == STEP_RUNNING)

    def set_detail(self, text: str):
        self.detail_label.setText(text)
        # set_error 之后要能退回常规次要色；属性未变不 repolish，避免
        # 运行期每次进度刷新都重算 QSS
        if self.detail_label.property("role") != "secondary":
            self.detail_label.setProperty("role", "secondary")
            repolish(self.detail_label)
        if self.detail_label.toolTip():
            self.detail_label.setToolTip("")
        self.detail_label.setVisible(bool(text) and not self._compact)

    def set_error(self, text: str):
        """失败原因直达：首行显示在 detail（错误色），全文进悬停提示。"""
        full = (text or "").strip()
        if not full:
            self.set_detail("")
            return
        first = full.splitlines()[0]
        if len(first) > 72:
            first = first[:72] + "…"
        self.detail_label.setText(first)
        self.detail_label.setProperty("role", "error")
        repolish(self.detail_label)
        self.detail_label.setToolTip(full)
        self.detail_label.setVisible(not self._compact)

    def _placeholder_text(self) -> str:
        return f"{self._output_prefix}：尚未生成"

    def set_output(self, path: str, prefix: str = ""):
        """显示产物文件名：单行中间省略，完整路径进 tooltip。"""
        self._output_path = path or ""
        if prefix:
            self._output_prefix = prefix
        if not self._output_path:
            self.output_label.setToolTip("")
            self._refresh_output()
            self.open_btn.hide()
            self.reveal_btn.hide()
            return
        self.output_label.setToolTip(
            f"{self._output_prefix}：{self._output_path}")
        self._refresh_output()
        visible = not self._busy
        self.open_btn.setVisible(visible)
        self.reveal_btn.setVisible(visible)
        self.open_btn.setToolTip(f"打开{self._output_prefix}")
        self.reveal_btn.setToolTip(
            f"在资源管理器中定位{self._output_prefix}")

    def _refresh_output(self):
        """按当前可用宽度重排输出行（保留文件名：中间省略）。"""
        path = getattr(self, "_output_path", "")
        prefix = getattr(self, "_output_prefix", "输出")
        if not path:
            self.output_label.setText(self._placeholder_text())
            return
        head = f"{prefix}："
        name = os.path.basename(path.rstrip("\\/")) or path
        metrics = QFontMetrics(self.output_label.font())
        width = self.output_label.width()
        if width < 80:
            # 尚未完成首次布局：先放完整文件名，后续 resizeEvent 会收敛
            shown = name
        else:
            avail = max(40, width - metrics.horizontalAdvance(head) - 4)
            shown = metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, avail)
        self.output_label.setText(f"{head}{shown}")

    def _on_output_link(self, url: str):
        """输出行链接分发：路径链接打开文件；folder: 链接定位所在文件夹。"""
        if url.startswith("folder:"):
            self._reveal_in_explorer(url[len("folder:"):])
        elif url.startswith("file:"):
            QDesktopServices.openUrl(QUrl(url))

    def _open_output(self):
        if self._output_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_path))

    @staticmethod
    def _reveal_in_explorer(path: str):
        """资源管理器中定位并高亮文件（explorer /select），失败退化为打开目录。"""
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                return
        except Exception:
            pass
        try:
            os.startfile(os.path.dirname(path) or path)
        except Exception:
            pass

    # ---------- 形态 ----------
    def _apply_shape(self, status: str):
        """完成/跳过折叠为一行；其余状态展开。"""
        self._compact = status in _COMPACT_STATES
        for widget in (self.progress, self.percent_label, self.detail_label,
                       self.output_label):
            widget.setVisible(not self._compact)
        if not self._compact:
            self.detail_label.setVisible(bool(self.detail_label.text()))
        self.summary_label.setVisible(self._compact)
        if status == STEP_DONE:
            name = os.path.basename(self._output_path) if self._output_path else ""
            self.summary_label.setText(f"已完成 · {name}" if name else "已完成")
            self.summary_label.setToolTip(self._output_path)
        elif status == STEP_SKIPPED:
            self.summary_label.setText("已跳过")
            self.summary_label.setToolTip("可点「重新生成」改用自动流程")
        else:
            self.summary_label.setText("")
            self.summary_label.setToolTip("")
        for btn in self._extra_buttons:
            btn.setVisible(not self._compact)
        self.setMaximumHeight(64 if self._compact else _MAX_EXPANDED)
        self.setMinimumHeight(50 if self._compact else 0)

    def _apply_buttons(self):
        status = self._status
        failed = status == STEP_FAILED
        self.start_btn.setText("重试" if failed else "开始")
        can_start = (status in (STEP_PENDING, STEP_FAILED) and self._ready
                     and not self._busy)
        self.start_btn.setVisible(can_start)
        self.start_btn.setEnabled(can_start)
        self.stop_btn.setVisible(status == STEP_RUNNING)
        self.stop_btn.setEnabled(status == STEP_RUNNING)
        self.open_btn.setVisible(bool(self._output_path) and not self._busy)
        self.reveal_btn.setVisible(bool(self._output_path) and not self._busy)
        self.rerun_btn.setVisible(status in _COMPACT_STATES and not self._busy)
        self.rerun_btn.setText("改用自动流程" if status == STEP_SKIPPED
                               else "重新生成")
        if status == STEP_SKIPPED and not self._output_path:
            self.rerun_btn.setToolTip("重新执行本步骤，改用自动提取/配音")
        else:
            self.rerun_btn.setToolTip("清空本步骤及下游结果后重新执行")

    def set_running(self, running: bool):
        """兼容旧调用：运行中切到 running 形态，结束回到待处理形态。"""
        if running:
            self.set_status(STEP_RUNNING)
        elif self._status == STEP_RUNNING:
            self.set_status(STEP_PENDING)

    def reset(self):
        self._ready = True
        self._busy = False
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.percent_label.setText("")
        self.set_detail("")
        self.set_output("")
        self.set_status(STEP_PENDING)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_output()
