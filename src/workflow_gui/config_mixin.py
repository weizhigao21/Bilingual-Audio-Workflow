# -*- coding: utf-8 -*-
"""主窗口 Mixin：全局配置与三个步骤的配置对话框。

按 UI_STYLE.md「设置页分组」：顶部使用下划线标签（常规/字幕提取/配音/
混音），激活标签使用 2 px 深色底边，不填充大色块；一组相关设置放在同一
白色容器内，行间细线分隔。三个步骤的配置面板内嵌其中，修改变更即自动保存。
"""
import copy
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QStackedWidget, QTabBar, QVBoxLayout, QWidget
)

from ..config import SetupDialog, WorkflowConfig
from ..ui_theme import WheelScrollGuard, repolish
from ..widgets import TTSConfigPanel, MixerConfigPanel, WhisperConfigPanel
from ..widgets.settings_group import PathRow, SettingsGroup


class GeneralSettingsPage(QWidget):
    """常规页：字幕提取程序路径与工作区目录（即 SetupDialog 的两项路径）。"""

    config_changed = pyqtSignal()

    def __init__(self, config: WorkflowConfig, parent=None):
        super().__init__(parent)
        self.config = config
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        group = SettingsGroup(
            "路径",
            "字幕提取程序需包含 infer.exe；工作区保存任务记录与中间产物。")
        self.whisper_row = PathRow("包含 infer.exe 的目录，可留空跳过字幕提取")
        self.whisper_row.setText(config.whisper_dir)
        self.whisper_row.text_changed.connect(lambda _t: self._save())
        group.add_wide_row("字幕提取程序", "", self.whisper_row)

        self.workspace_row = PathRow("任务输出根目录")
        self.workspace_row.setText(config.workspace_dir)
        self.workspace_row.text_changed.connect(lambda _t: self._save())
        group.add_wide_row("工作区目录", "", self.workspace_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "secondary")
        group.add_widget_row(self.status_label)
        layout.addWidget(group)

        # 性能（P2-5）：四个并发参数集中一处，实时预览有效并发
        perf_group = SettingsGroup(
            "性能",
            "并发参数集中在一处；实际并发取两者中较小值，推荐保持默认。")
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 20)
        self.threads_spin.setToolTip(
            "所有配音共用一个合成线程池（不随音频数叠加）。\n"
            "实际并发路数取本项与「配音全局并发上限」的较小值；\n"
            "建议两项设为同一个值（默认 8）。")
        self.threads_spin.valueChanged.connect(self._save_perf)
        perf_group.add_row("配音线程池", "所有音频共享的合成线程池", self.threads_spin)

        self.max_concurrent_spin = QSpinBox()
        self.max_concurrent_spin.setRange(1, 20)
        self.max_concurrent_spin.setToolTip(
            "同时真正发出的合成请求数上限，防止触发限流。\n"
            "实际并发路数取本项与「配音线程池」的较小值；\n"
            "建议两项设为同一个值（默认 8）。")
        self.max_concurrent_spin.valueChanged.connect(self._save_perf)
        perf_group.add_row("配音全局并发上限", "请求闸门，防止触发限流",
                           self.max_concurrent_spin)

        self.pipeline_tts_spin = QSpinBox()
        self.pipeline_tts_spin.setRange(1, 10)
        self.pipeline_tts_spin.setToolTip(
            "流水线模式下同时进行配音的音频数（任务级并行）。\n"
            "片段级并发由「配音全局并发上限」统一控制，不叠加。")
        self.pipeline_tts_spin.valueChanged.connect(self._save_perf)
        perf_group.add_row("流水线任务并行", "流水线模式下同时配音的音频数",
                           self.pipeline_tts_spin)

        self.mixer_threads_spin = QSpinBox()
        self.mixer_threads_spin.setRange(1, 32)
        self.mixer_threads_spin.setSuffix(" 线程")
        self.mixer_threads_spin.setToolTip("混音与声道检测的并行线程数。")
        self.mixer_threads_spin.valueChanged.connect(self._save_perf)
        perf_group.add_row("混音并行线程", "混音与声道检测的并行度",
                           self.mixer_threads_spin)

        self.perf_preview = QLabel("")
        self.perf_preview.setWordWrap(True)
        self.perf_preview.setProperty("role", "secondary")
        perf_group.add_widget_row(self.perf_preview)

        # 初值加载（阻断信号，避免 __init__ 期间触发保存）
        perf_spins = (self.threads_spin, self.max_concurrent_spin,
                      self.pipeline_tts_spin, self.mixer_threads_spin)
        for w in perf_spins:
            w.blockSignals(True)
        self.threads_spin.setValue(config.tts_cfg.get("edge_threads", 8))
        self.max_concurrent_spin.setValue(config.tts_cfg.get("edge_max_concurrent", 8))
        self.pipeline_tts_spin.setValue(config.tts_cfg.get("pipeline_tts_workers", 2))
        self.mixer_threads_spin.setValue(config.mixer_cfg.get("thread_count", 4))
        for w in perf_spins:
            w.blockSignals(False)
        self._update_perf_preview()

        self.perf_group = perf_group
        self.perf_check = QCheckBox("显示高级设置（并发参数）")
        self.perf_check.setToolTip("默认收起；需要调并发/线程数时再展开")
        self.perf_check.toggled.connect(perf_group.setVisible)
        layout.addWidget(self.perf_check)
        layout.addWidget(perf_group)
        perf_group.setVisible(False)
        layout.addStretch(1)
        self._validate()

    def _update_perf_preview(self):
        """实时预览有效并发 = min(线程池, 全局并发上限)。"""
        pool = self.threads_spin.value()
        gate = self.max_concurrent_spin.value()
        self.perf_preview.setText(
            f"有效配音并发 = {min(pool, gate)} 路"
            f"（配音线程池 {pool} 与全局并发上限 {gate} 取小）；"
            f"混音并行 {self.mixer_threads_spin.value()} 线程；"
            f"流水线任务并行 {self.pipeline_tts_spin.value()}。")

    def _save_perf(self):
        """性能区独立保存：不受路径校验门禁影响。"""
        self._update_perf_preview()
        self.config.tts_cfg["edge_threads"] = self.threads_spin.value()
        self.config.tts_cfg["edge_max_concurrent"] = self.max_concurrent_spin.value()
        self.config.tts_cfg["pipeline_tts_workers"] = self.pipeline_tts_spin.value()
        self.config.mixer_cfg["thread_count"] = self.mixer_threads_spin.value()
        try:
            self.config.save()
        except OSError:
            self.status_label.setText("保存失败：无法写入配置文件")
            self.status_label.setProperty("role", "error")
            repolish(self.status_label)
            return
        self.config_changed.emit()

    def _validate(self):
        whisper = self.whisper_row.text().strip()
        workspace = self.workspace_row.text().strip()
        errors = []
        if whisper and not WorkflowConfig._validate_subproject("whisper_dir", whisper):
            errors.append("字幕提取程序路径无效或缺少 infer.exe")
        if not workspace:
            errors.append("工作区目录不能为空")
        elif not os.path.isdir(workspace):
            try:
                os.makedirs(workspace, exist_ok=True)
            except OSError as exc:
                errors.append(f"工作区目录不可用：{exc}")
        if errors:
            self.status_label.setText("· ".join(errors))
            self.status_label.setProperty("role", "error")
        else:
            self.status_label.setText("路径有效。修改后自动保存。")
            self.status_label.setProperty("role", "success")
        repolish(self.status_label)
        return not errors

    def _save(self):
        if not self._validate():
            return
        self.config.config["subprojects"]["whisper_dir"] = self.whisper_row.text().strip()
        workspace = self.workspace_row.text().strip()
        if workspace and workspace != self.config.workspace_dir:
            self.config.config["workspace_dir"] = workspace
            os.makedirs(workspace, exist_ok=True)
        try:
            self.config.save()
        except OSError:
            self.status_label.setText("保存失败：无法写入配置文件")
            self.status_label.setProperty("role", "error")
            repolish(self.status_label)
            return
        self.config_changed.emit()


