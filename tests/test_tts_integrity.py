import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from PyQt6.QtCore import Qt

from src.steps import tts_cache, tts_utils
from src.steps.tts_profile import profile_key, profile_matches, save_profile
from src.steps.tts_worker import TTSWorker
from src.task_manager import create_task, TaskQueue, STEP_PENDING, STEP_SKIPPED
from src import config as config_module
from src import task_manager
from src.workflow_gui.config_mixin import ConfigDialogMixin


class FakeCache:
    def get_cache_stats(self):
        return {'total_count': 0, 'total_size': 0, 'total_reuse': 0}
    def close(self):
        pass


class TTSProfileTests(unittest.TestCase):
    def test_failed_edge_segment_makes_job_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            subtitle = Path(folder)/'source.lrc'
            subtitle.write_text('[00:00.00]one', encoding='utf-8')
            config = {'tts_mode':'edge', 'lrc_files':[str(subtitle)], 'output_dir':str(Path(folder)/'out'),
                      'subtitle_md5':'abc', 'prevent_sleep':False, 'edge_threads':1}
            with patch('src.steps.tts_worker.AudioCache', return_value=FakeCache()):
                worker = TTSWorker(config)
            statuses=[]
            worker.finished_signal.connect(statuses.append, Qt.ConnectionType.DirectConnection)
            with ThreadPoolExecutor(max_workers=1) as executor:
                with patch('src.steps.tts_worker.get_edge_executor', return_value=executor), \
                     patch('src.steps.tts_worker.edge_tts_task', return_value=(False,'failed')):
                    worker.run()
            self.assertEqual(statuses,[False])
            self.assertFalse(profile_matches(str(Path(config['output_dir'])/'abc'), config))

    def test_changing_tts_profile_invalidates_active_task_and_mix(self):
        with tempfile.TemporaryDirectory() as folder:
            old={'tts_mode':'edge','edge_voice':'v1'}
            new=dict(old, edge_voice='v2')
            directory=Path(folder)/'tts'
            directory.mkdir()
            save_profile(str(directory), old)
            task=create_task(folder, str(Path(folder)/'source.wav'))
            task.step2_status='done'
            task.step2_output=str(directory)
            task.step3_status='done'
            task.step3_output=str(Path(folder)/'mixed.mp3')
            updates=[]
            dummy=SimpleNamespace(
                task_queue=SimpleNamespace(tasks=[task], update_task=updates.append),
                config=SimpleNamespace(tts_cfg=new), _append_log=lambda msg:None,
            )
            ConfigDialogMixin._on_tts_config_changed(dummy)
            self.assertEqual((task.step2_status,task.step3_status),(STEP_PENDING,STEP_PENDING))
            self.assertTrue(task.force_remix)
            self.assertEqual(updates,[task])

    def test_state_files_are_replaced_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            task = create_task(folder, str(Path(folder)/'source.wav'))
            task.save()
            original = Path(task.task_json_path).read_bytes()
            task.step1_error = 'new value'
            with patch.object(task_manager.os, 'replace', side_effect=OSError('disk error')):
                with self.assertLogs('src.task_manager', level='WARNING'):
                    task.save()
            self.assertEqual(Path(task.task_json_path).read_bytes(), original)
            self.assertEqual(list(Path(task.task_dir).glob('.task-*')), [])

            path = Path(folder)/'workflow.json'
            with patch.object(config_module, 'get_config_path', return_value=str(path)):
                cfg = config_module.WorkflowConfig()
                cfg.save()
                original = path.read_bytes()
                cfg.config['workspace_dir'] = 'changed'
                with patch.object(config_module.os, 'replace', side_effect=OSError('disk error')):
                    with self.assertRaises(OSError):
                        cfg.save()
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(Path(folder).glob('.config-*')), [])

    def test_folder_scan_excludes_previous_mix_and_detects_aac_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'source.mp3'
            source.write_bytes(b'source')
            mixed = Path(folder)/'双语'
            mixed.mkdir()
            (mixed/'source_mixed.aac').write_bytes(b'mixed')
            self.assertEqual(TaskQueue.scan_folder_media(folder), [str(source)])
            task = create_task(folder, str(source))
            self.assertEqual(task._detect_mix_output(), str(mixed/'source_mixed.aac'))

    def test_profile_changes_for_audio_settings_only(self):
        base = {'tts_mode':'edge', 'edge_voice':'voice', 'edge_rate':'+0%', 'edge_volume':'+0%'}
        key = profile_key(base)
        for field, value in [('edge_voice','new'), ('edge_rate','+10%'), ('edge_volume','-20%')]:
            changed = dict(base, **{field:value})
            self.assertNotEqual(profile_key(changed), key)
        self.assertEqual(profile_key(dict(base, edge_threads=20)), key)
        api = {'tts_mode':'api', 'api_configs':[{'url':'http://a','model':'m'}]}
        self.assertNotEqual(profile_key(api), profile_key({'tts_mode':'api','api_configs':[{'url':'http://b','model':'m'}]}))

    def test_task_import_requires_matching_profile_and_remix(self):
        cfg = {'tts_mode':'edge', 'edge_voice':'v1'}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'source.mp3'
            source.write_bytes(b'audio')
            subtitle = Path(folder)/'source.lrc'
            subtitle.write_text('[00:00.00]hi', encoding='utf-8')
            workspace = Path(folder)/'workspace'
            output = workspace / hashlib.md5(subtitle.read_bytes()).hexdigest()[:8]
            output.mkdir(parents=True)
            (output/'0001_00-00-00.00_hi.wav').write_bytes(b'audio')
            mixed = Path(folder)/'双语'
            mixed.mkdir()
            (mixed/'source_mixed.mp3').write_bytes(b'old mix')
            save_profile(output, cfg)
            same = create_task(str(workspace), str(source), str(subtitle), tts_config=cfg)
            self.assertEqual(same.step2_status, STEP_SKIPPED)
            self.assertEqual(same.step3_status, STEP_SKIPPED)
            changed = create_task(str(workspace), str(source), str(subtitle),
                                  tts_config=dict(cfg, edge_rate='+20%'))
            self.assertEqual(changed.step2_status, STEP_PENDING)
            self.assertEqual(changed.step3_status, STEP_PENDING)
            self.assertTrue(changed.force_remix)
            self.assertNotEqual(same.task_id, changed.task_id)

    def test_failed_and_stopped_segments_make_job_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            subtitle = Path(folder)/'source.lrc'
            subtitle.write_text('[00:00.00]one\n[00:01.00]two', encoding='utf-8')
            output = Path(folder)/'out'
            config = {'tts_mode':'api', 'lrc_files':[str(subtitle)], 'output_dir':str(output),
                      'subtitle_md5':'abc', 'prevent_sleep':False,
                      'api_configs':[{'name':'local','url':'http://local','model':'m'}],
                      'use_bulk_api':False}
            with patch('src.steps.tts_worker.AudioCache', return_value=FakeCache()):
                worker = TTSWorker(config)
            statuses=[]
            worker.finished_signal.connect(statuses.append, Qt.ConnectionType.DirectConnection)
            with patch('src.steps.tts_worker.tts_task', return_value=(False,'failed')):
                worker.run()
            self.assertEqual(statuses, [False])
            self.assertFalse(profile_matches(str(output/'abc'), config))

            statuses.clear()
            calls=[]
            def stop_after_first(*args, **kwargs):
                calls.append(1)
                worker.stop_flag = True
                return True, 'ok'
            with patch('src.steps.tts_worker.tts_task', side_effect=stop_after_first):
                thread = threading.Thread(target=worker.run)
                thread.start()
                thread.join(timeout=4)
            self.assertFalse(thread.is_alive(), 'API stop left unfinished queue.join')
            self.assertEqual(statuses, [False])

    def test_api_download_failure_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'voice.wav'
            path.write_bytes(b'previous')
            response = SimpleNamespace(content=b'', raise_for_status=lambda:None)
            with patch('src.steps.tts_utils._get_session', return_value=SimpleNamespace(get=lambda *a,**k:response)):
                with self.assertRaises(ValueError):
                    tts_utils._download_audio('http://local/voice', str(path))
            self.assertEqual(path.read_bytes(), b'previous')
            self.assertEqual(list(Path(folder).glob('.tts-*')), [])

    def test_cache_copy_failure_is_not_indexed_and_parallel_writes_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(tts_cache, 'CACHE_DIR', folder):
                cache = tts_cache.AudioCache(str(Path(folder)/'cache.db'))
                self.assertFalse(cache.save_audio_cache('text', str(Path(folder)/'missing.wav')))
                self.assertEqual(cache.get_cache_stats()['total_count'], 0)
                sources=[]
                for i in range(12):
                    path=Path(folder)/f'{i}.wav'
                    path.write_bytes(bytes([i])*100000)
                    sources.append(path)
                with ThreadPoolExecutor(max_workers=6) as executor:
                    results=list(executor.map(lambda p: cache.save_audio_cache('text', str(p)), sources))
                self.assertTrue(all(results))
                stored=Path(cache.get_cached_audio('text'))
                self.assertEqual(len(stored.read_bytes()), 100000)
                self.assertEqual(cache.get_cache_stats()['total_count'], 1)
                self.assertEqual(list(Path(folder).glob('.cache-*')), [])
                cache.close()


if __name__ == '__main__':
    unittest.main()
