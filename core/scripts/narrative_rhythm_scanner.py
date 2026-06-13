#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
narrative_rhythm_scanner.py — 叙事节奏序列检测（cluster 视野 · 作者基线感知 · advisory）

阶段1（蒸馏画骨）。补 prose_rhythm_scanner（查句长「皮」）抓不到的**序列骨**：
「写了这一拍之后写下一拍」的节奏——张力怎么起伏、爽点后是不是秒收、推进有没有缓冲。

学界根因（Spoiler Alert arxiv 2604.09854 实证）：LLM 写作通病是**过早收束**——张力中段
就塌、后段几乎可预测；专业写作张力撑到结尾（峰后保持率 专业 52% vs LLM 23%，「一爽就泄」）。
段长/句长这种逐句静态属性完全看不到，必须看**全篇张力轨迹形态**。

三探针（全 advisory · 作者风格档 narrative_rhythm 基线第一权威 · 北极星⑤）：
  1. premature_resolution  后段张力 / 峰值 < 作者基线×0.6（无基线退 0.25）→ 过早收束/一爽就泄
  2. tension_flatline      张力轨迹变异系数过低 → 匀速平铺无起伏（机器腔）
  3. beat_monotony         连续「推进」段（动作/短句/危机）无「缓冲」段 streak 过长 → 一路平推不喘息

张力代理（genre 中性·阶段3 题材包可细化）：每窗情绪标点密度(！？…) + 短句爆发 + 强度词。

