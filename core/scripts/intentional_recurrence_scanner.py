#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intentional_recurrence_scanner.py — 犯而不犯 cross-cluster shadow · R23 W11 Batch-GG · P1

【缺口 · 古典评点（张竹坡《金瓶梅》评）】犯而不犯：跨章/跨 cluster 复现母题
（动作/场景/对话骨架≥2 次）+ 细节差异化 → 制造熟悉与陌生的张力。当前
repeat_noun_density 把所有重复都判为坏 · 真"犯而不犯"被误伤。

【做法 · 确定性 · 零 LLM/零联网】
  · 跨 cluster scan 摘要 / cluster_draft（占位简化版用字符 3gram Jaccard 相似度）
  · 五轴 div_axes：
    (1) actor   主角变化
    (2) place   场所变化
    (3) prop    道具变化
    (4) mood    情绪变化
    (5) outcome 结果变化
  · 0.60 ≤ sim ≤ 0.85 且 div ≥ 3 → STRONG advisory（intentional recurrence）·
    对 repeat_noun_density 反向豁免
  · sim > 0.85 且 div < 2 → real_repeat advisory（真重复）

【三 advisory】
  · INTENTIONAL_RECURRENCE_DETECTED — 犯而不犯命中 · 对 repeat_noun_density 反向豁免
  · INTENTIONAL_RECURRENCE_REAL_REPEAT — 真重复 · sim 太高 div 太低
  · INTENTIONAL_RECURRENCE_THIN_DATA   — cluster 摘要 < 2 个 · 跳过

【北极星】②④⑤ 全 advisory · cross-cluster · shadow 默认 · 占位 _placeholder=true
  INTENTIONAL_RECURRENCE_* 绝不进 audit_hub.HARD_GATE_CODES。

env INTENTIONAL_RECURRENCE_MODE: off / shadow（默认） / active
用法: python intentional_recurrence_scanner.py --project <root>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_DETECTED = "INTENTIONAL_RECURRENCE_DETECTED"
ISSUE_CODE_REAL_REPEAT = "INTENTIONAL_RECURRENCE_REAL_REPEAT"
ISSUE_CODE_THIN_DATA = "INTENTIONAL_RECURRENCE_THIN_DATA"

SIM_LO = 0.60
SIM_HI = 0.85
DIV_MIN_FOR_INTENTIONAL = 3
DIV_MAX_FOR_REPEAT = 2


