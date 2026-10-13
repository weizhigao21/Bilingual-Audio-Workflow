# -*- coding: utf-8 -*-
"""声笺风格主题（依据《声笺 UI 风格规范 / UI_STYLE.md》实现）。

白色工作区、浅灰侧栏、细分隔线、小圆角、紧凑列表；强调色 #e0651c 只用于
焦点、播放进度与少量状态。本模块集中管理：

- 色板与排版常量（PALETTE / 尺寸）
- 完整 QSS 样式表（build_stylesheet）
- 中文字体显式配置（configure_font，离屏预览同样生效）
- 一键应用（apply_theme：Fusion + 字体 + 样式表）
- 滚轮守卫 WheelScrollGuard（交互稳定性：数值框/下拉/滑条上的滚轮滚动页面）

复用约定：组件通过 objectName 与动态属性（kind / role / status）匹配样式，
新增控件时优先设置这些属性，而不是写内联样式。
"""
import os
import sys

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QScrollArea, QSlider, QWidget,
)

# ---------------------------------------------------------------- 色板
# 与 UI_STYLE.md「颜色与字体」一一对应
PALETTE = {
    # —— 分区策略：表面保持白/近白，靠"看得见的边界线"划分功能区域 ——
    # 曾试过灰阶分层（canvas #f0f0f3 / sidebar #e7e7eb），用户反馈"太丑"，
    # 想要的是"干净的白 + 线条分区"。所以表面全部回白，分区改由 border
    # 承担——但线必须真的看得见，旧的 #eeeeee 与白底只差 17 灰阶，1x 屏上
    # 等于没有边界，那才是"区域区分不明显"的真正原因。
    "bg": "#ffffff",            # 卡片、输入框、按钮、菜单（纯白）
    "canvas": "#ffffff",        # 工作区底：与卡片同白，区域感由边界线给出
    "sidebar": "#fbfbfc",       # 侧栏：极轻表面差，主要靠右侧 1px 边界线分界
    "inset": "#f0f0f3",         # 内凹底：分段容器、状态药丸（需与白前景拉开）
    "text": "#18181a",          # 主文字、主按钮背景
    "text_secondary": "#63636a",  # 次要文字
    "text_label": "#7e7e88",    # 辅助标签
    "border": "#d4d4da",        # 区域边界线 / 卡片边框（与白底差 ≥25 才可见）
    "separator": "#ececf0",     # 区域内二级分隔（卡片行间、菜单、表头）
    "selected": "#ededf0",      # 列表选中背景
    "row_hover": "#f4f4f6",     # 侧栏任务行悬停（比侧栏底深一档，浅于选中）
    "accent": "#e0651c",        # 强调色（进度、焦点、进行中）
    "focus": "#d9ac8c",         # 输入焦点边框
    "ready": "#2e7d32",         # 就绪/完成状态（明确的绿，用于状态药丸与日志成功）
    "ready_bg": "#eef7ef",      # 完成态浅绿底
    "ready_border": "#cbe4cf",  # 完成态浅绿边框
    "error": "#a2462e",         # 错误文字
    # 以下为派生色（规范未直接给出，按同色系推导，仅用于悬停/禁用等中间态）
    "hover": "#f4f4f6",
    "pressed": "#ececf0",
    "primary_hover": "#343436",
    "disabled_text": "#b0b0b4",
    "disabled_bg": "#f7f7f9",
    "disabled_border": "#efeff2",
    "primary_disabled": "#d6d6d9",
}

# 日志分级颜色（按色板推导，供 QTextEdit 富文本着色使用；
# 成功色直接复用"就绪状态"，避免同一语义出现两种绿）
LOG_COLORS = {
    "info": "#3d5a80",      # 常规信息（深蓝，区别于正文黑）
    "warn": "#b26a00",      # 警告（同强调色系但更深，保证 12px 可读）
    "error": "#a2462e",     # 错误（与 PALETTE['error'] 一致）
    "success": PALETTE["ready"],   # 完成（直接复用"就绪状态"，杜绝两处绿色漂移）
}