输出：JSON {scanner, violations, verdict, gate_level, metrics, author_baseline}
用法：python narrative_rhythm_scanner.py <cluster草稿> [--project <root>] [--style <作者风格.json>]
"""
from __future__ import annotations
import argparse
import json
import re
import statistics
import sys
from pathlib import Path

# 强度词（危机/冲突/动作·张力代理·genre 中性兜底）
INTENSITY_WORDS = ['血', '死', '杀', '痛', '碎', '裂', '断', '崩', '吼', '喊', '扑',
                   '撞', '颤', '抖', '惊', '怒', '泪', '骨', '刺', '爆', '冲', '逼']
N_WINDOWS = 8
FLATLINE_CV = 0.18          # 变异系数下限（低于=匀速平铺·calibrated 真作者通过）
RETENTION_DEFAULT = 0.25    # 无作者基线时后段保持度下限
# 连续推进段 streak 双档（calibrated：真作者《人生长恨》诛仙台酷刑场景天然 6 连推进
# 必须通过·只抓真正 8+ 的一路平推不喘息）
MONO_MINOR, MONO_MAJOR = 8, 12


def cjk(s: str) -> int:
    return sum(1 for c in s if '一' <= c <= '鿿')


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _author_rhythm_baseline(project: Path | None, style_path: Path | None) -> dict:
    """读作者 narrative_rhythm.tension_trajectory.post_climax_retention（第一权威）。"""
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
    ret = None
    if isinstance(data, dict):
        tt = (data.get("narrative_rhythm") or {}).get("tension_trajectory") or {}
        if isinstance(tt.get("post_climax_retention"), (int, float)):
            ret = float(tt["post_climax_retention"])
    return {"post_climax_retention": ret}


def _paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    return [p for p in paras
            if not re.match(r'^第[\d一二三四五六七八九十百千]+章', p) and not p.startswith('【')]


def _window_tension(seg: str) -> float:
    """一窗张力代理：情绪标点 + 短句爆发 + 强度词，按 CJK 归一（每千字）。"""
    n = cjk(seg) or 1
    excl = seg.count('！') + seg.count('!')
    ques = seg.count('？') + seg.count('?')
    ell = seg.count('…') + seg.count('...')
    inten = sum(seg.count(w) for w in INTENSITY_WORDS)
    # 短句爆发：句末标点切句·短句(≤8字)占比
    sents = [s for s in re.split(r'(?<=[。！？…])', seg) if cjk(s) >= 1]
    short_ratio = (sum(1 for s in sents if cjk(s) <= 8) / len(sents)) if sents else 0
    return (excl * 1.0 + ques * 0.8 + ell * 0.5 + inten * 0.6) / n * 1000 + short_ratio * 2


def _beat_class(p: str) -> str:
    """段落粗分类：推进(动作/短句/强度) vs 缓冲(对话主导/长心理/反思)。"""
    head = p.lstrip('　 ')
    if head[:1] in '“"\'「『':
        return '缓冲'   # 对话独行段=缓冲(交锋/喘息)
    n = cjk(p) or 1
    inten = sum(p.count(w) for w in INTENSITY_WORDS)
    sents = [s for s in re.split(r'(?<=[。！？…])', p) if cjk(s) >= 1]
    short_ratio = (sum(1 for s in sents if cjk(s) <= 10) / len(sents)) if sents else 0
    # 强度高或短句密=推进；否则缓冲(铺陈/心理/对话)
    if inten / n * 1000 >= 8 or short_ratio >= 0.6:
        return '推进'
    return '缓冲'


def scan(text: str, project: Path | None = None, style_path: Path | None = None) -> dict:
    baseline = _author_rhythm_baseline(project, style_path)
    paras = _paragraphs(text)
    total = cjk(text)
    if not paras or total < 500:
        return {"scanner": "narrative_rhythm", "violations_count": 0, "violations": [],
                "verdict": "PASS", "gate_level": "advisory", "metrics": {}, "_doc": "文本过短"}

    # 按 CJK 等分 N 窗算张力轨迹
    win_size = max(total // N_WINDOWS, 1)
    windows, buf, acc = [], [], 0
    for p in paras:
        buf.append(p)
        acc += cjk(p)
        if acc >= win_size and len(windows) < N_WINDOWS - 1:
            windows.append("\n".join(buf))
            buf, acc = [], 0
    if buf:
        windows.append("\n".join(buf))
    tensions = [_window_tension(w) for w in windows]

    violations = []
    mean_t = statistics.mean(tensions) if tensions else 0
    peak = max(tensions) if tensions else 0
    cv = (statistics.pstdev(tensions) / mean_t) if mean_t else 0
    retention = (tensions[-1] / peak) if peak else 1.0

    # 探针 1：过早收束（后段张力/峰值 < 绝对阈值）。
    # 🔴 注：作者档 narrative_rhythm.post_climax_retention 是 judge 标注的 0-10 张力口径，
    # 与本 scanner 的「文本代理张力(情绪标点+强度词+短句)」量纲不同·不可直接做阈值比较
    # （重蒸后 0.93 基线会把真作者 3 章样本误判）。故 premature 用固定绝对阈值(已校准真作者
    # 跨3章组 PASS)·judge 基线仅信息上报。作者特异的张力形态走 writer 注入(narrative_rhythm
    # directives)·非 scanner 阈值。
    base_ret = baseline["post_climax_retention"]
    ret_floor = RETENTION_DEFAULT
    if peak > 0 and retention < ret_floor:
        sev = 'major' if retention < ret_floor * 0.5 else 'minor'
        violations.append({
            'kind': 'premature_resolution', 'severity': sev,
            'post_retention': round(retention, 3), 'floor': ret_floor,
            'hint': f'后段张力仅峰值的 {round(retention*100)}%（阈值 {round(ret_floor*100)}%）'
                    f'=过早收束/一爽就泄；高潮/爽点后别立刻泄气，把张力(悬念/代价/新危机)撑到收尾',
        })

    # 探针 2：张力匀速平铺（变异系数过低）
    if len(tensions) >= 4 and cv < FLATLINE_CV:
        violations.append({
            'kind': 'tension_flatline', 'severity': 'minor',
            'cv': round(cv, 3), 'floor': FLATLINE_CV,
            'hint': f'张力轨迹变异系数 {round(cv,3)}<{FLATLINE_CV}=全程匀速平铺无起伏（机器腔）；'
                    f'制造「压抑→释放」落差，冲突升级与缓冲交替，别一个调子铺到底',
        })

    # 探针 3：节拍单调（连续推进段无缓冲）
    beats = [_beat_class(p) for p in paras]
    streak = mx = 0
    for b in beats:
        streak = streak + 1 if b == '推进' else 0
        mx = max(mx, streak)
    if mx >= MONO_MINOR:
        sev = 'major' if mx >= MONO_MAJOR else 'minor'
        violations.append({
            'kind': 'beat_monotony', 'severity': sev,
            'max_push_streak': mx, 'threshold': MONO_MAJOR if sev == 'major' else MONO_MINOR,
            'hint': f'连续 {mx} 段「推进」(动作/短句/危机)无缓冲段=一路平推不喘息；'
                    f'插入缓冲拍(反应/心理消化/对话交锋)让读者喘息，Swain scene-sequel 交替',
        })

    has_major = any(v['severity'] == 'major' for v in violations)
    verdict = 'PASS' if not violations else ('FAIL_MAJOR' if has_major else 'FAIL_MINOR')
    return {
        'scanner': 'narrative_rhythm',
        'violations_count': len(violations),
        'violations': violations,
        'verdict': verdict,
        'gate_level': 'advisory',
        'metrics': {
            'tension_cv': round(cv, 3),
            'post_climax_retention': round(retention, 3),
            'max_push_streak': mx,
            'windows': len(tensions),
        },
        'author_baseline': {'post_climax_retention': base_ret,
                            'from_author_profile': base_ret is not None},
        '_doc': '叙事节奏序列检测·张力轨迹/节拍单调·作者基线第一权威·advisory·治 LLM 过早收束',
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
