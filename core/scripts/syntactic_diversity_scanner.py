#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""syntactic_diversity_scanner.py — 句法多样性 / 篇章级 AI 腔检测（cluster 视野 · 作者基线第一权威 · advisory）

2026-06-16 新增（盲区 perplexity_obsolete 落地）。
背景（5 篇论文实证）：现代 LLM 产 low-perplexity 流畅文本，perplexity/burstiness 判 AI 已系统性失效；
业界两条无 token 概率的轻量新路可落地——
  ① 句法模板 overuse（arXiv 2407.00211）：POS 序列同骨架重复率，AI 95%+ 文本含重复句法模板 vs 人类 ~46%。
  ③ 篇章级叙事破绽（StoryScope · arXiv 2604.03136，AI 小说正 domain）：AI 小说最大破绽是 discourse 级——
     "过度解释主题/偏好规整单线"。

本 scanner 补 memory `feedback_inverted_modifier_sentence_mold_overuse` 白纸黑字记载的盲区：
  "同语法骨架复用·机械 scanner 查不出·只查同主语 streak 漏查同语法骨架"——
  prose_rhythm 只查字符级「主语+动作」streak / 段首倒装；本 scanner 把句子抽象成 POS 序列测同骨架重复。

🔴北极星五重对齐（逐条钉死）：
  (a) advisory 非 hard_gate：code = SYNTAX_TEMPLATE_OVERUSE / THEME_OVER_EXPLAIN，
      **绝不进 STRUCTURE.md 第十一节 15 码 / audit_hub.HARD_GATE_CODES**，每个 finding 带 gate_level='advisory'。
  (b) 作者档第一权威：阈值用作者自身分布（quantitative.syntactic_diversity 的 mean+kσ·由 consolidate
      确定性聚合）；**无作者档才退通用兜底**——且兜底用的是「真作者实测上限之上的绝对地板」
      (per-scene 模板覆盖率 0.567 上限 → 地板 0.70；theme 0.21/kCJK 上限 → 地板 0.6/kCJK)，
      **绝不用论文的跨群体锚 0.46/0.55**（实测中文 jieba POS 粒度粗 → 真作者 per-scene 覆盖 0.22-0.57，
      论文 55% 阈会把全部真作者误判，反噬北极星）。
  (c) 顾问非法官：只报「偏离作者自身基线」+ fix_hint，长句/高独行/作者签名句式一律不当缺陷；不阻断流水线。
  (d) cluster 为单位：入参 cluster 草稿全文，POS 序列**按场景边界分段算**（防跨场景误报 + 长度稳定）。

🔴 探针 2（词汇多样性 TTR/MATTR）按 design ROI **DEFER 不做**：方向数据集相关（英文新闻 AI 多样性更高，
   中文小说方向未实证），且与 repeat_noun_density 部分重叠——拿不准 → 不过度工程（北极星⑥）。

【两探针】
  1. syntactic_template_overuse  POS n-gram 同骨架复用率（per-scene 平均覆盖率 vs 作者基线 mean+kσ）
  3. theme_over_explanation      抽象大词 + 顿悟/总结连接词同句共现密度（per-kCJK vs 作者基线 mean+kσ）

输出：JSON {scanner, violations:[{kind,severity,...}], verdict, gate_level, metrics, author_baseline}
用法：python syntactic_diversity_scanner.py <cluster草稿> [--project <root>] [--style <作者风格.json>]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

