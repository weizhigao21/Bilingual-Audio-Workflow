# -*- coding: utf-8 -*-
"""P2-2 可取消批量 HTTP 守卫。

红线：
1. 点停止后 ≤3s 上报「已停止」，不再等满 300s 超时（慢响应服务实测）；
2. 「已停止」是结果语义不是失败异常——不得落成「批量异常」；
3. 缓存命中的片段不受影响；取消不产生半成品缓存写入；
4. 下载段同样可取消（全部下载在途时也能及时看到停止信号）。
"""
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.steps import tts_utils
from src.steps.tts_utils import BulkRequestCancelled, tts_bulk_task


def _make_task(idx, text, save_dir, mtime=0):
    return (idx, "00:00:01.00", text, save_dir, mtime)


def _cancel_after(seconds):
    """返回 cancel_check：seconds 之后开始报停止。"""
    start = time.monotonic()
    return lambda: (time.monotonic() - start) > seconds


class PostCancelTests(unittest.TestCase):
    def test_cancel_returns_within_3s(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class SlowSession:
            def post(self, url, json=None, timeout=None):
                release.wait(10)
                raise AssertionError("取消后不应消费响应")

        with patch("src.steps.tts_utils._get_session", return_value=SlowSession()):
            start = time.monotonic()
            with self.assertRaises(BulkRequestCancelled):
                tts_utils._post_bulk_with_cancel(
                    "http://x/infer_bulk", {"texts": ["a"]},
                    cancel_check=_cancel_after(0.2), poll=0.1)
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, 3.0, f"取消耗时 {elapsed:.2f}s，应 ≤3s")

    def test_no_cancel_returns_response_normally(self):
        sentinel = object()

        class FastSession:
            def post(self, url, json=None, timeout=None):
                return sentinel

        with patch("src.steps.tts_utils._get_session", return_value=FastSession()):
            resp = tts_utils._post_bulk_with_cancel(
                "http://x/infer_bulk", {"texts": ["a"]}, cancel_check=lambda: False)
        self.assertIs(resp, sentinel)

    def test_already_cancelled_raises_before_sending(self):
        class ExplodingSession:
            def post(self, url, json=None, timeout=None):
                raise AssertionError("已停止时不得发出请求")

        with patch("src.steps.tts_utils._get_session", return_value=ExplodingSession()):
            with self.assertRaises(BulkRequestCancelled):
                tts_utils._post_bulk_with_cancel(
                    "http://x/infer_bulk", {}, cancel_check=lambda: True)


class BulkTaskCancelTests(unittest.TestCase):
    def _run_bulk(self, tasks, cache_hits, cancel_check, session):
        """跑 tts_bulk_task：非本地服务路径，缓存按 cache_hits 映射。"""
        class Cache:
            def get_cached_audio(self, text, *_a, **_k):
                return cache_hits.get(text)

            def save_audio_cache(self, *a, **k):
                raise AssertionError("取消场景不应写缓存")

        with tempfile.TemporaryDirectory() as folder, \
             patch("src.steps.tts_utils.is_local_service", return_value=False), \
             patch("src.steps.tts_utils._get_session", return_value=session):
            real_tasks = []
            for idx, text in tasks:
                real_tasks.append(_make_task(idx, text, folder))
            for path in cache_hits.values():
                with open(path, "wb") as fh:
                    fh.write(b"x")
            return tts_bulk_task(real_tasks, "http://x:8080", "model", Cache(),
                                 cancel_check=cancel_check)

    def test_cancel_marks_stopped_and_keeps_cache_hits(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class SlowSession:
            def post(self, url, json=None, timeout=None):
                release.wait(10)
                raise AssertionError("取消后不应消费响应")

        with tempfile.TemporaryDirectory() as cache_dir:
            hit_path = cache_dir + "/cached.bin"
            with open(hit_path, "wb") as fh:
                fh.write(b"x")
            results = self._run_bulk(
                tasks=[(1, "命中片段"), (2, "未命中甲"), (3, "未命中乙")],
                cache_hits={"命中片段": hit_path},
                cancel_check=_cancel_after(0.2),
                session=SlowSession())

        self.assertTrue(results[0][0], "缓存命中片段应保持成功")
        self.assertIn("缓存复用", results[0][1])
        self.assertEqual(results[1], (False, "已停止"))
        self.assertEqual(results[2], (False, "已停止"))

    def test_cancel_message_is_stopped_not_error(self):
        # 「已停止」不得落成「批量异常」——语义红线
        release = threading.Event()
        self.addCleanup(release.set)

        class SlowSession:
            def post(self, url, json=None, timeout=None):
                release.wait(10)

        with tempfile.TemporaryDirectory() as cache_dir:
            hit_path = cache_dir + "/cached.bin"
            with open(hit_path, "wb") as fh:
                fh.write(b"x")
            results = self._run_bulk(
                tasks=[(1, "命中"), (2, "未命中")],
                cache_hits={"命中": hit_path},
                cancel_check=_cancel_after(0.2),
                session=SlowSession())
        self.assertEqual(results[1], (False, "已停止"))
        self.assertNotIn("异常", results[1][1])

    def test_cancel_during_download_returns_fast(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class FastSession:
            def post(self, url, json=None, timeout=None):
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"audio_urls": ["u1", "u2", "u3"]})

        def slow_download(url, save_path):
            release.wait(10)

        with patch("src.steps.tts_utils._download_audio", slow_download), \
             tempfile.TemporaryDirectory() as cache_dir:
            hit_path = cache_dir + "/cached.bin"
            with open(hit_path, "wb") as fh:
                fh.write(b"x")
            start = time.monotonic()
            results = self._run_bulk(
                tasks=[(1, "命中"), (2, "甲"), (3, "乙"), (4, "丙")],
                cache_hits={"命中": hit_path},
                cancel_check=_cancel_after(0.3),
                session=FastSession())
            elapsed = time.monotonic() - start

        self.assertLess(elapsed, 3.0, f"下载段取消耗时 {elapsed:.2f}s，应 ≤3s")
        self.assertTrue(results[0][0])
        for r in results[1:]:
            self.assertEqual(r, (False, "已停止"))


if __name__ == "__main__":
    unittest.main()
