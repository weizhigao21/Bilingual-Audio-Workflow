# -*- coding: utf-8 -*-
"""P1-3 同键 in-flight 合并守卫（2026-11-12）。

覆盖：并发同键只发一次请求（WSS 计数口径）、先行者失败不拖死等待者、
缓存复用消息、批量同键合并、重试退避区间。
"""
import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.steps import tts_cache, tts_utils


async def _async_sleep(delay):
    await asyncio.sleep(delay)


class InflightMergeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        folder = Path(self._tmp.name)
        self.cache = tts_cache.AudioCache(str(folder / "cache.db"))
        self.addCleanup(self.cache.close)
        self.save_dir = folder / "clips"
        self.save_dir.mkdir()

    @staticmethod
    def _counter_delta(baseline):
        return tts_utils.perf.COUNTERS.delta_since(baseline)

    @staticmethod
    def _make_stub(fail_first=0, delay=0.25):
        """替身生成器（协程）：可让前 N 次调用失败，其余写 8 字节产物。"""
        lock = threading.Lock()
        calls = []

        async def stub(text, voice, rate, volume, save_path):
            with lock:
                calls.append(text)
                nth = len(calls)
            await _async_sleep(delay)
            if nth <= fail_first:
                raise RuntimeError("合成端点拒绝")
            Path(save_path).write_bytes(b"MP3DATA!")

        return stub, calls

    def _run_threads(self, n, fn):
        """n 线程同时执行 fn(k)，返回结果列表。"""
        barrier = threading.Barrier(n)
        results = [None] * n

        def worker(k):
            barrier.wait()
            results[k] = fn(k)

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        return results

    def test_10_threads_same_text_issue_one_request(self):
        stub, calls = self._make_stub()
        baseline = tts_utils.perf.COUNTERS.snapshot()

        def run(k):
            return tts_utils.edge_tts_task(
                k + 1, "00-00-01.00", "你好世界", "zh-CN-XiaoxiaoNeural",
                "+0%", "+0%", str(self.save_dir), self.cache, source_mtime=1000)

        with patch.object(tts_utils, "_edge_tts_generate", stub):
            results = self._run_threads(10, run)

        self.assertEqual(len(calls), 1, "同键 10 线程应只发一次生成请求")
        for ok, msg in results:
            self.assertTrue(ok, msg)
        delta = self._counter_delta(baseline)
        self.assertEqual(delta.get("wss_requests", 0), 1)
        self.assertEqual(delta.get("dedup_merged", 0), 9)
        # 9 个等待者应走缓存复用文案
        reuse_msgs = [m for _, m in results if m.startswith("缓存复用")]
        self.assertGreaterEqual(len(reuse_msgs), 9)

    def test_owner_failure_does_not_block_waiters(self):
        # 先行者 1 次尝试即失败（max_retries=1）：如实上报失败；
        # 等待者重新竞争后自行生成成功，不被先行者拖死
        stub, calls = self._make_stub(fail_first=1, delay=0.05)
        baseline = tts_utils.perf.COUNTERS.snapshot()

        def run(k):
            return tts_utils.edge_tts_task(
                k + 1, "00-00-01.00", "失败文本", "zh-CN-XiaoxiaoNeural",
                "+0%", "+0%", str(self.save_dir), self.cache,
                source_mtime=1000, max_retries=1)

        with patch.object(tts_utils, "_edge_tts_generate", stub):
            results = self._run_threads(2, run)

        self.assertEqual(len(calls), 2, "先行者失败后等待者应自行发起请求")
        self.assertEqual(sorted(ok for ok, _ in results), [False, True], results)

    def test_waiter_copies_from_cache_not_shared_file(self):
        stub, _ = self._make_stub()

        def run(k):
            return tts_utils.edge_tts_task(
                k + 1, f"00-00-0{k}.00", "复用文本", "zh-CN-XiaoxiaoNeural",
                "+0%", "+0%", str(self.save_dir), self.cache, source_mtime=1000)

        with patch.object(tts_utils, "_edge_tts_generate", stub):
            results = self._run_threads(5, run)

        self.assertTrue(all(ok for ok, _ in results))
        files = sorted(p.name for p in self.save_dir.iterdir() if p.suffix == ".mp3")
        self.assertEqual(len(files), 5, "每个片段应有自己的产物文件")
        for p in self.save_dir.iterdir():
            self.assertGreater(p.stat().st_size, 0)

    def test_retry_backoff_follows_exponential_with_jitter(self):
        for attempt, base in ((1, 0.5), (2, 1.0), (3, 2.0)):
            for _ in range(20):
                delay = tts_utils._retry_backoff(attempt)
                self.assertGreaterEqual(delay, base)
                self.assertLessEqual(delay, base * 1.25)

    def test_bulk_dedupes_identical_texts(self):
        payload_texts = []

        class _Resp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"audio_urls": ["http://x/a.mp3"] * len(payload_texts)}

        def fake_post(endpoint, json=None, timeout=None):
            payload_texts.extend(json["texts"])
            return _Resp()

        def fake_download(audio_url, save_path, timeout=60):
            Path(save_path).write_bytes(b"MP3DATA!")

        tasks = [
            (i + 1, "00-00-01.00", "同句台词", str(self.save_dir), 1000.0)
            for i in range(4)
        ]
        baseline = tts_utils.perf.COUNTERS.snapshot()
        with patch.object(tts_utils, "_get_session") as gs, \
             patch.object(tts_utils, "_download_audio", fake_download), \
             patch.object(tts_utils, "is_local_service", lambda *a, **k: False):
            gs.return_value.post.side_effect = fake_post
            results = tts_utils.tts_bulk_task(
                tasks, "http://api.test", "model", self.cache, model_tag="t")

        self.assertEqual(payload_texts, ["同句台词"], "4 条同句应合并为 1 条请求")
        for ok, msg in results:
            self.assertTrue(ok, msg)
        delta = self._counter_delta(baseline)
        self.assertEqual(delta.get("api_requests", 0), 1)
        self.assertEqual(delta.get("dedup_merged", 0), 3)
        files = [p for p in self.save_dir.iterdir() if p.suffix == ".wav"]
        self.assertEqual(len(files), 4, "同键组每个片段都要有产物")

    def test_inflight_registry_no_leak_after_completion(self):
        stub, _ = self._make_stub()
        with patch.object(tts_utils, "_edge_tts_generate", stub):
            ok, _ = tts_utils.edge_tts_task(
                1, "00-00-01.00", "泄漏检查", "zh-CN-XiaoxiaoNeural",
                "+0%", "+0%", str(self.save_dir), self.cache, source_mtime=1)
        self.assertTrue(ok)
        self.assertEqual(tts_utils._inflight, {})


if __name__ == "__main__":
    unittest.main()
