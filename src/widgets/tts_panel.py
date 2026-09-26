# -*- coding: utf-8 -*-
"""TTS 配置面板与 API 服务器列表编辑对话框。"""
from PyQt6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QSpinBox, QCheckBox, QPushButton, QStackedWidget, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import pyqtSignal

from ..steps.tts_cache import AudioCache
from .common import EDGE_TTS_VOICES, RATE_PRESETS, VOLUME_PRESETS


class TTSConfigPanel(QGroupBox):
    """TTS 配置面板：模式选择、edge参数、api选择。

    修改配置后自动保存到 WorkflowConfig，并发 config_changed 信号。
    """
    config_changed = pyqtSignal()
    log_message = pyqtSignal(str)

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

        edge_layout.addWidget(QLabel("线程池数量:"), 3, 0)
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 20)
        self.threads_spin.setToolTip(
            "Edge 模式全局共享线程池的线程数（所有音频共用同一个池，\n"
            "不随音频数叠加）。同时可运行的任务数。\n"
            "实际发出的请求数另由「全局并发上限」闸门控制。"
        )
        self.threads_spin.valueChanged.connect(self._save)
        edge_layout.addWidget(self.threads_spin, 3, 1)

        edge_layout.addWidget(QLabel("全局并发上限:"), 4, 0)
        self.max_concurrent_spin = QSpinBox()
        self.max_concurrent_spin.setRange(1, 20)
        self.max_concurrent_spin.setToolTip(
            "Edge 模式真正发出的并发请求数上限（请求闸门）。\n"
            "超过线程池数量的任务会在闸门排队。过高会触发微软服务限流\n"
            "（Cannot connect to ...bing.com:443），建议 5-8"
        )
        self.max_concurrent_spin.valueChanged.connect(self._save)
        edge_layout.addWidget(self.max_concurrent_spin, 4, 1)

        edge_layout.addWidget(QLabel("流水线语音并行:"), 5, 0)
        self.pipeline_tts_spin = QSpinBox()
        self.pipeline_tts_spin.setRange(1, 10)
        self.pipeline_tts_spin.setToolTip(
            "流水线模式下同时进行语音生成的音频数（任务级并行）。\n"
            "设 1 = 按音频逐个生成语音；2 = 最多 2 个音频同时生成。\n"
            "片段级并发由「全局并发上限」统一控制（共享线程池，不叠加）。"
        )
        self.pipeline_tts_spin.valueChanged.connect(self._save)
        edge_layout.addWidget(self.pipeline_tts_spin, 5, 1)

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

        test_api_btn = QPushButton("测试全部")
        test_api_btn.setToolTip("测试所有 API 服务器是否可用，并刷新其状态")
        test_api_btn.clicked.connect(self._on_test_all_apis)
        api_layout.addWidget(test_api_btn, 0, 3)

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
                   self.volume_combo, self.threads_spin,
                   self.max_concurrent_spin, self.pipeline_tts_spin,
                   self.api_combo,
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
            self.max_concurrent_spin.setValue(cfg.get("edge_max_concurrent", 8))
            self.pipeline_tts_spin.setValue(cfg.get("pipeline_tts_workers", 2))
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
        cfg["tts_mode"] = self.mode_combo.currentData()
        cfg["edge_voice"] = self.voice_combo.currentData()
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
    """API 服务器列表编辑对话框。"""

    log_message = pyqtSignal(str)

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
        test_all_btn = QPushButton("测试全部")
        test_all_btn.setToolTip("逐个测试所有 API 服务器并刷新状态列")
        test_all_btn.clicked.connect(self._on_test_all)
        btn_row.addWidget(test_all_btn)
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

    def _on_test_all(self):
        """逐个测试所有 API 服务器并更新状态列。"""
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from ..steps.tts_utils import test_api_server
        for i, api in enumerate(self._apis):
            url = api.get("url", "")
            self.table.setItem(i, 3, QTableWidgetItem("测试中..."))
            ok, msg = test_api_server(url)
            api["status"] = "success" if ok else "failed"
            self.table.setItem(i, 3, QTableWidgetItem(api["status"]))
            status_text = "✓ 可用" if ok else "✗ 不可用"
            self.log_message.emit(
                f"[API测试] {api.get('name', '?')} ({url}) -> {status_text} ({msg})"
            )
            QApplication.processEvents()
        QMessageBox.information(self, "测试完成", "所有 API 服务器测试完成，状态已更新。")

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
