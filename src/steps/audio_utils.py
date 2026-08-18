# -*- coding: utf-8 -*-
import os
import re
import time
import math
import logging
import threading
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from pydub import AudioSegment

logger = logging.getLogger(__name__)

from .app_config import is_aac_nvenc_available

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None


_mix_files_cache = {}

# 声道检测的共享线程池：外层（批量混音任务）与内层（单个任务内的片段检测）
# 复用同一容量，避免批量模式下并发数乘方（外层 N × 内层 M）。
_pan_detect_executor = None
_pan_detect_workers = 0


def _get_pan_detect_executor(max_workers):
    """惰性返回按容量复用的检测线程池；容量变化时销毁重建（不等待旧任务）。"""
    global _pan_detect_executor, _pan_detect_workers
    if _pan_detect_executor is None or _pan_detect_workers != max_workers:
        if _pan_detect_executor is not None:
            _pan_detect_executor.shutdown(wait=False)
        _pan_detect_executor = ThreadPoolExecutor(max_workers=max_workers)
        _pan_detect_workers = max_workers
    return _pan_detect_executor


def parse_filename(filename):
    pattern = r"^(\d{4})_(\d{2})-(\d{2})-(\d{2})\.(\d{2})_(.+)\.wav$"
    match = re.match(pattern, filename)
    if not match:
        return None

    seq = int(match.group(1))
    hours = int(match.group(2))
    minutes = int(match.group(3))
    seconds = int(match.group(4))
    centiseconds = int(match.group(5))
    text = match.group(6)

    timestamp_ms = (
        (hours * 3600000) + (minutes * 60000) + (seconds * 1000) + (centiseconds * 10)
    )

    return {
        "sequence": seq,
        "timestamp_ms": timestamp_ms,
        "text": text,
        "filename": filename,
    }


def get_mix_audio_files(mix_folder):
    if mix_folder in _mix_files_cache:
        return _mix_files_cache[mix_folder]

    audio_files = []
    for f in os.listdir(mix_folder):
        if f.endswith(".wav"):
            parsed = parse_filename(f)
            if parsed:
                audio_files.append(parsed)

    audio_files.sort(key=lambda x: x["sequence"])
    _mix_files_cache[mix_folder] = audio_files
    return audio_files


def clear_mix_cache():
    _mix_files_cache.clear()


