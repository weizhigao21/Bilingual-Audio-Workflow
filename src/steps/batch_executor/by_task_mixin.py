# -*- coding: utf-8 -*-
"""批量执行器 Mixin：按任务遍历模式。"""
from ...task_manager import STEP_RUNNING, STEP_DONE, STEP_SKIPPED


class ByTaskMixin:
    """按任务遍历：每个任务依次执行所有选定步骤。"""

    def _run_by_task(self, total: int):
        """按任务遍历：每个任务依次执行所有选定步骤。"""
        success = 0
        fail = 0
        skipped = 0

        for i, task in enumerate(self.tasks):
            if self._stop_flag:
                break

            # 源文件检查：不存在且无法自动修复 → 跳过任务，避免后续步骤白跑
            if not self._resolve_source_path(task):
                self.log_signal.emit(f"[批量] [{task.source_name}] 源文件不存在，跳过任务")
                skipped += 1
                self.task_finished.emit(task.task_id, False)
                continue

            self.log_signal.emit(f"\n[批量] === 任务 {i + 1}/{total}: {task.source_name} ===")
            self.task_started.emit(task.task_id)
            self.progress_signal.emit(i, total, 0)

            # 检查任务是否已有正在运行的步骤
            has_running = any(
                task.step_status(s) == STEP_RUNNING for s in (1, 2, 3)
            )
            if has_running:
                self.log_signal.emit(f"[批量] 任务有正在运行的步骤，跳过")
                skipped += 1
                self.task_finished.emit(task.task_id, False)
                continue

            task_ok = True
            for step in self.steps:
                if self._stop_flag:
                    break

                status = task.step_status(step)
                if status in (STEP_DONE, STEP_SKIPPED):
                    self.log_signal.emit(f"[批量] 步骤{step} 已完成/跳过，跳过")
                    continue

                if not task.is_step_ready(step):
                    self.log_signal.emit(f"[批量] 步骤{step} 前置未完成，跳过此任务")
                    task_ok = False
                    break

                self.log_signal.emit(f"[批量] 执行步骤{step}...")
                self.progress_signal.emit(i, total, step)

                ok, msg = self._run_single_step(task, step, i, total)
                # 推进该步骤的任务间累计进度（即使失败也推进，避免卡住）
                self.step_progress_signal.emit(step, int((i + 1) * 100 / total))
                if ok:
                    self.log_signal.emit(f"[批量] 步骤{step} 完成: {msg}")
                else:
                    self.log_signal.emit(f"[批量] 步骤{step} 失败: {msg}")
                    task_ok = False
                    break  # 此任务后续步骤不再执行

            if task_ok:
                success += 1
            else:
                fail += 1
            self.task_finished.emit(task.task_id, task_ok)

        return success, fail, skipped
