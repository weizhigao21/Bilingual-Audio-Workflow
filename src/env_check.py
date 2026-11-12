# -*- coding: utf-8 -*-
"""启动环境自检（P1-4）：后台轻量检查运行依赖，异常才提示。

原则：
- 只做轻量检查（PATH 查找、文件存在性、磁盘用量），绝不拖慢启动；
- 自检失败不阻断启动——字幕提取缺失时保持「跳过字幕提取」的既有路径；
- 文案一律人话，不暴露配置键名；磁盘读不到（网络盘/权限目录）静默跳过。
"""
import os
import shutil

# 工作区剩余空间低于此值时提示：配音片段 + 成品导出需要余量
_FREE_SPACE_WARN_GB = 2


def collect_warnings(config) -> list:
    """检查 ffmpeg / ffprobe / infer.exe / 工作区空间，返回人话警告列表。

    空列表 = 全部正常（调用方应保持沉默）。本函数不抛异常。
    """
    warnings = []

    if shutil.which("ffmpeg") is None:
        warnings.append("未找到 ffmpeg：混音导出将无法进行。请安装 ffmpeg 并加入系统 PATH。")
    if shutil.which("ffprobe") is None:
        warnings.append("未找到 ffprobe：音频信息读取将失败。请安装 ffmpeg 套件并加入系统 PATH。")

    # 字幕提取：whisper_dir 为空视为用户主动跳过（Setup 可跳过），不提示；
    # 路径已配置但程序缺失才提示——保持「跳过字幕提取」路径不被打扰。
    whisper_dir = getattr(config, "whisper_dir", "") or ""
    if whisper_dir and not os.path.isfile(os.path.join(whisper_dir, "infer.exe")):
        warnings.append(
            "字幕提取程序（infer.exe）不在所选目录中：步骤 1 将无法使用，"
            "配音与混音不受影响。可在设置中重新选择目录。")

    workspace = getattr(config, "workspace_dir", "") or ""
    if workspace:
        try:
            free_gb = shutil.disk_usage(workspace).free / 1024 ** 3
        except OSError:
            free_gb = None  # 网络盘/权限目录读不到不算异常，不提示
        if free_gb is not None and free_gb < _FREE_SPACE_WARN_GB:
            warnings.append(
                f"工作区所在磁盘剩余空间不足 {_FREE_SPACE_WARN_GB} GB"
                f"（当前 {free_gb:.1f} GB）：成品导出可能失败，建议清理磁盘。")

    return warnings
