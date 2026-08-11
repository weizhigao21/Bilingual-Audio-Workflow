# -*- coding: utf-8 -*-
"""工作流主窗口：左右分栏（任务队列 + 三步面板）。"""
import os
import time

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QFileDialog, QMessageBox, QTextEdit, QLabel, QSplitter, QGroupBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon

from .config import WorkflowConfig, SetupDialog
from .task_manager import (
    TaskQueue, TaskInfo, TaskGroup,
    STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, STEP_SKIPPED
)
from .widgets import StepPanel, TaskListWidget, TTSConfigPanel, MixerConfigPanel, WhisperConfigPanel


# 文件类型分类
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts",
              ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wav", ".wma"}
SUBTITLE_EXTS = {".lrc", ".vtt", ".srt"}
MIX_AUDIO_EXTS = {".wav"}


class WorkflowMainWindow(QMainWindow):
    """工作流主窗口。"""

    def __init__(self, config: WorkflowConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle("双语音声工作流 v2.0.1")
        self.setMinimumSize(1100, 720)

        # 任务队列
        self.task_queue = TaskQueue(config.workspace_dir)
        # 当前活动的 worker（按步骤号 1/2/3 索引）
        self._workers = {1: None, 2: None, 3: None}
        # TTS 总任务数（用于进度计算）
        self._tts_total = 0
        # 批量执行器
        self._batch_executor = None
        # 当前选中的文件夹组（选中组节点时非 None）
        self._current_group: TaskGroup = None

        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # 工具栏
        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ 添加视频/音频")
        add_btn.clicked.connect(self._on_add_files)
        toolbar.addWidget(add_btn)

        add_folder_btn = QPushButton("+ 添加文件夹")
        add_folder_btn.setToolTip("导入文件夹，自动识别音频和字幕文件并配对创建任务")
        add_folder_btn.clicked.connect(self._on_add_folder)
        toolbar.addWidget(add_folder_btn)

        add_sub_btn = QPushButton("+ 添加字幕")
        add_sub_btn.setToolTip("为当前任务添加字幕文件（跳过字幕提取）")
        add_sub_btn.clicked.connect(self._on_add_subtitle)
        toolbar.addWidget(add_sub_btn)

        add_mix_btn = QPushButton("+ 添加配音")
        add_mix_btn.setToolTip("为当前任务添加配音目录（跳过语音生成）")
        add_mix_btn.clicked.connect(self._on_add_mix_folder)
        toolbar.addWidget(add_mix_btn)

        open_ws_btn = QPushButton("打开工作区")
        open_ws_btn.clicked.connect(self._on_open_workspace)
        toolbar.addWidget(open_ws_btn)

        config_btn = QPushButton("配置")
        config_btn.clicked.connect(self._on_config)
        toolbar.addWidget(config_btn)

        clear_btn = QPushButton("全部清空")
        clear_btn.setToolTip("清空所有任务（可选择是否删除已生成的文件）")
        clear_btn.setStyleSheet("color: #c00;")
        clear_btn.clicked.connect(self._on_clear_all)
        toolbar.addWidget(clear_btn)

        self.batch_btn = QPushButton("批量执行")
        self.batch_btn.setToolTip("自动遍历所有任务，按选定步骤批量执行")
        self.batch_btn.setStyleSheet("font-weight: bold; color: #0066cc;")
        self.batch_btn.clicked.connect(self._on_batch_execute)
        toolbar.addWidget(self.batch_btn)

        self.batch_stop_btn = QPushButton("停止批量")
        self.batch_stop_btn.setToolTip("停止正在进行的批量执行")
        self.batch_stop_btn.setEnabled(False)
        self.batch_stop_btn.clicked.connect(self._on_batch_stop)
        toolbar.addWidget(self.batch_stop_btn)

        toolbar.addStretch()
        self.status_bar_label = QLabel("就绪")
        toolbar.addWidget(self.status_bar_label)
        main_layout.addLayout(toolbar)

        # 左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：任务列表
        left_group = QGroupBox("任务队列")
        left_layout = QVBoxLayout(left_group)
        self.task_list = TaskListWidget()
        self.task_list.set_task_queue(self.task_queue)
        left_layout.addWidget(self.task_list)
        hint = QLabel("提示：可拖拽视频/音频文件或文件夹到此处；文件夹将作为分组节点展示内部音频树")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        left_layout.addWidget(hint)
        splitter.addWidget(left_group)

        # 右侧：三步面板 + 日志
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.current_task_label = QLabel("未选择任务")
        self.current_task_label.setStyleSheet("font-weight: bold; padding: 4px;")
        right_layout.addWidget(self.current_task_label)

        # 三个步骤面板
        self.step_panels = {}
        step_titles = {1: "字幕提取", 2: "语音生成", 3: "音频混音"}
        for step in (1, 2, 3):
            panel = StepPanel(step, step_titles[step])
            panel.start_requested.connect(self._on_start_step)
            panel.stop_requested.connect(self._on_stop_step)
            right_layout.addWidget(panel)
            self.step_panels[step] = panel
            # 步骤2面板内加"TTS配置"按钮
            if step == 2:
                tts_config_btn = panel.add_extra_button("TTS 配置...")
                tts_config_btn.setToolTip("设置语音合成模式（Edge/API）、声音、语速等参数")
                tts_config_btn.clicked.connect(self._on_open_tts_config)
            # 步骤3面板内加"混音配置"按钮
            if step == 3:
                mixer_config_btn = panel.add_extra_button("混音配置...")
                mixer_config_btn.setToolTip("设置导出格式、音量、声道检测等参数")
                mixer_config_btn.clicked.connect(self._on_open_mixer_config)
            # 步骤1面板内加"字幕配置"按钮
            if step == 1:
                whisper_config_btn = panel.add_extra_button("字幕配置...")
                whisper_config_btn.setToolTip("设置设备、VAD、字幕格式、段落合并等参数")
                whisper_config_btn.clicked.connect(self._on_open_whisper_config)

        # 全局日志区
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        log_layout.addWidget(self.log_view)
        right_layout.addWidget(log_group, 1)

        splitter.addWidget(right_widget)
        splitter.setSizes([300, 800])
        main_layout.addWidget(splitter, 1)

    def _connect_signals(self):
        self.task_list.task_selected.connect(self._on_task_selected)
        self.task_list.task_remove_requested.connect(self._on_task_remove)
        self.task_list.task_rerun_requested.connect(self._on_task_rerun)
        self.task_list.group_selected.connect(self._on_group_selected)
        self.task_list.group_remove_requested.connect(self._on_group_remove)
        self.task_list.group_rerun_requested.connect(self._on_group_rerun)
        self.task_queue.task_added.connect(self.task_list.add_task_item)
        self.task_queue.task_removed.connect(self.task_list.remove_task_item)
        self.task_queue.task_updated.connect(self._on_task_updated)
        self.task_queue.current_changed.connect(self._on_current_changed)
        self.task_queue.group_added.connect(self.task_list.add_group_item)
        self.task_queue.group_removed.connect(self.task_list.remove_group_item)

    def _restore_task_list(self):
        """（已废弃）启动时任务列表为空，需手动添加文件。"""
        pass

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

    def _on_config(self):
        dialog = SetupDialog(self.config)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._append_log("[配置] 已更新")

    def _on_open_tts_config(self):
        """打开 TTS 配置对话框。配置变更自动保存。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("TTS 配置")
        dialog.setMinimumWidth(420)
        dlg_layout = QVBoxLayout(dialog)
        panel = TTSConfigPanel(self.config, dialog)
        panel.config_changed.connect(self._on_tts_config_changed)
        panel.log_message.connect(self._append_log)
        dlg_layout.addWidget(panel)
        # 关闭按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        dlg_layout.addLayout(btn_row)
        dialog.exec()

    def _on_tts_config_changed(self):
        """TTS 配置变化时记录日志。"""
        mode = self.config.tts_cfg.get("tts_mode", "edge")
        if mode == "edge":
            voice = self.config.tts_cfg.get("edge_voice", "")
            self._append_log(f"[TTS配置] Edge模式, 声音={voice}")
        else:
            idx = self.config.tts_cfg.get("current_api_index", 0)
            apis = self.config.tts_cfg.get("api_configs", [])
            api_name = apis[idx].get("name", "?") if 0 <= idx < len(apis) else "?"
            self._append_log(f"[TTS配置] API模式, 当前API={api_name}")

    def _on_open_mixer_config(self):
        """打开混音配置对话框。配置变更自动保存。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("混音配置")
        dialog.setMinimumWidth(400)
        dlg_layout = QVBoxLayout(dialog)
        panel = MixerConfigPanel(self.config, dialog)
        panel.config_changed.connect(self._on_mixer_config_changed)
        dlg_layout.addWidget(panel)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        dlg_layout.addLayout(btn_row)
        dialog.exec()

    def _on_mixer_config_changed(self):
        """混音配置变化时记录日志。"""
        cfg = self.config.mixer_cfg
        self._append_log(
            f"[混音配置] 格式={cfg.get('output_format', 'mp4')}, "
            f"音量={cfg.get('volume_db', 0.0)}dB, "
            f"模式={cfg.get('auto_volume', 'fixed')}"
        )

    def _on_open_whisper_config(self):
        """打开字幕提取配置对话框。配置变更自动保存。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("字幕提取配置")
        dialog.setMinimumWidth(420)
        dlg_layout = QVBoxLayout(dialog)
        panel = WhisperConfigPanel(self.config, dialog)
        panel.config_changed.connect(self._on_whisper_config_changed)
        dlg_layout.addWidget(panel)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        dlg_layout.addLayout(btn_row)
        dialog.exec()

    def _on_whisper_config_changed(self):
        """字幕配置变化时记录日志。"""
        cfg = self.config.whisper_cfg
        self._append_log(
            f"[字幕配置] 设备={cfg.get('device', 'auto')}, "
            f"精度={cfg.get('compute_type', 'auto')}, "
            f"格式={cfg.get('sub_formats', 'lrc')}, "
            f"VAD阈值={cfg.get('vad_threshold', 0.5)}"
        )

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
        from .steps.batch_executor import BatchExecutor
        self._tts_total = 0  # 批量/流水线模式下进度为任务级累计，重置片段总数
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
            self.task_queue.clear_all(delete_files=True)
            self._append_log(f"[清空] 已清空 {count} 个任务并删除文件")

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

    # ========== 步骤执行 ==========
    def _on_start_step(self, step: int):
        # 组模式：对组内所有任务逐个执行该步骤
        if self._current_group:
            self._start_group_step(self._current_group, step)
            return
        task = self.task_queue.current
        if not task:
            QMessageBox.warning(self, "提示", "请先选择一个任务。")
            return
        if not os.path.exists(task.source_path):
            QMessageBox.warning(
                self, "提示",
                f"源文件不存在，无法执行：\n{task.source_path}\n\n"
                f"可能已被移动或重命名（如添加\"双语-\"前缀），请重新导入任务。"
            )
            return
        if not task.is_step_ready(step):
            QMessageBox.warning(self, "提示", f"步骤 {step} 的前置步骤尚未完成。")
            return

        # 创建 worker（延迟导入步骤模块，避免启动时加载重库）
        from .steps.step1_whisper import WhisperWorker
        from .steps.step2_tts import TTSBridgeWorker
        from .steps.step3_mixer import MixerWorker

        if step == 1:
            worker = WhisperWorker(task, self.config)
        elif step == 2:
            worker = TTSBridgeWorker(task, self.config)
            self._tts_total = 0
            worker.total_signal.connect(self._on_tts_total)
        else:
            worker = MixerWorker(task, self.config)

        worker.log_signal.connect(self._append_log)
        worker.progress_signal.connect(
            lambda v, s=step: self._on_step_progress(s, v)
        )
        worker.finished_signal.connect(
            lambda ok, msg, s=step, t=task: self._on_step_finished(s, t, ok, msg)
        )

        self._workers[step] = worker
        task.set_step_status(step, STEP_RUNNING)
        task.set_step_error(step, "")
        self.step_panels[step].set_running(True)
        self.step_panels[step].set_status(STEP_RUNNING)
        # 启动前重置该步骤进度条（TTS 的 total 信号会随后调整上限）
        self.step_panels[step].progress.setRange(0, 100)
        self.step_panels[step].progress.setValue(0)
        self._refresh_step_panels(task)
        worker.start()

    def _on_stop_step(self, step: int):
        worker = self._workers.get(step)
        if worker:
            worker.stop()
            self._append_log(f"[步骤{step}] 正在停止...")

    def _start_group_step(self, group: TaskGroup, step: int):
        """对文件夹组内所有任务逐个执行指定步骤（复用批量执行器）。"""
        if self._batch_executor:
            QMessageBox.warning(self, "提示", "已有批量执行正在进行中，请先停止。")
            return
        if not group.tasks:
            QMessageBox.information(self, "提示", "该文件夹组没有任务。")
            return
        pending = [t for t in group.tasks
                   if t.step_status(step) not in (STEP_DONE, STEP_SKIPPED)]
        if not pending:
            QMessageBox.information(
                self, "提示", f"组内所有任务的步骤{step} 均已完成或已跳过。"
            )
            return
        not_ready = [t.source_name for t in pending if not t.is_step_ready(step)]
        if not_ready:
            QMessageBox.warning(
                self, "提示",
                f"以下任务步骤 {step} 前置未完成，无法执行：\n"
                + "\n".join(not_ready[:10])
            )
            return

        from .steps.batch_executor import BatchExecutor
        self._tts_total = 0  # 组执行时进度为任务级累计，重置片段总数
        # 重置三个步骤进度条，避免残留上次运行的值
        for p in self.step_panels.values():
            p.progress.setRange(0, 100)
            p.progress.setValue(0)
        self._batch_executor = BatchExecutor(
            list(group.tasks), [step], self.config, order="by_task", parent=self
        )
        self._connect_batch_signals()
        self.batch_btn.setEnabled(False)
        self.batch_stop_btn.setEnabled(True)
        for panel in self.step_panels.values():
            panel.start_btn.setEnabled(False)
        self._append_log(
            f"[文件夹] 开始逐个执行: 「{group.group_name}」步骤{step} "
            f"({len(group.tasks)} 个任务)"
        )
        self._batch_executor.start()

    def _on_tts_total(self, total: int):
        self._tts_total = total
        if total > 0:
            self.step_panels[2].progress.setRange(0, total)

    def _on_step_progress(self, step: int, value: int):
        panel = self.step_panels[step]
        if self._batch_executor is not None:
            # 批量/流水线/组执行：value 恒为 0-100 的任务级累计百分比，
            # 统一按 0-100 显示，避免与 TTS 片段总数混用导致被 clamp 成 100%
            panel.progress.setRange(0, 100)
            panel.progress.setValue(max(0, min(100, value)))
            return
        if step == 2:
            if self._tts_total > 0:
                panel.progress.setRange(0, self._tts_total)
                panel.progress.setValue(value)
            else:
                panel.progress.setRange(0, 100)
                panel.progress.setValue(value)
        else:
            panel.progress.setRange(0, 100)
            panel.progress.setValue(value)

    def _on_step_total(self, step: int, total: int):
        """步骤内总任务数（如 TTS 的总片段数），用于设置进度条上限。"""
        if step == 2 and total > 0:
            self._tts_total = total
            self.step_panels[2].progress.setRange(0, total)

    def _on_step_finished(self, step: int, task: TaskInfo, success: bool, msg: str):
        if success:
            task.set_step_status(step, STEP_DONE)
            task.set_step_output(step, msg)
            self._append_log(f"[步骤{step}] 完成: {msg}")
            # 文件夹导入且混音完成后，重命名源文件夹添加"双语-"前缀
            if step == 3 and task.from_folder and self.config.mixer_cfg.get("folder_prefix", True):
                self._rename_source_folder(task)
        else:
            task.set_step_status(step, STEP_FAILED)
            task.set_step_error(step, msg)
            self._append_log(f"[步骤{step}] 失败: {msg}")

        self._workers[step] = None
        self.task_queue.update_task(task)
        self._refresh_step_panels(task)
        if step == 3:
            from .steps.audio_utils import clear_mix_cache, clear_voice_cache
            clear_mix_cache()
            clear_voice_cache()

    def _rename_source_folder(self, task: TaskInfo):
        """混音完成后，将拖入的源文件夹重命名，添加\"双语-\"前缀。
        只有当同文件夹所有任务混音都完成后才执行重命名。
        """
        try:
            self._do_rename_source_folder(task)
        except Exception:
            pass

    def _do_rename_source_folder(self, task: TaskInfo):
        source_dir = task.import_folder or os.path.dirname(task.source_path)
        # 检查同文件夹的其他任务是否都已完成混音
        for t in self.task_queue.tasks:
            if t is task:
                continue
            t_dir = t.import_folder or os.path.dirname(t.source_path)
            if t_dir == source_dir and t.step3_status not in (STEP_DONE, STEP_FAILED, STEP_SKIPPED):
                return  # 还有未完成的任务，等最后一个再重命名
        parent = os.path.dirname(source_dir)
        dir_name = os.path.basename(source_dir)
        # 已有前缀则跳过
        if dir_name.startswith("双语-"):
            return
        new_name = f"双语-{dir_name}"
        new_path = os.path.join(parent, new_name)
        if os.path.exists(new_path):
            self._append_log(f"[重命名] 目标已存在，跳过: {new_name}")
            return

        # 重试最多3次，避免文件句柄未释放导致失败
        renamed = False
        for attempt in range(1, 4):
            try:
                os.rename(source_dir, new_path)
                renamed = True
                break
            except OSError:
                if attempt < 3:
                    time.sleep(0.3)

        if not renamed:
            self._append_log(f"[重命名] 失败 (已重试3次): {dir_name}")
            return

        # 同步更新所有同源目录任务的 source_path
        for t in self.task_queue.tasks:
            if os.path.dirname(t.source_path) == new_path:
                continue
            try:
                if os.path.commonpath([t.source_path, source_dir]) != source_dir:
                    continue
                rel = os.path.relpath(t.source_path, source_dir)
                t.source_path = os.path.join(new_path, rel)
                t.from_folder = True
            except (OSError, ValueError):
                continue
        self._append_log(f"[重命名] 文件夹已重命名: {dir_name} → {new_name}")

    def _rename_batch_folders(self):
        """批量完成后，延迟半秒再统一重命名所有文件夹导入的源文件夹。
        延迟是为了让所有线程/子进程释放文件句柄。
        """
        self._rename_retry_count = 0
        QTimer.singleShot(500, self._do_rename_batch_folders)

    def _do_rename_batch_folders(self):
        """执行批量重命名逻辑。

        收集条件与单任务模式保持一致：已完成(STEP_DONE) 或 已跳过
        (STEP_SKIPPED，如自动检测到已有双语输出) 的文件夹导入任务。
        若有重命名失败（文件句柄未释放等），稍后整体重试（幂等：已加前缀的跳过）。
        """
        try:
            # 收集需要重命名的文件夹（去重）
            folders_to_rename = set()
            for t in self.task_queue.tasks:
                if t.from_folder and t.step3_status in (STEP_DONE, STEP_SKIPPED):
                    source_dir = t.import_folder or os.path.dirname(t.source_path)
                    dir_name = os.path.basename(source_dir)
                    if not dir_name.startswith("双语-"):
                        folders_to_rename.add(source_dir)

            if not folders_to_rename:
                return

            any_failed = False
            for folder in sorted(folders_to_rename):
                parent = os.path.dirname(folder)
                dir_name = os.path.basename(folder)
                new_name = f"双语-{dir_name}"
                new_path = os.path.join(parent, new_name)
                if os.path.exists(new_path):
                    self._append_log(f"[重命名] 目标已存在，跳过: {new_name}")
                    continue
                renamed = False
                for attempt in range(1, 4):
                    try:
                        os.rename(folder, new_path)
                        renamed = True
                        break
                    except OSError:
                        if attempt < 3:
                            time.sleep(0.5)
                if not renamed:
                    any_failed = True
                    self._append_log(f"[重命名] 失败 (已重试3次): {dir_name}")
                    continue
                for t in self.task_queue.tasks:
                    try:
                        if os.path.commonpath([t.source_path, folder]) != folder and \
                           os.path.dirname(t.source_path) != folder:
                            continue
                        rel = os.path.relpath(t.source_path, folder)
                        t.source_path = os.path.join(new_path, rel)
                    except ValueError:
                        continue
                self._append_log(f"[重命名] 文件夹已重命名: {dir_name} → {new_name}")

            # 有失败的，稍后整体重试（最多 3 次；已成功的不受影响）
            if any_failed:
                self._rename_retry_count = getattr(self, "_rename_retry_count", 0) + 1
                if self._rename_retry_count <= 3:
                    self._append_log(f"[重命名] 有文件夹未重命名，1.5 秒后重试 "
                                     f"({self._rename_retry_count}/3)")
                    QTimer.singleShot(1500, self._do_rename_batch_folders)
        except Exception:
            pass

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

    # ========== 日志 ==========
    def _append_log(self, msg: str):
        self.log_view.append(msg)
        # 自动滚动到底部
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.status_bar_label.setText(msg[:60] + "..." if len(msg) > 60 else msg)
