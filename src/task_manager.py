# -*- coding: utf-8 -*-
"""任务管理：目录约定、状态记录、多任务队列数据模型。

任务目录结构：
  workspace/
    20260720_150000_视频名/
      01_字幕/视频.lrc
      02_配音/<task_id>/视频名/*.wav
      03_混音/视频_mixed.mp4
      task.json
"""
import os
import re
import json
import time
import hashlib
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

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

    from_folder: bool = False           # 是否来自文件夹导入（用于混音输出前缀判断）
    import_folder: str = ""             # 拖入的原始文件夹路径（用于完成后的重命名）

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

    @classmethod
    def from_dict(cls, d: dict) -> "TaskInfo":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)

    def save(self):
        """将任务状态持久化到 task.json。"""
        task_dir = self.task_dir
        if not os.path.exists(task_dir):
            os.makedirs(task_dir, exist_ok=True)
        try:
            with open(self.task_json_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

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

    def apply_custom_inputs(self):
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
            tts_dir = self._detect_tts_by_subtitle_md5()
            if tts_dir:
                self.step2_status = STEP_SKIPPED
                self.step2_output = tts_dir

        # --- 步骤3：检测已生成的混音文件 ---
        mix_output = self._detect_mix_output()
        if mix_output:
            self.step3_status = STEP_SKIPPED
            self.step3_output = mix_output

    def _detect_tts_by_subtitle_md5(self):
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
            if os.path.isdir(tts_dir):
                wavs = [f for f in os.listdir(tts_dir) if f.endswith(".wav")]
                if wavs:
                    return tts_dir
        except Exception:
            pass
        return ""

    def _detect_mix_output(self):
        """检查源文件目录下"双语"文件夹是否有已生成的混音文件。

        Returns:
            str: 混音文件路径，未找到则返回空字符串
        """
        source_dir = os.path.dirname(self.source_path)
        bilingual_dir = os.path.join(source_dir, "双语")
        if not os.path.isdir(bilingual_dir):
            return ""
        # 找匹配源文件名的输出，常见格式
        common_exts = {".mp4", ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".mkv"}
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


def _sanitize_name(name: str) -> str:
    """清理文件名中的非法字符。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def create_task(workspace_root: str, source_path: str,
                subtitle_path: str = "", mix_folder: str = "") -> TaskInfo:
    """根据源视频/音频路径创建新任务。

    Args:
        workspace_root: workspace 根目录
        source_path: 原始视频/音频路径
        subtitle_path: 可选，自定义字幕文件（跳过步骤1）
        mix_folder: 可选，自定义配音目录（跳过步骤2）
    """
    source_name = os.path.splitext(os.path.basename(source_path))[0]
    source_name = _sanitize_name(source_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    task_id = f"{timestamp}_{source_name}"

    task = TaskInfo(
        task_id=task_id,
        source_path=os.path.abspath(source_path),
        source_name=source_name,
        workspace_root=workspace_root,
        custom_subtitle=os.path.abspath(subtitle_path) if subtitle_path else "",
        custom_mix_folder=os.path.abspath(mix_folder) if mix_folder else "",
    )
    # 应用自定义输入（检测已存在文件，自动跳过对应步骤）
    task.apply_custom_inputs()
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

    def __init__(self, workspace_root: str):
        super().__init__()
        self.workspace_root = workspace_root
        self.tasks: list = []
        self._current: Optional[TaskInfo] = None

    def add_task(self, source_path: str,
                 subtitle_path: str = "", mix_folder: str = "") -> TaskInfo:
        """添加新任务，按 source_name 插入到正确位置保持列表有序。"""
        task = create_task(self.workspace_root, source_path,
                           subtitle_path=subtitle_path, mix_folder=mix_folder)
        # 按 source_name 自然顺序找到插入位置
        new_key = _natural_key(task.source_name)
        insert_idx = 0
        for i, t in enumerate(self.tasks):
            if _natural_key(t.source_name) <= new_key:
                insert_idx = i + 1
            else:
                break
        self.tasks.insert(insert_idx, task)
        self.task_added.emit(task)
        return task

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
        task.apply_custom_inputs()
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
        task.apply_custom_inputs()
        self.update_task(task)

    def remove_task(self, task_id: str):
        """移除任务（仅从列表移除，不删目录）。"""
        for i, t in enumerate(self.tasks):
            if t.task_id == task_id:
                self.tasks.pop(i)
                self.task_removed.emit(task_id)
                if self._current and self._current.task_id == task_id:
                    self._current = self.tasks[0] if self.tasks else None
                    self.current_changed.emit(self._current)
                break

    def clear_all(self, delete_files: bool = False):
        """清空所有任务。"""
        removed_ids = [t.task_id for t in self.tasks]
        self.tasks.clear()
        self._current = None
        for tid in removed_ids:
            self.task_removed.emit(tid)
        self.current_changed.emit(None)

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
