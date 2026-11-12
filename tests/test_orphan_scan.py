# -*- coding: utf-8 -*-
"""P2-6 孤儿配音目录只读扫描守卫（2026-11-12）。

覆盖：孤儿判定与体积统计、step2_output / custom_mix_folder 引用保护、
工作区外引用不误伤、task.json 损坏可跳过、**扫描零写入**。
"""
import json
import os
import tempfile
import unittest

from src.orphan_scan import scan_workspace, format_report


def _write_task(task_dir, task_id, **fields):
    os.makedirs(task_dir, exist_ok=True)
    data = {"task_id": task_id}
    data.update(fields)
    with open(os.path.join(task_dir, "task.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def _fill_dir(path, size):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "clip.mp3"), "wb") as fh:
        fh.write(b"0" * size)


class OrphanScanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "workspace")
        os.makedirs(self.ws)

    def _snapshot(self):
        snap = {}
        for dirpath, _, filenames in os.walk(self.ws):
            for name in filenames:
                p = os.path.join(dirpath, name)
                st = os.stat(p)
                snap[p] = (st.st_size, st.st_mtime_ns)
        return snap

    def test_orphans_are_reported_with_sizes(self):
        _write_task(os.path.join(self.ws, "task-a"), "a",
                    step2_output=os.path.join(self.ws, "voice-used"))
        _fill_dir(os.path.join(self.ws, "voice-used"), 100)
        _fill_dir(os.path.join(self.ws, "voice-orphan-1"), 300)
        _fill_dir(os.path.join(self.ws, "voice-orphan-2"), 200)
        report = scan_workspace(self.ws)
        orphans = {os.path.basename(d["path"]): d["bytes"] for d in report["orphans"]}
        self.assertEqual(orphans, {"voice-orphan-1": 300, "voice-orphan-2": 200})
        self.assertEqual(report["orphan_bytes"], 500)
        self.assertEqual(len(report["task_dirs"]), 1)
        self.assertEqual(len(report["referenced_dirs"]), 1)

    def test_custom_mix_folder_protects_target(self):
        target = os.path.join(self.ws, "voice-custom")
        _fill_dir(target, 128)
        _write_task(os.path.join(self.ws, "task-b"), "b", custom_mix_folder=target)
        report = scan_workspace(self.ws)
        self.assertEqual(report["orphans"], [])
        self.assertEqual(len(report["referenced_dirs"]), 1)
        self.assertIn("b:custom_mix_folder", report["referenced_dirs"][0]["refs"])

    def test_external_reference_is_flagged_protected(self):
        external = os.path.join(self._tmp.name, "outside")
        _fill_dir(external, 64)
        _write_task(os.path.join(self.ws, "task-c"), "c", custom_mix_folder=external)
        report = scan_workspace(self.ws)
        self.assertEqual(len(report["external_refs"]), 1)
        self.assertEqual(report["orphans"], [])

    def test_stale_path_matches_by_dirname(self):
        """盘符/根路径迁移后 step2_output 失效：同名目录仍按引用保护。"""
        _fill_dir(os.path.join(self.ws, "a1b2c3d4"), 256)
        _write_task(os.path.join(self.ws, "task-m"), "m",
                    step2_output="g:\\old\\workspace\\a1b2c3d4")
        report = scan_workspace(self.ws)
        self.assertEqual(report["orphans"], [])
        self.assertEqual(len(report["referenced_dirs"]), 1)
        self.assertEqual(report["referenced_dirs"][0]["matched_by"], "name")

    def test_broken_task_json_is_skipped_with_warning(self):
        bad = os.path.join(self.ws, "task-bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "task.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        _fill_dir(os.path.join(self.ws, "voice-x"), 10)
        report = scan_workspace(self.ws)
        self.assertEqual(len(report["task_errors"]), 1)
        self.assertEqual(len(report["orphans"]), 1)

    def test_scan_is_read_only(self):
        _write_task(os.path.join(self.ws, "task-a"), "a",
                    step2_output=os.path.join(self.ws, "voice-used"))
        _fill_dir(os.path.join(self.ws, "voice-used"), 100)
        _fill_dir(os.path.join(self.ws, "voice-orphan"), 50)
        before = self._snapshot()
        report = scan_workspace(self.ws)
        format_report(report)  # 渲染也不许写
        self.assertEqual(self._snapshot(), before)

    def test_report_mentions_readonly_and_protections(self):
        _write_task(os.path.join(self.ws, "task-a"), "a",
                    step2_output=os.path.join(self.ws, "voice-used"))
        _fill_dir(os.path.join(self.ws, "voice-used"), 100)
        _fill_dir(os.path.join(self.ws, "voice-orphan"), 50)
        text = "\n".join(format_report(scan_workspace(self.ws)))
        self.assertIn("只读扫描", text)
        self.assertIn("受保护", text)
        self.assertIn("voice-orphan", text)


if __name__ == "__main__":
    unittest.main()
