#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strategic_empathy_alignment_scanner.py — Keen 三型 cluster 意图对齐
(R19 W8 Batch-Y·P2)

【缺口·2026-06-21·Suzanne Keen Empathy and the Novel(2007)】Keen 三型 strategic empathy:
  ① bounded       : 圈内/同类共情(我们 vs 他们)
  ② ambassadorial : 跨界代言/为他者发声(他者→我们)
  ③ broadcast     : 普世人性广播(全人类)
cluster brief 可标 strategic_empathy_intent ∈ {bounded, ambassadorial, broadcast}.
草稿应在三型 cue 词分布中体现该意图(对应 cue 占比应 > 其余两型). 否则 strategic intent 失准.

【输入】cluster 草稿(CLUSTER_MODE=1 env)+ cluster brief.
  brief.strategic_empathy_intent (可选)·缺 → 推断 dominant 类型(密度最高的型)·不告警仅观察.

【探针】
  1. 三型 cue hits per_1k_cjk.
  2. dominant_type = argmax(per_1k).
  3. 若 brief.intent 与 dominant_type 不符且 intent 型 hits 比 dominant 型低 ≥30% → MISALIGNED.

【北极星⑤】顾问非法官·全 advisory·env STRATEGIC_EMPATHY_ALIGNMENT_MODE 默认 shadow·
  STRATEGIC_EMPATHY_MISALIGNED 绝不 hard_gate.

【与既有 scanner 严格正交】
  - EC_vs_PD                : 共情 vs 个人痛苦(scene 内)·正交
  - bibliotherapy_arc       : Shrodes 三相·正交(读者侧 vs 文本侧)
  - VAD_UED                 : 角色情感动态·正交
  - thematic_argument       : 主题论证·正交
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "STRATEGIC_EMPATHY_MISALIGNED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 800
_DEFAULT_LEXICON = Path(__file__).resolve().parents[1] / "data" / "strategic_empathy_cues_placeholder.json"
MISALIGN_GAP = 0.30  # 30% 差距判 misaligned


def _mode() -> str:
    m = (os.environ.get("STRATEGIC_EMPATHY_ALIGNMENT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_cues(path=None):
    p = Path(path) if path else _DEFAULT_LEXICON
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cues = data.get("cues") or {}
    return cues if isinstance(cues, dict) else {}


def _load_brief(project_root, cluster_brief_path):
    data = None
    if cluster_brief_path and Path(cluster_brief_path).exists():
        try:
            data = json.loads(Path(cluster_brief_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None and project_root:
        p = Path(project_root) / "_数据库" / "事件簇.json"
        if p.exists():
            try:
                ec = json.loads(p.read_text(encoding="utf-8"))
                clusters = (ec or {}).get("clusters") or []
                if clusters:
                    data = clusters[0]
            except (OSError, json.JSONDecodeError):
                data = None
    return data if isinstance(data, dict) else None


def measure_types(text, cues):
    """每 type per_1k_cjk."""
    cjk = max(1, _cjk_count(text))
    out = {}
    for typ, words in cues.items():
        if not isinstance(words, list):
            continue
        hits = 0
        for w in words:
            if not isinstance(w, str) or not w:
                continue
            hits += text.count(w)
        out[typ] = {"hits": hits, "per_1k": round(hits * 1000.0 / cjk, 3)}
    return out


def scan(draft_path, project_root=None, cluster_brief_path=None,
         lexicon_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "strategic_empathy_alignment", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        out["note"] = "off·skip"
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    cues = _load_cues(lexicon_path)
    if not cues:
        out["note"] = "cue 词典缺失·skip"
        return out
    type_stats = measure_types(text, cues)
    if not type_stats:
        out["note"] = "type_stats 为空"
        return out

    # dominant_type
    dominant = max(type_stats.items(), key=lambda kv: kv[1]["per_1k"])[0]
    out["metrics"] = {"per_type": type_stats, "dominant_type": dominant}

    brief = _load_brief(project_root, cluster_brief_path)
    intent = None
    if isinstance(brief, dict):
        v = brief.get("strategic_empathy_intent")
        if isinstance(v, str) and v in type_stats:
            intent = v
    out["metrics"]["declared_intent"] = intent

    findings = []
    if intent and intent != dominant:
        intent_per = type_stats[intent]["per_1k"]
        dom_per = type_stats[dominant]["per_1k"]
        if dom_per > 0:
            gap_ratio = (dom_per - intent_per) / dom_per
            if gap_ratio >= MISALIGN_GAP:
                findings.append(
                    f"declared intent={intent!r} per_1k={intent_per} 远低于 dominant={dominant!r} "
                    f"per_1k={dom_per}·gap={round(gap_ratio,3)} ≥ {MISALIGN_GAP}")

    if findings:
        msg = " · ".join(findings)
        if mode == "active":
            out["violations"].append({
                "kind": "strategic_empathy_alignment", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "metrics": out["metrics"],
                "_doc": "R19 W8 Batch-Y·P2·Keen 三型 strategic empathy·advisory",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] strategic_empathy_alignment[{ISSUE_CODE}]: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="R19 W8 Batch-Y·P2·Keen 三型 strategic empathy·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-brief", default=None)
    ap.add_argument("--lexicon", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.cluster_brief, args.lexicon)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