def _detect_voice_channel_from_samples(samples_2d, sr):
    """从已转换的 (N, 2) float32 numpy 数组检测人声声道。

    接受预转换的样本数组，避免在批量检测时每个片段都重复复制数据。
    samples_2d 的 dtype 应为 float32。
    """
    if not HAS_NUMPY or samples_2d is None or len(samples_2d) < sr * 2:
        return 'both'

    try:
        # 防御性拷贝：下游会做切片和降采样，避免修改原始数组
        left = samples_2d[:, 0]
        right = samples_2d[:, 1]

        if sr > 16000:
            downsample_factor = max(1, sr // 16000)
            left = left[::downsample_factor]
            right = right[::downsample_factor]
            sr = sr // downsample_factor

        left_energy = _analyze_channel_energy(left, sr)
        right_energy = _analyze_channel_energy(right, sr)

        logger.debug(f"[人声检测] 左声道能量: {left_energy:.1f}, 右声道能量: {right_energy:.1f}, 比值: {left_energy/(right_energy+1e-10):.2f}")

        if left_energy + right_energy < 1e-10:
            return 'both'

        if left_energy > right_energy * 1.2:
            return 'left'
        elif right_energy > left_energy * 1.2:
            return 'right'
        return 'both'
    except Exception as e:
        logger.warning(f"[人声检测] 异常: {e}")
        return 'both'


def _analyze_channel_energy(signal, sample_rate):
    frame_len = 2048
    hop_len = 1024
    voice_lo, voice_hi = 85, 3000

    total_energy = 0.0

    for start in range(0, len(signal) - frame_len, hop_len):
        frame = signal[start:start + frame_len]
        window = np.hanning(frame_len)
        fft = np.abs(np.fft.rfft(frame * window))
        freqs = np.fft.rfftfreq(frame_len, 1.0 / sample_rate)

        voice_mask = (freqs >= voice_lo) & (freqs <= voice_hi)
        total_energy += np.sum(fft[voice_mask] ** 2)

    return total_energy


def detect_voice_onset(audio_segment, energy_threshold_ratio=0.02, min_onset_ms=10):
    """检测音频片段中人声实际起始点，返回起始偏移量(ms)。

    通过短时RMS能量检测第一个超过阈值的位置。
    """
    if not HAS_NUMPY:
        return 0

    try:
        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        dtype = dtype_map.get(audio_segment.sample_width, np.int16)
        samples = np.frombuffer(audio_segment.raw_data, dtype=dtype).astype(np.float64)

        if audio_segment.channels == 2:
            samples = (samples[0::2] + samples[1::2]) / 2.0

        sr = audio_segment.frame_rate
        if sr <= 0 or len(samples) < sr // 10:
            return 0

        frame_len = min(1024, len(samples) // 4)
        hop_len = frame_len // 2
        if frame_len < 64:
            return 0

        # 计算全局RMS用于确定阈值
        global_rms = np.sqrt(np.mean(samples ** 2))
        threshold = global_rms * energy_threshold_ratio
        if threshold < 1.0:
            threshold = 1.0

        # 逐帧检测能量超过阈值的位置
        for start in range(0, len(samples) - frame_len, hop_len):
            frame = samples[start:start + frame_len]
            rms = np.sqrt(np.mean(frame ** 2))
            if rms >= threshold:
                onset_ms = (start / sr) * 1000.0
                return max(onset_ms, min_onset_ms)

        return 0
    except Exception:
        return 0


def find_nearest_onset(mono_samples, sr, target_ms, search_start_ms, search_end_ms,
                       frame_len=1024, hop_len=512, max_offset_ms=500):
    """在单声道样本数组指定范围内检测人声起始点，返回最接近 target_ms 的起始点。

    用于基于原音频内容纠正字幕时间戳的误差。
    检测方法：短时能量差分，找能量上升沿（onset）。

    Args:
        mono_samples: 单声道 numpy 数组（float64），通常是预计算的，避免重复加载
        sr: 采样率
        target_ms: 目标时间戳（毫秒，来自字幕）
        search_start_ms: 搜索范围起始（毫秒）
        search_end_ms: 搜索范围结束（毫秒）
        frame_len: 帧长度（采样点），默认 1024
        hop_len: 帧步长（采样点），默认 512（44100Hz 下约 11.6ms）
        max_offset_ms: 允许的最大偏移量（毫秒），超过则不采纳

    Returns:
        int: 最接近 target_ms 的人声起始点（毫秒），未找到合适点则返回 target_ms
    """
    if not HAS_NUMPY or mono_samples is None or len(mono_samples) == 0 or sr <= 0:
        return target_ms

    try:
        # 切取搜索范围（view，不复制数据）
        start_sample = max(0, int(search_start_ms * sr / 1000))
        end_sample = min(int(search_end_ms * sr / 1000), len(mono_samples))

        # 至少需要 1 秒的数据
        if end_sample - start_sample < sr:
            return target_ms

        segment = mono_samples[start_sample:end_sample]

        # 计算短时 RMS 能量（向量化，避免 Python for 循环）
        num_frames = max(1, (len(segment) - frame_len) // hop_len + 1)
        energy = np.zeros(num_frames)
        for i in range(num_frames):
            start = i * hop_len
            end = min(start + frame_len, len(segment))
            if end > start:
                frame = segment[start:end]
                energy[i] = np.sqrt(np.mean(frame ** 2))

        if len(energy) < 3:
            return target_ms

        # 计算能量差分（只保留正向变化 = 能量上升沿）
        diff = np.diff(energy)
        diff[diff < 0] = 0

        max_diff = float(np.max(diff)) if len(diff) > 0 else 0.0
        if max_diff < 1e-10:
            return target_ms

        # 阈值 = 最大差分的 30%
        threshold = max_diff * 0.3

        # 找所有超过阈值的峰值
        onset_indices = np.where(diff >= threshold)[0]
        if len(onset_indices) == 0:
            return target_ms

        # 转换为原音频中的绝对时间（毫秒）
        onset_times_ms = []
        for idx in onset_indices:
            sample_offset = idx * hop_len
            abs_time_ms = (start_sample + sample_offset) * 1000.0 / sr
            onset_times_ms.append(abs_time_ms)

        onset_times_ms = np.array(onset_times_ms)
        distances = np.abs(onset_times_ms - target_ms)
        nearest_idx = int(np.argmin(distances))
        nearest_ms = int(onset_times_ms[nearest_idx])
        nearest_distance = float(distances[nearest_idx])

        # 偏移超过 max_offset_ms 则不采纳（可能检测错了段落）
        if nearest_distance > max_offset_ms:
            return target_ms

        return nearest_ms
    except Exception as e:
        logger.warning(f"[内容对齐] 检测异常: {e}")
        return target_ms


def overlay_with_pan(original, mix_segment, position, angle_degrees):
    import math
    if mix_segment.channels == 1:
        mix_mono = mix_segment
    else:
        mix_mono = mix_segment.split_to_mono()[0]

    if original.channels == 1:
        return original.overlay(mix_mono, position=position)

    if np is None:
        return original.overlay(mix_mono, position=position)

    try:
        left, right = original.split_to_mono()

        angle_rad = angle_degrees * math.pi / 180.0
        left_gain = math.cos(angle_rad / 2.0)
        right_gain = math.sin(angle_rad / 2.0)

        sr = original.frame_rate
        sw = original.sample_width
        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        dtype = dtype_map.get(sw, np.int16)
        max_val = 2 ** (sw * 8 - 1) - 1

        left_raw = np.array(left.get_array_of_samples(), dtype=dtype).astype(np.float64)
        right_raw = np.array(right.get_array_of_samples(), dtype=dtype).astype(np.float64)

        if mix_mono.frame_rate != sr:
            mix_mono = mix_mono.set_frame_rate(sr)
        mix_dtype = dtype_map.get(mix_mono.sample_width, np.int16)
        mix_raw = np.frombuffer(mix_mono.raw_data, dtype=mix_dtype).astype(np.float64)

        start_frame = round(position * sr / 1000)
        end_frame = min(start_frame + len(mix_raw), len(left_raw), len(right_raw))
        mix_len = end_frame - start_frame
        if mix_len > 0:
            mix_chunk = mix_raw[:mix_len]
            left_raw[start_frame:end_frame] += mix_chunk * left_gain
            right_raw[start_frame:end_frame] += mix_chunk * right_gain

        combined = np.stack([left_raw, right_raw], axis=1)
        threshold = 0.9
        x = combined / max_val
        abs_x = np.abs(x)
        sign_x = np.sign(x)
        excess = np.maximum(abs_x - threshold, 0.0) / (1.0 - threshold)
        clipped_abs = np.where(
            abs_x > threshold,
            threshold + (1.0 - threshold) * np.tanh(excess),
            abs_x,
        )
        combined = sign_x * clipped_abs * max_val
        combined = np.clip(combined, -max_val - 1, max_val).astype(dtype)

        interleaved = np.empty(len(combined) * 2, dtype=dtype)
        interleaved[0::2] = combined[:, 0]
        interleaved[1::2] = combined[:, 1]

        return left._spawn(interleaved.tobytes(), overrides={'channels': 2})
    except Exception:
        return original.overlay(mix_mono, position=position)


def detect_angles_parallel(original_audio, audio_files, channel_map, max_workers=4):
    if not HAS_NUMPY:
        return {af["filename"]: 180 for af in audio_files}

    sr = original_audio.frame_rate
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map.get(original_audio.sample_width, np.int16)
    channels = original_audio.channels
    frame_bytes = original_audio.sample_width * channels
    raw = original_audio.raw_data
    total_frames = len(raw) // frame_bytes

    min_segment_samples = int(2 * sr)  # 至少 2 秒才检测
    window_frames = int(10000 * sr / 1000)

    def detect_one(audio_info):
        t_ms = audio_info["timestamp_ms"]
        start_frame = int(t_ms * sr / 1000)
        end_frame = min(start_frame + window_frames, total_frames)
        voice_channel = 'both'
        if end_frame - start_frame >= min_segment_samples:
            # 仅对 10s 窗口转换（约几 MB），避免整段音频常驻为 float32 大数组
            start_byte = start_frame * frame_bytes
            end_byte = end_frame * frame_bytes
            chunk = np.frombuffer(raw[start_byte:end_byte], dtype=dtype)
            if channels == 2:
                segment = chunk.reshape(-1, 2).astype(np.float32)
            else:
                segment = np.column_stack([chunk, chunk]).astype(np.float32)
            voice_channel = _detect_voice_channel_from_samples(segment, sr)
        angle = channel_map.get(voice_channel, 180)
        return audio_info["filename"], voice_channel, angle

    results = {}
    thread_count = min(max_workers, len(audio_files))
    if thread_count <= 1:
        for af in audio_files:
            fn, vc, angle = detect_one(af)
            results[fn] = angle
            logger.debug(f"[检测] {fn} 人声在{vc} → 混音角度{angle}°")
    else:
        # 复用共享检测池：外层（批量任务）与内层（本段检测）同池排队，
        # 总并发不超过容量，避免批量模式下并发数乘方。
        executor = _get_pan_detect_executor(thread_count)
        futures = {executor.submit(detect_one, af): af for af in audio_files}
        for future in as_completed(futures):
            fn, vc, angle = future.result()
            results[fn] = angle
            logger.debug(f"[检测] {fn} 人声在{vc} → 混音角度{angle}°")
    return results


def compute_rms_db(audio_segment):
    if np is None:
        return -20.0
    try:
        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        dtype = dtype_map.get(audio_segment.sample_width, np.int16)
        samples = np.frombuffer(audio_segment.raw_data, dtype=dtype).astype(np.float64)
        rms = np.sqrt(np.mean(samples ** 2))
        if rms < 1:
            return -100.0
        max_val = 2 ** (audio_segment.sample_width * 8 - 1)
        return 20.0 * np.log10(rms / max_val)
    except Exception:
        return -20.0


def _mono_float_from_frames(raw, dtype, channels, width, f0, f1):
    """按帧区间切片原始字节并转为单声道 float64，内存有界（不物化整段音频）。"""
    if f1 <= f0:
        return None
    buf = raw[f0 * width * channels: f1 * width * channels]
    arr = np.frombuffer(buf, dtype=dtype)
    if channels == 2:
        return (arr[0::2].astype(np.float64) + arr[1::2].astype(np.float64)) * 0.5
    return arr.astype(np.float64)


def compute_rms_envelope(audio_segment, window_ms=100, hop_ms=50):
    if np is None:
        return None, hop_ms
    try:
        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        dtype = dtype_map.get(audio_segment.sample_width, np.int16)
        raw = audio_segment.raw_data
        channels = audio_segment.channels
        width = audio_segment.sample_width
        sr = audio_segment.frame_rate
        window_frames = int(window_ms * sr / 1000)
        hop_frames = int(hop_ms * sr / 1000)
        if window_frames <= 0 or hop_frames <= 0:
            return None, hop_ms
        max_val = 2 ** (width * 8 - 1)

        total_frames = len(raw) // (width * channels)

        if total_frames < window_frames:
            # 数据不足一个窗口，返回单点包络
            mono = _mono_float_from_frames(raw, dtype, channels, width, 0, total_frames)
            rms_val = float(np.sqrt(np.mean(mono * mono))) if mono is not None and len(mono) else 0.0
            return np.array([20.0 * np.log10(max(rms_val, 1.0) / max_val)]), hop_ms

        # 分块向量化：窗口起点固定在全局 hop 整数倍上，每个块只转换其所覆盖的帧区间，
        # 避免把整段音频一次性转成 float64、也不展开全部 (num_windows × window_frames) 方阵。
        window_count = ((total_frames - window_frames) // hop_frames) + 1
        wins_per_block = 128
        parts = []
        for s0 in range(0, window_count, wins_per_block):
            s1 = min(s0 + wins_per_block, window_count)
            starts = np.arange(s0, s1, dtype=np.int64) * hop_frames
            mono = _mono_float_from_frames(
                raw, dtype, channels, width,
                int(starts[0]), int(starts[-1] + window_frames),
            )
            wins = np.lib.stride_tricks.sliding_window_view(mono, window_frames)[::hop_frames]
            rms_vals = np.sqrt(np.mean(wins * wins, axis=1))
            parts.append(20.0 * np.log10(np.maximum(rms_vals, 1.0) / max_val))
        return np.concatenate(parts), hop_ms
    except Exception:
        return None, hop_ms


def interpolate_envelope(envelope, hop_ms, frame_rate, start_ms, num_frames):
    if envelope is None or len(envelope) == 0:
        return np.full(num_frames, -20.0)
    hop_frames = int(hop_ms * frame_rate / 1000)
    if hop_frames <= 0:
        return np.full(num_frames, envelope[0])
    sample_indices = np.arange(num_frames)
    start_frame = int(start_ms * frame_rate / 1000)
    abs_indices = start_frame + sample_indices
    env_indices = abs_indices / hop_frames
    env_indices = np.clip(env_indices, 0, len(envelope) - 1)
    idx_floor = np.floor(env_indices).astype(np.int32)
    idx_ceil = np.minimum(idx_floor + 1, len(envelope) - 1)
    frac = env_indices - idx_floor
    return envelope[idx_floor] * (1.0 - frac) + envelope[idx_ceil] * frac


def mix_with_numpy(original, mix_items, volume_db=0, auto_volume="off",
                   align_onset=False, content_alignment=False):
    if np is None or not HAS_NUMPY:
        logger.debug("[混音] numpy不可用，使用pydub fallback")
        result = original
        for mix_seg, pos_ms, angle in mix_items:
            result = overlay_with_pan(result, mix_seg, pos_ms, angle)
        return result

    try:
        sample_width = original.sample_width
        frame_rate = original.frame_rate
        channels = original.channels
        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        dtype = dtype_map.get(sample_width, np.int16)

        # 内容对齐：预先提取所有时间戳，用于确定搜索范围
        all_pos_ms = [item[1] for item in mix_items] if mix_items else []
        total_duration_ms = len(original) if hasattr(original, '__len__') else 0
        if content_alignment and all_pos_ms:
            logger.debug(f"[内容对齐] 启用, 共 {len(all_pos_ms)} 个片段, 总时长 {total_duration_ms}ms")

        logger.debug(f"[混音] sample_width={sample_width}, frame_rate={frame_rate}, "
              f"channels={channels}, dtype={dtype}, mix_items={len(mix_items)}, vol={volume_db}dB")

        raw = np.frombuffer(original.raw_data, dtype=dtype)
        if channels == 2:
            if len(raw) % 2 != 0:
                raw = raw[:-1]
            stereo = raw.reshape(-1, 2).astype(np.float32)
        else:
            stereo = np.column_stack([raw.astype(np.float32), raw.astype(np.float32)])

        total_frames = len(stereo)

        # 内容对齐：预计算单声道样本（复用已加载的 stereo 数组，避免重复加载原音频）
        mono_samples = None
        if content_alignment and all_pos_ms:
            mono_samples = stereo.mean(axis=1)
            logger.debug(f"[内容对齐] 预计算单声道样本, 长度={len(mono_samples)}, "
                  f"约 {len(mono_samples) * 8 / 1024 / 1024:.1f}MB")

        vol_linear = 10.0 ** (volume_db / 20.0)

        original_rms_db = None
        original_rms_envelope = None
        env_hop_ms = 50
        if auto_volume == "fixed":
            original_rms_db = compute_rms_db(original)
            logger.debug(f"[混音] auto_volume=fixed, 原始音频RMS={original_rms_db:.1f}dBFS, volume_db={volume_db}")
        elif auto_volume == "auto":
            original_rms_envelope, env_hop_ms = compute_rms_envelope(original, window_ms=100, hop_ms=50)
            if original_rms_envelope is not None:
                logger.debug(f"[混音] auto_volume=auto, 包络长度={len(original_rms_envelope)}, hop={env_hop_ms}ms, volume_db={volume_db}")
            else:
                original_rms_db = compute_rms_db(original)
                logger.debug(f"[混音] auto_volume=auto fallback to fixed, RMS={original_rms_db:.1f}dBFS")

        for i, (mix_seg, pos_ms, angle) in enumerate(mix_items):
            if mix_seg.channels == 1:
                mix_mono = mix_seg
            else:
                mix_mono = mix_seg.split_to_mono()[0]

            if mix_seg.frame_rate != frame_rate:
                mix_mono = mix_mono.set_frame_rate(frame_rate)

            actual_pos_ms = pos_ms

            # 内容对齐：在原音频中搜索最接近的人声起始点，纠正字幕时间戳误差
            if content_alignment and mono_samples is not None:
                search_start = all_pos_ms[i - 1] if i > 0 else 0
                search_end = all_pos_ms[i + 1] if i < len(all_pos_ms) - 1 else total_duration_ms
                if search_end > search_start:
                    new_pos = find_nearest_onset(
                        mono_samples, frame_rate, pos_ms, search_start, search_end
                    )
                    if new_pos != pos_ms:
                        if i < 5:
                            logger.debug(f"[内容对齐] 片段{i}: {pos_ms}ms → {new_pos}ms "
                                  f"(搜索范围 {search_start}-{search_end}ms)")
                        actual_pos_ms = new_pos

            onset_trim_frames = 0
            if align_onset:
                onset_ms = detect_voice_onset(mix_seg)
                if onset_ms > 0:
                    adjusted = actual_pos_ms - onset_ms
                    if adjusted >= 0:
                        actual_pos_ms = adjusted
                    else:
                        # 调整后位置为负，需要裁掉片段开头部分
                        onset_trim_frames = round(-adjusted * frame_rate / 1000)
                        actual_pos_ms = 0
                    if i < 5:
                        logger.debug(f"[对齐] 片段{i}: 原始pos={pos_ms}ms, onset={onset_ms:.0f}ms, "
                              f"调整后pos={actual_pos_ms}ms, trim={onset_trim_frames}帧")

            mix_dtype = dtype_map.get(mix_mono.sample_width, np.int16)
            mix_raw = np.frombuffer(mix_mono.raw_data, dtype=mix_dtype).astype(np.float32)
            if mix_mono.sample_width != sample_width:
                scale = 2 ** (sample_width * 8 - 1) / 2 ** (mix_mono.sample_width * 8 - 1)
                mix_raw *= scale

            if auto_volume == "fixed" and original_rms_db is not None:
                seg_rms_db = compute_rms_db(mix_mono)
                target_rms_db = original_rms_db + volume_db
                gain_db = target_rms_db - seg_rms_db
                gain_db = max(-50.0, min(50.0, gain_db))
                gain_linear = 10.0 ** (gain_db / 20.0)
                mix_raw *= gain_linear
                if i < 3:
                    logger.debug(f"[混音] 片段{i}: seg_rms={seg_rms_db:.1f}dBFS, gain={gain_db:.1f}dB ({gain_linear:.3f}x)")
            elif auto_volume == "auto" and original_rms_envelope is not None:
                seg_rms_db = compute_rms_db(mix_mono)
                orig_env_at_seg = interpolate_envelope(
                    original_rms_envelope, env_hop_ms, frame_rate,
                    actual_pos_ms, len(mix_raw),
                )
                target_rms = orig_env_at_seg + volume_db
                gain_db = target_rms - seg_rms_db
                gain_db = np.clip(gain_db, -50.0, 50.0)
                gain_curve = 10.0 ** (gain_db / 20.0)
                mix_raw *= gain_curve
                if i < 3:
                    logger.debug(f"[混音] 片段{i}: seg_rms={seg_rms_db:.1f}dBFS, "
                          f"orig_env=[{orig_env_at_seg[0]:.1f}..{orig_env_at_seg[-1]:.1f}]dBFS, "
                          f"gain=[{np.min(gain_db):.1f}..{np.max(gain_db):.1f}]dB")
            else:
                mix_raw *= vol_linear

            if onset_trim_frames > 0 and onset_trim_frames < len(mix_raw):
                mix_raw = mix_raw[onset_trim_frames:]
            mix_frames = len(mix_raw)

            start_frame = round(actual_pos_ms * frame_rate / 1000)
            end_frame = min(start_frame + mix_frames, total_frames)
            mix_len = end_frame - start_frame
            if mix_len <= 0:
                logger.debug(f"[混音] 片段{i}: start={start_frame}, end={end_frame}, mix_len={mix_len} (跳过)")
                continue

            mix_chunk = mix_raw[:mix_len]

            angle_rad = angle * math.pi / 180.0
            left_gain = math.cos(angle_rad / 2.0)
            right_gain = math.sin(angle_rad / 2.0)

            stereo[start_frame:end_frame, 0] += mix_chunk * left_gain
            stereo[start_frame:end_frame, 1] += mix_chunk * right_gain

            if i < 3:
                logger.debug(f"[混音] 片段{i}: pos={pos_ms}ms, frames={mix_frames}, angle={angle}°, "
                      f"L_gain={left_gain:.3f}, R_gain={right_gain:.3f}, "
                      f"chunk_max={np.max(np.abs(mix_chunk)):.1f}")

        logger.debug(f"[混音] 累加后 max={np.max(np.abs(stereo)):.1f}")

        max_val = 2 ** (sample_width * 8 - 1) - 1

        threshold = 0.9
        x = stereo / max_val
        abs_x = np.abs(x)
        sign_x = np.sign(x)
        excess = np.maximum(abs_x - threshold, 0.0) / (1.0 - threshold)
        clipped_abs = np.where(
            abs_x > threshold,
            threshold + (1.0 - threshold) * np.tanh(excess),
            abs_x,
        )
        stereo = sign_x * clipped_abs * max_val
        stereo = np.clip(stereo, -max_val - 1, max_val).astype(dtype)

        logger.debug(f"[混音] result max={np.max(np.abs(stereo))}, 形状={stereo.shape}")

        interleaved = np.empty(total_frames * 2, dtype=dtype)
        interleaved[0::2] = stereo[:, 0]
        interleaved[1::2] = stereo[:, 1]

        AudioSegment_instance = original.__class__
        return AudioSegment_instance(
            interleaved.tobytes(),
            sample_width=sample_width,
            frame_rate=frame_rate,
            channels=2,
        )
    except Exception as e:
        logger.exception(f"[混音] numpy混音异常: {e}")
        result = original
        for mix_seg, pos_ms, angle in mix_items:
            result = overlay_with_pan(result, mix_seg, pos_ms, angle)
        return result


def export_with_nvenc(audio_segment, output_path, format_type="mp3",
                      bitrate="192k", sample_rate=44100, channels=2,
                      stop_check=None):
    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp_wav.close()

    ar_arg = str(sample_rate)
    ac_arg = str(channels)

    try:
        audio_segment.export(temp_wav.name, format="wav")

        if format_type == "mp3":
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", temp_wav.name,
                "-c:a", "libmp3lame",
                "-b:a", bitrate,
                "-ar", ar_arg,
                "-ac", ac_arg,
                output_path
            ]
        elif format_type in ("aac", "m4a"):
            if is_aac_nvenc_available():
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", temp_wav.name,
                    "-c:a", "aac_nvenc",
                    "-b:a", bitrate,
                    "-ar", ar_arg,
                    "-ac", ac_arg,
                    output_path
                ]
            else:
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", temp_wav.name,
                    "-c:a", "aac",
                    "-b:a", bitrate,
                    "-ar", ar_arg,
                    "-ac", ac_arg,
                    output_path
                ]
        elif format_type in ("hevc", "h265"):
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", temp_wav.name,
                "-c:a", "aac",
                "-b:a", bitrate,
                "-ar", ar_arg,
                "-ac", ac_arg,
                "-tag:v", "hvc1",
                output_path
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", temp_wav.name,
                "-acodec", "pcm_s16le",
                "-ar", ar_arg,
                "-ac", ac_arg,
                output_path
            ]

        _run_ffmpeg(cmd, stop_check=stop_check)
    finally:
        try:
            os.unlink(temp_wav.name)
        except Exception:
            pass


def extract_audio_from_video(video_path, stop_check=None):
    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp_wav.close()
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "44100", "-ac", "2",
        temp_wav.name
    ]
    try:
        _run_ffmpeg(cmd, stop_check=stop_check)
    except Exception:
        # 失败时清理临时文件，避免泄露
        try:
            os.unlink(temp_wav.name)
        except Exception:
            pass
        raise
    return temp_wav.name


def replace_audio_in_video(video_path, audio_path, output_path,
                           bitrate="192k", sample_rate=44100, channels=2,
                           stop_check=None):
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", bitrate,
        "-ar", str(sample_rate), "-ac", str(channels),
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        output_path
    ]
    _run_ffmpeg(cmd, stop_check=stop_check)


def _run_ffmpeg(cmd, stop_check=None):
    """运行 ffmpeg 命令，支持通过 stop_check 回调中断。

    stop_check 是一个无参 callable，返回 True 时终止子进程并抛出 RuntimeError。
    """
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
    )
    try:
        while proc.poll() is None:
            if stop_check is not None and stop_check():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise RuntimeError("用户已停止处理")
            time.sleep(0.1)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, cmd, stdout, stderr
            )
    finally:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
