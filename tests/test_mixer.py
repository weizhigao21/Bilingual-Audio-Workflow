import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from pydub import AudioSegment

from src.steps.audio_utils.mixing import mix_with_numpy, _finish_mix
from src.steps.audio_utils.rms import compute_rms_db, compute_rms_envelope
from src.steps.audio_utils.detection import detect_voice_onset
from src.steps.audio_utils.common import _submit_pan_detection
from src.steps.audio_utils.ffmpeg_utils import resample_audio, _run_ffmpeg, export_audio_ffmpeg
from src.steps.step3_mixer import mix_single_task, _export_wav_24bit
from src.steps.audio_utils import clear_mix_cache
from src.task_manager import TaskInfo


def audio(samples, sr=48000, width=2):
    samples = np.asarray(samples)
    channels = samples.shape[1] if samples.ndim == 2 else 1
    return AudioSegment(samples.astype({1: np.int8, 2: np.int16, 4: np.int32}[width]).tobytes(),
                        sample_width=width, frame_rate=sr, channels=channels)


def tone(seconds=1, sr=48000, frequency=1000, amplitude=10000):
    return amplitude * np.sin(2 * np.pi * frequency * np.arange(round(seconds * sr)) / sr)


class MixingTests(unittest.TestCase):
    def test_24bit_pcm_values_and_classic_header(self):
        import wave
        values = np.array([-(2**31), -257, -1, 0, 1, 257, 2**31-1], dtype=np.int32)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'out.wav')
            _export_wav_24bit(audio(values, width=4), path)
            with wave.open(path, 'rb') as wav:
                raw = wav.readframes(len(values))
                self.assertEqual(wav.getsampwidth(), 3)
            decoded = [int.from_bytes(raw[i:i+3], 'little', signed=True) for i in range(0, len(raw), 3)]
            self.assertEqual(decoded, (values >> 8).tolist())

    def test_shared_detection_pool_can_resize_during_parallel_submission(self):
        from concurrent.futures import ThreadPoolExecutor
        def batch(workers):
            futures = _submit_pan_detection(lambda x: x * x, range(12), workers)
            return [future.result() for future in futures]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(batch, [2, 4, 3, 4] * 10))
        for result in results:
            self.assertEqual(result, [i*i for i in range(12)])

    def test_soft_mode_matches_legacy_curve_within_one_lsb(self):
        x = np.random.default_rng(0).uniform(-65000, 65000, (1000, 2))
        norm = x / 32767
        mag = np.abs(norm)
        expected = np.sign(norm) * np.where(mag > .9, .9 + .1*np.tanh(np.maximum(mag-.9, 0)/.1), mag) * 32767
        np.testing.assert_allclose(_finish_mix(x, 2, 'soft'), expected, atol=.501)

    def test_trimmed_clip_uses_actual_timeline_for_dynamic_gain(self):
        original = audio(np.zeros((48000, 2)))
        voice = audio(tone(seconds=.1))
        with patch('src.steps.audio_utils.mixing.interpolate_envelope', return_value=np.full(2400, -20.0)) as envelope:
            mix_with_numpy(original, [(voice, -50, 0)], auto_volume='auto')
        self.assertEqual(envelope.call_args.args[3:], (0, 2400))

    def test_rms_phase_invariant_and_matches_reference(self):
        x = tone()
        env, _ = compute_rms_envelope(audio(np.column_stack([x, -x])))
        ref, _ = compute_rms_envelope(audio(x))
        np.testing.assert_allclose(env, ref, atol=1e-8)
        self.assertAlmostEqual(float(env.mean()), compute_rms_db(audio(x)), places=5)

    def test_envelope_arbitrary_channels_and_tail(self):
        rng = np.random.default_rng(4)
        x = rng.integers(-10000, 10000, size=(9001, 3), dtype=np.int16)
        env, _ = compute_rms_envelope(audio(x, sr=22050), window_ms=100, hop_ms=50)
        ref = [20 * np.log10(np.sqrt(np.mean(x[i:i+2205].astype(float)**2))/32768)
               for i in range(0, len(x)-2205+1, 1102)]
        np.testing.assert_allclose(env, ref, atol=1e-10)

    def test_empty_and_silent_audio(self):
        self.assertEqual(compute_rms_db(audio([])), -100)
        self.assertEqual(compute_rms_envelope(audio([]))[0][0], -100)
        self.assertEqual(len(mix_with_numpy(audio([]), [])), 0)

    def test_32bit_positive_full_scale_never_wraps(self):
        for mode in ('peak', 'soft'):
            x = np.array([[2**31-1, -(2**31)], [4e9, -4e9]])
            result = _finish_mix(x, 4, mode)
            self.assertTrue(np.all(result[:, 0] > 0))
            self.assertTrue(np.all(result[:, 1] < 0))

    def test_peak_preserves_stereo_ratio_and_waveform(self):
        x = np.column_stack([tone(amplitude=60000), tone(amplitude=30000)])
        out = _finish_mix(x, 2, 'peak').astype(float)
        gain = 32767 * 10**(-1/20) / np.max(np.abs(x))
        self.assertLessEqual(np.max(np.abs(out)), 32767*10**(-1/20)+0.5)
        np.testing.assert_allclose(out, x*gain, atol=0.501)

    def test_quiet_pcm_is_preserved(self):
        x = np.array([[1, -1], [7654321, -6543211]], dtype=np.int32)
        out = mix_with_numpy(audio(x, width=4), [])
        np.testing.assert_array_equal(np.frombuffer(out.raw_data, dtype=np.int32).reshape(-1, 2), x)

    def test_stereo_voice_right_channel_not_discarded(self):
        voice = audio(np.column_stack([np.zeros(48000), tone()]))
        out = mix_with_numpy(audio(np.zeros((48000, 2))), [(voice, 0, 0)])
        x = np.frombuffer(out.raw_data, dtype=np.int16).reshape(-1, 2)
        self.assertGreater(np.max(x[:, 0]), 4000)
        self.assertEqual(np.max(np.abs(x[:, 1])), 0)

    def test_negative_timestamp_and_fully_trimmed_clip(self):
        original = audio(np.zeros((48000, 2)))
        voice = audio(tone(seconds=.1))
        self.assertEqual(mix_with_numpy(original, [(voice, -200, 0)]).raw_data, original.raw_data)
        out = mix_with_numpy(original, [(voice, -50, 0)])
        self.assertEqual(len(out), 1000)
        self.assertGreater(np.max(np.frombuffer(out.raw_data, dtype=np.int16)), 0)

    def test_onset_phase_invariant(self):
        x = np.concatenate([np.zeros(24000), tone(seconds=.5)])
        self.assertEqual(detect_voice_onset(audio(x)), detect_voice_onset(audio(np.column_stack([x, -x]))))
        self.assertGreater(detect_voice_onset(audio(x)), 450)

    def test_cancel_and_errors_are_not_silently_fallback(self):
        with self.assertRaisesRegex(RuntimeError, '停止'):
            mix_with_numpy(audio(tone()), [], stop_check=lambda: True)
        with self.assertRaises(ValueError):
            mix_with_numpy(audio(tone()), [], peak_mode='invalid')
        with self.assertRaises(ValueError):
            mix_with_numpy(audio(np.zeros((48000, 6))), [])


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg required')
class ExportTests(unittest.TestCase):
    def test_changed_voice_forces_remix_even_if_output_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / 'source.wav')
            audio(tone()).export(source, format='wav').close()
            clips = Path(folder) / 'clips'
            clips.mkdir()
            audio(tone(seconds=.2)).export(str(clips / '0001_00-00-00.00_test.wav'), format='wav').close()
            output = Path(folder) / 'source_mixed.mp3'
            output.write_bytes(b'old mix')
            cfg = SimpleNamespace(mixer_cfg={'output_folder': folder, 'output_format': 'mp3',
                                            'skip_existing': True, 'channel_detect': False,
                                            'auto_volume': 'off'})
            task = TaskInfo('test', source, 'source', folder, step2_output=str(clips),
                            force_remix=True)
            ok, path = mix_single_task(task, cfg)
            self.assertTrue(ok, path)
            self.assertNotEqual(output.read_bytes(), b'old mix')
            self.assertFalse(task.force_remix)
            clear_mix_cache()

    def test_same_rate_does_not_launch_ffmpeg(self):
        source = audio(tone(seconds=.1))
        with patch('src.steps.audio_utils.ffmpeg_utils._run_ffmpeg') as run:
            self.assertIs(resample_audio(source, 48000), source)
        run.assert_not_called()

    def test_aac_and_m4a_honor_requested_bitrate(self):
        for fmt in ('aac', 'm4a'):
            with patch('src.steps.audio_utils.ffmpeg_utils._run_ffmpeg') as run:
                export_audio_ffmpeg(audio(tone(seconds=.1)), 'unused.' + fmt, fmt, bitrate='320k')
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[cmd.index('-b:a') + 1], '320k')

    def test_quality_resampler_rejects_alias(self):
        source = audio(tone(seconds=.3, frequency=18000))
        high = resample_audio(source, 24000)
        fast = resample_audio(source, 24000, high_quality=False)
        self.assertAlmostEqual(len(high), 300, delta=1)
        self.assertLess(compute_rms_db(high[20:-20]), compute_rms_db(fast[20:-20]) - 40)

    def test_pipe_all_widths_and_formats(self):
        with tempfile.TemporaryDirectory() as folder:
            for width in (1, 2, 4):
                for fmt in ('mp3', 'm4a', 'aac', 'mp4'):
                    path = str(Path(folder) / f'{width}.{fmt}')
                    source = audio(tone(seconds=.3), width=2).set_sample_width(width)
                    export_audio_ffmpeg(source, path, fmt, bitrate='128k', sample_rate=44100, channels=1)
                    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', path]))
                    stream = info['streams'][0]
                    self.assertEqual(int(stream['sample_rate']), 44100)
                    self.assertEqual(stream['channels'], 1)
                    self.assertIn(stream['codec_name'], ('mp3', 'aac'))

    def test_stderr_drain_and_cancel_blocked_stdin(self):
        result = _run_ffmpeg([sys.executable, '-c', "import sys; sys.stderr.write('x'*1000000); sys.stdout.write('ok')"])
        self.assertEqual(result, b'ok')
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, '停止'):
            _run_ffmpeg([sys.executable, '-c', 'import time; time.sleep(30)'],
                        input_data=b'x' * 1000000, stop_check=lambda: time.monotonic()-start > .2)
        self.assertLess(time.monotonic()-start, 5)

    def test_export_failure_preserves_existing_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / 'source.wav')
            audio(tone()).export(source, format='wav').close()
            clips = Path(folder) / 'clips'
            clips.mkdir()
            audio(tone(seconds=.2)).export(str(clips / '0001_00-00-00.00_test.wav'), format='wav').close()
            output = Path(folder) / 'source_mixed.mp3'
            output.write_bytes(b'previous result')
            cfg = SimpleNamespace(mixer_cfg={'output_folder': folder, 'output_format': 'mp3', 'skip_existing': False,
                                            'channel_detect': False, 'auto_volume': 'off'})
            task = TaskInfo('test', source, 'source', folder, step2_output=str(clips))
            with patch('src.steps.step3_mixer.export_audio_ffmpeg', side_effect=RuntimeError('encoder failed')):
                ok, msg = mix_single_task(task, cfg)
            self.assertFalse(ok)
            self.assertIn('encoder failed', msg)
            self.assertEqual(output.read_bytes(), b'previous result')
            self.assertEqual(list(Path(folder).glob('.mix-*')), [])
            clear_mix_cache()

    def test_video_to_audio_and_video_and_wav_depths(self):
        import wave
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / 'source.mp4')
            _run_ffmpeg(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=32x32:rate=10:duration=1',
                         '-f', 'lavfi', '-i', 'sine=frequency=400:sample_rate=48000:duration=1',
                         '-c:v', 'libx264', '-c:a', 'aac', '-shortest', source])
            clips = Path(folder) / 'clips'
            clips.mkdir()
            audio(tone(seconds=.2, sr=24000), sr=24000).export(str(clips / '0001_00-00-00.00_test.wav'), format='wav').close()
            cfg = SimpleNamespace(mixer_cfg={'output_folder': folder, 'output_format': 'mp4', 'skip_existing': False,
                                            'channel_detect': False, 'audio_sample_rate': 48000})
            task = TaskInfo('test', source, 'source', folder, step2_output=str(clips))
            for fmt, depth in [('mp4', 16), ('mp3', 16), ('wav', 16), ('wav', 24), ('wav', 32)]:
                cfg.mixer_cfg.update(output_format=fmt, wav_bit_depth=depth)
                ok, path = mix_single_task(task, cfg)
                self.assertTrue(ok, path)
                if fmt == 'wav':
                    with wave.open(path, 'rb') as wav:
                        self.assertEqual(wav.getsampwidth(), depth//8)
                        self.assertEqual(wav.getframerate(), 48000)
                else:
                    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', path]))
                    self.assertEqual(len(info['streams']), 2 if fmt == 'mp4' else 1)
            clear_mix_cache()


if __name__ == '__main__':
    unittest.main()
