#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rhetoric_repetition_scanner.py — 修辞复读 / AI 长文退化三连指纹检测（cluster 视野 · advisory）

2026-06-03 新增。根因：gen_writer 元 anti-slop 把「否定对照 ≤20 / 比喻同喻体 ≤4 /
破折号 ≤8千字」写进 prompt，但弱模型（gen-model）freestyle 整块写时守不住
（《无脸者守则》cluster_002 实测：否定对照 4.4/千CJK、破折号 22/千CJK、「放凉的粥」
比喻 ×11、整段相似度 1.00 逐字复制）。光靠 prompt 喊话 ≠ 落地检测——本 scanner 在
cluster 草稿层做**客观检测闭环**兜底，advisory 反馈给 writer/fixer 修。

四探针（全 advisory · 作者风格档可豁免破折号/比喻偏好 · 顾问非法官）：
  1. neg_contrast_overuse  「不是X——是Y / 不是X，是Y」否定对照密度（享宇 AI 特征⑥修辞公式化）
  2. dash_overuse          破折号「——」密度（C4·惊悚乐园风格元素但过载=节奏单一化）
  3. simile_reuse          同一喻体跨场景复读（「像…鹅卵石/放凉的粥」AI 同质化指纹）
  4. paragraph_near_dup    整段近/全重复（difflib·AI 套路化描写段原样重出最硬指纹）

阈值用「每千 CJK 密度」（体量无关·对齐 repeat_noun_density 哲学），下限以
cluster_001（deepseek 认可的优秀样本）校准防矫枉过正：cluster_001 否定对照 2.1/kCJK、
破折号 12.5/kCJK 均 PASS；cluster_002 的 4.4 / 22 触发。

