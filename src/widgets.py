# -*- coding: utf-8 -*-
"""主控界面可复用组件：步骤面板、任务列表、TTS配置面板。"""
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QListWidget, QListWidgetItem,
    QFrame, QMenu, QComboBox, QSpinBox, QCheckBox, QDialog,
    QFormLayout, QLineEdit, QMessageBox, QGroupBox, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QStackedWidget, QDoubleSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QAction

from .task_manager import (
    TaskInfo, STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, _natural_key
)
from .steps.tts_cache import AudioCache


def _natural_sort_key(s: str) -> list:
    """自然排序 key：Track2 排在 Track10 之前。"""
    return _natural_key(s)


# 微软 Edge TTS 可用声音
EDGE_TTS_VOICES = {
    "zh-CN-XiaoxiaoNeural": "晓晓（女声）",
    "zh-CN-YunxiNeural": "云希（男声）",
    "zh-CN-XiaoyiNeural": "晓艺（女声）",
    "zh-CN-YunjianNeural": "云健（男声）",
    "zh-CN-XiaochenNeural": "晓辰（女声）",
    "zh-CN-XiaohanNeural": "晓涵（女声）",
    "zh-CN-XiaomengNeural": "晓梦（女声）",
    "zh-CN-XiaomoNeural": "晓墨（女声）",
    "zh-CN-XiaoqiuNeural": "晓秋（女声）",
    "zh-CN-XiaoruiNeural": "晓睿（女声）",
    "zh-CN-XiaoshuangNeural": "晓双（女声）",
    "zh-CN-XiaoxuanNeural": "晓萱（女声）",
    "zh-CN-XiaoyanNeural": "晓颜（女声）",
    "zh-CN-XiaozhenNeural": "晓甄（女声）",
    "zh-CN-YunfengNeural": "云枫（男声）",
    "zh-CN-YunhaoNeural": "云皓（男声）",
    "zh-CN-YunxiaNeural": "云夏（男声）",
    "zh-CN-YunyangNeural": "云扬（男声）",
    "zh-CN-YunzeNeural": "云泽（男声）",
}

# 语速/音量预设
RATE_PRESETS = ["-50%", "-30%", "-20%", "-10%", "+0%", "+10%", "+20%", "+30%", "+50%"]
VOLUME_PRESETS = ["-50%", "-30%", "-20%", "-10%", "+0%", "+10%", "+20%", "+30%", "+50%"]


# 步骤状态对应的显示文本和颜色
STATUS_TEXT = {
    STEP_PENDING: "等待中",
    STEP_RUNNING: "进行中",
    STEP_DONE: "已完成",
    STEP_FAILED: "失败",
    "skipped": "已跳过",
}
STATUS_COLOR = {
    STEP_PENDING: QColor(128, 128, 128),
    STEP_RUNNING: QColor(0, 120, 215),
    STEP_DONE: QColor(0, 160, 0),
    STEP_FAILED: QColor(200, 0, 0),
    "skipped": QColor(128, 128, 128),
}


