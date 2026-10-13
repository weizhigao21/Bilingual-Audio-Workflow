# -*- coding: utf-8 -*-
"""TTS 配置面板与 API 服务器列表编辑对话框（声笺风格）。

模式使用紧凑分段按钮切换（Edge TTS / API 服务器），选中项白底细边框；
两组参数分别放在白色设置卡片中。修改配置后自动保存并发出 config_changed。
"""
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget
)
from PyQt6.QtCore import pyqtSignal

from ..steps.tts_cache import AudioCache
from .common import EDGE_TTS_VOICES, RATE_PRESETS, VOLUME_PRESETS
from .local_voice_picker import VoicePicker
from .settings_group import SettingsGroup, SegmentedControl


class TTSConfigPanel(QWidget):
    """TTS 配置面板：模式选择、Edge 参数、API 参数。"""

    config_changed = pyqtSignal()
    log_message = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        # 模式：紧凑分段按钮（2 个互斥选项）
        mode_row = QHBoxLayout()
        mode_label = QLabel("合成模式")
        mode_label.setProperty("role", "section")
        mode_row.addWidget(mode_label)
        mode_row.addStretch(1)
        self.mode_segment = SegmentedControl([
            ("Edge TTS（免费）", "edge"),
            ("API 服务器", "api"),
        ])
        self.mode_segment.current_changed.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_segment)
        layout.addLayout(mode_row)

        # Edge 模式参数
        self.edge_group = SettingsGroup(
            "Edge TTS 参数", "免费微软在线合成，无需本地服务。")
        self.voice_combo = QComboBox()
        for vid, name in EDGE_TTS_VOICES.items():
            self.voice_combo.addItem(f"{name} - {vid}", vid)
        self.voice_combo.currentIndexChanged.connect(self._save)
        self.edge_group.add_row("声音", "免费 Edge TTS 仅支持上列普通话声音",
                                self.voice_combo)
        self.voice_hint = QLabel()
        self.voice_hint.setWordWrap(True)
        self.voice_hint.setProperty("role", "muted")
        self.edge_group.add_widget_row(self.voice_hint)

        self.rate_combo = QComboBox()
        self.rate_combo.addItems(RATE_PRESETS)
        self.rate_combo.setEditable(True)
        self.rate_combo.currentIndexChanged.connect(self._save)
        self.rate_combo.editTextChanged.connect(self._save)
        self.edge_group.add_row("语速", "相对基准语速的百分比", self.rate_combo)

        self.volume_combo = QComboBox()
        self.volume_combo.addItems(VOLUME_PRESETS)
        self.volume_combo.setEditable(True)
        self.volume_combo.currentIndexChanged.connect(self._save)
        self.volume_combo.editTextChanged.connect(self._save)
        self.edge_group.add_row("音量", "相对基准音量的百分比", self.volume_combo)

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 20)
        self.threads_spin.setToolTip(
            "Edge 模式全局共享线程池的线程数（所有音频共用同一个池，\n"
            "不随音频数叠加）。同时可运行的任务数。\n"
            "实际发出的请求数另由「全局并发上限」闸门控制。"
        )
        self.threads_spin.valueChanged.connect(self._save)
        self.edge_group.add_row("线程池数量", "所有音频共享的合成线程池",
                                self.threads_spin)

        self.max_concurrent_spin = QSpinBox()
        self.max_concurrent_spin.setRange(1, 20)
        self.max_concurrent_spin.setToolTip(
            "Edge 模式真正发出的并发请求数上限（请求闸门）。\n"
            "超过线程池数量的任务会在闸门排队。过高会触发微软服务限流\n"
            "（Cannot connect to ...bing.com:443），建议 5-8"
        )
        self.max_concurrent_spin.valueChanged.connect(self._save)
        self.edge_group.add_row("全局并发上限", "请求闸门，防止触发限流",
                                self.max_concurrent_spin)

        self.pipeline_tts_spin = QSpinBox()
        self.pipeline_tts_spin.setRange(1, 10)
        self.pipeline_tts_spin.setToolTip(
            "流水线模式下同时进行语音生成的音频数（任务级并行）。\n"
            "设 1 = 按音频逐个生成语音；2 = 最多 2 个音频同时生成。\n"
            "片段级并发由「全局并发上限」统一控制（共享线程池，不叠加）。"
        )
        self.pipeline_tts_spin.valueChanged.connect(self._save)
        self.edge_group.add_row("流水线语音并行", "流水线模式下的任务级并行数",
                                self.pipeline_tts_spin)

        # API 模式参数
        self.api_group = SettingsGroup(
            "API 服务器参数", "连接本地/远程 TTS 服务（如声笺、GPT-SoVITS）。")

        api_row = QHBoxLayout()
        api_row.setSpacing(6)
        self.api_combo = QComboBox()
        self.api_combo.currentIndexChanged.connect(self._on_api_changed)
        api_row.addWidget(self.api_combo, 1)
        edit_api_btn = QPushButton("编辑列表…")
        edit_api_btn.clicked.connect(self._on_edit_apis)
        api_row.addWidget(edit_api_btn)
        test_api_btn = QPushButton("测试全部")
        test_api_btn.setToolTip("测试所有 API 服务器是否可用，并刷新其状态")
        test_api_btn.clicked.connect(self._on_test_all_apis)
        api_row.addWidget(test_api_btn)
        api_row_widget = QWidget()
        api_row_widget.setLayout(api_row)
        self.api_group.add_wide_row("当前 API", "", api_row_widget)

        self.bulk_spin = QSpinBox()
        self.bulk_spin.setRange(1, 100)
        self.bulk_spin.valueChanged.connect(self._save)
        self.api_group.add_row("批量大小", "批量 API 每次提交的片段数", self.bulk_spin)

        self.multi_api_check = QCheckBox("多 API 轮询（失败自动切换下一个）")
        self.multi_api_check.toggled.connect(self._save)
        self.api_group.add_widget_row(self.multi_api_check)

        self.bulk_check = QCheckBox("使用批量 API（加速合成）")
        self.bulk_check.toggled.connect(self._save)
        self.api_group.add_widget_row(self.bulk_check)

        self.local_voice_picker = VoicePicker(self)
        self.api_group.add_widget_row(self.local_voice_picker)

        # 用 QStackedWidget 切换 edge/api 面板，避免窗口被撑大后不缩小
        self.config_stack = QStackedWidget()
        self.config_stack.addWidget(self.edge_group)   # index 0 = edge
        self.config_stack.addWidget(self.api_group)    # index 1 = api
        layout.addWidget(self.config_stack)

        # 缓存信息 + 清除按钮
        cache_row = QHBoxLayout()
        self.cache_info_label = QLabel("缓存：加载中...")
        self.cache_info_label.setProperty("role", "secondary")
        cache_row.addWidget(self.cache_info_label, 1)
        clear_cache_btn = QPushButton("清除缓存")
        clear_cache_btn.setToolTip("删除所有 TTS 音频缓存，下次合成将重新生成")
        clear_cache_btn.clicked.connect(self._on_clear_cache)
        cache_row.addWidget(clear_cache_btn)
        cache_widget = QWidget()
        cache_widget.setLayout(cache_row)
        layout.addWidget(cache_widget)
        layout.addStretch(1)

    def _load_values(self):
        cfg = self.config.tts_cfg
        # 阻断信号，防止加载过程中 _on_mode_changed→_save 覆盖配置
        widgets = [self.voice_combo, self.rate_combo,
                   self.volume_combo, self.threads_spin,
                   self.max_concurrent_spin, self.pipeline_tts_spin,
                   self.api_combo,
                   self.bulk_spin, self.multi_api_check, self.bulk_check]
        for w in widgets:
            w.blockSignals(True)
        try:
            # 模式（分段按钮，不触发 _save）
            mode = cfg.get("tts_mode", "edge")
            self.mode_segment.set_current_data(mode)
            # edge 参数
            voice = cfg.get("edge_voice", "zh-CN-XiaoxiaoNeural")
            idx = self.voice_combo.findData(voice)
            if idx < 0:
                self.voice_combo.addItem(f"不可用（请重新选择） - {voice}", voice)
                idx = self.voice_combo.count() - 1
            self.voice_combo.setCurrentIndex(idx)
            self.rate_combo.setCurrentText(cfg.get("edge_rate", "+0%"))
            self.volume_combo.setCurrentText(cfg.get("edge_volume", "+0%"))
            self.threads_spin.setValue(cfg.get("edge_threads", 5))
            self.max_concurrent_spin.setValue(cfg.get("edge_max_concurrent", 8))
            self.pipeline_tts_spin.setValue(cfg.get("pipeline_tts_workers", 2))
            # api 参数
            self._refresh_api_combo()
            self.bulk_spin.setValue(cfg.get("bulk_batch_size", 20))
            self.multi_api_check.setChecked(cfg.get("use_multi_api", False))
            self.bulk_check.setChecked(cfg.get("use_bulk_api", True))
            # 显示对应面板（不触发 _save）
            self.config_stack.setCurrentIndex(0 if mode == "edge" else 1)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._refresh_cache_info()
        self._update_voice_hint()

    def _update_voice_hint(self):
        """声音行下方的补充说明（与行内说明不重复：只给列表规模与限制）。"""
        if self.voice_combo.currentData() in EDGE_TTS_VOICES:
            self.voice_hint.setText(
                f"当前列表 {len(EDGE_TTS_VOICES)} 个音色；"
                f"Azure 声音不能直接用于此模式。")
        else:
            self.voice_hint.setText("保存的声音不在免费 Edge TTS 可用列表中，请重新选择声音后生成。")

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
        self.local_voice_picker.sync_from_config()

    def _on_mode_changed(self, mode):
        self.config_stack.setCurrentIndex(0 if mode == "edge" else 1)
        self._save()

    def _on_api_changed(self):
        idx = self.api_combo.currentIndex()
        self.config.tts_cfg["current_api_index"] = max(0, idx)
        self._save()
        self.local_voice_picker.sync_from_config()

    def _on_edit_apis(self):
        dialog = ApiConfigDialog(self.config, self)
        dialog.log_message.connect(self.log_message.emit)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_api_combo()
            self._save()

    def _on_test_all_apis(self):
        """测试所有 API 服务器，逐个刷新状态并写入日志。"""
        from PyQt6.QtWidgets import QMessageBox
        from ..steps.tts_utils import test_api_server
        apis = self.config.tts_cfg.get("api_configs", [])
        if not apis:
            QMessageBox.warning(self, "提示", "请先在“编辑API列表”中添加服务器。")
            return
        ok_count = 0
        for api in apis:
            url = api.get("url", "")
            ok, msg = test_api_server(url)
            api["status"] = "success" if ok else "failed"
            status_text = "✓ 可用" if ok else "✗ 不可用"
            self.log_message.emit(
                f"[API测试] {api.get('name', '?')} ({url}) -> {status_text} ({msg})"
            )
            if ok:
                ok_count += 1
        self.config.save()
        QMessageBox.information(
            self, "测试完成",
            f"共 {len(apis)} 个服务器，可用 {ok_count} 个",
        )

    def _save(self):
        cfg = self.config.tts_cfg
        cfg["tts_mode"] = self.mode_segment.current_data()
        cfg["edge_voice"] = self.voice_combo.currentData()
        self._update_voice_hint()
        cfg["edge_rate"] = self.rate_combo.currentText()
        cfg["edge_volume"] = self.volume_combo.currentText()
        cfg["edge_threads"] = self.threads_spin.value()
        cfg["edge_max_concurrent"] = self.max_concurrent_spin.value()
        cfg["pipeline_tts_workers"] = self.pipeline_tts_spin.value()
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
    """API 服务器列表编辑对话框（白底卡片 + 细线表格）。"""

    log_message = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("编辑 API 服务器列表")
        self.setMinimumWidth(620)
        self._apis = [dict(a) for a in config.tts_cfg.get("api_configs", [])]
        self._build_ui()

    def _build_ui(self):
        from PyQt6.QtWidgets import (
            QAbstractItemView, QDialogButtonBox, QHeaderView,
            QTableWidget, QTableWidgetItem, QVBoxLayout
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("API 服务器")
        title.setProperty("role", "section")
        layout.addWidget(title)
        hint = QLabel(
            "同一 URL 上换了模型时，在这里填不同的标签（如 GPT-SoVITS-女声A），\n"
            "用于区分语音缓存与语音输出目录；留空则与旧版本行为一致。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        # 表格
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["名称", "URL", "模型", "模型标签", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)
        self._refresh_table()

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        add_btn = QPushButton("+ 添加")
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)
        del_btn = QPushButton("- 删除")
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(del_btn)
        test_all_btn = QPushButton("测试全部")
        test_all_btn.setToolTip("逐个测试所有 API 服务器并刷新状态列")
        test_all_btn.clicked.connect(self._on_test_all)
        btn_row.addWidget(test_all_btn)
        btn_row.addStretch(1)
        ok_btn = QPushButton("确定")
        ok_btn.setProperty("kind", "primary")
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _refresh_table(self):
        from PyQt6.QtWidgets import QTableWidgetItem
        self.table.setRowCount(len(self._apis))
        for i, api in enumerate(self._apis):
            self.table.setItem(i, 0, QTableWidgetItem(api.get("name", "")))
            self.table.setItem(i, 1, QTableWidgetItem(api.get("url", "")))
            self.table.setItem(i, 2, QTableWidgetItem(api.get("model", "")))
            self.table.setItem(i, 3, QTableWidgetItem(api.get("model_tag", "")))
            self.table.setItem(i, 4, QTableWidgetItem(api.get("status", "unknown")))

    def _on_add(self):
        from PyQt6.QtWidgets import QTableWidgetItem
        self._apis.append({"name": "新服务器", "url": "http://", "model": "",
                           "model_tag": "", "status": "unknown"})
        self._refresh_table()
        self.table.selectRow(len(self._apis) - 1)

    def _on_delete(self):
        row = self.table.currentRow()
        if row >= 0 and row < len(self._apis):
            self._apis.pop(row)
            self._refresh_table()

    def _on_test_all(self):
        """逐个测试所有 API 服务器并更新状态列。"""
        from PyQt6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem
        from ..steps.tts_utils import test_api_server
        for i, api in enumerate(self._apis):
            url = api.get("url", "")
            self.table.setItem(i, 4, QTableWidgetItem("测试中..."))
            ok, msg = test_api_server(url)
            api["status"] = "success" if ok else "failed"
            self.table.setItem(i, 4, QTableWidgetItem(api["status"]))
            status_text = "✓ 可用" if ok else "✗ 不可用"
            self.log_message.emit(
                f"[API测试] {api.get('name', '?')} ({url}) -> {status_text} ({msg})"
            )
            QApplication.processEvents()
        QMessageBox.information(self, "测试完成", "所有 API 服务器测试完成，状态已更新。")

    def _on_accept(self):
        from PyQt6.QtWidgets import QMessageBox
        # 从表格收集数据
        for i in range(self.table.rowCount()):
            self._apis[i]["name"] = self.table.item(i, 0).text()
            self._apis[i]["url"] = self.table.item(i, 1).text()
            self._apis[i]["model"] = self.table.item(i, 2).text()
            self._apis[i]["model_tag"] = self.table.item(i, 3).text().strip()
            self._apis[i]["status"] = self.table.item(i, 4).text()
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
