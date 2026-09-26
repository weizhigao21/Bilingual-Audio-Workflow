# -*- coding: utf-8 -*-
"""ffmpeg 重采样、音频导出与视频音轨替换。"""
import os
import subprocess
import tempfile
import threading
import json
import wave
import audioop


def _pcm_input(audio):
    pcm_format = {1: "s8", 2: "s16le", 4: "s32le"}[audio.sample_width]
    return ["-f", pcm_format, "-ar", str(audio.frame_rate),
            "-ac", str(audio.channels), "-i", "pipe:0"]


def _resample_filter(high_quality):
    # swr 随 ffmpeg 提供，无需额外安装 libsoxr。
    return "aresample=filter_size=64:phase_shift=10" if high_quality else "anull"


def resample_audio(audio, sample_rate, stop_check=None, high_quality=True):
    """仅在采样率变化时转换；质量模式保留 32 位结果到混音阶段。"""
    if audio.frame_rate == sample_rate:
        return audio
    if not high_quality:
        return audio.set_frame_rate(sample_rate)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"] + _pcm_input(audio) + [
        "-af", _resample_filter(True), "-ar", str(sample_rate),
        "-ac", str(audio.channels), "-c:a", "pcm_s32le", "-f", "s32le", "pipe:1",
    ]
    raw = _run_ffmpeg(cmd, stop_check=stop_check, input_data=audio.raw_data)
    return audio.__class__(raw, sample_width=4, frame_rate=sample_rate,
                           channels=audio.channels)


def export_audio_ffmpeg(audio_segment, output_path, format_type="mp3",
                        bitrate="192k", sample_rate=44100, channels=2,
                        stop_check=None, high_quality=True):
    codecs = {"mp3": ("libmp3lame", "mp3"), "m4a": ("aac", "ipod"),
              "aac": ("aac", "adts"), "mp4": ("aac", "mp4"),
              "ogg": ("libvorbis", "ogg")}
    if format_type not in codecs:
        raise ValueError(f"不支持的音频导出格式: {format_type}")
    codec, muxer = codecs[format_type]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + _pcm_input(audio_segment) + [
        "-vn", "-c:a", codec, "-b:a", bitrate,
        "-af", _resample_filter(high_quality),
        "-ar", str(sample_rate), "-ac", str(channels), "-f", muxer, output_path,
    ]
    _run_ffmpeg(cmd, stop_check=stop_check, input_data=audio_segment.raw_data)


def export_with_nvenc(*args, **kwargs):
    """保留旧导入接口。音频使用软件编码器，不依赖 NVIDIA 显卡。"""
    return export_audio_ffmpeg(*args, **kwargs)


def probe_audio(source_path):
    """返回 (采样率, 时长秒)，无音轨或探测失败时返回 None。"""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,duration:format=duration",
             "-of", "json", source_path], capture_output=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        info = json.loads(proc.stdout)
        stream = info["streams"][0]
        rate = int(stream["sample_rate"])
        duration = float(stream.get("duration") or info.get("format", {}).get("duration") or 0)
        return (rate, duration) if proc.returncode == 0 and rate > 0 else None
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired):
        return None


def decode_audio_to_pcm(source_path, output_path, stop_check=None):
    """将首个音轨解码成双声道 s32le 磁盘文件，不在内存中展开整段音频。"""
    _run_ffmpeg([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", source_path,
        "-map", "0:a:0", "-vn", "-ac", "2", "-c:a", "pcm_s32le",
        "-f", "s32le", output_path,
    ], stop_check=stop_check)


