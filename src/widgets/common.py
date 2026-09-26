# -*- coding: utf-8 -*-
"""主控界面可复用组件的公共常量与工具。"""
from PyQt6.QtGui import QColor

from ..task_manager import STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, _natural_key


def _natural_sort_key(s: str) -> list:
    """自然排序 key：Track2 排在 Track10 之前。"""
    return _natural_key(s)


# 微软 Edge TTS 可用声音
EDGE_TTS_VOICES = {
    "zh-CN-XiaoxiaoNeural": "晓晓（女声）",
    "zh-CN-YunxiNeural": "云希（男声）",
    "zh-CN-XiaoyiNeural": "晓艺（女声）",
    "zh-CN-YunjianNeural": "云健（男声）",
    "zh-CN-XiaochenNeural": "晓辰（女声）",
    "zh-CN-XiaohanNeural": "晓涵（女声）",
    "zh-CN-XiaomengNeural": "晓梦（女声）",
    "zh-CN-XiaomoNeural": "晓墨（女声）",
    "zh-CN-XiaoqiuNeural": "晓秋（女声）",
    "zh-CN-XiaoruiNeural": "晓睿（女声）",
    "zh-CN-XiaoshuangNeural": "晓双（女声）",
    "zh-CN-XiaoxuanNeural": "晓萱（女声）",
    "zh-CN-XiaoyanNeural": "晓颜（女声）",
    "zh-CN-XiaozhenNeural": "晓甄（女声）",
    "zh-CN-YunfengNeural": "云枫（男声）",
    "zh-CN-YunhaoNeural": "云皓（男声）",
    "zh-CN-YunxiaNeural": "云夏（男声）",
    "zh-CN-YunyangNeural": "云扬（男声）",
    "zh-CN-YunzeNeural": "云泽（男声）",
}

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
