# -*- coding: utf-8 -*-
"""批量执行器：遍历多个任务，按选定步骤批量执行。

在 QThread 中用 QEventLoop 等待每步 worker 完成，
支持跳过失败继续、中途停止。

执行模式（order）：
    - "by_task"    按任务：每个任务依次跑完所有步骤，再跑下一个任务
    - "by_step"    按步骤：先把所有任务的step1跑完，再跑step2，再step3
    - "pipeline"   流水线：字幕先全部完成，然后语音生成与混音交错并行
                   （语音队列并行度=edge_threads，混音队列并行度可配，默认1）
"""
import queue
import threading

from PyQt6.QtCore import QThread, pyqtSignal, QEventLoop, Qt

from ..config import WorkflowConfig
from ..task_manager import TaskInfo, STEP_DONE, STEP_SKIPPED, STEP_FAILED, STEP_RUNNING
from .step1_whisper import WhisperWorker, WhisperBatchWorker
from .step2_tts import TTSBridgeWorker
from .step3_mixer import MixerWorker, MixerBatchWorker


class BatchExecutor(QThread):
    """批量执行器。"""
    # (task_index, total, step) — step=0 表示任务开始
    progress_signal = pyqtSignal(int, int, int)
    # (step, progress 0-100) — 步骤内进度，用于更新对应步骤面板的进度条
    step_progress_signal = pyqtSignal(int, int)
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

    def _run_by_task(self, total: int):
        """按任务遍历：每个任务依次执行所有选定步骤。"""
        success = 0
        fail = 0
        skipped = 0

        for i, task in enumerate(self.tasks):
            if self._stop_flag:
                break

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
        worker.task_result_signal.connect(on_task_result)
        worker.finished_signal.connect(on_finished)

        worker.start()
        self._current_worker = worker
        loop.exec()
        self._current_worker = None

        self.progress_signal.emit(total, total, 3)
        self.step_progress_signal.emit(3, 100)
        self.log_signal.emit(f"[批量] 步骤3 并行批量完成")

    def _run_single_step(self, task: TaskInfo, step: int,
                         task_index: int = 0, total: int = 1):
        """执行单个任务的单个步骤，返回 (success, output_or_error)。

        task_index/total 用于把任务内进度(0-100)映射到任务间累计进度，
        使批量/组执行时步骤进度条按"已完成任务数/总任务数"递增。
        步骤2（语音）多任务时忽略任务内片段进度，按任务级累计推进，
        避免"第一个任务完成就 100%"的误导。
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

        worker.log_signal.connect(self.log_signal.emit)

        def on_progress(v):
            # 多任务时语音进度按任务级累计（每完成一个任务 +1/N，由调用方推进），
            # 忽略任务内片段进度；单任务仍显示片段级进度
            if total > 1 and step == 2:
                return
            if total <= 1:
                self.step_progress_signal.emit(step, v)
            else:
                base = task_index / total
                mapped = int((base + (v / 100.0) / total) * 100)
                self.step_progress_signal.emit(step, max(0, min(100, mapped)))

        worker.progress_signal.connect(on_progress)
        # TTS 步骤有 total_signal，单任务时转发以设置进度条上限（片段总数）。
        # 多任务（批量/组）时进度为任务级累计百分比（0-100），
        # 若用片段总数做进度条 range，百分比值会被 clamp 成 100%（进度条直接满格），
        # 因此多任务时不再连接 total_signal。
        if hasattr(worker, 'total_signal') and total <= 1:
            worker.total_signal.connect(
                lambda t, s=step: self.step_total_signal.emit(s, t)
            )
        worker.finished_signal.connect(on_finished)
        worker.start()

        self._current_worker = worker
        loop.exec()
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

        任务内进度（步骤1/3，0-100 百分比）映射到
        [base_pct, base_pct+span_pct] 区间转发，使进度条在任务执行期间
        持续前进（修复"混音等待音频时一直显示 0%"）；任务完成后由
        _run_pipeline 按"每完成一个任务 +1/N"的累计值推进到区间的精确位置。
        步骤2（TTS）的 progress 信号是"已完成片段数"（0..total，非百分比），
        多任务并发时无法映射，故不转发，由累计值驱动。
        """
        worker = self._create_worker(task, step)
        if not worker:
            return False, "无法创建 worker"

        task.set_step_status(step, STEP_RUNNING)
        worker.log_signal.connect(self.log_signal.emit)

        def on_progress(v):
            # TTS 进度是片段计数（非 0-100），交由任务级累计推进
            if step == 2:
                return
            mapped = base_pct + int((v / 100.0) * span_pct)
            self.step_progress_signal.emit(step, max(0, min(100, mapped)))

        worker.progress_signal.connect(on_progress)

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

    def _run_pipeline(self, total: int):
        """流水线模式：字幕先全部完成，然后语音与混音交错并行。

        调度策略：
          1. 字幕（步骤1）逐个串行完成，失败的进入 failed 集合
          2. 语音生成（步骤2）：tts_workers 个线程并行处理就绪任务，
             每个任务完成后立即放入混音队列
          3. 混音（步骤3）：mix_workers 个线程（默认1）从混音队列取任务，
             一有空闲就继续混音——实现"混音完成又有语音完成就继续混音"
        """
        failed = set()
        failed_lock = threading.Lock()

        def mark_failed(task_id):
            with failed_lock:
                failed.add(task_id)

        def is_failed(task_id):
            with failed_lock:
                return task_id in failed

        # ---------- 阶段1：字幕（只跑步骤1） ----------
        if 1 in self.steps:
            self.log_signal.emit(f"\n[流水线] ==== 阶段1 字幕提取: 共 {total} 个任务 ====")
            for i, task in enumerate(self.tasks):
                if self._stop_flag:
                    break
                if task.step_status(1) in (STEP_DONE, STEP_SKIPPED):
                    continue
                if task.step_status(1) == STEP_RUNNING:
                    continue
                if not task.is_step_ready(1):
                    mark_failed(task.task_id)
                    self.task_finished.emit(task.task_id, False)
                    continue
                self.task_started.emit(task.task_id)
                self.progress_signal.emit(i, total, 1)
                denom = max(1, total)
                ok, msg = self._run_single_step_sync(
                    task, 1,
                    int(i * 100 / denom), int(100 / denom)
                )
                self.step_progress_signal.emit(1, int((i + 1) * 100 / denom))
                # 无论成功失败都通知刷新任务列表（成功也要发，否则列表不更新进度）
                self.task_finished.emit(task.task_id, ok)
                if ok:
                    self.log_signal.emit(f"[流水线] [{task.source_name}] 字幕完成")
                else:
                    self.log_signal.emit(f"[流水线] [{task.source_name}] 字幕失败: {msg}")
                    mark_failed(task.task_id)

        # ---------- 阶段2：语音 + 混音流水线 ----------
        if 2 not in self.steps and 3 not in self.steps:
            # 只跑了字幕
            pass
        else:
            # 任务级语音并行 = 同时生成语音的"音频数"（独立配置，默认 2），
            # 与任务内片段并发(edge_threads)分开，避免多个音频同时全速生成
            # 导致日志混杂、触发 Edge 限流
            tts_workers = max(
                1, int(self.config.tts_cfg.get("pipeline_tts_workers", 2))
            )
            tts_workers = min(tts_workers, max(1, len(self.tasks)))
            # 混音并行度直接使用混音配置的"线程数"(thread_count)，
            # 让用户在混音配置面板的调整真正生效（多任务同时混音）
            mix_workers = max(1, int(self.config.mixer_cfg.get("thread_count", 4)))
            # 不超过任务数，避免空转线程
            mix_workers = min(mix_workers, max(1, len(self.tasks)))
            self.log_signal.emit(
                f"\n[流水线] ==== 阶段2 语音+混音交错: "
                f"语音并行(音频) {tts_workers}, 混音并行 {mix_workers} ===="
            )

            tts_queue = queue.Queue()
            mix_queue = queue.Queue()

            # 任务级累计进度：每完成一个任务的语音/混音，进度 +1/N
            # （不转发任务内进度，避免多任务并发时进度条左右横跳）
            progress_lock = threading.Lock()
            tts_done = 0
            mix_done = 0
            total_tts_tasks = 0
            total_mix_tasks = 0

            # 只放需要处理的步骤（跳过已完成/已失败的任务）
            for task in self.tasks:
                if is_failed(task.task_id):
                    continue
                if 2 in self.steps and task.step_status(2) not in (STEP_DONE, STEP_SKIPPED):
                    total_tts_tasks += 1
                    tts_queue.put(task)
                elif task.step_status(3) not in (STEP_DONE, STEP_SKIPPED):
                    # 语音已就绪（步骤2 不在执行范围或已完成），直接混音
                    total_mix_tasks += 1
                    mix_queue.put(task)
            # 语音任务完成后都会流入混音队列，补上这部分混音总数
            total_mix_tasks += total_tts_tasks

            tts_done_event = threading.Event()

            def tts_worker_fn():
                nonlocal tts_done
                try:
                    while not self._stop_flag:
                        try:
                            task = tts_queue.get(timeout=0.2)
                        except queue.Empty:
                            break
                        if self._stop_flag:
                            tts_queue.task_done()
                            break
                        self.task_started.emit(task.task_id)
                        with progress_lock:
                            base = int(tts_done * 100 / max(1, total_tts_tasks))
                            span = int(100 / max(1, total_tts_tasks))
                        ok, msg = self._run_single_step_sync(task, 2, base, span)
                        # 语音完成立即通知刷新任务列表（显示语音进度）
                        self.task_finished.emit(task.task_id, ok)
                        if ok:
                            self.log_signal.emit(
                                f"[流水线] [{task.source_name}] 语音完成 → 待混音"
                            )
                            mix_queue.put(task)
                        else:
                            self.log_signal.emit(
                                f"[流水线] [{task.source_name}] 语音失败: {msg}"
                            )
                            mark_failed(task.task_id)
                        # 语音进度按任务累计推进
                        with progress_lock:
                            tts_done += 1
                            self.step_progress_signal.emit(
                                2, int(tts_done * 100 / max(1, total_tts_tasks))
                            )
                        tts_queue.task_done()
                finally:
                    pass

            def mix_worker_fn():
                nonlocal mix_done
                try:
                    while not self._stop_flag:
                        try:
                            task = mix_queue.get(timeout=0.2)
                        except queue.Empty:
                            # 语音全部完成且队列空 → 结束
                            if tts_queue.empty() and tts_done_event.is_set():
                                break
                            continue
                        if self._stop_flag:
                            mix_queue.task_done()
                            break
                        if is_failed(task.task_id):
                            mix_queue.task_done()
                            continue
                        self.task_started.emit(task.task_id)
                        with progress_lock:
                            base = int(mix_done * 100 / max(1, total_mix_tasks))
                            span = int(100 / max(1, total_mix_tasks))
                        ok, msg = self._run_single_step_sync(task, 3, base, span)
                        if ok:
                            self.log_signal.emit(f"[流水线] [{task.source_name}] 混音完成")
                        else:
                            self.log_signal.emit(f"[流水线] [{task.source_name}] 混音失败: {msg}")
                            mark_failed(task.task_id)
                        self.task_finished.emit(task.task_id, ok)
                        # 混音进度按任务累计推进
                        with progress_lock:
                            mix_done += 1
                            self.step_progress_signal.emit(
                                3, int(mix_done * 100 / max(1, total_mix_tasks))
                            )
                        mix_queue.task_done()
                finally:
                    pass

            tts_threads = []
            for _ in range(tts_workers):
                t = threading.Thread(target=tts_worker_fn, daemon=True)
                t.start()
                tts_threads.append(t)
            mix_threads = []
            for _ in range(mix_workers):
                t = threading.Thread(target=mix_worker_fn, daemon=True)
                t.start()
                mix_threads.append(t)

            # 先等所有语音线程结束（队列取空自然退出，或 stop 置位退出），
            # 再通知混音线程"不再有新任务"，等其处理完剩余队列
            for t in tts_threads:
                t.join()
            tts_done_event.set()
            for t in mix_threads:
                t.join()

            # 语音/混音进度条收尾
            self.step_progress_signal.emit(2, 100)
            self.step_progress_signal.emit(3, 100)

        # ---------- 统计 ----------
        success = 0
        fail = 0
        skipped = 0
        for task in self.tasks:
            if is_failed(task.task_id):
                fail += 1
            else:
                all_done = all(
                    task.step_status(s) in (STEP_DONE, STEP_SKIPPED)
                    for s in self.steps
                )
                if all_done:
                    success += 1
                else:
                    skipped += 1
        return success, fail, skipped

    def _create_worker(self, task: TaskInfo, step: int):
        if step == 1:
            return WhisperWorker(task, self.config)
        elif step == 2:
            return TTSBridgeWorker(task, self.config)
        elif step == 3:
            return MixerWorker(task, self.config)
        return None
