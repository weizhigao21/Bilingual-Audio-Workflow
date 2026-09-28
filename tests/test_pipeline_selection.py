import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.steps.batch_executor.pipeline_mixin import PipelineMixin
from src.task_manager import TaskInfo, STEP_DONE, STEP_PENDING, STEP_SKIPPED, STEP_FAILED


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
        for name in ('log_signal','task_finished','task_started','progress_signal',
                     'step_progress_signal','step_status_signal'):
            setattr(self,name,Signal())

    def _resolve_source_path(self, task):
        return True

    def _run_single_step_sync(self, task, step, *args, progress_callback=None):
        self.ran.append(step)
        if progress_callback is not None:
            progress_callback(50)
        task.set_step_status(step, STEP_DONE)
        return True, 'ok'

    def _run_whisper_batch(self, total, failed, on_task_ready=None):
        self.whisper_batches += 1
        for task in self.tasks:
            task.set_step_status(1, STEP_DONE)
            self.task_finished.emit(task.task_id, True)
            if on_task_ready is not None:
                on_task_ready(task)


class PipelineTests(unittest.TestCase):
    def test_subtitles_use_one_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            task=TaskInfo('id',str(Path(folder)/'src.wav'),'src',folder)
            pipeline=FakePipeline(task,[1])
            self.assertEqual(pipeline._run_pipeline(1),(1,0,0))
            self.assertEqual(pipeline.whisper_batches,1)
            self.assertEqual(pipeline.ran,[])

    def test_tts_and_mix_finish_first_task_before_last_subtitle(self):
        class StreamingPipeline(FakePipeline):
            def __init__(self, tasks):
                super().__init__(tasks[0], [1, 2, 3])
                self.tasks = tasks
                self.first_mixed = threading.Event()
                self.calls = []
                self.overlapped = False

            def _run_whisper_batch(self, total, failed, on_task_ready=None):
                self.whisper_batches += 1
                # 模型启动期间没有就绪任务，消费者不能把空队列当成完成。
                threading.Event().wait(0.3)
                first, second = self.tasks
                first.set_step_status(1, STEP_DONE)
                self.task_finished.emit(first.task_id, True)
                on_task_ready(first)
                on_task_ready(first)  # 重复确认不能重复合成或混音。
                self.overlapped = self.first_mixed.wait(5)
                second.set_step_status(1, STEP_DONE)
                on_task_ready(second)

            def _run_single_step_sync(self, task, step, *args, progress_callback=None):
                self.calls.append((task.task_id, step))
                progress_callback(100)
                task.set_step_status(step, STEP_DONE)
                if task.task_id == 'first' and step == 3:
                    self.first_mixed.set()
                return True, 'ok'

        with tempfile.TemporaryDirectory() as folder:
            tasks = [TaskInfo(task_id, str(Path(folder) / f'{task_id}.wav'),
                              task_id, folder) for task_id in ('first', 'second')]
            pipeline = StreamingPipeline(tasks)
            self.assertEqual(pipeline._run_pipeline(2), (2, 0, 0))
            self.assertTrue(pipeline.overlapped, '仍在等待整批字幕才启动语音/混音')
            self.assertEqual(pipeline.whisper_batches, 1)
            self.assertCountEqual(pipeline.calls,
                                  [('first', 2), ('first', 3), ('second', 2), ('second', 3)])

    def test_failed_subtitle_does_not_generate_voice(self):
        class PartialPipeline(FakePipeline):
            def _run_whisper_batch(self, total, failed, on_task_ready=None):
                first, second = self.tasks
                first.set_step_status(1, STEP_DONE)
                on_task_ready(first)
                second.set_step_status(1, STEP_FAILED)
                failed.add(second.task_id)

        with tempfile.TemporaryDirectory() as folder:
            tasks = [TaskInfo(task_id, str(Path(folder) / f'{task_id}.wav'),
                              task_id, folder) for task_id in ('first', 'second')]
            pipeline = PartialPipeline(tasks[0], [1, 2, 3])
            pipeline.tasks = tasks
            self.assertEqual(pipeline._run_pipeline(2), (1, 1, 0))
            self.assertEqual(tasks[1].step2_status, STEP_PENDING)
            self.assertCountEqual(pipeline.ran, [2, 3])

    def test_stop_while_waiting_for_subtitles_exits_consumers(self):
        class StoppedPipeline(FakePipeline):
            def _run_whisper_batch(self, total, failed, on_task_ready=None):
                self._stop_flag = True

        with tempfile.TemporaryDirectory() as folder:
            task = TaskInfo('id', str(Path(folder) / 'src.wav'), 'src', folder)
            pipeline = StoppedPipeline(task, [1, 2, 3])
            self.assertEqual(pipeline._run_pipeline(1), (0, 0, 1))
            self.assertEqual(pipeline.ran, [])
            self.assertNotIn((2, 100), pipeline.step_progress_signal.calls)

    def test_tts_exception_does_not_strand_next_task(self):
        class ErrorPipeline(FakePipeline):
            def _run_single_step_sync(self, task, step, *args, progress_callback=None):
                if task.task_id == 'first':
                    raise RuntimeError('generation error')
                return super()._run_single_step_sync(
                    task, step, *args, progress_callback=progress_callback
                )

        with tempfile.TemporaryDirectory() as folder:
            tasks = [TaskInfo(task_id, str(Path(folder) / f'{task_id}.wav'),
                              task_id, folder, step1_status=STEP_SKIPPED)
                     for task_id in ('first', 'second')]
            pipeline = ErrorPipeline(tasks[0], [2])
            pipeline.tasks = tasks
            pipeline.config.tts_cfg['pipeline_tts_workers'] = 1
            self.assertEqual(pipeline._run_pipeline(2), (1, 1, 0))
            self.assertEqual(tasks[0].step2_status, STEP_FAILED)
            self.assertEqual(tasks[1].step2_status, STEP_DONE)

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

    def test_parallel_mix_status_lists_active_short_names(self):
        class ParallelMixPipeline(FakePipeline):
            def __init__(self, tasks):
                super().__init__(tasks[0], [3])
                self.tasks = tasks
                self.barrier = threading.Barrier(2, timeout=5)

            def _run_single_step_sync(self, task, step, *args, progress_callback=None):
                self.barrier.wait()
                progress_callback(100)
                task.set_step_status(step, STEP_DONE)
                return True, 'ok'

        with tempfile.TemporaryDirectory() as folder:
            tasks = [TaskInfo(task_id, str(Path(folder) / f'{name}.wav'),
                              name, folder, step1_status=STEP_SKIPPED,
                              step2_status=STEP_SKIPPED)
                     for task_id, name in (('first', 'to11_more'),
                                           ('second', 'to22_more'))]
            pipeline = ParallelMixPipeline(tasks)
            self.assertEqual(pipeline._run_pipeline(2), (2, 0, 0))
            statuses = [text for step, text in pipeline.step_status_signal.calls
                        if step == 3]
            self.assertTrue(any(set(text.removeprefix('当前任务：').split('，'))
                                == {'to11', 'to22'} for text in statuses))
            self.assertEqual(statuses[-1], '')


if __name__ == '__main__':
    unittest.main()
