#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""text_metrics.py — 文本度量单一真理源（零重依赖·仅 re）。

全仓 CJK 字数统计的唯一实现。此前 150+ scanner 各自内联 BMP-only
`sum(1 for ch in text if "一" <= ch <= "鿿")`，与 chapter_io 的 BMP+扩展A区口径
（`[一-鿿㐀-䶿]`）分叉——本模块收敛为单一实现（北极星⑥保持单一实现）。

刻意零重依赖（只 import re）：可被任意独立 scanner 直接 import 而不引入额外依赖面。
"""
from __future__ import annotations

import re

# CJK Unified Ideographs (U+4E00–U+9FFF·BMP) + 扩展A区 (U+3400–U+4DBF)。
# 不含标点/空白/数字/字母/全角符号。全仓字数口径单一真理源。
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")


def count_cjk(text: str) -> int:
    """纯中日韩文字数（CJK Unified + 扩展A区·不含标点/空白/数字/字母）。"""
    if not text:
        return 0
    return len(_CJK_RE.findall(text))