# ---------------------------------------------------------------- 排版
FONT_FAMILY = "Microsoft YaHei"   # Windows；其他平台回退系统无衬线
FONT_FAMILY_FALLBACK = "Segoe UI, PingFang SC, Noto Sans CJK SC, sans-serif"
MONO_FAMILY = "Consolas, Cascadia Mono, Courier New, monospace"
FONT_BODY = 13      # 正文 px
FONT_AUX = 12       # 辅助文字 px
FONT_SECTION = 14   # 分区标题 px
FONT_TITLE = 23     # 页面标题 px

# ---------------------------------------------------------------- 尺寸
SIDEBAR_WIDTH = 208          # 左侧导航栏
SIDEBAR_PADDING = 12
CENTER_PADDING = (20, 18, 20, 12)   # 中央内容内边距 18–24
CONTROL_SPACING = 7          # 控件间距 6–8
SECTION_SPACING = 14         # 分区间距 12–16
RADIUS_CONTROL = 6           # 常规圆角
RADIUS_CARD = 6              # 内容卡片圆角
RADIUS_SETTINGS_CARD = 13    # 设置分组容器圆角
RADIUS_DIALOG = 14           # 弹窗圆角
BUTTON_PADDING = (10, 6)     # 按钮内边距 6×10（水平, 垂直）
INPUT_PADDING = (8, 6)       # 输入框内边距 6×8
SETTINGS_ROW_PADDING = (18, 14)  # 设置行内边距 18×14
LIST_ROW_HEIGHT = 50         # 模型/音频列表行高基准（任务列表同）
MODEL_ROW_HEIGHT = 66        # 设置页模型行高
PROGRESS_HEIGHT = 3          # 进度条 3px
ICON_SIZE = 18               # 常规图标 18px 细线
DEFAULT_WINDOW_SIZE = (1380, 900)
MIN_WINDOW_SIZE = (1080, 720)


def _resource_dir() -> str:
    """资源目录：开发时为项目根 ``resources/``，打包后为**资源包内**的 ``resources/``。

    打包版务必走 ``sys._MEIPASS``，**不能用 ``os.path.dirname(sys.executable)``**：
    PyInstaller 6 起 onedir 模式把 ``.spec`` 的 datas（含 ``resources/ui/check.png``）
    全部放进 ``_internal/``，而 ``sys._MEIPASS`` 正指向 ``_internal``；exe 同级目录下
    根本没有 ``resources/``。这一步走错会让 ``resource_url()`` **静默返回空串**
    （只读资源没有兜底），QSS 里少掉 ``image: url(...)``，勾选框就退化成纯黑方块
    ——即"完成""多 API 轮询"等所有勾选项一律不见勾。该缺陷是**打包版专属**，
    源码运行与离屏测试都察觉不到（``__file__`` 在源码下是对的）。
    """
    if getattr(sys, "frozen", False):
        # onedir / onefile 均以 _MEIPASS 为准；保留 exe 同级目录作兼容回退
        # （老式打包布局把 datas 放在 exe 旁边）
        candidates = [getattr(sys, "_MEIPASS", None),
                      os.path.dirname(sys.executable)]
        for base in candidates:
            if not base:
                continue
            candidate = os.path.join(base, "resources")
            if os.path.isdir(candidate):
                return candidate
        return os.path.join(candidates[0] or candidates[1], "resources")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "resources")


def resource_url(name: str) -> str:
    """QSS 中引用的资源路径（**纯文件路径**，PNG 由 QtGui 直接解码，不依赖 QtSvg）。

    注意：**不要加 ``file:///`` 前缀**。本机 Qt 的 QSS ``url()`` 解析器不认这个
    scheme——加了之后图片会静默加载失败，只画得出 ``background``，勾选框因此退化
    成纯黑方块（实测同一路径加前缀 0 个白色像素、不加前缀 23 个）。纯路径对中文
    目录同样有效。文件不存在时返回空串，调用方据此跳过该条样式。
    """
    path = os.path.join(_resource_dir(), "ui", name)
    if not os.path.exists(path):
        return ""
    return os.path.abspath(path).replace("\\", "/")


