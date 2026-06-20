#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""thematic_argument_motif_balance.py — thematic_argument 对偶母题平衡 aggregator
(advisory · cross-cluster · opt-in · shadow · 2026-06-20 · R8 W4 Batch-J · L34)

【缺口】R8 W4 联网调研(Kieffer songbirds vs snakes + Writes With Tools Thematic
Metaphors + Fawkes Strengthening Story with Symbolism + 庆余年明暗双 motif):
高手主题在两组对立 motif 池之间反复展开 (光/暗、火/水、笼/翼…). LLM 默认偏一侧
→ 主题单线展开 vs 复调对偶。

【做法 · 确定性纯规则·opt-in】:
  1. 仅在 author_profile.thematic_argument_pairs 显式声明时激活 (无 → skip)。
  2. 每对 pair = {position_A, symbols_A:[..], position_B, symbols_B:[..]}。
  3. 每 cluster (从 _数据库/cluster_index.json 或 cluster_summary)统计 A/B 池
     motif 命中数。
  4. 失衡判定: 差 ≥ 3 倍 连续 ≥ 3 cluster → advisory 建议引入弱侧。
  5. 绝不替换 throughline_balance (独立维度)。

【北极星 ⑤ 顾问非法官】opt-in / 距离 hard_gate 远 / 退化默认 skip。
code THEMATIC_ARGUMENT_IMBALANCE 绝不进 hard_gate 。
env THEMATIC_ARGUMENT_BALANCE_MODE: off / shadow(默认) / active。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "THEMATIC_ARGUMENT_IMBALANCE"


def _mode() -> str:
    m = (os.environ.get("THEMATIC_ARGUMENT_BALANCE_MODE")
         or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _resolve_pairs(project_root) -> list:
    ap = _load_json(Path(project_root) / "_数据库" / "作者风格.json") or {}
    if isinstance(ap, dict):
        pairs = ap.get("thematic_argument_pairs")
        if isinstance(pairs, list) and pairs:
            return [p for p in pairs if isinstance(p, dict)
                    and p.get("symbols_A") and p.get("symbols_B")]
    return []


def _cluster_texts(project_root) -> list:
    """读各 cluster 草稿文本 + cluster_id。"""
    db = Path(project_root) / "_数据库"
    out = []
    # 从事件簇.json 找 cluster 列表
    ec = _load_json(db / "事件簇.json") or {}
    clusters = []
    if isinstance(ec, dict):
        clusters = [c for c in (ec.get("clusters") or [])
                    if isinstance(c, dict)]
    chapter_dir = Path(project_root) / "章节"
    for c in clusters:
        cid = c.get("cluster_id")
        if not cid:
            continue
        status = (c.get("status") or "").strip().lower()
        if status not in {"done", "completed", "in_progress", "active"}:
            continue
        # 找该 cluster 草稿
        text_parts = []
        for cand in chapter_dir.glob(f"{cid}*draft*.txt") if chapter_dir.exists() else []:
            try:
                text_parts.append(cand.read_text(encoding="utf-8"))
            except OSError:
                pass
        # cluster_summary 兜底
        if not text_parts:
            cs = db / "故事块摘要.json"
            data = _load_json(cs) or {}
            if isinstance(data, dict):
                summaries = data.get(cid)
                if isinstance(summaries, dict):
                    text_parts.append(json.dumps(summaries, ensure_ascii=False))
        if text_parts:
            out.append((cid, "\n".join(text_parts)))
    return out


def _count_hits(text: str, symbols: list) -> int:
    n = 0
    for s in symbols:
        if isinstance(s, str) and s:
            n += text.count(s)
    return n


def aggregate(project_root) -> dict:
    mode = _mode()
    out = {"scanner": "thematic_argument_motif_balance", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    if not project_root or not Path(project_root).exists():
        out["note"] = "无项目根·跳过"
        return out
    pairs = _resolve_pairs(project_root)
    if not pairs:
        out["note"] = "无 thematic_argument_pairs 声明·跳过(opt-in · 北极星②)"
        return out
    texts = _cluster_texts(project_root)
    if len(texts) < 3:
        out["note"] = f"cluster 文本数过少 (n={len(texts)})·跳过"
        return out

    pair_results = []
    issues = []
    for pi, pair in enumerate(pairs):
        sa = pair.get("symbols_A") or []
        sb = pair.get("symbols_B") or []
        if not (isinstance(sa, list) and isinstance(sb, list)):
            continue
        per_cluster = []
        for cid, text in texts:
            na = _count_hits(text, sa)
            nb = _count_hits(text, sb)
            per_cluster.append((cid, na, nb))
        # 连续 ≥ 3 cluster 失衡 (a >= 3*b 或 b >= 3*a)
        streak_a = 0
        streak_b = 0
        for cid, na, nb in per_cluster:
            if na >= 3 * max(1, nb) and na > 0:
                streak_a += 1
                streak_b = 0
            elif nb >= 3 * max(1, na) and nb > 0:
                streak_b += 1
                streak_a = 0
            else:
                streak_a = 0
                streak_b = 0
        pair_results.append({
            "pair_index": pi, "per_cluster": per_cluster,
            "max_streak_a": streak_a, "max_streak_b": streak_b,
        })
        if streak_a >= 3:
            issues.append(
                f"pair#{pi} A-side ({pair.get('position_A', '?')}) 连续 {streak_a} "
                f"cluster 失衡·建议引入 B ({pair.get('position_B', '?')}) 池")
        if streak_b >= 3:
            issues.append(
                f"pair#{pi} B-side ({pair.get('position_B', '?')}) 连续 {streak_b} "
                f"cluster 失衡·建议引入 A ({pair.get('position_A', '?')}) 池")

    out["pair_count"] = len(pairs)
    out["cluster_count"] = len(texts)
    out["pair_results"] = pair_results

    msg = " · ".join(issues) if issues else None
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "thematic_argument_imbalance", "severity": "minor",
                "message": msg, "issues": issues,
                "_doc": ("对偶母题平衡是工艺 advisory · opt-in · 绝不 hard_gate · "
                         "独立 throughline_balance")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] thematic_argument_motif_balance: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="对偶母题平衡 (advisory · cross-cluster · opt-in)")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    report = aggregate(args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
