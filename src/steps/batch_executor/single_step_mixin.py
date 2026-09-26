# -*- coding: utf-8 -*-
"""批量执行器 Mixin：单任务单步执行（事件循环等待 / 同步等待两种方式）。"""
from PyQt6.QtCore import QEventLoop

from ...task_manager import TaskInfo, STEP_RUNNING, STEP_DONE, STEP_FAILED


class SingleStepMixin:
    """单步执行相关方法。"""

    def _run_single_step(self, task: TaskInfo, step: int,
                         task_index: int = 0, total: int = 1):
        """执行单个任务的单个步骤，返回 (success, output_or_error)。

        task_index/total 用于把任务内进度(0-100)映射到任务间累计进度，
        使批量/组执行时步骤进度条按"已完成任务数/总任务数"递增。
        所有步骤（含语音）的 progress 均为 0-100 百分比，
        多任务时映射为"任务间累计 + 任务内片段进度"的平滑值。
        """
        worker = self._create_worker(task, step)
        if not worker:
            return False, "无法创建 worker"

        task.set_step_status(step, STEP_RUNNING)
        loop = QEventLoop()
        result = [None]

        def on_finished(ok, msg):
            result[0] = (ok, msg)
            loop.quit()

        # 统一给 worker 日志加任务名前缀：批量/流水线多任务并发时，
        # 单任务 worker（WhisperWorker/TTSBridgeWorker/MixerWorker）内部日志
        # 不带任务名，不加前缀会完全无法区分归属
        worker.log_signal.connect(
            lambda m, n=task.source_name: self.log_signal.emit(f"[{n}] {m}")
        )

        def on_progress(v):
            # 所有步骤的进度统一为 0-100 百分比（TTS 已在 TTSBridgeWorker 归一化）。
            # 单任务直接转发；多任务映射到任务间累计区间，任务内平滑推进
            if total <= 1:
                self.step_progress_signal.emit(step, v)
            else:
                base = task_index / total
                mapped = int((base + (v / 100.0) / total) * 100)
                self.step_progress_signal.emit(step, max(0, min(100, mapped)))

        worker.progress_signal.connect(on_progress)
        # TTS 步骤有状态文本（片段 x/N · 剩余时间），转发到步骤面板进度条
        if hasattr(worker, 'status_signal'):
            worker.status_signal.connect(
                lambda text, s=step: self.step_status_signal.emit(s, text)
            )
        # TTS 步骤有 total_signal（片段总数），转发仅用于记录/日志展示。
        # 进度条 range 已统一为 0-100，不再依赖片段总数。
        if hasattr(worker, 'total_signal'):
            worker.total_signal.connect(
                lambda t, s=step: self.step_total_signal.emit(s, t)
            )
        worker.finished_signal.connect(on_finished)
        worker.start()

        self._current_worker = worker
        loop.exec()
        # finished_signal 在 run() 的 finally 之前发出，必须等线程真正退出再释放
        worker.wait()
        self._current_worker = None

        ok, msg = result[0] if result[0] else (False, "未知错误")
        if ok:
            task.set_step_status(step, STEP_DONE)
            task.set_step_output(step, msg)
        else:
            task.set_step_status(step, STEP_FAILED)
            task.set_step_error(step, msg)
        return ok, msg

    def _run_single_step_sync(self, task: TaskInfo, step: int,
                              base_pct: int = 0, span_pct: int = 100):
        """同步执行单个任务的单个步骤（供流水线线程调用）。

        与 _run_single_step 不同：不使用 QEventLoop 等待信号，
        而是 start() 后 wait() 阻塞，直接读取 worker.result 属性。

        任务内进度（各步骤均为 0-100 百分比）映射到
        [base_pct, base_pct+span_pct] 区间转发，使进度条在任务执行期间
        持续前进（修复"混音等待音频时一直显示 0%"）；任务完成后由
        _run_pipeline 按"每完成一个任务 +1/N"的累计值推进到区间的精确位置。
        """
        worker = self._create_worker(task, step)
        if not worker:
            return False, "无法创建 worker"

        task.set_step_status(step, STEP_RUNNING)
        # 与 _run_single_step 一致：worker 日志统一加任务名前缀
        worker.log_signal.connect(
            lambda m, n=task.source_name: self.log_signal.emit(f"[{n}] {m}")
        )

        def on_progress(v):
            # 所有步骤的进度统一为 0-100 百分比（TTS 已在 TTSBridgeWorker 归一化），
            # 映射到 [base_pct, base_pct+span_pct] 区间，任务内平滑推进
            mapped = base_pct + int((v / 100.0) * span_pct)
            self.step_progress_signal.emit(step, max(0, min(100, mapped)))

        worker.progress_signal.connect(on_progress)
        # TTS 步骤有状态文本（片段 x/N · 剩余时间），转发到步骤面板进度条
        if hasattr(worker, 'status_signal'):
            worker.status_signal.connect(
                lambda text, s=step: self.step_status_signal.emit(s, text)
            )

        self._track_worker(worker, True)
        worker.start()
        worker.wait()
        self._track_worker(worker, False)

        ok, msg = getattr(worker, "result", None) or (False, "未知错误")
        if ok:
            task.set_step_status(step, STEP_DONE)
            task.set_step_output(step, msg)
        else:
            task.set_step_status(step, STEP_FAILED)
            task.set_step_error(step, msg)
        return ok, msg
