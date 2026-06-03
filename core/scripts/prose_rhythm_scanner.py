#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prose_rhythm_scanner.py — 句法节奏 / 「流水账作文感」检测（cluster 视野 · 作者基线感知 · advisory）

2026-06-03 新增。根因（《无脸者守则》实证 + 惊悚乐园真作者金标准对比）：
  用户直觉「不像网文像作文·谁做什么做什么做什么」= 主语+裸动作 play-by-play 流水账。
  量化：本书主语开头句 27.8% vs 真作者 15.1%；均句长 16.8 vs 33.0；最长「主语+动作」streak
  3.8(峰值6) vs 2.1。根因三层——蒸馏✅对(作者档 sentence_length.mean=31)，writer 注入了但
  弱模型守不住 + C3 只管段首管不到句子级流水账，**scanner 层 validate_style 故意「绝不查句长」
  → 句长偏短全程无人报警**。本 scanner 补这个检测闭环。

三探针（全 advisory · 以作者风格档基线为第一权威 · 北极星⑤）：
  1. sentence_too_short  cluster 均句长 < 作者 sentence_length.mean × 0.70（无作者档退通用 18）
  2. subject_action_streak  连续句首是「主语(人物卡角色名/代词)」的流水账 streak ≥4 minor/≥6 major
  3. subject_start_ratio_high  主语开头叙述句占比 > max(作者基线×1.6, 24%)

不做「单句独行率上限」探针：cluster_001(优秀样本)独行率最高(77-84%)但质量好(吐槽短句)，
独行率高本身不是问题(爽文要独行)，问题是独行的是不是裸动作——靠句长+streak 精准抓，独行率会误伤。

