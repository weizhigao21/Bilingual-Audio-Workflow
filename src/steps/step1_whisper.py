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
            self.finished_signal.emit(False, str(e))

    def _run_impl(self):
        whisper_dir = self.config.whisper_dir
        infer_exe = os.path.join(whisper_dir, "infer.exe")
        if not os.path.exists(infer_exe):
            self.finished_signal.emit(False, f"找不到 infer.exe: {infer_exe}")
            return

        cfg = self.config.whisper_cfg
        source_path = self.task.source_path
        # 字幕输出到源文件所在目录
        output_dir = os.path.dirname(source_path)

        # 构建命令行
        cmd = [
            infer_exe,
            "--output_dir", output_dir,
            "--sub_formats", cfg.get("sub_formats", "lrc"),
            "--audio_suffixes", cfg.get("audio_suffixes", "wav,flac,mp3,mp4,mkv,avi,mov"),
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
        # 合并段落
        if cfg.get("merge_segments", True):
            cmd.append("--merge_segments")
            cmd.append("--merge_max_gap_ms")
            cmd.append(str(cfg.get("merge_max_gap_ms", 300)))
            cmd.append("--merge_max_duration_ms")
            cmd.append(str(cfg.get("merge_max_duration_ms", 30000)))
        else:
            cmd.append("--no_merge_segments")

        # 源文件路径作为 positional 参数
        cmd.append(source_path)

        self.log_signal.emit(f"[字幕提取] 启动: {os.path.basename(source_path)}")
        self.log_signal.emit(f"[字幕提取] 输出目录: {output_dir}")
        self.log_signal.emit(
            f"[字幕配置] 设备={cfg.get('device', 'auto')}, "
            f"精度={cfg.get('compute_type', 'auto')}, "
            f"格式={cfg.get('sub_formats', 'lrc')}, "
            f"VAD={cfg.get('vad_threshold', 0.5)}/{cfg.get('vad_min_silence_duration_ms', 500)}ms, "
            f"合并={cfg.get('merge_segments', True)}"
        )

        # 创建标志：CREATE_NO_WINDOW 避免弹出控制台窗口
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=whisper_dir,
            creationflags=creationflags,
            bufsize=1,
        )

        # 解析进度正则
        progress_patterns = [
            re.compile(r"(\d+(?:\.\d+)?)%"),           # "45%" 或 "45.5%"
            re.compile(r"(\d+)/(\d+)"),                 # "45/100"
        ]

        for line in self._process.stdout:
            if self._stop_flag:
                break
            line = line.rstrip()
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
            self.finished_signal.emit(False, "用户中止")
            return

        if ret != 0:
            self.finished_signal.emit(False, f"infer.exe 退出码: {ret}")
            return

        # 查找生成的字幕文件
        sub_formats = cfg.get("sub_formats", "lrc").split(",")
        found_sub = ""
        for fmt in sub_formats:
            fmt = fmt.strip()
            if not fmt:
                continue
            expected = os.path.join(output_dir, f"{self.task.source_name}.{fmt}")
            if os.path.exists(expected):
                found_sub = expected
                break

        # 如果精确名没找到，扫描目录里第一个匹配的
        if not found_sub:
            for f in os.listdir(output_dir):
                for fmt in sub_formats:
                    if f.endswith(f".{fmt.strip()}"):
                        found_sub = os.path.join(output_dir, f)
                        break
                if found_sub:
                    break

        if not found_sub:
            self.finished_signal.emit(False, "字幕提取完成但未找到输出文件")
            return

        self.progress_signal.emit(100)
        self.log_signal.emit(f"[字幕提取] 完成: {found_sub}")
        self.finished_signal.emit(True, found_sub)


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
            self.log_signal.emit(f"[字幕批量] 异常: {e}")
            # 所有未完成任务标记失败
            for task in self.tasks:
                self.task_result_signal.emit(task.task_id, False, str(e))
            self.finished_signal.emit(0, len(self.tasks))

    def _run_impl(self):
        whisper_dir = self.config.whisper_dir
        infer_exe = os.path.join(whisper_dir, "infer.exe")
        if not os.path.exists(infer_exe):
            for task in self.tasks:
                self.task_result_signal.emit(task.task_id, False, f"找不到 infer.exe: {infer_exe}")
            self.finished_signal.emit(0, len(self.tasks))
            return

        cfg = self.config.whisper_cfg

        # 创建临时输出目录
        tmp_output = tempfile.mkdtemp(prefix="whisper_batch_")
        self.log_signal.emit(f"[字幕批量] 临时输出目录: {tmp_output}")

        # 构建命令行
        cmd = [
            infer_exe,
            "--output_dir", tmp_output,
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
            cmd.append("--merge_segments")
            cmd.append("--merge_max_gap_ms")
            cmd.append(str(cfg.get("merge_max_gap_ms", 300)))
            cmd.append("--merge_max_duration_ms")
            cmd.append(str(cfg.get("merge_max_duration_ms", 30000)))
        else:
            cmd.append("--no_merge_segments")

        # 所有源文件路径作为 positional 参数
        source_paths = [t.source_path for t in self.tasks]
        cmd.extend(source_paths)

        self.log_signal.emit(
            f"[字幕批量] 启动: {len(source_paths)} 个文件, "
            f"设备={cfg.get('device', 'auto')}, 精度={cfg.get('compute_type', 'auto')}"
        )
        for t in self.tasks:
            self.log_signal.emit(f"[字幕批量]   - {t.source_name}")

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=whisper_dir,
            creationflags=creationflags,
            bufsize=1,
        )

        # 解析进度
        progress_patterns = [
            re.compile(r"(\d+(?:\.\d+)?)%"),
            re.compile(r"(\d+)/(\d+)"),
        ]

        for line in self._process.stdout:
            if self._stop_flag:
                break
            line = line.rstrip()
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
            for task in self.tasks:
                self.task_result_signal.emit(task.task_id, False, "用户中止")
            self.finished_signal.emit(0, len(self.tasks))
            shutil.rmtree(tmp_output, ignore_errors=True)
            return

        if ret != 0:
            self.log_signal.emit(f"[字幕批量] infer.exe 退出码: {ret}")
            for task in self.tasks:
                self.task_result_signal.emit(task.task_id, False, f"infer.exe 退出码: {ret}")
            self.finished_signal.emit(0, len(self.tasks))
            shutil.rmtree(tmp_output, ignore_errors=True)
            return

        # 扫描临时目录，把字幕文件分配到各任务
        sub_formats = [f.strip() for f in cfg.get("sub_formats", "lrc").split(",") if f.strip()]
        tmp_files = os.listdir(tmp_output)

        success_count = 0
        fail_count = 0

        for task in self.tasks:
            found_sub = ""
            # 先精确匹配 source_name
            for fmt in sub_formats:
                expected = os.path.join(tmp_output, f"{task.source_name}.{fmt}")
                if os.path.exists(expected):
                    found_sub = expected
                    break
            # 模糊匹配
            if not found_sub:
                for f in tmp_files:
                    for fmt in sub_formats:
                        if f.endswith(f".{fmt}") and task.source_name in f:
                            found_sub = os.path.join(tmp_output, f)
                            break
                    if found_sub:
                        break

            if not found_sub:
                self.task_result_signal.emit(task.task_id, False, "未找到输出字幕文件")
                fail_count += 1
                continue

            # 移动到源文件所在目录
            src_dir = os.path.dirname(task.source_path)
            dest = os.path.join(src_dir, os.path.basename(found_sub))
            try:
                shutil.move(found_sub, dest)
                self.log_signal.emit(f"[字幕批量] {task.source_name} → {dest}")
                self.task_result_signal.emit(task.task_id, True, dest)
                success_count += 1
            except Exception as e:
                self.task_result_signal.emit(task.task_id, False, f"移动文件失败: {e}")
                fail_count += 1

        self.progress_signal.emit(100)
        self.log_signal.emit(f"[字幕批量] 完成: 成功 {success_count}, 失败 {fail_count}")
        self.finished_signal.emit(success_count, fail_count)
        shutil.rmtree(tmp_output, ignore_errors=True)
