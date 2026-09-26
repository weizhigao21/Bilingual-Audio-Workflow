# -*- coding: utf-8 -*-
"""批量执行器：信号定义、生命周期与执行模式分发。

各执行模式（按任务/按步骤/单步同步/流水线）拆分到 mixin 模块。
"""
import os
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from ...config import WorkflowConfig
from ...task_manager import TaskInfo, STEP_DONE, STEP_SKIPPED, STEP_FAILED, STEP_RUNNING
from ..step1_whisper import WhisperWorker, WhisperBatchWorker
from ..step2_tts import TTSBridgeWorker
from ..step3_mixer import MixerWorker, MixerBatchWorker
from .by_task_mixin import ByTaskMixin
from .by_step_mixin import ByStepMixin
from .single_step_mixin import SingleStepMixin
from .pipeline_mixin import PipelineMixin


class BatchExecutor(
    ByTaskMixin, ByStepMixin, SingleStepMixin, PipelineMixin, QThread
):
    """批量执行器。"""
    # (task_index, total, step) — step=0 表示任务开始
    progress_signal = pyqtSignal(int, int, int)
    # (step, progress 0-100) — 步骤内进度，用于更新对应步骤面板的进度条
    step_progress_signal = pyqtSignal(int, int)
    # (step, status_text) — 步骤内状态文本（如"混音中 x/N · 当前 xx.mp3"）
    step_status_signal = pyqtSignal(int, str)
    # (step, total) — 步骤内总任务数（如 TTS 的总片段数），用于设置进度条上限
    step_total_signal = pyqtSignal(int, int)
    log_signal = pyqtSignal(str)
    task_started = pyqtSignal(str)        # task_id
    task_finished = pyqtSignal(str, bool) # (task_id, success)
    # (success_count, fail_count, skipped_count)
    finished_signal = pyqtSignal(int, int, int)

    def __init__(self, tasks, steps, config: WorkflowConfig,
                 order: str = "by_task", parent=None):
        """批量执行器。

        Args:
            tasks: 要执行的任务列表
            steps: 要执行的步骤列表，如 [1, 2, 3]
            config: WorkflowConfig
            order: 遍历顺序
                "by_task" — 按任务：每个任务依次跑完所有步骤，再跑下一个任务
                "by_step" — 按步骤：先把所有任务的step1跑完，再跑step2，再step3
        """
        super().__init__(parent)
        self.tasks = tasks
        self.steps = steps
        self.config = config
        self.order = order
        self._stop_flag = False
        self._current_worker = None
        self._active_workers = set()       # 流水线模式下正在运行的 worker
        self._active_lock = threading.Lock()

    def stop(self):
        self._stop_flag = True
        if self._current_worker:
            try:
                self._current_worker.stop()
            except Exception:
                pass
        # 流水线模式：停止所有正在运行的 worker
        with self._active_lock:
            workers = list(self._active_workers)
        for w in workers:
            try:
                w.stop()
            except Exception:
                pass

    def _track_worker(self, worker, active: bool):
        with self._active_lock:
            if active:
                self._active_workers.add(worker)
            else:
                self._active_workers.discard(worker)

    def _resolve_source_path(self, task: TaskInfo) -> bool:
        """检查任务源文件是否存在；不存在时尝试常见路径变体自动修复。

        常见失效场景：源文件夹被程序重命名添加"双语-"前缀（或用户手动改名），
        任务.source_path 仍指向旧路径。这里沿父目录链逐级查找带"双语-"前缀
        的目录段，生成"去前缀/加前缀"候选路径，找到则更新 task.source_path。

        Returns:
            True: 源文件可用（可能已自动修正 source_path）
            False: 源文件不存在且无法修复（调用方应跳过该任务）
        """
        if os.path.exists(task.source_path):
            return True

        src = task.source_path
        parent = os.path.dirname(src)
        name = os.path.basename(src)

        # 收集父目录链（文件所在目录 → 盘符根）
        chain = []
        cur = parent
        while cur and cur != os.path.dirname(cur):
            chain.append(cur)
            cur = os.path.dirname(cur)

        candidates = []
        for d in chain:
            try:
                seg = os.path.basename(d)
                rel = os.path.relpath(parent, d)
                if seg.startswith("双语-") and len(seg) > 3:
                    # 去前缀：双语-XXX → XXX
                    alt_dir = os.path.join(os.path.dirname(d), seg[3:])
                    candidates.append(os.path.join(alt_dir, rel, name))
                else:
                    # 加前缀：XXX → 双语-XXX
                    alt_dir = os.path.join(os.path.dirname(d), f"双语-{seg}")
                    candidates.append(os.path.join(alt_dir, rel, name))
            except (OSError, ValueError):
                continue

        for cand in candidates:
            if os.path.exists(cand):
                self.log_signal.emit(
                    f"[批量] [{task.source_name}] 源路径失效，自动修正: "
                    f"{task.source_path} → {cand}"
                )
                task.source_path = cand
                return True
        self.log_signal.emit(
            f"[批量] [{task.source_name}] 源文件不存在: {task.source_path}"
        )
        return False

    def run(self):
        total = len(self.tasks)

        if self.order == "by_step":
            success, fail, skipped = self._run_by_step(total)
        elif self.order == "pipeline":
            success, fail, skipped = self._run_pipeline(total)
        else:
            success, fail, skipped = self._run_by_task(total)

        self.log_signal.emit(
            f"\n[批量] 全部完成: 成功 {success}, 失败 {fail}, 跳过 {skipped}"
        )
        self.finished_signal.emit(success, fail, skipped)

    def _create_worker(self, task: TaskInfo, step: int):
        if step == 1:
            return WhisperWorker(task, self.config)
        elif step == 2:
            return TTSBridgeWorker(task, self.config)
        elif step == 3:
            return MixerWorker(task, self.config)
        return None
