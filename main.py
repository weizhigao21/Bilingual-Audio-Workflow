# -*- coding: utf-8 -*-
"""双语音声工作流主控入口。

整合三个子项目：
  1. 字幕提取（infer.exe，faster-whisper）
  2. 语音生成（TTS，PyQt6）
  3. 音频混音（audio_utils，抽取核心函数）

工作流：视频 → 字幕 → 配音 → 混音视频
"""
import sys
import os
import subprocess

if os.name == "nt":
    _orig_popen = subprocess.Popen

    class _NoWindowPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            if "creationflags" not in kwargs:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            if "startupinfo" not in kwargs:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs["startupinfo"] = si
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from src.workflow_gui import WorkflowMainWindow
from src.config import WorkflowConfig

VERSION = "v2.0.0"

_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "resources", "configs", "ui.ico")


def main():
    app = QApplication(sys.argv)
    if os.path.exists(_ICON_PATH):
        app.setWindowIcon(QIcon(_ICON_PATH))
    app.setApplicationVersion(VERSION)

    config = WorkflowConfig()
    if not config.is_configured():
        # 首次启动：引导用户配置三个子项目路径
        if not config.run_setup_dialog(app):
            print("未完成配置，程序退出。")
            sys.exit(0)

    window = WorkflowMainWindow(config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
