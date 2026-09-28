# -*- coding: utf-8 -*-
"""语音输出目录命名（md5(字幕内容 | 配置签名)）与历史目录迁移的守卫。

回归的问题：目录名过去只由字幕内容决定，换模型/声音后同名片段被覆盖进
同一目录，残留片段又被混音器无差别收录 → 两个模型的声音混在一条音轨里。
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from src.steps.tts_profile import (MANIFEST, api_cache_model, dir_name,
                                   legacy_dir_name, profile_key, save_profile)
from src.task_manager import TaskQueue
from src.steps.tts_utils import generate_filename
from src.steps.audio_utils import parse_filename

EDGE_CFG = {'tts_mode': 'edge', 'edge_voice': 'zh-CN-XiaoxiaoNeural',
            'edge_rate': '+0%', 'edge_volume': '+0%'}
API_CFG = {'tts_mode': 'api', 'use_multi_api': False, 'current_api_index': 0,
           'use_bulk_api': True,
           'api_configs': [{'name': '本地', 'url': 'http://127.0.0.1:8000',
                            'model': '八重神子_ZH', 'model_tag': ''}]}


def _legacy_profile_key_edge(cfg):
    """改造前的 Edge profile_key 算法（用于兼容性对照）。"""
    relevant = {'mode': 'edge', 'voice': cfg.get('edge_voice'),
                'rate': cfg.get('edge_rate'), 'volume': cfg.get('edge_volume')}
    return hashlib.sha256(json.dumps(relevant, ensure_ascii=False,
                                     sort_keys=True).encode('utf-8')).hexdigest()


def _legacy_profile_key_api(cfg):
    """改造前的 API profile_key 算法（未填 model_tag 时必须完全一致）。"""
    apis = cfg.get('api_configs', [])
    idx = cfg.get('current_api_index', 0)
    selected = [apis[idx]] if 0 <= idx < len(apis) else apis[:1]
    relevant = {'mode': 'api',
                'apis': [(a.get('url', '').rstrip('/'), a.get('model', '')) for a in selected],
                'bulk': bool(cfg.get('use_bulk_api', True))}
    return hashlib.sha256(json.dumps(relevant, ensure_ascii=False,
                                     sort_keys=True).encode('utf-8')).hexdigest()


class VoiceDirNameTests(unittest.TestCase):
    def test_dir_name_changes_with_voice_and_content(self):
        content = b'[00:00.00]hello'
        base = dir_name(content, EDGE_CFG)
        self.assertEqual(base, dir_name(content, dict(EDGE_CFG)))
        self.assertEqual(len(base), 8)
        self.assertNotEqual(base, legacy_dir_name(content),
                            '新命名必须与"只哈希字幕内容"的旧命名不同')

        other_voice = dict(EDGE_CFG, edge_voice='zh-CN-YunxiNeural')
        other_rate = dict(EDGE_CFG, edge_rate='+10%')
        self.assertNotEqual(base, dir_name(content, other_voice))
        self.assertNotEqual(base, dir_name(content, other_rate))
        self.assertNotEqual(base, dir_name(b'[00:00.00]hello2', EDGE_CFG))

    def test_same_config_same_content_is_stable(self):
        content = b'[00:00.00]same'
        self.assertEqual(dir_name(content, EDGE_CFG), dir_name(content, EDGE_CFG))

    def test_api_model_tag_splits_dirs(self):
        """同一 URL + 同模型名，只靠 model_tag 区分 → 目录必须不同。"""
        tagged_a = json.loads(json.dumps(API_CFG))
        tagged_a['api_configs'][0]['model_tag'] = '女声A'
        tagged_b = json.loads(json.dumps(API_CFG))
        tagged_b['api_configs'][0]['model_tag'] = '女声B'

        content = b'[00:00.00]hello'
        self.assertNotEqual(dir_name(content, tagged_a), dir_name(content, tagged_b))
        self.assertNotEqual(dir_name(content, tagged_a), dir_name(content, API_CFG))

    def test_profile_key_unchanged_when_tag_empty(self):
        """未填 model_tag 时必须与旧算法逐位一致，否则既有目录/缓存会整体失效。"""
        self.assertEqual(profile_key(EDGE_CFG), _legacy_profile_key_edge(EDGE_CFG))
        self.assertEqual(profile_key(API_CFG), _legacy_profile_key_api(API_CFG))

    def test_api_cache_model_unchanged_without_tag(self):
        old = f"{'八重神子_ZH'}|{'http://127.0.0.1:8000'}"
        self.assertEqual(api_cache_model('http://127.0.0.1:8000', '八重神子_ZH'), old)
        self.assertEqual(api_cache_model('http://127.0.0.1:8000/', '八重神子_ZH'), old)
        tagged = api_cache_model('http://127.0.0.1:8000', '八重神子_ZH', '女声B')
        self.assertNotEqual(tagged, old)
        self.assertIn('女声B', tagged)
        self.assertNotEqual(
            tagged, api_cache_model('http://127.0.0.1:8000', '八重神子_ZH', '女声A'),
            '不同模型标签不能命中同一条缓存')

    def test_segment_filename_rule_untouched(self):
        """片段文件名规则保持不变（混音器解析依赖它）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = generate_filename(1, '00:00:00.00', '学长好厉害', tmp)
            self.assertEqual(os.path.basename(path), '0001_00-00-00.00_学长.wav')
            parsed = parse_filename(os.path.basename(path))
            self.assertEqual(parsed['sequence'], 1)
            self.assertEqual(parsed['timestamp_ms'], 0)


class VoiceDirMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / 'workspace'
        self.subtitle = self.root / 'source.lrc'
        self.subtitle.write_text('[00:00.00]hello', encoding='utf-8')
        self.content = self.subtitle.read_bytes()
        self.source = self.root / 'source.wav'
        self.source.write_bytes(b'audio')

        queue = TaskQueue(str(self.workspace))
        self.task = queue.add_task(str(self.source), subtitle_path=str(self.subtitle))

    def _make_voice_dir(self, name, cfg, wav_name='0001_00-00-00.00_hello.wav'):
        directory = self.workspace / name
        directory.mkdir(parents=True)
        (directory / wav_name).write_bytes(b'clip')
        if cfg is not None:
            save_profile(str(directory), cfg)
        return directory

    def test_legacy_dir_is_migrated_and_reused(self):
        """旧命名目录 + 配置签名匹配 → 原地改名复用，不重新生成。"""
        legacy = self._make_voice_dir(legacy_dir_name(self.content), EDGE_CFG)
        expected = self.workspace / dir_name(self.content, EDGE_CFG)

        found = self.task._detect_tts_by_subtitle_md5(EDGE_CFG)

        self.assertEqual(found, str(expected))
        self.assertTrue(expected.is_dir())
        self.assertFalse(legacy.exists(), '迁移后旧命名目录不应残留')
        self.assertTrue((expected / '0001_00-00-00.00_hello.wav').is_file())
        self.assertEqual(json.loads((expected / MANIFEST).read_text(encoding='utf-8')),
                         {'profile': profile_key(EDGE_CFG)})

    def test_legacy_dir_not_migrated_when_profile_differs(self):
        """签名不匹配（换了声音）→ 不迁移、不复用，交给上层重新生成。"""
        legacy = self._make_voice_dir(legacy_dir_name(self.content), EDGE_CFG)

        found = self.task._detect_tts_by_subtitle_md5(
            dict(EDGE_CFG, edge_voice='zh-CN-YunxiNeural'))

        self.assertEqual(found, '')
        self.assertTrue(legacy.is_dir(), '配置不匹配时不得改动旧目录')

    def test_legacy_dir_without_manifest_is_not_reused(self):
        legacy = self._make_voice_dir(legacy_dir_name(self.content), None)
        self.assertEqual(self.task._detect_tts_by_subtitle_md5(EDGE_CFG), '')
        self.assertTrue(legacy.is_dir())

    def test_new_named_dir_wins_and_legacy_left_alone(self):
        new = self._make_voice_dir(dir_name(self.content, EDGE_CFG), EDGE_CFG)
        legacy = self._make_voice_dir(legacy_dir_name(self.content), EDGE_CFG)

        self.assertEqual(self.task._detect_tts_by_subtitle_md5(EDGE_CFG), str(new))
        self.assertTrue(legacy.is_dir())

    def test_migration_blocked_when_target_occupied(self):
        """目标名已被一个文件占住 → 不迁移，返回空（宁可重新生成）。"""
        self._make_voice_dir(legacy_dir_name(self.content), EDGE_CFG)
        (self.workspace / dir_name(self.content, EDGE_CFG)).write_bytes(b'occupied')

        self.assertEqual(self.task._detect_tts_by_subtitle_md5(EDGE_CFG), '')

    def test_two_models_get_two_dirs_same_subtitle(self):
        """同一字幕、两个配置 → 两个独立目录，互不覆盖。"""
        name_a = dir_name(self.content, EDGE_CFG)
        name_b = dir_name(self.content, dict(EDGE_CFG, edge_voice='zh-CN-YunxiNeural'))
        self.assertNotEqual(name_a, name_b)
        dir_a = self._make_voice_dir(name_a, EDGE_CFG)
        dir_b = self._make_voice_dir(name_b, dict(EDGE_CFG, edge_voice='zh-CN-YunxiNeural'))

        self.assertEqual(self.task._detect_tts_by_subtitle_md5(EDGE_CFG), str(dir_a))
        self.assertTrue(dir_b.is_dir())
        self.assertTrue(dir_a.is_dir())

    def test_no_config_falls_back_to_legacy_lookup(self):
        """tts_config 为空时保持旧行为：只按旧命名查找，不做迁移。"""
        legacy = self._make_voice_dir(legacy_dir_name(self.content), None)
        self.assertEqual(self.task._detect_tts_by_subtitle_md5(None), str(legacy))
        self.assertTrue(legacy.is_dir())

    def test_restore_migrates_legacy_dir_of_existing_task(self):
        """已存在的任务在启动恢复时把旧命名目录迁到新命名并落盘。"""
        legacy = self._make_voice_dir(legacy_dir_name(self.content), EDGE_CFG)
        expected = self.workspace / dir_name(self.content, EDGE_CFG)
        self.task.step2_status = 'done'
        self.task.step2_output = str(legacy)
        self.task.save()

        restored = TaskQueue(str(self.workspace), EDGE_CFG)
        self.assertEqual(restored.restore_tasks(), (1, 0))
        recovered = restored.tasks[0]
        self.assertEqual(recovered.step2_output, str(expected))
        self.assertTrue(expected.is_dir())
        self.assertFalse(legacy.exists())
        self.assertEqual(
            json.loads(Path(recovered.task_json_path).read_text(encoding='utf-8'))['step2_output'],
            str(expected), '迁移结果要落盘，否则重启后又指回旧目录')

    def test_restore_leaves_foreign_voice_dir_alone(self):
        """目录名既不等于新命名也不等于旧命名（比如手工改名）→ 不动它。"""
        foreign = self._make_voice_dir('my_custom_voice', EDGE_CFG)
        self.task.step2_status = 'done'
        self.task.step2_output = str(foreign)
        self.task.save()

        restored = TaskQueue(str(self.workspace), EDGE_CFG)
        self.assertEqual(restored.restore_tasks(), (1, 0))
        self.assertEqual(restored.tasks[0].step2_output, str(foreign))
        self.assertTrue(foreign.is_dir())

    def test_restore_does_not_migrate_video_outside_workspace(self):
        """自定义配音目录（不在工作区）不参与迁移。"""
        outside = self.root / 'custom_voice'
        outside.mkdir()
        (outside / '0001_00-00-00.00_hi.wav').write_bytes(b'clip')
        save_profile(str(outside), EDGE_CFG)
        self.task.step2_status = 'done'
        self.task.step2_output = str(outside)
        self.task.save()

        restored = TaskQueue(str(self.workspace), EDGE_CFG)
        restored.restore_tasks()
        self.assertEqual(restored.tasks[0].step2_output, str(outside))
        self.assertTrue(outside.is_dir())


if __name__ == '__main__':
    unittest.main()
