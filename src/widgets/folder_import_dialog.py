# -*- coding: utf-8 -*-
"""目录导入前预览并选择任务。"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class FolderImportPreviewDialog(QDialog):
    ROLE_CANDIDATE = Qt.ItemDataRole.UserRole

    def __init__(self, folder, candidates, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.candidates = candidates
        self._leaf_items = []
        self.setWindowTitle("预览文件夹导入")
        self.resize(1050, 620)

        layout = QVBoxLayout(self)
        hint = QLabel("勾选要导入的音频或整个目录。灰色项已在任务队列中，或输出路径会覆盖源文件。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(("目录 / 文件", "匹配字幕", "预计混音输出", "状态"))
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        layout.addWidget(self.tree, 1)

        self.tree.blockSignals(True)
        folder_items = {}
        for index, candidate in enumerate(candidates):
            parent_item = self.tree.invisibleRootItem()
            current = ""
            parts = candidate.relative_path.split(os.sep)
            for part in parts[:-1]:
                current = os.path.join(current, part) if current else part
                item = folder_items.get(current)
                if item is None:
                    item = QTreeWidgetItem(parent_item, [part])
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                                  Qt.ItemFlag.ItemIsAutoTristate)
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                    folder_items[current] = item
                parent_item = item
            state = candidate.reason or (
                "成品已存在，混音时可能跳过" if os.path.isfile(candidate.output_path) else "待导入"
            )
            item = QTreeWidgetItem(parent_item, [
                parts[-1], os.path.basename(candidate.subtitle_path) if candidate.subtitle_path else "未找到",
                candidate.output_path, state,
            ])
            item.setData(0, self.ROLE_CANDIDATE, index)
            item.setToolTip(0, candidate.source_path)
            item.setToolTip(1, candidate.subtitle_path or "未匹配到字幕；导入后可运行字幕提取")
            item.setToolTip(2, candidate.output_path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if candidate.default_selected
                               else Qt.CheckState.Unchecked)
            if candidate.locked:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._leaf_items.append(item)
        for item in reversed(list(folder_items.values())):
            states = [item.child(i).checkState(0) for i in range(item.childCount())]
            if states and all(s == Qt.CheckState.Checked for s in states):
                item.setCheckState(0, Qt.CheckState.Checked)
            elif any(s != Qt.CheckState.Unchecked for s in states):
                item.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self.tree.blockSignals(False)
        self.tree.expandToDepth(1)
        self.tree.setColumnWidth(0, 290)
        self.tree.setColumnWidth(1, 145)
        self.tree.setColumnWidth(2, 390)
        self.tree.setColumnWidth(3, 190)

        actions = QHBoxLayout()
        all_btn = QPushButton("全选可导入项")
        all_btn.clicked.connect(lambda: self._set_checks("all"))
        actions.addWidget(all_btn)
        default_btn = QPushButton("恢复默认选择")
        default_btn.clicked.connect(lambda: self._set_checks("default"))
        actions.addWidget(default_btn)
        none_btn = QPushButton("全部取消")
        none_btn.clicked.connect(lambda: self._set_checks("none"))
        actions.addWidget(none_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self.summary = QLabel()
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.import_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText("导入选中任务")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._accept_selection)
        layout.addWidget(buttons)
        self.tree.itemChanged.connect(self._update_summary)
        self._update_summary()

    def selected_candidates(self):
        return [candidate for candidate, item in zip(self.candidates, self._leaf_items)
                if not candidate.locked and item.checkState(0) == Qt.CheckState.Checked]

    def _set_checks(self, mode):
        self.tree.blockSignals(True)
        for candidate, item in zip(self.candidates, self._leaf_items):
            selected = not candidate.locked and (
                mode == "all" or mode == "default" and candidate.default_selected
            )
            item.setCheckState(0, Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._update_summary()

    def _update_summary(self, *_):
        selected = self.selected_candidates()
        output_counts = {}
        for candidate in selected:
            output = os.path.normcase(os.path.abspath(candidate.output_path))
            output_counts[output] = output_counts.get(output, 0) + 1
        collisions = sum(count - 1 for count in output_counts.values() if count > 1)
        text = f"共发现 {len(self.candidates)} 个媒体文件，已选择 {len(selected)} 个任务。"
        if collisions:
            text += f" 有 {collisions} 个输出路径冲突，请取消重复目标或修改混音配置。"
            self.summary.setStyleSheet("color: #a32d2d;")
        else:
            self.summary.setStyleSheet("")
        self.summary.setText(text)
        self.import_button.setEnabled(bool(selected) and not collisions)

    def _accept_selection(self):
        self._update_summary()
        if self.import_button.isEnabled():
            self.accept()
