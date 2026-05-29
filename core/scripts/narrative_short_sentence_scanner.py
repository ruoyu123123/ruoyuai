#!/usr/bin/env python3
"""
narrative_short_sentence_scanner.py — 叙述段短句堆叠检测

检测「叙述段（非对话非笔注）句号当逗号用」的 AI 模板节奏：
段长 < 80 CJK 字 且 句号 ≥3 且 逗号 ≤1 → 触发 advisory

排除：
- 对话段（含「」引号包裹的内容占段 ≥50%）
- 章末笔注段（前 1-3 段含 ZF-N 或 「样本编号」等档案标记后的所有段）
- 极短引子段（单句对话/动作描述）

输出：JSON {scanner, ch, violations:[{line,severity,evidence,period,comma,cjk}], total, density}

用法：
  python narrative_short_sentence_scanner.py <章节文件路径>
  python narrative_short_sentence_scanner.py <项目路径> --ch <N>
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

# v2 cluster 化（2026-05-29 复核）：本 scanner 的判定单元是「单个叙述段」——
# 触发阈值（段长 < 80 CJK 且 句号 ≥3 且 逗号 ≤1）只看单段内部结构，
# 与整篇文本体量（chapter 3-4k vs cluster 12-25k CJK）无关：cluster 草稿里的
# 每个段落和 chapter 里的段落是同一批物理段，逐段判定完全一致。
# violation_density 用 narrative_paras_total 归一，长文本天然不会因段多而误升。
# 故 cluster 化对本 scanner 阈值无意义 → 不引入 IS_CLUSTER_MODE 分支（删除死变量 + 误导注释）。



def is_dialogue_para(para: str) -> bool:
    """对话段：「」引号包裹内容占段 ≥50%"""
    quote_chars = sum(1 for c in para if c in '「」"""')
    if quote_chars < 2:
        return False
    # 提取「」内字符
    inside = re.findall(r'「([^」]*)」', para) + re.findall(r'"([^"]*)"', para) + re.findall(r'"([^"]*)"', para)
    inside_cjk = sum(len(re.findall(r'[一-鿿]', s)) for s in inside)
    total_cjk = len(re.findall(r'[一-鿿]', para))
    if total_cjk == 0:
        return False
    return (inside_cjk / total_cjk) >= 0.5


def detect_annotation_zone(paras: list) -> set:
    """检测章末笔注段范围"""
    annotation_markers = ['ZF-', '样本编号', '观测员档案', '【档案', '档案标号', 'ZF档案', '——ZF']
    annotation_start = None
    for i, p in enumerate(paras):
        if any(m in p for m in annotation_markers):
            annotation_start = i
            break
    if annotation_start is None:
        return set()
    return set(range(annotation_start, len(paras)))


def scan_chapter(text: str) -> dict:
    paras = text.split('\n')
    annotation_zone = detect_annotation_zone(paras)
    violations = []
    narrative_paras = 0

    for i, para in enumerate(paras):
        para = para.strip()
        if not para:
            continue
        if i in annotation_zone:
            continue
        if is_dialogue_para(para):
            continue
        # 跳过极短段（< 15 字）
        cjk = len(re.findall(r'[一-鿿]', para))
        if cjk < 15:
            continue
        narrative_paras += 1

        period = para.count('。')
        comma = para.count('，')

        # 触发条件：段长 < 80 字 且 句号 ≥3 且 逗号 ≤1
        if cjk < 80 and period >= 3 and comma <= 1:
            severity = 'major' if period >= 5 else 'minor'
            violations.append({
                'line': i + 1,
                'severity': severity,
                'evidence': para[:80],
                'period': period,
                'comma': comma,
                'cjk': cjk,
                'period_per_10cjk': round(period * 10 / cjk, 2)
            })

    return {
        'scanner': 'narrative_short_sentence_overuse',
        'narrative_paras_total': narrative_paras,
        'annotation_paras': len(annotation_zone),
        'violations_count': len(violations),
        'violation_density': round(len(violations) / max(narrative_paras, 1), 3),
        'violations': violations,
        'verdict': 'PASS' if not violations else ('FAIL_MAJOR' if any(v['severity'] == 'major' for v in violations) else 'FAIL_MINOR'),
        'gate_level': 'advisory',  # 默认 advisory（voice 工艺豁免后仍要看，不直接 hard_gate）
        '_doc': 'voice 工艺被错套到叙述段的探针；feedback_voice_craft_applies_to_boundary lesson 配套'
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python narrative_short_sentence_scanner.py <章节路径>")
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"路径不存在: {path}")
        sys.exit(2)
    text = path.read_text(encoding='utf-8')
    result = scan_chapter(text)
    result['file'] = str(path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # exit code: 0 PASS, 1 violations exist
    sys.exit(0 if result['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