def export_pcm_file(pcm_path, video_path, output_path, format_type,
                    source_rate, bitrate, sample_rate, channels,
                    high_quality=True, stop_check=None):
    """从磁盘 PCM 编码；视频输出复制画面，其他格式只写音频。"""
    input_args = ["-f", "s32le", "-ar", str(source_rate), "-ac", "2", "-i", pcm_path]
    if video_path and format_type == "mp4":
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", video_path] + input_args + [
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
            "-b:a", bitrate, "-af", _resample_filter(high_quality),
            "-ar", str(sample_rate), "-ac", str(channels), "-shortest", output_path,
        ]
    else:
        codec, muxer = {"mp3": ("libmp3lame", "mp3"), "m4a": ("aac", "ipod"),
                        "aac": ("aac", "adts"), "mp4": ("aac", "mp4"),
                        "ogg": ("libvorbis", "ogg")}[format_type]
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + input_args + [
            "-vn", "-c:a", codec, "-b:a", bitrate,
            "-af", _resample_filter(high_quality), "-ar", str(sample_rate),
            "-ac", str(channels), "-f", muxer, output_path,
        ]
    _run_ffmpeg(cmd, stop_check=stop_check)


def export_pcm_wav(pcm_path, output_path, source_rate, sample_rate,
                   channels, bit_depth, work_dir, high_quality=True,
                   stop_check=None):
    """分块写经典 PCM WAV 头，24 位无需整段转换。"""
    if source_rate != sample_rate or channels != 2:
        converted = os.path.join(work_dir, "resampled.raw")
        _run_ffmpeg([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "s32le", "-ar", str(source_rate), "-ac", "2", "-i", pcm_path,
            "-af", _resample_filter(high_quality), "-ar", str(sample_rate),
            "-ac", str(channels), "-c:a", "pcm_s32le", "-f", "s32le", converted,
        ], stop_check=stop_check)
        pcm_path = converted
    width = bit_depth // 8
    with open(pcm_path, "rb") as source, wave.open(output_path, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(sample_rate)
        while True:
            if stop_check is not None and stop_check():
                raise RuntimeError("用户已停止处理")
            block = source.read(sample_rate * channels * 4)
            if not block:
                break
            wav.writeframes(block if width == 4 else audioop.lin2lin(block, 4, width))


def extract_audio_from_video(video_path, stop_check=None):
    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp_wav.close()
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path, "-map", "0:a:0", "-vn", "-acodec", "pcm_s32le",
        "-ac", "2", temp_wav.name,
    ]
    # 保留源采样率，避免 48k → 44.1k → 48k 的重复重采样。
    try:
        _run_ffmpeg(cmd, stop_check=stop_check)
    except Exception:
        os.unlink(temp_wav.name)
        raise
    return temp_wav.name


def replace_audio_in_video(video_path, audio_source, output_path,
                           bitrate="192k", sample_rate=44100, channels=2,
                           stop_check=None, high_quality=True):
    """复制视频码流并替换音轨；PCM 管道保留输入位深。"""
    if isinstance(audio_source, str):
        input_args, input_data = ["-i", audio_source], None
    else:
        input_args, input_data = _pcm_input(audio_source), audio_source.raw_data
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video_path] + input_args + [
        "-c:v", "copy", "-c:a", "aac", "-b:a", bitrate,
        "-af", _resample_filter(high_quality),
        "-ar", str(sample_rate), "-ac", str(channels),
        "-map", "0:v:0", "-map", "1:a:0", "-shortest", output_path,
    ]
    _run_ffmpeg(cmd, stop_check=stop_check, input_data=input_data)


def _run_ffmpeg(cmd, stop_check=None, input_data=None):
    """同步收发管道以免 stderr 写满死锁，轮询支持取消并回收子进程。"""
    if stop_check is not None and stop_check():
        raise RuntimeError("用户已停止处理")
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    completed = threading.Event()
    result = []

    def communicate():
        # Windows 的 communicate 写 stdin 可能阻塞，放入线程才能随时取消。
        try:
            result.append(proc.communicate(input=input_data))
        except Exception as exc:
            result.append(exc)
        finally:
            completed.set()

    worker = threading.Thread(target=communicate, daemon=True)
    worker.start()
    try:
        while not completed.wait(0.1):
            if stop_check is not None and stop_check():
                raise RuntimeError("用户已停止处理")
        if isinstance(result[0], Exception):
            raise result[0]
        stdout, stderr = result[0]
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-4000:]
            raise RuntimeError(f"ffmpeg 失败（{proc.returncode}）: {detail}")
        return stdout
    finally:
        if proc.poll() is None:
            proc.kill()
        worker.join()
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
