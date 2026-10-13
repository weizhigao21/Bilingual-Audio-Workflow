# -*- coding: utf-8 -*-
"""设置页通用容器：白色分组卡片、分段按钮、路径行。

按 UI_STYLE.md「设置页分组」实现：
- 一组相关设置放在同一白色容器内，边框 #eeeeee、圆角 13 px；
- 行间用 1 px 细线分隔，行内边距 18 × 14 px；
- 行左侧为标题和灰色说明，右侧为控件；
- 页签与模式使用紧凑分段按钮，选中项白底细边框。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog
)

from ..ui_theme import StyledWidget


def _title_label(text: str) -> QLabel:
    """行标题：继承全局正文字号与主文字色，不写内联样式。"""
    return QLabel(text)


def _desc_label(text: str) -> QLabel:
    """行说明：走 muted 角色（12 px 辅助色），随主题统一调整。"""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setProperty("role", "muted")
    return label


def _separator() -> QWidget:
    line = QWidget()
    line.setProperty("separator", "true")
    line.setFixedHeight(1)
    return line


class SettingsGroup(StyledWidget):
    """白色设置分组卡片：标题 + 说明 + 细线分隔的行。

    行结构：左侧标题（13px）与灰色说明（12px），右侧控件；
    控件默认右对齐，需要占满宽度的控件用 add_wide_row。
    继承 StyledWidget：QWidget 子类必须开启 WA_StyledBackground，
    否则 QSS 的卡片边框与圆角（UI_STYLE.md 的 13 px）不会绘制。
    """

    def __init__(self, title: str = "", description: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("settingsGroup")
        self.setProperty("card", "true")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if title or description:
            header = QWidget()
            header_layout = QVBoxLayout(header)
            header_layout.setContentsMargins(18, 14, 18, 10 if description else 14)
            header_layout.setSpacing(2)
            if title:
                head = QLabel(title)
                head.setProperty("role", "section")
                header_layout.addWidget(head)
            if description:
                header_layout.addWidget(_desc_label(description))
            layout.addWidget(header)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        layout.addLayout(self._rows_layout)
        self._row_count = 0

    # ---------- 行构建 ----------
    def _new_row(self) -> tuple:
        row = QWidget()
        row.setProperty("row", "true")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(18, 14, 18, 14)
        row_layout.setSpacing(12)
        self._row_count += 1
        if self._row_count > 1:
            self._rows_layout.addWidget(_separator())
        self._rows_layout.addWidget(row)
        return row, row_layout

    def add_row(self, title: str, description: str = "", control: QWidget = None,
                control_stretch: int = 0):
        """标准设置行：左标题+说明，右控件（右对齐）。返回行控件。"""
        row, row_layout = self._new_row()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)
        left_layout.addWidget(_title_label(title))
        if description:
            left_layout.addWidget(_desc_label(description))
        row_layout.addWidget(left, 1)
        if control is not None:
            row_layout.addWidget(control, control_stretch)
        return row

    def add_wide_row(self, title: str, description: str = "",
                     control: QWidget = None):
        """宽控件行：控件占据剩余大部分宽度（路径、多输入框组合）。"""
        row, row_layout = self._new_row()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)
        left_layout.addWidget(_title_label(title))
        if description:
            left_layout.addWidget(_desc_label(description))
        row_layout.addWidget(left, 0)
        if control is not None:
            row_layout.addWidget(control, 1)
        return row

    def add_widget_row(self, control: QWidget, stretch: int = 1):
        """整行自定义控件（如缓存统计行）。"""
        row, row_layout = self._new_row()
        row_layout.addWidget(control, stretch)
        return row

    def add_footer(self, widget: QWidget):
        """卡片底部附加区域（不带分隔线的说明/操作行）。"""
        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(18, 12, 18, 14)
        wrapper_layout.addWidget(widget, 1)
        self._rows_layout.addWidget(wrapper)
        return wrapper


class SegmentedControl(StyledWidget):
    """紧凑分段按钮：2–3 个互斥选项，选中项白底细边框。

    继承 StyledWidget：容器自身的浅灰底与细边框由 QSS 绘制。
    """

    current_changed = pyqtSignal(object)

    def __init__(self, options: list, parent=None):
        """options: [(显示文本, 数据), ...]"""
        super().__init__(parent)
        self.setObjectName("segmented")
        self._buttons = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        for text, data in options:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda checked=False, d=data: self.current_changed.emit(d)
            )
            layout.addWidget(button)
            self._buttons.append((button, data))
        if self._buttons:
            self._buttons[0][0].setChecked(True)

    def current_data(self):
        for button, data in self._buttons:
            if button.isChecked():
                return data
        return None

    def set_current_data(self, data) -> bool:
        for button, value in self._buttons:
            if value == data:
                button.setChecked(True)
                return True
        return False


class PathRow(QWidget):
    """路径输入行：等宽输入框 + 浏览按钮。"""

    text_changed = pyqtSignal(str)

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.text_changed)
        layout.addWidget(self.edit, 1)
        self.browse_button = QPushButton("浏览…")
        self.browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_button.clicked.connect(self._on_browse)
        layout.addWidget(self.browse_button)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str):
        self.edit.setText(value)

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "选择目录")
        if folder:
            self.edit.setText(folder)
