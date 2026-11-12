"""守卫：配音开始前必须立刻给出剩余时间初估，而不是一直显示「计算中...」。

首批结果返回前（本地模型常要几十秒）界面只显示 `剩余 计算中...`，
用户会以为在跑一段很慢的计算。`_emit_initial_eta` 负责用字幕字数先给「约 X」。
"""
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, Qt

from src.steps.tts_worker import TTSWorker


def _api(name, status="success"):
    return {"name": name, "url": f"http://127.0.0.1:8{len(name)}00",
            "model": "m", "status": status}


def _task(chars, index=1):
    return (index, "00:00:00.00", "字" * chars, ".", 0)


class TTSInitialEtaTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])

    def _worker(self, **overrides):
        config = {
            "tts_mode": "api",
            "lrc_files": ["x.srt"],
            "output_dir": ".",
            "api_configs": [_api("a")],
            "current_api_index": 0,
            "use_multi_api": False,
            "edge_threads": 8,
            "edge_max_concurrent": 8,
        }
        config.update(overrides)
        # 不碰真实缓存库（AudioCache 会建 resources/ 下的 sqlite 文件）
        with patch("src.steps.tts_worker.AudioCache"):
            worker = TTSWorker(config)
        return worker

    def _collect(self, worker, pending):
        seen = []
        worker.eta_signal.connect(seen.append, Qt.ConnectionType.DirectConnection)
        worker._emit_initial_eta(pending)
        return seen

    def test_estimate_replaces_calculating_placeholder(self):
        worker = self._worker()
        seen = self._collect(worker, [_task(40, i) for i in range(1, 11)])
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith("约"), seen[0])
        self.assertNotIn("计算中", seen[0])

    def test_more_lanes_give_shorter_estimate(self):
        pending = [_task(40, i) for i in range(1, 31)]
        single = self._worker()
        multi = self._worker(
            use_multi_api=True,
            api_configs=[_api("a"), _api("bb")],
        )
        single_seconds = single._initial_eta_seconds(1200)
        multi_seconds = multi._initial_eta_seconds(1200)
        self.assertGreater(single_seconds, multi_seconds)
        self.assertAlmostEqual(single_seconds / 2, multi_seconds, places=3)
        self.assertTrue(self._collect(multi, pending))

    def test_edge_mode_uses_smaller_lane_count_of_pool_and_gate(self):
        worker = self._worker(tts_mode="edge", edge_threads=12, edge_max_concurrent=8)
        # 有效并发取池与闸门的较小值：12/8 → 8 路
        rate = TTSWorker._PRIOR_CHARS_PER_SECOND["edge"]
        self.assertAlmostEqual(worker._initial_eta_seconds(800), 800 / (rate * 8), places=3)

    def test_no_tasks_or_no_chars_emits_nothing(self):
        worker = self._worker()
        self.assertEqual(self._collect(worker, []), [])
        self.assertEqual(self._collect(worker, [_task(0)]), [])

    def test_estimate_is_at_least_one_second(self):
        worker = self._worker()
        self.assertEqual(self._collect(worker, [_task(2)])[0], "约1秒")


if __name__ == "__main__":
    unittest.main()
