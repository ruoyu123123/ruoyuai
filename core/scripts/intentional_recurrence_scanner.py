#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intentional_recurrence_scanner.py — 犯而不犯 cross-cluster shadow · R23 W11 Batch-GG · P1

【缺口 · 古典评点（张竹坡《金瓶梅》评）】犯而不犯：跨章/跨 cluster 复现母题
（动作/场景/对话骨架≥2 次）+ 细节差异化 → 制造熟悉与陌生的张力。当前
repeat_noun_density 把所有重复都判为坏 · 真"犯而不犯"被误伤。

【做法 · 确定性】
  · 跨 cluster scan 摘要 / cluster_draft 相似度：
    · 默认（无真语义后端）= 字符 3gram Jaccard 相似度（占位简化版·零 LLM/零联网）
    · 🔴 2026-07-01 EMBED_BACKEND 配置真后端时 = embedding 余弦相似度（能抓住同义改写的
      重复 motif——trigram Jaccard 抓不到换词表达的复现）
  · 五轴 div_axes：
    (1) actor   主角变化
    (2) place   场所变化
    (3) prop    道具变化
    (4) mood    情绪变化
    (5) outcome 结果变化
  · 0.60 ≤ sim ≤ 0.85 且 div ≥ 3 → STRONG advisory（intentional recurrence）·
    对 repeat_noun_density 反向豁免
  · sim > 0.85 且 div < 2 → real_repeat advisory（真重复）

【依赖】embedding_store.compute_embedding() + cosine_similarity()（同 topic_drift_scanner 模式）。
  EMBED_BACKEND 未设（默认 hash·无真语义）→ 完全走 trigram Jaccard·match_method="lexicon"。

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


# ── 真语义 embedding 可选路径 ───────
def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 非空且非 hash（本地 daemon/ruoyu_style/mstyle/local 链）→ True；
    未设或 =hash（默认 hash 袋·无真语义）→ False。本仓约定：每个消费风格 embedding
    的文件自带一份同口径判定，不互相 import。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    return bool(eb) and eb != "hash"


def _semantic_similarity(ta: str, tb: str, compute_embedding, cosine_similarity) -> "float | None":
    """embedding 余弦相似度替代字符 3gram Jaccard（抓同义改写的重复 motif）。
    维度不一致/计算异常 → None（调用方兜底 trigram Jaccard·不半真半假）。
    compute_embedding/cosine_similarity 由调用方（scan()）一次性 import 后传入——
    避免每对 cluster 都重复尝试 import·且顶层 match_method 能如实反映 import 是否成功。
    """
    try:
        emb_a = compute_embedding(ta)
        emb_b = compute_embedding(tb)
    except Exception:
        return None
    if not emb_a or not emb_b or len(emb_a) != len(emb_b):
        return None
    return cosine_similarity(emb_a, emb_b)


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

    use_semantic = False
    compute_embedding = cosine_similarity = None
    if _has_real_embedding_backend():
        try:
            from embedding_store import compute_embedding, cosine_similarity
            use_semantic = True
        except (ImportError, TypeError):
            use_semantic = False
    out["match_method"] = "semantic" if use_semantic else "lexicon"

    # 🔴 2026-07-03 Wave-4：语义路径下先收集本次全部 cluster 摘要文本，一次性 prefetch
    # 灌缓存——下面 O(N²) 逐对 compute_embedding 调用全部命中缓存（N 个 cluster 只需
    # 1 次后端批调用，取代当前每个 cluster 首次出现即各自触发一次单条 subprocess 调用）。
    if use_semantic:
        summary_texts = [t for t in (_summary_text_for_sim(s) for s in summaries) if t]
        if summary_texts:
            try:
                from embedding_store import prefetch_embeddings
                prefetch_embeddings(summary_texts)
            except (ImportError, TypeError):
                pass

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
            pair_method = "lexicon"
            sim = None
            if use_semantic:
                sim = _semantic_similarity(ta, tb, compute_embedding, cosine_similarity)
                if sim is not None:
                    pair_method = "semantic"
            if sim is None:
                sim = _jaccard(_trigrams(ta), _trigrams(tb))
                pair_method = "lexicon"
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
                "match_method": pair_method,
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
                    "match_method": out["match_method"],
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
