# -*- coding: utf-8 -*-
"""P0-1 性能观测守卫（《优化方案-易用性与速度》批次 0）。

覆盖：PerfTimer 计时与 PERF 报告形状、计数器增量口径、thread-local
跨线程传播、以及各埋点钩子（缓存 / ffprobe / ffmpeg / UI 信号 / 混音全链路）。
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.steps import perf
from src.steps import tts_cache
from src.steps.audio_utils import ffmpeg_utils, clear_mix_cache
from src.steps.progress_throttle import ProgressThrottle
from src.steps.step3_mixer import mix_single_task
from src.task_manager import TaskInfo

try:
    from pydub import AudioSegment
    import numpy as np
except Exception:  # pragma: no cover
    AudioSegment = None


class PerfTimerTests(unittest.TestCase):
    def setUp(self):
        perf.COUNTERS.reset()

    def test_span_accumulates_and_report_shape(self):
        timer = perf.PerfTimer("unit")
        with timer.span("片段解码"):
            time.sleep(0.01)
        with timer.span("片段解码"):
            time.sleep(0.01)
        stages = timer.snapshot()
        self.assertIn("片段解码", stages)
        self.assertGreater(stages["片段解码"], 0.01)

        payload = timer.to_dict()
        self.assertEqual(payload["event"], "PERF")
        self.assertEqual(payload["label"], "unit")
        self.assertGreaterEqual(payload["wall_s"], 0.0)
        self.assertIn("stages_s", payload)
        self.assertIn("counters", payload)
        # 七段之外的键不允许出现，防止口径漂移
        self.assertTrue(set(payload["stages_s"]) <= set(perf.STAGES))

    def test_report_counters_are_deltas_not_globals(self):
        timer = perf.PerfTimer("unit")
        perf.COUNTERS.incr("wss_requests", 2)
        perf.COUNTERS.incr("cache_hit")
        payload = timer.to_dict()
        self.assertEqual(payload["counters"].get("wss_requests"), 2)
        self.assertEqual(payload["counters"].get("cache_hit"), 1)
        self.assertTrue(set(payload["counters"]) <= set(perf.COUNTER_KEYS))

        # 新 timer 以创建时刻为基线，不受此前全局计数污染
        timer2 = perf.PerfTimer("unit2")
        self.assertEqual(timer2.to_dict()["counters"], {})

    def test_report_logs_one_perf_line(self):
        timer = perf.PerfTimer("unit")
        with self.assertLogs("tts_audio", level="INFO") as captured:
            line = timer.report()
        self.assertTrue(line.startswith("PERF "))
        json.loads(line[len("PERF "):])
        self.assertTrue(any("PERF " in r.getMessage() for r in captured.records))


class ThreadLocalTests(unittest.TestCase):
    def test_span_without_timer_is_noop(self):
        perf.attach(None)  # 归一化：不让先跑的用例残留 thread-local
        self.assertIsNone(perf.current())
        with perf.span("片段解码"):
            pass  # 不应抛异常

    def test_wrap_carries_timer_into_another_thread(self):
        timer = perf.PerfTimer("unit")
        perf.attach(timer)
        try:
            def work():
                with perf.span("片段解码"):
                    pass

            thread = threading.Thread(target=perf.wrap(work))
            thread.start()
            thread.join()
        finally:
            perf.attach(None)
        self.assertIn("片段解码", timer.snapshot())

    def test_wrap_restores_previous_timer(self):
        timer = perf.PerfTimer("unit")

        def work():
            return perf.current()

        # wrap 在提交线程捕获 timer：先 attach 再 wrap
        perf.attach(timer)
        try:
            wrapped = perf.wrap(work)
            self.assertIs(wrapped(), timer)
            self.assertIs(perf.current(), timer)
        finally:
            perf.attach(None)


class CounterHookTests(unittest.TestCase):
    def setUp(self):
        perf.COUNTERS.reset()

    def test_cache_counters_hit_miss_write(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(tts_cache, "CACHE_DIR", folder):
            cache = tts_cache.AudioCache(str(Path(folder) / "cache.db"))
            try:
                self.assertIsNone(cache.get_cached_audio("你好"))
                self.assertEqual(perf.COUNTERS.snapshot().get("cache_miss"), 1)

                source = Path(folder) / "src.wav"
                source.write_bytes(b"RIFFxxxxWAVEdata")
                self.assertTrue(cache.save_audio_cache("你好", str(source)))
                self.assertEqual(perf.COUNTERS.snapshot().get("cache_write"), 1)

                self.assertIsNotNone(cache.get_cached_audio("你好"))
                self.assertEqual(perf.COUNTERS.snapshot().get("cache_hit"), 1)
            finally:
                cache.close()

    def test_probe_audio_counts_ffprobe_even_on_failure(self):
        before = perf.COUNTERS.snapshot().get("ffprobe_procs", 0)
        self.assertIsNone(ffmpeg_utils.probe_audio("Z:/definitely-missing-file.wav"))
        self.assertEqual(
            perf.COUNTERS.snapshot().get("ffprobe_procs", 0), before + 1)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_run_ffmpeg_counts_processes(self):
        before = perf.COUNTERS.snapshot().get("ffmpeg_procs", 0)
        ffmpeg_utils._run_ffmpeg(["ffmpeg", "-hide_banner", "-version"])
        self.assertEqual(
            perf.COUNTERS.snapshot().get("ffmpeg_procs", 0), before + 1)

    def test_throttle_emits_count_as_ui_signals(self):
        emitted = []
        throttle = ProgressThrottle(
            5, lambda v: emitted.append(("p", v)),
            lambda v: emitted.append(("w", v)),
            lambda t: emitted.append(("e", t)),
            lambda m: emitted.append(("l", m)),
        )
        before = perf.COUNTERS.snapshot().get("ui_signals", 0)
        throttle.update(1, 10)   # 小任务直通：progress + weight 两次发射
        self.assertEqual(len(emitted), 2)
        self.assertEqual(
            perf.COUNTERS.snapshot().get("ui_signals", 0), before + 2)


@unittest.skipUnless(
    AudioSegment is not None and shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg/pydub required")
class MixPerfReportTests(unittest.TestCase):
    def test_mix_single_task_emits_perf_log_with_stages(self):
        samples = (np.sin(2 * np.pi * 440 * np.arange(4800) / 48000) * 8000) \
            .astype(np.int16)
        seg = AudioSegment(samples.tobytes(), sample_width=2,
                           frame_rate=48000, channels=1)
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / "source.wav")
            seg.export(source, format="wav").close()
            clips = Path(folder) / "clips"
            clips.mkdir()
            for i in range(3):
                clip = AudioSegment(
                    (np.sin(2 * np.pi * 220 * np.arange(2400) / 24000) * 6000)
                    .astype(np.int16).tobytes(),
                    sample_width=2, frame_rate=24000, channels=1)
                clip.export(str(clips / f"{i+1:04d}_00-00-0{i}.00_词.wav"),
                            format="wav").close()
            cfg = SimpleNamespace(mixer_cfg={
                'output_folder': folder, 'output_format': 'mp3',
                'skip_existing': False, 'channel_detect': False,
                'auto_volume': 'off',
            })
            task = TaskInfo('perf-test', source, 'source', folder,
                            step2_output=str(clips))
            try:
                with self.assertLogs("tts_audio", level="INFO") as captured:
                    ok, result = mix_single_task(task, cfg)
                self.assertTrue(ok, result)

                perf_lines = [r.getMessage() for r in captured.records
                              if r.getMessage().startswith("PERF ")]
                self.assertTrue(perf_lines, "mix_single_task 应输出 PERF 日志行")
                payload = json.loads(perf_lines[-1][len("PERF "):])
                self.assertTrue(payload["label"].startswith("step3-"))
                self.assertTrue(set(payload["stages_s"]) <= set(perf.STAGES))
                self.assertTrue(set(payload["counters"]) <= set(perf.COUNTER_KEYS))
                # 全链路埋点应至少落这几个桶
                for stage in ("片段解码", "混音累加", "导出"):
                    self.assertIn(stage, payload["stages_s"], stage)
                self.assertGreaterEqual(
                    payload["counters"].get("ffprobe_procs", 0), 1)
                # 入口包装负责摘 thread-local，不残留给复用线程
                self.assertIsNone(perf.current())
            finally:
                clear_mix_cache()


if __name__ == "__main__":
    unittest.main()
