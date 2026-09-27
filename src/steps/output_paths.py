# -*- coding: utf-8 -*-
"""混音输出路径规划；导入预览与实际混音共用同一套规则。"""
import os


def planned_mix_output(source_path, cfg, from_folder=False):
    source_path = os.path.abspath(source_path)
    custom_output = str(cfg.get("output_folder", "") or "").strip()
    if custom_output:
        output_folder = custom_output
    elif from_folder and cfg.get("output_folder_prefix", False):
        src_dir = os.path.dirname(source_path)
        parent = os.path.dirname(src_dir)
        dir_name = os.path.basename(src_dir)
        output_folder = (src_dir if dir_name.startswith("双语-") else
                         os.path.join(parent, f"双语-{dir_name}"))
    else:
        output_folder = os.path.join(os.path.dirname(source_path), "双语")
    name = os.path.splitext(os.path.basename(source_path))[0].strip()
    suffix = "_mixed" if cfg.get("add_suffix", True) else ""
    is_video = os.path.splitext(source_path)[1].lower() in {
        ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts"
    }
    output_format = cfg.get("output_format", "mp4" if is_video else "mp3")
    return os.path.join(output_folder, f"{name}{suffix}.{output_format}")
