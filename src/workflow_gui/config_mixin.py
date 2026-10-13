# -*- coding: utf-8 -*-
"""主窗口 Mixin：全局配置与三个步骤的配置对话框。

按 UI_STYLE.md「设置页分组」：顶部使用下划线标签（常规/字幕提取/语音生成/
音频混音），激活标签使用 2 px 深色底边，不填充大色块；一组相关设置放在同一
白色容器内，行间细线分隔。三个步骤的配置面板内嵌其中，修改变更即自动保存。
"""
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget,
    QTabBar, QVBoxLayout, QWidget
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
        layout.addStretch(1)
        self._validate()

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
    """统一设置弹窗：下划线标签切换 常规/字幕提取/语音生成/音频混音。"""

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
        for name in ("常规", "字幕提取", "语音生成", "音频混音"):
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
        dialog = SettingsDialog(self.config, self, initial_tab=0)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._append_log("[配置] 已更新")

    def _on_open_tts_config(self):
        """打开设置弹窗并定位到语音生成页。配置变更自动保存。"""
        SettingsDialog(self.config, self, initial_tab=2).exec()

    def _on_open_mixer_config(self):
        """打开设置弹窗并定位到音频混音页。配置变更自动保存。"""
        SettingsDialog(self.config, self, initial_tab=3).exec()

    def _on_open_whisper_config(self):
        """打开设置弹窗并定位到字幕提取页。配置变更自动保存。"""
        SettingsDialog(self.config, self, initial_tab=1).exec()

    def _on_tts_config_changed(self):
        """TTS 配置变化时记录日志。"""
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
            self._append_log(f"[TTS配置] Edge模式, 声音={voice}")
        else:
            idx = self.config.tts_cfg.get("current_api_index", 0)
            apis = self.config.tts_cfg.get("api_configs", [])
            api_name = apis[idx].get("name", "?") if 0 <= idx < len(apis) else "?"
            self._append_log(f"[TTS配置] API模式, 当前API={api_name}")

    def _on_mixer_config_changed(self):
        """混音配置变化时记录日志。"""
        cfg = self.config.mixer_cfg
        self._append_log(
            f"[混音配置] 格式={cfg.get('output_format', 'mp4')}, "
            f"音量={cfg.get('volume_db', 0.0)}dB, "
            f"模式={cfg.get('auto_volume', 'fixed')}"
        )

    def _on_whisper_config_changed(self):
        """字幕配置变化时记录日志。"""
        cfg = self.config.whisper_cfg
        self._append_log(
            f"[字幕配置] 设备={cfg.get('device', 'auto')}, "
            f"精度={cfg.get('compute_type', 'auto')}, "
            f"格式={cfg.get('sub_formats', 'lrc')}, "
            f"VAD阈值={cfg.get('vad_threshold', 0.5)}"
        )
