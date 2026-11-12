# -*- coding: utf-8 -*-
"""孤儿配音目录只读扫描：列出 workspace 中未被任何任务引用的配音目录。

只读不删：本模块绝不移动 / 重命名 / 删除任何文件。删除决策留给用户，
且删除前务必保留被 `step2_output` / `custom_mix_folder` 引用的目录。

用法：python -m src.orphan_scan [workspace 目录]（默认 ./workspace）

口径（与《项目复检报告-2026-10-10》P1-2 对账）：
  任务目录 = 顶层含 task.json 的目录
  配音目录 = 顶层不含 task.json 的目录
  孤儿     = 配音目录，且不被任何任务的 step2_output / custom_mix_folder 引用
"""
import json
import os
import sys


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _is_under(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False  # 不同盘符


def _dir_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


def scan_workspace(workspace):
    """扫描 workspace，返回报告 dict（只读，不改动任何文件）。"""
    workspace = os.path.abspath(workspace)
    task_dirs = []
    voice_dirs = []
    referenced = {}        # 归一化路径 -> [引用来源, ...]
    referenced_names = {}  # 目录名 -> [引用来源, ...]（盘符/根迁移后的兜底）
    task_errors = []

    try:
        entries = sorted(os.scandir(workspace), key=lambda e: e.name)
    except OSError as exc:
        return {"error": f"无法读取工作区: {exc}", "workspace": workspace}

    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        task_json = os.path.join(entry.path, "task.json")
        if os.path.isfile(task_json):
            task_dirs.append(entry.path)
            try:
                with open(task_json, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as exc:
                task_errors.append((entry.path, str(exc)))
                continue
            task_id = str(data.get("task_id") or os.path.basename(entry.path))
            for key in ("step2_output", "custom_mix_folder"):
                target = str(data.get(key) or "").strip()
                if not target:
                    continue
                source = f"{task_id}:{key}"
                referenced.setdefault(_norm(target), []).append(source)
                base = os.path.basename(_norm(target))
                if base:
                    referenced_names.setdefault(base, []).append(source)
        else:
            voice_dirs.append(entry.path)

    orphans = []
    referenced_dirs = []
    for path in voice_dirs:
        base = os.path.basename(_norm(path))
        refs = referenced.get(_norm(path))
        matched_by = "path"
        if not refs:
            refs = referenced_names.get(base)
            matched_by = "name"
        item = {"path": path, "bytes": _dir_size(path)}
        if refs:
            item["refs"] = refs
            item["matched_by"] = matched_by
            referenced_dirs.append(item)
        else:
            orphans.append(item)

    orphans.sort(key=lambda d: d["bytes"], reverse=True)
    ws_norm = _norm(workspace)
    external_refs = sorted(k for k in referenced if not _is_under(k, ws_norm))
    return {
        "workspace": workspace,
        "task_dirs": task_dirs,
        "voice_dirs": voice_dirs,
        "referenced_dirs": referenced_dirs,
        "orphans": orphans,
        "orphan_bytes": sum(d["bytes"] for d in orphans),
        "task_errors": task_errors,
        "external_refs": [(k, referenced[k]) for k in external_refs],
    }


def _mb(n):
    return n / 1024 / 1024


def format_report(report, list_all=True):
    """把报告 dict 渲染为可对账文本。"""
    lines = []
    if "error" in report:
        return [report["error"]]
    lines.append(f"扫描目录: {report['workspace']}")
    lines.append(f"任务目录（含 task.json）: {len(report['task_dirs'])} 个")
    lines.append(f"配音目录: {len(report['voice_dirs'])} 个")
    lines.append(f"  被任务引用: {len(report['referenced_dirs'])} 个")
    orphans = report["orphans"]
    lines.append(f"  未被引用（孤儿）: {len(orphans)} 个 → 占用 {_mb(report['orphan_bytes']):.1f} MB")
    for path, refs in report["external_refs"]:
        lines.append(f"  [受保护] 工作区外引用 {path} ← {', '.join(refs)}")
    for item in report["referenced_dirs"]:
        note = "路径已迁移，按目录名匹配" if item.get("matched_by") == "name" else "直接引用"
        lines.append(f"  [受保护] {item['path']} ← {', '.join(item['refs'])}"
                     f" ({_mb(item['bytes']):.1f} MB, {note})")
    for err_path, err in report["task_errors"]:
        lines.append(f"  [警告] task.json 解析失败: {err_path} - {err}")
    if orphans and list_all:
        lines.append("")
        lines.append("孤儿目录明细（按体积降序，只读不删）:")
        for item in orphans:
            lines.append(f"  {_mb(item['bytes']):>8.1f} MB  {item['path']}")
    lines.append("")
    lines.append("只读扫描：本脚本不删除任何文件。删除前请再次确认没有任务引用。")
    return lines


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    workspace = argv[0] if argv else "workspace"
    if not os.path.isdir(workspace):
        print(f"目录不存在: {workspace}")
        return 1
    report = scan_workspace(workspace)
    if "error" in report:
        print(report["error"])
        return 1
    for line in format_report(report):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
