# -*- coding: utf-8 -*-
"""主窗口 Mixin：任务/文件夹组选择、面板刷新与任务管理。"""
from PyQt6.QtWidgets import QMessageBox

from ..task_manager import (
    TaskInfo, TaskGroup,
    STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, STEP_SKIPPED
)


class TaskMixin:
    """任务选择与任务管理相关方法。"""

    # ========== 任务选择 ==========
    def _on_task_selected(self, task_id: str):
        self._current_group = None
        self.task_queue.set_current(task_id)

    def _on_group_selected(self, group_id: str):
        """选中文件夹组节点：右侧显示组汇总，步骤执行作用于组内全部任务。"""
        group = self.task_queue.get_group(group_id)
        if not group:
            return
        self._current_group = group
        self.task_queue.set_current(None)

    def _show_group_summary(self, group: TaskGroup):
        """在右侧面板显示文件夹组的汇总状态。"""
        done = group.done_count()
        total = len(group.tasks)
        self.current_task_label.setText(
            f"文件夹：{group.group_name}　({done}/{total} 完成 · {total} 个音频)"
        )
        for step in (1, 2, 3):
            panel = self.step_panels[step]
            status = self._group_step_status(group, step)
            panel.set_status(status)
            panel.set_output("")
            panel.progress.setRange(0, 100)
            panel.progress.setValue(int(group.progress() * 100))
            panel.start_btn.setEnabled(True)
            panel.stop_btn.setEnabled(False)

    @staticmethod
    def _group_step_status(group: TaskGroup, step: int) -> str:
        """组内某步骤的汇总状态：running > failed > done > pending。"""
        statuses = [t.step_status(step) for t in group.tasks]
        if any(s == STEP_RUNNING for s in statuses):
            return STEP_RUNNING
        if any(s == STEP_FAILED for s in statuses):
            return STEP_FAILED
        if statuses and all(s in (STEP_DONE, STEP_SKIPPED) for s in statuses):
            return STEP_DONE
        return STEP_PENDING

    def _on_current_changed(self, task: TaskInfo):
        if task is None:
            if self._current_group:
                self._show_group_summary(self._current_group)
                return
            self.current_task_label.setText("未选择任务")
            for panel in self.step_panels.values():
                panel.reset()
            return
        self._current_group = None
        self.current_task_label.setText(
            f"当前任务：{task.source_name}  (创建于 {task.created_at})"
        )
        self._refresh_step_panels(task)

    def _on_task_updated(self, task: TaskInfo):
        self.task_list.update_task_item(task)
        if self.task_queue.current and self.task_queue.current.task_id == task.task_id:
            self._refresh_step_panels(task)
        elif (self._current_group and not self._batch_executor
              and task.group_id == self._current_group.group_id):
            # 组内任务状态变化时刷新组汇总（批量执行中不刷新，避免干扰进度显示）
            self._show_group_summary(self._current_group)

    def _refresh_step_panels(self, task: TaskInfo):
        """根据任务状态刷新三个步骤面板。"""
        for step in (1, 2, 3):
            panel = self.step_panels[step]
            status = task.step_status(step)
            panel.set_status(status)

            output = task.step1_output if step == 1 else (
                task.step2_output if step == 2 else task.step3_output
            )
            panel.set_output(output or "")

            # 进度条按步骤状态重置，避免残留上一次运行的值：
            # 完成/跳过 → 100%；待处理/失败 → 0%；运行中 → 保持实时进度（由进度信号驱动）
            # 批量/流水线/组执行中：进度条完全由 step_progress_signal 驱动，
            # 这里不重置，避免"任务完成 → DONE → setValue(100)"覆盖任务级累计进度
            if self._batch_executor is not None:
                pass
            elif status == STEP_RUNNING:
                pass
            elif status in (STEP_DONE, STEP_SKIPPED):
                panel.progress.setRange(0, 100)
                panel.progress.setValue(100)
            else:
                panel.progress.setRange(0, 100)
                panel.progress.setValue(0)

            # 按钮可用性
            running = (status == STEP_RUNNING)
            skipped = (status == STEP_SKIPPED)
            ready = task.is_step_ready(step)
            # skipped 状态禁用开始按钮（已跳过，无需运行）
            # 但允许通过"重跑"来取消跳过
            panel.start_btn.setEnabled(not running and ready and not skipped)
            panel.stop_btn.setEnabled(running)

    # ========== 任务管理 ==========
    def _on_task_remove(self, task_id: str):
        reply = QMessageBox.question(
            self, "确认", "确定移除此任务？（不会删除已生成的文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.task_queue.remove_task(task_id)

    def _on_task_rerun(self, task_id: str, step: int):
        """重跑某一步（重置状态后启动）。"""
        task = self.task_queue.get_task(task_id)
        if not task:
            return
        if self._workers.get(step):
            QMessageBox.warning(self, "提示", f"步骤 {step} 正在运行中。")
            return
        # 切换到该任务
        self.task_queue.set_current(task_id)
        # 重置该步状态（取消 skipped/done/failed）
        task.set_step_status(step, STEP_PENDING)
        task.set_step_error(step, "")
        task.set_step_output(step, "")
        # 如果重跑的是前置步骤，后续步骤也需要重置
        if step == 1:
            task.set_step_status(2, STEP_PENDING)
            task.set_step_output(2, "")
            task.set_step_status(3, STEP_PENDING)
            task.set_step_output(3, "")
        elif step == 2:
            task.set_step_status(3, STEP_PENDING)
            task.set_step_output(3, "")
        self.task_queue.update_task(task)
        self._refresh_step_panels(task)
        # 启动
        self._on_start_step(step)

    def _on_group_remove(self, group_id: str):
        group = self.task_queue.get_group(group_id)
        if not group:
            return
        reply = QMessageBox.question(
            self, "移除文件夹组",
            f"确定移除文件夹组「{group.group_name}」及其 {len(group.tasks)} 个任务？\n"
            "（不会删除已生成的文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.task_queue.remove_group(group_id)
            if self._current_group and self._current_group.group_id == group_id:
                self._current_group = None
            self._append_log(
                f"[文件夹] 已移除组: {group.group_name} ({len(group.tasks)} 个任务)"
            )

    def _on_group_rerun(self, group_id: str, step: int):
        """重跑组内所有任务的某一步（重置状态后逐个执行）。"""
        group = self.task_queue.get_group(group_id)
        if not group:
            return
        if self._batch_executor:
            QMessageBox.warning(self, "提示", "已有批量执行正在进行中。")
            return
        self._current_group = group
        for task in group.tasks:
            task.set_step_status(step, STEP_PENDING)
            task.set_step_error(step, "")
            task.set_step_output(step, "")
            # 重跑前置步骤时，后续步骤一并重置
            if step == 1:
                task.set_step_status(2, STEP_PENDING)
                task.set_step_output(2, "")
                task.set_step_status(3, STEP_PENDING)
                task.set_step_output(3, "")
            elif step == 2:
                task.set_step_status(3, STEP_PENDING)
                task.set_step_output(3, "")
            self.task_queue.update_task(task)
        self._start_group_step(group, step)
