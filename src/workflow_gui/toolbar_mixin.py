# -*- coding: utf-8 -*-
"""主窗口 Mixin：工具栏动作、文件夹扫描与文件拖拽分发。"""
import os

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from ..task_manager import TaskQueue, STEP_DONE, STEP_RUNNING
from ..folder_import import build_folder_import_plan, find_matching_subtitle
from .common import VIDEO_EXTS, SUBTITLE_EXTS, MIX_AUDIO_EXTS


class ToolbarMixin:
    """工具栏动作与任务导入相关方法。"""

    # ========== 工具栏动作 ==========
    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频/音频文件",
            "", "媒体文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.webm *.ts *.wav *.mp3 *.flac *.m4a);;所有文件 (*)"
        )
        if files:
            self.on_files_dropped(files)

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹（自动识别音频和字幕）")
        if folder:
            self._scan_folder_for_tasks(folder)

    def _on_add_subtitle(self):
        """为当前任务添加字幕文件。"""
        task = self.task_queue.current
        if not task:
            QMessageBox.warning(self, "提示", "请先选择一个任务。")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择字幕文件",
            "", "字幕文件 (*.lrc *.vtt *.srt);;所有文件 (*)"
        )
        if path:
            self._apply_subtitle_to_task(task.task_id, path)

    def _on_add_mix_folder(self):
        """为当前任务添加配音目录。"""
        task = self.task_queue.current
        if not task:
            QMessageBox.warning(self, "提示", "请先选择一个任务。")
            return
        # 优先选目录，也支持选单个 wav 文件
        path = QFileDialog.getExistingDirectory(self, "选择配音目录（含 .wav 文件）")
        if path:
            self._apply_mix_folder_to_task(task.task_id, path)

    def _apply_subtitle_to_task(self, task_id: str, subtitle_path: str):
        """将字幕文件应用到任务（跳过步骤1）。"""
        if self._batch_executor or not self.task_queue.set_task_custom_subtitle(task_id, subtitle_path):
            self._append_log("[字幕] 任务正在运行或不存在，未更换字幕")
            return
        self._append_log(f"[字幕] 已设置自定义字幕: {os.path.basename(subtitle_path)} (跳过步骤1)")

    def _apply_mix_folder_to_task(self, task_id: str, mix_folder: str):
        """将配音目录应用到任务（跳过步骤2）。"""
        if self._batch_executor or not self.task_queue.set_task_custom_mix_folder(task_id, mix_folder):
            self._append_log("[配音] 任务正在运行或不存在，未更换配音目录")
            return
        self._append_log(f"[配音] 已设置自定义配音目录: {os.path.basename(mix_folder)} (跳过步骤2)")

    def _scan_folder_for_tasks(self, folder: str):
        """扫描文件夹，创建一个文件夹任务组，组内每个音频文件一个子任务。

        递归扫描文件夹中的媒体文件，自动匹配同名字幕创建子任务，
        文件夹以组节点形式保留在任务列表中，可展开查看音频树。
        """
        if not os.path.isdir(folder):
            self._append_log(f"[文件夹] 路径不存在: {folder}")
            return

        folder_name = os.path.basename(folder) or folder
        candidates = build_folder_import_plan(
            folder, self.config.mixer_cfg,
            (task.source_path for task in self.task_queue.tasks),
            self.config.workspace_dir,
        )
        if not candidates:
            self._append_log(f"[文件夹] 未找到支持的媒体文件: {folder_name}")
            return

        selected = []
        seen_outputs = set()
        for candidate in candidates:
            if not candidate.default_selected or not os.path.isfile(candidate.source_path):
                continue
            output = os.path.normcase(os.path.abspath(candidate.output_path))
            if output in seen_outputs:
                self._append_log(
                    f"[文件夹] 跳过 {candidate.relative_path}: 与其他任务的混音输出路径相同"
                )
                continue
            seen_outputs.add(output)
            selected.append(candidate)
        if not selected:
            self._append_log(f"[文件夹] 没有可导入的源媒体文件: {folder_name}")
            return

        # 创建文件夹任务组
        group = self.task_queue.create_group(folder)
        self._append_log(
            f"[文件夹] 导入: {folder_name} (发现 {len(candidates)} 个媒体文件，自动加入 {len(selected)} 个)"
        )

        for candidate in selected:
            task = self.task_queue.add_task(candidate.source_path,
                                            subtitle_path=candidate.subtitle_path,
                                            group_id=group.group_id)
            self._append_log(f"[任务] 添加: {task.source_name}")
            if candidate.subtitle_path:
                self._append_log(f"[任务]   ↳ 自动识别字幕: {os.path.basename(candidate.subtitle_path)}")

        self._append_log(
            f"[文件夹] 完成: 文件夹组「{group.group_name}」共 {len(group.tasks)} 个任务"
        )
        self._show_toast(f"已导入文件夹「{group.group_name}」：{len(group.tasks)} 个任务")

    def _find_matching_subtitle(self, media_path: str) -> str:
        """查找与媒体文件同目录、同名的字幕文件。

        查找顺序：
        1. 同名 + .lrc/.vtt/.srt（精确匹配）
        2. 同名 + 任意后缀 + .lrc/.vtt/.srt（如 "视频A.zh-CN.lrc"）
        找到返回路径，否则返回空字符串。
        """
        return find_matching_subtitle(media_path)

    def _can_prompt(self) -> bool:
        """最小测试宿主没有主按钮，不弹模态框；真实窗口才询问。"""
        return getattr(self, "run_task_btn", None) is not None

    def _ask_subtitle_mismatch(self, task, subtitle_path: str) -> bool:
        """字幕文件名与任务名不一致时，默认「不应用」。"""
        if not self._can_prompt():
            return True
        reply = QMessageBox.question(
            self, "字幕文件名不一致",
            f"字幕「{os.path.basename(subtitle_path)}」与当前任务"
            f"「{task.source_name}」的文件名不一致。\n\n仍要应用到当前任务吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _ask_wav_action(self, task, mix_dir: str) -> str:
        """一个 .wav 拖入可能是配音目录，也可能是新素材：让用户选一次。"""
        if not self._can_prompt():
            return ("mix" if task.step2_status not in (STEP_DONE, STEP_RUNNING)
                    else "cancel")
        box = QMessageBox(self)
        box.setWindowTitle("拖入的音频怎么处理")
        box.setText(
            f"检测到音频「{os.path.basename(mix_dir)}」目录下的 wav 文件。\n"
            f"要作为当前任务「{task.source_name}」的配音目录，"
            "还是新建任务？")
        box.setInformativeText(
            "配音目录：跳过步骤 2，直接用这些 wav 合成；\n"
            "新建任务：把 wav 当作新的源音频。")
        mix_btn = None
        if task.step2_status not in (STEP_DONE, STEP_RUNNING):
            mix_btn = box.addButton(
                f"作为配音目录添加到「{task.source_name}」",
                QMessageBox.ButtonRole.AcceptRole)
        new_btn = box.addButton("新建任务", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if mix_btn is not None and clicked is mix_btn:
            return "mix"
        if clicked is new_btn:
            return "new"
        return "cancel"

    def _create_tasks_from_paths(self, paths, group_id: str):
        for path in paths:
            sub_path = self._find_matching_subtitle(path)
            task = self.task_queue.add_task(path, subtitle_path=sub_path,
                                            group_id=group_id)
            self._append_log(f"[任务] 添加: {task.source_name}")
            if group_id:
                self._append_log(
                    f"[任务] 已加入文件夹组: {self._current_group.group_name}")
            if sub_path:
                self._append_log(
                    f"[任务] 自动识别字幕: {os.path.basename(sub_path)} (跳过步骤1)")

    def on_files_dropped(self, files):
        """拖拽或选择文件后调用，根据文件类型分发。"""
        video_files = []
        subtitle_files = []
        wav_files = []

        for path in files:
            if not os.path.exists(path):
                continue
            if os.path.isdir(path):
                # 目录：扫描媒体文件自动创建任务
                self._scan_folder_for_tasks(path)
            else:
                ext = os.path.splitext(path)[1].lower()
                if ext in SUBTITLE_EXTS:
                    subtitle_files.append(path)
                elif ext in MIX_AUDIO_EXTS:
                    wav_files.append(path)
                elif ext in VIDEO_EXTS:
                    video_files.append(path)
                else:
                    self._append_log(f"[拖拽] 跳过不支持的文件: {path}")

        # 先把「落到哪里」说清楚，避免同一动作三种结果却没有任何提示
        dest = (f"文件夹组「{self._current_group.group_name}」"
                if self._current_group else "新建任务")
        self._append_log(f"[拖拽] 落点：{dest}；{len(files)} 个文件")
        if files:
            self._show_toast(f"拖入 {len(files)} 个文件 → {dest}")

        # 视频/音频文件 → 创建新任务（自动查找同名字幕文件）
        # 若当前选中文件夹组，则新任务加入该组
        group_id = self._current_group.group_id if self._current_group else ""
        if video_files:
            self._create_tasks_from_paths(video_files, group_id)

        # .wav 文件：有当前任务时询问「配音目录 / 新建任务」，无当前任务则新建
        if wav_files:
            task = self.task_queue.current
            if task:
                mix_dir = os.path.dirname(wav_files[0])
                action = self._ask_wav_action(task, mix_dir)
                if action == "mix":
                    self._apply_mix_folder_to_task(task.task_id, mix_dir)
                    self._show_toast(f"已添加配音目录：{os.path.basename(mix_dir)}")
                elif action == "new":
                    self._create_tasks_from_paths(wav_files, group_id)
                    self._show_toast(f"已新建 {len(wav_files)} 个音频任务")
                else:
                    self._append_log("[拖拽] 已取消处理 .wav 文件")
            else:
                self._create_tasks_from_paths(wav_files, group_id)
                self._show_toast(f"已新建 {len(wav_files)} 个音频任务")

        # 字幕文件：按文件名匹配任务 source_name；无匹配时回退到当前任务，
        # 但文件名不一致必须先确认（默认不应用），避免一拖就覆盖错任务。
        if subtitle_files:
            applied_any = 0
            declined = False
            for sub_path in subtitle_files:
                sub_stem = os.path.splitext(os.path.basename(sub_path))[0]
                matched_task = None
                for t in self.task_queue.tasks:
                    if t.source_name == sub_stem:
                        matched_task = t
                        break
                if matched_task:
                    self._apply_subtitle_to_task(matched_task.task_id, sub_path)
                    self._append_log(
                        f"[字幕] 按文件名匹配: {os.path.basename(sub_path)} → 任务 {matched_task.source_name}"
                    )
                    applied_any += 1
                else:
                    task = self.task_queue.current
                    if task:
                        if (task.source_name != sub_stem
                                and not self._ask_subtitle_mismatch(task, sub_path)):
                            self._append_log(
                                f"[字幕] 已取消应用: {os.path.basename(sub_path)}"
                                f"（与当前任务 {task.source_name} 文件名不一致）")
                            declined = True
                            continue
                        self._apply_subtitle_to_task(task.task_id, sub_path)
                        applied_any += 1
                    else:
                        self._append_log(
                            f"[字幕] 无任务可应用: {os.path.basename(sub_path)}"
                        )
            if applied_any:
                self._show_toast(f"已应用 {applied_any} 个字幕文件")
            elif not declined:
                QMessageBox.warning(
                    self, "提示",
                    "字幕文件需要添加到已有任务。请先添加视频文件创建任务。"
                )

        # 配音目录：已由 _scan_folder_for_tasks 统一处理

        # 配音目录：已由 _scan_folder_for_tasks 统一处理

    def _on_open_workspace(self):
        ws = self.config.workspace_dir
        if ws and os.path.exists(ws):
            os.startfile(ws)
        else:
            QMessageBox.warning(self, "提示", "工作区目录不存在。")
