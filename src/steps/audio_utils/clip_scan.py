# -*- coding: utf-8 -*-
"""片段格式只读扫描：找出「扩展名与真实格式不符」的音频文件，输出迁移报告。

只读不改：本模块绝不移动 / 重命名 / 删除任何文件。迁移决策留给用户。
用法：python -m src.steps.audio_utils.clip_scan [目录 ...]（默认扫描 workspace/）
"""
import json
import os
import sys

from .mix_files import parse_filename


def sniff_format(path):
    """读文件头判断真实格式："mp3" / "wav" / "other" / "unreadable"。

    只读前 16 字节。MP3 判定覆盖两种真实开头：ID3 标签 或 MPEG 帧同步
    （0xFF + 高 3 位全 1）；WAV 判定为 RIFF....WAVE。
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return "unreadable"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    return "other"


def scan_dir(root):
    """扫描目录树，返回迁移报告 dict（只读）。

    分类口径：
      wav_ok / mp3_ok          扩展名与真实格式一致
      wav_named_mp3            扩展名 .wav、内容实为 MP3（历史遗留，待迁移）
      mp3_named_wav            扩展名 .mp3、内容实为 WAV（反向错名，应无）
      other / unreadable       无法归类或无法读取
    """
    counts = {
        "wav_ok": 0, "mp3_ok": 0,
        "wav_named_mp3": 0, "mp3_named_wav": 0,
        "other": 0, "unreadable": 0,
    }
    misnamed = []
    misnamed_bytes = 0
    total_files = 0
    total_bytes = 0
    clip_shaped = 0

    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if not name.endswith((".wav", ".mp3")):
                continue
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                counts["unreadable"] += 1
                continue
            total_files += 1
            total_bytes += size
            if parse_filename(name):
                clip_shaped += 1

            actual = sniff_format(path)
            ext = name.rsplit(".", 1)[-1].lower()
            if actual == "unreadable":
                counts["unreadable"] += 1
                continue
            if ext == "wav" and actual == "wav":
                counts["wav_ok"] += 1
            elif ext == "mp3" and actual == "mp3":
                counts["mp3_ok"] += 1
            elif ext == "wav" and actual == "mp3":
                counts["wav_named_mp3"] += 1
                misnamed_bytes += size
                misnamed.append((os.path.relpath(path, root), size, "wav", "mp3"))
            elif ext == "mp3" and actual == "wav":
                counts["mp3_named_wav"] += 1
                misnamed_bytes += size
                misnamed.append((os.path.relpath(path, root), size, "mp3", "wav"))
            else:
                counts["other"] += 1

    return {
        "root": os.path.abspath(root),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "clip_shaped": clip_shaped,
        "counts": counts,
        "misnamed_count": counts["wav_named_mp3"] + counts["mp3_named_wav"],
        "misnamed_bytes": misnamed_bytes,
        "misnamed_samples": misnamed[:20],
    }


def format_report(report):
    """把报告渲染成人话（无键名、无内部术语）。"""
    lines = [
        f"扫描目录: {report['root']}",
        f"共检查 {report['total_files']} 个音频文件"
        f"（其中 {report['clip_shaped']} 个是标准片段命名），"
        f"合计 {report['total_bytes'] / 1024 / 1024:.1f} MB",
        f"  名实相符: WAV {report['counts']['wav_ok']} 个, "
        f"MP3 {report['counts']['mp3_ok']} 个",
        f"  扩展名与内容不符: {report['misnamed_count']} 个"
        f"（合计 {report['misnamed_bytes'] / 1024 / 1024:.1f} MB）",
    ]
    if report["counts"]["wav_named_mp3"]:
        lines.append(
            f"  其中 {report['counts']['wav_named_mp3']} 个是「MP3 内容却叫 .wav」"
            "——外部播放器/工具按扩展名识别会出错，建议改名为 .mp3"
        )
    if report["counts"]["mp3_named_wav"]:
        lines.append(
            f"  其中 {report['counts']['mp3_named_wav']} 个是「WAV 内容却叫 .mp3」"
            "——建议改名为 .wav"
        )
    if report["misnamed_samples"]:
        lines.append("示例（前 20 条）:")
        for rel, size, wrong, right in report["misnamed_samples"]:
            lines.append(f"  {rel}  ({size / 1024:.0f} KB)  .{wrong} → .{right}")
    lines.append("本报告只读生成，未改动任何文件。")
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if not argv:
        root = os.path.join(os.getcwd(), "workspace")
        if not os.path.isdir(root):
            root = os.getcwd()
        argv = [root]

    reports = [scan_dir(p) for p in argv]
    if as_json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for report in reports:
            print(format_report(report))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