def _mode() -> str:
    m = (os.environ.get("INTENTIONAL_RECURRENCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _trigrams(text: str) -> set[str]:
    if not text:
        return set()
    s = re.sub(r"\s+", "", text)
    if len(s) < 3:
        return set()
    return {s[i:i + 3] for i in range(len(s) - 2)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _extract_axes(cluster: dict) -> dict:
    """从 cluster 摘要 dict 抽 5 轴关键字"""
    if not isinstance(cluster, dict):
        return {"actor": "", "place": "", "prop": "", "mood": "", "outcome": ""}
    def _str(v):
        if isinstance(v, str):
            return v.strip()
        if isinstance(v, list):
            return " ".join(str(x) for x in v if isinstance(x, (str, int, float)))[:100]
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)[:100]
        return ""
    actor = _str(cluster.get("characters_focus") or cluster.get("protagonist") or cluster.get("actor"))
    place = _str(cluster.get("hub_locations") or cluster.get("place") or cluster.get("scene_place"))
    prop = _str(cluster.get("anchor_props") or cluster.get("prop") or cluster.get("items"))
    mood = _str(cluster.get("mood") or cluster.get("emotion_tone") or cluster.get("affect"))
    outcome = _str(cluster.get("outcome") or cluster.get("ending_state") or cluster.get("stakes_delta"))
    return {"actor": actor, "place": place, "prop": prop, "mood": mood, "outcome": outcome}


def _div_axes(a: dict, b: dict) -> tuple[int, list[str]]:
    """计算 5 轴 div_count（不同 → +1）· 返回 (count, [diff_axes])"""
    diffs = []
    for k in ("actor", "place", "prop", "mood", "outcome"):
        va = (a.get(k) or "").strip()
        vb = (b.get(k) or "").strip()
        if not va or not vb:
            # 缺数据按"未差异"算 · 偏保守
            continue
        if va != vb:
            diffs.append(k)
    return len(diffs), diffs


def _collect_cluster_summaries(project_root: Path) -> list[dict]:
    """收集 cluster 摘要 · 优先 故事块摘要.json · 退 事件簇.json clusters"""
    summaries: list[dict] = []
    db = project_root / "_数据库"
    if not db.exists():
        return summaries
    primary = db / "故事块摘要.json"
    if primary.exists():
        try:
            obj = json.loads(primary.read_text(encoding="utf-8"))
            cs = obj.get("clusters") or obj.get("summaries") or []
            if isinstance(cs, list):
                summaries.extend([c for c in cs if isinstance(c, dict)])
        except (OSError, json.JSONDecodeError):
            pass
    if not summaries:
        ec = db / "事件簇.json"
        if ec.exists():
            try:
                obj = json.loads(ec.read_text(encoding="utf-8"))
                cs = obj.get("clusters") or []
                if isinstance(cs, list):
                    summaries.extend([c for c in cs if isinstance(c, dict)])
            except (OSError, json.JSONDecodeError):
                pass
    return summaries


def _summary_text_for_sim(c: dict) -> str:
    parts = []
    for k in ("scope_summary", "summary", "scene_storyboard", "brief", "synopsis"):
        v = c.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    parts.append(x)
                elif isinstance(x, dict):
                    parts.append(json.dumps(x, ensure_ascii=False))
    return "\n".join(parts)


def scan(project_root: str | Path) -> dict:
    mode = _mode()
    out = {"scanner": "intentional_recurrence", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory", "_placeholder": True,
           "violations": [], "verdict": "PASS", "warning": None,
           "thresholds": {"sim_lo": SIM_LO, "sim_hi": SIM_HI,
                          "div_min_intentional": DIV_MIN_FOR_INTENTIONAL,
                          "div_max_repeat": DIV_MAX_FOR_REPEAT}}
    if mode == "off":
        return out
    root = Path(project_root)
    summaries = _collect_cluster_summaries(root)
    if len(summaries) < 2:
        out["note"] = "cluster 摘要 < 2 个·跳过"
        if mode == "active":
            out["violations"].append({
                "kind": "intentional_recurrence", "severity": "info",
                "code": ISSUE_CODE_THIN_DATA,
                "message": f"cluster 摘要 {len(summaries)} 个 · 跳过",
                "_doc": "R23 W11 Batch-GG·advisory"})
        out["violations_count"] = len(out["violations"])
        return out

    pairs_intentional = []
    pairs_real_repeat = []
    pair_records = []
    for i in range(len(summaries)):
        for j in range(i + 1, len(summaries)):
            a = summaries[i]
            b = summaries[j]
            ta = _summary_text_for_sim(a)
            tb = _summary_text_for_sim(b)
            if not ta or not tb:
                continue
            sim = _jaccard(_trigrams(ta), _trigrams(tb))
            axes_a = _extract_axes(a)
            axes_b = _extract_axes(b)
            div_count, diff_axes = _div_axes(axes_a, axes_b)
            rec = {
                "i": i, "j": j,
                "id_a": a.get("cluster_id") or f"c{i:03d}",
                "id_b": b.get("cluster_id") or f"c{j:03d}",
                "sim": round(sim, 3),
                "div_count": div_count,
                "diff_axes": diff_axes,
                "kind": None,
            }
            if SIM_LO <= sim <= SIM_HI and div_count >= DIV_MIN_FOR_INTENTIONAL:
                rec["kind"] = "intentional_recurrence"
                pairs_intentional.append(rec)
            elif sim > SIM_HI and div_count <= DIV_MAX_FOR_REPEAT:
                rec["kind"] = "real_repeat"
                pairs_real_repeat.append(rec)
            pair_records.append(rec)

    out["pairs_scanned"] = len(pair_records)
    out["intentional_count"] = len(pairs_intentional)
    out["real_repeat_count"] = len(pairs_real_repeat)
    out["samples"] = pair_records[:10]

    flags = []
    if pairs_intentional:
        flags.append({
            "code": ISSUE_CODE_DETECTED,
            "msg": (f"犯而不犯命中 {len(pairs_intentional)} 对（0.60≤sim≤0.85 且 div≥3）·"
                    f"对 repeat_noun_density 反向豁免"),
            "severity": "minor",
        })
    if pairs_real_repeat:
        flags.append({
            "code": ISSUE_CODE_REAL_REPEAT,
            "msg": f"真重复 {len(pairs_real_repeat)} 对（sim>0.85 且 div<2）",
            "severity": "minor",
        })

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "intentional_recurrence", "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "张竹坡犯而不犯·R23 W11 Batch-GG·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] intentional_recurrence: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="犯而不犯 cross-cluster advisory shadow")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    rep = scan(args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
