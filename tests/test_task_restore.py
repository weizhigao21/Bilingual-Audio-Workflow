import json
import tempfile
import unittest
from pathlib import Path

from src.task_manager import (TaskQueue, STEP_DONE, STEP_FAILED,
                              STEP_PENDING, STEP_RUNNING, STEP_SKIPPED)
from src.steps.tts_profile import save_profile
from src.workflow_gui.rename_mixin import RenameMixin


class TaskRestoreTests(unittest.TestCase):
    def test_restore_groups_and_existing_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            workspace=Path(root)/'workspace'
            source=Path(root)/'source.wav'
            source.write_bytes(b'source')
            subtitle=Path(root)/'source.lrc'
            subtitle.write_text('[00:00.00]hi', encoding='utf-8')
            clips=Path(root)/'clips'
            clips.mkdir()
            (clips/'0001_00-00-00.00_hi.wav').write_bytes(b'clip')
            mixed=Path(root)/'source_mixed.mp3'
            mixed.write_bytes(b'mixed')
            queue=TaskQueue(str(workspace))
            group=queue.create_group(root)
            task=queue.add_task(str(source), subtitle_path=str(subtitle),
                                mix_folder=str(clips), group_id=group.group_id)
            task.step3_status=STEP_DONE
            task.step3_output=str(mixed)
            queue.update_task(task)
            restored=TaskQueue(str(workspace))
            self.assertEqual(restored.restore_tasks(), (1,0))
            self.assertEqual(len(restored.groups),1)
            self.assertEqual(restored.groups[0].group_id,group.group_id)
            self.assertIs(restored.groups[0].tasks[0],restored.tasks[0])
            self.assertEqual(restored.tasks[0].step3_status,STEP_DONE)
            self.assertEqual(restored.tasks[0].source_path,str(source))

    def test_running_and_missing_outputs_reset_without_losing_source(self):
        with tempfile.TemporaryDirectory() as root:
            workspace=Path(root)/'workspace'
            source=Path(root)/'source.wav'
            source.write_bytes(b'source')
            queue=TaskQueue(str(workspace))
            task=queue.add_task(str(source))
            task.step1_status=STEP_RUNNING
            task.step2_status=STEP_DONE
            task.step2_output=str(Path(root)/'missing-clips')
            task.step3_status=STEP_DONE
            task.step3_output=str(Path(root)/'missing-mix')
            queue.update_task(task)
            new=TaskQueue(str(workspace))
            self.assertEqual(new.restore_tasks(),(1,0))
            recovered=new.tasks[0]
            self.assertEqual([recovered.step_status(s) for s in (1,2,3)],
                             [STEP_PENDING]*3)
            self.assertIn('中断',recovered.step1_error)
            self.assertEqual(recovered.source_path,str(source))
            self.assertEqual(json.loads(Path(recovered.task_json_path).read_text(encoding='utf-8'))['step1_status'],STEP_PENDING)

    def test_profile_change_invalidates_mixed_output(self):
        with tempfile.TemporaryDirectory() as root:
            workspace=Path(root)/'workspace'
            source=Path(root)/'source.wav'
            source.write_bytes(b'source')
            clips=Path(root)/'clips'
            clips.mkdir()
            (clips/'0001.wav').write_bytes(b'clip')
            old={'tts_mode':'edge','edge_voice':'v1'}
            new={'tts_mode':'edge','edge_voice':'v2'}
            save_profile(str(clips),old)
            mixed=Path(root)/'双语'
            mixed.mkdir()
            (mixed/'source_mixed.mp3').write_bytes(b'mixed')
            queue=TaskQueue(str(workspace),old)
            task=queue.add_task(str(source))
            task.step1_status=STEP_DONE
            subtitle=Path(root)/'source.lrc'
            subtitle.write_bytes(b'subtitle')
            task.step1_output=str(subtitle)
            task.step2_status=STEP_DONE
            task.step2_output=str(clips)
            task.step3_status=STEP_DONE
            task.step3_output=str(mixed/'source_mixed.mp3')
            queue.update_task(task)
            restored=TaskQueue(str(workspace),new)
            self.assertEqual(restored.restore_tasks(),(1,0))
            recovered=restored.tasks[0]
            self.assertEqual(recovered.step1_status,STEP_DONE)
            self.assertEqual(recovered.step2_status,STEP_PENDING)
            self.assertEqual(recovered.step3_status,STEP_PENDING)
            self.assertTrue(recovered.force_remix)

    def test_remove_and_clear_are_durable(self):
        with tempfile.TemporaryDirectory() as root:
            queue=TaskQueue(root)
            source=Path(root)/'source.wav'
            source.write_bytes(b'file')
            task=queue.add_task(str(source))
            record=Path(task.task_json_path)
            self.assertTrue(record.exists())
            queue.remove_task(task.task_id)
            self.assertFalse(record.exists())
            task=queue.add_task(str(source))
            record=Path(task.task_json_path)
            queue.clear_all(delete_files=False)
            self.assertFalse(record.exists())
            self.assertEqual(TaskQueue(root).restore_tasks(),(0,0))

    def test_bad_record_does_not_prevent_other_tasks(self):
        with tempfile.TemporaryDirectory() as root:
            queue=TaskQueue(root)
            source=Path(root)/'source.wav'
            source.write_bytes(b'file')
            queue.add_task(str(source))
            bad=Path(root)/'bad'
            bad.mkdir()
            (bad/'task.json').write_text('{invalid',encoding='utf-8')
            loaded=TaskQueue(root)
            with self.assertLogs('src.task_manager',level='WARNING'):
                self.assertEqual(loaded.restore_tasks(),(1,1))

    def test_renamed_source_folder_paths_survive_restart(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            source_dir=root/'album'
            source_dir.mkdir()
            source=source_dir/'source.wav'
            source.write_bytes(b'file')
            subtitle=source_dir/'source.lrc'
            subtitle.write_bytes(b'lyric')
            output_dir=source_dir/'双语'
            output_dir.mkdir()
            mixed=output_dir/'source_mixed.mp3'
            mixed.write_bytes(b'mixed')
            clips=root/'clips'
            clips.mkdir()
            queue=TaskQueue(str(root/'workspace'))
            group=queue.create_group(str(source_dir))
            task=queue.add_task(str(source),subtitle_path=str(subtitle),
                                mix_folder=str(clips),group_id=group.group_id)
            task.step3_status=STEP_DONE
            task.step3_output=str(mixed)
            queue.update_task(task)
            class Renamer(RenameMixin):
                def __init__(self, task_queue): self.task_queue=task_queue
                def _append_log(self, message): pass
            Renamer(queue)._do_rename_source_folder(task)
            new_dir=root/'双语-album'
            self.assertTrue(new_dir.is_dir())
            restored=TaskQueue(str(root/'workspace'))
            self.assertEqual(restored.restore_tasks(),(1,0))
            recovered=restored.tasks[0]
            self.assertEqual(recovered.source_path,str(new_dir/'source.wav'))
            self.assertEqual(recovered.step1_output,str(new_dir/'source.lrc'))
            self.assertEqual(recovered.step3_output,str(new_dir/'双语'/'source_mixed.mp3'))
            self.assertEqual(recovered.step3_status,STEP_DONE)


if __name__=='__main__':
    unittest.main()
