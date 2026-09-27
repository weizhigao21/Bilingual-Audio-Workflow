import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.steps.batch_executor.pipeline_mixin import PipelineMixin
from src.task_manager import TaskInfo, STEP_DONE, STEP_PENDING, STEP_SKIPPED


class Signal:
    def __init__(self):
        self.calls=[]
    def emit(self,*args):
        self.calls.append(args)


class FakePipeline(PipelineMixin):
    def __init__(self, task, steps):
        self.tasks=[task]
        self.steps=steps
        self.config=SimpleNamespace(tts_cfg={'pipeline_tts_workers':2}, mixer_cfg={'thread_count':2})
        self._stop_flag=False
        self.ran=[]
        self.whisper_batches=0
        for name in ('log_signal','task_finished','task_started','progress_signal','step_progress_signal'):
            setattr(self,name,Signal())

    def _resolve_source_path(self, task):
        return True

    def _run_single_step_sync(self, task, step, *args, progress_callback=None):
        self.ran.append(step)
        if progress_callback is not None:
            progress_callback(50)
        task.set_step_status(step, STEP_DONE)
        return True, 'ok'

    def _run_whisper_batch(self, total, failed):
        self.whisper_batches += 1
        for task in self.tasks:
            task.set_step_status(1, STEP_DONE)
            self.task_finished.emit(task.task_id, True)


class PipelineTests(unittest.TestCase):
    def test_subtitles_use_one_batch_before_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            task=TaskInfo('id',str(Path(folder)/'src.wav'),'src',folder)
            pipeline=FakePipeline(task,[1])
            self.assertEqual(pipeline._run_pipeline(1),(1,0,0))
            self.assertEqual(pipeline.whisper_batches,1)
            self.assertEqual(pipeline.ran,[])

    def test_only_tts_does_not_start_mixing(self):
        with tempfile.TemporaryDirectory() as folder:
            task=TaskInfo('id',str(Path(folder)/'src.wav'),'src',folder,
                          step1_status=STEP_SKIPPED)
            pipeline=FakePipeline(task,[2])
            self.assertEqual(pipeline._run_pipeline(1),(1,0,0))
            self.assertEqual(pipeline.ran,[2])
            self.assertEqual(task.step3_status,STEP_PENDING)
            self.assertNotIn((3,100),pipeline.step_progress_signal.calls)

    def test_only_mixing_does_not_start_tts(self):
        with tempfile.TemporaryDirectory() as folder:
            task=TaskInfo('id',str(Path(folder)/'src.wav'),'src',folder,
                          step1_status=STEP_SKIPPED, step2_status=STEP_SKIPPED)
            pipeline=FakePipeline(task,[3])
            self.assertEqual(pipeline._run_pipeline(1),(1,0,0))
            self.assertEqual(pipeline.ran,[3])
            self.assertEqual(task.step2_status,STEP_SKIPPED)
            self.assertNotIn((2,100),pipeline.step_progress_signal.calls)

    def test_parallel_tts_progress_never_moves_backwards(self):
        class ParallelPipeline(FakePipeline):
            def __init__(self, tasks):
                super().__init__(tasks[0], [2])
                self.tasks = tasks
                self.first_reported = threading.Event()
                self.second_reported = threading.Event()

            def _run_single_step_sync(self, task, step, *args, progress_callback=None):
                if task.task_id == 'first':
                    progress_callback(80)
                    self.first_reported.set()
                    if not self.second_reported.wait(5):
                        raise TimeoutError('第二个语音任务没有并行启动')
                else:
                    if not self.first_reported.wait(5):
                        raise TimeoutError('第一个语音任务没有上报进度')
                    progress_callback(10)
                    self.second_reported.set()
                progress_callback(100)
                task.set_step_status(step, STEP_DONE)
                return True, 'ok'

        with tempfile.TemporaryDirectory() as folder:
            tasks = [TaskInfo(task_id, str(Path(folder) / f'{task_id}.wav'),
                              task_id, folder, step1_status=STEP_SKIPPED)
                     for task_id in ('first', 'second')]
            pipeline = ParallelPipeline(tasks)
            self.assertEqual(pipeline._run_pipeline(2), (2, 0, 0))
            values = [value for step, value in pipeline.step_progress_signal.calls if step == 2]
            self.assertEqual(values, sorted(values))
            self.assertIn(40, values)
            self.assertIn(45, values)
            self.assertEqual(values[-1], 100)


if __name__ == '__main__':
    unittest.main()
