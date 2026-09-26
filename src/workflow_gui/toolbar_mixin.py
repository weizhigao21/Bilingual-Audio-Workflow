# -*- coding: utf-8 -*-
"""主窗口 Mixin：工具栏动作、文件夹扫描与文件拖拽分发。"""
import os

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from ..task_manager import TaskQueue, STEP_DONE, STEP_RUNNING
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
        self.task_queue.set_task_custom_subtitle(task_id, subtitle_path)
        self._append_log(f"[字幕] 已设置自定义字幕: {os.path.basename(subtitle_path)} (跳过步骤1)")

    def _apply_mix_folder_to_task(self, task_id: str, mix_folder: str):
        """将配音目录应用到任务（跳过步骤2）。"""
        self.task_queue.set_task_custom_mix_folder(task_id, mix_folder)
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
        audio_files = TaskQueue.scan_folder_media(folder)

        if not audio_files:
            self._append_log(f"[文件夹] 未找到支持的媒体文件: {folder_name}")
            return

        # 创建文件夹任务组
        group = self.task_queue.create_group(folder)
        self._append_log(
            f"[文件夹] 导入: {folder_name} (发现 {len(audio_files)} 个媒体文件)"
        )

        for path in audio_files:
            sub_path = self._find_matching_subtitle(path)
            task = self.task_queue.add_task(path, subtitle_path=sub_path,
                                            group_id=group.group_id)
            self._append_log(f"[任务] 添加: {task.source_name}")
            if sub_path:
                self._append_log(f"[任务]   ↳ 自动识别字幕: {os.path.basename(sub_path)}")

        self._append_log(
            f"[文件夹] 完成: 文件夹组「{group.group_name}」共 {len(group.tasks)} 个任务"
        )

    def _find_matching_subtitle(self, media_path: str) -> str:
        """查找与媒体文件同目录、同名的字幕文件。

        查找顺序：
        1. 同名 + .lrc/.vtt/.srt（精确匹配）
        2. 同名 + 任意后缀 + .lrc/.vtt/.srt（如 "视频A.zh-CN.lrc"）
        找到返回路径，否则返回空字符串。
        """
        directory = os.path.dirname(media_path)
        stem = os.path.splitext(os.path.basename(media_path))[0]
        if not directory:
            directory = "."
        if not os.path.isdir(directory):
            return ""

        # 尝试的字幕扩展名（按优先级）
        sub_exts = [".lrc", ".srt", ".vtt"]

        try:
            files_in_dir = os.listdir(directory)
        except OSError:
            return ""

        # 1. 精确匹配：{stem}.{ext}
        for ext in sub_exts:
            target = f"{stem}{ext}"
            if target.lower() in (f.lower() for f in files_in_dir):
                # 找到实际文件名（保留大小写）
                for f in files_in_dir:
                    if f.lower() == target.lower():
                        return os.path.join(directory, f)

        # 2. 模糊匹配：{stem}.*.{ext}（如 "视频A.zh-CN.lrc"）
        for f in files_in_dir:
            f_lower = f.lower()
            if not f_lower.startswith(stem.lower() + "."):
                continue
            for ext in sub_exts:
                if f_lower.endswith(ext):
                    return os.path.join(directory, f)

        return ""

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

        # 视频/音频文件 → 创建新任务（自动查找同名字幕文件）
        # 若当前选中文件夹组，则新任务加入该组
        group_id = self._current_group.group_id if self._current_group else ""
        for path in video_files:
            # 自动查找同目录下的同名字幕文件
            sub_path = self._find_matching_subtitle(path)
            task = self.task_queue.add_task(path, subtitle_path=sub_path,
                                            group_id=group_id)
            self._append_log(f"[任务] 添加: {task.source_name}")
            if group_id:
                self._append_log(
                    f"[任务] 已加入文件夹组: {self._current_group.group_name}"
                )
            if sub_path:
                self._append_log(f"[任务] 自动识别字幕: {os.path.basename(sub_path)} (跳过步骤1)")

        # .wav 文件：有当前任务 → 添加为配音；无当前任务 → 创建新任务（视为源音频）
        if wav_files:
            task = self.task_queue.current
            if task and task.step2_status not in (STEP_DONE, STEP_RUNNING):
                # 用第一个 wav 文件所在目录作为配音目录
                mix_dir = os.path.dirname(wav_files[0])
                self._apply_mix_folder_to_task(task.task_id, mix_dir)
            elif not task:
                # 没有当前任务，视为源音频文件
                for path in wav_files:
                    sub_path = self._find_matching_subtitle(path)
                    task = self.task_queue.add_task(path, subtitle_path=sub_path,
                                                    group_id=group_id)
                    self._append_log(f"[任务] 添加: {task.source_name}")
                    if sub_path:
                        self._append_log(f"[任务] 自动识别字幕: {os.path.basename(sub_path)} (跳过步骤1)")
            else:
                self._append_log("[拖拽] 当前任务步骤2已完成，忽略 .wav 文件")

        # 字幕文件：按文件名匹配任务 source_name，无匹配才回退到当前任务
        if subtitle_files:
            applied_any = False
            for sub_path in subtitle_files:
                sub_stem = os.path.splitext(os.path.basename(sub_path))[0]
                # 在所有任务中找 source_name 与字幕文件名匹配的
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
                    applied_any = True
                else:
                    # 无匹配，回退到当前任务（带警告）
                    task = self.task_queue.current
                    if task:
                        # 检查当前任务是否已有字幕且不匹配
                        if task.source_name != sub_stem:
                            self._append_log(
                                f"[字幕] 警告: 字幕 {os.path.basename(sub_path)} 与当前任务 "
                                f"{task.source_name} 文件名不匹配，仍已应用（如需修正请重新指定）"
                            )
                        self._apply_subtitle_to_task(task.task_id, sub_path)
                        applied_any = True
                    else:
                        self._append_log(
                            f"[字幕] 无任务可应用: {os.path.basename(sub_path)}"
                        )
            if not applied_any:
                QMessageBox.warning(
                    self, "提示",
                    "字幕文件需要添加到已有任务。请先添加视频文件创建任务。"
                )

        # 配音目录：已由 _scan_folder_for_tasks 统一处理

    def _on_open_workspace(self):
        ws = self.config.workspace_dir
        if ws and os.path.exists(ws):
            os.startfile(ws)
        else:
            QMessageBox.warning(self, "提示", "工作区目录不存在。")
