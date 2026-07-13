#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
romance_pacing_scanner.py — 甜宠/言情题材专属节奏检测（cluster 视野 · advisory · 阶段3）

题材层(内容工艺)·仅在 genre=romance 时激活。甜宠核心：情感节拍 + 关系张力推进。
调研(Romancing the Beat / Quick Quick Slow / 12级触碰)：言情节奏=拉伸时间·对话驱动·
情绪标点密·动作后写感受。与爽文的「压缩时间/打脸爽点」根本不同。

探针（全 advisory·宽松·暂无金标准样本故只抓「明显不像言情」）：
  1. dialogue_too_sparse  对话占比过低(言情应对话驱动·爽文叙述驱动)
  2. emotion_punct_sparse 情绪标点(？…)密度过低(言情靠羞涩矛盾的省略号/心理停顿)
  3. sensory_flat         五感/感受词密度过低(情感流要慢镜头铺五官神态)

⚠️ 暂无甜宠金标准样本校准·阈值保守(宁可漏报不误伤)·待真甜宠书产出后校准。
用法：python romance_pacing_scanner.py <cluster草稿> [--project <root>] [--style <作者风格.json>]
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

from atomic_json import load_json  # 读侧单一真理源

SENSORY_WORDS = ['看', '听', '闻', '触', '感', '暖', '凉', '软', '香', '颤', '心跳',
                 '脸红', '耳根', '指尖', '气息', '眼神', '温度', '怀里', '掌心']
DIALOGUE_FLOOR = 0.18     # 言情对话占比下限(保守·真言情常 40-60%)
EMOTION_PUNCT_FLOOR = 2.0  # 情绪标点(？…)每千字下限
SENSORY_FLOOR = 3.0       # 五感词每千字下限


from text_metrics import count_cjk as cjk  # noqa: E402 字数口径单一真理源


def _load_json(p: Path):
    return load_json(p)


def _author_dialogue_floor(project: Path | None, style_path: Path | None) -> float | None:
    """作者风格档对话占比基线=第一权威(北极星⑤)。返回作者真实对话占比(0-1)或 None。

    对齐 prose_rhythm_scanner._author_baseline 范式：优先读 --style 指定档，
    否则回退项目 _数据库/作者风格.json / 作者风格_FINAL.json；
    取 quantitative.dialogue_ratio.mean（consolidate_author_profile 确定性写入·0-1 分数）。
    """
    data = None
    if style_path and style_path.exists():
        data = _load_json(style_path)
    if data is None and project:
        for cand in (project / "_数据库" / "作者风格.json",
                     project / "_数据库" / "作者风格_FINAL.json"):
            if cand.exists():
                data = _load_json(cand)
                if data:
                    break
    if isinstance(data, dict):
        q = data.get("quantitative") or {}
        dr = q.get("dialogue_ratio") or {}
        if isinstance(dr, dict) and isinstance(dr.get("mean"), (int, float)):
            return float(dr["mean"])
    return None


def scan(text: str, project: Path | None = None, style_path: Path | None = None) -> dict:
    paras = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()
             and not re.match(r'^第[\d一二三四五六七八九十百千]+章', p) and not p.startswith('【')]
    total = cjk(text)
    if not paras or total < 500:
        return {"scanner": "romance_pacing", "violations_count": 0, "violations": [],
                "verdict": "PASS", "gate_level": "advisory", "metrics": {}, "_doc": "文本过短"}
    dia_cjk = sum(cjk(p) for p in paras if p.lstrip()[:1] in '“"「『' or '“' in p)
    dia_ratio = round(dia_cjk / total, 3)
    ques = text.count('？') + text.count('?')
    ell = text.count('…') + text.count('...')
    emo_punct = round((ques + ell) / total * 1000, 2)
    sensory = round(sum(text.count(w) for w in SENSORY_WORDS) / total * 1000, 2)

    _author_dia = _author_dialogue_floor(project, style_path)
    # 作者真实对话占比偏低(如叙述驱动的文学言情)时按其基线放宽·绝不高于通用保守 floor(只减误报·北极星⑤)
    dia_floor = DIALOGUE_FLOOR if _author_dia is None else min(DIALOGUE_FLOOR, _author_dia * 0.85)

    violations = []
    if dia_ratio < dia_floor:
        violations.append({'kind': 'dialogue_too_sparse', 'severity': 'minor',
                           'dialogue_ratio': dia_ratio, 'floor': round(dia_floor, 3),
                           'hint': f'对话占比 {dia_ratio:.0%}<{dia_floor:.0%}=叙述太多·言情应对话驱动'
                                   f'(撩点/双向心动靠对话交锋)·提对话比'})
    if emo_punct < EMOTION_PUNCT_FLOOR:
        violations.append({'kind': 'emotion_punct_sparse', 'severity': 'minor',
                           'emotion_punct_per_1k': emo_punct, 'floor': EMOTION_PUNCT_FLOOR,
                           'hint': f'情绪标点(？…) {emo_punct}/千字偏低·言情靠羞涩矛盾的省略号+心理停顿承载暧昧'})
    if sensory < SENSORY_FLOOR:
        violations.append({'kind': 'sensory_flat', 'severity': 'minor',
                           'sensory_per_1k': sensory, 'floor': SENSORY_FLOOR,
                           'hint': f'五感/感受词 {sensory}/千字偏低·情感流要慢镜头铺五官神态光影·动作后写感受'})

    verdict = 'PASS' if not violations else 'FAIL_MINOR'
    return {
        'scanner': 'romance_pacing', 'violations_count': len(violations),
        'violations': violations, 'verdict': verdict, 'gate_level': 'advisory',
        'metrics': {'dialogue_ratio': dia_ratio, 'emotion_punct_per_1k': emo_punct,
                    'sensory_per_1k': sensory, 'dialogue_floor_effective': round(dia_floor, 3)},
        '_doc': '甜宠/言情题材节奏·对话驱动+情绪标点+五感铺陈·advisory·阈值保守待金标准校准',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("--project", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    p = Path(args.draft)
    if not p.exists():
        print(f"路径不存在: {p}")
        sys.exit(2)
    r = scan(p.read_text(encoding='utf-8'),
             project=Path(args.project) if args.project else None,
             style_path=Path(args.style) if args.style else None)
    r['file'] = str(p)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