class SettingsDialog(QDialog):
    """统一设置弹窗：下划线标签切换 常规/字幕提取/配音/混音。"""

    def __init__(self, config: WorkflowConfig, parent=None, initial_tab: int = 0):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("设置 - 双语音声工作流")
        self.resize(760, 640)
        self.setMinimumSize(640, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(0)

        title = QLabel("设置")
        title.setProperty("role", "title")
        layout.addWidget(title)
        hint = QLabel("修改即时生效并自动保存；状态变化不会移动其他行。")
        hint.setProperty("role", "muted")
        layout.addWidget(hint)
        layout.addSpacing(10)

        self.tab_bar = QTabBar()
        for name in ("常规", "字幕提取", "配音", "混音"):
            self.tab_bar.addTab(name)
        self.tab_bar.setCurrentIndex(max(0, initial_tab))
        self.tab_bar.setDrawBase(False)   # 不画大色块底边，只保留选中项 2px 深色下边线
        self.tab_bar.setExpanding(False)
        layout.addWidget(self.tab_bar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._wrap_page(GeneralSettingsPage(config)))
        self.pages.addWidget(self._wrap_page(WhisperConfigPanel(config)))
        self.pages.addWidget(self._wrap_page(TTSConfigPanel(config)))
        self.pages.addWidget(self._wrap_page(MixerConfigPanel(config)))
        layout.addWidget(self.pages, 1)

        self.tab_bar.currentChanged.connect(self.pages.setCurrentIndex)
        # 构造期设置的 initial_tab 不会触发 currentChanged，需手动对齐堆叠页
        self.pages.setCurrentIndex(self.tab_bar.currentIndex())

        footer = QHBoxLayout()
        line = QWidget()
        line.setObjectName("headerRule")
        line.setFixedHeight(1)
        layout.addSpacing(8)
        layout.addWidget(line)
        layout.addSpacing(10)
        footer.addStretch(1)
        close_btn = QPushButton("完成")
        close_btn.setProperty("kind", "primary")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        # 数值框/下拉/滑条上的滚轮滚动页面，不误改参数
        WheelScrollGuard(self).guard(self)

    def _wrap_page(self, panel: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        return scroll


class ConfigDialogMixin:
    """配置对话框相关方法。"""

    def _on_config(self):
        self._open_settings(0)

    def _on_open_tts_config(self):
        """打开设置弹窗并定位到配音页；关闭后提示配置对已有配音的影响。"""
        self._open_settings(2)

    def _on_open_mixer_config(self):
        self._open_settings(3)

    def _on_open_whisper_config(self):
        self._open_settings(1)

    def _tts_affected_tasks(self):
        """配置变化后需要重新配音的任务（自定义配音/未完成配音的不算）。"""
        from ..steps.tts_profile import profile_matches
        affected = []
        for task in self.task_queue.tasks:
            if task.custom_mix_folder:
                continue
            if task.step2_status not in (STEP_DONE, STEP_SKIPPED):
                continue
            if task.step2_output and not profile_matches(
                    task.step2_output, self.config.tts_cfg):
                affected.append(task)
        return affected

    def _open_settings(self, initial_tab: int):
        """设置弹窗的统一入口：配音参数变了要先问，再重置已有配音。

        旧实现里 `_on_tts_config_changed` 有实现、有单测，却从未 `.connect`，
        改声音后任务仍显示"已完成"但成品是旧声音。这里显式接线，并把
        「保留已有配音」作为默认选项，避免误清空已做好的成果。
        """
        before_tts = copy.deepcopy(self.config.tts_cfg)
        dialog = SettingsDialog(self.config, self, initial_tab=initial_tab)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        tts_changed = before_tts != self.config.tts_cfg
        if tts_changed:
            affected = self._tts_affected_tasks()
            if affected:
                reply = QMessageBox.question(
                    self, "配音配置已变化",
                    f"有 {len(affected)} 个任务已生成配音，但当前声音/语速与"
                    "成品不一致。\n\n"
                    "「重新生成」：清空这些任务的配音与混音，下次执行时用新声音重做。\n"
                    "「保留已有配音」：成品保持原样，仅后续任务使用新配置。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self._on_tts_config_changed()
                    self._append_log(
                        f"[配音配置] 已标记 {len(affected)} 个任务需重新配音")
                else:
                    # 配置面板是即时保存的：不恢复就等于悄悄改了后续任务。
                    # 用户选择"保留"时把 tts 段整体回滚，保证配置与成品一致。
                    self.config.config["tts"] = before_tts
                    try:
                        self.config.save()
                    except OSError as exc:
                        self._append_log(f"[配置] 回滚保存失败: {exc}")
                    self._append_log("[配音配置] 已保留现有配音，配置未变更")
            else:
                self._on_tts_config_changed()
        self._on_mixer_config_changed()
        self._on_whisper_config_changed()
        if accepted:
            self._append_log("[配置] 已更新")

    def _on_tts_config_changed(self):
        """配音配置变化时记录日志。"""
        from ..steps.tts_profile import profile_matches
        from ..task_manager import STEP_DONE, STEP_SKIPPED, STEP_PENDING
        for task in self.task_queue.tasks:
            if task.custom_mix_folder or task.step2_status not in (STEP_DONE, STEP_SKIPPED):
                continue
            if task.step2_output and not profile_matches(task.step2_output, self.config.tts_cfg):
                task.set_step_status(2, STEP_PENDING)
                task.set_step_output(2, "")
                task.set_step_status(3, STEP_PENDING)
                task.set_step_output(3, "")
                task.force_remix = True
                self.task_queue.update_task(task)
        mode = self.config.tts_cfg.get("tts_mode", "edge")
        if mode == "edge":
            voice = self.config.tts_cfg.get("edge_voice", "")
            self._append_log(f"[配音配置] Edge模式, 声音={voice}")
        else:
            idx = self.config.tts_cfg.get("current_api_index", 0)
            apis = self.config.tts_cfg.get("api_configs", [])
            api_name = apis[idx].get("name", "?") if 0 <= idx < len(apis) else "?"
            self._append_log(f"[配音配置] API模式, 当前API={api_name}")

    def _on_mixer_config_changed(self):
        """混音配置变化时记录日志。"""
        cfg = self.config.mixer_cfg
        # 值也翻成用户看得懂的说法，键名/枚举值不进日志区
        volume_mode = {"off": "关闭", "fixed": "固定增益",
                       "auto": "自动匹配响度"}.get(
            cfg.get('auto_volume', 'fixed'), cfg.get('auto_volume', 'fixed'))
        self._append_log(
            f"[混音配置] 格式={cfg.get('output_format', 'mp4')}, "
            f"音量={cfg.get('volume_db', 0.0)}dB, "
            f"模式={volume_mode}"
        )

    def _on_whisper_config_changed(self):
        """字幕配置变化时记录日志。"""
        cfg = self.config.whisper_cfg
        device = {"auto": "自动", "cpu": "CPU", "cuda": "CUDA（GPU）"}.get(
            cfg.get('device', 'auto'), cfg.get('device', 'auto'))
        compute = {"auto": "自动", "int8": "int8（最快/省显存）",
                   "int8_float16": "int8_float16（混合精度）",
                   "float16": "float16（平衡）",
                   "float32": "float32（最精确）"}.get(
            cfg.get('compute_type', 'auto'), cfg.get('compute_type', 'auto'))
        self._append_log(
            f"[字幕配置] 设备={device}, "
            f"精度={compute}, "
            f"格式={cfg.get('sub_formats', 'lrc')}, "
            f"VAD阈值={cfg.get('vad_threshold', 0.5)}"
        )
