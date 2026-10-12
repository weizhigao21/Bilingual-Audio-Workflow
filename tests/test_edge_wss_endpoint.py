import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import requests
from PyQt6.QtWidgets import QApplication

import edge_tts.communicate as edge_communicate

from src.steps.tts_utils import edge_tts_task, patch_edge_wss_endpoint
from src.widgets.local_voice_picker import DiscoveryTask
from src.widgets.tts_panel import TTSConfigPanel


class EdgeWssEndpointTests(unittest.TestCase):
    """锁定 rany2/edge-tts#491：微软 2026-10 起只认 api.msedgeservices.com。"""

    def test_module_import_applies_new_endpoint(self):
        # 导入 src.steps.tts_utils 时补丁即生效，无需调用方干预
        self.assertIn("api.msedgeservices.com", edge_communicate.WSS_URL)
        self.assertNotIn("speech.platform.bing.com", edge_communicate.WSS_URL)

    def test_synthesis_uses_new_host(self):
        url = patch_edge_wss_endpoint()
        self.assertIn("api.msedgeservices.com", url)
        self.assertNotIn("speech.platform.bing.com", url)
        self.assertIn("Ocp-Apim-Subscription-Key=", url)

    def test_patch_respects_official_fix_and_stays_idempotent(self):
        official = ("wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
                    "?Ocp-Apim-Subscription-Key=official-token")
        with patch.object(edge_communicate, "WSS_URL", official):
            # 官方修复发布后库自带新端点：补丁不得覆盖
            self.assertEqual(patch_edge_wss_endpoint(), official)
        legacy = ("wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud"
                  "/edge/v1?TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4")
        with patch.object(edge_communicate, "WSS_URL", legacy):
            # 端点被重置回旧值后再次调用仍能修复
            self.assertIn("api.msedgeservices.com", patch_edge_wss_endpoint())
        # 模块导入时补丁已生效，with 结束后恢复的即新端点
        self.assertIn("api.msedgeservices.com", edge_communicate.WSS_URL)

    def test_403_failure_reports_actionable_hint(self):
        async def handshake_403(*args):
            raise RuntimeError("WSServerHandshakeError: 403, message='Invalid response status'")

        cache = SimpleNamespace(get_cached_audio=lambda *args: None)
        with tempfile.TemporaryDirectory() as folder, \
             patch('src.steps.tts_utils._edge_tts_generate', handshake_403):
            ok, message = edge_tts_task(1, '00:00.00', 'test', 'zh-CN-YunxiNeural',
                                        '+0%', '+0%', folder, cache, max_retries=1)
        self.assertFalse(ok)
        self.assertIn('403', message)
        self.assertIn('时钟', message)


class ShengjianVoicePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, mode):
        config = SimpleNamespace(
            tts_cfg={'tts_mode': mode,
                     'api_configs': [{'name': '声笺', 'url': 'http://127.0.0.1:8765',
                                      'model': 'm', 'voice_discovery': True,
                                      'discovery_url': 'http://127.0.0.1:8765/workflow'}],
                     'current_api_index': 0},
            save=lambda: None)
        with patch.object(TTSConfigPanel, '_refresh_cache_info'):
            return TTSConfigPanel(config)

    def test_edge_mode_does_not_probe_shengjian(self):
        panel = self.make_panel('edge')
        picker = panel.local_voice_picker
        with patch('src.widgets.local_voice_picker.DiscoveryTask') as task, \
             patch('src.widgets.local_voice_picker.QThreadPool') as pool:
            picker.sync_from_config()
        task.assert_not_called()
        pool.globalInstance.assert_not_called()
        self.assertIn('Edge', picker.hint.text())

    def test_api_mode_still_probes_shengjian(self):
        panel = self.make_panel('api')
        picker = panel.local_voice_picker
        with patch.object(type(picker), 'isVisible', return_value=True), \
             patch('src.widgets.local_voice_picker.DiscoveryTask') as task, \
             patch('src.widgets.local_voice_picker.QThreadPool') as pool:
            picker.sync_from_config()
        task.assert_called_once()
        pool.globalInstance.return_value.start.assert_called_once()

    def test_stale_probe_result_discarded_after_switch_to_edge(self):
        panel = self.make_panel('api')
        picker = panel.local_voice_picker
        picker.serial = 1
        picker.tasks[1] = (MagicMock(), {'importing': False,
                                         'selection': picker.selection_token()})
        # 用户在探测在途时切换到 Edge 模式
        panel.config.tts_cfg['tts_mode'] = 'edge'
        logs = []
        panel.log_message.connect(logs.append)
        picker.request_done(1, None, '读取音色失败：无法连接 http://127.0.0.1:8765，请确认声笺已启动。')
        self.assertEqual(logs, [])
        self.assertIn('Edge', picker.hint.text())

    def test_probe_failure_still_logged_in_api_mode(self):
        panel = self.make_panel('api')
        picker = panel.local_voice_picker
        picker.serial = 1
        picker.tasks[1] = (MagicMock(), {'importing': False,
                                         'selection': picker.selection_token()})
        logs = []
        panel.log_message.connect(logs.append)
        picker.request_done(1, None, '读取音色失败：无法连接 http://127.0.0.1:8765，请确认声笺已启动。')
        self.assertTrue(any('声笺音色' in message for message in logs))

    def test_connection_refused_message_is_friendly(self):
        picker_task = DiscoveryTask(1, 'http://127.0.0.1:8765')
        received = []
        picker_task.signals.done.connect(
            lambda token, catalog, error: received.append((catalog, error)))
        with patch('src.widgets.local_voice_picker.discover_voices',
                   side_effect=requests.exceptions.ConnectionError(
                       "HTTPConnectionPool(host='127.0.0.1', port=8765): "
                       "Max retries exceeded")):
            picker_task.run()
        self.assertEqual(len(received), 1)
        catalog, error = received[0]
        self.assertIsNone(catalog)
        self.assertIn('无法连接', error)
        self.assertIn('已启动', error)
        self.assertNotIn('HTTPConnectionPool', error)


if __name__ == '__main__':
    unittest.main()
