"""将 infer.exe 的逐文件日志换算为整批、单调递增的识别进度。"""
import re


_START = re.compile(r"正在翻译[（(](\d+)/(\d+)[）)]")
_VAD = re.compile(r"VAD进度[：:].*?[（(](\d+(?:\.\d+)?)%")
_DURATION = re.compile(r"时长[：:]\s*([^→]+)\s*→")
_SEGMENT = re.compile(r"\[(\d+:\d+(?::\d+)?\.\d+)\s*-->\s*(\d+:\d+(?::\d+)?\.\d+)\]")
_WRITING = re.compile(r"正在写入[：:]")


def _seconds(value):
    parts = value.strip().split(":")
    try:
        return sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts)))
    except ValueError:
        return 0.0


def _chinese_duration(value):
    match = re.search(
        r"(?:(\d+)小时)?\s*(?:(\d+)分)?\s*(?:(\d+(?:\.\d+)?)秒)?", value
    )
    if not match or not any(match.groups()):
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def _clock(seconds):
    value = int(seconds)
    return f"{value // 60:02d}:{value % 60:02d}"


def show_in_gui(line):
    """详细时间轴保留在 infer.exe 的 latest.log，不占满界面日志。"""
    if _SEGMENT.search(line) or "VAD进度" in line:
        return False
    return not any(token in line for token in (
        "正在处理：", "文件后缀：", "有效后缀：", "为格式添加任务：",
        "已跳过 - 后缀", "debug.processing", "debug.file_suffix",
    ))


class WhisperProgress:
    def __init__(self, total):
        self.total = max(1, total)
        self.current = 0
        self.completed = 0
        self.duration = 0.0
        self.percent = -1
        self.phase = ""

    def feed(self, line):
        """返回 (整体百分比, 状态文本)；无可见变化时返回 None。"""
        start = _START.search(line)
        if start:
            self.current = min(max(int(start.group(1)), 1), self.total)
            self.completed = max(self.completed, self.current - 1)
            self.duration = 0.0
            return self._emit(self.completed / self.total, "扫描")

        if not self.current:
            return None

        duration = _DURATION.search(line)
        if duration:
            self.duration = _chinese_duration(duration.group(1))

        vad = _VAD.search(line)
        if vad:
            fraction = 0.05 * min(100.0, float(vad.group(1))) / 100
            return self._emit((self.completed + fraction) / self.total, "语音检测")

        if duration:
            return self._emit((self.completed + 0.05) / self.total, "识别")

        segment = _SEGMENT.search(line)
        if segment and self.duration:
            current_time = _seconds(segment.group(2))
            fraction = 0.05 + 0.94 * min(1.0, current_time / self.duration)
            status = (f"识别 {self.current}/{self.total} · "
                      f"{_clock(current_time)}/{_clock(self.duration)}")
            return self._emit((self.completed + fraction) / self.total,
                              "识别", status)

        if _WRITING.search(line):
            self.completed = max(self.completed, self.current)
            return self._emit(min(0.99, self.completed / self.total), "完成")
        return None

    def _emit(self, fraction, phase, status=None):
        percent = min(99, max(self.percent, int(fraction * 100)))
        changed = percent != self.percent or phase != self.phase
        self.percent = percent
        self.phase = phase
        if not changed:
            return None
        return percent, status or f"{phase} {self.current}/{self.total}"
