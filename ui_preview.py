# -*- coding: utf-8 -*-
"""离屏渲染台（开发辅助，用完即删）：把各个界面截成 PNG 供人工核对。"""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt6.QtWidgets import QApplication

from src.ui_theme import apply_theme

OUT = Path("_preview")
OUT.mkdir(exist_ok=True)


def make_config(root: Path):
    tts_cfg = {
        "tts_mode": "edge",
        "edge_voice": "zh-CN-XiaoxiaoNeural",
        "edge_rate": "+0%",
        "edge_volume": "+0%",
        "api_configs": [{"name": "本地 声笺", "url": "http://127.0.0.1:8765"}],
        "current_api_index": 0,
        "output_format": "wav",
    }
    mixer_cfg = {
        "output_format": "mp4",
        "volume_db": -3.0,
        "auto_volume": "dynamic",
        "detect_channels": True,
        "align_start": True,
    }
    whisper_cfg = {
        "device": "cuda", "compute_type": "float16", "sub_formats": "lrc,vtt",
        "vad_threshold": 0.5, "vad_min_silence_duration_ms": 500,
        "vad_min_speech_duration_ms": 0, "vad_speech_pad_ms": 400,
        "merge_segments": True, "merge_max_gap_ms": 300,
        "merge_max_duration_ms": 30000, "enable_batching": False, "overwrite": False,
    }
    return SimpleNamespace(
        workspace_dir=str(root / "tasks"),
        whisper_dir="F:/faster_whisper_transwithai_windows_cu122-chickenrice",
        tts_cfg=tts_cfg, mixer_cfg=mixer_cfg, whisper_cfg=whisper_cfg,
        config={"subprojects": {"whisper_dir": "F:/faster_whisper_transwithai_windows_cu122-chickenrice"},
                "workspace_dir": str(root / "tasks"),
                "whisper": whisper_cfg, "tts": tts_cfg, "mixer": mixer_cfg,
                "setup_completed": True},
        save=lambda: True,
        needs_setup=lambda: False,
        is_configured=lambda: True,
    )


def seed_tasks(window, root: Path):
    """造一棵有层级、有状态的任务树。"""
    from src.task_manager import STEP_DONE, STEP_RUNNING, STEP_FAILED, STEP_PENDING
    queue = window.task_queue
    group_dir = root / "素材" / "第一卷"
    (group_dir / "第01话").mkdir(parents=True, exist_ok=True)
    (group_dir / "第02话").mkdir(parents=True, exist_ok=True)
    for sub, names in (("第01话", ["Track1", "Track2"]), ("第02话", ["Track10"])):
        for n in names:
            (group_dir / sub / f"{n}.mp3").write_bytes(b"\x00")
    (group_dir / "OP.mp3").write_bytes(b"\x00")
    group = queue.create_group(str(group_dir))
    ids = []
    for path in sorted(group_dir.rglob("*.mp3")):
        task = queue.add_task(str(path), group_id=group.group_id)
        ids.append(task.task_id)
    # 三个散任务
    for name in ("散-片头曲", "散-访谈", "散-备用素材"):
        p = root / f"{name}.mp4"
        p.write_bytes(b"\x00")
        ids.append(queue.add_task(str(p)).task_id)
    # 状态铺开：完成 / 进行中 / 失败 / 等待
    states = [
        (0, {"step1_status": STEP_DONE,
             "step1_output": str(root / "tasks/01_字幕/第一卷/OP.lrc"),
             "step2_status": STEP_DONE,
             "step2_output": str(root / "tasks/配音/OP"),
             "step3_status": STEP_DONE,
             "step3_output": str(root / "tasks/03_混音/素材/第一卷/OP_双语.mp4")}),
        (1, {"step1_status": STEP_DONE, "step2_status": STEP_RUNNING}),
        (2, {"step1_status": STEP_DONE, "step2_status": STEP_FAILED,
             "step2_error": "Edge 合成端点不可用"}),
    ]
    for idx, patch in states:
        task = queue.get_task(ids[idx])
        for key, value in patch.items():
            setattr(task, key, value)
        queue.update_task(task)
    queue.set_current(ids[0])
    return group


def render_window(app):
    from src.workflow_gui import WorkflowMainWindow
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        window = WorkflowMainWindow(make_config(root))
        seed_tasks(window, root)
        window.resize(1380, 900)
        window.show()
        app.processEvents()
        window.grab().save(str(OUT / "01_window.png"))
        # 侧栏单独放大
        from PyQt6.QtWidgets import QWidget
        sidebar = window.findChildren(QWidget, "sidebar")[0]
        sidebar.grab().save(str(OUT / "02_sidebar.png"))
        print("window ok")
        window.close()


def render_idle(app):
    """空态：未选任务、无输出，步骤卡显示"尚未生成"占位行。

    该行恒定占位是防抖动的关键（显隐会让卡片高矮差一个行高，切换任务
    时中央区整列跳动）。留一张空态图，方便和 01_window.png 肉眼对高度。
    """
    from src.workflow_gui import WorkflowMainWindow
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        window = WorkflowMainWindow(make_config(root))
        window.resize(1380, 900)
        window.show()
        app.processEvents()
        window.grab().save(str(OUT / "09_window_idle.png"))
        print("idle ok")
        window.close()


