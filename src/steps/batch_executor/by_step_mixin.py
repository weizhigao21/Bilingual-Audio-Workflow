# -*- coding: utf-8 -*-
"""批量执行器 Mixin：按步骤遍历模式（含字幕批量与混音并行批量）。"""
from PyQt6.QtCore import QEventLoop

from ...task_manager import STEP_RUNNING, STEP_DONE, STEP_SKIPPED, STEP_FAILED
from ..step1_whisper import WhisperBatchWorker
from ..step3_mixer import MixerBatchWorker


class ByStepMixin:
    """按步骤遍历：先把所有任务的step1跑完，再跑step2，再step3。"""

    def _run_by_step(self, total: int):
        """按步骤遍历：先把所有任务的step1跑完，再跑step2，再step3。"""
        failed_tasks = set()

        for step in self.steps:
            if self._stop_flag:
                break

            step_name = {1: "字幕提取", 2: "语音生成", 3: "音频混音"}.get(step, f"步骤{step}")
            self.log_signal.emit(f"\n[批量] ===== {step_name}(步骤{step}): 处理全部 {total} 个任务 =====")

            # 步骤1启用批量处理时，一次性处理所有任务
            if step == 1 and self.config.whisper_cfg.get("enable_batching", False):
                self._run_whisper_batch(total, failed_tasks)
                continue

            # 步骤3启用批量并行时，多任务同时混音
            if step == 3 and self.config.mixer_cfg.get("enable_batch_parallel", True):
                self._run_mixer_batch(total, failed_tasks)
                continue

            for i, task in enumerate(self.tasks):
                if self._stop_flag:
                    break

                # 已失败的任务跳过
                if task.task_id in failed_tasks:
                    continue

                # 源文件检查：不存在且无法自动修复 → 该任务所有步骤都跳过
                if not self._resolve_source_path(task):
                    self.log_signal.emit(f"[批量] [{task.source_name}] 源文件不存在，跳过任务")
                    failed_tasks.add(task.task_id)
                    self.task_finished.emit(task.task_id, False)
                    continue

                # 检查是否正在运行
                if task.step_status(step) == STEP_RUNNING:
                    self.log_signal.emit(f"[批量] [{task.source_name}] 步骤{step} 正在运行，跳过")
                    continue

                # 已完成/跳过的步骤跳过
                status = task.step_status(step)
                if status in (STEP_DONE, STEP_SKIPPED):
                    continue

                # 检查前置
                if not task.is_step_ready(step):
                    self.log_signal.emit(
                        f"[批量] [{task.source_name}] 步骤{step} 前置未完成，标记失败"
                    )
                    failed_tasks.add(task.task_id)
                    self.task_finished.emit(task.task_id, False)
                    continue

                self.log_signal.emit(f"[批量] [{task.source_name}] 执行步骤{step}...")
                self.task_started.emit(task.task_id)
                self.progress_signal.emit(i, total, step)

                ok, msg = self._run_single_step(task, step, i, total)
                # 推进该步骤的任务间累计进度
                self.step_progress_signal.emit(step, int((i + 1) * 100 / total))
                if ok:
                    self.log_signal.emit(f"[批量] [{task.source_name}] 步骤{step} 完成: {msg}")
                else:
                    self.log_signal.emit(f"[批量] [{task.source_name}] 步骤{step} 失败: {msg}")
                    failed_tasks.add(task.task_id)
                self.task_finished.emit(task.task_id, ok)

        # 统计结果
        success = 0
        fail = 0
        skipped = 0
        for task in self.tasks:
            if task.task_id in failed_tasks:
                fail += 1
            else:
                # 检查所有选定步骤是否都完成
                all_done = all(
                    task.step_status(s) in (STEP_DONE, STEP_SKIPPED)
                    for s in self.steps
                )
                if all_done:
                    success += 1
                else:
                    skipped += 1

        return success, fail, skipped

    def _run_whisper_batch(self, total: int, failed_tasks: set):
        """步骤1批量处理：一次性把所有待处理任务的源文件传给 infer.exe。"""
        # 收集所有需要处理 step1 的任务
        pending_tasks = []
        for i, task in enumerate(self.tasks):
            if task.task_id in failed_tasks:
                continue
            status = task.step_status(1)
            if status in (STEP_DONE, STEP_SKIPPED):
                continue
            if status == STEP_RUNNING:
                self.log_signal.emit(f"[批量] [{task.source_name}] 步骤1 正在运行，跳过")
                continue
            if not task.is_step_ready(1):
                self.log_signal.emit(f"[批量] [{task.source_name}] 步骤1 前置未完成，标记失败")
                failed_tasks.add(task.task_id)
                self.task_finished.emit(task.task_id, False)
                continue
            pending_tasks.append(task)

        if not pending_tasks:
            self.log_signal.emit("[批量] 步骤1 无待处理任务")
            return

        self.log_signal.emit(f"[批量] 步骤1 批量模式: 一次性处理 {len(pending_tasks)} 个任务")
        self.progress_signal.emit(0, total, 1)

        # 标记所有任务为 running
        for task in pending_tasks:
            task.set_step_status(1, STEP_RUNNING)
            self.task_started.emit(task.task_id)

        worker = WhisperBatchWorker(pending_tasks, self.config)
        loop = QEventLoop()
        results = {}  # task_id -> (ok, msg)

        def on_task_result(task_id, ok, msg):
            results[task_id] = (ok, msg)
            task_obj = next((t for t in pending_tasks if t.task_id == task_id), None)
            if task_obj:
                if ok:
                    task_obj.set_step_status(1, STEP_DONE)
                    task_obj.set_step_output(1, msg)
                    self.log_signal.emit(f"[批量] [{task_obj.source_name}] 字幕完成: {msg}")
                else:
                    task_obj.set_step_status(1, STEP_FAILED)
                    task_obj.set_step_error(1, msg)
                    failed_tasks.add(task_id)
                    self.log_signal.emit(f"[批量] [{task_obj.source_name}] 字幕失败: {msg}")
                self.task_finished.emit(task_id, ok)

        def on_finished(success_count, fail_count):
            loop.quit()

        worker.log_signal.connect(self.log_signal.emit)
        # worker.progress_signal 是 (int)，转发为 step_progress_signal(step, value)
        worker.progress_signal.connect(
            lambda v: self.step_progress_signal.emit(1, v)
        )
        worker.task_result_signal.connect(on_task_result)
        worker.finished_signal.connect(on_finished)

        worker.start()
        self._current_worker = worker
        loop.exec()
        # finished_signal 在 run() 的 finally 之前发出，必须等线程真正退出再释放
        worker.wait()
        self._current_worker = None

        self.progress_signal.emit(total, total, 1)
        self.step_progress_signal.emit(1, 100)
        self.log_signal.emit(
            f"[批量] 步骤1 批量完成: 成功 {len(results) - sum(1 for ok, _ in results.values() if not ok)}, "
            f"失败 {sum(1 for ok, _ in results.values() if not ok)}"
        )

    def _run_mixer_batch(self, total: int, failed_tasks: set):
        """步骤3批量并行处理：用 ThreadPoolExecutor 同时混音多个任务。"""
        # 收集所有需要处理 step3 的任务
        pending_tasks = []
        for i, task in enumerate(self.tasks):
            if task.task_id in failed_tasks:
                continue
            status = task.step_status(3)
            if status in (STEP_DONE, STEP_SKIPPED):
                continue
            if status == STEP_RUNNING:
                self.log_signal.emit(f"[批量] [{task.source_name}] 步骤3 正在运行，跳过")
                continue
            if not task.is_step_ready(3):
                self.log_signal.emit(f"[批量] [{task.source_name}] 步骤3 前置未完成，标记失败")
                failed_tasks.add(task.task_id)
                self.task_finished.emit(task.task_id, False)
                continue
            pending_tasks.append(task)

        if not pending_tasks:
            self.log_signal.emit("[批量] 步骤3 无待处理任务")
            return

        thread_count = self.config.mixer_cfg.get("thread_count", 4)
        self.log_signal.emit(
            f"[批量] 步骤3 并行模式: {len(pending_tasks)} 个任务, "
            f"线程数={thread_count}"
        )
        self.progress_signal.emit(0, total, 3)

        # 标记所有任务为 running
        for task in pending_tasks:
            task.set_step_status(3, STEP_RUNNING)
            self.task_started.emit(task.task_id)

        worker = MixerBatchWorker(pending_tasks, self.config)
        loop = QEventLoop()

        def on_task_result(task_id, ok, msg):
            task_obj = next((t for t in pending_tasks if t.task_id == task_id), None)
            if task_obj:
                if ok:
                    task_obj.set_step_status(3, STEP_DONE)
                    task_obj.set_step_output(3, msg)
                else:
                    task_obj.set_step_status(3, STEP_FAILED)
                    task_obj.set_step_error(3, msg)
                    failed_tasks.add(task_id)
                self.task_finished.emit(task_id, ok)

        def on_finished(success_count, fail_count):
            loop.quit()

        worker.log_signal.connect(self.log_signal.emit)
        worker.progress_signal.connect(
            lambda pct: self.progress_signal.emit(int(pct * total / 100), total, 3)
        )
        # 同时转发到 step_progress_signal 更新步骤3面板进度条
        worker.progress_signal.connect(
            lambda v: self.step_progress_signal.emit(3, v)
        )
        # 状态文本（当前混音任务名等）转发到 GUI 显示
        worker.status_signal.connect(
            lambda text: self.step_status_signal.emit(3, text)
        )
        worker.task_result_signal.connect(on_task_result)
        worker.finished_signal.connect(on_finished)

        worker.start()
        self._current_worker = worker
        loop.exec()
        # finished_signal 在 run() 的 finally 之前发出，必须等线程真正退出再释放
        worker.wait()
        self._current_worker = None

        self.progress_signal.emit(total, total, 3)
        self.step_progress_signal.emit(3, 100)
        self.log_signal.emit(f"[批量] 步骤3 并行批量完成")
