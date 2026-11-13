# -*- coding: utf-8 -*-
"""主窗口 Mixin：批量执行与全部清空。"""
from PyQt6.QtWidgets import QMessageBox, QLabel

from ..task_manager import STEP_DONE, STEP_FAILED, STEP_SKIPPED
from ..ui_theme import repolish


class BatchMixin:
    """批量执行相关方法。"""

    # ========== 批量执行 ==========
    def _on_batch_execute(self):
        """批量执行：先选范围，再看统计，最后选步骤；顺序收进「高级」。"""
        if self._batch_executor:
            QMessageBox.warning(self, "提示", "批量执行正在进行中。")
            return
        if not self.task_queue.tasks:
            QMessageBox.warning(self, "提示", "任务列表为空，请先添加视频文件。")
            return
        running = [s for s in (1, 2, 3) if self._workers.get(s)]
        if running:
            QMessageBox.warning(self, "提示", f"步骤 {running} 正在运行中，请先停止。")
            return

        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QCheckBox, QDialogButtonBox, QHBoxLayout
        )
        from ..widgets.settings_group import SettingsGroup, SegmentedControl
        dialog = QDialog(self)
        dialog.setWindowTitle("批量执行")
        dialog.setMinimumWidth(480)
        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(20, 18, 20, 14)
        dlg_layout.setSpacing(10)

        whisper_available = self.config.is_configured()
        if not whisper_available:
            notice = QLabel(
                "未配置有效的字幕提取程序路径，字幕提取及完整自动流水线不可用。\n"
                "已有字幕的任务仍可使用「配音 → 混音」。")
            notice.setWordWrap(True)
            notice.setProperty("role", "error")
            dlg_layout.addWidget(notice)

        # 1) 范围：默认全部；只跑未完成的更常见，放第二位
        scope_group = SettingsGroup("执行范围")
        scope_control = SegmentedControl([
            ("全部任务", "all"), ("仅未完成", "unfinished"), ("当前组", "current")])
        scope_control.set_current_data("all")
        scope_group.add_wide_row("范围", "", scope_control)
        scope_hint = QLabel("")
        scope_hint.setWordWrap(True)
        scope_hint.setProperty("role", "muted")
        scope_group.add_widget_row(scope_hint)
        dlg_layout.addWidget(scope_group)
        current_available = bool(self._current_group or self.task_queue.current)
        if not current_available:
            for button, data in scope_control._buttons:
                if data == "current":
                    button.setEnabled(False)
                    button.setToolTip("请先在左侧选中任务或文件夹组")

        # 2) 步骤
        step_group = SettingsGroup("执行步骤", "已完成的步骤会自动跳过。")
        step_names = {1: "步骤 1 · 字幕提取", 2: "步骤 2 · 配音",
                      3: "步骤 3 · 混音"}
        checks = {}
        for step in (1, 2, 3):
            cb = QCheckBox(step_names[step])
            cb.setChecked(True)
            if step == 1 and not whisper_available:
                cb.setChecked(False)
                cb.setEnabled(False)
                cb.setToolTip("请先在「配置 → 字幕提取」设置含 infer.exe 的目录。")
            step_group.add_widget_row(cb)
            checks[step] = cb
        dlg_layout.addWidget(step_group)

        stats = QLabel("")
        stats.setWordWrap(True)
        stats.setProperty("role", "secondary")
        dlg_layout.addWidget(stats)

        # 3) 顺序：默认流水线，需要时才展开
        advanced = QCheckBox("高级：自定义执行顺序")
        advanced.setToolTip("默认流水线：字幕就绪即配音→混音，吞吐最高")
        dlg_layout.addWidget(advanced)
        order_group = SettingsGroup("执行顺序")
        order_combo = SegmentedControl([
            ("流水线（推荐）", "pipeline"), ("按步骤", "by_step"),
            ("按任务", "by_task")])
        order_group.add_wide_row("模式", "", order_combo)
        order_group.add_widget_row(QLabel(
            "流水线：字幕就绪即进入配音/混音\n"
            "按步骤：先全部字幕 → 再全部配音 → 再全部混音\n"
            "按任务：一个任务跑完三步再跑下一个"))
        order_group.setVisible(False)
        dlg_layout.addWidget(order_group)
        advanced.toggled.connect(order_group.setVisible)

        hint = QLabel("失败的任务会跳过后续步骤继续下一个；结束后可从结果条重跑失败项。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        dlg_layout.addWidget(hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("开始执行")
        btns.button(QDialogButtonBox.StandardButton.Ok).setProperty("kind", "primary")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        dlg_layout.addWidget(btns)

        def selected_steps():
            return [s for s in (1, 2, 3) if checks[s].isChecked()]

        def scope_tasks(scope):
            tasks = list(self.task_queue.tasks)
            if scope == "unfinished":
                return [t for t in tasks
                        if any(t.step_status(s) not in (STEP_DONE, STEP_SKIPPED)
                               for s in (1, 2, 3))]
            if scope == "current":
                return self._scope_tasks()
            return tasks

        def refresh_stats():
            tasks = scope_tasks(scope_control.current_data())
            steps = selected_steps()
            pending = [t for t in tasks
                       if any(t.step_status(s) not in (STEP_DONE, STEP_SKIPPED)
                              for s in steps)]
            done = len(tasks) - len(pending)
            if not tasks:
                stats.setText("当前范围没有任务。")
            else:
                stats.setText(
                    f"范围 {len(tasks)} 个任务：{len(pending)} 个需要执行，"
                    f"{done} 个已完成会自动跳过。")

        scope_control.current_changed.connect(lambda _v: refresh_stats())
        for cb in checks.values():
            cb.toggled.connect(lambda _v: refresh_stats())
        refresh_stats()

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        steps = selected_steps()
        if not steps:
            QMessageBox.warning(self, "提示", "请至少选择一个步骤。")
            return
        tasks = scope_tasks(scope_control.current_data())
        if not tasks:
            QMessageBox.information(self, "提示", "当前范围没有可执行的任务。")
            return
        if 1 in steps and not self._check_whisper_available():
            return
        if not self._check_subtitles_available(tasks, steps):
            return
        order = order_combo.current_data() if advanced.isChecked() else "pipeline"
        self._start_batch(tasks, steps, order)

    def _start_batch(self, tasks, steps, order):
        """构造并启动 BatchExecutor；导入/构造失败必须给出可见提示。"""
        try:
            from ..steps.batch_executor import BatchExecutor
            self._tts_total = 0
            for p in self.step_panels.values():
                p.set_progress(0)
                p.set_detail("")
            self._batch_executor = BatchExecutor(
                list(tasks), steps, self.config, order=order, parent=self)
            self._connect_batch_signals()
        except Exception as exc:
            self._batch_executor = None
            self._append_log(f"[批量] 启动失败: {type(exc).__name__}: {exc}")
            QMessageBox.critical(self, "批量执行失败",
                                 f"{type(exc).__name__}: {exc}")
            return
        self._batch_steps = list(steps)
        self._append_log(
            f"[批量] 开始执行 {len(tasks)} 个任务，步骤 {steps}")
        if getattr(self, "result_bar", None) is not None:
            self.result_bar.hide()
        self._refresh_run_buttons()
        self._batch_executor.start()

    def _connect_batch_signals(self):
        """连接批量执行器的全部信号到主窗口槽（批量/组执行共用）。"""
        self._batch_executor.log_signal.connect(self._append_log)
        self._batch_executor.progress_signal.connect(self._on_batch_progress)
        self._batch_executor.step_progress_signal.connect(self._on_step_progress)
        self._batch_executor.step_status_signal.connect(self._on_step_status)
        self._batch_executor.step_total_signal.connect(self._on_step_total)
        self._batch_executor.task_started.connect(self._on_batch_task_started)
        self._batch_executor.task_updated.connect(self._on_batch_task_updated)
        self._batch_executor.task_finished.connect(self._on_batch_task_finished)
        self._batch_executor.finished_signal.connect(self._on_batch_finished)

    def _on_batch_stop(self):
        if self._batch_executor:
            self._append_log("[批量] 正在停止...")
            self._batch_executor.stop()

    def _on_batch_progress(self, task_idx: int, total: int, step: int):
        """批量进度：底栏移除后改为"每进入一个任务记一行日志"。

        步骤内部进度由各步骤卡的 6px 进度条呈现（step_progress_signal 驱动），
        这里只在任务序号变化时落一行，避免每一步刷屏。
        """
        if step != 0 and task_idx == getattr(self, "_last_batch_idx", None):
            return
        self._last_batch_idx = task_idx
        suffix = f" 步骤{step}" if step else ""
        self._append_log(f"[批量] 任务 {task_idx + 1}/{total}{suffix}")

    def _on_batch_task_started(self, task_id: str):
        task = self.task_queue.get_task(task_id)
        if task:
            self.task_queue.update_task(task)
            if self.task_queue.current is None and self._current_group is None:
                self.task_queue.set_current(task_id)

    def _on_batch_task_updated(self, task_id: str):
        task = self.task_queue.get_task(task_id)
        if task:
            self.task_queue.update_task(task)

    def _on_batch_task_finished(self, task_id: str, success: bool):
        task = self.task_queue.get_task(task_id)
        if task:
            self.task_queue.update_task(task)
            if self.task_queue.current and self.task_queue.current.task_id == task_id:
                self._refresh_step_panels(task)

    def _on_batch_finished(self, success: int, fail: int, skipped: int):
        executor = self._batch_executor
        executed = list(executor.tasks) if executor is not None else []
        steps = list(getattr(self, "_batch_steps", (1, 2, 3)))
        stopped = bool(executor is not None and executor._stop_flag)
        if executor is not None:
            executor.wait()
        for panel in self.step_panels.values():
            panel.set_detail("")
        self._batch_executor = None
        # 恢复步骤面板按钮状态（任务或文件夹组模式）
        if self.task_queue.current:
            self._refresh_step_panels(self.task_queue.current)
        elif self._current_group:
            self._show_group_summary(self._current_group)
        self._refresh_run_buttons()
        label = "批量执行已停止" if stopped else "批量执行完成"
        self._show_toast(f"{label}：成功 {success} · 失败 {fail} · 跳过 {skipped}")
        failed_tasks = [t for t in executed
                        if any(t.step_status(s) == STEP_FAILED for s in steps)]
        self._last_failed = (failed_tasks, steps)
        self._show_batch_result(success, fail, skipped, failed_tasks, stopped)
        # 批量完成后，统一重命名文件夹导入的源文件夹
        if self.config.mixer_cfg.get("folder_prefix", False):
            self._rename_batch_folders()

    def _show_batch_result(self, success, fail, skipped, failed_tasks, stopped):
        bar = getattr(self, "result_bar", None)
        if bar is None:
            return
        if failed_tasks:
            bar.setProperty("state", "error")
            text = (f"{'已停止' if stopped else '执行完成'}：成功 {success} · "
                    f"失败 {len(failed_tasks)} · 跳过 {skipped}")
            if self.result_retry_btn:
                self.result_retry_btn.setVisible(True)
        else:
            bar.setProperty("state", "success")
            text = f"{'已停止' if stopped else '全部完成'}：成功 {success} · 跳过 {skipped}"
            self.result_retry_btn.setVisible(False)
        self.result_label.setText(text)
        repolish(bar)
        bar.setVisible(True)

    def _on_dismiss_result(self):
        if getattr(self, "result_bar", None) is not None:
            self.result_bar.hide()

    def _on_retry_failed(self):
        """只重跑失败的任务；失败步骤按原批量步骤集合重试。"""
        if self._batch_executor is not None or any(self._workers.values()):
            QMessageBox.warning(self, "提示", "已有任务正在执行，请先停止。")
            return
        failed_tasks, steps = getattr(self, "_last_failed", ([], [1, 2, 3]))
        if not failed_tasks:
            self._on_dismiss_result()
            return
        self._start_batch(failed_tasks, steps, "pipeline")

    def _on_clear_all(self):
        """清空所有任务。"""
        if not self.task_queue.tasks:
            QMessageBox.information(self, "提示", "任务列表已为空。")
            return
        # 检查批量执行
        if self._batch_executor:
            QMessageBox.warning(self, "提示", "批量执行正在进行中，请先停止。")
            return
        # 检查是否有正在运行的任务
        running_steps = []
        for step in (1, 2, 3):
            if self._workers.get(step):
                running_steps.append(step)
        if running_steps:
            QMessageBox.warning(
                self, "提示",
                f"步骤 {running_steps} 正在运行中，请先停止后再清空。"
            )
            return

        count = len(self.task_queue.tasks)
        msg = QMessageBox(self)
        msg.setWindowTitle("全部清空")
        msg.setText(f"确定清空所有 {count} 个任务？")
        msg.setInformativeText(
            "「仅清空列表」：从界面移除所有任务，保留已生成的文件。\n"
            "「清空并删除文件」：同时删除 workspace 下的任务目录（不可恢复）。"
        )
        btn_list_only = msg.addButton("仅清空列表", QMessageBox.ButtonRole.AcceptRole)
        btn_delete_files = msg.addButton("清空并删除文件", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_list_only:
            self.task_queue.clear_all(delete_files=False)
            self._append_log(f"[清空] 已清空 {count} 个任务（保留文件）")
        elif clicked is btn_delete_files:
            deleted = self.task_queue.clear_all(delete_files=True)
            self._append_log(
                f"[清空] 已清空 {count} 个任务，删除任务目录 {deleted} 个"
            )
