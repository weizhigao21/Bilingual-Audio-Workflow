# -*- coding: utf-8 -*-
"""混音核心：pydub 叠加回退与 numpy 向量化混音。"""
import math
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from pydub import AudioSegment

from .common import HAS_NUMPY, np, logger
from .decode import load_audio_segment
from .detection import find_nearest_onset, detect_voice_onset
from .rms import compute_rms_db, compute_rms_envelope, interpolate_envelope
from .ffmpeg_utils import resample_audio
from .. import perf


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


class _SkipItem(Exception):
    """解码失败/空片段：按旧语义只跳过该片段，不让整段混音失败。"""


def _prepare_mix_item(mix_seg, frame_rate, high_quality, stop_check,
                      align_onset, tag):
    """单个片段的纯函数准备：解码 → 单声道 → 重采样 →（可选）起始检测。

    供线程池并发执行；逐片段独立调用、参数与旧串行路径完全一致，
    因此结果逐位相同。解码失败/空片段抛 _SkipItem（消费方跳过并记日志）；
    其余异常向上传播（保持旧语义：重采样/起始检测失败 = 整段混音失败）。
    """
    if isinstance(mix_seg, (str, os.PathLike)):
        try:
            with perf.span("片段解码"):
                mix_seg = load_audio_segment(mix_seg)
        except Exception as exc:
            raise _SkipItem(f"片段读取失败，已跳过: {tag} - {exc}")
        if len(mix_seg) == 0:
            raise _SkipItem(f"片段为空，已跳过: {tag}")
    original_seg = mix_seg
    if original_seg.channels == 1:
        mix_mono = original_seg
    else:
        mix_mono = original_seg.set_channels(1)

    if original_seg.frame_rate != frame_rate:
        mix_mono = resample_audio(mix_mono, frame_rate, stop_check, high_quality)

    # 起始检测基于解码后的原片段（与旧路径一致，非单声道/重采样结果）
    onset_ms = detect_voice_onset(original_seg) if align_onset else 0
    return mix_mono, onset_ms


def _prepare_mix_items(mix_items, frame_rate, high_quality, stop_check,
                       align_onset, max_workers):
    """并发准备全部片段，**严格按原顺序**产出 (i, prepared)。

    prepared 是 (mix_mono, onset_ms) 或 _SkipItem 实例（由消费方按旧语义
    跳过）。有界前瞻窗口（2×线程数）限制同时驻留内存的已备片段数，
    长音频走磁盘缓冲时不会把全部片段囤在内存。消费方遍历顺序、
    进度回调与叠加顺序与旧串行路径完全一致。
    """
    total = len(mix_items)
    workers = max(1, int(max_workers or 1))
    window = workers * 2
    wrapped = perf.wrap(_prepare_mix_item)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mix-prep")
    fut_map = {}
    results = {}
    next_yield = 0
    submit_from = 0

    def _submit(idx):
        mix_seg = mix_items[idx][0]
        tag = mix_seg if isinstance(mix_seg, (str, os.PathLike)) else f"片段#{idx}"
        fut = pool.submit(wrapped, mix_seg, frame_rate, high_quality,
                          stop_check, align_onset, tag)
        fut_map[fut] = idx

    try:
        while submit_from < min(total, window):
            _submit(submit_from)
            submit_from += 1
        while next_yield < total:
            _check_stop(stop_check)
            if not fut_map:
                raise RuntimeError("混音准备管线停滞（不应发生）")
            done, _ = wait(fut_map, return_when=FIRST_COMPLETED, timeout=0.1)
            for fut in done:
                idx = fut_map.pop(fut)
                try:
                    results[idx] = fut.result()
                except _SkipItem as exc:
                    results[idx] = exc
                if submit_from < total:
                    _submit(submit_from)
                    submit_from += 1
            while next_yield in results:
                yield next_yield, results.pop(next_yield)
                next_yield += 1
    finally:
        for fut in list(fut_map):
            fut.cancel()
            if fut.done() and not fut.cancelled():
                try:
                    fut.result()
                except Exception:
                    pass  # 迟到异常只吞掉告警，语义已由首异常表达
        pool.shutdown(wait=False, cancel_futures=True)


def mix_with_numpy(original, mix_items, volume_db=0, auto_volume="off",
                   align_onset=False, content_alignment=False,
                   peak_mode="peak", high_quality=True, stop_check=None,
                   output_path=None, accumulator_path=None, item_callback=None,
                   max_workers=4):
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

        # 片段准备（解码+单声道+重采样）并发执行、消费严格按原序：
        # 输出与旧串行路径逐位一致，进度回调与跳过语义不变。
        prepared_items = _prepare_mix_items(
            mix_items, frame_rate, high_quality, stop_check,
            align_onset, max_workers)
        for i, prepared in prepared_items:
            _check_stop(stop_check)
            mix_seg, pos_ms, angle = mix_items[i]
            if isinstance(prepared, _SkipItem):
                logger.warning(f"[混音] {prepared}")
                if item_callback is not None:
                    item_callback(i + 1, len(mix_items))
                continue
            mix_mono, onset_ms = prepared

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
                # onset_ms 由准备阶段并发算好（对解码原片段检测，与旧路径一致）
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
