# -*- coding: utf-8 -*-
"""人声检测：声道能量检测、起始点检测与并行角度检测。"""
from concurrent.futures import as_completed

from .common import HAS_NUMPY, np, logger, _submit_pan_detection

# ---------------------------------------------------------------- 声道检测常量
# 人声频段（Hz）：男声基频 ~85Hz 起，3 kHz 以上基本是齿音/噪声，对"人在哪一侧"
# 贡献小且容易被音乐占据。
VOICE_BAND = (85.0, 3000.0)
# 判定"明显偏侧"的归一化不平衡度 |L−R| / (L+R)。
# 旧写法 ``left > right * 1.2`` 只相当于不平衡度 0.091 —— 背景音乐差 10% 就被
# 判成"人声在左/右"，灵敏得离谱，用户只能把三个角度全往中间收来兜底。
PAN_IMBALANCE_THRESHOLD = 0.30
# 目标帧长（毫秒）：语音分析的标准量级是 20–40 ms。旧实现固定 2048 点，降采样到
# ≤16 kHz 后约 93 ms 一帧，短促辅音全被抹平。
TARGET_FRAME_MS = 23.0
# 帧长按采样率换算后夹在 256–1024 点：8 kHz 窄带语音不会拿到 128 ms 的超长帧，
# 96 kHz 高采样率也不会把帧缩到 10 ms 以下。
FRAME_LEN_MIN = 256
FRAME_LEN_MAX = 1024


def _frame_params(sample_rate):
    """按采样率换算 (帧长, 步长)，保证任何常见采样率下帧长都在 20–40 ms 量级。"""
    frame = int(round(sample_rate * TARGET_FRAME_MS / 1000.0))
    frame = max(FRAME_LEN_MIN, min(FRAME_LEN_MAX, frame))
    return frame, frame // 2


def _voice_band_frame_energies(signal, sample_rate):
    """逐帧返回人声频段（85–3000 Hz）能量，长度 = 帧数。

    **不降采样**：旧实现先 ``signal[::factor]`` 抽点把采样率压到 ≤16 kHz，既没有
    抗混叠（高频会折返回人声频段污染比较），又把帧长拖到 ~93 ms。现在直接在原采样率
    上按 23 ms 取帧——FFT 总点数与旧实现基本持平（旧：429 帧 × 2048 点；
    44.1 kHz 新：869 帧 × 1014 点），却既没有别名、帧长也落在语音量级。
    """
    frame_len, hop_len = _frame_params(sample_rate)
    if len(signal) <= frame_len:
        return np.zeros(0)
    window = np.hanning(frame_len)
    frames = np.lib.stride_tricks.sliding_window_view(signal, frame_len)[::hop_len]
    fft = np.abs(np.fft.rfft(frames * window, axis=1))
    freqs = np.fft.rfftfreq(frame_len, 1.0 / sample_rate)
    mask = (freqs >= VOICE_BAND[0]) & (freqs <= VOICE_BAND[1])
    return np.sum(fft[:, mask] ** 2, axis=1)


def detect_voice_channel(samples_2d, sr):
    """检测原音频人声偏向哪个声道，返回 ``(label, confidence)``。

    ``label`` ∈ ``{'left', 'right', 'both'}``；``confidence`` ∈ [0, 1]，
    是"偏侧判定"的置信度——``'both'`` 时恒为 0，表示**没有可用的侧向信息**
    （人声确实居中、或根本判不出），调用方不应把它当成" confidently centered"。

    相对旧实现的修正：
    1. **阈值**：改用归一化不平衡度 ``|L−R| / (L+R)``，阈值 0.30（旧 1.2× 只
       相当于 9% 不平衡度，音乐差一成就误判）。
    2. **帧长**：按采样率换算到 ~23 ms，替代旧的 ~93 ms。
    3. **不再降采样**：旧实现抽点降采样没有抗混叠，高频会折返回人声频段。
    4. **返回置信度**（旧实现没有），打进日志就能回看"判出来的"和"猜的"。

    关于"语音门控"：曾加过一版"只比较语音活跃帧"（帧能量 ≥ 窗口峰值 15%），
    并用 300 例随机合成音频做对照实验——门控开/关判定结果**完全一致（0 例差异）**。
    原因是门控用 OR 合并两声道，而真正把音乐挡在外面的是 85–3000 Hz 带通；
    门控只在两声道都安静时减少计算量，对判定没有影响。已移除，避免留一段
    "看起来很稳健、实际不干活"的代码。
    """
    if not HAS_NUMPY or samples_2d is None or len(samples_2d) < sr * 2:
        return 'both', 0.0

    try:
        left = samples_2d[:, 0]
        right = samples_2d[:, 1]

        left_frames = _voice_band_frame_energies(left, sr)
        right_frames = _voice_band_frame_energies(right, sr)
        if len(left_frames) < 3:
            return 'both', 0.0

        left_energy = float(left_frames.sum())
        right_energy = float(right_frames.sum())
        if left_energy + right_energy < 1e-10:
            return 'both', 0.0

        imbalance = abs(left_energy - right_energy) / (left_energy + right_energy)
        logger.debug(
            f"[人声检测] 左 {left_energy:.1f} / 右 {right_energy:.1f}，"
            f"不平衡度 {imbalance:.3f}（阈值 {PAN_IMBALANCE_THRESHOLD}）")

        if imbalance < PAN_IMBALANCE_THRESHOLD:
            return 'both', 0.0
        label = 'left' if left_energy > right_energy else 'right'
        confidence = min(1.0, imbalance / PAN_IMBALANCE_THRESHOLD)
        return label, confidence
    except Exception as e:
        logger.warning(f"[人声检测] 异常: {e}")
        return 'both', 0.0


