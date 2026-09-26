import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from pydub import AudioSegment

from src.steps.step3_mixer import mix_single_task
from src.task_manager import TaskInfo
from src.steps.audio_utils import clear_mix_cache


def make_wav(path, seconds, rate, amplitude, channels=2):
    t=np.arange(round(seconds*rate))/rate
    wave=(amplitude*np.sin(2*np.pi*440*t)).astype(np.int16)
    if channels==2:
        wave=np.column_stack([wave,wave])
    AudioSegment(wave.tobytes(),sample_width=2,frame_rate=rate,channels=channels).export(str(path),format='wav').close()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'source.wav'
        make_wav(self.source,3,24000,5000)
        self.clips=self.root/'clips'
        self.clips.mkdir()
        make_wav(self.clips/'0001_00-00-00.40_a.wav',.7,16000,3000,1)
        make_wav(self.clips/'0002_00-00-01.60_b.wav',.8,24000,4000,1)
        self.task=TaskInfo('test',str(self.source),'source',str(self.root),step2_output=str(self.clips))
        self.cfg=SimpleNamespace(mixer_cfg={
            'output_folder':str(self.root),'output_format':'wav','wav_bit_depth':16,
            'audio_sample_rate':24000,'audio_channels':2,'skip_existing':False,
            'channel_detect':False,'align_onset':False,'content_alignment':False,
            'auto_volume':'off','peak_mode':'peak','high_quality_resample':True,
            'streaming_threshold_minutes':1000,
        })
        self.addCleanup(clear_mix_cache)

    def _run(self, streaming):
        self.cfg.mixer_cfg['streaming_threshold_minutes']=0 if streaming else 1000
        ok, path=mix_single_task(self.task,self.cfg)
        self.assertTrue(ok,path)
        with open(path,'rb') as f:
            out=AudioSegment.from_file(f)
        return np.frombuffer(out.raw_data,dtype=np.int16).copy()

    def test_matches_existing_mixer_across_volume_modes(self):
        for volume in ('off','fixed','auto'):
            with self.subTest(volume=volume):
                self.cfg.mixer_cfg['auto_volume']=volume
                full=self._run(False)
                stream=self._run(True)
                self.assertEqual(full.shape,stream.shape)
                self.assertLessEqual(np.max(np.abs(full.astype(np.int32)-stream.astype(np.int32))),12)
                self.assertEqual(list(self.root.glob('.mix-work-*')),[])

    def test_audio_export_mp3_and_wav24(self):
        for fmt, depth in [('mp3',16),('wav',24)]:
            with self.subTest(format=fmt):
                self.cfg.mixer_cfg.update(output_format=fmt,wav_bit_depth=depth,
                                          audio_sample_rate=44100,streaming_threshold_minutes=0)
                ok,path=mix_single_task(self.task,self.cfg)
                self.assertTrue(ok,path)
                info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',path]))
                self.assertEqual(int(info['streams'][0]['sample_rate']),44100)
                if fmt=='wav':
                    self.assertEqual(info['streams'][0]['bits_per_sample'],24)
                self.assertEqual(list(self.root.glob('.mix-work-*')),[])

    def test_video_keeps_video_stream(self):
        video=self.root/'source.mp4'
        subprocess.run([
            'ffmpeg','-y','-loglevel','error','-f','lavfi','-i',
            'color=c=black:s=160x90:r=10:d=3','-i',str(self.source),
            '-c:v','mpeg4','-c:a','aac','-shortest',str(video),
        ],check=True)
        self.task.source_path=str(video)
        self.cfg.mixer_cfg.update(output_format='mp4',streaming_threshold_minutes=0)
        ok,path=mix_single_task(self.task,self.cfg)
        self.assertTrue(ok,path)
        source_info=json.loads(subprocess.check_output([
            'ffprobe','-v','error','-show_streams','-of','json',str(video)]))
        output_info=json.loads(subprocess.check_output([
            'ffprobe','-v','error','-show_streams','-of','json',path]))
        self.assertEqual(source_info['streams'][0]['codec_name'],
                         output_info['streams'][0]['codec_name'])
        self.assertEqual(output_info['streams'][1]['codec_name'],'aac')
        self.assertEqual(list(self.root.glob('.mix-*')),[])

    def test_export_error_keeps_existing_output_and_cleans_spool(self):
        target=self.root/'source_mixed.mp3'
        target.write_bytes(b'old output')
        self.cfg.mixer_cfg.update(output_format='mp3',streaming_threshold_minutes=0)
        with patch('src.steps.stream_mixer.export_pcm_file',side_effect=RuntimeError('encoder error')):
            ok,msg=mix_single_task(self.task,self.cfg)
        self.assertFalse(ok)
        self.assertIn('encoder error',msg)
        self.assertEqual(target.read_bytes(),b'old output')
        self.assertEqual(list(self.root.glob('.mix-*')),[])

    def test_stop_during_streamed_mix_cleans_spool(self):
        self.cfg.mixer_cfg['streaming_threshold_minutes']=0
        calls=[0]
        def stop():
            calls[0]+=1
            return calls[0]>5
        ok,msg=mix_single_task(self.task,self.cfg,stop_check=stop)
        self.assertFalse(ok)
        self.assertEqual(list(self.root.glob('.mix-*')),[])


if __name__=='__main__':
    unittest.main()
