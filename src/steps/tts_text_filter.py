# -*- coding: utf-8 -*-
"""配音前的纯数字字幕过滤。

规则刻意保持最小：只有**整条字幕去掉首尾标点/空白后只剩数字**时才跳过，
句子里的数字（如“第3集”“2024年”“A4纸”）一个字都不动。
"""
import re

# 中文数字（含常用大写/异体）与阿拉伯数字（含全角）
_CN_DIGITS = "零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟"
_AR_DIGITS = "0123456789０１２３４５６７８９"
# 纯数字表达式中允许出现的连接符/单位符号
_NUMERIC_SEPARATORS = ".,，．、:：;；/／\\-–—~～+＋%％"
# 首尾可能包裹数字的标点（中英文括号、引号、书名号等）
_EDGE_PUNCT = " \t\r\n　。！？!?，,、；;：:…·.~～-—–_=+%％()（）[]【】{}<>《》〈〉“”‘’\"'`"
_NUMBER_CHARS = set(_CN_DIGITS + _AR_DIGITS)
_CN_DIGIT_CHARS = set(_CN_DIGITS)
_ALLOWED_CHARS = _NUMBER_CHARS | set(_NUMERIC_SEPARATORS)

# 至少一个真正的数字字符；避免“...”或纯分隔符被误判
_HAS_DIGIT = re.compile(f"[{_AR_DIGITS}{_CN_DIGITS}]")


def is_pure_number_line(text: str) -> bool:
    """整条字幕是否只由数字/数字表达式组成。

    例：``七``、``十七``、``2024``、``3.5``、``50%``、``（七）`` → True；
    ``第3集``、``2024年``、``3秒``、``一起``、``A4纸`` → False。
    """
    if text is None:
        return False
    stripped = str(text).strip().strip(_EDGE_PUNCT)
    if not stripped or not _HAS_DIGIT.search(stripped):
        return False
    # 去掉内部空白（“七 六”这类倒计时写法也按纯数字处理）
    compact = re.sub(r"\s+", "", stripped)
    if not all(ch in _ALLOWED_CHARS for ch in compact):
        return False
    # 中文数字只认“单字成行”（七/六/五…）。多字组合如“万一”“十七”
    # 极易是正常词或非倒计时数字，宁可保留也不误删。
    if all(ch in _CN_DIGIT_CHARS for ch in compact) and len(compact) != 1:
        return False
    return True
