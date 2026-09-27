import unittest

from src.steps.whisper_progress import WhisperProgress, show_in_gui


class WhisperProgressTests(unittest.TestCase):
    def test_six_files_keep_overall_progress_monotonic(self):
        progress = WhisperProgress(6)
        values = []
        statuses = []
        for index in range(1, 7):
            lines = [
                f"正在翻译（{index}/6）：音频{index}.wav",
                "VAD进度：25/50 块（50.0%）在 GPU (CUDA) 上",
                "VAD进度：50/50 块（100.0%）在 GPU (CUDA) 上时长：24分58.5秒 → 19分13.5秒",
                "[00:01.86 --> 00:04.96] 第一段",
                "[12:00.00 --> 12:30.00] 中间段",
                "[24:50.00 --> 24:58.98] 最后一段",
                f"正在写入：音频{index}.lrc",
            ]
            for line in lines:
                event = progress.feed(line)
                if event:
                    values.append(event[0])
                    statuses.append(event[1])

        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 99)
        self.assertIn("识别 1/6 · 12:30/24:58", statuses)
        self.assertIn("语音检测 2/6", statuses)
        self.assertIn("完成 6/6", statuses)
        self.assertFalse(show_in_gui("[12:00.00 --> 12:30.00] 中间段"))
        self.assertFalse(show_in_gui("VAD进度：25/50 块（50.0%）"))
        self.assertTrue(show_in_gui("正在写入：音频1.lrc"))

    def test_unknown_duration_does_not_jump_to_complete(self):
        progress = WhisperProgress(1)
        progress.feed("正在翻译（1/1）：音频.wav")
        event = progress.feed("VAD进度：10/10 块（100.0%）")
        self.assertEqual(event[0], 5)
        self.assertIsNone(progress.feed("[00:01.00 --> 00:02.00] 一句"))
        self.assertEqual(progress.feed("正在写入：音频.lrc")[0], 99)


if __name__ == "__main__":
    unittest.main()
