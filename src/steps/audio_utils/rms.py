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


def _frame_db_from_interval(audio_segment, start_ms, end_ms):
    """区间内 20ms 帧的 dBFS 序列；只读取该区间，不复制整段音频。"""
    sr = audio_segment.frame_rate
    channels = audio_segment.channels
    width = audio_segment.sample_width
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map.get(width, np.int16)
    max_val = 2 ** (width * 8 - 1)
    total_frames = len(audio_segment.raw_data) // (width * channels)
    f0 = max(0, min(total_frames, int(round(start_ms * sr / 1000.0))))
    f1 = max(0, min(total_frames, int(round(end_ms * sr / 1000.0))))
    if f1 <= f0:
        return np.empty(0)
    buf = audio_segment.raw_data[f0 * width * channels: f1 * width * channels]
    arr = np.frombuffer(buf, dtype=dtype)
    arr = arr[:len(arr) // channels * channels].reshape(-1, channels)
    if not len(arr):
        return np.empty(0)
    frame_len = max(1, int(round(sr * 0.02)))
    hop = max(1, int(round(sr * 0.01)))
    if len(arr) < frame_len:
        power = np.mean(arr.astype(np.float64) ** 2)
        energies = np.array([power])
    else:
        frames = arr.astype(np.float64)
        # 先算各声道能量再平均，避免 L/R 反相抵消。
        power = np.mean(frames * frames, axis=1)
        cumulative = np.empty(len(power) + 1, dtype=np.float64)
        cumulative[0] = 0
        np.cumsum(power, out=cumulative[1:])
        starts = np.arange(0, len(power) - frame_len + 1, hop, dtype=np.int64)
        energies = (cumulative[starts + frame_len] - cumulative[starts]) / frame_len
    return 10.0 * np.log10(np.maximum(energies / (max_val * max_val), 1e-10))


def speech_intervals_from_positions(positions_ms, total_duration_ms,
                                    max_span_ms=5000):
    """把配音落点变成原音频的候选语音区间。

    每条字幕从自己的时间戳到下一條字幕/文件末尾，最长取 5 秒；这样既能
    覆盖大多数整句，又不会把长段音乐/静音整块算进参考电平。
    """
    total = max(0, int(round(total_duration_ms or 0)))
    points = sorted({max(0, min(total, int(round(p))))
                     for p in positions_ms if p is not None})
    intervals = []
    for i, start in enumerate(points):
        nxt = points[i + 1] if i + 1 < len(points) else total
        end = min(total, start + max_span_ms, nxt)
        if end > start:
            intervals.append((start, end))
    return intervals


def compute_speech_rms_db(audio_segment, intervals_ms=None,
                          gate_db=-50.0, percentile=70.0):
    """原音频“人声段”参考电平：只统计字幕覆盖的区间，并滤掉静音帧。

    比整条音频 RMS 更接近用户听到的原人声；没有有效区间/全静音时回退到
    整条 RMS，保证旧行为仍有兜底。
    """
    if np is None:
        return -20.0
    try:
        if not intervals_ms:
            return compute_rms_db(audio_segment)
        values = []
        for start, end in intervals_ms:
            db = _frame_db_from_interval(audio_segment, start, end)
            if len(db):
                values.append(db)
        if not values:
            return compute_rms_db(audio_segment)
        all_db = np.concatenate(values)
        active = all_db[all_db >= gate_db]
        if not len(active):
            return compute_rms_db(audio_segment)
        return float(np.percentile(active, percentile))
    except Exception:
        return compute_rms_db(audio_segment)


def mask_speech_envelope(envelope, hop_ms, intervals_ms,
                         gate_db=-50.0, smooth_ms=200):
    """只保留字幕区间内的有效语音包络，静音/音乐处保持上一帧。

    返回与输入等长的数组；若所有区间都低于门限（例如纯静音测试音频），
    返回 None，由调用方决定回退。
    """
    if envelope is None or not len(envelope):
        return None
    n = len(envelope)
    mask = np.zeros(n, dtype=bool)
    for start, end in intervals_ms or []:
        i0 = max(0, int(round(start / hop_ms)))
        i1 = min(n, int(round(end / hop_ms)) + 1)
        if i1 > i0:
            mask[i0:i1] = True
    mask &= envelope >= gate_db
    valid = np.flatnonzero(mask)
    if not len(valid):
        return None
    # 每个位置取最近的已有效帧；静音处保持上一帧的人声电平。
    pos = np.searchsorted(valid, np.arange(n, dtype=np.int64), side="right") - 1
    pos[pos < 0] = 0
    filled = envelope[valid[pos]]
    smooth_frames = max(1, int(round(smooth_ms / hop_ms))) if smooth_ms else 1
    if smooth_frames > 1:
        padded = np.pad(filled, (smooth_frames // 2,
                                 smooth_frames - 1 - smooth_frames // 2),
                        mode="edge")
        filled = np.convolve(padded, np.ones(smooth_frames) / smooth_frames,
                             mode="valid")
    return filled


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
