# -*- coding: utf-8 -*-
"""纯数字字幕过滤规则测试。"""
import unittest

from src.steps.tts_text_filter import is_pure_number_line


class PureNumberLineTests(unittest.TestCase):
    def test_standalone_chinese_countdown_is_filtered(self):
        for text in ("七", "六", "五", "四", "三", "二", "一", "零"):
            with self.subTest(text=text):
                self.assertTrue(is_pure_number_line(text))

    def test_arabic_and_mixed_numeric_expressions_are_filtered(self):
        for text in ("2024", "3.5", "50%", "1/2", "3-5", "3万", "１００"):
            with self.subTest(text=text):
                self.assertTrue(is_pure_number_line(text))

    def test_edge_punctuation_does_not_hide_the_number(self):
        for text in ("七。", "（七）", " 七 ", "七、", "“七”"):
            with self.subTest(text=text):
                self.assertTrue(is_pure_number_line(text))

    def test_numbers_inside_sentences_are_never_touched(self):
        for text in ("第3集", "共3个", "2024年", "3秒", "A4纸", "MP3",
                     "电话123", "一起", "万一", "十分高兴", "三明治",
                     "二次元", "七上八下", "一方面", "十七", "三百"):
            with self.subTest(text=text):
                self.assertFalse(is_pure_number_line(text))

    def test_empty_or_punctuation_only_is_not_filtered(self):
        for text in ("", "   ", "...", "。", "——"):
            with self.subTest(text=text):
                self.assertFalse(is_pure_number_line(text))


if __name__ == "__main__":
    unittest.main()
