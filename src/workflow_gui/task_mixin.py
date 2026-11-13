# -*- coding: utf-8 -*-
"""主窗口 Mixin：任务/文件夹组选择、面板刷新与任务管理。"""
import os

from PyQt6.QtWidgets import QMessageBox

from ..task_manager import (
    TaskInfo, TaskGroup,
    STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, STEP_SKIPPED
)
from ..ui_theme import repolish
from ..widgets.common import STATUS_TEXT
from ..widgets.metadata_dialog import MetadataDialog


class TaskMixin:
    """任务选择与任务管理相关方法。"""

    # ========== 任务选择 ==========
    def _on_task_selected(self, task_id: str):
        self._current_group = None
        self._selected_folder = None
        self.task_queue.set_current(task_id)

    def _on_group_selected(self, group_id: str):
        """选中文件夹组节点：右侧显示组汇总，步骤执行作用于组内全部任务。"""
        group = self.task_queue.get_group(group_id)
        if not group:
            return
        self._current_group = group
        self._selected_folder = None
        self.task_queue.set_current(None)

    def _folder_group(self, group_id: str, relative_folder: str):
        root = self.task_queue.get_group(group_id)
        if not root:
            return None
        tasks = self.task_queue.tasks_in_group_folder(group_id, relative_folder)
        if not tasks:
            return None
        return TaskGroup(
            group_id, relative_folder,
            os.path.join(root.folder_path, relative_folder), tasks,
        )

    def _on_folder_selected(self, group_id: str, relative_folder: str):
        folder = self._folder_group(group_id, relative_folder)
        if folder:
            self._current_group = folder
            self._selected_folder = (group_id, relative_folder)
            self.task_queue.set_current(None)

    def _on_task_added(self, _task):
        self._update_queue_count()

    def _on_task_removed(self, task_id: str):
        # 列表已通过 task_removed 移除，计数必须同步，否则出现
        # 「左侧空空、标题还写 36 项」的错觉。
        self._update_queue_count()
        # 子目录选择优先：folder 是 group 的派生视图，不能先用根组覆盖它
        if self._selected_folder:
            group_id, relative_folder = self._selected_folder
            folder = self._folder_group(group_id, relative_folder)
            if folder:
                self._current_group = folder
                self._show_group_summary(folder)
            else:
                self._selected_folder = None
                self.task_list.select_group_item(group_id)
            return
        if self._current_group is not None:
            group = self.task_queue.get_group(self._current_group.group_id)
            if group is not None:
                self._current_group = group
                self._show_group_summary(group)
            else:
                # 组已不存在：清掉作用域，交给 _on_group_removed 收尾
                self._current_group = None

    def _on_group_removed(self, group_id: str):
        """组被整体移除后必须清空页头作用域，否则会留下一个幽灵组。"""
        if self._current_group and self._current_group.group_id == group_id:
            self._current_group = None
            self._selected_folder = None
        self._update_queue_count()
        self._on_current_changed(self.task_queue.current)
        self._refresh_run_buttons()

    def _show_group_summary(self, group: TaskGroup):
        """在右侧面板显示文件夹组 / 子目录的汇总状态。"""
        done = group.done_count()
        total = len(group.tasks)
        failed = sum(1 for t in group.tasks
                     if any(t.step_status(s) == STEP_FAILED for s in (1, 2, 3)))
        self._set_empty_state(False)
        self.current_task_label.setText(group.group_name or "文件夹")
        self.current_task_label.setToolTip(group.folder_path)
        scope_kind = "子目录" if self._selected_folder else "文件夹组"
        scope_tag = getattr(self, "scope_label", None)
        if scope_tag is not None:
            scope_tag.setText(f"{scope_kind} · {total} 个任务")
        subtitle = getattr(self, "current_task_subtitle", None)
        if subtitle is not None:
            prefix = f"{failed} 个失败 · " if failed else ""
            subtitle.setText(prefix + group.folder_path)
            subtitle.setToolTip(group.folder_path)
        busy = self._batch_executor is not None or any(self._workers.values())
        for step in (1, 2, 3):
            panel = self.step_panels[step]
            status = self._group_step_status(group, step)
            remaining = [t for t in group.tasks
                         if t.step_status(step) not in (STEP_DONE, STEP_SKIPPED)]
            reason = ""
            if status in (STEP_DONE, STEP_SKIPPED) or not remaining:
                reason = "组内该步骤均已完成"
            elif busy:
                reason = "有任务正在执行，请稍候"
            panel.apply_state(status, ready=bool(remaining), busy=busy,
                              reason=reason)
            completed = sum(t.step_status(step) in (STEP_DONE, STEP_SKIPPED)
                            for t in group.tasks)
            panel.set_progress(int(completed * 100 / max(1, total)))
        self._update_header(group)
        self._refresh_entry_buttons()
        self._refresh_run_buttons()

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
            if (self._current_group
                    and self.task_queue.get_group(self._current_group.group_id)):
                self._show_group_summary(self._current_group)
                return
            self._current_group = None
            self._selected_folder = None
            has_tasks = bool(self.task_queue.tasks)
            self._set_empty_state(True)
            if hasattr(self, "empty_title"):
                self.empty_title.setText(
                    "选择一个任务" if has_tasks else "还没有任务")
                self.empty_desc.setText(
                    "左侧是任务队列；点一个任务或文件夹，这里显示它的三步进度。"
                    if has_tasks else
                    "添加素材后，这里按「字幕 → 配音 → 混音」三步推进；"
                    "顶部「执行本任务」会一次跑完剩余步骤。")
            self.current_task_label.setText(
                "未选择任务" if not has_tasks else "选择左侧任务")
            scope_tag = getattr(self, "scope_label", None)
            if scope_tag is not None:
                scope_tag.setText("" if not has_tasks else "未选择")
            self.current_task_label.setToolTip("")
            subtitle = getattr(self, "current_task_subtitle", None)
            if subtitle is not None:
                subtitle.setText("选择左侧任务，或添加视频/音频文件开始"
                                 if not has_tasks else "点左侧任务或文件夹查看详情")
                subtitle.setToolTip("")
            for panel in self.step_panels.values():
                panel.reset()
            self._update_header(None)
            self._refresh_entry_buttons()
            self._refresh_run_buttons()
            return
        self._current_group = None
        self._selected_folder = None
        self._set_empty_state(False)
        self.current_task_label.setText(task.source_name)
        self.current_task_label.setToolTip(
            f"创建于 {task.created_at}\n{task.source_path}")
        scope_tag = getattr(self, "scope_label", None)
        if scope_tag is not None:
            scope_tag.setText("单个任务")
        subtitle = getattr(self, "current_task_subtitle", None)
        if subtitle is not None:
            subtitle.setText(task.source_path)
            subtitle.setToolTip(task.source_path)
        self._refresh_step_panels(task)

    def _set_empty_state(self, empty: bool):
        """空队列只显示引导卡；有任务时显示三张步骤卡。"""
        card = getattr(self, "empty_card", None)
        if card is not None:
            card.setVisible(empty)
        for panel in getattr(self, "step_panels", {}).values():
            panel.setVisible(not empty)
        for dot in getattr(self, "step_dot_labels", {}).values():
            dot.setVisible(not empty)
        overall = getattr(self, "overall_label", None)
        if overall is not None:
            overall.setVisible(not empty)

    def _update_header(self, item=None):
        """同步第二行的三步状态点、总进度与主按钮文案。"""
        dots = getattr(self, "step_dot_labels", {})
        overall = getattr(self, "overall_label", None)
        if not dots:
            return
        if item is None:
            for step, dot in dots.items():
                dot.setProperty("status", STEP_PENDING)
                dot.setToolTip(f"{step}：等待中")
                repolish(dot)
            if overall is not None:
                overall.setText("")
            return
        if isinstance(item, TaskGroup):
            statuses = {step: self._group_step_status(item, step)
                        for step in (1, 2, 3)}
            progress = int(item.progress() * 100)
        else:
            statuses = {step: item.step_status(step) for step in (1, 2, 3)}
            progress = int(item.overall_progress() * 100)
        names = {1: "字幕", 2: "配音", 3: "混音"}
        for step, dot in dots.items():
            status = statuses[step]
            dot.setProperty("status", status)
            dot.setText(f"{'●' if status != STEP_PENDING else '○'} {names[step]}")
            dot.setToolTip(f"{step} · {names[step]}：{STATUS_TEXT.get(status, status)}")
            repolish(dot)
        if overall is not None:
            overall.setText(f"{progress}%")

    def _refresh_run_buttons(self):
        """唯一主按钮的状态：无任务禁用，运行中变「停止」。"""
        run_btn = getattr(self, "run_task_btn", None)
        batch_btn = getattr(self, "batch_btn", None)
        if run_btn is None:
            return
        busy = self._batch_executor is not None or any(self._workers.values())
        has_scope = bool(self.task_queue.current or self._current_group)
        if busy:
            run_btn.setText("停止")
            run_btn.setEnabled(True)
            run_btn.setToolTip("停止当前执行；已完成的步骤会保留")
        else:
            if self._selected_folder:
                count = len(self._current_group.tasks) if self._current_group else 0
                run_btn.setText(f"执行目录（{count}）")
            elif self._current_group is not None:
                count = len(self._current_group.tasks)
                run_btn.setText(f"执行组（{count}）")
            else:
                current = self.task_queue.current
                pending = [s for s in (1, 2, 3)
                           if current is not None
                           and current.step_status(s) not in (STEP_DONE, STEP_SKIPPED)]
                if current is not None and not pending:
                    run_btn.setText("查看成品")
                    output = current.step3_output
                    exists = bool(output) and os.path.exists(output)
                    run_btn.setEnabled(exists)
                    run_btn.setToolTip("打开混音成品" if exists
                                       else "成品文件已不存在，可在步骤卡重新生成")
                else:
                    run_btn.setText("执行本任务")
                    run_btn.setEnabled(has_scope)
                    run_btn.setToolTip("按 1→2→3 依次执行尚未完成的步骤")
        if batch_btn is not None:
            batch_btn.setEnabled(not busy and bool(self.task_queue.tasks))
            batch_btn.setToolTip("选择范围与步骤，批量执行多个任务" if not busy
                                 else "当前执行结束后可用")
        count_label = getattr(self, "queue_count_label", None)
        if count_label is not None:
            self._update_queue_count()

    def _on_task_updated(self, task: TaskInfo):
        self.task_list.update_task_item(task)
        if self.task_queue.current and self.task_queue.current.task_id == task.task_id:
            self._refresh_step_panels(task)
            self._refresh_entry_buttons()
        elif (self._current_group
              and any(t.task_id == task.task_id for t in self._current_group.tasks)):
            # 批量执行时也刷新状态，汇总视图保留信号驱动的累计进度。
            self._show_group_summary(self._current_group)

    @staticmethod
    def _set_button_enabled(button, enabled, reason=""):
        """按钮禁用/启用并同步 tooltip（P2-4）。

        禁用态必须说明原因（灰按钮不再装哑巴）；启用后还原按钮自带的
        功能提示（如「为当前任务添加字幕文件」）。
        """
        default_tip = button.property("tipDefault")
        if default_tip is None:
            default_tip = button.toolTip()
            button.setProperty("tipDefault", default_tip)
        button.setEnabled(enabled)
        button.setToolTip(default_tip if enabled else reason)

    def _refresh_step_panels(self, task: TaskInfo):
        """根据任务状态刷新三个步骤面板（状态驱动形态）。"""
        busy = self._batch_executor is not None or any(self._workers.values())
        for step in (1, 2, 3):
            panel = self.step_panels[step]
            status = task.step_status(step)
            output = (task.step1_output if step == 1 else
                      task.step2_output if step == 2 else task.step3_output)
            prefix = {1: "字幕", 2: "配音", 3: "成品"}[step]
            ready = task.is_step_ready(step)
            reason = ""
            if status == STEP_PENDING and not ready:
                reason = f"需先完成步骤 {step - 1}"
            panel.apply_state(status, ready=ready, busy=busy, reason=reason,
                              output_path=output or "", prefix=prefix)

            # 进度条按步骤状态重置，避免残留上一次运行的值。
            # 批量/流水线执行中：进度由 step_progress_signal 驱动，这里不重置。
            if self._batch_executor is not None:
                pass
            elif status == STEP_RUNNING:
                progress, detail = getattr(self, "_step_runtime", {}).get(
                    (task.task_id, step), (0, ""))
                panel.set_progress(progress)
                panel.set_detail(detail)
            elif status in (STEP_DONE, STEP_SKIPPED):
                panel.set_detail("")
                panel.set_progress(100)
            elif status == STEP_FAILED:
                panel.set_error(getattr(task, f"step{step}_error", ""))
                panel.set_progress(0)
            else:
                panel.set_detail("")
                panel.set_progress(0)
        self._update_header(task)
        # 条目级按钮（添加字幕/配音）随步骤运行状态联动
        self._refresh_entry_buttons()
        self._refresh_run_buttons()

    # ---------- 工具栏条目级按钮与底部栏同步 ----------
    def _refresh_entry_buttons(self):
        """按当前选择刷新工具栏「添加字幕/添加配音」的可用性。

        仅选中具体任务且字幕/配音步骤未运行时可用；文件夹组或无选中时禁用。
        """
        buttons = [b for b in (getattr(self, "add_subtitle_btn", None),
                               getattr(self, "add_mix_btn", None))
                   if b is not None]
        if not buttons:
            return
        task = self.task_queue.current if self.task_queue else None
        enabled = bool(task is not None
                       and self._batch_executor is None
                       and all(task.step_status(s) != STEP_RUNNING for s in (1, 2, 3)))
        if enabled:
            reason = ""
        elif task is None:
            reason = ("请选中具体任务，而非文件夹组" if self._current_group is not None
                      else "请先在左侧选择任务")
        elif self._batch_executor is not None:
            reason = "批量执行中，请等待完成"
        else:
            reason = "有步骤正在运行，请稍候"
        for button in buttons:
            self._set_button_enabled(button, enabled, reason)


    # ========== 任务管理 ==========
    def _on_edit_metadata(self, task_id: str):
        task = self.task_queue.get_task(task_id)
        if not task or not os.path.isfile(task.step3_output):
            QMessageBox.warning(self, "成品不存在", "找不到该任务的混音成品。")
            return
        if task.step3_status == STEP_RUNNING:
            QMessageBox.warning(self, "正在混音", "请在混音结束后编辑成品信息。")
            return
        dialog = MetadataDialog(task, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._append_log(f"[成品信息] 已保存: {os.path.basename(task.step3_output)}")

    def _on_task_remove(self, task_id: str):
        task = self.task_queue.get_task(task_id)
        if not task:
            return
        if task.step_status(3) == STEP_RUNNING or any(self._workers.values()):
            QMessageBox.warning(self, "任务正在运行", "请在当前步骤结束后再移除。")
            return
        box = QMessageBox(self)
        box.setWindowTitle("移除任务")
        box.setText(f"确定移除「{task.source_name}」？")
        box.setInformativeText(
            "默认只从任务列表移除，保留源文件与已生成成品。\n"
            "勾选下方选项会额外删除该任务的工作区目录（中间文件，不可恢复）。")
        from PyQt6.QtWidgets import QCheckBox
        cleanup = QCheckBox("同时删除该任务的工作区目录")
        cleanup.setToolTip(task.task_dir)
        box.setCheckBox(cleanup)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        if cleanup.isChecked():
            import shutil
            root = os.path.abspath(task.workspace_root)
            target = os.path.abspath(task.task_dir)
            try:
                inside = os.path.commonpath([root, target]) == root
            except ValueError:
                inside = False
            if inside and os.path.isdir(target):
                try:
                    shutil.rmtree(target)
                    self._append_log(f"[清理] 已删除任务目录: {task.task_id}")
                except OSError as exc:
                    self._append_log(f"[清理] 删除任务目录失败: {exc}")
        self.task_queue.remove_task(task_id)

    def _on_task_rerun(self, task_id: str, step: int):
        """重跑某一步（重置状态后启动）。"""
        task = self.task_queue.get_task(task_id)
        if not task:
            return
        if self._batch_executor or any(self._workers.values()):
            QMessageBox.warning(self, "提示", "已有任务正在运行，请先停止。")
            return
        if step == 1 and not self._check_whisper_available():
            return
        # 切换到该任务
        self.task_queue.set_current(task_id)
        task.reset_from(step)
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
            count = len(group.tasks)
            self.task_queue.remove_group(group_id)
            if self._current_group and self._current_group.group_id == group_id:
                self._current_group = None
                self._selected_folder = None
            self._append_log(
                f"[文件夹] 已移除组: {group.group_name} ({count} 个任务)"
            )

    def _on_folder_remove(self, group_id: str, relative_folder: str):
        tasks = self.task_queue.tasks_in_group_folder(group_id, relative_folder)
        if not tasks:
            return
        if self._batch_executor or any(self._workers.values()):
            QMessageBox.warning(self, "任务正在运行", "请在当前处理结束后移除目录任务。")
            return
        reply = QMessageBox.question(
            self, "移除目录任务",
            f"确定移除「{relative_folder}」及其子目录中的 {len(tasks)} 个任务？\n"
            "只从任务列表移除，不删除源音频或已生成的文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = self.task_queue.remove_group_folder(group_id, relative_folder)
        self.task_list.select_group_item(group_id)
        self._append_log(f"[文件夹] 已移除目录任务: {relative_folder} ({removed} 个)")

    def _on_group_rerun(self, group_id: str, step: int):
        """重跑组内所有任务的某一步（重置状态后逐个执行）。"""
        group = self.task_queue.get_group(group_id)
        if not group:
            return
        self._selected_folder = None
        self._rerun_group_tasks(group, step)

    def _on_folder_rerun(self, group_id: str, relative_folder: str, step: int):
        group = self._folder_group(group_id, relative_folder)
        if group:
            self.task_list.select_folder_item(group_id, relative_folder)
            self._selected_folder = (group_id, relative_folder)
            self._rerun_group_tasks(group, step)

    def _rerun_group_tasks(self, group: TaskGroup, step: int):
        if self._batch_executor or any(self._workers.values()):
            QMessageBox.warning(self, "提示", "已有批量执行正在进行中。")
            return
        if step == 1 and not self._check_whisper_available():
            return
        self._current_group = group
        for task in group.tasks:
            task.reset_from(step)
            self.task_queue.update_task(task)
        self._start_group_step(group, step)

    # ---------- 任务级一键执行 ----------
    def _scope_tasks(self):
        if self._current_group is not None:
            return list(self._current_group.tasks)
        current = self.task_queue.current
        return [current] if current else []

    def _on_run_task(self):
        """唯一主按钮：按 1→2→3 执行当前作用域内尚未完成的步骤。"""
        if self._batch_executor is not None or any(self._workers.values()):
            self._on_stop_run()
            return
        tasks = self._scope_tasks()
        if not tasks:
            QMessageBox.information(self, "没有可执行的任务",
                                    "请先在左侧选择任务或添加素材。")
            return
        pending = [t for t in tasks
                   if any(t.step_status(s) not in (STEP_DONE, STEP_SKIPPED)
                          for s in (1, 2, 3))]
        if not pending:
            current = self.task_queue.current
            if current is not None and current.step3_output:
                self.step_panels[3]._open_output()
                return
            QMessageBox.information(self, "已全部完成",
                                    "当前作用域内三个步骤都已完成，无需执行。")
            return
        if len(pending) > 3:
            reply = QMessageBox.question(
                self, "确认执行",
                f"将对 {len(pending)} 个任务依次执行剩余步骤。\n"
                "已完成的步骤会自动跳过，执行中可以随时停止。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._start_scope_pipeline(pending)

    def _on_stop_run(self):
        """运行中主按钮变「停止」：批量走执行器停止，单步走 worker.stop。"""
        if self._batch_executor is not None:
            self._on_batch_stop()
            return
        stopped = False
        for step, worker in self._workers.items():
            if worker is not None:
                worker.stop()
                stopped = True
        if stopped:
            self._append_log("[执行] 正在停止…")

    def _on_rerun_step(self, step: int):
        """步骤卡里的「重新生成 / 改用自动流程」。"""
        if self._current_group is not None:
            self._rerun_group_tasks(self._current_group, step)
            return
        task = self.task_queue.current
        if task:
            self._on_task_rerun(task.task_id, step)
