# -*- coding: utf-8 -*-
"""声笺 UI 风格守卫测试（对应 UI_STYLE.md）。

覆盖：
- 色板 / 字体 / 布局常量不被随意改动
- QSS 关键选择器存在（侧栏、中央、底栏、主按钮、状态药丸、设置卡、分段、下划线页签）
- apply_theme 可离屏执行（防 QFont.StyleHint 之类的 API 回归直接崩启动）
- 主窗口三区骨架与规范宽度（208 侧栏 / 72 底栏；检查器已按反馈移除）
- 侧栏任务树结构：Qt 缩进语义、导引线/箭头像素、缩进区点击展开、整行选中背景
- 工具栏单行按钮序列（旧版形态）+ 条目级按钮可用性联动
- 分段按钮互斥、设置弹窗四个页签与定位；步骤卡配置入口（字幕/TTS/混音）
- 滚轮守卫把滚轮事件转成页面滚动（交互稳定性第 3 条）
- 输出路径 RichText 可点击链接 + HTML 转义 + 单行省略 + tooltip 完整内容
- 勾选图标资源存在（.spec datas 遗漏会导致运行时 QSS 引用为空）
- 打包后的资源目录解析（frozen 下 datas 在 _MEIPASS/_internal，不是 exe 同级）
"""
import ast
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, QModelIndex, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QScrollBar, QStyleOptionViewItem, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)
from PyQt6.QtTest import QTest

from src import ui_theme
from src.task_manager import (
    STEP_DONE, STEP_FAILED, STEP_PENDING, STEP_RUNNING, STEP_SKIPPED, TaskInfo,
)
from src.ui_theme import (
    FONT_AUX, FONT_BODY, FONT_FAMILY, FONT_SECTION,
    FONT_TITLE, LIST_ROW_HEIGHT, LOG_COLORS, PALETTE, PROGRESS_HEIGHT,
    RADIUS_CONTROL, RADIUS_DIALOG, RADIUS_SETTINGS_CARD, SIDEBAR_WIDTH,
    StyledWidget, WheelScrollGuard, apply_theme, build_stylesheet,
    configure_font, repolish, resource_url,
)
from src.widgets.common import ElidedLabel
from src.widgets.settings_group import SegmentedControl, SettingsGroup
from src.widgets.step_panel import StepPanel
from src.widgets.task_list import (
    _CHEVRON_SIZE, _GUIDE_COLOR, _SELECTED_BG, TaskListWidget, TaskRowDelegate,
)
from src.widgets.toast import ToastPopup


def _make_config(root: Path) -> SimpleNamespace:
    """测试用最小配置替身（与 test_folder_import 同风格）。"""
    return SimpleNamespace(
        workspace_dir=str(root / "tasks"),
        whisper_dir="",
        tts_cfg={},
        mixer_cfg={"output_format": "mp3", "volume_db": 0.0,
                   "auto_volume": "fixed"},
        whisper_cfg={},
        config={"subprojects": {}, "workspace_dir": str(root / "tasks")},
        needs_setup=lambda: False,
    )


class ThemeConstantTests(unittest.TestCase):
    """常量与 UI_STYLE.md 的约定一一对应，改动必须显式 Review。"""

    def test_palette_matches_style_spec(self):
        self.assertEqual(PALETTE["bg"], "#ffffff")
        self.assertEqual(PALETTE["canvas"], "#ffffff")
        self.assertEqual(PALETTE["sidebar"], "#fbfbfc")
        self.assertEqual(PALETTE["text"], "#18181a")
        self.assertEqual(PALETTE["separator"], "#ececf0")
        self.assertEqual(PALETTE["border"], "#d4d4da")
        self.assertEqual(PALETTE["accent"], "#e0651c")
        self.assertEqual(PALETTE["focus"], "#d9ac8c")

    def test_region_borders_are_visible_on_white(self):
        """分区靠"看得见的边界线"，边界线必须与白底拉开对比。

        分区策略变过一次：先按明度分层（灰底 + 白卡），用户反馈"太丑"；
        改为纯白表面 + 边界线。但线必须真的看得见——旧的 #eeeeee 与白底
        只差 17 灰阶，1x 屏上等于没有边界，那正是"区域区分不明显"的根源。
        """
        def _lum(hex_color: str) -> float:
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        white = _lum(PALETTE["bg"])
        self.assertEqual(PALETTE["canvas"], PALETTE["bg"],
                         "线框分区下工作区底应与卡片同为白")
        self.assertGreaterEqual(
            white - _lum(PALETTE["border"]), 25,
            "区域边界线太淡，白底上看不出分界（旧 #eeeeee 只差 17）")
        self.assertGreaterEqual(
            white - _lum(PALETTE["separator"]), 8,
            "二级分隔线太淡，卡片内行/项之间分不开")
        # 内凹底（分段容器、状态药丸）必须与白前景拉开，否则"选中项"看不出来
        self.assertGreaterEqual(
            white - _lum(PALETTE["inset"]), 10,
            "内凹底色太接近白，分段按钮的选中态会消失")

    def test_typography_and_layout_sizes(self):
        self.assertEqual((FONT_TITLE, FONT_SECTION, FONT_BODY, FONT_AUX),
                         (23, 14, 13, 12))
        self.assertEqual(FONT_FAMILY, "Microsoft YaHei")
        self.assertEqual(SIDEBAR_WIDTH, 208)
        self.assertEqual(LIST_ROW_HEIGHT, 50)
        self.assertEqual(PROGRESS_HEIGHT, 3)
        self.assertEqual((RADIUS_CONTROL, RADIUS_SETTINGS_CARD, RADIUS_DIALOG),
                         (6, 13, 14))

    def test_stylesheet_contains_required_selectors(self):
        qss = build_stylesheet()
        for fragment in (
            'QWidget#sidebar', 'QWidget#centerPane',
            'QPushButton[kind="primary"]',
            'QLabel[pill="true"]', 'QWidget#settingsGroup',
            'QWidget#segmented', 'QTabBar::tab', 'QLabel#stepBadge',
            'QWidget#toast',
            # 侧栏品牌层级、统一选择窗选项按钮、日志卡、工具栏分隔线
            'QLabel[role="brand"]', 'QPushButton[kind="option"]',
            'QTextEdit#logView', 'QWidget#toolbarDivider',
            # 页头分隔线 + 步骤卡状态色条
            'QWidget#headerRule', 'QFrame[card="true"][state="running"]',
        ):
            self.assertIn(fragment, qss, f"QSS 缺少选择器 {fragment}")
        # 侧栏品牌必须大于正文（层级：17px vs 13px）
        self.assertIn('QLabel[role="brand"] { font-size: 17px', qss)
        # 检查器已移除：QSS 不应残留 #inspector 规则
        self.assertNotIn('QWidget#inspector', qss)
        # 底栏已移除：QSS 不应残留 #bottomBar / clipTitle 规则
        self.assertNotIn('QWidget#bottomBar', qss)
        self.assertNotIn('clipTitle', qss)
        # 进度条 3px 细条；主按钮用深色 #18181a（只认主按钮规则，避免被
        # stepBadge 等同色规则蹭过）
        self.assertIn(f"height: {PROGRESS_HEIGHT}px", qss)
        self.assertIn(
            'QPushButton[kind="primary"] { background: '
            f"{PALETTE['text']}; color: #ffffff",
            qss,
        )
        # 强调色只出现在进度/焦点/状态相关规则里
        self.assertIn(PALETTE["accent"], qss)

    def test_check_icon_resource_exists(self):
        """.spec 的 datas 必须带上 resources/ui，否则勾选图标静默丢失。"""
        url = resource_url("check.png")
        self.assertTrue(url, "check.png 缺失：打包 datas 需要包含 resources/ui")
        self.assertTrue(Path(url).is_file(), f"资源文件不存在：{url}")
        # Qt 的 QSS url() 不认 file:/// 前缀——加了图片会静默加载失败，
        # 勾选框退化成纯色块（见 SquareIndicatorTests 的像素守卫）
        self.assertFalse(url.startswith("file:///"),
                         "QSS 资源必须给纯路径，不能带 file:/// 前缀")


