#!/usr/bin/env python3
"""
repeat_noun_density_scanner.py — 段窗口内事物名词高密度重复检测

检测「同事物名词在 5 段窗口内 ≥4 次重复」的 AI 模板节奏：
扫「那 + [量词?] + 名词」「这 + 量词 + 名词」类指示性名词组合
窗口 5 段内同 token ≥4 次 → 触发 advisory

输出：JSON {scanner, ch, violations:[{window_start, token, count, evidence_paras}], total, max_density}

用法：
  python repeat_noun_density_scanner.py <章节文件路径>
"""
from __future__ import annotations
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# 本 scanner 用「5 段滑动窗口内同 token ≥4 次」判定，判定单元是固定大小的段窗口
# （window_size=5），与整篇文本体量无关：cluster 草稿（12-25k CJK）和 chapter
# （3-4k CJK）共用同一套窗口扫描，长文本只是窗口数量更多，每个窗口的密度判定阈值
# 本身不该随体量浮动（否则放宽 = 漏掉同样密集的重复堆叠）。故本 scanner 不需要
# 区分 cluster/chapter 视野。


# 「那/这 + [数词?][量词?] + 名词(1-3字)」指示性名词组合提取正则。
# 用正则提取候选名词 token（而非硬编码词表）：固定白名单只能覆盖表里的词，覆盖不到
# 白名单外的高频重复名词（那把剑 / 那道光 / 作者特有名词）。
#   group(1) = 指示词（那/这），group(2) = 紧跟的名词 token
# 名词槽限 1-3 个 CJK 字，避免贪婪吃过整句；候选名词以「指示词+名词」组合形态计密度，
# 既抓 AI 模板节奏（指示性名词反复堆叠），又不会把裸名词在无关语境里误计。
_DEMONSTRATIVE_RE = re.compile(
    r'(那|这)[一两二三]?[只个种道把座条根块张段次场]?([一-鿿]{1,3})'
)


def extract_tokens(para: str) -> set:
    """提取本段所有指示性名词候选 token（正则提取 group(2) 名词部分）。

    返回名词 token 集合（如「剑」「光」「声音」），后续按窗口统计该名词跟在
    指示词后的出现密度。"""
    return {m.group(2) for m in _DEMONSTRATIVE_RE.finditer(para)}


def _count_noun_phrase(para: str, noun: str) -> int:
    """统计本段内「指示词(+数词?+量词?) + <noun>」组合出现次数。"""
    return sum(1 for m in _DEMONSTRATIVE_RE.finditer(para) if m.group(2) == noun)


def count_token_in_window(paras: list, start: int, end: int, token: str) -> int:
    """统计名词 token（以指示性名词组合形态）在 paras[start:end] 中出现次数。"""
    return sum(_count_noun_phrase(p, token) for p in paras[start:end])


def scan_chapter(text: str, window_size: int = 5, threshold: int = 4) -> dict:
    paras = [p for p in text.split('\n') if p.strip()]
    violations = []
    seen_windows = set()

    for i in range(len(paras) - window_size + 1):
        window = paras[i:i + window_size]
        # 提取这窗口所有 token + 全文匹配该 token
        candidate_tokens = set()
        for p in window:
            candidate_tokens |= extract_tokens(p)

        for token in candidate_tokens:
            cnt = count_token_in_window(paras, i, i + window_size, token)
            if cnt >= threshold:
                key = (i, token)
                # 避免重复报告：同 token 在 overlapping 窗口报多次
                neighbor_key = any((i - k <= 2 and t == token) for (k, t) in seen_windows)
                if not neighbor_key:
                    violations.append({
                        'window_start_para': i + 1,
                        'window_size': window_size,
                        'token': token,
                        'count_in_window': cnt,
                        'severity': 'major' if cnt >= 6 else 'minor',
                        'evidence_paras': [p[:60] for p in window[:3]]
                    })
                seen_windows.add((i, token))

    return {
        'scanner': 'repeat_noun_density',
        'total_paras': len(paras),
        'window_size': window_size,
        'threshold': threshold,
        'violations_count': len(violations),
        'violations': violations,
        'verdict': 'PASS' if not violations else ('FAIL_MAJOR' if any(v['severity'] == 'major' for v in violations) else 'FAIL_MINOR'),
        'gate_level': 'advisory',
        '_doc': 'AI 模板节奏第二探针：同事物名词高密度重复；feedback_voice_craft_applies_to_boundary lesson 配套'
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python repeat_noun_density_scanner.py <章节路径>")
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"路径不存在: {path}")
        sys.exit(2)
    text = path.read_text(encoding='utf-8')
    result = scan_chapter(text)
    result['file'] = str(path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
