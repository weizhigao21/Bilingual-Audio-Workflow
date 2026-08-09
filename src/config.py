# -*- coding: utf-8 -*-
"""工作流配置管理。

管理字幕提取项目路径、workspace 根目录、各步骤默认参数。
首次启动时弹出引导对话框让用户指定路径。
"""
import os
import sys
import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QMessageBox, QGroupBox, QFormLayout,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox
)
from PyQt6.QtCore import Qt


def get_base_path():
    """返回主控项目根目录（打包后返回 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def get_config_path():
    return str(get_base_path() / "resources" / "configs" / "workflow_config.json")


# 默认配置
DEFAULT_CONFIG = {
    "subprojects": {
        "whisper_dir": "",
    },
    "workspace_dir": str(get_base_path() / "workspace"),
    "whisper": {
        "device": "auto",
        "compute_type": "auto",
        "sub_formats": "lrc",
        "audio_suffixes": "wav,flac,mp3,mp4,mkv,avi,mov",
        "enable_batching": False,
        "overwrite": False,
        "vad_threshold": 0.5,
        "vad_min_silence_duration_ms": 500,
        "vad_min_speech_duration_ms": 0,
        "vad_speech_pad_ms": 400,
        "merge_segments": True,
        "merge_max_gap_ms": 300,
        "merge_max_duration_ms": 30000,
    },
    "tts": {
        "tts_mode": "edge",
        "edge_voice": "zh-CN-XiaoxiaoNeural",
        "edge_rate": "+0%",
        "edge_volume": "+0%",
        "edge_threads": 5,
        "edge_max_concurrent": 8,
        "pipeline_tts_workers": 2,
        "use_bulk_api": True,
        "bulk_batch_size": 20,
        "use_multi_api": False,
        "current_api_index": 0,
        "api_configs": [
            {
                "name": "本地服务器",
                "url": "http://127.0.0.1:8000",
                "model": "八重神子_ZH",
                "status": "success"
            }
        ],
    },
    "mixer": {
        "volume_db": 0.0,
        "output_format": "mp4",
        "export_preset": "standard",
        "audio_bitrate": "192k",
        "audio_sample_rate": 44100,
        "audio_channels": 2,
        "wav_bit_depth": 16,
        "use_gpu": True,
        "channel_detect": True,
        "align_onset": True,
        "content_alignment": False,
        "auto_volume": "fixed",
        "add_suffix": True,
        "skip_existing": True,
        "output_folder": "",
        "thread_count": 4,
        "enable_batch_parallel": True,
        "folder_prefix": True,
        "channel_map": {"left": 155, "right": 25, "both": 135},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 覆盖 base。"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class WorkflowConfig:
    """工作流配置管理器。"""

    def __init__(self):
        self.config = dict(DEFAULT_CONFIG)
        self._load()
        # 确保工作区目录存在
        ws_dir = self.config.get("workspace_dir", "")
        if ws_dir:
            os.makedirs(ws_dir, exist_ok=True)

    def _load(self):
        path = get_config_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.config = _deep_merge(DEFAULT_CONFIG, saved)
            except Exception as e:
                print(f"[配置] 加载失败，使用默认配置: {e}")

    def save(self):
        path = get_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def is_configured(self) -> bool:
        """检查字幕提取项目路径是否已正确配置。"""
        sp = self.config.get("subprojects", {})
        return bool(sp.get("whisper_dir", "")) and os.path.isdir(sp["whisper_dir"])

    @staticmethod
    def _validate_subproject(key: str, path: str) -> bool:
        if not path or not os.path.isdir(path):
            return False
        checks = {
            "whisper_dir": ["infer.exe"],
        }
        return all(os.path.exists(os.path.join(path, f)) for f in checks.get(key, []))

    # 便捷访问
    @property
    def whisper_dir(self): return self.config["subprojects"]["whisper_dir"]
    @property
    def workspace_dir(self): return self.config["workspace_dir"]
    @property
    def whisper_cfg(self): return self.config["whisper"]
    @property
    def tts_cfg(self): return self.config["tts"]
    @property
    def mixer_cfg(self): return self.config["mixer"]

    def run_setup_dialog(self, app) -> bool:
        """首次启动引导对话框，返回是否完成配置。"""
        dialog = SetupDialog(self)
        return dialog.exec() == QDialog.DialogCode.Accepted


class SetupDialog(QDialog):
    """首次启动配置对话框。"""

    def __init__(self, config: WorkflowConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle("首次启动配置 - 双语音声工作流")
        self.setMinimumWidth(680)
        self._build_ui()
        self._load_existing()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        hint = QLabel("请指定字幕提取项目的路径。程序会自动验证路径有效性。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 字幕提取项目路径
        self.whisper_edit = self._add_path_row(
            layout, "字幕提取项目 (含 infer.exe)：",
            "whisper_dir", is_file=False
        )

        # workspace 路径
        self.workspace_edit = self._add_path_row(
            layout, "工作区目录 (任务输出根目录)：",
            "workspace_dir", is_file=False
        )

        # 按钮区
        btn_layout = QHBoxLayout()
        verify_btn = QPushButton("验证路径")
        verify_btn.clicked.connect(self._verify_all)
        btn_layout.addWidget(verify_btn)
        btn_layout.addStretch()

        ok_btn = QPushButton("保存并开始")
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

    def _add_path_row(self, parent_layout, label_text, key, is_file=False) -> QLineEdit:
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        edit = QLineEdit()
        row.addWidget(edit, 1)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(lambda: self._browse(edit, is_file))
        row.addWidget(browse_btn)
        parent_layout.addLayout(row)
        return edit

    def _browse(self, edit: QLineEdit, is_file: bool):
        if is_file:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        else:
            path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            edit.setText(path)

    def _load_existing(self):
        sp = self.config.config.get("subprojects", {})
        self.whisper_edit.setText(sp.get("whisper_dir", ""))
        self.workspace_edit.setText(self.config.config.get("workspace_dir", ""))

    def _verify_all(self) -> bool:
        values = {
            "whisper_dir": self.whisper_edit.text().strip(),
        }
        workspace = self.workspace_edit.text().strip()
        errors = []
        for key, path in values.items():
            if not WorkflowConfig._validate_subproject(key, path):
                errors.append(f"  - {key}: 路径无效或缺少必要文件")
        if not workspace:
            errors.append("  - workspace_dir: 未指定工作区目录")
        if errors:
            self._status_label.setText("验证失败：\n" + "\n".join(errors))
            self._status_label.setStyleSheet("color: red;")
            return False
        self._status_label.setText("验证通过！")
        self._status_label.setStyleSheet("color: green;")
        return True

    def _on_accept(self):
        if not self._verify_all():
            QMessageBox.warning(self, "提示", "请先通过路径验证。")
            return
        self.config.config["subprojects"]["whisper_dir"] = self.whisper_edit.text().strip()
        ws_dir = self.workspace_edit.text().strip()
        self.config.config["workspace_dir"] = ws_dir
        os.makedirs(ws_dir, exist_ok=True)
        self.config.save()
        self.accept()
