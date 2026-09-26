# -*- coding: utf-8 -*-
"""任务管理：目录约定、状态记录、多任务队列数据模型。

目录约定（现行结构）：
  workspace/
    <task_id>/task.json      # 任务状态，update_task() 时落盘
    <字幕MD5[:8]>/*.wav      # TTS 输出，按字幕内容 MD5 寻址（见 tts_worker）
    源文件同目录/*.lrc       # 字幕与混音输出均落在源文件所在目录

任务状态写入 task.json，启动时可恢复任务与文件夹组。
"""
import os
import re
import json
import time
import hashlib
import shutil
import tempfile
import logging
import uuid
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict, fields
from typing import Optional
from .steps.tts_profile import profile_matches

from PyQt6.QtCore import QObject, pyqtSignal


# 步骤状态枚举
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"


@dataclass
class TaskInfo:
    """单个任务的信息。"""
    task_id: str                       # 目录名（时间戳_视频名）
    source_path: str                   # 原始视频/音频路径
    source_name: str                   # 视频名（不含扩展名）
    workspace_root: str                # workspace 根目录
    created_at: str = ""               # 创建时间

    # 用户自定义输入（跳过对应步骤）
    custom_subtitle: str = ""          # 自定义字幕文件路径（跳过步骤1）
    custom_mix_folder: str = ""        # 自定义配音目录（跳过步骤2）

    # 各步骤状态
    step1_status: str = STEP_PENDING   # 字幕提取
    step2_status: str = STEP_PENDING   # 语音生成
    step3_status: str = STEP_PENDING   # 音频混音

    # 各步骤输出（成功后填入）
    step1_output: str = ""             # 字幕文件路径
    step2_output: str = ""             # 配音目录（task_id 子目录）
    step3_output: str = ""             # 混音输出文件

    # 错误信息
    step1_error: str = ""
    step2_error: str = ""
    step3_error: str = ""
    force_remix: bool = False             # TTS 参数变化后已有混音需重做

    from_folder: bool = False           # 是否来自文件夹导入（用于混音输出前缀判断）
    import_folder: str = ""             # 拖入的原始文件夹路径（用于完成后的重命名）
    group_id: str = ""                  # 所属文件夹任务组 ID（空=散任务）

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 目录路径
    @property
    def task_dir(self) -> str:
        return os.path.join(self.workspace_root, self.task_id)

    @property
    def subtitle_dir(self) -> str:
        return os.path.join(self.task_dir, "01_字幕")

    @property
    def tts_dir(self) -> str:
        return self.task_dir

    @property
    def mixer_dir(self) -> str:
        return os.path.join(self.task_dir, "03_混音")

    @property
    def task_json_path(self) -> str:
        return os.path.join(self.task_dir, "task.json")

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self):
        """将任务状态持久化到 task.json。"""
        task_dir = self.task_dir
        if not os.path.exists(task_dir):
            os.makedirs(task_dir, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=task_dir,
                                             prefix=".task-", suffix=".json", delete=False) as f:
                temp_path = f.name
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.task_json_path)
        except OSError as e:
            logging.getLogger(__name__).warning("保存任务状态失败: %s", e)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def forget(self):
        """仅撤销任务恢复记录，不删除任务目录或生成文件。"""
        try:
            os.unlink(self.task_json_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logging.getLogger(__name__).warning("移除任务记录失败: %s", e)

    def step_status(self, step: int) -> str:
        return [self.step1_status, self.step2_status, self.step3_status][step - 1]

    def set_step_status(self, step: int, status: str):
        if step == 1: self.step1_status = status
        elif step == 2: self.step2_status = status
        elif step == 3: self.step3_status = status

    def set_step_output(self, step: int, output: str):
        if step == 1: self.step1_output = output
        elif step == 2: self.step2_output = output
        elif step == 3: self.step3_output = output

    def set_step_error(self, step: int, error: str):
        if step == 1: self.step1_error = error
        elif step == 2: self.step2_error = error
        elif step == 3: self.step3_error = error

    def is_step_ready(self, step: int) -> bool:
        """检查某步骤是否可以执行（前置步骤已完成或跳过）。"""
        def _prev_done(s):
            return s in (STEP_DONE, STEP_SKIPPED)
        if step == 1:
            return True
        if step == 2:
            return _prev_done(self.step1_status)
        if step == 3:
            return _prev_done(self.step2_status)
        return False

    def apply_custom_inputs(self, tts_config=None):
        """检测已存在的文件，自动跳过对应步骤。

        检测顺序：
        1. 自定义字幕（custom_subtitle）→ 跳过步骤1
        2. 字幕MD5文件夹中是否有已生成的语音文件 → 跳过步骤2
        3. 自定义配音目录（custom_mix_folder）→ 跳过步骤2
        4. 源文件目录下的"双语"文件夹中是否有已生成的混音文件 → 跳过步骤3
        """
        # --- 步骤1：检测字幕文件 ---
        if self.custom_subtitle and os.path.exists(self.custom_subtitle):
            self.step1_status = STEP_SKIPPED
            self.step1_output = self.custom_subtitle

        # --- 步骤2：检测已生成的语音文件 ---
        # 优先检测自定义配音目录
        if self.custom_mix_folder and os.path.isdir(self.custom_mix_folder):
            self.step2_status = STEP_SKIPPED
            self.step2_output = self.custom_mix_folder
        # 没有自定义配音时，检测字幕MD5文件夹
        elif self.step1_status == STEP_SKIPPED:
            tts_dir = self._detect_tts_by_subtitle_md5(tts_config)
            if tts_dir:
                self.step2_status = STEP_SKIPPED
                self.step2_output = tts_dir
            elif tts_config is not None and self.step2_status == STEP_SKIPPED:
                self.step2_status = STEP_PENDING
                self.step2_output = ""
            if not tts_dir and tts_config is not None:
                self.force_remix = bool(self._detect_mix_output())
                if self.step3_status == STEP_SKIPPED:
                    self.step3_status = STEP_PENDING
                    self.step3_output = ""

        # --- 步骤3：检测已生成的混音文件 ---
        mix_output = self._detect_mix_output() if self.step2_status in (STEP_DONE, STEP_SKIPPED) else ""
        if mix_output:
            self.step3_status = STEP_SKIPPED
            self.step3_output = mix_output

    def _detect_tts_by_subtitle_md5(self, tts_config=None):
        """根据字幕文件MD5检查 workspace 中是否有已生成的语音文件。

        Returns:
            str: 语音文件夹路径，未找到则返回空字符串
        """
        if not self.step1_output or not os.path.exists(self.step1_output):
            return ""
        try:
            with open(self.step1_output, 'rb') as f:
                md5 = hashlib.md5(f.read()).hexdigest()[:8]
            tts_dir = os.path.join(self.workspace_root, md5)
            if os.path.isdir(tts_dir) and (tts_config is None or profile_matches(tts_dir, tts_config)):
                wavs = [f for f in os.listdir(tts_dir) if f.endswith(".wav")]
                if wavs:
                    return tts_dir
        except Exception:
            pass
        return ""

    def _detect_mix_output(self):
        """检查已有混音输出，兼容三种目录结构：
        1. 源目录下"双语"子目录（默认）
        2. 父目录下"双语-<源文件夹名>"（输出前缀配置开启时的输出位置）
        3. 源目录本身已带"双语-"前缀时，输出直接在其根目录

        Returns:
            str: 混音文件路径，未找到则返回空字符串
        """
        source_dir = os.path.dirname(self.source_path)
        candidates = []
        if self.from_folder and os.path.basename(source_dir).startswith("双语-"):
            # 源文件夹已带前缀：输出直接在其根目录
            candidates.append(source_dir)
        else:
            candidates.append(os.path.join(source_dir, "双语"))
            if self.from_folder:
                parent = os.path.dirname(source_dir)
                dir_name = os.path.basename(source_dir)
                if parent and dir_name:
                    candidates.append(os.path.join(parent, f"双语-{dir_name}"))
        # 找匹配源文件名的输出，常见格式
        common_exts = {".mp4", ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".mkv", ".aac"}
        for bilingual_dir in candidates:
            if not os.path.isdir(bilingual_dir):
                continue
            for c in os.listdir(bilingual_dir):
                c_path = os.path.join(bilingual_dir, c)
                if not os.path.isfile(c_path):
                    continue
                name, ext = os.path.splitext(c)
                if ext.lower() not in common_exts:
                    continue
                # 匹配 source_name 或 source_name_mixed
                stem = name.removesuffix("_mixed")
                if stem == self.source_name:
                    return c_path
        return ""

    def overall_progress(self) -> float:
        """整体进度 0~1。"""
        scores = {
            STEP_PENDING: 0.0, STEP_RUNNING: 0.0,
            STEP_FAILED: 0.0, STEP_SKIPPED: 1.0, STEP_DONE: 1.0,
        }
        return (scores[self.step1_status] + scores[self.step2_status] + scores[self.step3_status]) / 3.0


@dataclass
class TaskGroup:
    """文件夹任务组：包含一个文件夹内扫描出的多个音频任务。

    组节点只作为组织与汇总容器，真正执行时遍历其 tasks 逐个处理。
    """
    group_id: str                       # 唯一 ID
    group_name: str                     # 文件夹名
    folder_path: str                    # 文件夹绝对路径
    tasks: list = field(default_factory=list)   # List[TaskInfo]

    def add_task(self, task: TaskInfo):
        self.tasks.append(task)

    def remove_task(self, task_id: str):
        for i, t in enumerate(self.tasks):
            if t.task_id == task_id:
                self.tasks.pop(i)
                break

    def progress(self) -> float:
        """组整体进度 0~1（子任务整体进度的平均）。"""
        if not self.tasks:
            return 0.0
        return sum(t.overall_progress() for t in self.tasks) / len(self.tasks)

    def done_count(self) -> int:
        """已完成混音的子任务数。"""
        return sum(1 for t in self.tasks if t.step3_status == STEP_DONE)

    def overall_status(self) -> str:
        if not self.tasks:
            return STEP_PENDING
        if any(t.step_status(s) == STEP_RUNNING for t in self.tasks for s in (1, 2, 3)):
            return STEP_RUNNING
        if all(t.step3_status == STEP_DONE for t in self.tasks):
            return STEP_DONE
        if any(t.step_status(s) == STEP_FAILED for t in self.tasks for s in (1, 2, 3)):
            return STEP_FAILED
        return STEP_PENDING


def _sanitize_name(name: str) -> str:
    """清理文件名中的非法字符。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def create_task(workspace_root: str, source_path: str,
                subtitle_path: str = "", mix_folder: str = "", tts_config=None) -> TaskInfo:
    """根据源视频/音频路径创建新任务。

    Args:
        workspace_root: workspace 根目录
        source_path: 原始视频/音频路径
        subtitle_path: 可选，自定义字幕文件（跳过步骤1）
        mix_folder: 可选，自定义配音目录（跳过步骤2）
    """
    source_name = os.path.splitext(os.path.basename(source_path))[0]
    source_name = _sanitize_name(source_name)
    # Windows 时钟可能连续两次返回相同微秒；追加随机标识避免任务目录碰撞。
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    task_id = f"{timestamp}_{uuid.uuid4().hex[:8]}_{source_name}"

    task = TaskInfo(
        task_id=task_id,
        source_path=os.path.abspath(source_path),
        source_name=source_name,
        workspace_root=workspace_root,
        custom_subtitle=os.path.abspath(subtitle_path) if subtitle_path else "",
        custom_mix_folder=os.path.abspath(mix_folder) if mix_folder else "",
    )
    # 应用自定义输入（检测已存在文件，自动跳过对应步骤）
    task.apply_custom_inputs(tts_config)
    return task


def _natural_key(s: str) -> list:
    """自然排序 key：把字符串中的数字段按整数解析，使 Track2 排在 Track10 之前。"""
    parts = re.split(r"(\d+)", s)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def sort_tasks_by_name(tasks: list) -> list:
    """按 source_name 自然排序（升序），返回新列表。"""
    return sorted(tasks, key=lambda t: _natural_key(t.source_name))



class TaskQueue(QObject):
    """多任务队列模型（Qt 信号通知变化）。"""
    task_added = pyqtSignal(object)       # TaskInfo
    task_removed = pyqtSignal(str)        # task_id
    task_updated = pyqtSignal(object)     # TaskInfo
    current_changed = pyqtSignal(object)  # 当前选中任务（可能为 None）
    group_added = pyqtSignal(object)      # TaskGroup
    group_removed = pyqtSignal(str)       # group_id

    # 源媒体文件扩展名（与 GUI 保持一致）
    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts",
                  ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}

    def __init__(self, workspace_root: str, tts_config=None):
        super().__init__()
        self.workspace_root = workspace_root
        self.tts_config = tts_config
        self.tasks: list = []            # 所有叶子任务（散任务 + 组内任务，扁平）
        self.groups: list = []           # List[TaskGroup]
        self._current: Optional[TaskInfo] = None

    def restore_tasks(self) -> tuple:
        """从工作区的 task.json 恢复任务和分组，返回(恢复数,忽略数)。"""
        if not os.path.isdir(self.workspace_root):
            return 0, 0
        restored = []
        ignored = 0
        names = {f.name for f in fields(TaskInfo)}
        existing = {task.task_id for task in self.tasks}
        for entry in os.scandir(self.workspace_root):
            if not entry.is_dir(follow_symlinks=False):
                continue
            state_path = os.path.join(entry.path, "task.json")
            if not os.path.isfile(state_path):
                continue
            try:
                with open(state_path, encoding="utf-8") as f:
                    data = json.load(f)
                if (not isinstance(data, dict) or data.get("task_id") != entry.name
                        or not isinstance(data.get("source_path"), str)
                        or not isinstance(data.get("source_name"), str)
                        or entry.name in existing):
                    raise ValueError("任务记录字段无效或 ID 重复")
                data = {key: value for key, value in data.items() if key in names}
                data["workspace_root"] = self.workspace_root
                task = TaskInfo(**data)
                if self._reconcile_restored_task(task):
                    task.save()
                restored.append(task)
                existing.add(task.task_id)
            except (OSError, ValueError, TypeError) as e:
                logging.getLogger(__name__).warning("忽略无效任务记录 %s: %s", state_path, e)
                ignored += 1

        # 先恢复分组，再发任务信号；列表控件需要先有父节点。
        for task in restored:
            if task.group_id and not self.get_group(task.group_id):
                folder = task.import_folder or os.path.dirname(task.source_path)
                group = TaskGroup(task.group_id, os.path.basename(folder) or folder, folder)
                self.groups.append(group)
                self.group_added.emit(group)
        for task in sorted(restored, key=lambda t: (_natural_key(t.source_name), t.task_id)):
            group = self.get_group(task.group_id) if task.group_id else None
            if group:
                group.add_task(task)
            self.tasks.append(task)
            self.task_added.emit(task)
        return len(restored), ignored

    def _reconcile_restored_task(self, task: TaskInfo) -> bool:
        """将中断与失效的输出调整为可重试状态。"""
        changed = False
        valid = {STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, STEP_SKIPPED}
        for step in (1, 2, 3):
            status = task.step_status(step)
            output = task.step1_output if step == 1 else task.step2_output if step == 2 else task.step3_output
            available = os.path.isdir(output) if step == 2 else os.path.isfile(output)
            if status not in valid or status == STEP_RUNNING or (status in (STEP_DONE, STEP_SKIPPED) and not available):
                task.set_step_status(step, STEP_PENDING)
                task.set_step_output(step, "")
                if status == STEP_RUNNING:
                    task.set_step_error(step, "上次运行中断，可重新执行")
                changed = True
        if task.step2_status in (STEP_DONE, STEP_SKIPPED) and not task.custom_mix_folder:
            if self.tts_config is not None and not profile_matches(task.step2_output, self.tts_config):
                task.step2_status = STEP_PENDING
                task.step2_output = ""
                task.force_remix = True
                changed = True
        if task.step1_status not in (STEP_DONE, STEP_SKIPPED) and not task.custom_mix_folder:
            if task.step2_status in (STEP_DONE, STEP_SKIPPED):
                task.step2_status = STEP_PENDING
                task.step2_output = ""
                changed = True
        if task.step2_status not in (STEP_DONE, STEP_SKIPPED):
            if task.step3_status in (STEP_DONE, STEP_SKIPPED):
                task.step3_status = STEP_PENDING
                task.step3_output = ""
                changed = True
            if task._detect_mix_output() and not task.force_remix:
                task.force_remix = True
                changed = True
        return changed

    def add_task(self, source_path: str,
                 subtitle_path: str = "", mix_folder: str = "",
                 group_id: str = "") -> TaskInfo:
        """添加新任务，按 source_name 插入到正确位置保持列表有序。

        group_id 非空时任务加入对应文件夹组，并自动标记 from_folder。
        """
        task = create_task(self.workspace_root, source_path,
                           subtitle_path=subtitle_path, mix_folder=mix_folder,
                           tts_config=self.tts_config)
        if group_id:
            group = self.get_group(group_id)
            if group:
                task.group_id = group_id
                task.from_folder = True
                task.import_folder = group.folder_path
                group.add_task(task)
        # 按 source_name 自然顺序找到插入位置（二分查找：
        # 任务量大时避免每次插入都线性全扫已有任务）
        new_key = _natural_key(task.source_name)
        lo, hi = 0, len(self.tasks)
        while lo < hi:
            mid = (lo + hi) // 2
            if _natural_key(self.tasks[mid].source_name) <= new_key:
                lo = mid + 1
            else:
                hi = mid
        self.tasks.insert(lo, task)
        task.save()
        self.task_added.emit(task)
        return task

    # ---------- 文件夹组管理 ----------
    @staticmethod
    def scan_folder_media(folder: str) -> list:
        """递归扫描文件夹中的媒体文件，返回绝对路径列表（自然排序）。"""
        media = []
        for root, dirs, files in os.walk(folder):
            # 默认混音输出位于源目录的“双语/”内；重复导入时不能再当源媒体。
            dirs[:] = [d for d in dirs if d != "双语"]
            for f in files:
                if os.path.splitext(f)[1].lower() in TaskQueue.VIDEO_EXTS:
                    media.append(os.path.join(root, f))
        return sorted(media, key=_natural_key)

    def create_group(self, folder_path: str) -> TaskGroup:
        """创建文件夹任务组（不含任务），并通知界面。"""
        folder_path = os.path.abspath(folder_path)
        group = TaskGroup(
            group_id=f"grp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.groups)}",
            group_name=os.path.basename(folder_path) or folder_path,
            folder_path=folder_path,
        )
        self.groups.append(group)
        self.group_added.emit(group)
        return group

    def get_group(self, group_id: str) -> Optional[TaskGroup]:
        for g in self.groups:
            if g.group_id == group_id:
                return g
        return None

    def remove_group(self, group_id: str):
        """移除文件夹组及其全部子任务（仅列表，不删目录）。"""
        group = self.get_group(group_id)
        if not group:
            return
        for t in list(group.tasks):
            self.remove_task(t.task_id)
        self.groups.remove(group)
        self.group_removed.emit(group_id)

    def set_task_custom_subtitle(self, task_id: str, subtitle_path: str):
        """为已有任务设置/更新自定义字幕（跳过步骤1）。"""
        task = self.get_task(task_id)
        if not task:
            return
        task.custom_subtitle = os.path.abspath(subtitle_path) if subtitle_path else ""
        # 清除字幕时恢复步骤1为待处理
        if not task.custom_subtitle and task.step1_status == STEP_SKIPPED:
            task.step1_status = STEP_PENDING
            task.step1_output = ""
        # 重置后续步骤状态，重新执行文件检测
        for s in (2, 3):
            if task.step_status(s) not in (STEP_DONE, STEP_SKIPPED):
                task.set_step_status(s, STEP_PENDING)
        task.set_step_output(1, "")
        task.set_step_error(1, "")
        task.apply_custom_inputs(self.tts_config)
        self.update_task(task)

    def set_task_custom_mix_folder(self, task_id: str, mix_folder: str):
        """为已有任务设置/更新自定义配音目录（跳过步骤2）。"""
        task = self.get_task(task_id)
        if not task:
            return
        task.custom_mix_folder = os.path.abspath(mix_folder) if mix_folder else ""
        # 重置步骤2，重新执行文件检测
        if task.step2_status not in (STEP_DONE,):
            task.step2_status = STEP_PENDING
            task.step2_output = ""
        if task.step3_status not in (STEP_DONE,):
            task.step3_status = STEP_PENDING
            task.step3_output = ""
        task.apply_custom_inputs(self.tts_config)
        self.update_task(task)

    def remove_task(self, task_id: str):
        """移除任务（仅从列表移除，不删目录）。"""
        for i, t in enumerate(self.tasks):
            if t.task_id == task_id:
                self.tasks.pop(i)
                t.forget()
                # 同步从所属文件夹组移除
                if t.group_id:
                    group = self.get_group(t.group_id)
                    if group:
                        group.remove_task(task_id)
                self.task_removed.emit(task_id)
                if self._current and self._current.task_id == task_id:
                    self._current = self.tasks[0] if self.tasks else None
                    self.current_changed.emit(self._current)
                break

    def clear_all(self, delete_files: bool = False) -> int:
        """清空所有任务。

        Args:
            delete_files: True 时同时删除各任务在 workspace 下的任务目录
                （含 task.json）。混音输出与语音缓存按内容寻址、可被其他
                任务复用，不在删除范围内。

        Returns:
            实际删除的任务目录数（delete_files=False 时为 0）。
        """
        removed_ids = [t.task_id for t in self.tasks]
        removed_group_ids = [g.group_id for g in self.groups]
        deleted_dirs = 0
        if delete_files:
            for task in self.tasks:
                task_dir = task.task_dir
                if os.path.isdir(task_dir):
                    try:
                        shutil.rmtree(task_dir)
                        deleted_dirs += 1
                    except Exception:
                        pass
        for task in self.tasks:
            task.forget()
        self.tasks.clear()
        self.groups.clear()
        self._current = None
        for tid in removed_ids:
            self.task_removed.emit(tid)
        for gid in removed_group_ids:
            self.group_removed.emit(gid)
        self.current_changed.emit(None)
        return deleted_dirs

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def set_current(self, task_id: Optional[str]):
        if task_id is None:
            self._current = None
        else:
            self._current = self.get_task(task_id)
        self.current_changed.emit(self._current)

    @property
    def current(self) -> Optional[TaskInfo]:
        return self._current

    def update_task(self, task: TaskInfo):
        """通知任务状态变化，并持久化到磁盘。"""
        task.save()
        self.task_updated.emit(task)
