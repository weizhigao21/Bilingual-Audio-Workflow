# -*- coding: utf-8 -*-
"""混音核心：pydub 叠加回退与 numpy 向量化混音。"""
import math
import os

from pydub import AudioSegment

from .common import HAS_NUMPY, np, logger
from .detection import find_nearest_onset, detect_voice_onset
from .rms import compute_rms_db, compute_rms_envelope, interpolate_envelope
from .ffmpeg_utils import resample_audio


def _check_stop(stop_check):
    if stop_check is not None and stop_check():
        raise RuntimeError("用户已停止处理")


def _finish_mix(samples, sample_width, mode, stop_check=None, output_path=None):
    """分块限幅与量化：临时内存不随整段音频长度增长。"""
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[sample_width]
    maximum = 2 ** (sample_width * 8 - 1) - 1
    block_frames = 1 << 18
    gain = 1.0
    if mode == "peak":
        peak = 0.0
        for start in range(0, len(samples), block_frames):
            _check_stop(stop_check)
            peak = max(peak, float(np.max(np.abs(samples[start:start + block_frames]))))
        # 两声道使用同一固定增益：保留声像/动态，留出 1dB 样本峰值余量。
        ceiling = maximum * 10 ** (-1.0 / 20.0)
        gain = min(1.0, ceiling / peak) if peak else 1.0
        logger.debug(f"[混音] 峰值保护衰减={20 * math.log10(gain):.2f}dB")
    elif mode != "soft":
        raise ValueError(f"未知峰值保护模式: {mode}")
    result = None if output_path else np.empty(samples.shape, dtype=dtype)
    output = open(output_path, "wb") if output_path else None
    try:
        for start in range(0, len(samples), block_frames):
            _check_stop(stop_check)
            # 32bit 上界必须能精确表示，float32 的 2147483647 会舍入到 2147483648。
            block = samples[start:start + block_frames].astype(np.float64)
            if mode == "peak":
                block *= gain
            else:
                normalized = block / maximum
                magnitude = np.abs(normalized)
                mask = magnitude > 0.9
                magnitude[mask] = 0.9 + 0.1 * np.tanh((magnitude[mask] - 0.9) / 0.1)
                block = np.copysign(magnitude, normalized) * maximum
            np.rint(block, out=block)
            np.clip(block, -maximum - 1, maximum, out=block)
            packed = block.astype(dtype)
            if output is None:
                result[start:start + block_frames] = packed
            else:
                output.write(packed.tobytes())
    finally:
        if output is not None:
            output.close()
    return result


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


