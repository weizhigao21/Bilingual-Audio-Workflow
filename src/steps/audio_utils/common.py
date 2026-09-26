# -*- coding: utf-8 -*-
"""混音工具库的公共依赖：numpy 探测、日志与声道检测共享线程池。"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None

logger = logging.getLogger(__name__)


# 声道检测的共享线程池：外层（批量混音任务）与内层（单个任务内的片段检测）
# 复用同一容量，避免批量模式下并发数乘方（外层 N × 内层 M）。
_pan_detect_executor = None
_pan_detect_workers = 0
_pan_detect_lock = threading.RLock()


def _get_pan_detect_executor(max_workers):
    """惰性返回按容量复用的检测线程池；容量变化时销毁重建（不等待旧任务）。"""
    global _pan_detect_executor, _pan_detect_workers
    with _pan_detect_lock:
        if _pan_detect_executor is None or _pan_detect_workers != max_workers:
            if _pan_detect_executor is not None:
                _pan_detect_executor.shutdown(wait=False)
            _pan_detect_executor = ThreadPoolExecutor(max_workers=max_workers)
            _pan_detect_workers = max_workers
        return _pan_detect_executor


def _submit_pan_detection(fn, items, max_workers):
    # 获取池与提交必须同锁，否则另一混音任务调整容量后可能向已关闭的池提交。
    with _pan_detect_lock:
        executor = _get_pan_detect_executor(max_workers)
        return {executor.submit(fn, item): item for item in items}
