# -*- coding: utf-8 -*-
"""声像（pan）参数：把「分离度 / 居中偏置」换算成检测器要的 声道→角度 映射。

角度语义（与 ``steps/audio_utils/mixing.py`` 的增益公式一致）::

    left_gain  = cos(θ/2)
    right_gain = sin(θ/2)

    θ =   0° → 左增益 1、右增益 0（极左，右耳完全听不到）
    θ =  90° → 左右各 0.707（正中，各 −3 dB）
    θ = 180° → 左增益 0、右增益 1（极右）

设计变更（原配置是三个自由度）
--------------------------------
旧配置 ``channel_map`` 是三个各自独立的 0–180° 角度（人声在左/中/右时配音放哪）。
实际使用中用户总把它们调成 45–135° 的"软声像"——等于**手动把三个参数收敛成了
一个**。而且 0°/180° 是单侧增益为 0，一旦检测器判错，人声会整段消失在一边，
这个区间本来就不该暴露给人手填。

现改为两个语义参数：

- ``channel_separation``（分离度，**度数 0–90**）：原音人声判在左/右时，配音
  放到**对侧**偏转这么多度。
      人声在左 → 90° + separation；人声在右 → 90° − separation。
  用度数而不是百分比，是为了让旧配置迁移**无损**：百分比取整会带来最多 0.9°
  的偏差，而度数可以直接由 ``(|left−90| + |right−90|) / 2`` 算出。
- ``channel_center_bias``（居中偏置，−30/0/30）：原音人声在中间、或检测器
  判不出声道时的配音落点。负 = 偏左，0 = 与原音同在正中，正 = 偏右。

兼容：两个新键都缺失、且旧 ``channel_map`` 非空时，按上面的公式迁移；
否则（含完全没有声道配置）用新默认值。
"""
from __future__ import annotations

# 分离度上限（度）：90 + 90 = 180°，即"完全放到对侧"
SEPARATION_MAX = 90.0
# 原音居中时的合法偏置（度）：负=偏左、0=正中、正=偏右
CENTER_BIAS_CHOICES = (-30, 0, 30)
CENTER_BIAS_LABELS = {-30: "偏左", 0: "正中", 30: "偏右"}

SEPARATION_DEFAULT = 45
CENTER_BIAS_DEFAULT = 30


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _nearest(value: float, choices) -> float:
    return min(choices, key=lambda c: abs(c - value))


def params_from_config(cfg) -> tuple:
    """从混音配置取出 ``(separation:int 0–90, bias:float)``。

    新键优先；两个新键都缺失且旧 ``channel_map`` 非空时按旧三角度迁移；
    其余情况（含完全没有声道配置）用新默认值。
    """
    cfg = cfg or {}
    separation = cfg.get("channel_separation")
    bias = cfg.get("channel_center_bias")
    if separation is None and bias is None:
        legacy = cfg.get("channel_map")
        if legacy:
            left = float(legacy.get("left", 90.0))
            right = float(legacy.get("right", 90.0))
            both = float(legacy.get("both", 90.0))
            # 左/右两种"对侧"情形相对 90° 的平均偏转 → 分离度
            separation = (abs(left - 90.0) + abs(right - 90.0)) / 2.0
            bias = both - 90.0
    separation = _clamp(
        float(separation if separation is not None else SEPARATION_DEFAULT),
        0, SEPARATION_MAX)
    bias = float(bias if bias is not None else CENTER_BIAS_DEFAULT)
    if bias not in CENTER_BIAS_CHOICES:
        bias = _nearest(bias, CENTER_BIAS_CHOICES)
    return int(round(separation)), bias


def angles_from_config(cfg) -> dict:
    """从混音配置算出 ``channel_map``：``{"left": θ, "both": θ, "right": θ}``。

    这就是 ``detect_angles_parallel`` 的入参。面板保存与混音执行（step3 /
    stream_mixer）必须共用本函数，避免"面板上看到的"和"实际混音用的"不是一套。
    """
    separation, bias = params_from_config(cfg)
    return {
        "left": 90.0 + separation,    # 原音人声在左 → 配音放右侧
        "right": 90.0 - separation,   # 原音人声在右 → 配音放左侧
        "both": 90.0 + bias,          # 居中 / 判不出 → 固定偏置
    }


def config_from_legacy(channel_map) -> dict:
    """旧 ``channel_map`` → 新配置键（供迁移与测试使用）。"""
    separation, bias = params_from_config({"channel_map": channel_map})
    return {"channel_separation": separation, "channel_center_bias": bias}


def describe(separation: int, bias: float) -> str:
    """把两个参数说成人话，供设置页提示与日志使用。"""
    separation = int(_clamp(separation, 0, SEPARATION_MAX))
    if separation <= 0:
        return "配音始终居中（不分离）"
    side = CENTER_BIAS_LABELS.get(bias, f"偏置 {bias:+.0f}°")
    return (f"原音偏左→配音偏右 {90 + separation}°，原音偏右→配音偏左 "
            f"{90 - separation}°，原音居中→{side}")
