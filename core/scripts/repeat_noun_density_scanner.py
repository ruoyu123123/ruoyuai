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

# v2 cluster 化（2026-05-28）：CLUSTER_MODE env 感知 · scanner 内部可按 mode 切阈值
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"


# 已知高频「那/这 + 量词? + 名词」模板组合白名单
# 这些是 AI 写作典型的指示性名词，重复出现 = 模板节奏信号
KNOWN_DEMONSTRATIVE_NOUNS = [
    # 那 + 量词 + 名词
    '那只手', '那个手', '那双手',
    '那个声音', '那种声音', '那阵声音',
    '那种热', '那种冷', '那种感觉', '那种东西', '那种味道',
    '那个地方', '那一处', '那条路', '那条道', '那块地',
    '那根木头', '那截木头', '那块木头', '那段木头',
    '那只兽', '那只鸟', '那个孩子', '那个人', '那个女人', '那个男人',
    '那张兽皮', '那块兽皮', '那张皮', '那块皮',
    '那个梦', '那场梦', '那次梦',
    '那东西', '那玩意', '那物件',
    '那一夜', '那一天', '那一刻', '那一次',
    '那一身', '那一截',
    # 这 + 量词 + 名词
    '这只手', '这个声音', '这种感觉', '这个东西', '这个地方',
    '这种热', '这场梦', '这块兽皮', '这根木头',
]


def extract_tokens(para: str) -> set:
    """提取本段所有指示性名词 token（白名单匹配，避免贪婪吃边界）"""
    tokens = set()
    for tok in KNOWN_DEMONSTRATIVE_NOUNS:
        if tok in para:
            tokens.add(tok)
    return tokens


def count_token_in_window(paras: list, start: int, end: int, token: str) -> int:
    """统计 token 在 paras[start:end] 中出现次数"""
    return sum(p.count(token) for p in paras[start:end])


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
