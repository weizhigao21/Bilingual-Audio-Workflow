# -*- coding: utf-8 -*-
"""主窗口 Mixin：批量执行与全部清空。"""
from PyQt6.QtWidgets import QMessageBox, QLabel, QHBoxLayout


class BatchMixin:
    """批量执行相关方法。"""

    # ========== 批量执行 ==========
    def _on_batch_execute(self):
        """弹出步骤选择对话框，启动批量执行。"""
        if self._batch_executor:
            QMessageBox.warning(self, "提示", "批量执行正在进行中。")
            return
        if not self.task_queue.tasks:
            QMessageBox.warning(self, "提示", "任务列表为空，请先添加视频文件。")
            return
        # 检查是否有正在运行的单步 worker
        running = [s for s in (1, 2, 3) if self._workers.get(s)]
        if running:
            QMessageBox.warning(self, "提示", f"步骤 {running} 正在运行中，请先停止。")
            return

        # 步骤选择对话框
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QCheckBox, QDialogButtonBox,
            QComboBox, QGroupBox as QDlgGroupBox
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("批量执行 - 选择步骤")
        dialog.setMinimumWidth(400)
        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.addWidget(QLabel(f"将对 {len(self.task_queue.tasks)} 个任务批量执行以下步骤："))

        step_names = {1: "步骤1: 字幕提取", 2: "步骤2: 语音生成", 3: "步骤3: 音频混音"}
        checks = {}
        for step in (1, 2, 3):
            cb = QCheckBox(step_names[step])
            cb.setChecked(True)
            dlg_layout.addWidget(cb)
            checks[step] = cb

        # 执行顺序选择
        order_group = QDlgGroupBox("执行顺序")
        order_layout = QHBoxLayout(order_group)
        order_layout.addWidget(QLabel("模式:"))
        order_combo = QComboBox()
        order_combo.addItem("流水线：语音与混音交错并行（最快）", "pipeline")
        order_combo.addItem("按步骤：先全部字幕→再全部语音→再全部混音", "by_step")
        order_combo.addItem("按任务：每个任务跑完三步再跑下一个", "by_task")
        order_layout.addWidget(order_combo, 1)
        dlg_layout.addWidget(order_group)

        hint = QLabel("已完成的步骤会自动跳过；失败的任务会跳过后续步骤继续下一个。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        dlg_layout.addWidget(hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        dlg_layout.addWidget(btns)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        steps = [s for s in (1, 2, 3) if checks[s].isChecked()]
        if not steps:
            QMessageBox.warning(self, "提示", "请至少选择一个步骤。")
            return

        order = order_combo.currentData()

        # 启动批量执行
        from ..steps.batch_executor import BatchExecutor
        self._tts_total = 0  # 批量/流水线模式下进度为 0-100 百分比，重置片段总数记录
        # 重置三个步骤进度条，避免残留上次运行的值（批量执行中由累计进度驱动）
        for p in self.step_panels.values():
            p.progress.setRange(0, 100)
            p.progress.setValue(0)
        self._batch_executor = BatchExecutor(
            list(self.task_queue.tasks), steps, self.config,
            order=order, parent=self
        )
        self._connect_batch_signals()

        self.batch_btn.setEnabled(False)
        self.batch_stop_btn.setEnabled(True)
        # 禁用所有步骤面板的开始按钮
        for panel in self.step_panels.values():
            panel.start_btn.setEnabled(False)

        self._append_log(f"[批量] 开始执行 {len(self.task_queue.tasks)} 个任务，步骤 {steps}")
        self._batch_executor.start()

    def _connect_batch_signals(self):
        """连接批量执行器的全部信号到主窗口槽（批量/组执行共用）。"""
        self._batch_executor.log_signal.connect(self._append_log)
        self._batch_executor.progress_signal.connect(self._on_batch_progress)
        self._batch_executor.step_progress_signal.connect(self._on_step_progress)
        self._batch_executor.step_status_signal.connect(self._on_step_status)
        self._batch_executor.step_total_signal.connect(self._on_step_total)
        self._batch_executor.task_started.connect(self._on_batch_task_started)
        self._batch_executor.task_finished.connect(self._on_batch_task_finished)
        self._batch_executor.finished_signal.connect(self._on_batch_finished)

    def _on_batch_stop(self):
        if self._batch_executor:
            self._append_log("[批量] 正在停止...")
            self._batch_executor.stop()

    def _on_batch_progress(self, task_idx: int, total: int, step: int):
        if step == 0:
            self.status_bar_label.setText(f"批量: 任务 {task_idx + 1}/{total}")
        else:
            self.status_bar_label.setText(f"批量: 任务 {task_idx + 1}/{total} 步骤{step}")
            # 步骤进度条由 step_progress_signal 驱动（任务内进度 / 任务间累计进度），
            # 不再在每任务开始时重置为 0，避免累计进度被清零

    def _on_batch_task_started(self, task_id: str):
        task = self.task_queue.get_task(task_id)
        if task:
            self.task_queue.set_current(task_id)
            self._refresh_step_panels(task)

    def _on_batch_task_finished(self, task_id: str, success: bool):
        task = self.task_queue.get_task(task_id)
        if task:
            self.task_queue.update_task(task)
            if self.task_queue.current and self.task_queue.current.task_id == task_id:
                self._refresh_step_panels(task)

    def _on_batch_finished(self, success: int, fail: int, skipped: int):
        self._batch_executor = None
        self.batch_btn.setEnabled(True)
        self.batch_stop_btn.setEnabled(False)
        # 恢复步骤面板按钮状态（任务或文件夹组模式）
        if self.task_queue.current:
            self._refresh_step_panels(self.task_queue.current)
        elif self._current_group:
            self._show_group_summary(self._current_group)
        self.status_bar_label.setText("就绪")
        # 批量完成后，统一重命名文件夹导入的源文件夹
        if self.config.mixer_cfg.get("folder_prefix", True):
            self._rename_batch_folders()

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
