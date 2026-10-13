# -*- coding: utf-8 -*-
"""主控界面可复用组件的公共常量与工具。"""
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QFontMetrics
from PyQt6.QtWidgets import QLabel, QSizePolicy

from ..task_manager import STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, _natural_key
from ..steps.edge_voices import EDGE_TTS_VOICES


class ElidedLabel(QLabel):
    """单行省略标签：按控件实际宽度省略，悬停显示完整内容。

    UI_STYLE.md 的长文本约定是"单行省略 + 悬停完整内容"。QLabel 自身不会
    省略，长路径/长名称会把布局撑宽或直接显示不下；按字符数手工截断又无法
    适应窗口缩放。本类统一处理：按像素宽度省略，并在 resizeEvent 与字体
    变化时重算，同时把完整文本挂在 tooltip 上。

    ``text()`` 返回完整文本（渲染用 ``super().text()``），调用方读到的永远
    是未省略的原值。
    """

    def __init__(self, text: str = "", parent=None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self._full_text = ""
        self._elide_mode = mode
        self.setTextFormat(Qt.TextFormat.PlainText)
        # 横向忽略 sizeHint：由布局给多少就省略到多少；纵向固定高度防抖动
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    # ---------- 文本 ----------
    def text(self) -> str:
        return self._full_text

    def setText(self, text: str):
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self.setAccessibleName(self._full_text)
        self._fit_text()

    def set_elide_mode(self, mode: Qt.TextElideMode):
        self._elide_mode = mode
        self._fit_text()

    def _fit_text(self):
        metrics = QFontMetrics(self.font())
        margin = self.contentsMargins()
        height = (metrics.height() + margin.top() + margin.bottom()
                  + self.frameWidth() * 2)
        # 仅在高度变化时改动固定高度：resizeEvent 中反复 setFixedHeight
        # 会触发无谓的重新布局（同值也应跳过），造成抖动
        if self.height() != height:
            self.setFixedHeight(height)
        width = self.width() - margin.left() - margin.right() - self.frameWidth() * 2
        if width <= 0:
            # 尚未完成首次布局：先放完整文本，resizeEvent 时再收敛
            super().setText(self._full_text)
            return
        shown = metrics.elidedText(self._full_text.replace("\n", " "),
                                   self._elide_mode, width)
        super().setText(shown)

    # ---------- 自适应 ----------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_text()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._fit_text()


def _natural_sort_key(s: str) -> list:
    """自然排序 key：Track2 排在 Track10 之前。"""
    return _natural_key(s)


# 语速/音量预设
RATE_PRESETS = ["-50%", "-30%", "-20%", "-10%", "+0%", "+10%", "+20%", "+30%", "+50%"]
VOLUME_PRESETS = ["-50%", "-30%", "-20%", "-10%", "+0%", "+10%", "+20%", "+30%", "+50%"]


# 步骤状态对应的显示文本和颜色（颜色与声笺主题色板一致：
# 进行中=强调橙，完成=就绪绿，失败=错误红，等待/跳过=辅助灰）
STATUS_TEXT = {
    STEP_PENDING: "等待中",
    STEP_RUNNING: "进行中",
    STEP_DONE: "已完成",
    STEP_FAILED: "失败",
    "skipped": "已跳过",
}
STATUS_COLOR = {
    STEP_PENDING: QColor("#85858a"),
    STEP_RUNNING: QColor("#e0651c"),
    STEP_DONE: QColor("#83907c"),
    STEP_FAILED: QColor("#a2462e"),
    "skipped": QColor("#85858a"),
}
