# -*- coding: utf-8 -*-
"""音频混音配置面板。"""
from PyQt6.QtWidgets import (
    QGroupBox, QFormLayout, QHBoxLayout, QWidget, QLabel, QLineEdit, QPushButton,
    QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox
)
from PyQt6.QtCore import pyqtSignal


class MixerConfigPanel(QGroupBox):
    """音频混音配置面板：导出格式、音量、声道等。

    修改配置后自动保存到 WorkflowConfig，并发 config_changed 信号。
    """
    config_changed = pyqtSignal()

    # 质量档位 → (比特率, 采样率, 声道数)
    PRESET_PARAMS = {
        "low": ("128k", 44100, 2),
        "standard": ("192k", 44100, 2),
        "high": ("256k", 44100, 2),
        "extreme": ("320k", 48000, 2),
    }

    def __init__(self, config, parent=None):
        super().__init__("混音配置")
        self.config = config
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QFormLayout(self)

        # 导出目录
        out_box = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("留空=输出到源文件同目录下的\"双语\"文件夹")
        self.output_edit.textChanged.connect(self._save)
        out_box.addWidget(self.output_edit)
        browse_btn = QPushButton("选择...")
        browse_btn.clicked.connect(self._on_browse_output)
        out_box.addWidget(browse_btn)
        out_widget = QWidget()
        out_widget.setLayout(out_box)
        layout.addRow("导出目录:", out_widget)

        # 导出格式
        self.format_combo = QComboBox()
        self.format_combo.addItem("MP4（视频，H.264）", "mp4")
        self.format_combo.addItem("MP3（音频）", "mp3")
        self.format_combo.addItem("M4A（AAC 音频）", "m4a")
        self.format_combo.addItem("WAV（无损音频）", "wav")
        self.format_combo.addItem("AAC（音频）", "aac")
        self.format_combo.currentIndexChanged.connect(self._save)
        layout.addRow("导出格式:", self.format_combo)

        # 导出音频质量（档位预设）
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("标准（192k / 44.1kHz）", "standard")
        self.preset_combo.addItem("低（128k / 44.1kHz）", "low")
        self.preset_combo.addItem("高（256k / 44.1kHz）", "high")
        self.preset_combo.addItem("极致（320k / 48kHz）", "extreme")
        self.preset_combo.addItem("自定义", "custom")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        layout.addRow("音频质量:", self.preset_combo)

        # 高级参数（选"自定义"或手动改动时启用）
        self.bitrate_combo = QComboBox()
        for b in ("128k", "192k", "256k", "320k"):
            self.bitrate_combo.addItem(b, b)
        self.bitrate_combo.currentIndexChanged.connect(self._on_quality_param_changed)
        layout.addRow("比特率:", self.bitrate_combo)

        self.sr_combo = QComboBox()
        self.sr_combo.addItem("44100 Hz", 44100)
        self.sr_combo.addItem("48000 Hz", 48000)
        self.sr_combo.currentIndexChanged.connect(self._on_quality_param_changed)
        layout.addRow("采样率:", self.sr_combo)

        self.channels_combo = QComboBox()
        self.channels_combo.addItem("立体声（2 声道）", 2)
        self.channels_combo.addItem("单声道（1 声道）", 1)
        self.channels_combo.currentIndexChanged.connect(self._on_quality_param_changed)
        layout.addRow("声道:", self.channels_combo)

        self.depth_combo = QComboBox()
        for d in (16, 24, 32):
            self.depth_combo.addItem(f"{d} bit", d)
        self.depth_combo.currentIndexChanged.connect(self._save)
        layout.addRow("WAV 位深:", self.depth_combo)

        # 音量调整（dB）
        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(-30.0, 30.0)
        self.volume_spin.setSingleStep(0.5)
        self.volume_spin.setSuffix(" dB")
        self.volume_spin.valueChanged.connect(self._save)
        layout.addRow("配音音量:", self.volume_spin)

        # 自动音量模式
        self.auto_vol_combo = QComboBox()
        self.auto_vol_combo.addItem("匹配原音频整体 RMS", "fixed")
        self.auto_vol_combo.addItem("动态跟随原音频 RMS", "auto")
        self.auto_vol_combo.addItem("仅使用配音音量增益", "off")
        self.auto_vol_combo.currentIndexChanged.connect(self._save)
        layout.addRow("音量模式:", self.auto_vol_combo)

        self.peak_combo = QComboBox()
        self.peak_combo.addItem("保真峰值保护（整段统一衰减）", "peak")
        self.peak_combo.addItem("软限幅（保留旧版听感）", "soft")
        self.peak_combo.setToolTip("保真模式将超出 -1 dBFS 的样本峰值统一降低，保留动态和声像；尖峰可能让整段更轻。不是过采样真峰值限幅。")
        self.peak_combo.currentIndexChanged.connect(self._save)
        layout.addRow("峰值保护:", self.peak_combo)

        # 声道检测
        self.channel_detect_check = QCheckBox("启用声道检测（根据原音频位置自动定位）")
        self.channel_detect_check.toggled.connect(self._save)
        layout.addRow("", self.channel_detect_check)

        # 声道映射角度
        chan_box = QHBoxLayout()
        chan_box.addWidget(QLabel("左:"))
        self.angle_left = QSpinBox()
        self.angle_left.setRange(0, 180)
        self.angle_left.valueChanged.connect(self._save)
        chan_box.addWidget(self.angle_left)
        chan_box.addWidget(QLabel("中:"))
        self.angle_both = QSpinBox()
        self.angle_both.setRange(0, 180)
        self.angle_both.valueChanged.connect(self._save)
        chan_box.addWidget(self.angle_both)
        chan_box.addWidget(QLabel("右:"))
        self.angle_right = QSpinBox()
        self.angle_right.setRange(0, 180)
        self.angle_right.valueChanged.connect(self._save)
        chan_box.addWidget(self.angle_right)
        chan_widget = QWidget()
        chan_widget.setLayout(chan_box)
        layout.addRow("声道角度:", chan_widget)

        # 起始对齐
        self.align_check = QCheckBox("起始对齐（对齐配音起拍点到原音频）")
        self.align_check.toggled.connect(self._save)
        layout.addRow("", self.align_check)

        # 内容对齐
        self.content_align_check = QCheckBox("内容对齐（用原音频人声起始点纠正时间戳）")
        self.content_align_check.toggled.connect(self._save)
        layout.addRow("", self.content_align_check)

        self.resample_check = QCheckBox("高质量重采样（采样率不同时更慢）")
        self.resample_check.setToolTip("配音和输出采样率转换使用 FFmpeg 64 点滤波；关闭后配音使用快速转换。视频画面直接复制，音频编码不依赖 GPU。")
        self.resample_check.toggled.connect(self._save)
        layout.addRow("", self.resample_check)

        # 添加后缀
        self.suffix_check = QCheckBox("输出文件添加 _mixed 后缀")
        self.suffix_check.toggled.connect(self._save)
        layout.addRow("", self.suffix_check)

        # 文件夹前缀
        self.folder_prefix_check = QCheckBox("文件夹导入混音后重命名源文件夹（添加\"双语-\"前缀）")
        self.folder_prefix_check.toggled.connect(self._save)
        layout.addRow("", self.folder_prefix_check)

        # 输出目录带源文件夹名前缀
        self.output_prefix_check = QCheckBox("输出目录带源文件夹名前缀（双语 → 双语-文件夹名，仅文件夹导入）")
        self.output_prefix_check.toggled.connect(self._save)
        layout.addRow("", self.output_prefix_check)

        # 跳过已存在
        self.skip_check = QCheckBox("跳过已存在的输出文件")
        self.skip_check.toggled.connect(self._save)
        layout.addRow("", self.skip_check)

        # 线程数
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, 32)
        self.thread_spin.setSuffix(" 线程")
        self.thread_spin.valueChanged.connect(self._save)
        layout.addRow("线程数:", self.thread_spin)

        self.streaming_spin = QSpinBox()
        self.streaming_spin.setRange(0, 240)
        self.streaming_spin.setSuffix(" 分钟")
        self.streaming_spin.setSpecialValueText("始终使用磁盘缓冲")
        self.streaming_spin.setToolTip("达到此时长后使用磁盘缓冲混音，降低内存占用，但需要更多临时磁盘空间。设为 0 可始终启用。")
        self.streaming_spin.valueChanged.connect(self._save)
        layout.addRow("长音频磁盘缓冲:", self.streaming_spin)

        # 批量并行
        self.batch_parallel_check = QCheckBox("批量执行时多任务并行混音")
        self.batch_parallel_check.toggled.connect(self._save)
        layout.addRow("", self.batch_parallel_check)

    def _on_preset_changed(self):
        """档位预设切换：联动参数控件并锁定/解锁。"""
        preset = self.preset_combo.currentData()
        if preset in self.PRESET_PARAMS:
            br, sr, ch = self.PRESET_PARAMS[preset]
            for w, val in ((self.bitrate_combo, br),
                           (self.sr_combo, sr),
                           (self.channels_combo, ch)):
                idx = w.findData(val)
                if idx >= 0:
                    w.blockSignals(True)
                    w.setCurrentIndex(idx)
                    w.blockSignals(False)
            self.bitrate_combo.setEnabled(False)
            self.sr_combo.setEnabled(False)
            self.channels_combo.setEnabled(False)
        else:
            self.bitrate_combo.setEnabled(True)
            self.sr_combo.setEnabled(True)
            self.channels_combo.setEnabled(True)
        self._save()

    def _on_quality_param_changed(self):
        """手动修改任意参数后，档位自动切换为"自定义"。"""
        preset = self.preset_combo.currentData()
        if preset in self.PRESET_PARAMS:
            idx = self.preset_combo.findData("custom")
            if idx >= 0:
                self.preset_combo.blockSignals(True)
                self.preset_combo.setCurrentIndex(idx)
                self.preset_combo.blockSignals(False)
            self.bitrate_combo.setEnabled(True)
            self.sr_combo.setEnabled(True)
            self.channels_combo.setEnabled(True)
        self._save()

    def _load_values(self):
        cfg = self.config.mixer_cfg
        widgets = [self.output_edit, self.format_combo, self.preset_combo,
                   self.bitrate_combo, self.sr_combo, self.channels_combo,
                   self.depth_combo, self.volume_spin,
                   self.auto_vol_combo, self.peak_combo, self.channel_detect_check,
                   self.angle_left, self.angle_both, self.angle_right,
                   self.align_check, self.content_align_check, self.resample_check,
                   self.suffix_check, self.folder_prefix_check,
                   self.output_prefix_check,
                   self.skip_check, self.thread_spin, self.streaming_spin,
                   self.batch_parallel_check]
        for w in widgets:
            w.blockSignals(True)
        try:
            # 导出目录
            self.output_edit.setText(cfg.get("output_folder", ""))
            # 导出格式
            fmt = cfg.get("output_format", "mp4")
            idx = self.format_combo.findData(fmt)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
            # 音频质量
            bitrate = cfg.get("audio_bitrate", "192k")
            sample_rate = cfg.get("audio_sample_rate", 44100)
            channels = cfg.get("audio_channels", 2)
            for w, val in ((self.bitrate_combo, bitrate),
                           (self.sr_combo, sample_rate),
                           (self.channels_combo, channels),
                           (self.depth_combo, cfg.get("wav_bit_depth", 16))):
                idx = w.findData(val)
                if idx >= 0:
                    w.setCurrentIndex(idx)
            # 根据参数匹配档位，不匹配则显示"自定义"
            preset = "custom"
            for p, params in self.PRESET_PARAMS.items():
                if params == (bitrate, sample_rate, channels):
                    preset = p
                    break
            idx = self.preset_combo.findData(preset)
            if idx >= 0:
                self.preset_combo.setCurrentIndex(idx)
            is_custom = preset == "custom"
            self.bitrate_combo.setEnabled(is_custom)
            self.sr_combo.setEnabled(is_custom)
            self.channels_combo.setEnabled(is_custom)
            # 音量
            self.volume_spin.setValue(cfg.get("volume_db", 0.0))
            # 自动音量
            av = cfg.get("auto_volume", "fixed")
            idx = self.auto_vol_combo.findData(av)
            if idx >= 0:
                self.auto_vol_combo.setCurrentIndex(idx)
            self.peak_combo.setCurrentIndex(max(0, self.peak_combo.findData(cfg.get("peak_mode", "peak"))))
            # 声道检测
            self.channel_detect_check.setChecked(cfg.get("channel_detect", True))
            # 声道角度
            cm = cfg.get("channel_map", {"left": 155, "right": 25, "both": 135})
            self.angle_left.setValue(cm.get("left", 155))
            self.angle_both.setValue(cm.get("both", 135))
            self.angle_right.setValue(cm.get("right", 25))
            # 其他选项
            self.align_check.setChecked(cfg.get("align_onset", True))
            self.content_align_check.setChecked(cfg.get("content_alignment", False))
            self.resample_check.setChecked(cfg.get("high_quality_resample", True))
            self.suffix_check.setChecked(cfg.get("add_suffix", True))
            self.folder_prefix_check.setChecked(cfg.get("folder_prefix", True))
            self.output_prefix_check.setChecked(cfg.get("output_folder_prefix", False))
            self.skip_check.setChecked(cfg.get("skip_existing", True))
            self.thread_spin.setValue(cfg.get("thread_count", 4))
            self.streaming_spin.setValue(cfg.get("streaming_threshold_minutes", 20))
            self.batch_parallel_check.setChecked(cfg.get("enable_batch_parallel", True))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _save(self):
        cfg = self.config.mixer_cfg
        cfg["output_folder"] = self.output_edit.text().strip()
        cfg["output_format"] = self.format_combo.currentData()
        cfg["export_preset"] = self.preset_combo.currentData()
        cfg["audio_bitrate"] = self.bitrate_combo.currentData()
        cfg["audio_sample_rate"] = self.sr_combo.currentData()
        cfg["audio_channels"] = self.channels_combo.currentData()
        cfg["wav_bit_depth"] = self.depth_combo.currentData()
        cfg["volume_db"] = self.volume_spin.value()
        cfg["auto_volume"] = self.auto_vol_combo.currentData()
        cfg["peak_mode"] = self.peak_combo.currentData()
        cfg["channel_detect"] = self.channel_detect_check.isChecked()
        cfg["channel_map"] = {
            "left": self.angle_left.value(),
            "both": self.angle_both.value(),
            "right": self.angle_right.value(),
        }
        cfg["align_onset"] = self.align_check.isChecked()
        cfg["content_alignment"] = self.content_align_check.isChecked()
        cfg["high_quality_resample"] = self.resample_check.isChecked()
        cfg["add_suffix"] = self.suffix_check.isChecked()
        cfg["folder_prefix"] = self.folder_prefix_check.isChecked()
        cfg["output_folder_prefix"] = self.output_prefix_check.isChecked()
        cfg["skip_existing"] = self.skip_check.isChecked()
        cfg["thread_count"] = self.thread_spin.value()
        cfg["streaming_threshold_minutes"] = self.streaming_spin.value()
        cfg["enable_batch_parallel"] = self.batch_parallel_check.isChecked()
        self.config.save()
        self.config_changed.emit()

    def _on_browse_output(self):
        from PyQt6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if folder:
            self.output_edit.setText(folder)