def configure_font(app: QApplication) -> None:
    """显式加载中文字体，保证离屏预览与各平台显示一致。"""
    font = QFont(FONT_FAMILY)
    font.setStyleHint(QFont.StyleHint.System)
    font.setPointSize(10)  # QSS 中的 px 尺寸会覆盖具体控件的字号
    app.setFont(font)


def build_stylesheet() -> str:
    """生成完整 QSS。颜色/尺寸全部取自上方常量，改风格只改这一处。"""
    p = PALETTE
    check_url = resource_url("check.png")
    checked_indicator = (
        f"image: url({check_url});" if check_url else ""
    )
    return f"""
/* ---------- 全局 ---------- */
QWidget {{
    font-family: "{FONT_FAMILY}", {FONT_FAMILY_FALLBACK};
    font-size: {FONT_BODY}px;
    color: {p['text']};
}}
/* 顶层窗口用"画布灰"，让白色卡片浮起来；卡片本身仍是纯白 */
QMainWindow {{ background: {p['canvas']}; }}
QDialog {{ background: {p['canvas']}; }}
QToolTip {{
    background: {p['bg']}; color: {p['text']};
    border: 1px solid {p['border']}; border-radius: 4px; padding: 4px 6px;
}}
QWidget:disabled {{ color: {p['disabled_text']}; }}

/* ---------- 标签角色 ---------- */
QLabel {{ background: transparent; border: none; }}
QLabel[role="brand"] {{ font-size: 17px; font-weight: 600; color: {p['text']}; }}
QLabel[role="title"] {{ font-size: {FONT_TITLE}px; color: {p['text']}; }}
QLabel[role="section"] {{ font-size: {FONT_SECTION}px; color: {p['text']}; }}
QLabel[role="muted"] {{ color: {p['text_label']}; font-size: {FONT_AUX}px; }}
QLabel[role="secondary"] {{ color: {p['text_secondary']}; font-size: {FONT_AUX}px; }}
QLabel[role="mono"] {{ font-family: {MONO_FAMILY}; font-size: {FONT_AUX}px; color: {p['text_secondary']}; }}
QLabel[role="error"] {{ color: {p['error']}; font-size: {FONT_AUX}px; }}
QLabel[role="success"] {{ color: {p['ready']}; font-size: {FONT_AUX}px; }}

/* ---------- 状态药丸（文字+颜色双通道） ---------- */
QLabel[pill="true"] {{
    border-radius: 9px; padding: 2px 9px; font-size: {FONT_AUX}px;
    background: {p['inset']}; color: {p['text_label']}; border: 1px solid {p['separator']};
}}
QLabel[pill="true"][status="running"] {{ color: {p['accent']}; background: #fdf3ec; border-color: #f3d9c6; }}
QLabel[pill="true"][status="done"] {{ color: {p['ready']}; background: {p['ready_bg']}; border-color: {p['ready_border']}; }}
QLabel[pill="true"][status="failed"] {{ color: {p['error']}; background: #faf2f0; border-color: #ecd7d0; }}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {p['bg']}; border: 1px solid #e3e3e5; border-radius: {RADIUS_CONTROL}px;
    padding: {BUTTON_PADDING[1]}px {BUTTON_PADDING[0]}px; min-height: 16px;
}}
QPushButton:hover {{ background: {p['hover']}; border-color: #d6d6d8; }}
QPushButton:pressed {{ background: {p['pressed']}; }}
QPushButton:disabled {{ color: {p['disabled_text']}; background: {p['disabled_bg']}; border-color: {p['disabled_border']}; }}
QPushButton[kind="primary"] {{ background: {p['text']}; color: #ffffff; border-color: {p['text']}; }}
QPushButton[kind="primary"]:hover {{ background: {p['primary_hover']}; border-color: {p['primary_hover']}; }}
QPushButton[kind="primary"]:pressed {{ background: {p['text']}; }}
QPushButton[kind="primary"]:disabled {{ background: {p['primary_disabled']}; border-color: {p['primary_disabled']}; color: #ffffff; }}
QPushButton[kind="text"] {{ background: transparent; border: none; color: {p['text_secondary']}; padding: 6px 8px; }}
QPushButton[kind="text"]:hover {{ background: {p['sidebar']}; color: {p['text']}; }}
QPushButton[kind="text"]:disabled {{ background: transparent; border: none; }}
QPushButton[kind="danger"] {{ color: {p['error']}; }}
QPushButton[kind="danger"]:hover {{ background: #faf5f2; border-color: #d9b4a8; }}
/* 统一选择窗里的大选项按钮（"添加素材"先选文件还是文件夹） */
QPushButton[kind="option"] {{
    text-align: left; padding: 11px 14px; border-radius: 8px;
    background: {p['bg']}; border: 1px solid {p['border']};
}}
QPushButton[kind="option"]:hover {{ background: {p['sidebar']}; border-color: {p['focus']}; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {p['bg']}; border: 1px solid {p['border']}; border-radius: {RADIUS_CONTROL}px;
    padding: {INPUT_PADDING[1]}px {INPUT_PADDING[0]}px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {p['focus']}; }}
QComboBox:focus {{ border-color: {p['focus']}; }}
QComboBox::drop-down {{ border: none; width: 18px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {p['bg']}; border: 1px solid {p['border']}; border-radius: {RADIUS_CONTROL}px;
    padding: 4px; selection-background-color: {p['selected']}; selection-color: {p['text']};
    outline: none;
}}
QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: 4px; min-height: 18px; }}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent; border: none; width: 14px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ width: 8px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ width: 8px; height: 8px; }}

/* ---------- 复选框 ---------- */
QCheckBox {{ background: transparent; spacing: 7px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid {p['border']};
    border-radius: 3px; background: {p['bg']};
}}
QCheckBox::indicator:hover {{ border-color: #c9c9cc; }}
QCheckBox::indicator:checked {{ background: {p['text']}; border-color: {p['text']}; {checked_indicator} }}
QCheckBox::indicator:checked:disabled {{ background: {p['disabled_text']}; border-color: {p['disabled_text']}; {checked_indicator} }}

/* ---------- 进度条（3px 细条，文字外置） ---------- */
QProgressBar {{
    background: {p['selected']}; border: none; border-radius: 1px;
    height: {PROGRESS_HEIGHT}px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 1px; }}

/* ---------- 滑块（分离度等连续参数） ---------- */
/* 细槽 + 圆形白柄：与整体"细线条"语言一致，不用 Fusion 默认的粗槽 */
QSlider::groove:horizontal {{
    height: 4px; border-radius: 2px; background: {p['separator']};
}}
QSlider::sub-page:horizontal {{ background: {p['text']}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: {p['separator']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
    background: {p['bg']}; border: 1px solid {p['border']};
}}
QSlider::handle:horizontal:hover {{ border-color: {p['focus']}; }}
QSlider::handle:horizontal:pressed {{ background: {p['hover']}; }}
QSlider::sub-page:horizontal:disabled {{ background: {p['disabled_bg']}; }}
QSlider::handle:horizontal:disabled {{ background: {p['disabled_bg']}; border-color: {p['disabled_border']}; }}

/* ---------- 滚动条（细） ---------- */
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #d6d6d8; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #c2c2c5; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #d6d6d8; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------- 主工作台骨架（侧栏 + 中央两区） ---------- */
QWidget#sidebar {{ background: {p['sidebar']}; border-right: 1px solid {p['border']}; }}
QWidget#centerPane {{ background: {p['canvas']}; }}
QWidget#toolbarDivider {{ background: {p['border']}; }}
/* 页头（当前条目 + 工具栏）与下方滚动内容的分界线：
   没有这条线时工具栏与第一张卡片同色相邻，看不出"固定区/滚动区"之分 */
QWidget#headerRule {{ background: {p['border']}; }}

/* ---------- 卡片（步骤卡 / 日志卡 / 属性卡） ---------- */
QWidget[card="true"], QFrame[card="true"] {{
    background: {p['bg']}; border: 1px solid {p['border']}; border-radius: {RADIUS_CARD}px;
}}
/* 步骤卡左缘状态色条：一眼看出"现在在哪一步"。
   四条规则都把宽度钉死 3px（含 pending 用中性色），状态切换不产生宽度跳动。 */
QFrame[card="true"][state="pending"] {{ border-left: 3px solid {p['border']}; }}
QFrame[card="true"][state="running"] {{ border-left: 3px solid {p['accent']}; }}
QFrame[card="true"][state="done"] {{ border-left: 3px solid {p['ready']}; }}
QFrame[card="true"][state="failed"] {{ border-left: 3px solid {p['error']}; }}
QFrame[card="true"][state="skipped"] {{ border-left: 3px solid {p['disabled_text']}; }}
QLabel#stepBadge {{
    background: {p['text']}; color: #ffffff; border-radius: 5px;
    font-size: {FONT_AUX}px;
}}

/* ---------- 运行日志（等宽、细边框，高度由代码按需限制） ---------- */
/* 卡内"内容井"：白底 + 边框，与日志卡自身的边框形成明确的内嵌关系
   （分区策略是"白表面 + 边界线"，所以这里不再用灰底降明度） */
QTextEdit#logView {{
    font-family: {MONO_FAMILY}; font-size: {FONT_AUX}px;
    background: {p['bg']}; border: 1px solid {p['border']};
    border-radius: {RADIUS_CARD}px; padding: 8px;
}}

/* ---------- 设置分组容器（白底、细边框、13px 圆角、行间细线） ---------- */
QWidget#settingsGroup {{
    background: {p['bg']}; border: 1px solid {p['border']}; border-radius: {RADIUS_SETTINGS_CARD}px;
}}
QWidget#settingsGroup QWidget[row="true"] {{ background: transparent; }}
QWidget#settingsGroup QWidget[separator="true"] {{ background: {p['separator']}; min-height: 1px; max-height: 1px; }}

/* ---------- 分段按钮（选中项白底细边框） ---------- */
QWidget#segmented {{ background: {p['inset']}; border: 1px solid {p['border']}; border-radius: {RADIUS_CONTROL}px; }}
QWidget#segmented QPushButton {{
    background: transparent; border: none; border-radius: 4px; color: {p['text_secondary']};
    padding: 5px 12px;
}}
QWidget#segmented QPushButton:hover {{ color: {p['text']}; }}
QWidget#segmented QPushButton:checked {{ background: {p['bg']}; color: {p['text']}; border: 1px solid #e3e3e5; }}

/* ---------- 树列表（侧栏任务队列） ---------- */
QTreeWidget {{ background: transparent; border: none; outline: 0; }}
QTreeWidget::item {{ border: none; }}
QTreeWidget::branch {{ background: transparent; }}
QTreeWidget QScrollBar:vertical {{ width: 6px; }}

/* ---------- 表格（API 列表） ---------- */
QTableWidget {{ background: {p['bg']}; border: none; gridline-color: {p['separator']}; }}
QTableWidget::item {{ padding: 4px 6px; selection-background-color: {p['selected']}; selection-color: {p['text']}; }}
QTableWidget::item:selected {{ background: {p['selected']}; color: {p['text']}; }}
QHeaderView::section {{
    background: {p['sidebar']}; color: {p['text_secondary']}; border: none;
    border-bottom: 1px solid {p['separator']}; padding: 6px 8px; font-size: {FONT_AUX}px;
}}
QHeaderView::section:horizontal {{ border-right: 1px solid {p['separator']}; }}

/* ---------- 下划线页签 ---------- */
QTabBar::tab {{
    background: transparent; border: none; padding: 8px 14px; margin-right: 4px;
    color: {p['text_secondary']};
}}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {p['text']}; }}
QTabBar::tab:hover:!selected {{ color: {p['text']}; }}

/* ---------- 菜单与右键 ---------- */
QMenu {{
    background: {p['bg']}; border: 1px solid {p['separator']}; border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 20px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p['selected']}; }}
QMenu::separator {{ height: 1px; background: {p['separator']}; margin: 4px 8px; }}

/* ---------- 浮动提示 ---------- */
QWidget#toast {{
    background: {p['text']}; border-radius: {RADIUS_CONTROL}px;
}}
QWidget#toast QLabel {{ color: #ffffff; background: transparent; font-size: {FONT_AUX}px; }}

/* ---------- 分组框残留样式（旧 QGroupBox 容器统一为白底卡片） ---------- */
QGroupBox {{
    background: {p['bg']}; border: 1px solid {p['separator']}; border-radius: {RADIUS_CARD}px;
    margin-top: 8px; padding: 10px;
}}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 2px; }}
"""