输出：JSON {scanner, violations:[{kind,severity,...}], verdict, gate_level, metrics, author_baseline}
用法：python prose_rhythm_scanner.py <cluster草稿> [--project <root>] [--style <作者风格.json>]
"""
from __future__ import annotations
import argparse
import json
import re
import statistics
import sys
from pathlib import Path

PRONOUNS = ['他们', '她们', '它们', '两人', '二人', '众人', '他', '她', '它']
# 句长偏短双档系数（相对作者基线 mean）
SHORT_MINOR, SHORT_MAJOR = 0.70, 0.55
STREAK_MINOR, STREAK_MAJOR = 4, 6
DEFAULT_AUTHOR_SENT_MEAN = 26.0   # 无作者档时的通用兜底句长基线（偏保守·网文中位）
DEFAULT_SUBJ_PCT_CAP = 24.0       # 主语开头占比通用上限(%)


def cjk(s: str) -> int:
    return sum(1 for c in s if '一' <= c <= '鿿')


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _author_baseline(project: Path | None, style_path: Path | None) -> dict:
    """从作者风格档读句法基线：sentence_length.mean + 主语占比(若有)。北极星⑤第一权威。"""
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
    sent_mean = None
    if isinstance(data, dict):
        q = data.get("quantitative") or {}
        sl = q.get("sentence_length") or {}
        if isinstance(sl, dict) and isinstance(sl.get("mean"), (int, float)):
            sent_mean = float(sl["mean"])
    return {"sentence_mean": sent_mean}


def _char_names(project: Path | None) -> list:
    """读人物卡 characters[].name/id 作主语名集（抓「池迟做X」流水账·非仅代词）。"""
    if not project:
        return []
    pc = _load_json(project / "_数据库" / "人物卡.json")
    names = []
    if isinstance(pc, dict):
        for c in pc.get("characters", []) or []:
            if isinstance(c, dict):
                for k in ("name", "id"):
                    v = c.get(k)
                    if isinstance(v, str) and cjk(v) >= 2:
                        names.append(v)
    return names


def scan(text: str, project: Path | None = None, style_path: Path | None = None) -> dict:
    baseline = _author_baseline(project, style_path)
    sent_mean_base = baseline["sentence_mean"] or DEFAULT_AUTHOR_SENT_MEAN
    subj_words = _char_names(project) + PRONOUNS

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    body = [l for l in lines if not re.match(r'^第\d+章', l) and not l.startswith('【')]

    # 全文句长（含对话·对齐作者 sentence_length.mean 口径）
    all_lens = []
    # 叙述句（排除对话主导句）用于主语流水账判定
    narr_is_subj = []
    for p in body:
        for s in re.split(r'(?<=[。！？…])', p):
            s = s.strip()
            if cjk(s) < 2:
                continue
            all_lens.append(cjk(s))
            head_raw = s.lstrip('　 ')
            if head_raw[:1] in '""\'「『（(':   # 对话主导句不计主语流水账
                continue
            head = head_raw.lstrip('""\'「『（(')
            narr_is_subj.append(any(head.startswith(w) for w in subj_words))

    if not all_lens:
        return {"scanner": "prose_rhythm", "violations": [], "verdict": "PASS",
                "gate_level": "advisory", "metrics": {}, "_doc": "空文本"}

    mean_len = round(statistics.mean(all_lens), 1)
    narr_n = len(narr_is_subj)
    subj_n = sum(1 for x in narr_is_subj if x)
    subj_pct = round(subj_n / narr_n * 100, 1) if narr_n else 0.0
    # 最长连续主语开头 streak
    streak = mx = 0
    for x in narr_is_subj:
        streak = streak + 1 if x else 0
        mx = max(mx, streak)

    violations = []

    # 探针 1：句长偏短（vs 作者基线）
    ratio = mean_len / sent_mean_base if sent_mean_base else 1.0
    if ratio < SHORT_MINOR:
        sev = 'major' if ratio < SHORT_MAJOR else 'minor'
        violations.append({
            'kind': 'sentence_too_short', 'severity': sev,
            'cluster_mean': mean_len, 'author_mean': round(sent_mean_base, 1),
            'ratio': round(ratio, 2),
            'hint': f'均句长 {mean_len} 仅作者基线 {round(sent_mean_base,1)} 的 {round(ratio*100)}%→碎句流水账/作文感；'
                    f'多用逗号连缀的复合长句承载信息(因果/让步/比喻)，长短句交替而非匀速短句',
        })

    # 探针 2：主语+动作流水账 streak
    if mx >= STREAK_MINOR:
        sev = 'major' if mx >= STREAK_MAJOR else 'minor'
        violations.append({
            'kind': 'subject_action_streak', 'severity': sev,
            'max_streak': mx, 'threshold': STREAK_MAJOR if sev == 'major' else STREAK_MINOR,
            'hint': f'连续 {mx} 句以「主语(人名/他/她)」开头=「谁做什么做什么」流水账；'
                    f'第3句起换起头(环境/动作中段/对话/心理/感官/省主语)，或把动作融进对话与评论',
        })

    # 探针 3：主语开头占比偏高（vs 作者基线·真作者约 15%）
    subj_cap = max(DEFAULT_SUBJ_PCT_CAP, 0.0)  # 作者档暂无主语占比基线→用通用上限 24%
    if subj_pct > subj_cap:
        sev = 'major' if subj_pct > subj_cap + 12 else 'minor'
        violations.append({
            'kind': 'subject_start_ratio_high', 'severity': sev,
            'subj_start_pct': subj_pct, 'cap': subj_cap,
            'hint': f'主语开头叙述句占比 {subj_pct}%(网文金标准约15%·阈值{subj_cap}%)→句首单一像作文；'
                    f'句首多样化:环境/状语/对话/感官/说书人评论起头',
        })

    has_major = any(v['severity'] == 'major' for v in violations)
    verdict = 'PASS' if not violations else ('FAIL_MAJOR' if has_major else 'FAIL_MINOR')
    return {
        'scanner': 'prose_rhythm',
        'violations_count': len(violations),
        'violations': violations,
        'verdict': verdict,
        'gate_level': 'advisory',
        'metrics': {
            'sentence_mean': mean_len,
            'subject_start_pct': subj_pct,
            'max_subject_streak': mx,
            'narrative_sentences': narr_n,
        },
        'author_baseline': {'sentence_mean': sent_mean_base,
                            'from_author_profile': baseline["sentence_mean"] is not None},
        '_doc': '句法节奏/流水账作文感检测·作者基线第一权威·advisory·补 validate_style「不查句长」缺口',
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
    result = scan(p.read_text(encoding='utf-8'),
                  project=Path(args.project) if args.project else None,
                  style_path=Path(args.style) if args.style else None)
    result['file'] = str(p)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
