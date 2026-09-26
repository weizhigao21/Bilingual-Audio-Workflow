# -*- coding: utf-8 -*-
"""工作流主窗口包。

由 workflow_gui.py 拆分为包结构，本模块保持原有导入接口不变：
    from src.workflow_gui import WorkflowMainWindow
"""
from .common import VIDEO_EXTS, SUBTITLE_EXTS, MIX_AUDIO_EXTS
from .window import WorkflowMainWindow

__all__ = [
    "WorkflowMainWindow",
    "VIDEO_EXTS", "SUBTITLE_EXTS", "MIX_AUDIO_EXTS",
]