class StepPanel(QFrame):
    """单个步骤的面板：标题+状态+进度+输出+按钮。"""

    start_requested = pyqtSignal(int)   # step_number
    stop_requested = pyqtSignal(int)

    def __init__(self, step_number: int, title: str, parent=None):
        super().__init__()
        self.step_number = step_number
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._build_ui(title)

    def _build_ui(self, title: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        # 标题行
        header = QHBoxLayout()
        title_label = QLabel(f"<b>步骤 {self.step_number}：{title}</b>")
        header.addWidget(title_label)
        header.addStretch()
        self.status_label = QLabel(STATUS_TEXT[STEP_PENDING])
        self.status_label.setStyleSheet("color: gray;")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        # 输出路径
        self.output_label = QLabel("输出：—")
        self.output_label.setStyleSheet("color: #666; font-size: 11px;")
        self.output_label.setWordWrap(True)
        layout.addWidget(self.output_label)

        # 按钮
        self.btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始")
        self.start_btn.clicked.connect(lambda: self.start_requested.emit(self.step_number))
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.stop_requested.emit(self.step_number))
        self.btn_row.addWidget(self.start_btn)
        self.btn_row.addWidget(self.stop_btn)
        self.btn_row.addStretch()
        layout.addLayout(self.btn_row)

    def add_extra_button(self, text: str) -> QPushButton:
        """在按钮行右侧添加一个额外按钮，返回按钮实例（外部连接信号）。"""
        btn = QPushButton(text)
        self.btn_row.addWidget(btn)
        return btn

    def set_status(self, status: str):
        self.status_label.setText(STATUS_TEXT.get(status, status))
        self.status_label.setStyleSheet(f"color: {STATUS_COLOR.get(status, QColor(0,0,0)).name()};")

    def set_progress(self, value: int, maximum: int = 100):
        self.progress.setRange(0, maximum)
        self.progress.setValue(value)

    def set_output(self, path: str):
        if path:
            display = path if len(path) < 80 else "..." + path[-77:]
            self.output_label.setText(f"输出：<a href='file:///{path}'>{display}</a>")
            self.output_label.setOpenExternalLinks(True)
        else:
            self.output_label.setText("输出：—")

    def set_running(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def reset(self):
        self.set_status(STEP_PENDING)
        self.progress.setValue(0)
        self.set_output("")
        self.set_running(False)


class TaskListWidget(QListWidget):
    """左侧任务列表，支持拖拽添加视频/字幕/配音文件。"""
    task_selected = pyqtSignal(str)   # task_id
    task_remove_requested = pyqtSignal(str)
    task_rerun_requested = pyqtSignal(str, int)  # (task_id, step)

    # 源视频/音频文件（创建新任务）
    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts",
                  ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
    # 字幕文件（添加到当前任务，跳过步骤1）
    SUBTITLE_EXTS = {".lrc", ".vtt", ".srt"}
    # 配音文件（添加到当前任务，跳过步骤2）
    MIX_AUDIO_EXTS = {".wav"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.currentItemChanged.connect(self._on_item_changed)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._task_queue = None  # 由主窗口设置引用

    def set_task_queue(self, task_queue):
        """主窗口设置 task_queue 引用，用于按 source_name 排序时反查。"""
        self._task_queue = task_queue

    def add_task_item(self, task: TaskInfo):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, task.task_id)
        self._update_item_text(item, task)
        # 按 source_name 自然顺序找到插入位置
        new_key = _natural_sort_key(task.source_name)
        insert_idx = self.count()
        for i in range(self.count()):
            existing_item = self.item(i)
            existing_tid = existing_item.data(Qt.ItemDataRole.UserRole)
            existing_task = (
                self._task_queue.get_task(existing_tid)
                if self._task_queue else None
            )
            if existing_task is None:
                continue
            if _natural_sort_key(existing_task.source_name) > new_key:
                insert_idx = i
                break
        self.insertItem(insert_idx, item)
        self.setCurrentItem(item)

    def update_task_item(self, task: TaskInfo):
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == task.task_id:
                self._update_item_text(item, task)
                break

    def remove_task_item(self, task_id: str):
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.takeItem(i)
                break

    def _update_item_text(self, item: QListWidgetItem, task: TaskInfo):
        progress = int(task.overall_progress() * 100)
        status_icon = {"done": "✓", "running": "▶", "failed": "✗", "pending": "○"}.get(
            self._overall_status(task), "○"
        )
        text = f"{progress}% {status_icon} {task.source_name}"
        item.setText(text)

    @staticmethod
    def _overall_status(task: TaskInfo) -> str:
        if task.step3_status == STEP_DONE:
            return "done"
        if task.step1_status == STEP_FAILED or task.step2_status == STEP_FAILED or task.step3_status == STEP_FAILED:
            return "failed"
        if task.step1_status == STEP_RUNNING or task.step2_status == STEP_RUNNING or task.step3_status == STEP_RUNNING:
            return "running"
        return "pending"

    def _on_item_changed(self, current, previous):
        if current:
            task_id = current.data(Qt.ItemDataRole.UserRole)
            self.task_selected.emit(task_id)

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        act_remove = QAction("移除任务", self)
        act_remove.triggered.connect(lambda: self.task_remove_requested.emit(task_id))
        menu.addAction(act_remove)
        menu.addSeparator()
        act_rerun1 = QAction("重跑 步骤1(字幕)", self)
        act_rerun1.triggered.connect(lambda: self.task_rerun_requested.emit(task_id, 1))
        act_rerun2 = QAction("重跑 步骤2(配音)", self)
        act_rerun2.triggered.connect(lambda: self.task_rerun_requested.emit(task_id, 2))
        act_rerun3 = QAction("重跑 步骤3(混音)", self)
        act_rerun3.triggered.connect(lambda: self.task_rerun_requested.emit(task_id, 3))
        menu.addAction(act_rerun1)
        menu.addAction(act_rerun2)
        menu.addAction(act_rerun3)
        menu.exec(self.viewport().mapToGlobal(pos))

    # 拖拽支持
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        files = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                files.append(path)
        if files:
            # 主窗口根据文件类型分发：视频→新任务，字幕→跳过步骤1，wav/目录→跳过步骤2
            self.window().on_files_dropped(files)
        event.acceptProposedAction()


class TTSConfigPanel(QGroupBox):
    """TTS 配置面板：模式选择、edge参数、api选择。

    修改配置后自动保存到 WorkflowConfig，并发 config_changed 信号。
    """
    config_changed = pyqtSignal()

    def __init__(self, config, parent=None):
        super().__init__("TTS 配置")
        self.config = config
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 第1行：模式选择
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("微软 Edge TTS（免费）", "edge")
        self.mode_combo.addItem("API 服务器", "api")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row1.addWidget(self.mode_combo)
        row1.addStretch()
        layout.addLayout(row1)

        # edge 模式配置
        self.edge_group = QGroupBox("Edge TTS 参数")
        edge_layout = QGridLayout(self.edge_group)

        edge_layout.addWidget(QLabel("声音:"), 0, 0)
        self.voice_combo = QComboBox()
        for vid, name in EDGE_TTS_VOICES.items():
            self.voice_combo.addItem(f"{name} - {vid}", vid)
        self.voice_combo.currentIndexChanged.connect(self._save)
        edge_layout.addWidget(self.voice_combo, 0, 1)

        edge_layout.addWidget(QLabel("语速:"), 1, 0)
        self.rate_combo = QComboBox()
        self.rate_combo.addItems(RATE_PRESETS)
        self.rate_combo.setEditable(True)
        self.rate_combo.currentIndexChanged.connect(self._save)
        self.rate_combo.editTextChanged.connect(self._save)
        edge_layout.addWidget(self.rate_combo, 1, 1)

        edge_layout.addWidget(QLabel("音量:"), 2, 0)
        self.volume_combo = QComboBox()
        self.volume_combo.addItems(VOLUME_PRESETS)
        self.volume_combo.setEditable(True)
        self.volume_combo.currentIndexChanged.connect(self._save)
        self.volume_combo.editTextChanged.connect(self._save)
        edge_layout.addWidget(self.volume_combo, 2, 1)

        edge_layout.addWidget(QLabel("线程数:"), 3, 0)
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 20)
        self.threads_spin.valueChanged.connect(self._save)
        edge_layout.addWidget(self.threads_spin, 3, 1)

        # api 模式配置
        self.api_group = QGroupBox("API 服务器参数")
        api_layout = QGridLayout(self.api_group)

        api_layout.addWidget(QLabel("当前API:"), 0, 0)
        self.api_combo = QComboBox()
        self.api_combo.currentIndexChanged.connect(self._on_api_changed)
        api_layout.addWidget(self.api_combo, 0, 1)

        edit_api_btn = QPushButton("编辑API列表...")
        edit_api_btn.clicked.connect(self._on_edit_apis)
        api_layout.addWidget(edit_api_btn, 0, 2)

        api_layout.addWidget(QLabel("批量大小:"), 1, 0)
        self.bulk_spin = QSpinBox()
        self.bulk_spin.setRange(1, 100)
        self.bulk_spin.valueChanged.connect(self._save)
        api_layout.addWidget(self.bulk_spin, 1, 1)

        self.multi_api_check = QCheckBox("多API轮询（失败自动切换下一个）")
        self.multi_api_check.toggled.connect(self._save)
        api_layout.addWidget(self.multi_api_check, 2, 0, 1, 3)

        self.bulk_check = QCheckBox("使用批量API（加速合成）")
        self.bulk_check.toggled.connect(self._save)
        api_layout.addWidget(self.bulk_check, 3, 0, 1, 3)

        # 用 QStackedWidget 切换 edge/api 面板，避免窗口被撑大后不缩小
        self.config_stack = QStackedWidget()
        self.config_stack.addWidget(self.edge_group)   # index 0 = edge
        self.config_stack.addWidget(self.api_group)    # index 1 = api
        layout.addWidget(self.config_stack)

        # 缓存信息 + 清除按钮
        cache_row = QHBoxLayout()
        self.cache_info_label = QLabel("缓存：加载中...")
        self.cache_info_label.setStyleSheet("color: #666; font-size: 12px;")
        cache_row.addWidget(self.cache_info_label)
        cache_row.addStretch()
        clear_cache_btn = QPushButton("清除缓存")
        clear_cache_btn.setToolTip("删除所有 TTS 音频缓存，下次合成将重新生成")
        clear_cache_btn.clicked.connect(self._on_clear_cache)
        cache_row.addWidget(clear_cache_btn)
        layout.addLayout(cache_row)

    def _load_values(self):
        cfg = self.config.tts_cfg
        # 阻断信号，防止加载过程中 _on_mode_changed→_save 覆盖配置
        widgets = [self.mode_combo, self.voice_combo, self.rate_combo,
                   self.volume_combo, self.threads_spin, self.api_combo,
                   self.bulk_spin, self.multi_api_check, self.bulk_check]
        for w in widgets:
            w.blockSignals(True)
        try:
            # 模式
            mode = cfg.get("tts_mode", "edge")
            idx = self.mode_combo.findData(mode)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)
            # edge 参数
            voice = cfg.get("edge_voice", "zh-CN-XiaoxiaoNeural")
            idx = self.voice_combo.findData(voice)
            if idx >= 0:
                self.voice_combo.setCurrentIndex(idx)
            self.rate_combo.setCurrentText(cfg.get("edge_rate", "+0%"))
            self.volume_combo.setCurrentText(cfg.get("edge_volume", "+0%"))
            self.threads_spin.setValue(cfg.get("edge_threads", 5))
            # api 参数
            self._refresh_api_combo()
            self.bulk_spin.setValue(cfg.get("bulk_batch_size", 20))
            self.multi_api_check.setChecked(cfg.get("use_multi_api", False))
            self.bulk_check.setChecked(cfg.get("use_bulk_api", True))
            # 显示对应面板（不触发 _save）
            mode = self.mode_combo.currentData()
            self.config_stack.setCurrentIndex(0 if mode == "edge" else 1)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._refresh_cache_info()

    def _refresh_api_combo(self):
        cfg = self.config.tts_cfg
        apis = cfg.get("api_configs", [])
        self.api_combo.blockSignals(True)
        self.api_combo.clear()
        for api in apis:
            self.api_combo.addItem(f"{api.get('name','?')} - {api.get('url','')}", api.get("name", ""))
        idx = cfg.get("current_api_index", 0)
        if 0 <= idx < self.api_combo.count():
            self.api_combo.setCurrentIndex(idx)
        self.api_combo.blockSignals(False)

    def _on_mode_changed(self):
        mode = self.mode_combo.currentData()
        self.config_stack.setCurrentIndex(0 if mode == "edge" else 1)
        self._save()

    def _on_api_changed(self):
        idx = self.api_combo.currentIndex()
        self.config.tts_cfg["current_api_index"] = max(0, idx)
        self._save()

    def _on_edit_apis(self):
        dialog = ApiConfigDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_api_combo()
            self._save()

    def _save(self):
        cfg = self.config.tts_cfg
        cfg["tts_mode"] = self.mode_combo.currentData()
        cfg["edge_voice"] = self.voice_combo.currentData()
        cfg["edge_rate"] = self.rate_combo.currentText()
        cfg["edge_volume"] = self.volume_combo.currentText()
        cfg["edge_threads"] = self.threads_spin.value()
        cfg["bulk_batch_size"] = self.bulk_spin.value()
        cfg["use_multi_api"] = self.multi_api_check.isChecked()
        cfg["use_bulk_api"] = self.bulk_check.isChecked()
        cfg["current_api_index"] = max(0, self.api_combo.currentIndex())
        self.config.save()
        self.config_changed.emit()

    def _refresh_cache_info(self):
        """刷新缓存统计信息显示。"""
        try:
            cache = AudioCache()
            stats = cache.get_cache_stats()
            count = stats["total_count"]
            size_mb = stats["total_size"] / 1024 / 1024
            reuse = stats["total_reuse"]
            self.cache_info_label.setText(
                f"缓存：{count} 条 | {size_mb:.2f} MB | 累计复用 {reuse} 次"
            )
            cache.close()
        except Exception:
            self.cache_info_label.setText("缓存：读取失败")

    def _on_clear_cache(self):
        """清除所有 TTS 音频缓存。"""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "确认清除缓存",
            "确定要清除所有 TTS 音频缓存吗？\n"
            "清除后，之前合成的音频将不可复用，需要重新生成。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            cache = AudioCache()
            deleted_count, deleted_files = cache.clear_cache()
            cache.close()
            self._refresh_cache_info()
            QMessageBox.information(
                self, "清除完成",
                f"已清除 {deleted_count} 条缓存记录，删除 {deleted_files} 个文件。"
            )
        except Exception as e:
            QMessageBox.warning(self, "清除失败", f"清除缓存时出错：{e}")


