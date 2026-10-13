# -*- coding: utf-8 -*-
"""字幕提取配置面板（声笺风格设置分组卡片）。

修改配置后自动保存到 WorkflowConfig，并发 config_changed 信号。
"""
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox, QVBoxLayout, QWidget
)
from PyQt6.QtCore import pyqtSignal

from .settings_group import SettingsGroup


class WhisperConfigPanel(QWidget):
    """字幕提取配置面板：设备、VAD、合并段落等。"""

    config_changed = pyqtSignal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        # 识别参数
        basic = SettingsGroup("识别参数", "设备与精度影响速度与显存占用。")
        self.device_combo = QComboBox()
        self.device_combo.addItem("自动", "auto")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("CUDA（GPU）", "cuda")
        self.device_combo.currentIndexChanged.connect(self._save)
        basic.add_row("设备", "自动优先使用 GPU", self.device_combo)

        self.compute_combo = QComboBox()
        self.compute_combo.addItem("自动", "auto")
        self.compute_combo.addItem("int8（最快/省显存）", "int8")
        self.compute_combo.addItem("int8_float16", "int8_float16")
        self.compute_combo.addItem("float16（平衡）", "float16")
        self.compute_combo.addItem("float32（最精确）", "float32")
        self.compute_combo.currentIndexChanged.connect(self._save)
        basic.add_row("计算精度", "", self.compute_combo)

        self.subfmt_combo = QComboBox()
        self.subfmt_combo.addItem("LRC", "lrc")
        self.subfmt_combo.addItem("VTT", "vtt")
        self.subfmt_combo.addItem("LRC + VTT", "lrc,vtt")
        self.subfmt_combo.addItem("SRT", "srt")
        self.subfmt_combo.addItem("LRC + SRT", "lrc,srt")
        self.subfmt_combo.currentIndexChanged.connect(self._save)
        basic.add_row("字幕格式", "语音生成读取 LRC；SRT/VTT 供其他用途",
                      self.subfmt_combo)
        layout.addWidget(basic)

        # VAD 与段落合并
        vad = SettingsGroup("VAD 与段落合并", "静音检测阈值与相邻段落合并规则。")
        self.vad_threshold_spin = QDoubleSpinBox()
        self.vad_threshold_spin.setRange(0.0, 1.0)
        self.vad_threshold_spin.setSingleStep(0.05)
        self.vad_threshold_spin.setDecimals(2)
        self.vad_threshold_spin.valueChanged.connect(self._save)
        vad.add_row("VAD 阈值", "越高越严格，环境嘈杂时可适当调高",
                    self.vad_threshold_spin)

        self.vad_silence_spin = QSpinBox()
        self.vad_silence_spin.setRange(0, 5000)
        self.vad_silence_spin.setSingleStep(50)
        self.vad_silence_spin.setSuffix(" ms")
        self.vad_silence_spin.valueChanged.connect(self._save)
        vad.add_row("最小静音时长", "超过该时长的静音才切分段落",
                    self.vad_silence_spin)

        self.vad_speech_spin = QSpinBox()
        self.vad_speech_spin.setRange(0, 5000)
        self.vad_speech_spin.setSingleStep(50)
        self.vad_speech_spin.setSuffix(" ms")
        self.vad_speech_spin.valueChanged.connect(self._save)
        vad.add_row("最小语音时长", "短于该时长的语音片段被忽略",
                    self.vad_speech_spin)

        self.vad_pad_spin = QSpinBox()
        self.vad_pad_spin.setRange(0, 2000)
        self.vad_pad_spin.setSingleStep(50)
        self.vad_pad_spin.setSuffix(" ms")
        self.vad_pad_spin.valueChanged.connect(self._save)
        vad.add_row("语音填充", "片段前后各保留的缓冲，避免吞字",
                    self.vad_pad_spin)

        self.merge_check = QCheckBox("启用段落合并")
        self.merge_check.setToolTip("合并相邻短段落，减少碎片字幕")
        self.merge_check.toggled.connect(self._save)
        vad.add_widget_row(self.merge_check)

        self.merge_gap_spin = QSpinBox()
        self.merge_gap_spin.setRange(0, 5000)
        self.merge_gap_spin.setSingleStep(50)
        self.merge_gap_spin.setSuffix(" ms")
        self.merge_gap_spin.valueChanged.connect(self._save)
        vad.add_row("合并最大间隔", "间隔小于该值才合并", self.merge_gap_spin)

        self.merge_dur_spin = QSpinBox()
        self.merge_dur_spin.setRange(1000, 60000)
        self.merge_dur_spin.setSingleStep(1000)
        self.merge_dur_spin.setSuffix(" ms")
        self.merge_dur_spin.valueChanged.connect(self._save)
        vad.add_row("合并最大时长", "合并后单段不超过该时长", self.merge_dur_spin)
        layout.addWidget(vad)

        # 高级
        advanced = SettingsGroup("高级")
        self.batch_check = QCheckBox("启用批处理（加速但占更多显存）")
        self.batch_check.toggled.connect(self._save)
        advanced.add_widget_row(self.batch_check)
        self.overwrite_check = QCheckBox("覆盖已有字幕文件")
        self.overwrite_check.toggled.connect(self._save)
        advanced.add_widget_row(self.overwrite_check)
        layout.addWidget(advanced)
        layout.addStretch(1)

    def _load_values(self):
        cfg = self.config.whisper_cfg
        widgets = [self.device_combo, self.compute_combo, self.subfmt_combo,
                   self.vad_threshold_spin, self.vad_silence_spin,
                   self.vad_speech_spin, self.vad_pad_spin, self.merge_check,
                   self.merge_gap_spin, self.merge_dur_spin, self.batch_check,
                   self.overwrite_check]
        for w in widgets:
            w.blockSignals(True)
        try:
            # 设备
            dev = cfg.get("device", "auto")
            idx = self.device_combo.findData(dev)
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)
            # 计算精度
            ct = cfg.get("compute_type", "auto")
            idx = self.compute_combo.findData(ct)
            if idx >= 0:
                self.compute_combo.setCurrentIndex(idx)
            # 字幕格式
            sf = cfg.get("sub_formats", "lrc")
            idx = self.subfmt_combo.findData(sf)
            if idx >= 0:
                self.subfmt_combo.setCurrentIndex(idx)
            # VAD 参数
            self.vad_threshold_spin.setValue(cfg.get("vad_threshold", 0.5))
            self.vad_silence_spin.setValue(cfg.get("vad_min_silence_duration_ms", 500))
            self.vad_speech_spin.setValue(cfg.get("vad_min_speech_duration_ms", 0))
            self.vad_pad_spin.setValue(cfg.get("vad_speech_pad_ms", 400))
            # 合并
            self.merge_check.setChecked(cfg.get("merge_segments", True))
            self.merge_gap_spin.setValue(cfg.get("merge_max_gap_ms", 300))
            self.merge_dur_spin.setValue(cfg.get("merge_max_duration_ms", 30000))
            # 其他
            self.batch_check.setChecked(cfg.get("enable_batching", False))
            self.overwrite_check.setChecked(cfg.get("overwrite", False))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _save(self):
        cfg = self.config.whisper_cfg
        cfg["device"] = self.device_combo.currentData()
        cfg["compute_type"] = self.compute_combo.currentData()
        cfg["sub_formats"] = self.subfmt_combo.currentData()
        cfg["vad_threshold"] = self.vad_threshold_spin.value()
        cfg["vad_min_silence_duration_ms"] = self.vad_silence_spin.value()
        cfg["vad_min_speech_duration_ms"] = self.vad_speech_spin.value()
        cfg["vad_speech_pad_ms"] = self.vad_pad_spin.value()
        cfg["merge_segments"] = self.merge_check.isChecked()
        cfg["merge_max_gap_ms"] = self.merge_gap_spin.value()
        cfg["merge_max_duration_ms"] = self.merge_dur_spin.value()
        cfg["enable_batching"] = self.batch_check.isChecked()
        cfg["overwrite"] = self.overwrite_check.isChecked()
        self.config.save()
        self.config_changed.emit()
