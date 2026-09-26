# -*- coding: utf-8 -*-
"""RMS 计算与音量包络（固定音量/自动音量模式共用）。"""
from .common import HAS_NUMPY, np


def compute_rms_db(audio_segment):
    if np is None:
        return -20.0
    try:
        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        dtype = dtype_map.get(audio_segment.sample_width, np.int16)
        samples = np.frombuffer(audio_segment.raw_data, dtype=dtype)
        if not len(samples):
            return -100.0
        # 每次最多转换约 8MB，长音频不再分配两份整段 float64。
        energy = 0.0
        for start in range(0, len(samples), 1 << 20):
            block = samples[start:start + (1 << 20)].astype(np.float64)
            energy += float(np.sum(block * block))
        rms = np.sqrt(energy / len(samples))
        max_val = 2 ** (audio_segment.sample_width * 8 - 1)
        return float(20.0 * np.log10(max(rms / max_val, 1e-5)))
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
            return np.array([compute_rms_db(audio_segment)]), hop_ms

        # 分块向量化：窗口起点固定在全局 hop 整数倍上，每个块只转换其所覆盖的帧区间，
        # 避免把整段音频一次性转成 float64、也不展开全部 (num_windows × window_frames) 方阵。
        window_count = ((total_frames - window_frames) // hop_frames) + 1
        wins_per_block = 128
        parts = []
        for s0 in range(0, window_count, wins_per_block):
            s1 = min(s0 + wins_per_block, window_count)
            starts = np.arange(s0, s1, dtype=np.int64) * hop_frames
            f0, f1 = int(starts[0]), int(starts[-1] + window_frames)
            samples = np.frombuffer(raw, dtype=dtype,
                                    count=(f1 - f0) * channels,
                                    offset=f0 * width * channels)
            frames = samples.reshape(-1, channels).astype(np.float64)
            # 先算各声道能量再求平均，避免 L/R 反相抵消导致配音忽大忽小。
            power = np.mean(frames * frames, axis=1)
            cumulative = np.empty(len(power) + 1, dtype=np.float64)
            cumulative[0] = 0
            np.cumsum(power, out=cumulative[1:])
            local = starts - starts[0]
            energies = (cumulative[local + window_frames] - cumulative[local]) / window_frames
            rms_vals = np.sqrt(np.maximum(energies, 0)) / max_val
            parts.append(20.0 * np.log10(np.maximum(rms_vals, 1e-5)))
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