class ApiConfigDialog(QDialog):
    """API 服务器列表编辑对话框。"""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("编辑 API 服务器列表")
        self.setMinimumWidth(560)
        self._apis = [dict(a) for a in config.tts_cfg.get("api_configs", [])]
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 表格
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "URL", "模型", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked)
        layout.addWidget(self.table)
        self._refresh_table()

        # 按钮
        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ 添加")
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)
        del_btn = QPushButton("- 删除")
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _refresh_table(self):
        self.table.setRowCount(len(self._apis))
        for i, api in enumerate(self._apis):
            self.table.setItem(i, 0, QTableWidgetItem(api.get("name", "")))
            self.table.setItem(i, 1, QTableWidgetItem(api.get("url", "")))
            self.table.setItem(i, 2, QTableWidgetItem(api.get("model", "")))
            self.table.setItem(i, 3, QTableWidgetItem(api.get("status", "unknown")))

    def _on_add(self):
        self._apis.append({"name": "新服务器", "url": "http://", "model": "", "status": "unknown"})
        self._refresh_table()
        self.table.selectRow(len(self._apis) - 1)

    def _on_delete(self):
        row = self.table.currentRow()
        if row >= 0 and row < len(self._apis):
            self._apis.pop(row)
            self._refresh_table()

    def _on_accept(self):
        # 从表格收集数据
        for i in range(self.table.rowCount()):
            self._apis[i]["name"] = self.table.item(i, 0).text()
            self._apis[i]["url"] = self.table.item(i, 1).text()
            self._apis[i]["model"] = self.table.item(i, 2).text()
            self._apis[i]["status"] = self.table.item(i, 3).text()
        if not self._apis:
            QMessageBox.warning(self, "提示", "至少保留一个 API 配置。")
            return
        self.config.tts_cfg["api_configs"] = self._apis
        # 修正 current_api_index
        idx = self.config.tts_cfg.get("current_api_index", 0)
        if idx >= len(self._apis):
            self.config.tts_cfg["current_api_index"] = 0
        self.config.save()
        self.accept()


