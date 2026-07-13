#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cotton_needle_subtext_advisor.py — 绵针泥刺潜针对话 advisory · R23 W11 Batch-HH · P1

【缺口 · 古典评点（张竹坡评《金瓶梅》）】绵针泥刺：表面温情/恭顺/关切语言下
藏针锋（"笑里藏刀 / 锦里藏针"）。当前 dialogue 路径默认朴素 say-attack 二分，
无法表达「surface_warmth + subtext_aggression 同段共存」的反派阴狠/伪善质感。

【做法 · 确定性 · 零 LLM/零联网（替身 LLM-judge）】
  · 切对话段（中文弯引号 U+201C..U+201D / 中文直角「」 / 英文双引号）
  · 每段独立打 surface_warmth + subtext_aggression 两分
    - surface_warmth：cotton_needle_lexicon.surface_warmth_lexicon 5 桶命中归一
    - subtext_aggression：cotton_needle_lexicon.subtext_aggression_lexicon 6 桶命中归一
  · 同段 surface>0.4 且 subtext>0.4 → 绵针泥刺命中
  · 汇总到 角色关系.json.subtext_aggression_edges + author_intent_card（active 才写回）
  · 作者档 antagonist_style ∈ {阴狠/伪善} → 推荐密度↑（density_target_per_1k_dialogue）

【三 advisory】
  · COTTON_NEEDLE_DETECTED       — 同段 surface+subtext 双高 · 绵针泥刺命中（info）
  · COTTON_NEEDLE_BELOW_TARGET   — 作者档 antagonist_style 推荐高频但实际密度 < 0.5×target
  · COTTON_NEEDLE_OVER           — 实际密度 > 2× recommended（伪善过载·全篇阴阳）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  COTTON_NEEDLE_* 绝不进 audit_hub.HARD_GATE_CODES。

env COTTON_NEEDLE_MODE: off / shadow（默认） / active
用法: python cotton_needle_subtext_advisor.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_DETECTED = "COTTON_NEEDLE_DETECTED"
ISSUE_CODE_BELOW = "COTTON_NEEDLE_BELOW_TARGET"
ISSUE_CODE_OVER = "COTTON_NEEDLE_OVER"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

_SURFACE_THRESHOLD = 0.4
_SUBTEXT_THRESHOLD = 0.4
_BELOW_RATIO = 0.5
_OVER_RATIO = 2.0
_DEFAULT_LEX_PATH = Path(__file__).resolve().parent / "lexicons" / "cotton_needle_lexicon.json"

# 对话段抓取：中文弯/直角/英文双引号
_DIALOGUE_RE = re.compile(
    r"[“”](.+?)[“”]"
    r"|[「」『』](.+?)[「」『』]"
    r"|\"(.+?)\"",
    re.S,
)


