# -*- coding: utf-8 -*-
"""步骤2：语音生成。"""
import os
import hashlib

from PyQt6.QtCore import QThread, pyqtSignal, QEventLoop

from ..config import WorkflowConfig
from ..task_manager import TaskInfo
from .tts_worker import TTSWorker


class TTSBridgeWorker(QThread):
    """TTS 工作线程桥接器。

    内部持有 TTS 项目的 TTSWorker 实例，转发其信号到主控。
    """
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)         # 当前已完成数
    total_signal = pyqtSignal(int)            # 总任务数
    eta_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)   # (success, output_dir_or_error)

    def __init__(self, task: TaskInfo, config: WorkflowConfig):
        super().__init__()
        self.task = task
        self.config = config
        self._tts_worker = None
        self._total = 0

    def stop(self):
        if self._tts_worker:
            try:
                self._tts_worker.stop()
            except Exception:
                pass

    def pause(self):
        if self._tts_worker:
            self._tts_worker.pause()

    def resume(self):
        if self._tts_worker:
            self._tts_worker.resume()

    def run(self):
        try:
            self._run_impl()
        except Exception as e:
            self.log_signal.emit(f"[语音生成] 异常: {e}")
            self.finished_signal.emit(False, str(e))

    def _run_impl(self):
        # 检查字幕文件
        lrc_path = self.task.step1_output
        if not lrc_path or not os.path.exists(lrc_path):
            self.finished_signal.emit(False, f"字幕文件不存在: {lrc_path}")
            return

        cfg = self.config.tts_cfg

        # 计算字幕文件内容的MD5作为输出文件夹名
        with open(lrc_path, 'rb') as f:
            subtitle_content = f.read()
        self._subtitle_md5 = hashlib.md5(subtitle_content).hexdigest()[:8]

        tts_config = {
            "tts_mode": cfg.get("tts_mode", "edge"),
            "prevent_sleep": True,
            "lrc_files": [lrc_path],
            "output_dir": self.task.workspace_root,
            "subtitle_md5": self._subtitle_md5,
            "current_api_index": cfg.get("current_api_index", 0),
            "api_configs": cfg.get("api_configs", []),
            "use_multi_api": cfg.get("use_multi_api", False),
            "edge_voice": cfg.get("edge_voice", "zh-CN-XiaoxiaoNeural"),
            "edge_rate": cfg.get("edge_rate", "+0%"),
            "edge_volume": cfg.get("edge_volume", "+0%"),
            "edge_threads": cfg.get("edge_threads", 5),
            "use_bulk_api": cfg.get("use_bulk_api", True),
            "bulk_batch_size": cfg.get("bulk_batch_size", 20),
        }

        self.log_signal.emit(f"[语音生成] 启动: {os.path.basename(lrc_path)}")
        self.log_signal.emit(f"[语音生成] 模式: {tts_config['tts_mode']}")
        if tts_config["tts_mode"] == "edge":
            self.log_signal.emit(f"[语音生成] 声音: {tts_config['edge_voice']}")
            self.log_signal.emit(
                f"[语音生成] 语速={tts_config['edge_rate']}, "
                f"音量={tts_config['edge_volume']}, "
                f"线程={tts_config['edge_threads']}"
            )
        else:
            idx = tts_config["current_api_index"]
            apis = tts_config["api_configs"]
            api_name = apis[idx].get("name", "?") if 0 <= idx < len(apis) else "?"
            self.log_signal.emit(
                f"[语音生成] API: {api_name}, "
                f"批量={tts_config['bulk_batch_size']}, "
                f"多API轮询={tts_config['use_multi_api']}"
            )

        # 创建 TTSWorker 并桥接信号
        self._tts_worker = TTSWorker(tts_config)
        self._tts_worker.log_signal.connect(self.log_signal.emit)
        self._tts_worker.progress_signal.connect(self.progress_signal.emit)
        self._tts_worker.total_tasks_signal.connect(self._on_total)
        self._tts_worker.eta_signal.connect(self.eta_signal.emit)
        self._tts_worker.finished_signal.connect(self._on_finished)

        # 启动 TTSWorker（QThread），用 QEventLoop 等待其完成
        # 不用 wait() 是因为它会阻塞线程不处理事件，导致 progress_signal 信号丢失
        loop = QEventLoop()
        self._tts_worker.finished_signal.connect(loop.quit)
        self._tts_worker.start()
        loop.exec()
        # TTSWorker 已完成，_on_finished 在 loop 中通过信号已自动调用

    def _on_total(self, total: int):
        self._total = total
        self.total_signal.emit(total)

    def _on_finished(self, success: bool):
        """TTSWorker 完成回调。"""
        if not success:
            self.finished_signal.emit(False, "TTS 合成失败，详见日志")
            return

        # 查找输出目录：workspace_root/<subtitle_md5>/
        task_dir = os.path.join(self.task.workspace_root, self._subtitle_md5)

        if os.path.isdir(task_dir):
            wavs = [f for f in os.listdir(task_dir) if f.endswith(".wav")]
            output_dir = task_dir if wavs else ""
        else:
            output_dir = ""

        if not output_dir:
            self.finished_signal.emit(False, "TTS 完成但未找到输出目录")
            return

        self.log_signal.emit(f"[语音生成] 完成: {output_dir}")
        self.finished_signal.emit(True, output_dir)
