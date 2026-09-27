# -*- coding: utf-8 -*-
"""批量执行器 Mixin：流水线模式（字幕串行 + 语音/混音交错并行）。"""
import queue
import threading

from ...task_manager import STEP_DONE, STEP_SKIPPED


class PipelineMixin:
    """流水线模式：字幕先全部完成，然后语音与混音交错并行。"""

    def _run_pipeline(self, total: int):
        """流水线模式：字幕先全部完成，然后语音与混音交错并行。

        调度策略：
          1. 字幕（步骤1）按导入文件夹集中完成，失败的进入 failed 集合
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
            self._run_whisper_batch(total, failed)
            self.log_signal.emit("[流水线] 阶段1 字幕提取完成")

        # ---------- 阶段2：语音 + 混音流水线 ----------
        if 2 not in self.steps and 3 not in self.steps:
            # 只跑了字幕
            pass
        else:
            # 任务级语音并行 = 同时生成语音的"音频数"（独立配置，默认 2），
            # 与片段级并发（Edge 全局共享线程池/并发上限）分开，
            # 避免多个音频同时全速生成导致日志混杂、触发 Edge 限流
            tts_workers = max(
                1, int(self.config.tts_cfg.get("pipeline_tts_workers", 2))
            )
            tts_workers = min(tts_workers, max(1, len(self.tasks))) if 2 in self.steps else 0
            # 混音并行度直接使用混音配置的"线程数"(thread_count)，
            # 让用户在混音配置面板的调整真正生效（多任务同时混音）
            mix_workers = max(1, int(self.config.mixer_cfg.get("thread_count", 4)))
            # 不超过任务数，避免空转线程
            mix_workers = min(mix_workers, max(1, len(self.tasks))) if 3 in self.steps else 0
            self.log_signal.emit(
                f"\n[流水线] ==== 阶段2 语音+混音交错: "
                f"语音并行(音频) {tts_workers}, 混音并行 {mix_workers} ===="
            )

            tts_queue = queue.Queue()
            mix_queue = queue.Queue()

            # 各任务内进度独立累计，再折算为整批进度，允许并发且不回退。
            progress_lock = threading.Lock()
            total_tts_tasks = 0
            total_mix_tasks = 0
            tts_progress = {}
            mix_progress = {}

            # 只放需要处理的步骤（跳过已完成/已失败的任务）
            for task in self.tasks:
                if is_failed(task.task_id):
                    continue
                # 源文件检查：不存在且无法自动修复 → 跳过任务（语音/混音都依赖源文件）
                if not self._resolve_source_path(task):
                    self.log_signal.emit(f"[流水线] [{task.source_name}] 源文件不存在，跳过任务")
                    mark_failed(task.task_id)
                    self.task_finished.emit(task.task_id, False)
                    continue
                if 2 in self.steps and task.step_status(2) not in (STEP_DONE, STEP_SKIPPED):
                    total_tts_tasks += 1
                    tts_queue.put(task)
                elif 3 in self.steps and task.step_status(3) not in (STEP_DONE, STEP_SKIPPED):
                    # 语音已就绪（步骤2 不在执行范围或已完成），直接混音
                    total_mix_tasks += 1
                    mix_queue.put(task)
            # 语音任务完成后都会流入混音队列，补上这部分混音总数
            if 3 in self.steps:
                total_mix_tasks += total_tts_tasks

            def update_step_progress(step, task_id, value):
                """并发任务各自累计，避免较慢任务把进度条拉回。"""
                with progress_lock:
                    values = tts_progress if step == 2 else mix_progress
                    values[task_id] = max(values.get(task_id, 0), min(100, max(0, value)))
                    total_tasks = total_tts_tasks if step == 2 else total_mix_tasks
                    overall = int(sum(values.values()) / max(1, total_tasks))
                    self.step_progress_signal.emit(step, overall)

            tts_done_event = threading.Event()

            def tts_worker_fn():
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
                        self.log_signal.emit(
                            f"[流水线] [{task.source_name}] 开始语音生成"
                        )
                        ok, msg = self._run_single_step_sync(
                            task, 2, progress_callback=lambda value, task_id=task.task_id:
                            update_step_progress(2, task_id, value)
                        )
                        # 语音完成立即通知刷新任务列表（显示语音进度）
                        self.task_finished.emit(task.task_id, ok)
                        if ok:
                            self.log_signal.emit(
                                f"[流水线] [{task.source_name}] 语音完成 → 待混音"
                            )
                            if 3 in self.steps and task.step_status(3) not in (STEP_DONE, STEP_SKIPPED):
                                mix_queue.put(task)
                        else:
                            self.log_signal.emit(
                                f"[流水线] [{task.source_name}] 语音失败: {msg}"
                            )
                            mark_failed(task.task_id)
                        # 语音进度按任务累计推进
                        update_step_progress(2, task.task_id, 100)
                        tts_queue.task_done()
                finally:
                    pass

            def mix_worker_fn():
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
                            # 语音阶段已失败的任务不再混音，明确打日志避免"静默跳过"
                            self.log_signal.emit(
                                f"[流水线] [{task.source_name}] 语音阶段失败，跳过混音"
                            )
                            mix_queue.task_done()
                            continue
                        self.task_started.emit(task.task_id)
                        self.log_signal.emit(
                            f"[流水线] [{task.source_name}] 开始混音"
                        )
                        ok, msg = self._run_single_step_sync(
                            task, 3, progress_callback=lambda value, task_id=task.task_id:
                            update_step_progress(3, task_id, value)
                        )
                        if ok:
                            self.log_signal.emit(f"[流水线] [{task.source_name}] 混音完成")
                        else:
                            self.log_signal.emit(f"[流水线] [{task.source_name}] 混音失败: {msg}")
                            mark_failed(task.task_id)
                        self.task_finished.emit(task.task_id, ok)
                        # 混音进度按任务累计推进
                        update_step_progress(3, task.task_id, 100)
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
            self.log_signal.emit("[流水线] 语音与混音全部完成")

            # 语音/混音进度条收尾
            if 2 in self.steps:
                self.step_progress_signal.emit(2, 100)
            if 3 in self.steps:
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
