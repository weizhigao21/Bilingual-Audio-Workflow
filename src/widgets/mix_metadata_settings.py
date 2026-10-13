# -*- coding: utf-8 -*-
"""导出时写入的作品信息设置。"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from ..version import APP_VERSION
from .settings_group import SettingsGroup


class MixMetadataSettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("编辑导出作品信息")
        self.setMinimumWidth(520)
        cfg = config.mixer_cfg
        fields = cfg.get("metadata_fields") or {}
        values = cfg.get("metadata_values") or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(14)

        hint = QLabel("勾选要写入的字段。留空会沿用该成品已有的信息；{文件名} 会替换为当前音频文件名。")
        hint.setWordWrap(True)
        hint.setProperty("role", "secondary")
        layout.addWidget(hint)

        group = SettingsGroup("写入字段", "仅勾选的字段会写入成品；备注支持多行。")
        self.field_checks = {}
        self.value_edits = {}
        for key, label in (("title", "标题"), ("artist", "艺术家"),
                           ("album", "专辑"), ("comment", "备注")):
            check = QCheckBox(label)
            check.setChecked(bool(fields.get(key, True)))
            check.setMinimumWidth(76)
            check.setCursor(Qt.CursorShape.PointingHandCursor)
            edit = QTextEdit() if key == "comment" else QLineEdit()
            value = values.get(key, "{文件名}" if key == "title" else "")
            if key == "comment":
                edit.setPlainText(value)
                edit.setMaximumHeight(70)
            else:
                edit.setText(value)
                edit.setMaxLength(255)
            edit.setEnabled(check.isChecked())
            check.toggled.connect(edit.setEnabled)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(12)
            row_layout.addWidget(check)
            row_layout.addWidget(edit, 1)
            group.add_widget_row(row)
            self.field_checks[key] = check
            self.value_edits[key] = edit

        self.version_check = QCheckBox(f"程序版本（当前 {APP_VERSION}）")
        self.version_check.setChecked(bool(fields.get("version", True)))
        self.version_check.setCursor(Qt.CursorShape.PointingHandCursor)
        group.add_widget_row(self.version_check)

        note = QLabel("WAV 和原始 AAC 的字段保存在工作区任务记录中。")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        group.add_footer(note)
        layout.addWidget(group)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setProperty("kind", "primary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _save(self):
        cfg = self.config.mixer_cfg
        cfg["metadata_fields"] = {
            **{key: check.isChecked() for key, check in self.field_checks.items()},
            "version": self.version_check.isChecked(),
        }
        cfg["metadata_values"] = {
            key: edit.toPlainText() if isinstance(edit, QTextEdit) else edit.text()
            for key, edit in self.value_edits.items()
        }
        self.config.save()
        self.accept()