class ThemeApplicationTests(unittest.TestCase):
    """apply_theme 必须能在离屏环境跑完（启动路径的第一道关卡）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_theme_sets_font_and_stylesheet(self):
        apply_theme(self.app)
        self.assertEqual(self.app.font().family(), FONT_FAMILY)
        self.assertIn('QWidget#sidebar', self.app.styleSheet())
        configure_font(self.app)  # 单独调用也不应抛异常

    def test_repolish_reevaluates_dynamic_properties(self):
        label = QLabel("x")
        self.addCleanup(label.deleteLater)
        label.setProperty("role", "error")
        repolish(label)
        self.assertEqual(label.property("role"), "error")


class ShellLayoutTests(unittest.TestCase):
    """主窗口两区骨架：侧栏 + 中央（检查器/底栏均已按用户反馈移除）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        from src.workflow_gui import WorkflowMainWindow
        self.window = WorkflowMainWindow(_make_config(self.root))
        self.addCleanup(self.window.deleteLater)
        self.window.resize(1380, 900)

    def _region(self, name):
        found = self.window.findChildren(QWidget, name)
        self.assertEqual(len(found), 1, f"区域 {name} 应唯一，实际 {len(found)}")
        return found[0]

    def test_shell_regions_with_spec_widths(self):
        # 宽度按 UI_STYLE.md 字面值断言：同时捕获"常量被改"与
        # "控件没按常量布局"两类回归
        self.assertEqual(self._region("sidebar").width(), 208)
        self.assertTrue(self._region("centerPane").width() > 0)

    def test_bottom_bar_removed(self):
        """底栏整体移除：无区域、无构建方法、无底栏控件、无 QSS 规则。"""
        self.assertEqual(self.window.findChildren(QWidget, "bottomBar"), [])
        self.assertFalse(hasattr(self.window, "_build_bottom_bar"))
        for name in ("bottom_progress", "bottom_percent", "status_bar_label",
                     "global_stop_btn", "current_entry_btn"):
            self.assertFalse(hasattr(self.window, name), f"底栏控件 {name} 未清理")
        self.assertNotIn("QWidget#bottomBar", build_stylesheet())
        # 中央区必须一直延伸到窗口底部（底栏腾出的高度不能变成空白）
        central = self.window.centralWidget()
        self.assertLessEqual(
            abs(self._region("centerPane").height() - central.height()), 2)

    def test_inspector_region_removed(self):
        """检查器面板整体移除：无区域、无构建方法、无属性卡标签。"""
        self.assertEqual(self.window.findChildren(QWidget, "inspector"), [])
        self.assertFalse(hasattr(self.window, "_build_inspector"))
        self.assertFalse(hasattr(self.window, "_build_property_card"))
        self.assertFalse(hasattr(self.window, "_build_params_card"))
        self.assertFalse(hasattr(self.window, "inspector_labels"))
        self.assertFalse(hasattr(self.window, "_refresh_param_summaries"))

    def test_each_step_panel_has_exactly_one_primary_button(self):
        from PyQt6.QtWidgets import QPushButton
        panels = self.window.step_panels
        self.assertEqual(sorted(panels), [1, 2, 3])
        for step, panel in panels.items():
            buttons = [b for b in panel.findChildren(QPushButton)
                       if b.property("kind") == "primary"]
            self.assertEqual(len(buttons), 1, f"步骤 {step} 主按钮应唯一")

    def test_step_panel_output_elides_with_full_tooltip(self):
        panel = self.window.step_panels[1]
        long_path = "C:/" + "/".join(f"folder{index}" for index in range(30)) \
                    + "/subtitle.lrc"
        panel.set_output(long_path, "字幕")
        text = panel.output_label.text()
        self.assertIn("…", text)
        self.assertRegex(text, r"subtitle\.lrc</a>$")
        self.assertIn(long_path, panel.output_label.toolTip())
        panel.set_output("")
        # 输出行不再隐藏：显隐会让卡片高矮差一个行高，切换任务时中央区
        # 整列跳动（截图反馈"UI 有跳动"）。改为占位文案，高度恒定。
        self.assertFalse(panel.output_label.isHidden())
        self.assertEqual(panel.output_label.text(), "字幕：尚未生成")
        self.assertEqual(panel.output_label.toolTip(), "")

    def test_step_card_height_is_stable_with_and_without_output(self):
        """有/无输出时步骤卡高度必须一致（跳动回归守卫）。"""
        self.window.show()
        panel = self.window.step_panels[1]
        for _ in range(3):
            self.app.processEvents()
        baseline = panel.height()
        long_path = "C:/" + "/".join(f"folder{index}" for index in range(30)) \
                    + "/subtitle.lrc"
        panel.set_output(long_path, "字幕")
        for _ in range(3):
            self.app.processEvents()
        self.assertEqual(panel.height(), baseline,
                         "出现输出路径后卡片变高，切换任务时中央区会跳动")
        panel.set_output("")
        for _ in range(3):
            self.app.processEvents()
        self.assertEqual(panel.height(), baseline,
                         "清空输出后卡片变矮，切换任务时中央区会跳动")


