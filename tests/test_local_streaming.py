"""Whole-job intake must deliver files, logs and progress before final completion."""
import io
from pathlib import Path
import time
import wave
from unittest.mock import patch

from PyQt6.QtCore import Qt
from src.steps.tts_worker import TTSWorker
from src.steps import tts_utils


def wav_bytes():
    out=io.BytesIO()
    with wave.open(out,'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\x01\x00'*160)
    return out.getvalue()


class Cache:
    def __init__(self,cached=None): self.cached,self.saved=cached,[]
    def get_cached_audio(self,*args): return self.cached
    def save_audio_cache(self,*args,**kwargs): self.saved.append(args)
    def get_cache_stats(self): return dict(total_count=len(self.saved),total_reuse=0,total_size=0)


class Response:
    def __init__(self,body=None,status=200,content=b''):
        self.body,self.status_code,self.content=body,status,content
    def json(self): return self.body
    def raise_for_status(self): assert self.status_code in (200,202)
    # _download_audio 用 requests 的流式接口：with session.get(..., stream=True)
    def __enter__(self): return self
    def __exit__(self,*exc): return False
    def iter_content(self,chunk_size=65536):
        if self.content: yield self.content


class LocalService:
    def __init__(self,progress,folder):
        self.progress,self.folder=progress,folder
        self.submissions,self.posts,self.polls=[],[],0
    def get(self,url,**kwargs):
        if url.endswith('/health'):
            return Response(dict(service='local-voice-workbench',workflow_incremental=True,
                capabilities=['async_jobs','workflow_record_only','workflow_grouping']))
        if '/workflow/audio/' in url: return Response(content=wav_bytes())
        assert url.endswith('/progress')
        self.polls+=1
        if self.polls==1:
            return Response(dict(status='running',segments=[dict(idx=1,status='done',audio_url='/workflow/audio/1.wav')]))
        assert self.progress==[1], 'UI progress waited for the whole job'
        assert list(self.folder.glob('*.wav')), 'First output was not downloaded during generation'
        return Response(dict(status='done',segments=[dict(idx=i,status='done',audio_url=f'/workflow/audio/{i}.wav')
            for i in range(1,len(self.submissions[0]['segments'])+1)]))
    def post(self,url,**kwargs):
        self.posts.append(url)
        if url.endswith('/jobs'):
            self.submissions.append(kwargs['json'])
            return Response(dict(job_id='one-task',generation=1,
                segment_indices=list(range(1,len(kwargs['json']['segments'])+1))),status=202)
        return Response(dict(errors=[]))


def worker_for(tmp_path,api,cache):
    with patch('src.steps.tts_worker.AudioCache',return_value=cache):
        worker=TTSWorker(dict(use_bulk_api=True,bulk_batch_size=20,api_configs=[api],current_api_index=0))
    worker._read_failures=0
    progress,logs,finished=[],[],[]
    worker.progress_signal.connect(progress.append,Qt.ConnectionType.DirectConnection)
    worker.log_signal.connect(logs.append,Qt.ConnectionType.DirectConnection)
    worker.finished_signal.connect(finished.append,Qt.ConnectionType.DirectConnection)
    return worker,progress,logs,finished


def test_55_rows_submit_once_and_progress_arrives_during_generation(tmp_path):
    api=dict(name='声笺',url='http://127.0.0.1:8765',model='voice')
    cache=Cache()
    worker,progress,logs,finished=worker_for(tmp_path,api,cache)
    service=LocalService(progress,tmp_path)
    tasks=[(i,f'00:{i:02}','text-'+str(i),str(tmp_path),7) for i in range(1,56)]
    with patch.object(tts_utils,'_get_session',return_value=service):
        worker._run_api_tts(tasks,0,0,time.time(),'run',str(tmp_path))
    assert len(service.submissions)==1 and len(service.submissions[0]['segments'])==55
    assert progress==list(range(1,56)) and finished==[True]
    assert len(cache.saved)==55 and len(list(tmp_path.glob('*.wav')))==55
    assert len([line for line in logs if '完成：' in line])==55


def test_stop_keeps_downloaded_output_and_does_not_report_100_percent(tmp_path):
    api=dict(name='声笺',url='http://127.0.0.1:8765',model='voice')
    worker,progress,logs,finished=worker_for(tmp_path,api,Cache())
    worker.progress_signal.connect(lambda _:worker.stop(),Qt.ConnectionType.DirectConnection)
    service=LocalService(progress,tmp_path)
    with patch.object(tts_utils,'_get_session',return_value=service):
        worker._run_api_tts([(i,'','text-'+str(i),str(tmp_path),0) for i in range(1,6)],0,0,time.time(),'run',str(tmp_path))
    assert progress==[1] and finished==[False] and service.posts[-1].endswith('/cancel')
    assert len(list(tmp_path.glob('*.wav')))==1


def test_all_cached_results_do_not_submit_a_job(tmp_path):
    cached=tmp_path/'cached.data'
    cached.write_bytes(wav_bytes())
    api=dict(name='声笺',url='http://127.0.0.1:8765',model='voice')
    worker,progress,logs,finished=worker_for(tmp_path,api,Cache(str(cached)))
    service=LocalService(progress,tmp_path)
    with patch.object(tts_utils,'_get_session',return_value=service):
        worker._run_api_tts([(i,'','text-'+str(i),str(tmp_path),0) for i in range(1,4)],0,0,time.time(),'run',str(tmp_path))
    assert not service.submissions and progress==[1,2,3] and finished==[True]


def test_other_providers_keep_their_micro_batch_contract(tmp_path):
    api=dict(name='other',url='http://remote-provider',model='voice')
    worker,progress,logs,finished=worker_for(tmp_path,api,Cache())
    with patch('src.steps.tts_worker.tts_bulk_task',side_effect=lambda tasks,*a,**kw:[(True,'done') for _ in tasks]) as bulk:
        worker._run_api_tts([(i,'','text',str(tmp_path),0) for i in range(55)],0,0,time.time(),'run',str(tmp_path))
    assert [len(c.args[0]) for c in bulk.call_args_list]==[20,20,15]
    assert finished==[True]
