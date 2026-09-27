import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.config import DEFAULT_CONFIG
from src.widgets.mixer_panel import MixerConfigPanel
from src.widgets.mix_metadata_settings import MixMetadataSettingsDialog


class _Config:
    def __init__(self):
        self.mixer_cfg = copy.deepcopy(DEFAULT_CONFIG["mixer"])
        self.saved = 0

    def save(self):
        self.saved += 1


class MixMetadataSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_toggle_and_editor_save(self):
        config = _Config()
        panel = MixerConfigPanel(config)
        self.addCleanup(panel.deleteLater)
        self.assertTrue(panel.metadata_check.isChecked())
        panel.metadata_check.setChecked(False)
        self.assertFalse(config.mixer_cfg["metadata_enabled"])

        dialog = MixMetadataSettingsDialog(config, panel)
        dialog.field_checks["album"].setChecked(False)
        dialog.value_edits["artist"].setText("作者")
        dialog.version_check.setChecked(False)
        dialog._save()
        self.assertFalse(config.mixer_cfg["metadata_fields"]["album"])
        self.assertFalse(config.mixer_cfg["metadata_fields"]["version"])
        self.assertEqual(config.mixer_cfg["metadata_values"]["artist"], "作者")

        reopened = MixMetadataSettingsDialog(config, panel)
        self.assertFalse(reopened.field_checks["album"].isChecked())
        self.assertFalse(reopened.version_check.isChecked())
        self.assertEqual(reopened.value_edits["artist"].text(), "作者")
        self.assertGreaterEqual(config.saved, 2)


if __name__ == "__main__":
    unittest.main()