class MixerConfigPanel(QGroupBox):
    """音频混音配置面板：导出格式、音量、声道等。

    修改配置后自动保存到 WorkflowConfig，并发 config_changed 信号。
    """
    config_changed = pyqtSignal()

    def __init__(self, config, parent=None):
        super().__init__("混音配置")
        self.config = config
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QFormLayout(self)

        # 导出目录
        out_box = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("留空=输出到源文件同目录下的\"双语\"文件夹")
        self.output_edit.textChanged.connect(self._save)
        out_box.addWidget(self.output_edit)
        browse_btn = QPushButton("选择...")
        browse_btn.clicked.connect(self._on_browse_output)
        out_box.addWidget(browse_btn)
        out_widget = QWidget()
        out_widget.setLayout(out_box)
        layout.addRow("导出目录:", out_widget)

        # 导出格式
        self.format_combo = QComboBox()
        self.format_combo.addItem("MP4（视频，H.264）", "mp4")
        self.format_combo.addItem("MP3（音频）", "mp3")
        self.format_combo.addItem("M4A（AAC 音频）", "m4a")
        self.format_combo.addItem("WAV（无损音频）", "wav")
        self.format_combo.addItem("AAC（音频）", "aac")
        self.format_combo.currentIndexChanged.connect(self._save)
        layout.addRow("导出格式:", self.format_combo)

        # 音量调整（dB）
        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(-30.0, 30.0)
        self.volume_spin.setSingleStep(0.5)
        self.volume_spin.setSuffix(" dB")
        self.volume_spin.valueChanged.connect(self._save)
        layout.addRow("配音音量:", self.volume_spin)

        # 自动音量模式
        self.auto_vol_combo = QComboBox()
        self.auto_vol_combo.addItem("固定音量（volume_db）", "fixed")
        self.auto_vol_combo.addItem("自动匹配原音频响度", "auto")
        self.auto_vol_combo.addItem("关闭（不调整）", "off")
        self.auto_vol_combo.currentIndexChanged.connect(self._save)
        layout.addRow("音量模式:", self.auto_vol_combo)

        # 声道检测
        self.channel_detect_check = QCheckBox("启用声道检测（根据原音频位置自动定位）")
        self.channel_detect_check.toggled.connect(self._save)
        layout.addRow("", self.channel_detect_check)

        # 声道映射角度
        chan_box = QHBoxLayout()
        chan_box.addWidget(QLabel("左:"))
        self.angle_left = QSpinBox()
        self.angle_left.setRange(0, 180)
        self.angle_left.valueChanged.connect(self._save)
        chan_box.addWidget(self.angle_left)
        chan_box.addWidget(QLabel("中:"))
        self.angle_both = QSpinBox()
        self.angle_both.setRange(0, 180)
        self.angle_both.valueChanged.connect(self._save)
        chan_box.addWidget(self.angle_both)
        chan_box.addWidget(QLabel("右:"))
        self.angle_right = QSpinBox()
        self.angle_right.setRange(0, 180)
        self.angle_right.valueChanged.connect(self._save)
        chan_box.addWidget(self.angle_right)
        chan_widget = QWidget()
        chan_widget.setLayout(chan_box)
        layout.addRow("声道角度:", chan_widget)

        # 起始对齐
        self.align_check = QCheckBox("起始对齐（对齐配音起拍点到原音频）")
        self.align_check.toggled.connect(self._save)
        layout.addRow("", self.align_check)

        # 内容对齐
        self.content_align_check = QCheckBox("内容对齐（用原音频人声起始点纠正时间戳）")
        self.content_align_check.toggled.connect(self._save)
        layout.addRow("", self.content_align_check)

        # GPU 加速
        self.gpu_check = QCheckBox("GPU 加速导出（NVENC，需 NVIDIA 显卡）")
        self.gpu_check.toggled.connect(self._save)
        layout.addRow("", self.gpu_check)

        # 添加后缀
        self.suffix_check = QCheckBox("输出文件添加 _mixed 后缀")
        self.suffix_check.toggled.connect(self._save)
        layout.addRow("", self.suffix_check)

        # 文件夹前缀
        self.folder_prefix_check = QCheckBox("文件夹导入混音后重命名源文件夹（添加\"双语-\"前缀）")
        self.folder_prefix_check.toggled.connect(self._save)
        layout.addRow("", self.folder_prefix_check)

        # 跳过已存在
        self.skip_check = QCheckBox("跳过已存在的输出文件")
        self.skip_check.toggled.connect(self._save)
        layout.addRow("", self.skip_check)

        # 线程数
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, 32)
        self.thread_spin.setSuffix(" 线程")
        self.thread_spin.valueChanged.connect(self._save)
        layout.addRow("线程数:", self.thread_spin)

        # 批量并行
        self.batch_parallel_check = QCheckBox("批量执行时多任务并行混音")
        self.batch_parallel_check.toggled.connect(self._save)
        layout.addRow("", self.batch_parallel_check)

    def _load_values(self):
        cfg = self.config.mixer_cfg
        widgets = [self.output_edit, self.format_combo, self.volume_spin,
                   self.auto_vol_combo, self.channel_detect_check,
                   self.angle_left, self.angle_both, self.angle_right,
                   self.align_check, self.content_align_check, self.gpu_check,
                   self.suffix_check, self.folder_prefix_check,
                   self.skip_check, self.thread_spin,
                   self.batch_parallel_check]
        for w in widgets:
            w.blockSignals(True)
        try:
            # 导出目录
            self.output_edit.setText(cfg.get("output_folder", ""))
            # 导出格式
            fmt = cfg.get("output_format", "mp4")
            idx = self.format_combo.findData(fmt)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
            # 音量
            self.volume_spin.setValue(cfg.get("volume_db", 0.0))
            # 自动音量
            av = cfg.get("auto_volume", "fixed")
            idx = self.auto_vol_combo.findData(av)
            if idx >= 0:
                self.auto_vol_combo.setCurrentIndex(idx)
            # 声道检测
            self.channel_detect_check.setChecked(cfg.get("channel_detect", True))
            # 声道角度
            cm = cfg.get("channel_map", {"left": 155, "right": 25, "both": 135})
            self.angle_left.setValue(cm.get("left", 155))
            self.angle_both.setValue(cm.get("both", 135))
            self.angle_right.setValue(cm.get("right", 25))
            # 其他选项
            self.align_check.setChecked(cfg.get("align_onset", True))
            self.content_align_check.setChecked(cfg.get("content_alignment", False))
            self.gpu_check.setChecked(cfg.get("use_gpu", True))
            self.suffix_check.setChecked(cfg.get("add_suffix", True))
            self.folder_prefix_check.setChecked(cfg.get("folder_prefix", True))
            self.skip_check.setChecked(cfg.get("skip_existing", True))
            self.thread_spin.setValue(cfg.get("thread_count", 4))
            self.batch_parallel_check.setChecked(cfg.get("enable_batch_parallel", True))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _save(self):
        cfg = self.config.mixer_cfg
        cfg["output_folder"] = self.output_edit.text().strip()
        cfg["output_format"] = self.format_combo.currentData()
        cfg["volume_db"] = self.volume_spin.value()
        cfg["auto_volume"] = self.auto_vol_combo.currentData()
        cfg["channel_detect"] = self.channel_detect_check.isChecked()
        cfg["channel_map"] = {
            "left": self.angle_left.value(),
            "both": self.angle_both.value(),
            "right": self.angle_right.value(),
        }
        cfg["align_onset"] = self.align_check.isChecked()
        cfg["content_alignment"] = self.content_align_check.isChecked()
        cfg["use_gpu"] = self.gpu_check.isChecked()
        cfg["add_suffix"] = self.suffix_check.isChecked()
        cfg["folder_prefix"] = self.folder_prefix_check.isChecked()
        cfg["skip_existing"] = self.skip_check.isChecked()
        cfg["thread_count"] = self.thread_spin.value()
        cfg["enable_batch_parallel"] = self.batch_parallel_check.isChecked()
        self.config.save()
        self.config_changed.emit()

    def _on_browse_output(self):
        from PyQt6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if folder:
            self.output_edit.setText(folder)