class ToolbarEntryTests(unittest.TestCase):
    """工具栏单行按钮（旧版形态）与条目级操作可用性。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        from src.workflow_gui import WorkflowMainWindow
        self.window = WorkflowMainWindow(_make_config(self.root))
        self.addCleanup(self.window.deleteLater)
        self.window.resize(1380, 900)

    def test_toolbar_button_sequence(self):
        buttons = self.window._build_toolbar_buttons()
        texts = [b.text() for b in buttons if isinstance(b, QPushButton)]
        # 导入类入口全部离开工具栏：添加媒体并入侧栏「＋ 添加素材」，
        # 添加字幕/配音下沉到对应步骤卡
        self.assertEqual(
            texts, ["打开工作区", "配置", "批量执行", "停止批量"])
        primary = [b for b in buttons
                   if isinstance(b, QPushButton)
                   and b.property("kind") == "primary"]
        self.assertEqual(len(primary), 1, "工具栏主按钮应只有「批量执行」")

    def test_add_media_entry_lives_in_sidebar_above_tree(self):
        """合并后的添加入口在侧栏、位于程序名之下、任务树之上。"""
        from PyQt6.QtWidgets import QPushButton
        btn = self.window.add_media_btn
        self.assertEqual(btn.property("kind"), "primary")
        sidebar = self._sidebar()
        self.assertIn(btn, sidebar.findChildren(QPushButton))
        # 垂直位置：品牌标签之下、任务树之上
        layout = sidebar.layout()
        order = [layout.itemAt(i).widget() for i in range(layout.count())]
        brand_index = next(i for i, w in enumerate(order) if w is not None
                           and w.text() == "双语音声工作流")
        self_index = order.index(btn)
        tree_index = order.index(self.window.task_list)
        self.assertLess(brand_index, self_index, "添加按钮应在程序名之下")
        self.assertLess(self_index, tree_index, "添加按钮应在任务树之上")

    def test_add_media_dialog_offers_files_and_folder(self):
        from src.workflow_gui.window import AddMediaDialog
        dialog = AddMediaDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.result_choice, 0, "未选择时结果应为 0（取消）")
        self.assertEqual(dialog.files_btn.property("kind"), "option")
        self.assertEqual(dialog.folder_btn.property("kind"), "option")
        dialog.files_btn.click()
        self.assertEqual(dialog.result_choice, AddMediaDialog.FILES)
        dialog.folder_btn.click()
        self.assertEqual(dialog.result_choice, AddMediaDialog.FOLDER)
        # 取值不得与 QDialog.DialogCode 撞号（Accepted=1 / Rejected=0）
        self.assertNotIn(AddMediaDialog.FILES, (0, 1))
        self.assertNotIn(AddMediaDialog.FOLDER, (0, 1))

    def test_add_buttons_live_in_matching_step_cards(self):
        """添加字幕 → 字幕提取卡；添加配音 → 语音生成卡。"""
        from PyQt6.QtWidgets import QPushButton
        step1_texts = [b.text() for b in
                       self.window.step_panels[1].findChildren(QPushButton)]
        step2_texts = [b.text() for b in
                       self.window.step_panels[2].findChildren(QPushButton)]
        step3_texts = [b.text() for b in
                       self.window.step_panels[3].findChildren(QPushButton)]
        self.assertIn("＋ 添加字幕", step1_texts)
        self.assertIn("＋ 添加配音", step2_texts)
        self.assertNotIn("＋ 添加字幕", step2_texts + step3_texts)
        self.assertNotIn("＋ 添加配音", step1_texts + step3_texts)
        # 工具栏里不得再出现这两个入口
        toolbar_texts = [b.text() for b in
                         self.window._build_toolbar_buttons()
                         if isinstance(b, QPushButton)]
        self.assertNotIn("+ 添加字幕", toolbar_texts)
        self.assertNotIn("+ 添加配音", toolbar_texts)

    def _sidebar(self):
        found = self.window.findChildren(QWidget, "sidebar")
        self.assertEqual(len(found), 1)
        return found[0]

    def test_entry_buttons_follow_current_task(self):
        subtitle_btn = self.window.add_subtitle_btn
        mix_btn = self.window.add_mix_btn
        # 无选中任务：禁用
        self.window.task_queue.set_current(None)
        self.assertFalse(subtitle_btn.isEnabled())
        self.assertFalse(mix_btn.isEnabled())
        # 选中具体任务：启用
        task = self.window.task_queue.add_task(str(self.root / "demo.mp4"))
        self.window.task_queue.set_current(task.task_id)
        self.assertTrue(subtitle_btn.isEnabled())
        self.assertTrue(mix_btn.isEnabled())
        # 取消选中：重新禁用
        self.window.task_queue.set_current(None)
        self.assertFalse(subtitle_btn.isEnabled())
        self.assertFalse(mix_btn.isEnabled())

    def test_step_cards_have_config_entries(self):
        """设置入口与旧版一致：步骤卡内「字幕/TTS/混音 配置…」按钮。"""
        expected = {1: "字幕配置…", 2: "TTS 配置…", 3: "混音配置…"}
        for step, text in expected.items():
            panel = self.window.step_panels[step]
            texts = [b.text() for b in panel.findChildren(QPushButton)]
            self.assertIn(text, texts, f"步骤 {step} 缺少配置入口 {text}")
        for name in ("_on_open_whisper_config", "_on_open_tts_config",
                     "_on_open_mixer_config", "_on_config"):
            self.assertTrue(callable(getattr(self.window, name, None)),
                            f"缺少设置入口方法 {name}")


class StepPanelOutputTests(unittest.TestCase):
    """输出路径必须是可点击链接（RichText），且路径字符需 HTML 转义。

    PlainText 会把 <a href> 按纯文本裸显（截图事故：输出行直接露出
    file:/// 原始 HTML），textFormat 回归到这里拦。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = StepPanel(1, "字幕提取")
        self.addCleanup(self.panel.deleteLater)

    def test_output_label_renders_rich_text_link(self):
        self.panel.set_output("C:/work/tasks/subtitle.lrc", "字幕")
        label = self.panel.output_label
        self.assertEqual(label.textFormat(), Qt.TextFormat.RichText)
        self.assertIn('<a href="file:///C:/work/tasks/subtitle.lrc">',
                      label.text())
        self.assertTrue(label.openExternalLinks())

    def test_special_characters_are_escaped(self):
        path = "C:/a&b/<c>/d>e/sub&title.lrc"
        self.panel.set_output(path, "字幕")
        text = self.panel.output_label.text()
        self.assertIn("&amp;", text)
        self.assertIn("&lt;c&gt;", text)
        self.assertNotIn("<c>", text)
        self.assertIn("file:///C:/a&amp;b/&lt;c&gt;/d&gt;e/sub&amp;title.lrc",
                      text)
        # tooltip 保留原始路径（未转义）
        self.assertIn(path, self.panel.output_label.toolTip())