def _detect_voice_channel_from_samples(samples_2d, sr):
    """只取标签的兼容包装（内部请直接用 ``detect_voice_channel``）。"""
    return detect_voice_channel(samples_2d, sr)[0]


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


def _frame_rms_energy(segment, frame_len, hop_len):
    """分块滑动窗口的短时 RMS 能量（逐帧循环的向量化等价实现）。

    用块内累积和计算窗口能量，避免一次物化 (帧数 × 帧长) 的窗口矩阵，
    因此搜索区间很长时内存依然有界；数值上与逐帧 `sqrt(mean(frame**2))` 一致。
    """
    if not len(segment):
        return np.zeros(0)
    if len(segment) <= frame_len:
        return np.array([np.sqrt(np.mean(segment ** 2))], dtype=np.float64)

    num_frames = (len(segment) - frame_len) // hop_len + 1
    energy = np.empty(num_frames, dtype=np.float64)
    wins_per_block = 128
    local_starts = np.arange(wins_per_block) * hop_len
    for s0 in range(0, num_frames, wins_per_block):
        s1 = min(s0 + wins_per_block, num_frames)
        f0 = s0 * hop_len
        f1 = (s1 - 1) * hop_len + frame_len
        chunk = segment[f0:f1]
        cumulative = np.empty(len(chunk) + 1, dtype=np.float64)
        cumulative[0] = 0.0
        np.cumsum(chunk * chunk, out=cumulative[1:])
        local = local_starts[:s1 - s0]
        energy[s0:s1] = np.sqrt(
            (cumulative[local + frame_len] - cumulative[local]) / frame_len
        )
    return energy


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

        # 短时 RMS 能量：向量化的分块实现，避免逐帧 Python 循环，
        # 同时限制临时数组大小（搜索区间很长时也不会一次物化全部窗口）。
        energy = _frame_rms_energy(segment, frame_len, hop_len)

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
        voice_channel, confidence = 'both', 0.0
        if end_frame - start_frame >= min_segment_samples:
            # 仅对 10s 窗口转换（约几 MB），避免整段音频常驻为 float32 大数组
            start_byte = start_frame * frame_bytes
            end_byte = end_frame * frame_bytes
            chunk = np.frombuffer(raw[start_byte:end_byte], dtype=dtype)
            if channels == 2:
                segment = chunk.reshape(-1, 2).astype(np.float32)
            else:
                segment = np.column_stack([chunk, chunk]).astype(np.float32)
            voice_channel, confidence = detect_voice_channel(segment, sr)
        angle = channel_map.get(voice_channel, 180)
        return audio_info["filename"], voice_channel, angle, confidence

    results = {}
    thread_count = min(max_workers, len(audio_files))
    if thread_count <= 1:
        for af in audio_files:
            fn, vc, angle, conf = detect_one(af)
            results[fn] = angle
            logger.debug(f"[检测] {fn} 人声在{vc}(置信{conf:.2f}) → 混音角度{angle}°")
    else:
        # 复用共享检测池：外层（批量任务）与内层（本段检测）同池排队，
        # 总并发不超过容量，避免批量模式下并发数乘方。
        # 池容量按配置固定，不随每个任务的片段数变化。
        futures = _submit_pan_detection(detect_one, audio_files, max_workers)
        for future in as_completed(futures):
            fn, vc, angle, conf = future.result()
            results[fn] = angle
            logger.debug(f"[检测] {fn} 人声在{vc}(置信{conf:.2f}) → 混音角度{angle}°")
    return results
