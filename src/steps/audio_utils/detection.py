# -*- coding: utf-8 -*-
"""人声检测：声道能量检测、起始点检测与并行角度检测。"""
from concurrent.futures import as_completed

from .common import HAS_NUMPY, np, logger, _submit_pan_detection


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

    if len(signal) <= frame_len:
        return 0.0

    # 向量化：滑动窗口一次取出全部帧，批量 rfft，避免逐帧 Python 循环
    # （每片段上百次 FFT 调用 → 单次二维 FFT，声道检测可提速一个数量级）
    n_frames = (len(signal) - frame_len) // hop_len + 1
    window = np.hanning(frame_len)
    frames = np.lib.stride_tricks.sliding_window_view(signal, frame_len)[::hop_len]
    fft = np.abs(np.fft.rfft(frames * window, axis=1))
    freqs = np.fft.rfftfreq(frame_len, 1.0 / sample_rate)

    voice_mask = (freqs >= voice_lo) & (freqs <= voice_hi)
    return float(np.sum(fft[:, voice_mask] ** 2))


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

        # 能量检测不应先平均波形，否则左右反相时会被误判成静音。
        power = np.mean(samples.reshape(-1, audio_segment.channels) ** 2, axis=1)

        sr = audio_segment.frame_rate
        if sr <= 0 or len(power) < sr // 10:
            return 0

        frame_len = min(1024, len(power) // 4)
        hop_len = frame_len // 2
        if frame_len < 64:
            return 0

        # 计算全局RMS用于确定阈值
        global_rms = np.sqrt(np.mean(power))
        threshold = global_rms * energy_threshold_ratio
        if threshold < 1.0:
            threshold = 1.0

        # 滑动窗口直接归约功率，不物化窗口平方数组。
        n_frames = (len(power) - frame_len) // hop_len + 1
        block = 128
        for start_idx in range(0, n_frames, block):
            end_idx = min(start_idx + block, n_frames)
            frames = np.lib.stride_tricks.sliding_window_view(
                power, frame_len
            )[start_idx * hop_len:end_idx * hop_len:hop_len]
            rms = np.sqrt(np.mean(frames, axis=1))
            hit = np.argmax(rms >= threshold)
            if rms[hit] >= threshold:
                onset_ms = ((start_idx + hit) * hop_len / sr) * 1000.0
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
        # 池容量按配置固定，不随每个任务的片段数变化。
        futures = _submit_pan_detection(detect_one, audio_files, max_workers)
        for future in as_completed(futures):
            fn, vc, angle = future.result()
            results[fn] = angle
            logger.debug(f"[检测] {fn} 人声在{vc} → 混音角度{angle}°")
    return results