class TaskTreeStructureTests(unittest.TestCase):
    """侧栏任务树结构：Qt 缩进语义、导引线/箭头、缩进区点击、整行选中。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree = TaskListWidget()
        self.addCleanup(self.tree.deleteLater)
        self.tree.resize(208, 400)
        self.group = self._add("group", None, "双语-RJ01648761")
        self.folder = self._add("folder", self.group, "wav")
        self.task = self._add("task", self.folder, "01 试听.mp3")
        # 组下再挂一个目录：wav 因此不是末子，它这一支的祖先列竖线在本行
        # 仍需延续（Qt 的 State_Sibling 语义；末子之后必须收线）
        self.folder2 = self._add("folder", self.group, "srt")
        self.loose = self._add("task", None, "散任务.mp3")
        self.tree.expandAll()

    def _add(self, kind, parent, name):
        item = QTreeWidgetItem(parent if parent is not None else self.tree)
        item.setData(0, self.tree.ROLE_KIND, kind)
        item.setData(0, self.tree.ROLE_ID, name)
        item.setData(0, self.tree.ROLE_NAME, name)
        item.setData(0, self.tree.ROLE_STATUS, "pending")
        item.setData(0, self.tree.ROLE_SUBTITLE, "待处理")
        item.setData(0, self.tree.ROLE_PROGRESS, 0)
        item.setText(0, name)
        return item

    def _pixel(self, image, x, y):
        """viewport 坐标 → 抓图像素（兼容帧偏移）。"""
        offset = self.tree.viewport().mapTo(self.tree, QPoint(0, 0))
        return image.pixel(offset.x() + x, offset.y() + y)

    def test_content_left_follows_qt_indentation(self):
        # Qt 语义：option.rect.left() 已含 indentation×(depth+1) 缩进，
        # 委托直接用它作内容起点；depth  helper 必须与层级一致
        indent = self.tree.indentation()
        self.assertEqual(indent, 14)
        for item, depth in ((self.group, 0), (self.folder, 1),
                            (self.task, 2), (self.loose, 0)):
            index = self.tree.indexFromItem(item)
            rect = self.tree.visualRect(index)
            self.assertEqual(rect.left(), indent * (depth + 1),
                             f"{item.text(0)} 的内容起点不符合 Qt 缩进语义")
            self.assertEqual(TaskRowDelegate._depth(index), depth)

    def test_paint_uses_qt_rect_left_without_extra_indent(self):
        """源码级守卫：委托不得在 rect.left() 上再加缩进（双重缩进回归）。"""
        source = (Path(__file__).resolve().parents[1] / "src" / "widgets"
                  / "task_list.py").read_text(encoding="utf-8")
        self.assertIn("left = rect.left()", source,
                      "委托内容起点应直接取 rect.left()")
        self.assertNotIn("rect.left() + indent", source)
        self.assertNotIn("rect.left() + 8", source)

    def test_draw_branches_suppressed_and_structure_drawn(self):
        """原生分支绘制置空；导引线/箭头由委托 _draw_structure 绘制。"""
        source = (Path(__file__).resolve().parents[1] / "src" / "widgets"
                  / "task_list.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        branches = [n for n in ast.walk(module)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "drawBranches"]
        self.assertEqual(len(branches), 1, "TaskListWidget 应置空 drawBranches")
        body = branches[0].body
        self.assertTrue(
            all(isinstance(stmt, ast.Return)
                or (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant))
                for stmt in body),
            "drawBranches 函数体应只有文档字符串与 return")
        self.assertIn("_draw_structure", source)

    def test_guide_lines_drawn_at_ancestor_columns(self):
        image = self.tree.grab().toImage()
        rect = self.tree.visualRect(self.tree.indexFromItem(self.task))
        center_y = rect.center().y()
        guide = _GUIDE_COLOR.rgb()
        # depth=2 任务行：祖先列 x=7 竖线贯穿，x=21 肘点
        self.assertEqual(self._pixel(image, 7, center_y), guide)
        self.assertEqual(self._pixel(image, 21, center_y), guide)
        # 自身列 x=35：肘线横向穿过（一行高），但不得有竖线
        self.assertEqual(self._pixel(image, 35, center_y), guide)
        self.assertNotEqual(self._pixel(image, 35, rect.top() + 4), guide,
                            "自身列不应出现竖线（只该有横向肘线）")

    def test_chevron_drawn_for_expandable_rows(self):
        image = self.tree.grab().toImage()
        rect = self.tree.visualRect(self.tree.indexFromItem(self.folder))
        center = rect.center()
        # 目录行（depth=1）自身分支列中心 x=21 处有展开箭头
        self.assertEqual(self._pixel(image, 21, center.y()),
                         QColor("#a2a2a8").rgb())

    def test_selection_covers_indent_area(self):
        """选中背景必须铺满整行（含缩进导引区），不允许原生高亮色残留。"""
        self.tree.setCurrentItem(self.task)
        image = self.tree.grab().toImage()
        rect = self.tree.visualRect(self.tree.indexFromItem(self.task))
        center_y = rect.center().y()
        selected = _SELECTED_BG.rgb()
        # x=2：缩进导引区（分支列左侧）也应是选中底色
        self.assertEqual(self._pixel(image, 2, center_y), selected)
        # x=7：祖先列中心的导引线画在选中背景之上，仍然可见
        self.assertEqual(self._pixel(image, 7, center_y),
                         _GUIDE_COLOR.rgb())

    def test_branch_column_click_toggles_expand(self):
        """交互契约：点击条目自身分支列切换展开/收起，且不改变选中项。

        分支命中由 Qt 原生提供（drawBranches 只置空绘制、不动命中），
        此守卫锁定用户可见契约：若有人改动展开策略或吞掉分支点击即失败。
        """
        self.tree.show()
        index = self.tree.indexFromItem(self.group)
        self.assertTrue(self.tree.isExpanded(index))
        rect = self.tree.visualRect(index)
        pos = QPoint(7, rect.center().y())   # group 自身分支列中心
        current = self.tree.currentItem()
        QTest.mouseClick(self.tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=pos)
        self.assertFalse(self.tree.isExpanded(index),
                         "分支列点击应收起分组")
        self.assertEqual(self.tree.currentItem(), current,
                         "分支列点击不应改变选中项")
        QTest.mouseClick(self.tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=pos)
        self.assertTrue(self.tree.isExpanded(index))

    def test_content_click_selects_without_toggle(self):
        self.tree.show()
        index = self.tree.indexFromItem(self.group)
        rect = self.tree.visualRect(index)
        QTest.mouseClick(self.tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(80, rect.center().y()))
        self.assertTrue(self.tree.isExpanded(index),
                        "内容区点击不应切换展开")
        self.assertEqual(self.tree.currentItem(), self.group)

    def test_row_height_matches_spec(self):
        delegate = TaskRowDelegate(self.tree)
        self.assertEqual(delegate.sizeHint(QStyleOptionViewItem(),
                                           QModelIndex()).height(), 50)


class SegmentedControlTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_options_are_exclusive_and_queryable(self):
        control = SegmentedControl([("流水线", "pipeline"), ("按步骤", "by_step")])
        self.addCleanup(control.deleteLater)
        self.assertEqual(control.current_data(), "pipeline")
        self.assertTrue(control.set_current_data("by_step"))
        self.assertEqual(control.current_data(), "by_step")
        checked = [b for b, _ in control._buttons if b.isChecked()]
        self.assertEqual(len(checked), 1)
        self.assertFalse(control.set_current_data("nope"))

    def test_current_changed_emits_data(self):
        control = SegmentedControl([("A", "a"), ("B", "b")])
        self.addCleanup(control.deleteLater)
        seen = []
        control.current_changed.connect(seen.append)
        control._buttons[1][0].click()   # 真实点击路径才发信号
        self.assertEqual(seen, ["b"])
        self.assertEqual(control.current_data(), "b")


class SettingsDialogTabTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def _dialog(self, initial_tab=0):
        from src.workflow_gui.config_mixin import SettingsDialog
        dialog = SettingsDialog(_make_config(self.root), initial_tab=initial_tab)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_four_tabs_and_initial_positioning(self):
        dialog = self._dialog(0)
        self.assertEqual(
            [dialog.tab_bar.tabText(i) for i in range(dialog.tab_bar.count())],
            ["常规", "字幕提取", "语音生成", "音频混音"])
        self.assertFalse(dialog.tab_bar.drawBase())
        self.assertEqual(dialog.tab_bar.currentIndex(), 0)
        self.assertEqual(dialog.pages.currentIndex(), 0)

        for tab in (1, 2, 3):
            dialog = self._dialog(tab)
            self.assertEqual(dialog.tab_bar.currentIndex(), tab)
            self.assertEqual(dialog.pages.currentIndex(), tab)

    def test_tab_switch_moves_stacked_page(self):
        dialog = self._dialog(0)
        dialog.tab_bar.setCurrentIndex(3)
        self.assertEqual(dialog.pages.currentIndex(), 3)


class WheelScrollGuardTests(unittest.TestCase):
    """滚轮在数值框/下拉上应滚动页面，而不是改值（交互稳定性第 3 条）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        outer = QWidget()
        self.addCleanup(outer.deleteLater)
        layout = QVBoxLayout(outer)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setMinimumSize(200, 2000)
        content_layout = QVBoxLayout(content)
        combo = QComboBox()
        combo.addItems(["a", "b", "c"])
        content_layout.addWidget(combo)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        outer.resize(300, 200)
        outer.show()
        return scroll, combo

    def test_guard_installs_on_value_widgets(self):
        outer = QWidget()
        self.addCleanup(outer.deleteLater)
        combo = QComboBox()
        combo.setParent(outer)
        installed = WheelScrollGuard(outer).guard(outer)
        self.assertGreaterEqual(installed, 1)

    def test_wheel_over_combo_scrolls_page_not_value(self):
        scroll, combo = self._page()
        WheelScrollGuard(scroll).guard(scroll)
        bar = scroll.verticalScrollBar()
        self.assertEqual(bar.value(), 0)
        event = QWheelEvent(
            QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(combo, event)
        self.assertGreater(bar.value(), 0, "滚轮应滚动页面")
        self.assertEqual(combo.currentIndex(), 0, "滚轮不应改变下拉值")


class ToastPopupTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_show_message_displays_and_hides(self):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        parent.resize(400, 300)
        parent.show()
        toast = ToastPopup(parent)
        toast.show_message("已添加 3 个任务", duration=50)
        self.assertTrue(toast.isVisible())
        self.assertEqual(toast._label.text(), "已添加 3 个任务")
        # 动画结束前隐藏不应报错
        toast.hide()
        self.assertFalse(toast.isVisible())


class StyledBackgroundTests(unittest.TestCase):
    """QWidget 子类必须开启 WA_StyledBackground，否则 QSS 背景/边框被静默忽略。

    回归事故：SettingsGroup / SegmentedControl / ToastPopup 三个 QWidget 子类
    都没开这个属性——设置分组卡片的 13px 圆角边框、分段容器底色、提示浮层的
    深色底全部没画出来（浮层还变成浅灰底 + 白字，几乎不可读）。实例与
    QFrame/QDialog 不受影响，所以只有子类需要这条守卫。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _border_pixel(self, widget, y=None):
        """把控件放进母容器渲染，取其左边框像素（顶层 grab 不反映 QSS）。"""
        host = QWidget()
        self.addCleanup(host.deleteLater)
        host.resize(520, 320)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(widget)
        layout.addStretch(1)
        host.show()
        for _ in range(3):
            self.app.processEvents()
        image = host.grab().toImage()
        origin = widget.mapTo(host, QPoint(0, 0))
        y = origin.y() + widget.height() // 2 if y is None else origin.y() + y
        return QColor(image.pixel(origin.x(), y)).name()

    def test_settings_group_card_border_is_painted(self):
        group = SettingsGroup("标题", "说明")
        self.addCleanup(group.deleteLater)
        group.add_row("行标题", "行说明", QLabel("控件"))
        self.assertTrue(group.testAttribute(
            Qt.WidgetAttribute.WA_StyledBackground))
        # 卡片边框统一用 border（比分隔线更深一档），灰底上才看得见框
        self.assertEqual(self._border_pixel(group), PALETTE["border"])

    def test_segmented_control_container_is_painted(self):
        control = SegmentedControl([("A", "a"), ("B", "b")])
        self.addCleanup(control.deleteLater)
        self.assertTrue(control.testAttribute(
            Qt.WidgetAttribute.WA_StyledBackground))
        self.assertEqual(self._border_pixel(control), PALETTE["border"])

    def test_toast_surface_is_dark_so_white_text_is_readable(self):
        toast = ToastPopup()
        self.addCleanup(toast.deleteLater)
        toast.show_message("已添加 3 个任务")
        toast._effect.setOpacity(1.0)
        self.assertTrue(toast.testAttribute(
            Qt.WidgetAttribute.WA_StyledBackground))
        # 浮层底色为深色主文字色，配合 QSS 的白字才可读
        self.assertEqual(self._border_pixel(toast), PALETTE["text"])
        # setGraphicsEffect 会清掉鼠标穿透属性，必须在设置效果之后重新打开
        self.assertTrue(toast.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents))

    def test_styled_widget_is_the_base_for_styled_subclasses(self):
        for cls in (SettingsGroup, SegmentedControl, ToastPopup):
            self.assertTrue(issubclass(cls, StyledWidget),
                            f"{cls.__name__} 应继承 StyledWidget")


class StepCardStateStripeTests(unittest.TestCase):
    """步骤卡左缘 3px 状态色条：一眼看出"现在在哪一步"。

    灰阶分层解决"区域分不清"，色条解决"不知道看哪里"——三张步骤卡外观
    完全一致时，用户只能读右上角的小药丸才知道进度。色条宽度恒定 3px
    （含 pending 用中性色），状态切换不产生宽度跳动。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(build_stylesheet())

    def _stripe(self, status: str) -> str:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        host.resize(520, 320)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(20, 20, 20, 20)
        panel = StepPanel(2, "语音生成")
        self.addCleanup(panel.deleteLater)
        layout.addWidget(panel)
        layout.addStretch(1)
        host.show()
        panel.set_status(status)
        for _ in range(3):
            self.app.processEvents()
        image = host.grab().toImage()
        origin = panel.mapTo(host, QPoint(0, 0))
        # 左边界往内 1px 正落在 3px 状态色条上
        return QColor(image.pixel(origin.x() + 1,
                                  origin.y() + panel.height() // 2)).name()

    def test_stripe_follows_step_status(self):
        for status, expected in (
            (STEP_PENDING, PALETTE["border"]),
            (STEP_RUNNING, PALETTE["accent"]),
            (STEP_DONE, PALETTE["ready"]),
            (STEP_FAILED, PALETTE["error"]),
        ):
            with self.subTest(status=status):
                self.assertEqual(self._stripe(status), expected)

    def test_stripe_width_is_pinned_for_every_status(self):
        """四条规则宽度都必须是 3px，否则切状态时会横向跳动。"""
        qss = build_stylesheet()
        for status in ("pending", "running", "done", "failed", "skipped"):
            self.assertIn(
                f'QFrame[card="true"][state="{status}"] '
                '{ border-left: 3px solid',
                qss, f"{status} 缺少 3px 状态色条规则")


class InlineStyleTests(unittest.TestCase):
    """组件样式一律走 objectName / 动态属性，禁止内联 setStyleSheet。

    内联样式会截断 QSS 继承（子控件拿不到主题规则），改主题时要满仓找颜色；
    统一由 ui_theme.build_stylesheet 提供。
    """

    def test_no_inline_stylesheet_outside_ui_theme(self):
        root = Path(__file__).resolve().parents[1] / "src"
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if path.name == "ui_theme.py":
                continue
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                if "setStyleSheet(" in line and not line.strip().startswith("#"):
                    offenders.append(f"{path.relative_to(root)}: {line.strip()}")
        self.assertEqual(offenders, [], "应改用 objectName/动态属性而非内联样式")


class LogColorTests(unittest.TestCase):
    """日志分级颜色集中在 ui_theme.LOG_COLORS，且与状态色板一致。"""

    def test_log_colors_reuse_status_palette(self):
        self.assertEqual(LOG_COLORS["success"], PALETTE["ready"])
        self.assertEqual(LOG_COLORS["error"], PALETTE["error"])
        self.assertEqual(sorted(LOG_COLORS), ["error", "info", "success", "warn"])

    def test_log_painter_uses_central_colors_only(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "workflow_gui"
                  / "window.py").read_text(encoding="utf-8")
        self.assertIn("LOG_COLORS[level]", source)
        # _append_log 内不得再硬编码色值
        block = source.split("def _append_log")[1].split("\n    def ")[0]
        self.assertNotRegex(block, r"#[0-9a-fA-F]{6}")


class SidebarSubtitleTests(unittest.TestCase):
    """侧栏第二行在 208px 栏宽下不能被省略号截断。

    实测第二行可用宽度（indentation=14、右侧为进度预留 48px、左边距
    12px×2 + 状态点 8px）：层级 0/1/2 = 95/81/67px。目录可随导入路径
    任意加深，所以按最深层 67px 预算设计，文案越短越稳。
    """

    WIDEST_SUBTITLE_PX = 67      # 层级 2（组 > 目录 > 任务）可用宽度实测值

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = QFont()
        font.setPixelSize(12)
        cls.metrics = QFontMetrics(font)

    def _task(self, **status):
        task = TaskInfo(task_id="t", source_path="C:/a.mp3", source_name="a",
                        workspace_root="C:/w")
        for step, value in status.items():
            task.set_step_status(int(step[-1]), value)
        return task

    def _subtitle(self, task):
        return TaskListWidget._task_subtitle(TaskListWidget.__new__(TaskListWidget), task)

    def _assert_fits(self, text):
        width = self.metrics.horizontalAdvance(text)
        self.assertLessEqual(
            width, self.WIDEST_SUBTITLE_PX,
            f"“{text}” 宽 {width}px，超过二层可用 "
            f"{self.WIDEST_SUBTITLE_PX}px 会被省略号截断")
        self.assertEqual(
            self.metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                    self.WIDEST_SUBTITLE_PX),
            text, f"“{text}” 不应被省略")

    def test_task_subtitles_fit_deepest_indent_budget(self):
        cases = [
            self._task(),
            self._task(step1=STEP_DONE, step2=STEP_RUNNING),
            self._task(step1=STEP_DONE, step2=STEP_FAILED),
            self._task(step1=STEP_DONE, step2=STEP_DONE, step3=STEP_DONE),
            self._task(step1=STEP_SKIPPED, step2=STEP_SKIPPED, step3=STEP_DONE),
        ]
        for task in cases:
            self._assert_fits(self._subtitle(task))

    def test_group_count_subtitles_fit_deepest_indent_budget(self):
        # 原格式 “N 项 · M 完成” 实测 120–156px，连最上层 95px 都放不下；
        # 紧凑的 “done/total” 在两位数内整体通过。三位数总量（如 64/128，
        # 72px）在最深层会退化成 Qt 优雅省略，完整值仍在 tooltip 中。
        for done, total in ((0, 1), (5, 12), (9, 9), (12, 7), (99, 99)):
            self._assert_fits(TaskListWidget._count_subtitle(done, total))

    def test_subtitle_is_the_bare_status_word(self):
        # 第二行只承载状态词；"进行中 · 步骤2" 108px 会被截断
        cases = [
            ({}, "等待中"),
            ({"step1": STEP_DONE, "step2": STEP_RUNNING}, "进行中"),
            ({"step1": STEP_DONE, "step2": STEP_FAILED}, "失败"),
            ({"step1": STEP_DONE, "step2": STEP_DONE, "step3": STEP_DONE}, "已完成"),
            ({"step1": STEP_SKIPPED}, "等待中"),
        ]
        for status, expected in cases:
            self.assertEqual(self._subtitle(self._task(**status)), expected)

    def test_step_detail_lives_in_tooltip(self):
        # 步骤定位不再放进第二行，但必须仍在 tooltip 数据源里可查
        marks = TaskListWidget._step_marks(
            self._task(step1=STEP_DONE, step2=STEP_FAILED))
        self.assertEqual(marks, ["1✓", "2✗", "3○"])

    def test_step_numbers_are_not_glued_to_marks(self):
        # 旧格式 “1○” 会被读成 “10”，第二行不得再使用这种拼接
        subtitle = self._subtitle(self._task(step1=STEP_DONE, step2=STEP_RUNNING))
        self.assertNotRegex(subtitle, r"\d[✓–✗▶○]")


class IndicatorPaintTests(unittest.TestCase):
    """勾选图标必须是真的"勾"，不能退化成纯色块。

    回归事故：QSS 里写成 ``image: url(file:///...)``——本机 Qt 的 ``url()``
    不认这个 scheme，图片静默加载失败，指示器只剩 ``background``，看上去就是
    一个实心黑方块。这里直接数像素判定：既要有深色底，也要有浅色勾。
    """

    LIGHT = 190          # 亮度阈值：勾是白的
    DARK = 90            # 亮度阈值：底是 #18181a

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def _counts(self, checked: bool):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        host.resize(220, 44)
        layout = QHBoxLayout(host)
        layout.setContentsMargins(12, 8, 12, 8)
        box = QCheckBox()
        self.addCleanup(box.deleteLater)
        box.setChecked(checked)
        layout.addWidget(box)
        host.show()
        for _ in range(3):
            self.app.processEvents()
        image = host.grab().toImage()
        origin = box.mapTo(host, box.rect().topLeft())
        light = dark = 0
        for y in range(origin.y() + 3, origin.y() + 12):
            for x in range(origin.x() + 3, origin.x() + 11):
                color = image.pixelColor(x, y)
                luma = (color.red() * 299 + color.green() * 587
                        + color.blue() * 114) // 1000
                if luma >= self.LIGHT:
                    light += 1
                elif luma < self.DARK:
                    dark += 1
        host.close()
        return light, dark

    def test_checked_indicator_paints_a_white_check_on_dark_fill(self):
        light, dark = self._counts(True)
        self.assertGreaterEqual(
            dark, 20, "勾选指示器没有深色底（background 丢失）")
        self.assertGreaterEqual(
            light, 8,
            "勾选指示器是纯色块：check.png 没画出来（QSS url() 是否带了 "
            "file:/// 前缀？）")

    def test_unchecked_indicator_is_not_a_dark_fill(self):
        _, dark = self._counts(False)
        self.assertLess(dark, 8, "未勾选时不应有深色填充")

    def test_stylesheet_uses_bare_path_for_icon(self):
        qss = build_stylesheet()
        self.assertIn("image: url(", qss, "勾选图标样式缺失")
        self.assertNotIn("url(file:///", qss)


class BundledResourcePathTests(unittest.TestCase):
    """打包（frozen）后勾选图标仍必须能被 QSS 找到。

    回归事故（用户反馈"这里没有打勾，还有好多地方没有打勾"）：**打包版**全屏
    勾选框都退化成纯黑方块。根因不在 QSS，而在 ``ui_theme._resource_dir()`` 用
    ``os.path.dirname(sys.executable)`` 拼资源目录——PyInstaller 6 起 onedir 把
    ``.spec`` 的 datas 放进 ``_internal/``（正指向 ``sys._MEIPASS``），exe 同级
    目录下没有 ``resources/``，于是 ``resource_url('check.png')`` **静默返回空串**
    （只读资源没有兜底），QSS 里少掉 ``image: url(...)``。

    该缺陷**只在打包版出现**：源码运行与离屏测试都察觉不到（``__file__`` 在源码
    下是对的），所以必须显式模拟 frozen 环境才能守住。
    """

    def _pretend_frozen(self, meipass: str) -> None:
        """把进程伪装成打包环境，用例结束后精确还原（原先没有的属性删掉）。"""
        missing = object()
        saved = {key: getattr(sys, key, missing)
                 for key in ("frozen", "_MEIPASS", "executable")}
        sys.frozen = True
        sys._MEIPASS = meipass

        def restore():
            for key, value in saved.items():
                if value is missing:
                    if hasattr(sys, key):
                        delattr(sys, key)
                else:
                    setattr(sys, key, value)
        self.addCleanup(restore)

    def test_resource_dir_uses_meipass_when_frozen(self):
        """onedir：datas 在 ``_MEIPASS``（``_internal/``），必须优先用它。"""
        with TemporaryDirectory() as tmp:
            ui_dir = Path(tmp) / "resources" / "ui"
            ui_dir.mkdir(parents=True)
            (ui_dir / "check.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            self._pretend_frozen(tmp)

            self.assertEqual(
                os.path.normcase(ui_theme._resource_dir()),
                os.path.normcase(str(Path(tmp) / "resources")),
                "_MEIPASS 下有 resources/ 时必须用它；退回 exe 同级目录会让"
                "resource_url() 返回空串，勾选框退化成纯黑方块")
            self.assertTrue(resource_url("check.png"),
                            "打包后 check.png 解析为空 → 所有勾选框都不见勾")

    def test_resource_dir_falls_back_to_exe_dir(self):
        """老式布局：datas 与 exe 同级时仍要能找到资源。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "resources" / "ui").mkdir(parents=True)
            (root / "resources" / "ui" / "check.png").write_bytes(b"x")
            empty_meipass = root / "internal"
            empty_meipass.mkdir()
            self._pretend_frozen(str(empty_meipass))
            sys.executable = str(root / "app.exe")

            self.assertEqual(
                os.path.normcase(ui_theme._resource_dir()),
                os.path.normcase(str(root / "resources")),
                "_MEIPASS 下没有 resources/ 时，应回退到 exe 同级目录")


class StatusGreenTests(unittest.TestCase):
    """「已完成」必须是明确的绿色（截图反馈：原来偏灰看不出完成）。"""

    def test_ready_color_is_green_dominant(self):
        color = QColor(PALETTE["ready"])
        self.assertGreater(color.green(), color.red())
        self.assertGreater(color.green(), color.blue())
        # 与另两个通道拉开差距，避免又变成"灰绿"
        self.assertGreater(
            color.green() - max(color.red(), color.blue()), 30,
            f"{PALETTE['ready']} 绿意不足，会看成灰色")

    def test_done_pill_and_dot_use_the_green(self):
        qss = build_stylesheet()
        self.assertIn(f'QLabel[pill="true"][status="done"] {{ color: '
                      f"{PALETTE['ready']}", qss)
        # 侧栏状态点与药丸同色，避免两处完成色不一致
        from src.widgets.task_list import _STATUS_DOT_COLORS
        self.assertEqual(_STATUS_DOT_COLORS["done"], PALETTE["ready"])
        self.assertEqual(_STATUS_DOT_COLORS["running"], PALETTE["accent"])
        self.assertEqual(_STATUS_DOT_COLORS["failed"], PALETTE["error"])


class TreeConnectorTests(unittest.TestCase):
    """任务树必须有横向肘线（├ / └），光有竖线看不出从属关系。"""

    # 与实现同源：硬编码色值会在调色板变动时静默失配（守卫找不到线，
    # 报"竖线断开"这种指错方向的失败）
    GUIDE = QColor(_GUIDE_COLOR)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        from src.task_manager import TaskQueue, TaskGroup
        from PyQt6.QtWidgets import QTreeWidgetItem
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        base = self.root / "组" / "子目录"
        base.mkdir(parents=True)
        for name in ("a.mp3", "b.mp3"):
            (base / name).write_bytes(b"\x00")
        self.queue = TaskQueue(str(self.root / "tasks"))
        self.group = self.queue.create_group(str(self.root / "组"))
        self.tasks = [self.queue.add_task(str(base / n),
                                          group_id=self.group.group_id)
                      for n in ("a.mp3", "b.mp3")]
        self.tree = TaskListWidget()
        self.addCleanup(self.tree.deleteLater)
        self.tree.set_task_queue(self.queue)
        self.tree.add_group_item(self.group)
        for task in self.queue.tasks:
            self.tree.add_task_item(task)
        self.host = QWidget()
        self.addCleanup(self.host.deleteLater)
        layout = QVBoxLayout(self.host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        self.host.resize(208, 400)
        self.host.show()
        for _ in range(3):
            self.app.processEvents()
        self.tree.expandAll()
        for _ in range(5):
            self.app.processEvents()
        self.image = self.host.grab().toImage()

    def _is_guide(self, color):
        return (abs(color.red() - self.GUIDE.red()) < 18
                and abs(color.green() - self.GUIDE.green()) < 18
                and abs(color.blue() - self.GUIDE.blue()) < 18)

    def _guide_row(self, rect, x_from, x_to):
        """行中心 ±1px 的横带上数连接线像素（用于找横向肘线）。"""
        cy = rect.top() + rect.height() // 2
        return sum(1 for y in range(cy - 1, cy + 2)
                   for x in range(x_from, x_to)
                   if self._is_guide(self.image.pixelColor(x, y)))

    def _guide_column(self, rect, x_center):
        """整行高度上、某一列附近的连接线像素（用于区分 ├ 与 └）。"""
        return sum(1 for y in range(rect.top(), rect.bottom() + 1)
                   for x in range(x_center - 1, x_center + 2)
                   if self._is_guide(self.image.pixelColor(x, y)))

    def _guide_span(self, x_center, y_from, y_to):
        """指定 y 区间内某列附近的连接线像素（逐行判定连续性）。"""
        return sum(1 for y in range(y_from, y_to + 1)
                   for x in range(x_center - 1, x_center + 2)
                   if self._is_guide(self.image.pixelColor(x, y)))

    @staticmethod
    def _elbow_x(rect, indent):
        """自身与父节点相连的肘点列 = 父节点列中心。

        Qt 的 ``rect.left() = indentation×(depth+1)``，父列中心再往左一个
        indent 再加半个 indent，即 ``left - 1.5×indent``。
        """
        return rect.left() - indent - indent // 2

    def test_child_rows_have_a_horizontal_elbow(self):
        """子节点行要有从肘点伸向内容起点的横向连线（不只是竖线）。"""
        indent = self.tree.indentation()
        folder = self.tree.topLevelItem(0).child(0)
        for child in (self.tree.topLevelItem(0).child(0), folder.child(0)):
            rect = self.tree.visualRect(self.tree.indexFromItem(child))
            elbow_x = self._elbow_x(rect, indent)
            found = self._guide_row(rect, elbow_x + 2, rect.left() - 1)
            self.assertGreater(
                found, 0,
                f"“{child.text(0)}” 所在行没有横向连接线（只有竖线）")

    def test_elbow_line_stops_at_the_chevron_without_a_stub(self):
        """横线必须止于展开箭头，不得从箭头右侧露出一截线头。

        截图反馈的"多余线头"：箭头画在 ``content_left - indent/2``、横跨
        ``±_CHEVRON_SIZE/2``，而横线一路画到 ``content_left-2``（比箭头
        右缘还远 5px），末端就从箭头右侧露出来。修法：有箭头时终点改为
        ``cx - _CHEVRON_SIZE/4``，既不留缝也不露头。
        """
        indent = self.tree.indentation()
        folder = self.tree.topLevelItem(0).child(0)
        self.assertGreater(folder.childCount(), 0, "本用例需要一行有子节点的目录")
        rect = self.tree.visualRect(self.tree.indexFromItem(folder))
        cy = rect.center().y()
        cx = rect.left() - indent // 2

        # ① 横线要接到箭头左缘，不能半途断掉留出缝隙
        self.assertTrue(
            self._is_guide(self.image.pixelColor(cx - _CHEVRON_SIZE // 2, cy)),
            "横线没有接到展开箭头（箭头左侧出现缝隙）")
        # ② 箭头右缘到内容起点之间（状态点之前）不得再有横线像素
        stray = [x for x in range(cx + _CHEVRON_SIZE // 2, rect.left())
                 if self._is_guide(self.image.pixelColor(x, cy))]
        self.assertEqual(stray, [], f"箭头右侧仍有多余线头（x={stray}）")
        # ③ 无子节点的行不受影响：横线仍应接到内容起点
        leaf = folder.child(0)
        leaf_rect = self.tree.visualRect(self.tree.indexFromItem(leaf))
        self.assertTrue(
            self._is_guide(self.image.pixelColor(leaf_rect.left() - 2,
                                                 leaf_rect.center().y())),
            "无子节点的行横线应接到内容起点")

    def test_last_child_uses_corner_style(self):
        """末子 └（竖线止于肘点），非末子 ├（竖线贯穿整行接到下一个兄弟）。"""
        indent = self.tree.indentation()
        folder = self.tree.topLevelItem(0).child(0)
        self.assertGreaterEqual(folder.childCount(), 2)
        first = folder.child(0)
        last = folder.child(folder.childCount() - 1)
        first_rect = self.tree.visualRect(self.tree.indexFromItem(first))
        last_rect = self.tree.visualRect(self.tree.indexFromItem(last))
        column = self._elbow_x(first_rect, indent)

        for rect, name in ((first_rect, first.text(0)),
                           (last_rect, last.text(0))):
            above = self._guide_span(column, rect.top(), rect.center().y())
            self.assertGreater(
                above, rect.height() // 4,
                f"“{name}”缺少肘点上方的竖线（├ / └ 的竖段）")

        below_first = self._guide_span(column, first_rect.center().y() + 2,
                                       first_rect.bottom())
        self.assertGreater(
            below_first, first_rect.height() // 4,
            "非末子肘点以下缺竖线，与下一个兄弟断开（├ 应贯穿整行）")
        below_last = self._guide_span(column, last_rect.center().y() + 2,
                                      last_rect.bottom())
        self.assertEqual(below_last, 0, "末子画成了 ├，应止于肘点（└）")

    def test_guide_color_stays_clear_of_surface_colors(self):
        """导引线色必须与侧栏各表面色拉开距离（每通道 ≥19）。

        ``_is_guide`` 按 ±18 容差找线条像素；行悬停/选中底色一旦落进这个
        容差带，非末子的空白区也会被当成"竖线"，└ 的守卫就误报（本次把
        悬停色从 #fafafa 改成 #e3e3e8 时正撞上 #d2d2d7 的导引线）。
        """
        from src.widgets.task_list import _STATUS_DOT_COLORS
        guide = QColor(_GUIDE_COLOR)
        for name, color in (
            ("侧栏底", QColor(PALETTE["sidebar"])),
            ("行悬停", QColor(PALETTE["row_hover"])),
            ("行选中", QColor(PALETTE["selected"])),
            ("等待状态点", QColor(_STATUS_DOT_COLORS["pending"])),
        ):
            gap = min(abs(color.red() - guide.red()),
                      abs(color.green() - guide.green()),
                      abs(color.blue() - guide.blue()))
            self.assertGreaterEqual(
                gap, 19,
                f"{name} {color.name()} 与导引线 {guide.name()} 太近，"
                "会被树连线守卫误判成线条")

    def test_parent_spine_is_continuous(self):
        """父节点下方必须是一条连续竖线，子项用横线挂在它上面。

        截图反馈原话：「wav 一条直线下面，它下有级的文件一条横线链接
        wav 下面的直线，是这个链接逻辑」。旧实现每行只画"行顶 → 行中心"
        的半行竖线，行与行之间留缝（看着是断开的虚线），末子更是完全不画。
        """
        indent = self.tree.indentation()
        folder = self.tree.topLevelItem(0).child(0)
        folder_rect = self.tree.visualRect(self.tree.indexFromItem(folder))
        last = folder.child(folder.childCount() - 1)
        last_rect = self.tree.visualRect(self.tree.indexFromItem(last))
        column = self._elbow_x(last_rect, indent)     # 子级竖线所在列

        gaps = [y for y in range(folder_rect.center().y() + 6,
                                 last_rect.center().y() + 1)
                if self._guide_span(column, y, y) == 0]
        self.assertEqual(
            gaps, [],
            f"从 {folder.text(0)} 中心到末子中心的竖线在 y={gaps} 处断开")

    def test_sole_child_does_not_leave_a_dangling_line(self):
        """末子之后不再续画祖先列竖线（Qt 的 State_Sibling 语义）。

        否则会留下一条挂不到任何节点、一路延伸到子树底部的悬空长线。
        """
        indent = self.tree.indentation()
        top = self.tree.topLevelItem(0)
        self.assertEqual(top.childCount(), 1, "本用例需要组下只有一个目录")
        folder = top.child(0)
        child = folder.child(0)
        rect = self.tree.visualRect(self.tree.indexFromItem(child))
        hits = self._guide_span(indent // 2, rect.top(), rect.bottom())
        self.assertEqual(hits, 0, "末子之后仍残留祖先列竖线（悬空长线）")

    def test_root_level_has_no_connector(self):
        """顶层节点没有父列，不该凭空画连接线。"""
        top = self.tree.topLevelItem(0)
        rect = self.tree.visualRect(self.tree.indexFromItem(top))
        self.assertEqual(
            self._guide_row(rect, 0, rect.left() - 1), 0,
            "顶层行左侧不应出现连接线")


class LogAreaLayoutTests(unittest.TestCase):
    """日志区必须吃掉中央区剩余高度（截图反馈：卡片下手大片空白）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        from src.workflow_gui import WorkflowMainWindow
        self.window = WorkflowMainWindow(_make_config(Path(self.folder.name)))
        self.addCleanup(self.window.deleteLater)
        self.window.resize(1380, 900)

    def test_log_view_has_no_small_height_cap(self):
        # 旧实现 max-height:220px，剩余高度就变成了卡片下方的空白
        self.assertGreaterEqual(
            self.window.log_view.maximumHeight(), 2000,
            "日志框被 max-height 截断，剩余高度会留成空白")

    def test_log_card_takes_the_stretch(self):
        content = self.window.findChildren(QWidget, "centerScrollContent")
        self.assertEqual(len(content), 1)
        layout = content[0].layout()
        index = layout.count() - 1
        self.assertIsNotNone(
            layout.itemAt(index).widget(),
            "中央内容末尾不应再挂伸缩空白项（空白会被留在最下方）")
        self.assertEqual(layout.stretch(index), 1,
                         "日志卡应拿到 1 权重以吸收剩余高度")
        self.assertIs(layout.itemAt(index).widget(),
                      self.window.log_view.parentWidget())


class ElidedLabelTests(unittest.TestCase):
    """单行省略标签：按像素宽度省略、tooltip/取值保留完整文本。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_text_stays_complete_while_display_is_elided(self):
        label = ElidedLabel()
        self.addCleanup(label.deleteLater)
        label.resize(120, 20)
        long_text = "C:/" + "/".join(f"folder{index}" for index in range(20))
        label.setText(long_text)
        # text() 返回完整文本（渲染值是省略后的）
        self.assertEqual(label.text(), long_text)
        self.assertEqual(label.toolTip(), long_text)
        self.assertIn("…", QLabel.text(label))
        self.assertLessEqual(label.width(), 120)

    def test_short_text_is_not_elided(self):
        label = ElidedLabel()
        self.addCleanup(label.deleteLater)
        label.resize(300, 20)
        label.setText("工作区：tasks")
        self.assertEqual(QLabel.text(label), "工作区：tasks")


if __name__ == "__main__":
    unittest.main()
