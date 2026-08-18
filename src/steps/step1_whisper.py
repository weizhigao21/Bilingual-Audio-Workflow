# -*- coding: utf-8 -*-
"""步骤1：字幕提取（调用 infer.exe）。

通过 subprocess 调用 faster-whisper 打包好的 infer.exe，
实时读取 stdout 输出进度和日志。
"""
import os
import re
import shutil
import subprocess
import tempfile

from PyQt6.QtCore import QThread, pyqtSignal

from ..config import WorkflowConfig
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
            "--audio_suffixes", cfg.get("audio_suffixes", "wav,flac,mp3,mp4,mkv,avi,mov"),
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

        # 以二进制读取 stdout，逐行用 utf-8/gbk 兜底解码，避免中文乱码
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=whisper_dir,
            creationflags=creationflags,
            bufsize=1,
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

        # 如果精确名没找到，扫描目录里第一个匹配的
        if not found_sub:
            for f in os.listdir(search_dir):
                for fmt in sub_formats:
                    if f.endswith(f".{fmt.strip()}"):
                        found_sub = os.path.join(search_dir, f)
                        break
                if found_sub:
                    break

        if not found_sub:
            self._emit_finished(False, "字幕提取完成但未找到输出文件")
            return

        self.progress_signal.emit(100)
        self.log_signal.emit(f"[字幕提取] 完成: {found_sub}")
        self._emit_finished(True, found_sub)


