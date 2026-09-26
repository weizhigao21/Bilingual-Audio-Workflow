# -*- coding: utf-8 -*-
"""字幕提取配置面板。"""
from PyQt6.QtWidgets import (
    QGroupBox, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox
)
from PyQt6.QtCore import pyqtSignal


class WhisperConfigPanel(QGroupBox):
    """字幕提取配置面板：设备、VAD、合并段落等。

    修改配置后自动保存到 WorkflowConfig，并发 config_changed 信号。
    """
    config_changed = pyqtSignal()

    def __init__(self, config, parent=None):
        super().__init__("字幕提取配置")
        self.config = config
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QFormLayout(self)

        # 设备
        self.device_combo = QComboBox()
        self.device_combo.addItem("自动", "auto")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("CUDA（GPU）", "cuda")
        self.device_combo.currentIndexChanged.connect(self._save)
        layout.addRow("设备:", self.device_combo)

        # 计算精度
        self.compute_combo = QComboBox()
        self.compute_combo.addItem("自动", "auto")
        self.compute_combo.addItem("int8（最快/省显存）", "int8")
        self.compute_combo.addItem("int8_float16", "int8_float16")
        self.compute_combo.addItem("float16（平衡）", "float16")
        self.compute_combo.addItem("float32（最精确）", "float32")
        self.compute_combo.currentIndexChanged.connect(self._save)
        layout.addRow("计算精度:", self.compute_combo)

        # 字幕格式
        self.subfmt_combo = QComboBox()
        self.subfmt_combo.addItem("LRC", "lrc")
        self.subfmt_combo.addItem("VTT", "vtt")
        self.subfmt_combo.addItem("LRC + VTT", "lrc,vtt")
        self.subfmt_combo.addItem("SRT", "srt")
        self.subfmt_combo.addItem("LRC + SRT", "lrc,srt")
        self.subfmt_combo.currentIndexChanged.connect(self._save)
        layout.addRow("字幕格式:", self.subfmt_combo)

        # VAD 阈值
        self.vad_threshold_spin = QDoubleSpinBox()
        self.vad_threshold_spin.setRange(0.0, 1.0)
        self.vad_threshold_spin.setSingleStep(0.05)
        self.vad_threshold_spin.valueChanged.connect(self._save)
        layout.addRow("VAD 阈值:", self.vad_threshold_spin)

        # 最小静音时长
        self.vad_silence_spin = QSpinBox()
        self.vad_silence_spin.setRange(0, 5000)
        self.vad_silence_spin.setSingleStep(50)
        self.vad_silence_spin.setSuffix(" ms")
        self.vad_silence_spin.valueChanged.connect(self._save)
        layout.addRow("最小静音时长:", self.vad_silence_spin)

        # 最小语音时长
        self.vad_speech_spin = QSpinBox()
        self.vad_speech_spin.setRange(0, 5000)
        self.vad_speech_spin.setSingleStep(50)
        self.vad_speech_spin.setSuffix(" ms")
        self.vad_speech_spin.valueChanged.connect(self._save)
        layout.addRow("最小语音时长:", self.vad_speech_spin)

        # 语音填充
        self.vad_pad_spin = QSpinBox()
        self.vad_pad_spin.setRange(0, 2000)
        self.vad_pad_spin.setSingleStep(50)
        self.vad_pad_spin.setSuffix(" ms")
        self.vad_pad_spin.valueChanged.connect(self._save)
        layout.addRow("语音填充:", self.vad_pad_spin)

        # 合并段落
        self.merge_check = QCheckBox("启用段落合并")
        self.merge_check.toggled.connect(self._save)
        layout.addRow("", self.merge_check)

        # 合并最大间隔
        self.merge_gap_spin = QSpinBox()
        self.merge_gap_spin.setRange(0, 5000)
        self.merge_gap_spin.setSingleStep(50)
        self.merge_gap_spin.setSuffix(" ms")
        self.merge_gap_spin.valueChanged.connect(self._save)
        layout.addRow("合并最大间隔:", self.merge_gap_spin)

        # 合并最大时长
        self.merge_dur_spin = QSpinBox()
        self.merge_dur_spin.setRange(1000, 60000)
        self.merge_dur_spin.setSingleStep(1000)
        self.merge_dur_spin.setSuffix(" ms")
        self.merge_dur_spin.valueChanged.connect(self._save)
        layout.addRow("合并最大时长:", self.merge_dur_spin)

        # 批处理
        self.batch_check = QCheckBox("启用批处理（加速但占更多显存）")
        self.batch_check.toggled.connect(self._save)
        layout.addRow("", self.batch_check)

        # 覆盖已有
        self.overwrite_check = QCheckBox("覆盖已有字幕文件")
        self.overwrite_check.toggled.connect(self._save)
        layout.addRow("", self.overwrite_check)

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