输出：JSON {scanner, violations:[{kind,severity,...}], verdict, gate_level, metrics}
用法：python rhetoric_repetition_scanner.py <cluster草稿或章节路径>
"""
from __future__ import annotations
import json
import re
import sys
import difflib
from collections import Counter
from pathlib import Path

# ── 阈值（每千 CJK 密度 · 双档 minor/major）────────────────────────────
NEG_CONTRAST_MINOR, NEG_CONTRAST_MAJOR = 2.8, 4.0       # 否定对照 /kCJK
DASH_MINOR, DASH_MAJOR = 15.0, 22.0                     # 破折号 —— /kCJK
SIMILE_REUSE_MINOR, SIMILE_REUSE_MAJOR = 5, 8           # 同喻体绝对次数
DUP_RATIO_MINOR, DUP_RATIO_MAJOR = 0.75, 0.85           # 段落相似度
DUP_MIN_CJK = 30                                        # 仅比 CJK≥此值的长段

# 「不是X——是Y」/「不是X，是Y」否定对照（破折号或逗号承接）
_NEG_CONTRAST = re.compile(r'不是[^。！？\n【】]{1,28}?(?:——|—|，|、)+\s*是')
# 比喻喻体提取：「像/仿佛/如同 + 喻体(1-14字) [+一样/似的/般]」
_SIMILE = re.compile(r'(?:像|仿佛|如同|宛如|好似)([^，。！？、\n]{2,14}?)(?:一样|似的|般|$|[，。！？、])')
# 喻体归一化：剥掉前缀量词/「一」等，便于同喻体归并
_VEHICLE_STRIP = re.compile(r'^(?:一[只个块条道张片把]?|那[只个块条道张片把]?|这[只个块条道张片把]?)')


from text_metrics import count_cjk as cjk  # noqa: E402 字数口径单一真理源


def _norm_vehicle(v: str) -> str:
    v = _VEHICLE_STRIP.sub('', v).strip()
    return v


def scan(text: str, author_dash_per_kcjk: float | None = None) -> dict:
    """author_dash_per_kcjk: 作者风格档破折号基线（/千CJK）。北极星⑤：作者几乎不用破折号
    (如惊悚乐园 0.3/千) → 收紧阈值到 8/14；作者爱用破折号则保持通用 15/22。"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    body = [l for l in lines if not re.match(r'^第\d+章\s', l)]
    joined = '\n'.join(body)
    total_cjk = sum(cjk(l) for l in body)
    kcjk = max(total_cjk / 1000.0, 0.001)
    # 破折号阈值：作者基线感知（作者 <2/千=不爱用 → 收紧；否则通用宽松）
    if author_dash_per_kcjk is not None and author_dash_per_kcjk < 2.0:
        dash_minor, dash_major = 8.0, 14.0
    else:
        dash_minor, dash_major = DASH_MINOR, DASH_MAJOR
    violations = []

    # 探针 1：否定对照密度
    neg_n = len(_NEG_CONTRAST.findall(joined))
    neg_d = round(neg_n / kcjk, 2)
    if neg_d >= NEG_CONTRAST_MINOR:
        sev = 'major' if neg_d >= NEG_CONTRAST_MAJOR else 'minor'
        sample = _NEG_CONTRAST.findall(joined)[:3]
        violations.append({
            'kind': 'neg_contrast_overuse', 'severity': sev,
            'count': neg_n, 'density_per_kcjk': neg_d,
            'threshold': NEG_CONTRAST_MAJOR if sev == 'major' else NEG_CONTRAST_MINOR,
            'hint': '「不是X——是Y」对照句式过载→从恐怖修辞退化成 AI 口头禅；换陈述/动作/反问',
            'evidence': sample,
        })

    # 探针 2：破折号密度
    dash_n = joined.count('——')
    dash_d = round(dash_n / kcjk, 2)
    if dash_d >= dash_minor:
        sev = 'major' if dash_d >= dash_major else 'minor'
        violations.append({
            'kind': 'dash_overuse', 'severity': sev,
            'count': dash_n, 'density_per_kcjk': dash_d,
            'threshold': dash_major if sev == 'major' else dash_minor,
            'hint': '破折号——过载→节奏单一化；改用句号断句/逗号/直接陈述（作者档偏好高频破折号可豁免）',
        })

    # 探针 3：比喻喻体复读
    vehicles = Counter(_norm_vehicle(m) for m in _SIMILE.findall(joined) if _norm_vehicle(m))
    for vehicle, n in vehicles.most_common():
        if n >= SIMILE_REUSE_MINOR:
            sev = 'major' if n >= SIMILE_REUSE_MAJOR else 'minor'
            violations.append({
                'kind': 'simile_reuse', 'severity': sev,
                'vehicle': vehicle, 'count': n,
                'threshold': SIMILE_REUSE_MAJOR if sev == 'major' else SIMILE_REUSE_MINOR,
                'hint': f'喻体「{vehicle}」全 cluster 复读 {n} 次→AI 同质化/塑料感；换不同喻体别复读同一个',
            })

    # 探针 4：整段近/全重复（长段两两 difflib · 长度差>25% 预筛跳过）
    longs = [p for p in body if cjk(p) >= DUP_MIN_CJK and not p.startswith('【')]
    dup_pairs = []
    seen = set()
    for i in range(len(longs)):
        for j in range(i + 1, len(longs)):
            a, b = longs[i], longs[j]
            la, lb = len(a), len(b)
            if min(la, lb) / max(la, lb) < 0.75:  # 长度差预筛
                continue
            r = difflib.SequenceMatcher(None, a, b).ratio()
            if r >= DUP_RATIO_MINOR:
                key = (a[:20], b[:20])
                if key in seen:
                    continue
                seen.add(key)
                dup_pairs.append((round(r, 2), a, b))
    for r, a, b in sorted(dup_pairs, key=lambda x: -x[0]):
        sev = 'major' if r >= DUP_RATIO_MAJOR else 'minor'
        violations.append({
            'kind': 'paragraph_near_dup', 'severity': sev,
            'ratio': r,
            'hint': '套路化描写段在 cluster 内原样重出→AI 整段复制指纹；重写其一换表达',
            'evidence': [a[:40] + '…', b[:40] + '…'],
        })

    has_major = any(v['severity'] == 'major' for v in violations)
    verdict = 'PASS' if not violations else ('FAIL_MAJOR' if has_major else 'FAIL_MINOR')
    return {
        'scanner': 'rhetoric_repetition',
        'total_cjk': total_cjk,
        'violations_count': len(violations),
        'violations': violations,
        'verdict': verdict,
        'gate_level': 'advisory',
        'metrics': {
            'neg_contrast_per_kcjk': neg_d if total_cjk else 0,
            'dash_per_kcjk': dash_d if total_cjk else 0,
            'top_similes': vehicles.most_common(5),
            'near_dup_pairs': len(dup_pairs),
        },
        '_doc': 'AI 长文退化三连指纹（修辞公式化/比喻复读/整段重复）· advisory · 兜底 gen_writer 元anti-slop',
    }


def _read_author_dash(style_path: str | None, project: str | None) -> float | None:
    """从作者风格档读破折号基线（/千CJK）：punctuation_per_1k.dash 或 punctuation_density_per_1000.dash。"""
    import json as _json
    cands = []
    if style_path:
        cands.append(Path(style_path))
    if project:
        cands += [Path(project) / "_数据库" / "作者风格.json",
                  Path(project) / "_数据库" / "作者风格_FINAL.json"]
    for c in cands:
        if c and c.exists():
            try:
                q = (_json.loads(c.read_text(encoding='utf-8')) or {}).get("quantitative") or {}
            except Exception:
                continue
            for key in ("punctuation_per_1k", "punctuation_density_per_1000"):
                d = q.get(key) or {}
                v = d.get("dash")
                if isinstance(v, dict):
                    v = v.get("mean")
                if isinstance(v, (int, float)):
                    return float(v)
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("--style", default=None)
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    path = Path(args.draft)
    if not path.exists():
        print(f"路径不存在: {path}")
        sys.exit(2)
    author_dash = _read_author_dash(args.style, args.project)
    result = scan(path.read_text(encoding='utf-8'), author_dash_per_kcjk=author_dash)
    result['author_dash_baseline'] = author_dash
    result['file'] = str(path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