def _mode() -> str:
    m = (os.environ.get("COTTON_NEEDLE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_lexicon(path: Path | None = None) -> dict:
    p = Path(path) if path else _DEFAULT_LEX_PATH
    if not p.exists():
        return {"surface_warmth_lexicon": {}, "subtext_aggression_lexicon": {},
                "antagonist_styles": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"surface_warmth_lexicon": {}, "subtext_aggression_lexicon": {},
                "antagonist_styles": {}}


def _bucket_hits(text: str, bucket: dict) -> int:
    hits = 0
    for words in bucket.values():
        if isinstance(words, list):
            for w in words:
                if w and isinstance(w, str):
                    hits += text.count(w)
    return hits


def _normalized_score(text: str, lex: dict) -> float:
    """N 个 bucket·按归一化分数（每命中桶 1/N，叠加 hits 微调 cap=1.0）。

    实证：用「命中桶占总桶数」+ hits 微调 → 短段也能稳定在 [0, 1]。
    """
    if not lex:
        return 0.0
    bucket_total = len(lex)
    if bucket_total == 0:
        return 0.0
    hit_buckets = 0
    hit_count = 0
    for words in lex.values():
        if not isinstance(words, list):
            continue
        bk_hits = 0
        for w in words:
            if w and isinstance(w, str) and w in text:
                bk_hits += text.count(w)
        if bk_hits:
            hit_buckets += 1
            hit_count += bk_hits
    bucket_score = hit_buckets / bucket_total
    # 内部加成（多重叠加不超过 1.0）
    return min(1.0, bucket_score + 0.05 * (hit_count - hit_buckets))


def _extract_dialogues(text: str) -> list[str]:
    out: list[str] = []
    for m in _DIALOGUE_RE.finditer(text):
        for g in m.groups():
            if g and g.strip():
                out.append(g.strip())
                break
    return out


def _read_author_antagonist_style(project_root) -> str | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            v = obj.get("antagonist_style")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _write_relations_back(project_root, edges_to_append: list[dict]) -> str | None:
    """active 模式把命中段写回 角色关系.json.subtext_aggression_edges 占位列表。

    幂等：基于 dialogue_snippet hash 去重。
    """
    if not project_root or not edges_to_append:
        return None
    p = Path(project_root) / "_数据库" / "角色关系.json"
    try:
        obj = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    edges = obj.get("subtext_aggression_edges")
    if not isinstance(edges, list):
        edges = []
    seen = {e.get("snippet_hash") for e in edges if isinstance(e, dict)}
    added = 0
    for e in edges_to_append:
        if e.get("snippet_hash") in seen:
            continue
        edges.append(e)
        added += 1
    obj["subtext_aggression_edges"] = edges
    # 顺便落 author_intent_card 占位
    aic = obj.get("author_intent_card")
    if not isinstance(aic, dict):
        aic = {}
    aic["cotton_needle_observation_count"] = aic.get(
        "cotton_needle_observation_count", 0) + added
    aic["_doc"] = "R23 W11 Batch-HH·绵针泥刺累积观察·advisory·非硬契约"
    obj["author_intent_card"] = aic
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)
    except OSError:
        return None


def scan(draft_path, project_root=None, lexicon_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "cotton_needle_subtext_advisor", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 800:
        out["note"] = "草稿太短·跳过"
        return out

    lex_obj = _load_lexicon(Path(lexicon_path) if lexicon_path else None)
    surface_lex = lex_obj.get("surface_warmth_lexicon") or {}
    subtext_lex = lex_obj.get("subtext_aggression_lexicon") or {}
    styles = lex_obj.get("antagonist_styles") or {}

    dialogues = _extract_dialogues(text)
    dialogue_total_cjk = sum(_cjk_count(d) for d in dialogues)
    if not dialogues or dialogue_total_cjk < 100:
        out["note"] = "对话段不足·跳过"
        out["dialogues_count"] = len(dialogues)
        return out

    cotton_hits = []
    for snip in dialogues:
        if _cjk_count(snip) < 8:
            continue
        s_score = _normalized_score(snip, surface_lex)
        t_score = _normalized_score(snip, subtext_lex)
        if s_score >= _SURFACE_THRESHOLD and t_score >= _SUBTEXT_THRESHOLD:
            cotton_hits.append({
                "snippet": snip[:80],
                "snippet_hash": str(abs(hash(snip)) % (10**12)),
                "surface_warmth": round(s_score, 3),
                "subtext_aggression": round(t_score, 3),
            })

    density_per_1k_dialogue = (len(cotton_hits) / (dialogue_total_cjk / 1000.0)
                               if dialogue_total_cjk else 0.0)
    antagonist_style = _read_author_antagonist_style(project_root)
    target_density = None
    recommend = None
    if antagonist_style and antagonist_style in styles:
        prof = styles[antagonist_style]
        if isinstance(prof, dict):
            tgt = prof.get("density_target_per_1k_dialogue")
            if isinstance(tgt, (int, float)):
                target_density = float(tgt)
            recommend = prof.get("recommend")

    out.update({
        "cjk": cjk,
        "dialogues_count": len(dialogues),
        "dialogue_total_cjk": dialogue_total_cjk,
        "cotton_hits_count": len(cotton_hits),
        "cotton_hits": cotton_hits[:8],
        "density_per_1k_dialogue": round(density_per_1k_dialogue, 3),
        "antagonist_style": antagonist_style,
        "target_density_per_1k_dialogue": target_density,
        "recommend": recommend,
    })

    flags = []
    if cotton_hits:
        flags.append({
            "code": ISSUE_CODE_DETECTED,
            "msg": f"绵针泥刺 {len(cotton_hits)} 段(密度 {density_per_1k_dialogue:.2f}/千字对话)",
            "severity": "info",
        })
    if target_density is not None:
        if density_per_1k_dialogue < target_density * _BELOW_RATIO:
            flags.append({
                "code": ISSUE_CODE_BELOW,
                "msg": (f"作者档 antagonist_style「{antagonist_style}」推荐"
                        f"密度 {target_density:.2f}·实际 {density_per_1k_dialogue:.2f}·偏低"),
                "severity": "minor",
            })
        elif density_per_1k_dialogue > target_density * _OVER_RATIO:
            flags.append({
                "code": ISSUE_CODE_OVER,
                "msg": (f"实际密度 {density_per_1k_dialogue:.2f} > 2x target"
                        f" {target_density:.2f}·伪善过载"),
                "severity": "minor",
            })

    written_path = None
    if mode == "active" and cotton_hits:
        edges = [{"snippet_hash": h["snippet_hash"],
                  "snippet_preview": h["snippet"],
                  "surface_warmth": h["surface_warmth"],
                  "subtext_aggression": h["subtext_aggression"],
                  "_source": "cotton_needle_subtext_advisor"}
                 for h in cotton_hits]
        written_path = _write_relations_back(project_root, edges)

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "cotton_needle", "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "张竹坡评金瓶梅绵针泥刺·R23 W11 Batch-HH·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR" if any(
                v["severity"] == "minor" for v in out["violations"]) else "PASS"
            out["warning"] = msg
        else:
            print(f"[SHADOW] cotton_needle: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    out["relations_path"] = written_path
    return out


def main():
    ap = argparse.ArgumentParser(description="绵针泥刺潜针对话 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--lexicon", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.lexicon)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
