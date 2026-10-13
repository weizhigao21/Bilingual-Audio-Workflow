# -*- coding: utf-8 -*-
"""音频混音配置面板（声笺风格设置分组卡片）。

修改配置后自动保存到 WorkflowConfig，并发 config_changed 信号。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget
)

from ..steps.audio_utils.panning import (
    CENTER_BIAS_DEFAULT, CENTER_BIAS_LABELS, SEPARATION_MAX,
    angles_from_config, describe, params_from_config,
)
from ..ui_theme import FONT_AUX, FONT_FAMILY
from .settings_group import PathRow, SegmentedControl, SettingsGroup

# 分离度读数的"最宽可能"样例（偏转 90° 时左右角度取到 180°/0°），用于给标签定最小宽度。
_READOUT_SAMPLE = "偏转 90° → 180° / 0°"


def _readout_min_width() -> int:
    """读数标签的最小宽度。

    读数必须**完整可见**，所以按最宽文案算一个像素宽度钉住它。不能给它套
    ``Ignored`` 尺寸策略——那样布局会忽略 sizeHint，而滑块（stretch=1）会把整行
    吃光，读数只剩一条 1px 的竖线（v2.2.6 离屏实测：滑块沟槽一直画到 x≈662，
    读数区仅剩 ~28px，文字被裁到只剩一个笔画）。
    """
    font = QFont(FONT_FAMILY)
    font.setPixelSize(FONT_AUX)
    return QFontMetrics(font).horizontalAdvance(_READOUT_SAMPLE) + 6


class MixerConfigPanel(QWidget):
    """音频混音配置面板：导出格式、音量、声道等。"""

    config_changed = pyqtSignal()

    # 质量档位 → (比特率, 采样率, 声道数)
    PRESET_PARAMS = {
        "low": ("128k", 44100, 2),
        "standard": ("192k", 44100, 2),
        "high": ("256k", 44100, 2),
        "extreme": ("320k", 48000, 2),
    }

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._build_ui()
        # 兼容旧引用：导出目录输入框
        self.output_edit = self.output_row.edit
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        # 导出
        export = SettingsGroup("导出", "输出位置、封装格式与音频质量。")
        self.output_row = PathRow("留空=输出到源文件同目录下的\"双语\"文件夹")
        self.output_row.text_changed.connect(self._save)
        export.add_wide_row("导出目录", "", self.output_row)

        self.format_combo = QComboBox()
        self.format_combo.addItem("MP4（视频，H.264）", "mp4")
        self.format_combo.addItem("MP3（音频）", "mp3")
        self.format_combo.addItem("M4A（AAC 音频）", "m4a")
        self.format_combo.addItem("WAV（无损音频）", "wav")
        self.format_combo.addItem("AAC（音频）", "aac")
        self.format_combo.currentIndexChanged.connect(self._save)
        export.add_row("导出格式", "", self.format_combo)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("标准（192k / 44.1kHz）", "standard")
        self.preset_combo.addItem("低（128k / 44.1kHz）", "low")
        self.preset_combo.addItem("高（256k / 44.1kHz）", "high")
        self.preset_combo.addItem("极致（320k / 48kHz）", "extreme")
        self.preset_combo.addItem("自定义", "custom")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        export.add_row("音频质量", "选择预设后自动联动比特率/采样率/声道",
                       self.preset_combo)

        self.bitrate_combo = QComboBox()
        for b in ("128k", "192k", "256k", "320k"):
            self.bitrate_combo.addItem(b, b)
        self.bitrate_combo.currentIndexChanged.connect(self._on_quality_param_changed)
        export.add_row("比特率", "仅自定义质量可用", self.bitrate_combo)

        self.sr_combo = QComboBox()
        self.sr_combo.addItem("44100 Hz", 44100)
        self.sr_combo.addItem("48000 Hz", 48000)
        self.sr_combo.currentIndexChanged.connect(self._on_quality_param_changed)
        export.add_row("采样率", "", self.sr_combo)

        self.channels_combo = QComboBox()
        self.channels_combo.addItem("立体声（2 声道）", 2)
        self.channels_combo.addItem("单声道（1 声道）", 1)
        self.channels_combo.currentIndexChanged.connect(self._on_quality_param_changed)
        export.add_row("声道", "", self.channels_combo)

        self.depth_combo = QComboBox()
        for d in (16, 24, 32):
            self.depth_combo.addItem(f"{d} bit", d)
        self.depth_combo.currentIndexChanged.connect(self._save)
        export.add_row("WAV 位深", "仅 WAV 输出生效", self.depth_combo)
        layout.addWidget(export)

        # 音量与对齐
        volume = SettingsGroup("音量与对齐", "配音相对原音频的电平与空间定位。")
        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(-30.0, 30.0)
        self.volume_spin.setSingleStep(0.5)
        self.volume_spin.setSuffix(" dB")
        self.volume_spin.valueChanged.connect(self._save)
        volume.add_row("配音音量", "在自动音量基础上的附加增益", self.volume_spin)

        self.auto_vol_combo = QComboBox()
        self.auto_vol_combo.addItem("匹配原音频整体 RMS", "fixed")
        self.auto_vol_combo.addItem("动态跟随原音频 RMS", "auto")
        self.auto_vol_combo.addItem("仅使用配音音量增益", "off")
        self.auto_vol_combo.currentIndexChanged.connect(self._save)
        volume.add_row("音量模式", "", self.auto_vol_combo)

        self.peak_combo = QComboBox()
        self.peak_combo.addItem("保真峰值保护（整段统一衰减）", "peak")
        self.peak_combo.addItem("软限幅（保留旧版听感）", "soft")
        self.peak_combo.setToolTip(
            "保真模式将超出 -1 dBFS 的样本峰值统一降低，保留动态和声像；"
            "尖峰可能让整段更轻。不是过采样真峰值限幅。")
        self.peak_combo.currentIndexChanged.connect(self._save)
        volume.add_row("峰值保护", "", self.peak_combo)

        self.channel_detect_check = QCheckBox("启用声道检测（根据原音频位置自动定位）")
        self.channel_detect_check.toggled.connect(self._save)
        volume.add_widget_row(self.channel_detect_check)

        # 声道角度：一个「分离度」滑块 + 一个「原音居中时」三选一。
        #
        # 旧版是三个各自独立的 0–180° 数字框（channel_map.left/both/right），
        # 用户实际总把它们调成 45–135° 的"软声像"——等于**手动把三个参数收敛成
        # 了一个**；而 0°/180° 是单侧增益为 0（对侧完全听不到），检测器一旦判错
        # 人声就整段消失在一边，这个区间本来就不该暴露给人手填。
        # 参数语义与换算见 steps/audio_utils/panning.py。
        chan_box = QHBoxLayout()
        chan_box.setSpacing(10)
        self.sep_slider = QSlider(Qt.Orientation.Horizontal)
        self.sep_slider.setRange(0, int(SEPARATION_MAX))
        self.sep_slider.setSingleStep(1)
        self.sep_slider.setPageStep(15)
        self.sep_slider.setTickInterval(15)
        self.sep_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.sep_slider.setToolTip(
            "检测到原音人声偏左/偏右时，配音放到**对侧**的偏转角。\n"
            "0° = 始终居中；90° = 完全放到对侧（0°/180°，对侧完全听不到）。")
        self.sep_slider.valueChanged.connect(self._on_separation_changed)
        chan_box.addWidget(self.sep_slider, 1)
        self.sep_readout = QLabel()
        self.sep_readout.setProperty("role", "secondary")
        self.sep_readout.setMinimumWidth(_readout_min_width())
        self.sep_readout.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        chan_box.addWidget(self.sep_readout)
        chan_widget = QWidget()
        chan_widget.setLayout(chan_box)
        volume.add_wide_row(
            "配音分离度", "原音人声偏左/偏右时，配音放到对侧的幅度", chan_widget)

        self.center_segment = SegmentedControl([
            (CENTER_BIAS_LABELS[bias], bias) for bias in (-30, 0, 30)
        ])
        self.center_segment.setToolTip(
            "原音人声在中间、或检测不出声道时的配音落点。")
        self.center_segment.current_changed.connect(lambda _v: self._save())
        volume.add_row(
            "原音居中时", "原音人声在中间、或检测不出声道时的配音位置",
            self.center_segment)

        self.align_check = QCheckBox("起始对齐（对齐配音起拍点到原音频）")
        self.align_check.toggled.connect(self._save)
        volume.add_widget_row(self.align_check)

        self.content_align_check = QCheckBox("内容对齐（用原音频人声起始点纠正时间戳）")
        self.content_align_check.toggled.connect(self._save)
        volume.add_widget_row(self.content_align_check)
        layout.addWidget(volume)

        # 输出选项
        output = SettingsGroup("输出选项", "命名规则、跳过策略与作品信息。")
        self.suffix_check = QCheckBox("输出文件添加 _mixed 后缀")
        self.suffix_check.toggled.connect(self._save)
        output.add_widget_row(self.suffix_check)

        self.folder_prefix_check = QCheckBox("文件夹导入混音后重命名源文件夹（添加\"双语-\"前缀）")
        self.folder_prefix_check.toggled.connect(self._save)
        output.add_widget_row(self.folder_prefix_check)

        self.output_prefix_check = QCheckBox("输出目录带源文件夹名前缀（双语 → 双语-文件夹名，仅文件夹导入）")
        self.output_prefix_check.toggled.connect(self._save)
        output.add_widget_row(self.output_prefix_check)

        self.skip_check = QCheckBox("跳过已存在的输出文件")
        self.skip_check.toggled.connect(self._save)
        output.add_widget_row(self.skip_check)

        metadata_row = QHBoxLayout()
        metadata_row.setSpacing(8)
        self.metadata_check = QCheckBox("导出时写入作品信息")
        self.metadata_check.setToolTip("控制标题、艺术家、专辑、备注及版本标签；工作区任务记录仍会保存混音版本。")
        self.metadata_check.toggled.connect(self._save)
        metadata_btn = QPushButton("编辑信息…")
        metadata_btn.setToolTip("选择导出时写入的字段并设置内容")
        metadata_btn.clicked.connect(self._edit_metadata_settings)
        metadata_row.addWidget(self.metadata_check)
        metadata_row.addWidget(metadata_btn)
        metadata_row.addStretch(1)
        metadata_widget = QWidget()
        metadata_widget.setLayout(metadata_row)
        output.add_widget_row(metadata_widget)
        layout.addWidget(output)

        # 高级
        advanced = SettingsGroup("高级", "重采样质量、线程与内存策略。")
        self.resample_check = QCheckBox("高质量重采样（采样率不同时更慢）")
        self.resample_check.setToolTip(
            "配音和输出采样率转换使用 FFmpeg 64 点滤波；关闭后配音使用快速转换。"
            "视频画面直接复制，音频编码不依赖 GPU。")
        self.resample_check.toggled.connect(self._save)
        advanced.add_widget_row(self.resample_check)

        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, 32)
        self.thread_spin.setSuffix(" 线程")
        self.thread_spin.valueChanged.connect(self._save)
        advanced.add_row("线程数", "FFmpeg 编码线程", self.thread_spin)

        self.streaming_spin = QSpinBox()
        self.streaming_spin.setRange(0, 240)
        self.streaming_spin.setSuffix(" 分钟")
        self.streaming_spin.setSpecialValueText("始终使用磁盘缓冲")
        self.streaming_spin.setToolTip(
            "达到此时长后使用磁盘缓冲混音，降低内存占用，"
            "但需要更多临时磁盘空间。设为 0 可始终启用。")
        self.streaming_spin.valueChanged.connect(self._save)
        advanced.add_row("长音频磁盘缓冲", "超过该时长走磁盘缓冲混音", self.streaming_spin)

        self.batch_parallel_check = QCheckBox("批量执行时多任务并行混音")
        self.batch_parallel_check.toggled.connect(self._save)
        advanced.add_widget_row(self.batch_parallel_check)
        layout.addWidget(advanced)
        layout.addStretch(1)

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

    def _refresh_separation_readout(self, value: int):
        """把分离度换算成实际混音角度显示出来（滑块位置本身没有意义，角度才有）。"""
        bias = self.center_segment.current_data()
        if bias is None:
            bias = CENTER_BIAS_DEFAULT
        angles = angles_from_config({
            "channel_separation": value, "channel_center_bias": bias})
        self.sep_readout.setText(
            f"偏转 {value}° → {angles['left']:.0f}° / {angles['right']:.0f}°")
        self.sep_readout.setToolTip(describe(value, bias))

    def _on_separation_changed(self, value: int):
        self._refresh_separation_readout(value)
        self._save()

    def _load_values(self):
        cfg = self.config.mixer_cfg
        widgets = [self.output_row.edit, self.format_combo, self.preset_combo,
                   self.bitrate_combo, self.sr_combo, self.channels_combo,
                   self.depth_combo, self.volume_spin,
                   self.auto_vol_combo, self.peak_combo, self.channel_detect_check,
                   self.sep_slider, self.center_segment,
                   self.align_check, self.content_align_check, self.resample_check,
                   self.suffix_check, self.folder_prefix_check,
                   self.output_prefix_check,
                   self.skip_check, self.thread_spin, self.streaming_spin,
                   self.batch_parallel_check, self.metadata_check]
        for w in widgets:
            w.blockSignals(True)
        try:
            # 导出目录
            self.output_row.setText(cfg.get("output_folder", ""))
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
            # 声道角度（新版：分离度 + 居中偏置；旧 channel_map 三角度自动迁移）
            sep_value, bias_value = params_from_config(cfg)
            self.sep_slider.setValue(sep_value)
            self.center_segment.set_current_data(bias_value)
            self._refresh_separation_readout(sep_value)
            # 其他选项
            self.align_check.setChecked(cfg.get("align_onset", True))
            self.content_align_check.setChecked(cfg.get("content_alignment", False))
            self.resample_check.setChecked(cfg.get("high_quality_resample", True))
            self.suffix_check.setChecked(cfg.get("add_suffix", True))
            self.folder_prefix_check.setChecked(cfg.get("folder_prefix", True))
            self.output_prefix_check.setChecked(cfg.get("output_folder_prefix", False))
            self.skip_check.setChecked(cfg.get("skip_existing", True))
            self.metadata_check.setChecked(cfg.get("metadata_enabled", True))
            self.thread_spin.setValue(cfg.get("thread_count", 4))
            self.streaming_spin.setValue(cfg.get("streaming_threshold_minutes", 20))
            self.batch_parallel_check.setChecked(cfg.get("enable_batch_parallel", True))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _save(self):
        cfg = self.config.mixer_cfg
        cfg["output_folder"] = self.output_row.text().strip()
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
        # 声道角度：保存两个语义参数。混音侧（step3 / stream_mixer）用
        # angles_from_config() 把它们换算成 channel_map——面板与混音共用同一
        # 个函数，避免"面板上看到的"和"实际混音用的"不是一套。
        cfg["channel_separation"] = self.sep_slider.value()
        cfg["channel_center_bias"] = self.center_segment.current_data()
        cfg["align_onset"] = self.align_check.isChecked()
        cfg["content_alignment"] = self.content_align_check.isChecked()
        cfg["high_quality_resample"] = self.resample_check.isChecked()
        cfg["add_suffix"] = self.suffix_check.isChecked()
        cfg["folder_prefix"] = self.folder_prefix_check.isChecked()
        cfg["output_folder_prefix"] = self.output_prefix_check.isChecked()
        cfg["skip_existing"] = self.skip_check.isChecked()
        cfg["metadata_enabled"] = self.metadata_check.isChecked()
        cfg["thread_count"] = self.thread_spin.value()
        cfg["streaming_threshold_minutes"] = self.streaming_spin.value()
        cfg["enable_batch_parallel"] = self.batch_parallel_check.isChecked()
        self.config.save()
        self.config_changed.emit()

    def _edit_metadata_settings(self):
        from .mix_metadata_settings import MixMetadataSettingsDialog
        dialog = MixMetadataSettingsDialog(self.config, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.config_changed.emit()

    def _on_browse_output(self):
        from PyQt6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if folder:
            self.output_row.setText(folder)
