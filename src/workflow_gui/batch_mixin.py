# -*- coding: utf-8 -*-
"""主窗口 Mixin：批量执行与全部清空。"""
from PyQt6.QtWidgets import QMessageBox, QLabel


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

        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QCheckBox, QDialogButtonBox
        )
        from ..widgets.settings_group import SettingsGroup, SegmentedControl
        dialog = QDialog(self)
        dialog.setWindowTitle("批量执行 - 选择步骤")
        dialog.setMinimumWidth(440)
        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(20, 20, 20, 16)
        dlg_layout.setSpacing(12)

        whisper_available = self.config.is_configured()
        if not whisper_available:
            notice = QLabel(
                "未配置有效的字幕提取程序路径，字幕提取及完整自动流水线不可用。\n"
                "需要自行准备字幕：通过“+ 添加字幕”关联，或导入时使用音频旁的同名字幕。"
                "已有字幕的任务仍可使用语音→混音流水线。"
            )
            notice.setWordWrap(True)
            notice.setProperty("role", "error")
            dlg_layout.addWidget(notice)

        group = SettingsGroup("执行步骤",
                              f"将对 {len(self.task_queue.tasks)} 个任务批量执行以下步骤")
        step_names = {1: "步骤1: 字幕提取", 2: "步骤2: 语音生成", 3: "步骤3: 音频混音"}
        checks = {}
        for step in (1, 2, 3):
            cb = QCheckBox(step_names[step])
            cb.setChecked(True)
            if step == 1 and not whisper_available:
                cb.setChecked(False)
                cb.setEnabled(False)
                cb.setToolTip("请通过工具栏的“配置”设置含 infer.exe 的字幕提取程序目录。")
            group.add_widget_row(cb)
            checks[step] = cb
        dlg_layout.addWidget(group)

        order_group = SettingsGroup("执行顺序")
        order_combo = SegmentedControl([
            ("流水线", "pipeline"),
            ("按步骤", "by_step"),
            ("按任务", "by_task"),
        ])
        order_desc = QLabel(
            "流水线：字幕就绪即语音→混音（推荐）\n"
            "按步骤：先全部字幕→再全部语音→再全部混音\n"
            "按任务：每个任务跑完三步再跑下一个"
        )
        order_desc.setWordWrap(True)
        order_desc.setProperty("role", "muted")
        order_group.add_wide_row("模式", "", order_combo)
        order_group.add_widget_row(order_desc)
        dlg_layout.addWidget(order_group)

        hint = QLabel("已完成的步骤会自动跳过；失败的任务会跳过后续步骤继续下一个。")
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

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        steps = [s for s in (1, 2, 3) if checks[s].isChecked()]
        if not steps:
            QMessageBox.warning(self, "提示", "请至少选择一个步骤。")
            return

        if 1 in steps and not self._check_whisper_available():
            return
        if not self._check_subtitles_available(self.task_queue.tasks, steps):
            return

        order = order_combo.current_data()

        # 启动批量执行
        from ..steps.batch_executor import BatchExecutor
        self._tts_total = 0  # 批量/流水线模式下进度为 0-100 百分比，重置片段总数记录
        # 重置三个步骤进度条，避免残留上次运行的值（批量执行中由累计进度驱动）
        for p in self.step_panels.values():
            p.progress.setRange(0, 100)
            p.progress.setValue(0)
            p.set_detail("")
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
        """批量进度：底栏移除后改为"每进入一个任务记一行日志"。

        步骤内部进度由各步骤卡的 3px 进度条呈现（step_progress_signal 驱动），
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
            self.task_queue.set_current(task_id)
            self._refresh_step_panels(task)

    def _on_batch_task_finished(self, task_id: str, success: bool):
        task = self.task_queue.get_task(task_id)
        if task:
            self.task_queue.update_task(task)
            if self.task_queue.current and self.task_queue.current.task_id == task_id:
                self._refresh_step_panels(task)

    def _on_batch_finished(self, success: int, fail: int, skipped: int):
        self.step_panels[2].set_detail("")
        self._batch_executor = None
        self.batch_btn.setEnabled(True)
        self.batch_stop_btn.setEnabled(False)
        # 恢复步骤面板按钮状态（任务或文件夹组模式）
        if self.task_queue.current:
            self._refresh_step_panels(self.task_queue.current)
        elif self._current_group:
            self._show_group_summary(self._current_group)
        self._show_toast(f"批量执行完成：成功 {success} · 失败 {fail} · 跳过 {skipped}")
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