def render_settings(app):
    from src.workflow_gui.config_mixin import SettingsDialog
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = make_config(root)
        for tab, name in ((0, "general"), (1, "whisper"), (2, "tts"), (3, "mixer")):
            dialog = SettingsDialog(cfg, None, initial_tab=tab)
            dialog.resize(760, 640)
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(OUT / f"03_settings_{tab}_{name}.png"))
            dialog.close()
        print("settings ok")


def render_panels(app):
    from src.widgets.mixer_panel import MixerConfigPanel
    from src.widgets.tts_panel import TTSConfigPanel
    from src.widgets.whisper_panel import WhisperConfigPanel
    from src.workflow_gui.config_mixin import GeneralSettingsPage
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = make_config(root)
        for name, widget in (
            ("general", GeneralSettingsPage(cfg)),
            ("whisper", WhisperConfigPanel(cfg)),
            ("tts", TTSConfigPanel(cfg)),
            ("mixer", MixerConfigPanel(cfg)),
        ):
            widget.resize(700, 900)
            widget.show()
            app.processEvents()
            widget.grab().save(str(OUT / f"04_panel_{name}.png"))
            widget.close()
        print("panels ok")


def render_setup(app):
    from src.config import SetupDialog
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        dialog = SetupDialog(make_config(root))
        dialog.resize(720, 520)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(OUT / "05_setup.png"))
        print("setup ok")
        dialog.close()


def render_metadata(app):
    """混音成品信息编辑弹窗（需真实文件），跳过不存在的情况。"""
    try:
        from src.task_manager import TaskInfo
        from src.widgets.metadata_dialog import MetadataDialog
    except Exception as exc:  # noqa: BLE001
        print("metadata skip:", exc)
        return
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        media = root / "Track1_双语.mp4"
        media.write_bytes(b"\x00")
        task = TaskInfo(task_id="demo", source_path=str(root / "Track1.mp3"),
                        source_name="Track1", workspace_root=str(root))
        task.step3_output = str(media)
        dialog = MetadataDialog(task)
        dialog.resize(560, 620)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(OUT / "06_metadata.png"))
        print("metadata ok")
        dialog.close()


def render_sidebar_deep(app):
    """三层展开的侧栏：重点核对最深层第二行不被省略号截断。

    侧栏 208px 下第二行可用宽度按层级为 95/81/67px，这是第二行文案的
    硬预算（见 tests/test_ui_theme.py::SidebarSubtitleTests）。
    """
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from src.task_manager import TaskQueue, STEP_DONE, STEP_FAILED, STEP_RUNNING
    from src.ui_theme import SIDEBAR_PADDING, SIDEBAR_WIDTH
    from src.widgets.task_list import TaskListWidget
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        group_dir = root / "素材" / "第一卷"
        for sub, names in (("第01话", ["Track1", "Track2"]),
                           ("第02话", ["Track10"])):
            (group_dir / sub).mkdir(parents=True, exist_ok=True)
            for n in names:
                (group_dir / sub / f"{n}.mp3").write_bytes(b"\x00")
        queue = TaskQueue(str(root / "tasks"))
        group = queue.create_group(str(group_dir))
        ids = [queue.add_task(str(p), group_id=group.group_id).task_id
               for p in sorted(group_dir.rglob("*.mp3"))]
        for tid, patch in zip(ids, ({"step1_status": STEP_DONE,
                                     "step2_status": STEP_RUNNING},
                                    {"step1_status": STEP_DONE,
                                     "step2_status": STEP_FAILED,
                                     "step2_error": "Edge 合成端点不可用"},
                                    {"step1_status": STEP_DONE,
                                     "step2_status": STEP_DONE})):
            task = queue.get_task(tid)
            for key, value in patch.items():
                setattr(task, key, value)
            queue.update_task(task)

        host = QWidget()
        host.setObjectName("sidebar")
        host.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(SIDEBAR_PADDING, 16, SIDEBAR_PADDING, 12)
        tree = TaskListWidget()
        tree.set_task_queue(queue)
        tree.add_group_item(group)
        for task in queue.tasks:
            tree.add_task_item(task)
        layout.addWidget(tree, 1)
        host.resize(SIDEBAR_WIDTH, 420)
        host.show()
        for _ in range(5):
            app.processEvents()
        tree.expandAll()
        for _ in range(8):
            app.processEvents()
        host.grab().save(str(OUT / "07_sidebar_deep.png"))
        print("sidebar_deep ok")


def render_add_media(app):
    """统一添加入口弹窗（文件 / 文件夹二选一）。"""
    from src.workflow_gui.window import AddMediaDialog
    dialog = AddMediaDialog()
    dialog.resize(400, 300)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(OUT / "08_add_media.png"))
    print("add_media ok")
    dialog.close()


def main():
    app = QApplication(sys.argv)
    apply_theme(app)
    wanted = sys.argv[1] if len(sys.argv) > 1 else "all"
    jobs = {"window": render_window, "idle": render_idle,
            "settings": render_settings,
            "panels": render_panels, "setup": render_setup,
            "metadata": render_metadata, "deep": render_sidebar_deep,
            "addmedia": render_add_media}
    for name, fn in jobs.items():
        if wanted in ("all", name):
            try:
                fn(app)
            except Exception as exc:  # noqa: BLE001
                print(f"{name} FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