def mix_with_numpy(original, mix_items, volume_db=0, auto_volume="off",
                   align_onset=False, content_alignment=False,
                   peak_mode="peak", high_quality=True, stop_check=None,
                   output_path=None, accumulator_path=None, item_callback=None):
    if np is None or not HAS_NUMPY:
        raise RuntimeError("高质量混音需要 numpy，请安装项目依赖")

    sample_width = original.sample_width
    frame_rate = original.frame_rate
    channels = original.channels
    if channels not in (1, 2):
        raise ValueError("混音目前仅支持单声道/立体声，请先将多声道源转换为立体声")
    _check_stop(stop_check)
    # 32bit PCM 使用双精度，避免高位深信号在混音前丢失低位。
    float_dtype = np.float64 if sample_width == 4 else np.float32
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
    stereo = None
    if output_path:
        if not accumulator_path or not len(raw):
            raise ValueError("流式混音需要非空音频与累加临时文件")
        total = len(raw) // channels
        stereo = np.memmap(accumulator_path, dtype=float_dtype, mode="w+", shape=(total, 2))
        try:
            for start in range(0, total, 1 << 18):
                _check_stop(stop_check)
                end = min(start + (1 << 18), total)
                frames = raw[start * channels:end * channels].reshape(-1, channels)
                if channels == 2:
                    stereo[start:end] = frames
                else:
                    stereo[start:end, 0] = frames[:, 0]
                    stereo[start:end, 1] = frames[:, 0]
        except BaseException:
            stereo.flush()
            stereo._mmap.close()
            if "frames" in locals():
                del frames
            del raw
            raise
    elif channels == 2:
        if len(raw) % 2 != 0:
            raw = raw[:-1]
        stereo = raw.reshape(-1, 2).astype(float_dtype)
    else:
        stereo = np.repeat(raw[:, None], 2, axis=1).astype(float_dtype)

    try:
        total_frames = len(stereo)

        # 内容对齐：预计算单声道样本（复用已加载的 stereo 数组，避免重复加载原音频）
        mono_samples = None
        if content_alignment and all_pos_ms:
            if output_path:
                mono_path = os.path.join(os.path.dirname(accumulator_path), "mono.raw")
                mono_samples = np.memmap(mono_path, dtype=float_dtype, mode="w+", shape=(total_frames,))
                for start in range(0, total_frames, 1 << 18):
                    _check_stop(stop_check)
                    end = min(start + (1 << 18), total_frames)
                    mono_samples[start:end] = stereo[start:end].mean(axis=1)
            else:
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
            _check_stop(stop_check)
            if isinstance(mix_seg, (str, os.PathLike)):
                with open(mix_seg, "rb") as clip_file:
                    mix_seg = AudioSegment.from_file(clip_file)
            if mix_seg.channels == 1:
                mix_mono = mix_seg
            else:
                mix_mono = mix_seg.set_channels(1)

            if mix_seg.frame_rate != frame_rate:
                mix_mono = resample_audio(mix_mono, frame_rate, stop_check, high_quality)

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
            mix_raw = np.frombuffer(mix_mono.raw_data, dtype=mix_dtype).astype(float_dtype)
            # 裁切先于动态增益计算，保证包络时间与实际落点一致。
            if actual_pos_ms < 0:
                onset_trim_frames += round(-actual_pos_ms * frame_rate / 1000)
                actual_pos_ms = 0
            if onset_trim_frames:
                mix_raw = mix_raw[onset_trim_frames:]
            if not len(mix_raw):
                continue
            if mix_mono.sample_width != sample_width:
                scale = 2 ** (sample_width * 8 - 1) / 2 ** (mix_mono.sample_width * 8 - 1)
                mix_raw *= scale

            if original_rms_db is not None:
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

            mix_frames = len(mix_raw)

            start_frame = round(actual_pos_ms * frame_rate / 1000)
            end_frame = min(start_frame + mix_frames, total_frames)
            mix_len = end_frame - start_frame
            if mix_len <= 0:
                logger.debug(f"[混音] 片段{i}: start={start_frame}, end={end_frame}, mix_len={mix_len} (跳过)")
                continue

            mix_chunk = mix_raw[:mix_len]

            # 片段边界淡入淡出：消除叠加到原音频时的波形阶跃（爆音/咔哒声）。
            # 起始对齐可能从非零采样点裁入、片段结尾也可能是非零采样，
            # 硬切会在边界形成阶跃，听感即为"突然出现的短促杂音"。
            # 5ms 淡入 + 15ms 淡出，短到几乎无听感损失，足以抹平阶跃。
            n_fade_in = min(int(0.005 * frame_rate), max(1, mix_len // 3))
            n_fade_out = min(int(0.015 * frame_rate), max(1, mix_len // 3))
            if n_fade_in + n_fade_out <= mix_len:
                mix_chunk[:n_fade_in] *= np.linspace(0.0, 1.0, n_fade_in)
                mix_chunk[-n_fade_out:] *= np.linspace(1.0, 0.0, n_fade_out)

            angle_rad = angle * math.pi / 180.0
            left_gain = math.cos(angle_rad / 2.0)
            right_gain = math.sin(angle_rad / 2.0)

            stereo[start_frame:end_frame, 0] += mix_chunk * left_gain
            stereo[start_frame:end_frame, 1] += mix_chunk * right_gain

            if i < 3:
                logger.debug(f"[混音] 片段{i}: pos={pos_ms}ms, frames={mix_frames}, angle={angle}°, "
                      f"L_gain={left_gain:.3f}, R_gain={right_gain:.3f}, "
                      f"chunk_max={np.max(np.abs(mix_chunk)):.1f}")

            if item_callback is not None:
                item_callback(i + 1, len(mix_items))

        stereo_int = _finish_mix(stereo, sample_width, peak_mode, stop_check, output_path)
        if output_path:
            return output_path

        # (N,2) 的 C 连续数组按行主序 reshape(-1) 天然就是 L/R 交织，
        # 无需再分配 interleaved 中间数组（省一份全量内存）
        return original.__class__(
            stereo_int.reshape(-1).tobytes(),
            sample_width=sample_width,
            frame_rate=frame_rate,
            channels=2,
        )
    finally:
        # Windows 无法删除仍被映射的临时文件；取消和异常路径也必须显式关闭。
        if "frames" in locals():
            del frames
        del raw
        mono_map = locals().get("mono_samples")
        if isinstance(mono_map, np.memmap):
            mono_map.flush()
            mono_map._mmap.close()
        if isinstance(stereo, np.memmap):
            stereo.flush()
            stereo._mmap.close()