class WhisperConfigPanel(QGroupBox):
    """字幕提取配置面板：设备、VAD、合并段落等。

    修改配置后自动保存到 WorkflowConfig，并发 config_changed 信号。
    """
    config_changed = pyqtSignal()

    def __init__(self, config, parent=None):
        super().__init__("字幕提取配置")
        self.config = config
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QFormLayout(self)

        # 设备
        self.device_combo = QComboBox()
        self.device_combo.addItem("自动", "auto")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("CUDA（GPU）", "cuda")
        self.device_combo.currentIndexChanged.connect(self._save)
        layout.addRow("设备:", self.device_combo)

        # 计算精度
        self.compute_combo = QComboBox()
        self.compute_combo.addItem("自动", "auto")
        self.compute_combo.addItem("int8（最快/省显存）", "int8")
        self.compute_combo.addItem("int8_float16", "int8_float16")
        self.compute_combo.addItem("float16（平衡）", "float16")
        self.compute_combo.addItem("float32（最精确）", "float32")
        self.compute_combo.currentIndexChanged.connect(self._save)
        layout.addRow("计算精度:", self.compute_combo)

        # 字幕格式
        self.subfmt_combo = QComboBox()
        self.subfmt_combo.addItem("LRC", "lrc")
        self.subfmt_combo.addItem("VTT", "vtt")
        self.subfmt_combo.addItem("LRC + VTT", "lrc,vtt")
        self.subfmt_combo.addItem("SRT", "srt")
        self.subfmt_combo.addItem("LRC + SRT", "lrc,srt")
        self.subfmt_combo.currentIndexChanged.connect(self._save)
        layout.addRow("字幕格式:", self.subfmt_combo)

        # VAD 阈值
        self.vad_threshold_spin = QDoubleSpinBox()
        self.vad_threshold_spin.setRange(0.0, 1.0)
        self.vad_threshold_spin.setSingleStep(0.05)
        self.vad_threshold_spin.valueChanged.connect(self._save)
        layout.addRow("VAD 阈值:", self.vad_threshold_spin)

        # 最小静音时长
        self.vad_silence_spin = QSpinBox()
        self.vad_silence_spin.setRange(0, 5000)
        self.vad_silence_spin.setSingleStep(50)
        self.vad_silence_spin.setSuffix(" ms")
        self.vad_silence_spin.valueChanged.connect(self._save)
        layout.addRow("最小静音时长:", self.vad_silence_spin)

        # 最小语音时长
        self.vad_speech_spin = QSpinBox()
        self.vad_speech_spin.setRange(0, 5000)
        self.vad_speech_spin.setSingleStep(50)
        self.vad_speech_spin.setSuffix(" ms")
        self.vad_speech_spin.valueChanged.connect(self._save)
        layout.addRow("最小语音时长:", self.vad_speech_spin)

        # 语音填充
        self.vad_pad_spin = QSpinBox()
        self.vad_pad_spin.setRange(0, 2000)
        self.vad_pad_spin.setSingleStep(50)
        self.vad_pad_spin.setSuffix(" ms")
        self.vad_pad_spin.valueChanged.connect(self._save)
        layout.addRow("语音填充:", self.vad_pad_spin)

        # 合并段落
        self.merge_check = QCheckBox("启用段落合并")
        self.merge_check.toggled.connect(self._save)
        layout.addRow("", self.merge_check)

        # 合并最大间隔
        self.merge_gap_spin = QSpinBox()
        self.merge_gap_spin.setRange(0, 5000)
        self.merge_gap_spin.setSingleStep(50)
        self.merge_gap_spin.setSuffix(" ms")
        self.merge_gap_spin.valueChanged.connect(self._save)
        layout.addRow("合并最大间隔:", self.merge_gap_spin)

        # 合并最大时长
        self.merge_dur_spin = QSpinBox()
        self.merge_dur_spin.setRange(1000, 60000)
        self.merge_dur_spin.setSingleStep(1000)
        self.merge_dur_spin.setSuffix(" ms")
        self.merge_dur_spin.valueChanged.connect(self._save)
        layout.addRow("合并最大时长:", self.merge_dur_spin)

        # 批处理
        self.batch_check = QCheckBox("启用批处理（加速但占更多显存）")
        self.batch_check.toggled.connect(self._save)
        layout.addRow("", self.batch_check)

        # 覆盖已有
        self.overwrite_check = QCheckBox("覆盖已有字幕文件")
        self.overwrite_check.toggled.connect(self._save)
        layout.addRow("", self.overwrite_check)

    def _load_values(self):
        cfg = self.config.whisper_cfg
        widgets = [self.device_combo, self.compute_combo, self.subfmt_combo,
                   self.vad_threshold_spin, self.vad_silence_spin,
                   self.vad_speech_spin, self.vad_pad_spin, self.merge_check,
                   self.merge_gap_spin, self.merge_dur_spin, self.batch_check,
                   self.overwrite_check]
        for w in widgets:
            w.blockSignals(True)
        try:
            # 设备
            dev = cfg.get("device", "auto")
            idx = self.device_combo.findData(dev)
            if idx >= 0:
                self.device_combo.setCurrentIndex(idx)
            # 计算精度
            ct = cfg.get("compute_type", "auto")
            idx = self.compute_combo.findData(ct)
            if idx >= 0:
                self.compute_combo.setCurrentIndex(idx)
            # 字幕格式
            sf = cfg.get("sub_formats", "lrc")
            idx = self.subfmt_combo.findData(sf)
            if idx >= 0:
                self.subfmt_combo.setCurrentIndex(idx)
            # VAD 参数
            self.vad_threshold_spin.setValue(cfg.get("vad_threshold", 0.5))
            self.vad_silence_spin.setValue(cfg.get("vad_min_silence_duration_ms", 500))
            self.vad_speech_spin.setValue(cfg.get("vad_min_speech_duration_ms", 0))
            self.vad_pad_spin.setValue(cfg.get("vad_speech_pad_ms", 400))
            # 合并
            self.merge_check.setChecked(cfg.get("merge_segments", True))
            self.merge_gap_spin.setValue(cfg.get("merge_max_gap_ms", 300))
            self.merge_dur_spin.setValue(cfg.get("merge_max_duration_ms", 30000))
            # 其他
            self.batch_check.setChecked(cfg.get("enable_batching", False))
            self.overwrite_check.setChecked(cfg.get("overwrite", False))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _save(self):
        cfg = self.config.whisper_cfg
        cfg["device"] = self.device_combo.currentData()
        cfg["compute_type"] = self.compute_combo.currentData()
        cfg["sub_formats"] = self.subfmt_combo.currentData()
        cfg["vad_threshold"] = self.vad_threshold_spin.value()
        cfg["vad_min_silence_duration_ms"] = self.vad_silence_spin.value()
        cfg["vad_min_speech_duration_ms"] = self.vad_speech_spin.value()
        cfg["vad_speech_pad_ms"] = self.vad_pad_spin.value()
        cfg["merge_segments"] = self.merge_check.isChecked()
        cfg["merge_max_gap_ms"] = self.merge_gap_spin.value()
        cfg["merge_max_duration_ms"] = self.merge_dur_spin.value()
        cfg["enable_batching"] = self.batch_check.isChecked()
        cfg["overwrite"] = self.overwrite_check.isChecked()
        self.config.save()
        self.config_changed.emit()
