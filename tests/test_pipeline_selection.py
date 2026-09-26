import tempfile
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
        for name in ('log_signal','task_finished','task_started','progress_signal','step_progress_signal'):
            setattr(self,name,Signal())

    def _resolve_source_path(self, task):
        return True

    def _run_single_step_sync(self, task, step, *args):
        self.ran.append(step)
        task.set_step_status(step, STEP_DONE)
        return True, 'ok'


class PipelineTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
