# -*- coding: utf-8 -*-
"""声像（pan）参数与声道检测守卫。

覆盖两件事：
1. ``panning.py`` 的「分离度 + 居中偏置」→ 角度换算，以及旧 ``channel_map``
   三角度配置的迁移（迁移必须**精确保留** left/right 角度，否则老用户一升级
   听感就变）。
2. ``detection.detect_voice_channel`` 的合成音频行为——重点是旧实现的三个
   真实缺陷：9% 不平衡度就误判、整段音乐被算进人声、没有置信度。
"""
import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from src.steps.audio_utils import (
    CENTER_BIAS_CHOICES, SEPARATION_MAX, angles_from_config, config_from_legacy,
    describe, params_from_config,
)
from src.steps.audio_utils.detection import (
    FRAME_LEN_MAX, FRAME_LEN_MIN, PAN_IMBALANCE_THRESHOLD, TARGET_FRAME_MS,
    _frame_params, _voice_band_frame_energies, detect_voice_channel,
)

SR = 44100
DURATION = 3.0


def _tone(freq: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(DURATION * SR)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def _band_noise(amp: float, lo: float, hi: float) -> np.ndarray:
    """带限噪声（FFT 置零法），用来模拟"音乐/嘶声"等非人声内容。"""
    n = int(DURATION * SR)
    noise = np.random.default_rng(7).standard_normal(n)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    spectrum[(freqs < lo) | (freqs > hi)] = 0
    out = np.fft.irfft(spectrum, n)
    peak = float(np.max(np.abs(out)))
    return amp * out / peak if peak > 0 else out


def _stereo(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.column_stack([left, right]).astype(np.float32)


def _voice(side: str) -> np.ndarray:
    """人声（300 Hz + 1200 Hz 两个正弦，落在 85–3000 Hz 人声频段内）。"""
    silence = np.zeros(int(DURATION * SR))
    speech = _tone(300.0) + _tone(1200.0)
    return speech if side == "left" else (silence if side == "right" else speech)


class PanningParamsTests(unittest.TestCase):
    """「分离度 + 居中偏置」→ 角度换算。

    分离度用**度数**（0–90）而不是百分比：百分比取整会带来最多 0.9° 偏差，
    旧配置迁移就不再无损（用户机器上现存的就是旧三角度配置）。
    """

    def test_new_keys_take_priority(self):
        cfg = {"channel_separation": 20, "channel_center_bias": -30,
               "channel_map": {"left": 180, "both": 0, "right": 0}}
        self.assertEqual(params_from_config(cfg), (20, -30))

    def test_defaults_when_config_empty(self):
        # 完全没有声道配置 → 新默认值（不从旧默认值反推）
        self.assertEqual(params_from_config({}), (45, 30))
        self.assertEqual(params_from_config(None), (45, 30))
        self.assertEqual(params_from_config({"channel_map": {}}), (45, 30))

    def test_angles_follow_the_pan_formula(self):
        # 0° = 极左、90° = 正中、180° = 极右（mixing.py: cos/sin(θ/2)）
        angles = angles_from_config({"channel_separation": 0,
                                     "channel_center_bias": 0})
        self.assertEqual(angles, {"left": 90.0, "right": 90.0, "both": 90.0})

        angles = angles_from_config({"channel_separation": int(SEPARATION_MAX),
                                     "channel_center_bias": 30})
        self.assertEqual(angles["left"], 180.0)
        self.assertEqual(angles["right"], 0.0)
        self.assertEqual(angles["both"], 120.0)

    def test_separation_is_clamped(self):
        self.assertEqual(params_from_config({"channel_separation": -20})[0], 0)
        self.assertEqual(
            params_from_config({"channel_separation": 250})[0], int(SEPARATION_MAX))

    def test_bias_snaps_to_nearest_choice(self):
        for raw, expected in ((-90, -30), (-20, -30), (-10, 0), (10, 0),
                              (20, 30), (90, 30)):
            self.assertEqual(
                params_from_config({"channel_center_bias": raw})[1], expected,
                f"偏置 {raw} 应就近取档到 {expected}")

    def test_all_bias_choices_are_representable(self):
        for bias in CENTER_BIAS_CHOICES:
            _, got = params_from_config({"channel_center_bias": bias})
            self.assertEqual(got, bias)


class LegacyMigrationTests(unittest.TestCase):
    """旧 ``channel_map`` 三角度配置必须无损迁移。

    用户机器上现存的就是这套配置（如 左 135 / 中 110 / 右 45）。迁移要是粗略
    取整，一升级听感就变——这是最容易被忽略、后果最直接的回归。
    """

    def test_left_and_right_angles_are_preserved_exactly(self):
        legacy = {"left": 135, "both": 110, "right": 45}
        angles = angles_from_config({"channel_map": legacy})
        self.assertEqual(angles["left"], 135.0, "原音在左 → 配音角度必须仍是 135°")
        self.assertEqual(angles["right"], 45.0, "原音在右 → 配音角度必须仍是 45°")

    def test_both_angle_snaps_to_nearest_bias(self):
        # 110° 距 90° 为 +20°，就近取档到 +30°（偏右）
        angles = angles_from_config(
            {"channel_map": {"left": 135, "both": 110, "right": 45}})
        self.assertEqual(angles["both"], 120.0)

    def test_shipped_defaults_migrate_exactly(self):
        """旧出厂默认 155/135/25 → 分离度 65，角度必须一模一样。"""
        legacy = {"left": 155, "both": 135, "right": 25}
        separation, bias = params_from_config({"channel_map": legacy})
        self.assertEqual((separation, bias), (65, 30))
        angles = angles_from_config({"channel_map": legacy})
        self.assertEqual(angles["left"], 155.0)
        self.assertEqual(angles["right"], 25.0)

    def test_config_from_legacy_round_trip(self):
        legacy = {"left": 150, "both": 120, "right": 30}
        new_keys = config_from_legacy(legacy)
        self.assertEqual(set(new_keys),
                         {"channel_separation", "channel_center_bias"})
        again = angles_from_config(dict(new_keys))
        self.assertEqual(again["left"], 150.0)
        self.assertEqual(again["right"], 30.0)

    def test_empty_legacy_map_falls_back_to_new_defaults(self):
        self.assertEqual(params_from_config({"channel_map": {}}), (45, 30))

    def test_describe_reads_like_a_sentence(self):
        text = describe(50, 30)
        self.assertIn("140°", text)
        self.assertIn("40°", text)
        self.assertIn("偏右", text)
        self.assertIn("始终居中", describe(0, 0))


class VoiceChannelDetectionTests(unittest.TestCase):
    """声道检测：合成音频下的行为守卫。

    三个用例对应旧实现的真实缺陷（用户反馈"之前的角度检测器感觉不是特别好用"）：
    - 10% 不平衡度被误判成偏侧（旧阈值 ``left > right*1.2`` 只相当于 9%）
    - 人声在左、右侧却是大声乐时被带偏（旧实现整段求和，没有语音门控）
    - 右声道整体更响但人声居中时误判
    """

    @classmethod
    def setUpClass(cls):
        cls.has_numpy = np is not None

    def setUp(self):
        if not self.has_numpy:
            self.skipTest("numpy 不可用")

    def test_voice_on_left(self):
        label, conf = detect_voice_channel(_stereo(_voice("left"), _voice("right")), SR)
        self.assertEqual(label, "left")
        self.assertGreaterEqual(conf, 1.0)

    def test_voice_on_right(self):
        label, conf = detect_voice_channel(_stereo(_voice("right"), _voice("left")), SR)
        self.assertEqual(label, "right")
        self.assertGreaterEqual(conf, 1.0)

    def test_centered_voice_is_both(self):
        label, conf = detect_voice_channel(_stereo(_voice("left"), _voice("left")), SR)
        self.assertEqual(label, "both")
        self.assertEqual(conf, 0.0, "没有可用的侧向信息时置信度必须为 0")

    def test_ten_percent_imbalance_is_not_a_pan(self):
        """旧实现在这里会返回 'left'——背景音乐差一成就把配音甩到对侧。"""
        speech = _tone(300.0)
        label, _ = detect_voice_channel(_stereo(speech, speech * 0.90), SR)
        self.assertEqual(label, "both")

    def test_threshold_itself_is_not_triggered(self):
        speech = _tone(300.0)
        # 不平衡度恰好等于阈值时应判"有侧向"（>= 语义）
        ratio = (1 - (1 - PAN_IMBALANCE_THRESHOLD) / (1 + PAN_IMBALANCE_THRESHOLD))
        label, conf = detect_voice_channel(_stereo(speech, speech * ratio), SR)
        self.assertIn(label, ("left", "right"))
        self.assertGreater(conf, 0.0)

    def test_loud_out_of_band_content_does_not_steer_the_verdict(self):
        """人声在左、右侧是带外大噪声：85–3000 Hz 带通必须把它挡在外面。"""
        label, conf = detect_voice_channel(
            _stereo(_voice("left"), _band_noise(0.9, 3000.0, 15000.0)), SR)
        self.assertEqual(label, "left")
        self.assertGreaterEqual(conf, 1.0)

    def test_ultrasonic_tone_is_not_aliased_into_the_voice_band(self):
        """21 kHz 纯音不得被折返回人声频段（旧实现抽点降采样没有抗混叠）。

        右声道给到 1.5 倍幅度：一旦发生别名，折返回 ~1 kHz 的能量会反过来压过
        左声道人声，判定从 'left' 翻成 'right'——守卫必须抓住这种翻转。
        """
        n = int(DURATION * SR)
        ultrasonic = 1.5 * np.sin(2 * np.pi * 21000.0 * np.arange(n) / SR)
        label, conf = detect_voice_channel(
            _stereo(_voice("left"), ultrasonic.astype(np.float32)), SR)
        self.assertEqual(label, "left")
        self.assertGreaterEqual(conf, 1.0)

    def test_voice_band_energies_ignore_out_of_band_tones(self):
        """4 kHz 以上的纯音，人声频段能量必须严格为 0。

        这一条同时守住"带通"和"不产生别名"：21 kHz 在 44.1 kHz 下若做抽点降采样
        （无抗混叠），会折返回 ~1 kHz，能量就不再是 0——旧实现正是这么干的。
        低频端（40/60 Hz）受 Hann 窗频谱泄漏影响会有 13%–41% 的残余，这里不纳入
        断言（隆隆声通常双声道对称，会自行抵消，见 detect_voice_channel 文档）。
        """
        n = int(DURATION * SR)
        t = np.arange(n) / SR
        for freq in (4000.0, 5000.0, 12000.0, 21050.0):
            tone = (0.8 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
            energy = float(_voice_band_frame_energies(tone, SR).sum())
            # 参照：同幅度的 1 kHz 纯音能量约 1.6e7，这里卡到 1e-3 已低 10 个量级，
            # 只是给浮点残余留余量；一旦发生别名，能量会跳到 1e6 量级。
            self.assertLess(energy, 1e-3,
                            f"{freq} Hz 纯音落进了人声频段（别名或带通失效）：{energy}")

    def test_louder_right_channel_with_centered_voice_is_both(self):
        label, _ = detect_voice_channel(
            _stereo(_tone(300.0), _tone(300.0) + _band_noise(0.5, 3000.0, 15000.0)),
            SR)
        self.assertEqual(label, "both")

    def test_silence_is_both(self):
        label, conf = detect_voice_channel(
            _stereo(np.zeros(int(DURATION * SR)), np.zeros(int(DURATION * SR))), SR)
        self.assertEqual(label, "both")
        self.assertEqual(conf, 0.0)

    def test_frame_length_stays_in_the_speech_range(self):
        """帧长必须是语音分析量级（20–40 ms），不能退回旧的 ~93 ms。"""
        for sr in (8000, 16000, 22050, 44100, 48000, 96000):
            frame_len, hop_len = _frame_params(sr)
            frame_ms = frame_len / sr * 1000
            self.assertLessEqual(
                frame_ms, 40,
                f"{sr} Hz 下帧长 {frame_ms:.0f} ms 太粗（旧实现约 93 ms）")
            self.assertLess(hop_len, frame_len)
            self.assertGreaterEqual(frame_len, FRAME_LEN_MIN)
            self.assertLessEqual(frame_len, FRAME_LEN_MAX)

    def test_target_frame_ms_is_the_speech_standard(self):
        self.assertTrue(15.0 <= TARGET_FRAME_MS <= 40.0)

    def test_threshold_is_far_above_the_old_equivalent(self):
        # 旧写法 left > right * 1.2 只相当于不平衡度 0.091
        self.assertGreaterEqual(PAN_IMBALANCE_THRESHOLD, 0.25)
        self.assertLess(PAN_IMBALANCE_THRESHOLD, 0.6)


class MixerPanelPanningTests(unittest.TestCase):
    """设置面板：滑块/分段 → 配置 → 角度，两端必须落在同一套参数上。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _config(self):
        from src.config import DEFAULT_CONFIG
        from src.widgets.mixer_panel import MixerConfigPanel

        class _Cfg:
            def __init__(self):
                self.mixer_cfg = copy.deepcopy(DEFAULT_CONFIG["mixer"])
                self.saved = 0

            def save(self):
                self.saved += 1

        return _Cfg(), MixerConfigPanel

    def test_defaults_load_into_controls(self):
        cfg, panel_cls = self._config()
        panel = panel_cls(cfg)
        self.addCleanup(panel.deleteLater)
        self.assertEqual(panel.sep_slider.value(), 45)
        self.assertEqual(panel.center_segment.current_data(), 30)
        self.assertEqual(panel.sep_readout.text(), "偏转 45° → 135° / 45°")

    def test_slider_writes_config_and_readout(self):
        cfg, panel_cls = self._config()
        panel = panel_cls(cfg)
        self.addCleanup(panel.deleteLater)
        before = cfg.saved
        panel.sep_slider.setValue(80)
        self.assertEqual(cfg.mixer_cfg["channel_separation"], 80)
        self.assertEqual(cfg.saved, before + 1)
        self.assertEqual(panel.sep_readout.text(), "偏转 80° → 170° / 10°")

    def test_segment_click_updates_angles(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        cfg, panel_cls = self._config()
        panel = panel_cls(cfg)
        self.addCleanup(panel.deleteLater)
        buttons = [b for b, _ in panel.center_segment._buttons]
        self.assertEqual([b.text() for b in buttons], ["偏左", "正中", "偏右"])

        QTest.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
        self.assertEqual(cfg.mixer_cfg["channel_center_bias"], -30)
        angles = angles_from_config(cfg.mixer_cfg)
        self.assertEqual(angles["both"], 60.0)
        # left/right 只由分离度决定，切换"居中偏置"不影响它们
        self.assertEqual(angles["left"], 135.0)
        self.assertEqual(angles["right"], 45.0)

    def test_legacy_channel_map_migrates_into_controls(self):
        cfg, panel_cls = self._config()
        cfg.mixer_cfg = {"channel_map": {"left": 135, "both": 110, "right": 45}}
        panel = panel_cls(cfg)
        self.addCleanup(panel.deleteLater)
        self.assertEqual(panel.sep_slider.value(), 45)
        self.assertEqual(panel.center_segment.current_data(), 30)
        self.assertEqual(panel.sep_readout.text(), "偏转 45° → 135° / 45°")

    def test_panel_and_mixer_agree_on_angles(self):
        """面板保存的参数经 angles_from_config 换算，必须等于面板读数显示的值。"""
        cfg, panel_cls = self._config()
        panel = panel_cls(cfg)
        self.addCleanup(panel.deleteLater)
        panel.sep_slider.setValue(60)
        angles = angles_from_config(cfg.mixer_cfg)
        self.assertEqual(angles["left"], 150.0)
        self.assertEqual(angles["right"], 30.0)
        self.assertIn("150°", panel.sep_readout.text())
        self.assertIn("30°", panel.sep_readout.text())

    def test_readout_is_not_squeezed_by_the_slider(self):
        """分离度读数必须**完整显示**，不能被滑块挤成一条细竖线。

        首版给读数套了 ``Ignored`` 尺寸策略（本意是"长标签别反向撑宽卡片"），
        但布局会因此忽略 sizeHint，而滑块带 stretch=1 会把整行吃光——离屏实测
        滑块沟槽一直画到 x≈662，读数区只剩 ~28px，文字被裁到只剩一个笔画
        （像素扫描确认该行右侧几乎没有文字像素）。
        """
        from src.ui_theme import apply_theme
        apply_theme(self.app)
        cfg, panel_cls = self._config()
        panel = panel_cls(cfg)
        self.addCleanup(panel.deleteLater)
        panel.resize(560, 900)
        panel.show()
        self.addCleanup(panel.hide)
        for _ in range(3):
            self.app.processEvents()
        panel.sep_slider.setValue(90)          # 最宽读数：偏转 90° → 180° / 0°
        for _ in range(3):
            self.app.processEvents()

        label = panel.sep_readout
        self.assertEqual(label.text(), "偏转 90° → 180° / 0°")
        needed = label.fontMetrics().horizontalAdvance(label.text())
        self.assertGreaterEqual(
            label.width(), needed,
            f"读数被滑块挤压：实际宽 {label.width()}px < 需要 {needed}px，文字显示不全")


if __name__ == "__main__":
    unittest.main()
