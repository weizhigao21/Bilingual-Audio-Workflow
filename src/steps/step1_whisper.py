# -*- coding: utf-8 -*-
"""步骤1：字幕提取（调用 infer.exe）。

通过 subprocess 调用 faster-whisper 打包好的 infer.exe，
实时读取 stdout 输出进度和日志。
"""
import os
import re
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal

from ..config import WorkflowConfig, WHISPER_MEDIA_SUFFIXES
from ..task_manager import TaskInfo


def _decode_console_line(b):
    """按行解码子进程输出：优先 utf-8，失败退回 gbk，避免 Windows 下中文乱码。"""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return b.decode("gbk", errors="replace")
    except (UnicodeError, ValueError):
        pass
    return b.decode("utf-8", errors="replace")


class WhisperWorker(QThread):
    """字幕提取工作线程。"""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)       # 0-100
    finished_signal = pyqtSignal(bool, str) # (success, output_path_or_error)

    def __init__(self, task: TaskInfo, config: WorkflowConfig):
        super().__init__()
        self.task = task
        self.config = config
        self._stop_flag = False
        self._process = None
        # 同步结果：(success, output_path_or_error)，供流水线模式 wait() 后读取
        self.result = (False, "未执行")

    def _emit_finished(self, ok: bool, msg: str):
        self.result = (ok, msg)
        self.finished_signal.emit(ok, msg)

    def stop(self):
        self._stop_flag = True
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass

    def run(self):
        try:
            self._run_impl()
        except Exception as e:
            self.log_signal.emit(f"[字幕提取] 异常: {e}")
            self._emit_finished(False, str(e))

    def _run_impl(self):
        whisper_dir = self.config.whisper_dir
        infer_exe = os.path.join(whisper_dir, "infer.exe")
        if not os.path.exists(infer_exe):
            self._emit_finished(False, f"找不到 infer.exe: {infer_exe}")
            return

        cfg = self.config.whisper_cfg
        source_path = self.task.source_path
        # 字幕最终会出现在源文件所在目录
        search_dir = os.path.dirname(source_path)
        # 每个任务独立识别自己的源文件（文件夹组的子任务同样按文件处理）
        input_path = source_path
        output_dir = search_dir

        # 构建命令行
        cmd = [
            infer_exe,
            "--sub_formats", cfg.get("sub_formats", "lrc"),
            "--audio_suffixes", cfg.get("audio_suffixes", WHISPER_MEDIA_SUFFIXES),
            "--device", cfg.get("device", "auto"),
            "--compute_type", cfg.get("compute_type", "auto"),
            "--vad_threshold", str(cfg.get("vad_threshold", 0.5)),
            "--vad_min_silence_duration_ms", str(cfg.get("vad_min_silence_duration_ms", 500)),
            "--vad_min_speech_duration_ms", str(cfg.get("vad_min_speech_duration_ms", 0)),
            "--vad_speech_pad_ms", str(cfg.get("vad_speech_pad_ms", 400)),
        ]
        if output_dir:
            cmd.extend(["--output_dir", output_dir])
        if cfg.get("enable_batching", False):
            cmd.append("--enable_batching")
        if cfg.get("overwrite", False):
            cmd.append("--overwrite")
        # 合并段落
        if cfg.get("merge_segments", True):
            cmd.append("--merge_segments")
            cmd.append("--merge_max_gap_ms")
            cmd.append(str(cfg.get("merge_max_gap_ms", 300)))
            cmd.append("--merge_max_duration_ms")
            cmd.append(str(cfg.get("merge_max_duration_ms", 30000)))
        else:
            cmd.append("--no_merge_segments")

        # 文件夹或源文件路径作为 positional 参数
        cmd.append(input_path)

        self.log_signal.emit(f"[字幕提取] 启动: {input_path}")
        self.log_signal.emit(f"[字幕提取] 输出目录: {search_dir}")
        self.log_signal.emit(
            f"[字幕配置] 设备={cfg.get('device', 'auto')}, "
            f"精度={cfg.get('compute_type', 'auto')}, "
            f"格式={cfg.get('sub_formats', 'lrc')}, "
            f"VAD={cfg.get('vad_threshold', 0.5)}/{cfg.get('vad_min_silence_duration_ms', 500)}ms, "
            f"合并={cfg.get('merge_segments', True)}"
        )

        # 创建标志：CREATE_NO_WINDOW 避免弹出控制台窗口
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        # 以二进制读取 stdout，逐行用 utf-8/gbk 兜底解码，避免中文乱码。
        # bufsize 不能用 1：行缓冲仅在文本模式有效，二进制下会被忽略并触发
        # RuntimeWarning；默认缓冲（-1）的 readline 同样即时返回完整行。
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=whisper_dir,
            creationflags=creationflags,
            bufsize=-1,
        )

        # 解析进度正则
        progress_patterns = [
            re.compile(r"(\d+(?:\.\d+)?)%"),           # "45%" 或 "45.5%"
            re.compile(r"(\d+)/(\d+)"),                 # "45/100"
        ]

        for raw in self._process.stdout:
            if self._stop_flag:
                break
            line = _decode_console_line(raw).rstrip()
            if not line:
                continue
            self.log_signal.emit(line)

            # 尝试解析进度
            for pat in progress_patterns:
                m = pat.search(line)
                if m:
                    try:
                        if pat.groups == 2:
                            cur, total = int(m.group(1)), int(m.group(2))
                            if total > 0:
                                pct = int(cur * 100 / total)
                                self.progress_signal.emit(min(pct, 99))
                        else:
                            pct = int(float(m.group(1)))
                            self.progress_signal.emit(min(pct, 99))
                        break
                    except (ValueError, ZeroDivisionError):
                        pass

        self._process.wait()
        ret = self._process.returncode

        if self._stop_flag:
            self.log_signal.emit("[字幕提取] 已中止")
            self._emit_finished(False, "用户中止")
            return

        if ret != 0:
            self._emit_finished(False, f"infer.exe 退出码: {ret}")
            return

        # 查找生成的字幕文件
        sub_formats = cfg.get("sub_formats", "lrc").split(",")
        found_sub = ""
        for fmt in sub_formats:
            fmt = fmt.strip()
            if not fmt:
                continue
            expected = os.path.join(search_dir, f"{self.task.source_name}.{fmt}")
            if os.path.exists(expected):
                found_sub = expected
                break

        if not found_sub:
            self._emit_finished(False, "字幕提取完成但未找到输出文件")
            return

        self.progress_signal.emit(100)
        self.log_signal.emit(f"[字幕提取] 完成: {found_sub}")
        self._emit_finished(True, found_sub)


