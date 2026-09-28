# -*- coding: utf-8 -*-
"""主控界面可复用组件的公共常量与工具。"""
from PyQt6.QtGui import QColor

from ..task_manager import STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, _natural_key
from ..steps.edge_voices import EDGE_TTS_VOICES


def _natural_sort_key(s: str) -> list:
    """自然排序 key：Track2 排在 Track10 之前。"""
    return _natural_key(s)


# 语速/音量预设
RATE_PRESETS = ["-50%", "-30%", "-20%", "-10%", "+0%", "+10%", "+20%", "+30%", "+50%"]
VOLUME_PRESETS = ["-50%", "-30%", "-20%", "-10%", "+0%", "+10%", "+20%", "+30%", "+50%"]


# 步骤状态对应的显示文本和颜色
STATUS_TEXT = {
    STEP_PENDING: "等待中",
    STEP_RUNNING: "进行中",
    STEP_DONE: "已完成",
    STEP_FAILED: "失败",
    "skipped": "已跳过",
}
STATUS_COLOR = {
    STEP_PENDING: QColor(128, 128, 128),
    STEP_RUNNING: QColor(0, 120, 215),
    STEP_DONE: QColor(0, 160, 0),
    STEP_FAILED: QColor(200, 0, 0),
    "skipped": QColor(128, 128, 128),
}
