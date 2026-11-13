# -*- coding: utf-8 -*-
"""P0 性能修复守卫（2026-11-05）。

覆盖：缓存层零逐命中 commit / WAL / use_count 不丢、进度节流不吞终值、
失败信息不聚合、Edge 池容量不超过闸门、默认并发自洽、ffmpeg 等待粒度。
"""
import concurrent.futures
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication

from src import config as config_module
from src.steps import tts_cache
from src.steps.progress_throttle import ProgressThrottle
from src.steps.tts_worker import TTSWorker


class _CountingConnection:
    """统计 commit 次数的连接代理；其余方法原样转发。"""

    def __init__(self, conn):
        self._conn = conn
        self.commits = 0

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._conn.executemany(*args, **kwargs)

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self.commits += 1
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()


class CachePerfTests(unittest.TestCase):
    def test_cache_hits_do_not_commit_and_use_count_survives_close(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(tts_cache, "CACHE_DIR", folder):
            db_path = str(Path(folder) / "cache.db")
            cache = tts_cache.AudioCache(db_path)
            source = Path(folder) / "a.wav"
            source.write_bytes(b"x" * 100)
            self.assertTrue(cache.save_audio_cache("text", str(source)))

            counter = _CountingConnection(cache._conn)
            cache._conn = counter
            for _ in range(30):
                self.assertIsNotNone(cache.get_cached_audio("text"))
            self.assertEqual(counter.commits, 0, "命中路径不应产生任何 commit")

            # 未刷盘的命中数要计入统计，显示不能倒退。
            self.assertEqual(cache.get_cache_stats()["total_reuse"], 31)
            cache.close()

            conn = sqlite3.connect(db_path)
            try:
                stored = conn.execute("SELECT use_count FROM audio_cache").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(stored, 31, "close() 必须把内存计数落库")

    def test_connection_uses_wal_and_normal_sync(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(tts_cache, "CACHE_DIR", folder):
            cache = tts_cache.AudioCache(str(Path(folder) / "cache.db"))
            try:
                journal = cache._conn.execute("PRAGMA journal_mode").fetchone()[0]
                synchronous = cache._conn.execute("PRAGMA synchronous").fetchone()[0]
            finally:
                cache.close()
            self.assertEqual(str(journal).lower(), "wal")
            self.assertEqual(synchronous, 1)  # NORMAL

    def test_save_still_commits_immediately(self):
        """缓存索引是数据，不能只靠 close() 兜底。"""
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(tts_cache, "CACHE_DIR", folder):
            cache = tts_cache.AudioCache(str(Path(folder) / "cache.db"))
            source = Path(folder) / "a.wav"
            source.write_bytes(b"x" * 100)
            counter = _CountingConnection(cache._conn)
            cache._conn = counter
            self.assertTrue(cache.save_audio_cache("text", str(source)))
            self.assertGreaterEqual(counter.commits, 1)
            cache.close()


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, delta):
        self.value += delta


class ThrottleTests(unittest.TestCase):
    def _sink(self):
        return [], [], [], []

    def test_small_task_is_passthrough(self):
        progress, weight, eta, logs = self._sink()
        throttle = ProgressThrottle(5, progress.append, weight.append,
                                    eta.append, logs.append)
        for i in range(1, 6):
            throttle.update(i, i * 2, f"eta{i}")
            throttle.log(f"完成: {i}.wav")
        throttle.flush()
        self.assertEqual(progress, [1, 2, 3, 4, 5])
        self.assertEqual(logs, [f"完成: {i}.wav" for i in range(1, 6)])

    def test_large_task_collapses_signals_but_delivers_final_value(self):
        clock = _Clock()
        progress, weight, eta, logs = self._sink()
        throttle = ProgressThrottle(300, progress.append, weight.append,
                                    eta.append, logs.append, now=clock)
        for i in range(1, 301):
            clock.advance(0.01)
            throttle.update(i, i * 2, f"剩余 {300 - i} 秒")
            throttle.log(f"完成: {i}.wav")
        throttle.flush()
        self.assertLess(len(progress), 30, "300 片段不应发出 300 次进度信号")
        self.assertLess(len(logs), 20, "成功日志应被聚合")
        self.assertEqual(progress[-1], 300, "flush 后终值必须送达")
        self.assertEqual(weight[-1], 600)
        self.assertTrue(any("已完成" in line for line in logs))
        self.assertEqual(progress.count(300), 1, "终值不应重复发送")

    def test_immediate_logs_bypass_aggregation(self):
        clock = _Clock()
        progress, weight, eta, logs = self._sink()
        throttle = ProgressThrottle(300, progress.append, weight.append,
                                    eta.append, logs.append, now=clock)
        throttle.log("失败: 0007.wav", immediate=True)
        throttle.log("异常: 连接中断", immediate=True)
        self.assertEqual(logs, ["失败: 0007.wav", "异常: 连接中断"])


class _FutureExecutor:
    """已完成的 Future，让 _run_edge_tts 的 as_completed / wait 正常收尾。"""

    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        future.set_result((True, "ok"))
        return future

    def shutdown(self, *args, **kwargs):
        pass


class ConcurrencyConfigTests(unittest.TestCase):
    def test_default_pool_matches_gate(self):
        tts = config_module.DEFAULT_CONFIG["tts"]
        self.assertEqual(tts["edge_threads"], tts["edge_max_concurrent"],
                         "池 < 闸门会让全局并发上限永远不触发")

    def test_edge_pool_never_exceeds_gate(self):
        QCoreApplication.instance() or QCoreApplication([])
        fake_cache = SimpleNamespace(
            close=lambda: None,
            get_cache_stats=lambda: dict(total_count=0, total_size=0, total_reuse=0),
        )
        with patch("src.steps.tts_worker.AudioCache", return_value=fake_cache):
            worker = TTSWorker({"edge_threads": 12, "edge_max_concurrent": 8})
        # 直接调 _run_edge_tts 而不是 run()，run() 里初始化的字段需补上。
        worker._read_failures = 0
        seen = {}

        def capture_pool(value):
            seen["pool"] = value
            return _FutureExecutor()

        with patch("src.steps.tts_worker.get_edge_executor", side_effect=capture_pool), \
                patch("src.steps.tts_worker.set_edge_max_concurrent",
                      side_effect=lambda value: seen.setdefault("gate", value)), \
                patch.object(worker, "_finish_generation"):
            worker._run_edge_tts(
                [(1, "00:00.00", "text", "unused", 0)], 0, 0,
                time.time(), "id", "unused",
            )
        self.assertEqual(seen.get("pool"), 8, "多余线程不能卡在闸门排队空转")
        self.assertEqual(seen.get("gate"), 8)


class SourceGuardTests(unittest.TestCase):
    def test_log_formats_are_cached_but_colors_stay_central(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src" / "workflow_gui" / "window.py").read_text(
            encoding="utf-8")
        # 格式对象按级别缓存（日志抽屉的级别筛选会反复重建文本）
        block = source.split("def _insert_log_line")[1].split("\n    def ")[0]
        self.assertIn("LOG_COLORS[level]", block)
        self.assertIn("_log_formats", block)
        # _append_log 只做分级与入库，不再重复创建 QTextCharFormat
        append_block = source.split("def _append_log")[1].split("\n    def ")[0]
        self.assertNotIn("QTextCharFormat(", append_block)

    def test_tts_worker_uses_throttle(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src" / "steps" / "tts_worker.py").read_text(encoding="utf-8")
        self.assertIn("ProgressThrottle", source)
        self.assertIn("throttle.flush()", source)


if __name__ == "__main__":
    unittest.main()
