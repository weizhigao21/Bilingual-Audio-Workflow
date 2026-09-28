import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from edge_tts.exceptions import NoAudioReceived

from src.widgets.tts_panel import TTSConfigPanel
from src.steps.edge_voices import EDGE_TTS_VOICES
from src.steps.tts_worker import TTSWorker
from src.steps.tts_utils import edge_tts_task


class EdgeVoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, voice):
        config = SimpleNamespace(tts_cfg={'tts_mode': 'edge', 'edge_voice': voice},
                                 save=lambda: None)
        with patch.object(TTSConfigPanel, '_refresh_cache_info'):
            panel = TTSConfigPanel(config)
        return panel, config

    def test_saved_unavailable_voice_is_not_silently_shown_as_first(self):
        panel, config = self.make_panel('zh-CN-XiaochenNeural')
        self.assertEqual(panel.voice_combo.currentData(), 'zh-CN-XiaochenNeural')
        self.assertIn('不可用', panel.voice_combo.currentText())
        self.assertIn('请重新选择', panel.voice_hint.text())
        panel.voice_combo.setCurrentIndex(panel.voice_combo.findData('zh-CN-YunxiNeural'))
        self.assertEqual(config.tts_cfg['edge_voice'], 'zh-CN-YunxiNeural')

    def test_all_available_options_save_their_own_voice(self):
        panel, config = self.make_panel('zh-CN-XiaoxiaoNeural')
        self.assertEqual(panel.voice_combo.count(), 6)
        for voice in EDGE_TTS_VOICES:
            panel.voice_combo.setCurrentIndex(panel.voice_combo.findData(voice))
            self.assertEqual(config.tts_cfg['edge_voice'], voice)
        self.assertEqual(panel.voice_combo.findData('zh-CN-XiaochenNeural'), -1)

    def test_unavailable_voice_fails_before_submitting_segments(self):
        with patch('src.steps.tts_worker.AudioCache'):
            worker = TTSWorker({'edge_voice': 'zh-CN-XiaochenNeural'})
        logs = []
        worker.log_signal.connect(logs.append, Qt.ConnectionType.DirectConnection)
        with patch.object(worker, '_finish_generation') as finish, \
             patch('src.steps.tts_worker.get_edge_executor') as executor:
            worker._run_edge_tts([('unused',)], 0, 0, time.time(), 'id', 'unused')
        executor.assert_not_called()
        finish.assert_called_once_with(False, 'unused')
        self.assertTrue(any('zh-CN-XiaochenNeural' in message for message in logs))

    def test_no_audio_error_identifies_voice(self):
        async def no_audio(*args):
            raise NoAudioReceived('No audio was received')

        cache = SimpleNamespace(get_cached_audio=lambda *args: None)
        with tempfile.TemporaryDirectory() as folder, \
             patch('src.steps.tts_utils._edge_tts_generate', no_audio):
            ok, message = edge_tts_task(1, '00:00.00', 'test', 'zh-CN-YunxiNeural',
                                        '+0%', '+0%', folder, cache, max_retries=1)
        self.assertFalse(ok)
        self.assertIn('zh-CN-YunxiNeural', message)
        self.assertIn('NoAudioReceived', message)


if __name__ == '__main__':
    unittest.main()
