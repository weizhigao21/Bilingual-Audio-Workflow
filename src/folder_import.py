# -*- coding: utf-8 -*-
"""文件夹导入预览的数据规划，与界面分离以便复用和验证。"""
import os
from dataclasses import dataclass

from .task_manager import TaskQueue, generated_import_reason
from .steps.output_paths import planned_mix_output


def _inside(path, directory):
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


@dataclass(frozen=True)
class ImportCandidate:
    source_path: str
    relative_path: str
    subtitle_path: str
    output_path: str
    reason: str = ""
    locked: bool = False

    @property
    def default_selected(self):
        return not self.reason


def find_matching_subtitle(media_path: str, directory_cache=None) -> str:
    """同目录优先精确字幕名，再匹配多语言后缀；缓存目录列表避免重复扫描。"""
    directory = os.path.dirname(media_path) or "."
    stem = os.path.splitext(os.path.basename(media_path))[0].lower()
    if directory_cache is not None and directory in directory_cache:
        names = directory_cache[directory]
    else:
        try:
            names = os.listdir(directory)
        except OSError:
            names = []
        if directory_cache is not None:
            directory_cache[directory] = names
    lower_names = {name.lower(): name for name in names}
    for ext in (".lrc", ".srt", ".vtt"):
        name = lower_names.get(stem + ext)
        if name:
            return os.path.join(directory, name)
    for name in names:
        lower = name.lower()
        if lower.startswith(stem + ".") and lower.endswith((".lrc", ".srt", ".vtt")):
            return os.path.join(directory, name)
    return ""


def build_folder_import_plan(folder: str, mixer_cfg: dict,
                             existing_sources=(), workspace_dir="") -> list[ImportCandidate]:
    folder = os.path.abspath(folder)
    normalized_root = os.path.normcase(folder)
    existing = {os.path.normcase(os.path.abspath(path)) for path in existing_sources}
    workspace = os.path.normcase(os.path.abspath(workspace_dir)) if workspace_dir else ""
    custom_output = str(mixer_cfg.get("output_folder", "") or "").strip()
    directory_cache = {}
    candidates = []
    paths = TaskQueue.scan_folder_media(folder, include_generated=True)
    scanned_sources = {os.path.normcase(os.path.abspath(path)) for path in paths}
    for path in paths:
        output = planned_mix_output(path, mixer_cfg, from_folder=True)
        normalized = os.path.normcase(os.path.abspath(path))
        target = os.path.normcase(os.path.abspath(output))
        locked = normalized in existing or target in scanned_sources or target in existing
        if normalized in existing:
            reason = "已在任务队列中"
        elif target in scanned_sources or target in existing:
            reason = "预计输出将覆盖源文件，请先修改混音配置"
        elif (workspace and workspace != normalized_root
              and _inside(workspace, normalized_root)
              and _inside(normalized, workspace)):
            reason = "位于工作区目录"
        else:
            reason = generated_import_reason(folder, path, custom_output)
        candidates.append(ImportCandidate(
            path, os.path.relpath(path, folder),
            find_matching_subtitle(path, directory_cache), output, reason, locked,
        ))
    return candidates