def apply_theme(app: QApplication) -> None:
    """应用声笺风格：Fusion 基底 + 中文字体 + 样式表。"""
    app.setStyle("Fusion")
    configure_font(app)
    app.setStyleSheet(build_stylesheet())


def repolish(widget) -> None:
    """动态属性（kind/role/status）变更后重新求值 QSS。

    Qt 只在属性设置时求值一次；运行中改属性必须手动 unpolish/polish，
    否则界面不会更新（例如验证结果从错误文案切回成功文案）。
    """
    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class StyledWidget(QWidget):
    """需要 QSS background / border 的 QWidget 基类。

    Qt 对 **QWidget 的 Python 子类** 不会自动绘制样式表背景与边框（实例可以，
    QFrame/QDialog 也可以）。未开启 WA_StyledBackground 时，形如
    ``QWidget#settingsGroup {{ border: 1px solid ... }}`` 的规则会被静默忽略，
    控件仍按 Fusion 默认底色绘制——表现为"卡片框不见了"。

    凡是自身要靠 QSS 画底/画框的 QWidget 子类，请继承本类而不是 QWidget。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


# ---------------------------------------------------------------- 滚轮守卫
class WheelScrollGuard(QObject):
    """数值框 / 下拉框 / 滑条上的滚轮改为滚动所在页面（交互稳定性第 3 条）。

    控件获得焦点时仍可用键盘上下键修改参数；页面整体滚动由 QScrollArea 承担，
    修改参数只通过点击、拖动或输入，避免误触。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._guarded = (QComboBox, QAbstractSpinBox, QSlider)

    def guard(self, widget) -> int:
        """给 widget 及其全部需要拦截的后代控件装上守卫，返回安装数量。"""
        installed = 0
        candidates = [widget] + widget.findChildren(
            QWidget, options=Qt.FindChildOption.FindChildrenRecursively
        )
        for child in candidates:
            if isinstance(child, self._guarded):
                child.installEventFilter(self)
                installed += 1
        return installed

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and isinstance(watched, self._guarded):
            area = self._find_scroll_area(watched)
            if area is not None:
                bar = area.verticalScrollBar()
                if bar is not None and bar.maximum() > bar.minimum():
                    bar.setValue(int(bar.value() - event.angleDelta().y() / 120 * 48))
                    event.accept()
                    return True
            # 页面本身不滚动时吞掉事件，避免误改数值
            event.accept()
            return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _find_scroll_area(widget):
        parent = widget.parent()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parent()
        return None
