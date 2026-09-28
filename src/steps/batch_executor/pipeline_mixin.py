# -*- coding: utf-8 -*-
"""流水线：字幕集中识别，逐份派发语音和混音。"""
import queue
import threading

from ...task_manager import STEP_DONE, STEP_SKIPPED, STEP_FAILED


class PipelineMixin:
    """infer.exe 持续识别，字幕就绪的任务立即进入后续队列。"""

    def _run_pipeline(self, total: int):
        failed = set()
        failed_lock = threading.Lock()

        def mark_failed(task_id):
            with failed_lock:
                failed.add(task_id)

        def is_failed(task_id):
            with failed_lock:
                return task_id in failed

        def counts():
            success = fail = skipped = 0
            for task in self.tasks:
                if is_failed(task.task_id):
                    fail += 1
                elif all(task.step_status(step) in (STEP_DONE, STEP_SKIPPED)
                         for step in self.steps):
                    success += 1
                else:
                    skipped += 1
            return success, fail, skipped

        if 2 not in self.steps and 3 not in self.steps:
            if 1 in self.steps:
                self._run_whisper_batch(total, failed)
            return counts()

        eligible = []
        for task in self.tasks:
            if self._resolve_source_path(task):
                eligible.append(task)
            else:
                mark_failed(task.task_id)
                self.task_finished.emit(task.task_id, False)
        eligible_ids = {task.task_id for task in eligible}

        needs_tts = {task.task_id for task in eligible if 2 in self.steps
                     and task.step_status(2) not in (STEP_DONE, STEP_SKIPPED)}
        needs_mix = {task.task_id for task in eligible if 3 in self.steps
                     and task.step_status(3) not in (STEP_DONE, STEP_SKIPPED)}
        tts_workers = min(max(1, int(self.config.tts_cfg.get("pipeline_tts_workers", 2))),
                          len(needs_tts))
        mix_workers = min(max(1, int(self.config.mixer_cfg.get("thread_count", 4))),
                          len(needs_mix))
        self.log_signal.emit(
            f"\n[流水线] 字幕就绪即派发：语音并行(音频) {tts_workers}, 混音并行 {mix_workers}"
        )

        tts_queue = queue.Queue()
        mix_queue = queue.Queue()
        producer_done = threading.Event()
        tts_done = threading.Event()
        routed_tasks = set()
        progress_lock = threading.Lock()
        tts_progress = {}
        mix_progress = {}
        active_mix_names = {}

        def update_progress(step, task_id, value):
            with progress_lock:
                values = tts_progress if step == 2 else mix_progress
                total_tasks = len(needs_tts) if step == 2 else len(needs_mix)
                values[task_id] = max(values.get(task_id, 0), min(100, max(0, value)))
                self.step_progress_signal.emit(
                    step, int(sum(values.values()) / max(1, total_tasks))
                )

        def update_mix_status(task, active):
            with progress_lock:
                if active:
                    active_mix_names[task.task_id] = task.source_name[:4]
                else:
                    active_mix_names.pop(task.task_id, None)
                names = '，'.join(active_mix_names.values())
                self.step_status_signal.emit(3, f"当前任务：{names}" if names else "")

        def fail_task(task, step, message):
            task.set_step_status(step, STEP_FAILED)
            task.set_step_error(step, message)
            mark_failed(task.task_id)
            self.log_signal.emit(f"[流水线] [{task.source_name}] 步骤{step} 失败: {message}")
            self.task_finished.emit(task.task_id, False)

        def dispatch_ready(task):
            # 此回调在字幕批量执行器的事件循环中调用；重复确认不会重复排队。
            if (self._stop_flag or task.task_id not in eligible_ids
                    or is_failed(task.task_id) or task.task_id in routed_tasks):
                return
            if 1 in self.steps and task.step_status(1) not in (STEP_DONE, STEP_SKIPPED):
                return
            routed_tasks.add(task.task_id)
            if task.task_id in needs_tts:
                if task.is_step_ready(2):
                    tts_queue.put(task)
                else:
                    fail_task(task, 2, "字幕尚未就绪")
            elif task.task_id in needs_mix:
                if task.is_step_ready(3):
                    mix_queue.put(task)
                else:
                    fail_task(task, 3, "配音尚未就绪")

        def execute(task, step):
            try:
                return self._run_single_step_sync(
                    task, step, progress_callback=lambda value:
                    update_progress(step, task.task_id, value)
                )
            except Exception as exc:
                task.set_step_status(step, STEP_FAILED)
                task.set_step_error(step, str(exc))
                return False, str(exc)

        def tts_worker_fn():
            while not self._stop_flag:
                try:
                    task = tts_queue.get(timeout=0.2)
                except queue.Empty:
                    if producer_done.is_set() and tts_queue.empty():
                        break
                    continue
                try:
                    if self._stop_flag:
                        break
                    self.task_started.emit(task.task_id)
                    self.log_signal.emit(f"[流水线] [{task.source_name}] 开始语音生成")
                    ok, msg = execute(task, 2)
                    self.task_finished.emit(task.task_id, ok)
                    if ok:
                        self.log_signal.emit(f"[流水线] [{task.source_name}] 语音完成")
                        if task.task_id in needs_mix:
                            mix_queue.put(task)
                    else:
                        mark_failed(task.task_id)
                        self.log_signal.emit(f"[流水线] [{task.source_name}] 语音失败: {msg}")
                    update_progress(2, task.task_id, 100)
                finally:
                    tts_queue.task_done()

        def mix_worker_fn():
            while not self._stop_flag:
                try:
                    task = mix_queue.get(timeout=0.2)
                except queue.Empty:
                    if producer_done.is_set() and tts_done.is_set() and mix_queue.empty():
                        break
                    continue
                try:
                    if self._stop_flag:
                        break
                    if is_failed(task.task_id):
                        continue
                    self.task_started.emit(task.task_id)
                    self.log_signal.emit(f"[流水线] [{task.source_name}] 开始混音")
                    update_mix_status(task, True)
                    try:
                        ok, msg = execute(task, 3)
                    finally:
                        update_mix_status(task, False)
                    self.task_finished.emit(task.task_id, ok)
                    if ok:
                        self.log_signal.emit(f"[流水线] [{task.source_name}] 混音完成")
                    else:
                        mark_failed(task.task_id)
                        self.log_signal.emit(f"[流水线] [{task.source_name}] 混音失败: {msg}")
                    update_progress(3, task.task_id, 100)
                finally:
                    mix_queue.task_done()

        # 消费线程必须先启动，并等到字幕生产者结束才退出空队列。
        tts_threads = [threading.Thread(target=tts_worker_fn, daemon=True)
                       for _ in range(tts_workers)]
        mix_threads = [threading.Thread(target=mix_worker_fn, daemon=True)
                       for _ in range(mix_workers)]
        for thread in tts_threads + mix_threads:
            thread.start()
        try:
            # 已有字幕/配音的任务直接进入后续队列。
            for task in eligible:
                dispatch_ready(task)
            if 1 in self.steps and not self._stop_flag:
                self.log_signal.emit(f"[流水线] 字幕集中识别: 共 {total} 个任务")
                self._run_whisper_batch(total, failed, on_task_ready=dispatch_ready)
                self.log_signal.emit("[流水线] 字幕识别结束")
            for task in eligible:
                dispatch_ready(task)
        finally:
            producer_done.set()
            for thread in tts_threads:
                thread.join()
            tts_done.set()
            for thread in mix_threads:
                thread.join()

        if not self._stop_flag:
            for step in (2, 3):
                if step in self.steps:
                    self.step_progress_signal.emit(step, 100)
            self.log_signal.emit("[流水线] 字幕、语音与混音处理结束")
        return counts()
