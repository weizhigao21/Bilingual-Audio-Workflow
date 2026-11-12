# -*- coding: utf-8 -*-
"""片段级进度 / 日志节流，避免大批量合成时 UI 被高频信号淹没。

设计约束（改动前务必读完）：
- **小任务直通**：片段数 <= MIN_ITEMS_TO_THROTTLE 时逐条发送，保持原有
  「每个片段一条日志 + 一条进度」的语义，增量交付类测试不受影响。
- **终值必达**：循环结束必须调用 flush()，保证进度条停在 100%、
  聚合日志不会吞掉最后一批。
- **失败立即输出**：错误/异常信息走 immediate=True，不参与聚合，
  否则用户会看不到哪一句合成失败。
- 本模块纯 Python，不依赖 Qt，方便离线测试。
"""
import threading
import time

from . import perf

MIN_ITEMS_TO_THROTTLE = 20
PROGRESS_INTERVAL = 0.15   # 秒：进度 / 加权进度最小发送间隔
ETA_INTERVAL = 0.5         # 秒：剩余时间最小发送间隔
LOG_INTERVAL = 0.5         # 秒：聚合日志最小发送间隔
LOG_BATCH = 20             # 条：聚合日志最大积压条数


class ProgressThrottle:
    """把高频片段回调压缩成低频信号；线程安全。

    Args:
        total: 本次运行的片段总数（用于判定是否启用节流与是否到达终值）。
        emit_progress / emit_weight / emit_eta / emit_log: 实际发射回调，
            通常是 ``signal.emit``。
        now: 单调时钟，测试时可注入假时钟。
    """

    def __init__(self, total, emit_progress, emit_weight, emit_eta, emit_log,
                 now=time.monotonic):
        self._total = max(0, int(total))
        self.enabled = self._total > MIN_ITEMS_TO_THROTTLE

        def counted(fn):
            # 每次真实发射计入 ui_signals（PERF 口径）；节流本身不改变发射语义。
            def _inner(*args, **kwargs):
                perf.COUNTERS.incr("ui_signals")
                return fn(*args, **kwargs)
            return _inner

        self._emit_progress = counted(emit_progress)
        self._emit_weight = counted(emit_weight)
        self._emit_eta = counted(emit_eta)
        self._emit_log = counted(emit_log)
        self._now = now
        self._lock = threading.Lock()

        started = self._now()
        self._last_progress = started
        self._last_eta = started
        self._last_log = started
        self._last_completed = None
        self._pending = None          # (completed, weight, eta_text)
        self._log_count = 0
        self._last_msg = ""

    def update(self, completed, weight, eta_text=""):
        """记录一个片段完成。小任务直通；大任务按时间间隔发送。"""
        if not self.enabled:
            self._emit_progress(completed)
            self._emit_weight(weight)
            if eta_text:
                self._emit_eta(eta_text)
            return

        with self._lock:
            self._pending = (completed, weight, eta_text)
            now = self._now()
            final = completed >= self._total
            if final or now - self._last_progress >= PROGRESS_INTERVAL:
                self._emit_progress(completed)
                self._emit_weight(weight)
                self._last_progress = now
                self._last_completed = completed
            if eta_text and (final or now - self._last_eta >= ETA_INTERVAL):
                self._emit_eta(eta_text)
                self._last_eta = now

    def log(self, message, immediate=False):
        """成功片段聚合输出；失败 / 异常 / 小任务立即输出。"""
        if not self.enabled or immediate:
            self._emit_log(message)
            return
        with self._lock:
            self._log_count += 1
            self._last_msg = message
            now = self._now()
            if self._log_count >= LOG_BATCH or now - self._last_log >= LOG_INTERVAL:
                self._flush_log_locked(now)

    def _flush_log_locked(self, now):
        if self._log_count <= 0:
            return
        self._emit_log(
            f"已完成 {self._log_count} 个片段（最新：{self._last_msg}）"
        )
        self._log_count = 0
        self._last_log = now

    def flush(self):
        """循环结束后调用：补发剩余聚合日志与最终进度值。"""
        if not self.enabled:
            return
        with self._lock:
            self._flush_log_locked(self._now())
            if self._pending is None:
                return
            completed, weight, eta_text = self._pending
            if completed == self._last_completed:
                return
            self._emit_progress(completed)
            self._emit_weight(weight)
            if eta_text:
                self._emit_eta(eta_text)
            self._last_completed = completed
