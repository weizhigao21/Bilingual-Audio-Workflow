# -*- coding: utf-8 -*-
"""短暂浮动提示（声笺风格：轻量操作结果用浮动提示，不打断操作）。"""
from PyQt6.QtCore import QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget
)

from ..ui_theme import StyledWidget


class ToastPopup(StyledWidget):
    """父窗口顶部的浮动提示：淡入显示、短暂停留、淡出消失。

    仅用于轻量结果反馈（如“已添加 3 个任务”）；失败与需要决策的提示
    仍走日志区与弹窗。

    两处 Qt 约束：
    1. 深色底由 QSS 绘制，QWidget 子类必须继承 StyledWidget（否则浮层
       会是默认浅灰底 + 白字，几乎不可读）；
    2. 本控件是窗口内的子控件，``windowOpacity`` 只对顶层窗口生效，
       淡入淡出改用 QGraphicsOpacityEffect。setGraphicsEffect 会清除
       WA_TransparentForMouseEvents，故该属性必须在设置效果之后再设。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self._opacity = 0.0
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        # 必须在 setGraphicsEffect 之后：该调用会清掉这个鼠标穿透属性
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 9, 16, 9)
        self._label = QLabel("", self)
        layout.addWidget(self._label)

        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)
        self.hide()

    def _set_opacity(self, value: float):
        self._opacity = value
        self._effect.setOpacity(value)

    def show_message(self, text: str, duration: int = 2600):
        if not text:
            return
        self._label.setText(text)
        self.adjustSize()
        self._reposition()
        self._hold.stop()
        self._fade.stop()
        self.show()
        self.raise_()
        self._set_opacity(0.0)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hold.start(duration)

    def _fade_out(self):
        self._fade.stop()
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.move(max(0, (parent.width() - self.width()) // 2), 20)

    def hideEvent(self, event):
        self._hold.stop()
        self._fade.stop()
        super().hideEvent(event)
