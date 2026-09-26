# -*- coding: utf-8 -*-
"""主控界面可复用组件：步骤面板、任务列表、各配置面板。

由 widgets.py 拆分为包结构，本模块保持原有导入接口不变：
    from .widgets import StepPanel, TaskListWidget, TTSConfigPanel, ...
"""
from .common import (
    _natural_sort_key, EDGE_TTS_VOICES, RATE_PRESETS, VOLUME_PRESETS,
    STATUS_TEXT, STATUS_COLOR,
)
from .step_panel import StepPanel
from .task_list import TaskListWidget
from .tts_panel import TTSConfigPanel, ApiConfigDialog
from .mixer_panel import MixerConfigPanel
from .whisper_panel import WhisperConfigPanel

__all__ = [
    "_natural_sort_key",
    "EDGE_TTS_VOICES", "RATE_PRESETS", "VOLUME_PRESETS",
    "STATUS_TEXT", "STATUS_COLOR",
    "StepPanel",
    "TaskListWidget",
    "TTSConfigPanel", "ApiConfigDialog",
    "MixerConfigPanel",
    "WhisperConfigPanel",
]
