# -*- coding: utf-8 -*-
"""导出时写入的作品信息设置。"""
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QTextEdit, QVBoxLayout, QWidget,
)

from ..version import APP_VERSION


class MixMetadataSettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("编辑导出作品信息")
        self.setMinimumWidth(480)
        cfg = config.mixer_cfg
        fields = cfg.get("metadata_fields") or {}
        values = cfg.get("metadata_values") or {}

        layout = QVBoxLayout(self)
        hint = QLabel("勾选要写入的字段。留空会沿用该成品已有的信息；{文件名} 会替换为当前音频文件名。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.field_checks = {}
        self.value_edits = {}
        for key, label in (("title", "标题"), ("artist", "艺术家"),
                           ("album", "专辑"), ("comment", "备注")):
            row = QHBoxLayout()
            check = QCheckBox(label)
            check.setChecked(bool(fields.get(key, True)))
            check.setMinimumWidth(76)
            row.addWidget(check)
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
            row.addWidget(edit, 1)
            wrapper = QWidget()
            wrapper.setLayout(row)
            layout.addWidget(wrapper)
            self.field_checks[key] = check
            self.value_edits[key] = edit

        self.version_check = QCheckBox(f"程序版本（当前 {APP_VERSION}）")
        self.version_check.setChecked(bool(fields.get("version", True)))
        layout.addWidget(self.version_check)
        layout.addWidget(QLabel("WAV 和原始 AAC 的字段保存在工作区任务记录中。"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