class WhisperBatchWorker(QThread):
    """一次启动 infer.exe 处理全部待识别文件；完整导入目录直接传目录。"""

    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    task_result_signal = pyqtSignal(str, bool, str)
    finished_signal = pyqtSignal(int, int)

    def __init__(self, tasks, config: WorkflowConfig, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        self.config = config
        self._stop_flag = False
        self._process = None
        self._reported = {}

    def stop(self):
        self._stop_flag = True
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass

    def _report(self, task_id, ok, msg):
        if task_id not in self._reported:
            self._reported[task_id] = ok
            self.task_result_signal.emit(task_id, ok, msg)

    @staticmethod
    def _normalized(path):
        return os.path.normcase(os.path.abspath(path))

    @classmethod
    def _folder_covers_tasks(cls, folder, tasks, suffixes):
        """仅当整棵目录树都被选中时传目录；否则传文件路径，尊重文件树删除。"""
        if not os.path.isdir(folder):
            return False
        selected = {cls._normalized(task.source_path) for task in tasks}
        extensions = {"." + ext.strip().lower().lstrip(".")
                      for ext in suffixes.split(",") if ext.strip()}
        found = set()
        for root, _, filenames in os.walk(folder):
            for name in filenames:
                if os.path.splitext(name)[1].lower() in extensions:
                    found.add(cls._normalized(os.path.join(root, name)))
                    if found - selected:
                        return False
        return found == selected

    def _inputs(self, suffixes):
        roots = {}
        for task in self.tasks:
            root = task.import_folder if task.from_folder and task.import_folder else None
            if root:
                roots.setdefault(self._normalized(root), []).append(task)
        inputs = []
        covered = set()
        for root, tasks in roots.items():
            if self._folder_covers_tasks(root, tasks, suffixes):
                inputs.append(root)
                covered.update(task.task_id for task in tasks)
        inputs.extend(task.source_path for task in self.tasks
                      if task.task_id not in covered)
        return list(dict.fromkeys(inputs))

    @staticmethod
    def _chunks(inputs, cmd):
        """Windows 命令行约 32K 字符，超长时才分多次启动。"""
        limit = 24000 if os.name == "nt" else 131072
        group = []
        size = sum(len(str(arg)) + 3 for arg in cmd)
        for path in inputs:
            needed = len(path) + 3
            if group and size + needed > limit:
                yield group
                group = []
                size = sum(len(str(arg)) + 3 for arg in cmd)
            group.append(path)
            size += needed
        if group:
            yield group

    def _run_impl(self):
        whisper_dir = self.config.whisper_dir
        infer_exe = os.path.join(whisper_dir, "infer.exe")
        if not os.path.isfile(infer_exe):
            for task in self.tasks:
                self._report(task.task_id, False, f"找不到 infer.exe: {infer_exe}")
            self.finished_signal.emit(0, len(self._reported))
            return

        cfg = self.config.whisper_cfg
        suffixes = cfg.get("audio_suffixes", WHISPER_MEDIA_SUFFIXES)
        cmd = [
            infer_exe,
            "--sub_formats", cfg.get("sub_formats", "lrc"),
            "--audio_suffixes", suffixes,
            "--device", cfg.get("device", "auto"),
            "--compute_type", cfg.get("compute_type", "auto"),
            "--vad_threshold", str(cfg.get("vad_threshold", 0.5)),
            "--vad_min_silence_duration_ms", str(cfg.get("vad_min_silence_duration_ms", 500)),
            "--vad_min_speech_duration_ms", str(cfg.get("vad_min_speech_duration_ms", 0)),
            "--vad_speech_pad_ms", str(cfg.get("vad_speech_pad_ms", 400)),
        ]
        if cfg.get("enable_batching", False):
            cmd.append("--enable_batching")
        if cfg.get("overwrite", False):
            cmd.append("--overwrite")
        if cfg.get("merge_segments", True):
            cmd.extend([
                "--merge_segments",
                "--merge_max_gap_ms", str(cfg.get("merge_max_gap_ms", 300)),
                "--merge_max_duration_ms", str(cfg.get("merge_max_duration_ms", 30000)),
            ])
        else:
            cmd.append("--no_merge_segments")

        inputs = self._inputs(suffixes)
        batches = list(self._chunks(inputs, cmd))
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        progress = re.compile(r"(\d+(?:\.\d+)?)%")
        for index, batch in enumerate(batches):
            if self._stop_flag:
                break
            self.log_signal.emit(
                f"[字幕批量] 启动 infer.exe ({index + 1}/{len(batches)}): "
                f"{len(batch)} 个输入，{len(self.tasks)} 个任务"
            )
            self._process = subprocess.Popen(
                cmd + batch,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=whisper_dir,
                creationflags=creationflags,
                bufsize=-1,
            )
            for raw in self._process.stdout:
                if self._stop_flag:
                    break
                line = _decode_console_line(raw).rstrip()
                if line:
                    self.log_signal.emit(line)
                    match = progress.search(line)
                    if match:
                        self.progress_signal.emit(min(int(float(match.group(1))), 99))
            if self._stop_flag:
                self._process.terminate()
            self._process.wait()
            returncode = self._process.returncode
            self._process = None
            if returncode != 0 and not self._stop_flag:
                self.log_signal.emit(f"[字幕批量] infer.exe 退出码: {returncode}")
                for task in self.tasks:
                    source = self._normalized(task.source_path)
                    for path in batch:
                        candidate = self._normalized(path)
                        if source == candidate or (os.path.isdir(path) and
                                os.path.splitdrive(source)[0] == os.path.splitdrive(candidate)[0] and
                                os.path.commonpath((source, candidate)) == candidate):
                            self._report(task.task_id, False, f"infer.exe 退出码: {returncode}")
                            break

        formats = [fmt.strip() for fmt in cfg.get("sub_formats", "lrc").split(",")
                   if fmt.strip()]
        for task in self.tasks:
            if task.task_id in self._reported:
                continue
            if self._stop_flag:
                self._report(task.task_id, False, "用户中止")
                continue
            base = os.path.splitext(task.source_path)[0]
            output = next((f"{base}.{fmt}" for fmt in formats
                           if os.path.isfile(f"{base}.{fmt}")), "")
            self._report(task.task_id, bool(output),
                         output or "未找到对应音频的字幕文件")

        success = sum(self._reported.values())
        failed = len(self._reported) - success
        if not self._stop_flag:
            self.progress_signal.emit(100)
        self.log_signal.emit(f"[字幕批量] 完成: 成功 {success}, 失败 {failed}")
        self.finished_signal.emit(success, failed)

    def run(self):
        try:
            self._run_impl()
        except Exception as exc:
            self.log_signal.emit(f"[字幕批量] 异常: {exc}")
            for task in self.tasks:
                self._report(task.task_id, False, str(exc))
            success = sum(self._reported.values())
            self.finished_signal.emit(success, len(self._reported) - success)
        finally:
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    pass
                self._process = None