# ── 探针 1：句法模板 overuse 参数 ─────────────────────────────
_POS_NGRAM_NS = (4, 5, 6, 7, 8)        # POS n-gram 长度（照搬 arXiv 2407.00211 的 4-8）
_NGRAM_TAU = 3                         # 同一 POS n-gram 出现 ≥τ 次才算「模板」
_TEMPLATE_Z_MINOR, _TEMPLATE_Z_MAJOR = 3.0, 4.5   # vs 作者基线的偏离 σ 倍数
# 通用兜底地板（作者档缺 syntactic_diversity 维时用）。
# 🔴 实测三本真作者(诡秘σ27/主神σ15/惊悚) per-scene 模板覆盖率上限 0.567 → 地板坐落其上 0.70，
#    保证真作者绝不触发；论文人类 0.46 锚对中文 jieba POS 不成立(粒度粗)，坚决不用。
_TEMPLATE_COV_FLOOR_MINOR = 0.70
_TEMPLATE_COV_FLOOR_MAJOR = 0.85
_MIN_POS_TOKENS = 80                   # 单场景 POS token 下限（短场景不参与·防噪）
_MIN_SCENE_CHARS = 600                 # 场景目标字数（不足合并·与 cluster 草稿尺度对齐）

# ── 探针 3：篇章级主题过度解释参数 ─────────────────────────────
# 抽象大词（复用 semantic_slop ABSTRACT_NOUN 同源词表，单文件零依赖故内联）
_ABSTRACT_NOUN = re.compile(
    r"(命运|人生|世界|真相|选择|时间|孤独|恐惧|希望|答案|生命|死亡|"
    r"自由|未来|过去|人性|欲望|信仰|宿命|代价|意义|本质|灵魂|存在|"
    r"黑暗|救赎|远方|青春|成长|羁绊|执念|信念|觉悟)"
)
# 顿悟/断言/总结连接词（StoryScope「显式点破主题」代理信号）
_EPIPHANY_MARK = re.compile(
    r"(这一刻[^，。！？]{0,8}(?:明白|懂得|领悟|意识到|清楚|知道)|"
    r"原来|正如|所谓|也就是说|换句话说|意味着|象征着|代表着|"
    r"这说明|这表明|归根结底|说到底|本质上|这就是|从某种意义上)"
)
_THEME_PER_K_MINOR, _THEME_PER_K_MAJOR = 0.6, 1.2   # 通用兜底：per-kCJK 共现密度
                                                    # （真作者实测 0.07-0.21 → 地板 0.6 安全 ≥3×）
_THEME_Z_MINOR, _THEME_Z_MAJOR = 2.5, 4.0           # vs 作者基线的偏离 σ 倍数
_THEME_MIN_HITS = 4                                  # 至少 N 处共现才报（防短 cluster 噪声）

# 对话整体（剥离后不进 POS 序列·与 cross_scene_voice_drift DIALOGUE_RE 同源·去对话防 POS 污染）
_DIALOGUE_SPAN = re.compile(r'["“「『][^"”」』\n]{0,300}["”」』]')


from text_metrics import count_cjk as cjk  # noqa: E402 字数口径单一真理源
from atomic_json import load_json  # noqa: E402


def _load_json(p: Path):
    return load_json(p)


