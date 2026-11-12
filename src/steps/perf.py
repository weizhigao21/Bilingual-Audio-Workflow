# -*- coding: utf-8 -*-
"""轻量性能观测：分段计时 + 全局计数器（《优化方案-易用性与速度》P0-1）。

设计约束（改动前务必读完）：
- **纯观测，零业务分支**：任何失败都不得影响主流程，report() 全部异常内吞。
- **线程安全**：TTS 池线程 / 混音批量线程都会写计数与计时；计时挂在
  thread-local 上，跨线程提交用 :func:`wrap` 把 timer 带过去。
- **不进 UI**：PERF 行走 ``tts_audio`` logger（落盘 + 控制台），
  绝不走 log_signal，避免批量时刷屏。
- **计数器为进程级全局**；report() 输出的是相对 timer 创建时刻的**增量**，
  保证多任务并行时每个任务的 PERF 行互不污染。
- 本模块不依赖 Qt、不依赖项目内其他模块（logger 延迟导入，避免
  tts_cache → perf → tts_logger → tts_cache 循环导入）。
"""
import json
import logging
import threading
import time
from contextlib import contextmanager

# 七段耗时的固定口径（《性能优化方案》第三节）：
#   字幕提取 / TTS请求 / 缓存命中 / 片段解码 / 声道检测 / 混音累加 / 导出
STAGES = ("字幕提取", "TTS请求", "缓存命中", "片段解码", "声道检测", "混音累加", "导出")

# 计数器口径（report 的 counters 字段只含出现过的键）：
#   ffmpeg_procs / ffprobe_procs / wss_requests / api_requests
#   cache_hit / cache_miss / cache_write / ui_signals
#   clip_decode_fast（soundfile 进程内解码）/ clip_decode_fallback（回退子进程）
COUNTER_KEYS = (
    "ffmpeg_procs", "ffprobe_procs", "wss_requests", "api_requests",
    "cache_hit", "cache_miss", "cache_write", "ui_signals",
    "clip_decode_fast", "clip_decode_fallback",
)


class PerfCounters:
    """进程级全局计数器；线程安全。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def incr(self, name, n=1):
        with self._lock:
            self._data[name] = self._data.get(name, 0) + int(n)

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def reset(self):
        with self._lock:
            self._data.clear()

    def delta_since(self, baseline):
        """相对 baseline 快照的增量（只含出现过的键）。"""
        now = self.snapshot()
        keys = set(now) | set(baseline or {})
        return {k: now.get(k, 0) - (baseline or {}).get(k, 0)
                for k in sorted(keys) if now.get(k, 0) != (baseline or {}).get(k, 0)}


COUNTERS = PerfCounters()


class PerfTimer:
    """按名称累加耗时；线程安全。

    典型用法（每个任务一个 timer）::

        timer = task_timer("step3-混音")
        try:
            with timer.span("片段解码"):
                ...
        finally:
            timer.report()
    """

    def __init__(self, label=""):
        self.label = label
        self._lock = threading.Lock()
        self._stages = {}
        self._t0 = time.perf_counter()
        self._counters_t0 = COUNTERS.snapshot()

    def add(self, stage, seconds):
        with self._lock:
            self._stages[stage] = self._stages.get(stage, 0.0) + float(seconds)

    @contextmanager
    def span(self, stage):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add(stage, time.perf_counter() - start)

    def snapshot(self):
        with self._lock:
            return {k: round(v, 4) for k, v in self._stages.items()}

    def total(self):
        return time.perf_counter() - self._t0

    def to_dict(self):
        return {
            "event": "PERF",
            "label": self.label,
            "wall_s": round(self.total(), 3),
            "stages_s": self.snapshot(),
            "counters": COUNTERS.delta_since(self._counters_t0),
        }

    def report(self, logger=None):
        """输出一行 PERF JSON；异常内吞，返回日志行（失败返回空串）。"""
        try:
            line = "PERF " + json.dumps(self.to_dict(), ensure_ascii=False)
            if logger is None:
                logger = _default_logger()
            logger.info(line)
            return line
        except Exception:
            return ""


def _default_logger():
    """延迟导入 tts_logger（配置好的 tts_audio logger），避免循环导入。"""
    try:
        from .tts_logger import setup_logger
        return setup_logger()
    except Exception:
        return logging.getLogger("perf")


# ---------- thread-local 当前 timer ----------

_local = threading.local()


def current():
    return getattr(_local, "timer", None)


def attach(timer):
    """把 timer 挂到当前线程；传 None 即解除。返回先前的 timer。"""
    prev = current()
    _local.timer = timer
    return prev


def task_timer(label=""):
    """创建 timer 并挂到当前线程（每个任务的 run() 入口调用一次）。"""
    timer = PerfTimer(label)
    attach(timer)
    return timer


@contextmanager
def span(stage):
    """在当前线程的 timer 上计时；无 timer 时是无操作（零开销短路）。"""
    timer = current()
    if timer is None:
        yield
        return
    with timer.span(stage):
        yield


def wrap(fn, timer=None):
    """把 timer 带进另一个线程执行 fn（ThreadPoolExecutor.submit 用）。

    在**提交线程**调用，捕获当前 thread-local 的 timer；包装函数在工作线程
    运行前后自动 attach / 还原，避免 timer 泄漏进共享线程池。
    """
    if timer is None:
        timer = current()

    def _wrapped(*args, **kwargs):
        prev = attach(timer)
        try:
            return fn(*args, **kwargs)
        finally:
            attach(prev)

    return _wrapped
