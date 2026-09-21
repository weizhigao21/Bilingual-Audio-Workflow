# -*- coding: utf-8 -*-
"""全局崩溃/异常日志兜底。

程序任何未捕获异常（主线程、worker 线程）与 Qt 警告/致命消息（如
"QThread: Destroyed while thread is still running" 这类原生 abort 前的
最后一条信息）都会追加写入 resources/logs/crash_YYYYMMDD.log；
同时在创建 QApplication 前启用 faulthandler，原生崩溃（访问违规等）
时能把 Python 线程栈落盘，闪退/异常后可直接定位根因，无需靠现象猜测。

安装顺序：必须在创建 QApplication 之前调用 install()。
"""
import os
import sys
import time
import threading
import traceback
import faulthandler

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "resources", "logs",
)

_lock = threading.Lock()


def _log_path() -> str:
    os.makedirs(_LOG_DIR, exist_ok=True)
    return os.path.join(_LOG_DIR, time.strftime("crash_%Y%m%d.log"))


def _write(header: str, body: str) -> None:
    """线程安全的追加写。回调内绝不抛异常（兜底逻辑本身不能崩）。"""
    with _lock:
        try:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {header} =====\n")
                f.write(body.rstrip() + "\n")
        except Exception:
            pass


def _handle_uncaught(exc_type, exc_value, exc_tb):
    """主线程未捕获异常。"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _write(f"未捕获异常 [{threading.current_thread().name}]", tb)
    # 保留默认行为（打印到 stderr），不吞掉异常
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _handle_thread_exception(args):
    """非主线程（worker 线程）未捕获异常。"""
    tb = "".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
    )
    _write(f"线程异常 [{args.thread.name}]", tb)


_qt_level_names = {
    0: "Debug",
    1: "Warning",
    2: "Critical",
    3: "Fatal",
    4: "Info",
}


def _qt_message_handler(mode, context, message):
    """Qt 消息回调。qFatal 是原生 abort 前的最后一条信息，必须先落盘。

    注意：本回调内任何异常逃逸都会导致进程 abort（PyQt6 行为），
    因此整体包在 try/except 里，绝不转发给默认处理器（默认处理器会把
    消息打到 stderr，中文在 ascii 控制台下会再抛一次编码异常）。
    """
    try:
        m = int(getattr(mode, "value", mode))
        name = _qt_level_names.get(m, str(mode))
        # Warning 以上（如 "QThread: Destroyed while thread is still running"、
        # "Internal C++ object already deleted"）都是高价值诊断信息
        if m >= 1:
            _write(f"Qt {name}", message)
    except Exception:
        pass


def install() -> None:
    """安装全部兜底钩子，必须在创建 QApplication 之前调用。"""
    sys.excepthook = _handle_uncaught
    threading.excepthook = _handle_thread_exception

    # faulthandler：原生崩溃（Windows 异常）时输出 Python 线程栈。
    # enable() 内部持有 file 引用，不随本函数退出而关闭。
    try:
        _log_path()  # 预先创建目录
        faulthandler.enable(open(_log_path(), "a"), all_threads=True)
    except Exception:
        pass

    try:
        from PyQt6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        pass
