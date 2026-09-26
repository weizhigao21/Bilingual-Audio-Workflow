# -*- coding: utf-8 -*-
"""主窗口 Mixin：全局配置与三个步骤的配置对话框。"""
from ..config import SetupDialog
from ..widgets import TTSConfigPanel, MixerConfigPanel, WhisperConfigPanel


class ConfigDialogMixin:
    """配置对话框相关方法。"""

    def _on_config(self):
        dialog = SetupDialog(self.config)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._append_log("[配置] 已更新")

    def _on_open_tts_config(self):
        """打开 TTS 配置对话框。配置变更自动保存。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("TTS 配置")
        dialog.setMinimumWidth(420)
        dlg_layout = QVBoxLayout(dialog)
        panel = TTSConfigPanel(self.config, dialog)
        panel.config_changed.connect(self._on_tts_config_changed)
        panel.log_message.connect(self._append_log)
        dlg_layout.addWidget(panel)
        # 关闭按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        dlg_layout.addLayout(btn_row)
        dialog.exec()

    def _on_tts_config_changed(self):
        """TTS 配置变化时记录日志。"""
        from ..steps.tts_profile import profile_matches
        from ..task_manager import STEP_DONE, STEP_SKIPPED, STEP_PENDING
        for task in self.task_queue.tasks:
            if task.custom_mix_folder or task.step2_status not in (STEP_DONE, STEP_SKIPPED):
                continue
            if task.step2_output and not profile_matches(task.step2_output, self.config.tts_cfg):
                task.set_step_status(2, STEP_PENDING)
                task.set_step_output(2, "")
                task.set_step_status(3, STEP_PENDING)
                task.set_step_output(3, "")
                task.force_remix = True
                self.task_queue.update_task(task)
        mode = self.config.tts_cfg.get("tts_mode", "edge")
        if mode == "edge":
            voice = self.config.tts_cfg.get("edge_voice", "")
            self._append_log(f"[TTS配置] Edge模式, 声音={voice}")
        else:
            idx = self.config.tts_cfg.get("current_api_index", 0)
            apis = self.config.tts_cfg.get("api_configs", [])
            api_name = apis[idx].get("name", "?") if 0 <= idx < len(apis) else "?"
            self._append_log(f"[TTS配置] API模式, 当前API={api_name}")

    def _on_open_mixer_config(self):
        """打开混音配置对话框。配置变更自动保存。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("混音配置")
        dialog.setMinimumWidth(400)
        dlg_layout = QVBoxLayout(dialog)
        panel = MixerConfigPanel(self.config, dialog)
        panel.config_changed.connect(self._on_mixer_config_changed)
        dlg_layout.addWidget(panel)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        dlg_layout.addLayout(btn_row)
        dialog.exec()

    def _on_mixer_config_changed(self):
        """混音配置变化时记录日志。"""
        cfg = self.config.mixer_cfg
        self._append_log(
            f"[混音配置] 格式={cfg.get('output_format', 'mp4')}, "
            f"音量={cfg.get('volume_db', 0.0)}dB, "
            f"模式={cfg.get('auto_volume', 'fixed')}"
        )

    def _on_open_whisper_config(self):
        """打开字幕提取配置对话框。配置变更自动保存。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("字幕提取配置")
        dialog.setMinimumWidth(420)
        dlg_layout = QVBoxLayout(dialog)
        panel = WhisperConfigPanel(self.config, dialog)
        panel.config_changed.connect(self._on_whisper_config_changed)
        dlg_layout.addWidget(panel)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        dlg_layout.addLayout(btn_row)
        dialog.exec()

    def _on_whisper_config_changed(self):
        """字幕配置变化时记录日志。"""
        cfg = self.config.whisper_cfg
        self._append_log(
            f"[字幕配置] 设备={cfg.get('device', 'auto')}, "
            f"精度={cfg.get('compute_type', 'auto')}, "
            f"格式={cfg.get('sub_formats', 'lrc')}, "
            f"VAD阈值={cfg.get('vad_threshold', 0.5)}"
        )