def _author_baseline(project: Path | None, style_path: Path | None) -> dict:
    """读作者风格档 quantitative.syntactic_diversity（由 consolidate_author_profile 确定性聚合）。
    北极星⑤第一权威。无该维 → 返回 None，scan 退通用绝对地板兜底。

    与 prose_rhythm_scanner._author_baseline 同一 contract（同样优先 --style，再项目 _数据库 双档）。
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
    out = {"template_cov_mean": None, "template_cov_std": None,
           "theme_per_k_mean": None, "theme_per_k_std": None,
           "from_author_profile": False}
    if isinstance(data, dict):
        q = data.get("quantitative") or {}
        sd = q.get("syntactic_diversity") or {}
        if isinstance(sd, dict):
            tc_m = sd.get("template_coverage_mean")
            tc_s = sd.get("template_coverage_std")
            th_m = sd.get("theme_explain_per_kcjk_mean")
            th_s = sd.get("theme_explain_per_kcjk_std")
            if isinstance(tc_m, (int, float)):
                out["template_cov_mean"] = float(tc_m)
                out["template_cov_std"] = float(tc_s) if isinstance(tc_s, (int, float)) else None
                out["from_author_profile"] = True
            if isinstance(th_m, (int, float)):
                out["theme_per_k_mean"] = float(th_m)
                out["theme_per_k_std"] = float(th_s) if isinstance(th_s, (int, float)) else None
                out["from_author_profile"] = True
    return out


def split_scenes(text: str, target: int = _MIN_SCENE_CHARS) -> list[str]:
    """按场景边界切：优先 \\n---\\n / 三连换行；不足 target 的相邻段合并。
    cluster 视野下 POS 覆盖率**必须 per-scene 算再平均**——整块 concat 算会因长度膨胀 n-gram
    碰撞率（实测真作者 concat 0.66-0.80 vs per-scene 0.22-0.57），跨场景污染 + 长度不稳。"""
    hard = re.split(r"\n---+\n|\n\n\n+", text)
    hard = [h for h in hard if h.strip()]
    scenes, cur = [], ""
    for blk in hard:
        # 块内再按空行聚到 target
        for para in re.split(r"\n\s*\n", blk):
            para = para.strip()
            if not para:
                continue
            cur += para + "\n"
            if cjk(cur) >= target:
                scenes.append(cur)
                cur = ""
        if cur.strip():        # 硬分隔处收一刀（保场景边界语义）
            scenes.append(cur)
            cur = ""
    if cur.strip():
        scenes.append(cur)
    return scenes or ([text] if text.strip() else [])


def _strip_for_pos(text: str) -> str:
    """剥离对话引号内内容 + 章节标题/系统标记行（POS 只看叙述骨架·北极星④切章不参与）。"""
    lines = [l for l in text.split('\n')
             if not re.match(r'^第\d+章', l.strip()) and not l.strip().startswith('【')]
    t = '\n'.join(lines)
    t = _DIALOGUE_SPAN.sub('', t)
    return t


def _pos_sequence(text: str):
    """jieba.posseg 对叙述文本 POS 标注 → POS flag 序列（去空白 token）。
    jieba 是模块级懒加载（import 成本只付一次）；失败回退 None（不崩 scanner）。"""
    try:
        import jieba.posseg as pseg
    except Exception:
        return None
    cleaned = _strip_for_pos(text)
    try:
        return [w.flag for w in pseg.cut(cleaned) if w.word and w.word.strip()]
    except Exception:
        return None


def _template_coverage(toks: list[str], ns=_POS_NGRAM_NS, tau: int = _NGRAM_TAU) -> float:
    """模板覆盖率 = 被「出现 ≥τ 次的 POS n-gram」覆盖的 token 位置占比（arXiv 2407.00211 指标 B）。"""
    if not toks or len(toks) < min(ns):
        return 0.0
    covered = [False] * len(toks)
    for n in ns:
        if len(toks) < n:
            continue
        grams = [tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)]
        cnt = Counter(grams)
        for i, g in enumerate(grams):
            if cnt[g] >= tau:
                for j in range(i, i + n):
                    covered[j] = True
    return sum(covered) / len(toks)


def _scan_template_overuse(text: str, baseline: dict, cluster_mode: bool) -> tuple:
    """探针 1：per-scene 模板覆盖率均值 vs 作者基线 mean+kσ（无基线退绝对地板）。
    返回 (violation|None, metrics)。"""
    scenes = split_scenes(text)
    cov_per_scene = []
    for sc in scenes:
        toks = _pos_sequence(sc)
        if toks is None:        # jieba 不可用 → 整探针放弃（advisory 不阻断）
            return None, {"template_coverage_mean": None, "_pos_unavailable": True}
        if len(toks) >= _MIN_POS_TOKENS:
            cov_per_scene.append(_template_coverage(toks))
    if not cov_per_scene:
        return None, {"template_coverage_mean": None, "scenes_scored": 0}
    cov_mean = statistics.mean(cov_per_scene)
    metrics = {
        "template_coverage_mean": round(cov_mean, 3),
        "template_coverage_max_scene": round(max(cov_per_scene), 3),
        "scenes_scored": len(cov_per_scene),
    }
    a_mean, a_std = baseline.get("template_cov_mean"), baseline.get("template_cov_std")
    if a_mean is not None and a_std and a_std > 1e-6:
        z = (cov_mean - a_mean) / a_std
        metrics["template_coverage_z"] = round(z, 2)
        # cluster_mode 容忍：minor 门槛抬到 major 线，只报真显著（对齐 fake_range cluster 容忍）
        if z >= _TEMPLATE_Z_MAJOR:
            sev = 'major'
        elif z >= (_TEMPLATE_Z_MINOR if not cluster_mode else _TEMPLATE_Z_MAJOR):
            sev = 'minor'
        else:
            return None, metrics
        return ({
            'kind': 'syntactic_template_overuse', 'severity': sev,
            'coverage_mean': round(cov_mean, 3), 'author_mean': round(a_mean, 3),
            'author_std': round(a_std, 3), 'z': round(z, 2),
            'hint': f'句法模板覆盖率 {round(cov_mean*100)}%（POS n-gram 同骨架复用）落在作者自身基线'
                    f'{round(a_mean*100)}%±{round(a_std*100)}% 的上方 {round(z,1)}σ→同语法骨架反复套用'
                    f'(主语+动词+宾语流水账/「X的[主语]」倒装批量)；变换从句结构、插入对话节拍、'
                    f'让句法骨架多样化（别让同一 POS 模式霸占多句）',
        }, metrics)
    # 无作者基线 → 绝对地板（坐落真作者实测上限 0.567 之上·北极星⑤不拿群体锚拽向均值）
    if cov_mean >= _TEMPLATE_COV_FLOOR_MINOR:
        sev = 'major' if cov_mean >= _TEMPLATE_COV_FLOOR_MAJOR else 'minor'
        return ({
            'kind': 'syntactic_template_overuse', 'severity': sev,
            'coverage_mean': round(cov_mean, 3), 'floor': _TEMPLATE_COV_FLOOR_MINOR,
            'hint': f'句法模板覆盖率 {round(cov_mean*100)}%（无作者档·通用地板 {round(_TEMPLATE_COV_FLOOR_MINOR*100)}%）'
                    f'=大量句子共享同一 POS 骨架→塑料流水账；变换句法结构、长短句交替、插对话/心理打断同骨架连发',
        }, metrics)
    return None, metrics


def _scan_theme_over_explanation(text: str, baseline: dict, total_cjk: int, cluster_mode: bool) -> tuple:
    """探针 3：抽象大词 + 顿悟/总结连接词同句共现密度 vs 作者基线 mean+kσ（无基线退绝对地板）。
    StoryScope「过度解释主题」代理信号·篇章级。返回 (violation|None, metrics)。"""
    lines = [l for l in text.split('\n')
             if not re.match(r'^第\d+章', l.strip()) and not l.strip().startswith('【')]
    body = '\n'.join(lines)
    sents = [s.strip() for s in re.split(r'(?<=[。！？])', body) if s.strip()]
    hits = []
    for i, s in enumerate(sents):
        if _ABSTRACT_NOUN.search(s) and _EPIPHANY_MARK.search(s):
            hits.append(i)
    n_hits = len(hits)
    per_k = round(n_hits / total_cjk * 1000, 3) if total_cjk else 0.0
    metrics = {"theme_explain_hits": n_hits, "theme_explain_per_kcjk": per_k}
    if n_hits < _THEME_MIN_HITS:
        return None, metrics
    a_mean, a_std = baseline.get("theme_per_k_mean"), baseline.get("theme_per_k_std")
    if a_mean is not None and a_std and a_std > 1e-6:
        z = (per_k - a_mean) / a_std
        metrics["theme_explain_z"] = round(z, 2)
        if z >= _THEME_Z_MAJOR:
            sev = 'major'
        elif z >= (_THEME_Z_MINOR if not cluster_mode else _THEME_Z_MAJOR):
            sev = 'minor'
        else:
            return None, metrics
        return ({
            'kind': 'theme_over_explanation', 'severity': sev,
            'per_kcjk': per_k, 'hits': n_hits, 'author_mean': round(a_mean, 3),
            'author_std': round(a_std, 3), 'z': round(z, 2),
            'hint': f'主题过度解释 {n_hits} 处({per_k}/千字·抽象大词+「这一刻明白/原来/意味着」共现)落在作者基线'
                    f'{a_mean}±{round(a_std,3)} 上方 {round(z,1)}σ→显式点破主题/不信任读者(StoryScope 证 AI 小说最大破绽)；'
                    f'让主题从动作与画面里自然透出，删掉「原来这就是…」式升华',
        }, metrics)
    # 无作者基线 → 绝对地板（真作者实测 0.07-0.21/kCJK → 地板 0.6 安全）
    if per_k >= _THEME_PER_K_MINOR:
        sev = 'major' if per_k >= _THEME_PER_K_MAJOR else 'minor'
        return ({
            'kind': 'theme_over_explanation', 'severity': sev,
            'per_kcjk': per_k, 'hits': n_hits, 'floor': _THEME_PER_K_MINOR,
            'hint': f'主题过度解释 {n_hits} 处({per_k}/千字·无作者档·通用地板 {_THEME_PER_K_MINOR})'
                    f'=反复显式点破主题/升华→AI 小说腔；让主题从情节透出，删抽象大词+顿悟句的直白点题',
        }, metrics)
    return None, metrics


def scan(text: str, project: Path | None = None, style_path: Path | None = None) -> dict:
    cluster_mode = os.environ.get("CLUSTER_MODE") == "1"
    baseline = _author_baseline(project, style_path)
    total_cjk = cjk(text)

    if total_cjk < 50:
        return {"scanner": "syntactic_diversity", "violations_count": 0, "violations": [],
                "verdict": "PASS", "gate_level": "advisory", "metrics": {},
                "author_baseline": {"from_author_profile": baseline.get("from_author_profile", False)},
                "_doc": "空/极短文本"}

    violations = []
    v1, m1 = _scan_template_overuse(text, baseline, cluster_mode)
    if v1:
        violations.append(v1)
    v3, m3 = _scan_theme_over_explanation(text, baseline, total_cjk, cluster_mode)
    if v3:
        violations.append(v3)

    has_major = any(v['severity'] == 'major' for v in violations)
    verdict = 'PASS' if not violations else ('FAIL_MAJOR' if has_major else 'FAIL_MINOR')
    metrics = {}
    metrics.update(m1 or {})
    metrics.update(m3 or {})
    metrics["total_cjk"] = total_cjk
    return {
        'scanner': 'syntactic_diversity',
        'violations_count': len(violations),
        'violations': violations,
        'verdict': verdict,
        'gate_level': 'advisory',     # 北极星⑤顾问非法官·恒 advisory
        'metrics': metrics,
        'author_baseline': {
            'from_author_profile': baseline.get("from_author_profile", False),
            'template_cov_mean': baseline.get("template_cov_mean"),
            'theme_per_k_mean': baseline.get("theme_per_k_mean"),
        },
        '_doc': '句法多样性/篇章级主题过度解释检测·作者基线第一权威·advisory·'
                '补「同语法骨架复用」(memory feedback_inverted)+StoryScope 篇章级 AI 腔缺口',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("--project", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    p = Path(args.draft)
    if not p.exists():
        print(f"路径不存在: {p}", file=sys.stderr)
        sys.exit(2)
    result = scan(p.read_text(encoding='utf-8'),
                  project=Path(args.project) if args.project else None,
                  style_path=Path(args.style) if args.style else None)
    result['file'] = str(p)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
