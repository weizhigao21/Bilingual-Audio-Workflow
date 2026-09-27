# -*- coding: utf-8 -*-
"""混音成品的可见标签与跨格式制作记录。"""
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from ..version import APP_VERSION
from .audio_utils.ffmpeg_utils import _run_ffmpeg


TAG_KEYS = ("title", "artist", "album", "comment")
EMBEDDED_FORMATS = {"mp3", "m4a", "mp4", "ogg"}
SOFTWARE_MARKER = f"双语音声工作流 {APP_VERSION}"
MIX_MARKER = f"混音：{SOFTWARE_MARKER}"
EDIT_MARKER = f"信息编辑：{SOFTWARE_MARKER}"
MARKER_PATTERN = re.compile(r"(?:混音：|信息编辑：)?双语音声工作流 v[\w.+-]+")
MIX_SETTING_KEYS = (
    "volume_db", "auto_volume", "channel_detect", "channel_map",
    "align_onset", "content_alignment", "peak_mode",
    "high_quality_resample", "audio_bitrate", "audio_sample_rate",
    "audio_channels", "wav_bit_depth", "output_format",
    "metadata_enabled", "metadata_fields", "metadata_values",
)


def sidecar_path(output_path):
    return os.fspath(output_path) + ".mix.json"


def supports_embedded_tags(output_path):
    return os.path.splitext(output_path)[1].lower().lstrip(".") in EMBEDDED_FORMATS


def normalize_tags(tags):
    """只接受界面暴露的文本字段，不将任意字段传给封装器。"""
    return {key: str((tags or {}).get(key) or "").strip() for key in TAG_KEYS}


def _strip_software_marker(comment):
    lines = str(comment or "").splitlines()
    if lines and MARKER_PATTERN.fullmatch(lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def metadata_args(tags, marker=MIX_MARKER, fields=None, include_version=True):
    """构造 FFmpeg 输出标签；可按混音配置选择字段。"""
    tags = normalize_tags(tags)
    if fields is None:
        fields = {key: True for key in TAG_KEYS}
    args = []
    if fields.get("comment") or include_version:
        comment = tags["comment"] if fields.get("comment") else ""
        if include_version:
            comment = f"{comment}\n{marker}" if comment else marker
        args += ["-metadata", f"comment={comment}"]
    if include_version:
        args += ["-metadata", f"encoded_by={marker}"]
    for key in ("title", "artist", "album"):
        if fields.get(key):
            args += ["-metadata", f"{key}={tags[key]}"]
    return args


def mix_metadata_options(cfg, task, output_path):
    """按用户设置生成作品信息；关闭时仍由制作记录保存混音版本。"""
    if not cfg.get("metadata_enabled", True):
        return {}, []
    fields = cfg.get("metadata_fields") or {}
    defaults = {"title": True, "artist": True, "album": True,
                "comment": True, "version": True}
    fields = {key: bool(fields.get(key, value)) for key, value in defaults.items()}
    values = cfg.get("metadata_values") or {}
    previous = read_editable_tags(output_path, fallback_title=task.source_name)
    tags = {}
    for key in TAG_KEYS:
        if not fields[key]:
            continue
        template = str(values.get(key, "{文件名}" if key == "title" else "") or "")
        if key == "title" and template == "{文件名}":
            # 保留混音后手工编辑过的单文件标题。
            tags[key] = previous[key]
        elif not template:
            tags[key] = previous[key]
        else:
            tags[key] = template.replace("{文件名}", task.source_name)
    return tags, ["-map_metadata", "-1"] + metadata_args(
        tags, fields=fields, include_version=fields["version"]
    )


def _read_sidecar(output_path):
    try:
        with open(sidecar_path(output_path), encoding="utf-8") as source:
            record = json.load(source)
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


def _marker_for_edit(output_path):
    record = _read_sidecar(output_path)
    if record.get("app_version"):
        return f"混音：双语音声工作流 {record['app_version']}"
    embedded = _probe_embedded_tags(output_path)
    lines = str(embedded.get("comment") or "").splitlines()
    if lines and MARKER_PATTERN.fullmatch(lines[-1].strip()):
        return lines[-1].strip()
    return EDIT_MARKER


def _probe_embedded_tags(output_path):
    if not supports_embedded_tags(output_path):
        return {}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags",
             "-of", "json", output_path], capture_output=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if proc.returncode != 0:
            return {}
        info = json.loads(proc.stdout)
        return {str(k).lower(): v for k, v in info.get("format", {}).get("tags", {}).items()}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def read_editable_tags(output_path, fallback_title=""):
    """可嵌入格式优先读取成品标签，以反映外部程序做过的编辑。"""
    record = _read_sidecar(output_path)
    stored = record.get("metadata")
    tags = normalize_tags(stored if isinstance(stored, dict) else {})
    if os.path.isfile(output_path):
        embedded = _probe_embedded_tags(output_path)
        for key in TAG_KEYS:
            if key in embedded:
                tags[key] = str(embedded[key]).strip()
    tags["comment"] = _strip_software_marker(tags["comment"])
    if not tags["title"]:
        tags["title"] = fallback_title
    return tags


def write_provenance(output_path, task, cfg, tags, edited=False):
    """同目录原子写入制作记录；不记录 API 密钥或本机绝对路径。"""
    record = _read_sidecar(output_path) if edited else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not record:
        record = {
            "schema_version": 1,
            "source_file": os.path.basename(task.source_path),
            "output_file": os.path.basename(output_path),
            "mix_settings": {key: cfg[key] for key in MIX_SETTING_KEYS if key in cfg}
            if not edited else {},
        }
        record["mixed_at_utc" if not edited else "recorded_at_utc"] = now
    if edited:
        record["metadata_editor_version"] = APP_VERSION
    else:
        record["app_version"] = APP_VERSION
    record["metadata"] = normalize_tags(tags)
    if edited:
        record["metadata_updated_at_utc"] = now
    target = sidecar_path(output_path)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=os.path.dirname(target),
            prefix=".mix-info-", suffix=".json", delete=False,
        ) as output:
            temp_path = output.name
            json.dump(record, output, ensure_ascii=False, indent=2)
        os.replace(temp_path, target)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def edit_output_metadata(output_path, tags, task, stop_check=None):
    """支持标签的格式无损重封装；WAV/ADTS AAC 只更新制作记录。"""
    if not os.path.isfile(output_path):
        raise FileNotFoundError(output_path)
    if stop_check is not None and stop_check():
        raise RuntimeError("用户已停止处理")
    tags = normalize_tags(tags)
    if supports_embedded_tags(output_path):
        marker = _marker_for_edit(output_path)
        suffix = os.path.splitext(output_path)[1]
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=os.path.dirname(output_path), prefix=".mix-tags-",
                suffix=suffix, delete=False,
            ) as output:
                temp_path = output.name
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-i", output_path, "-map", "0", "-c", "copy",
                   "-map_metadata", "0"] + metadata_args(tags, marker) + [temp_path]
            _run_ffmpeg(cmd, stop_check=stop_check)
            if stop_check is not None and stop_check():
                raise RuntimeError("用户已停止处理")
            os.replace(temp_path, output_path)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        try:
            write_provenance(output_path, task, {}, tags, edited=True)
        except OSError as exc:
            return f"文件标签已保存，但制作记录写入失败: {exc}"
    else:
        write_provenance(output_path, task, {}, tags, edited=True)
    return ""
