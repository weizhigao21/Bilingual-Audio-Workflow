# -*- coding: utf-8 -*-
"""主窗口 Mixin：单步执行、组内逐步执行与步骤信号处理。"""
import os

from PyQt6.QtWidgets import QMessageBox

from ..task_manager import (
    TaskInfo, TaskGroup,
    STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, STEP_SKIPPED
)


class StepMixin:
    """步骤执行相关方法。"""

    # ========== 步骤执行 ==========
    def _on_start_step(self, step: int):
        # 组模式：对组内所有任务逐个执行该步骤
        if self._current_group:
            self._start_group_step(self._current_group, step)
            return
        task = self.task_queue.current
        if not task:
            QMessageBox.warning(self, "提示", "请先选择一个任务。")
            return
        if not os.path.exists(task.source_path):
            QMessageBox.warning(
                self, "提示",
                f"源文件不存在，无法执行：\n{task.source_path}\n\n"
                f"可能已被移动或重命名（如添加\"双语-\"前缀），请重新导入任务。"
            )
            return
        if not task.is_step_ready(step):
            QMessageBox.warning(self, "提示", f"步骤 {step} 的前置步骤尚未完成。")
            return

        # 创建 worker（延迟导入步骤模块，避免启动时加载重库）
        from ..steps.step1_whisper import WhisperWorker
        from ..steps.step2_tts import TTSBridgeWorker
        from ..steps.step3_mixer import MixerWorker

        if step == 1:
            worker = WhisperWorker(task, self.config)
        elif step == 2:
            worker = TTSBridgeWorker(task, self.config)
            self._tts_total = 0
            worker.total_signal.connect(self._on_tts_total)
            worker.status_signal.connect(
                lambda text, s=step: self._on_step_status(s, text)
            )
        else:
            worker = MixerWorker(task, self.config)

        worker.log_signal.connect(self._append_log)
        worker.progress_signal.connect(
            lambda v, s=step: self._on_step_progress(s, v)
        )
        worker.finished_signal.connect(
            lambda ok, msg, s=step, t=task: self._on_step_finished(s, t, ok, msg)
        )

        self._workers[step] = worker
        task.set_step_status(step, STEP_RUNNING)
        task.set_step_error(step, "")
        self.step_panels[step].set_running(True)
        self.step_panels[step].set_status(STEP_RUNNING)
        # 启动前重置该步骤进度条（TTS 的 total 信号会随后调整上限）
        self.step_panels[step].progress.setRange(0, 100)
        self.step_panels[step].progress.setValue(0)
        self._refresh_step_panels(task)
        worker.start()

    def _on_stop_step(self, step: int):
        worker = self._workers.get(step)
        if worker:
            worker.stop()
            self._append_log(f"[步骤{step}] 正在停止...")

    def _start_group_step(self, group: TaskGroup, step: int):
        """对文件夹组内所有任务逐个执行指定步骤（复用批量执行器）。"""
        if self._batch_executor:
            QMessageBox.warning(self, "提示", "已有批量执行正在进行中，请先停止。")
            return
        if not group.tasks:
            QMessageBox.information(self, "提示", "该文件夹组没有任务。")
            return
        pending = [t for t in group.tasks
                   if t.step_status(step) not in (STEP_DONE, STEP_SKIPPED)]
        if not pending:
            QMessageBox.information(
                self, "提示", f"组内所有任务的步骤{step} 均已完成或已跳过。"
            )
            return
        not_ready = [t.source_name for t in pending if not t.is_step_ready(step)]
        if not_ready:
            QMessageBox.warning(
                self, "提示",
                f"以下任务步骤 {step} 前置未完成，无法执行：\n"
                + "\n".join(not_ready[:10])
            )
            return

        from ..steps.batch_executor import BatchExecutor
        self._tts_total = 0  # 组执行时进度为 0-100 百分比，重置片段总数记录
        # 重置三个步骤进度条，避免残留上次运行的值
        for p in self.step_panels.values():
            p.progress.setRange(0, 100)
            p.progress.setValue(0)
        self._batch_executor = BatchExecutor(
            list(group.tasks), [step], self.config, order="by_task", parent=self
        )
        self._connect_batch_signals()
        self.batch_btn.setEnabled(False)
        self.batch_stop_btn.setEnabled(True)
        for panel in self.step_panels.values():
            panel.start_btn.setEnabled(False)
        self._append_log(
            f"[文件夹] 开始逐个执行: 「{group.group_name}」步骤{step} "
            f"({len(group.tasks)} 个任务)"
        )
        self._batch_executor.start()

    def _on_tts_total(self, total: int):
        # 进度条已统一为 0-100 百分比（TTS 进度由 TTSBridgeWorker 归一化），
        # 片段总数仅记录备用，不再作为进度条 range
        self._tts_total = total

    def _on_step_progress(self, step: int, value: int):
        # 所有步骤（含 TTS）的 progress 均为 0-100 百分比，
        # 统一 range 显示，避免片段数与百分比混用导致 clamp 假 100%/回退
        panel = self.step_panels[step]
        panel.progress.setRange(0, 100)
        panel.progress.setValue(max(0, min(100, value)))

    def _on_step_status(self, step: int, text: str):
        """步骤内状态文本显示在进度条上（如"混音中 2/4 · 当前 xx.mp3"）。"""
        panel = self.step_panels[step]
        panel.progress.setFormat(f"{text}  |  %p%")

    def _on_step_total(self, step: int, total: int):
        """步骤内总任务数（如 TTS 的总片段数），仅记录，进度条 range 恒为 0-100。"""
        if step == 2 and total > 0:
            self._tts_total = total

    def _on_step_finished(self, step: int, task: TaskInfo, success: bool, msg: str):
        if success:
            task.set_step_status(step, STEP_DONE)
            task.set_step_output(step, msg)
            self._append_log(f"[步骤{step}] 完成: {msg}")
            # 文件夹导入且混音完成后，重命名源文件夹添加"双语-"前缀
            if step == 3 and task.from_folder and self.config.mixer_cfg.get("folder_prefix", True):
                self._rename_source_folder(task)
        else:
            task.set_step_status(step, STEP_FAILED)
            task.set_step_error(step, msg)
            self._append_log(f"[步骤{step}] 失败: {msg}")

        # 释放 worker 前必须等线程真正退出：finished_signal 在 run() 的
        # finally（防休眠关闭/缓存落盘）之前发出，立即丢弃引用会触发
        # "QThread: Destroyed while thread is still running" 原生 abort 闪退
        worker = self._workers.get(step)
        if worker is not None:
            worker.wait()
            self._workers[step] = None
        self.task_queue.update_task(task)
        self._refresh_step_panels(task)
        if step == 3:
            from ..steps.audio_utils import clear_mix_cache
            clear_mix_cache()