class WhisperBatchWorker(QThread):
    """字幕提取批量工作线程。

    一次性把所有任务的源文件传给 infer.exe，只加载一次模型。
    完成后按文件名把字幕分配到各任务的 subtitle_dir。

    信号:
        log_signal: 日志
        progress_signal: 进度 0-100
        task_result_signal: (task_id, success, output_path_or_error) 每个任务完成时发
        finished_signal: (success_count, fail_count) 整个批量完成
    """
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    task_result_signal = pyqtSignal(str, bool, str)  # task_id, ok, msg
    finished_signal = pyqtSignal(int, int)            # success_count, fail_count

    def __init__(self, tasks, config: WorkflowConfig, parent=None):
        super().__init__(parent)
        self.tasks = tasks  # List[TaskInfo]
        self.config = config
        self._stop_flag = False
        self._process = None
        self._reported = {}  # task_id -> ok，用于去重，避免同一任务被重复上报

    def stop(self):
        self._stop_flag = True
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass

    def _report(self, task_id, ok, msg):
        """上报单个任务结果；已上报过的任务忽略，避免重复。"""
        if task_id in self._reported:
            return
        self._reported[task_id] = ok
        self.task_result_signal.emit(task_id, ok, msg)

    def _finish_stats(self):
        """根据已上报结果统计成功/失败数。"""
        ok_count = sum(1 for ok in self._reported.values() if ok)
        return ok_count, len(self._reported) - ok_count

    def run(self):
        try:
            self._run_impl()
        except Exception as e:
            self.log_signal.emit(f"[字幕批量] 异常: {e}")
            # 仅对尚未上报的任务标记失败，避免与已正常上报的任务重复
            for task in self.tasks:
                self._report(task.task_id, False, str(e))
            self.finished_signal.emit(*self._finish_stats())

    def _run_impl(self):
        whisper_dir = self.config.whisper_dir
        infer_exe = os.path.join(whisper_dir, "infer.exe")
        if not os.path.exists(infer_exe):
            for task in self.tasks:
                self._report(task.task_id, False, f"找不到 infer.exe: {infer_exe}")
            self.finished_signal.emit(*self._finish_stats())
            return

        cfg = self.config.whisper_cfg

        # 创建临时输出目录
        tmp_output = tempfile.mkdtemp(prefix="whisper_batch_")
        self.log_signal.emit(f"[字幕批量] 临时输出目录: {tmp_output}")

        # 按输入文件夹分组。每个文件夹使用独立的输出子目录，避免不同文件夹下
        # 同名文件（如都叫 01.mp4）的字幕在平铺目录里互相覆盖或误匹配。
        groups = {}
        for t in self.tasks:
            folder = (t.import_folder if t.from_folder and t.import_folder
                      else os.path.dirname(t.source_path))
            groups.setdefault(folder, []).append(t)

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        progress_patterns = [
            re.compile(r"(\d+(?:\.\d+)?)%"),
            re.compile(r"(\d+)/(\d+)"),
        ]

        for gi, (folder, group_tasks) in enumerate(groups.items()):
            out_dir = os.path.join(tmp_output, str(gi))
            os.makedirs(out_dir, exist_ok=True)

            # 构建命令行（infer.exe 自动扫描输入文件夹中的音频）
            cmd = [
                infer_exe,
                "--output_dir", out_dir,
                "--sub_formats", cfg.get("sub_formats", "lrc"),
                "--audio_suffixes", cfg.get("audio_suffixes", "wav,flac,mp3,mp4,mkv,avi,mov"),
                "--device", cfg.get("device", "auto"),
                "--compute_type", cfg.get("compute_type", "auto"),
                "--vad_threshold", str(cfg.get("vad_threshold", 0.5)),
                "--vad_min_silence_duration_ms", str(cfg.get("vad_min_silence_duration_ms", 500)),
                "--vad_min_speech_duration_ms", str(cfg.get("vad_min_speech_duration_ms", 0)),
                "--vad_speech_pad_ms", str(cfg.get("vad_speech_pad_ms", 400)),
                "--enable_batching",
            ]
            if cfg.get("overwrite", False):
                cmd.append("--overwrite")
            # 合并段落
            if cfg.get("merge_segments", True):
                cmd.extend([
                    "--merge_segments",
                    "--merge_max_gap_ms", str(cfg.get("merge_max_gap_ms", 300)),
                    "--merge_max_duration_ms", str(cfg.get("merge_max_duration_ms", 30000)),
                ])
            else:
                cmd.append("--no_merge_segments")
            cmd.append(folder)

            self.log_signal.emit(
                f"[字幕批量] 启动({gi + 1}/{len(groups)}): {folder}, "
                f"设备={cfg.get('device', 'auto')}, 精度={cfg.get('compute_type', 'auto')}"
            )

            # 以二进制读取 stdout，逐行用 utf-8/gbk 兜底解码，避免中文乱码
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=whisper_dir,
                creationflags=creationflags,
                bufsize=1,
            )

            # 解析进度
            for raw in self._process.stdout:
                if self._stop_flag:
                    break
                line = _decode_console_line(raw).rstrip()
                if not line:
                    continue
                self.log_signal.emit(line)
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
                self.log_signal.emit("[字幕批量] 已中止")
                for task in group_tasks:
                    self._report(task.task_id, False, "用户中止")
                self.finished_signal.emit(*self._finish_stats())
                shutil.rmtree(tmp_output, ignore_errors=True)
                return

            if ret != 0:
                self.log_signal.emit(f"[字幕批量] {folder} infer.exe 退出码: {ret}")
                for task in group_tasks:
                    self._report(task.task_id, False, f"infer.exe 退出码: {ret}")
                continue

            # 在该组输出子目录内匹配字幕（组内文件名唯一，无跨文件夹冲突）
            sub_formats = [f.strip() for f in cfg.get("sub_formats", "lrc").split(",") if f.strip()]
            out_files = os.listdir(out_dir)

            for task in group_tasks:
                found_sub = ""
                # 先精确匹配 source_name
                for fmt in sub_formats:
                    expected = os.path.join(out_dir, f"{task.source_name}.{fmt}")
                    if os.path.exists(expected):
                        found_sub = expected
                        break
                # 模糊匹配
                if not found_sub:
                    for f in out_files:
                        if any(f.endswith(f".{fmt}") and task.source_name in f for fmt in sub_formats):
                            found_sub = os.path.join(out_dir, f)
                            break

                if not found_sub:
                    self._report(task.task_id, False, "未找到输出字幕文件")
                    continue

                # 移动到源文件所在目录
                src_dir = os.path.dirname(task.source_path)
                dest = os.path.join(src_dir, os.path.basename(found_sub))
                try:
                    shutil.move(found_sub, dest)
                    self.log_signal.emit(f"[字幕批量] {task.source_name} → {dest}")
                    self._report(task.task_id, True, dest)
                except Exception as e:
                    self._report(task.task_id, False, f"移动文件失败: {e}")

            if self._stop_flag:
                self.log_signal.emit("[字幕批量] 已中止")
                self.finished_signal.emit(*self._finish_stats())
                shutil.rmtree(tmp_output, ignore_errors=True)
                return

        s, f = self._finish_stats()
        self.progress_signal.emit(100)
        self.log_signal.emit(f"[字幕批量] 完成: 成功 {s}, 失败 {f}")
        self.finished_signal.emit(s, f)
        shutil.rmtree(tmp_output, ignore_errors=True)
