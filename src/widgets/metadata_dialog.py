# -*- coding: utf-8 -*-
"""混音成品信息编辑器；重封装在后台线程进行。"""
import os

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QTextEdit, QVBoxLayout,
)

from ..ui_theme import repolish
from ..version import APP_VERSION


class _MetadataEditWorker(QThread):
    def __init__(self, output_path, tags, task, parent=None):
        super().__init__(parent)
        self.output_path = output_path
        self.tags = tags
        self.task = task
        self._stop_requested = False
        self.error = ""
        self.warning = ""

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            from ..steps.audio_metadata import edit_output_metadata
            self.warning = edit_output_metadata(
                self.output_path, self.tags, self.task,
                stop_check=lambda: self._stop_requested,
            )
        except Exception as exc:
            self.error = str(exc)


class MetadataDialog(QDialog):
    def __init__(self, task, parent=None):
        super().__init__(parent)
        from ..steps.audio_metadata import read_editable_tags, supports_embedded_tags
        self.task = task
        self.output_path = task.step3_output
        self._worker = None
        self._close_when_finished = False
        self.setWindowTitle("编辑混音成品信息")
        self.setMinimumWidth(450)

        tags = read_editable_tags(self.output_path, task.source_name, task)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        name_label = QLabel(os.path.basename(self.output_path))
        name_label.setProperty("role", "section")
        layout.addWidget(name_label)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.title_edit = QLineEdit(tags["title"])
        self.artist_edit = QLineEdit(tags["artist"])
        self.album_edit = QLineEdit(tags["album"])
        for widget in (self.title_edit, self.artist_edit, self.album_edit):
            widget.setMaxLength(255)
        self.comment_edit = QTextEdit()
        self.comment_edit.setPlainText(tags["comment"])
        self.comment_edit.setMaximumHeight(90)
        form.addRow("标题:", self.title_edit)
        form.addRow("艺术家:", self.artist_edit)
        form.addRow("专辑:", self.album_edit)
        form.addRow("备注:", self.comment_edit)
        layout.addLayout(form)

        self.status_label = QLabel()
        if supports_embedded_tags(self.output_path):
            self.status_label.setText(
                f"保存时无损重封装；保留原混音版本，旧成品标注编辑版本 {APP_VERSION}。"
            )
        else:
            self.status_label.setText(
                "此格式不提供可靠的通用标签；信息会保存在工作区任务记录中。"
            )
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "muted")
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("保存")
        save_button.setProperty("kind", "primary")
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("取消")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.buttons.accepted.connect(self._start_save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _set_status(self, text: str, role: str = "muted"):
        self.status_label.setText(text)
        self.status_label.setProperty("role", role)
        repolish(self.status_label)

    def _start_save(self):
        if self._worker is not None:
            return
        tags = {
            "title": self.title_edit.text(),
            "artist": self.artist_edit.text(),
            "album": self.album_edit.text(),
            "comment": self.comment_edit.toPlainText(),
        }
        self._worker = _MetadataEditWorker(self.output_path, tags, self.task, self)
        self._worker.finished.connect(self._on_save_finished)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self._set_status("正在保存成品信息，请稍候…")
        self._worker.start()

    def _on_save_finished(self):
        worker = self._worker
        self._worker = None
        error = worker.error
        warning = worker.warning
        worker.deleteLater()
        if self._close_when_finished:
            super().reject()
        elif error:
            self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
            self._set_status("保存失败，原成品已保留。", role="error")
            QMessageBox.warning(self, "保存成品信息失败", error)
        else:
            if warning:
                QMessageBox.warning(self, "部分信息未保存", warning)
            super().accept()

    def reject(self):
        if self._worker is not None:
            self._close_when_finished = True
            self._worker.stop()
            self._set_status("正在取消，请稍候…")
            return
        super().reject()
