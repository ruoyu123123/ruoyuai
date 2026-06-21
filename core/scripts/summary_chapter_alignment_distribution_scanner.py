#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""summary_chapter_alignment_distribution_scanner.py — Attention Flows 摘要-章节概念质量分布 advisory · cross-cluster · 2026-06-21 R20 W9 Batch-AA · P1

【缺口 · R20 Q3-Q4 论文 id 17】Attention Flows / Summary-Chapter Conceptual
Mass 分布：评估章节级摘要(chapter summaries / cluster sub_summary)
在『全章节序列』上的概念覆盖均衡度。健康分布期望摘要质量近似均匀
分布 — LLM 默认偏向「头重(开篇过度摘述)」或「尾重(末段堆砌反思)」。

【做法 · 确定性 · 零额外 LLM】
  · 读 _数据库/故事块摘要.json (cluster_summary_reader)
  · 每 cluster 取 sub_summaries / scope_summary 文字
  · 文本表示 = char bigram TF 向量(SBERT-zh 真嵌入 defer · _placeholder=true)
  · 对每个 cluster 算 summary_mass = (本 cluster summary 文本长度) /
    (摘要正文 cjk 总长度) — 这是「质量分布」近似
  · chapter_position = cluster 序号在全书的位置(0..1)
  · 两 advisory：
     - SUMMARY_FIRST_QUARTER_OVERWEIGHT — 前 25% chapter_position 的累计
       summary_mass > 0.40
     - SUMMARY_TAIL_BIAS — 末 25% chapter_position 的累计
       summary_mass > 0.40

【distill 抽取 author_summary_mass_signature SLOW_UPDATE】
  · consolidate_author_profile 在 phase-3 写入
    author_profile.slow_update.summary_mass_signature_band = {head_share, tail_share}
  · 本 scanner 优先用作者档 band

【与既有 scanner 严格正交】
  · cross_cluster_throughline_balance 查主题线均衡 · 不查摘要质量分布
  · cross_cluster_arc_progression 查角色弧推进 · 不查 summary mass
  · sagging_middle 查中段坍塌 · 不查头尾偏置
  本 scanner = 摘要-章节 mass 分布 唯一覆盖。

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  SUMMARY_FIRST_QUARTER_OVERWEIGHT / SUMMARY_TAIL_BIAS 绝不进 audit_hub.HARD_GATE_CODES。

env SUMMARY_MASS_DISTRIBUTION_MODE: off / shadow(默认) / active
用法: python summary_chapter_alignment_distribution_scanner.py <project> [--last-n N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402

ISSUE_HEAD = "SUMMARY_FIRST_QUARTER_OVERWEIGHT"
ISSUE_TAIL = "SUMMARY_TAIL_BIAS"

DEFAULT_HEAD_SHARE_MAX = 0.40
DEFAULT_TAIL_SHARE_MAX = 0.40
QUARTER_WINDOW = 0.25


def _mode() -> str:
    m = (os.environ.get("SUMMARY_MASS_DISTRIBUTION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_author_baseline(project_root) -> dict | None:
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
            slow = obj.get("slow_update") or {}
            sm = slow.get("summary_mass_signature_band") or obj.get("summary_mass_signature_band")
            if isinstance(sm, dict):
                return sm
    return None


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _cluster_summary_text(c: dict) -> str:
    """采集 cluster 内全部 summary 文字。"""
    parts: list[str] = []
    for k in ("scope_summary", "summary"):
        v = c.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    sub = c.get("sub_summaries") or []
    if isinstance(sub, list):
        for s in sub:
            if isinstance(s, str):
                parts.append(s)
            elif isinstance(s, dict):
                t = s.get("text") or s.get("summary") or ""
                if isinstance(t, str):
                    parts.append(t)
    chapters = c.get("chapters") or {}
    if isinstance(chapters, dict):
        for rec in chapters.values():
            if isinstance(rec, dict):
                s = rec.get("sub_summary") or rec.get("summary") or ""
                if isinstance(s, str):
                    parts.append(s)
    return "\n".join(parts)


def compute_distribution(clusters: list[dict]) -> dict:
    """每 cluster summary 长度 → mass · chapter_position → bucket head/mid/tail。"""
    items = []
    for idx, c in enumerate(clusters):
        text = _cluster_summary_text(c)
        items.append({"idx": idx, "cluster_id": c.get("cluster_id"), "cjk": _cjk_count(text)})
    total_cjk = sum(it["cjk"] for it in items) or 1
    n = len(items)
    head_share = 0.0
    tail_share = 0.0
    mid_share = 0.0
    for it in items:
        pos = (it["idx"] + 0.5) / max(1, n)
        share = it["cjk"] / total_cjk
        it["position"] = round(pos, 4)
        it["summary_mass"] = round(share, 4)
        if pos <= QUARTER_WINDOW:
            head_share += share
        elif pos >= 1 - QUARTER_WINDOW:
            tail_share += share
        else:
            mid_share += share
    return {
        "items": items,
        "head_share": round(head_share, 4),
        "mid_share": round(mid_share, 4),
        "tail_share": round(tail_share, 4),
        "total_cjk": total_cjk,
        "cluster_count": n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=None,
                    help="(可选)只看最近 N 个 cluster")
    args = ap.parse_args()
    mode = _mode()
    if mode == "off":
        print("[OFF] SUMMARY_MASS_DISTRIBUTION_MODE=off")
        sys.exit(0)
    project_root = Path(args.project)
    clusters = csr.get_clusters(project_root, last_n=args.last_n)
    if len(clusters) < 4:
        print(f"[SKIP] cluster 数 {len(clusters)} < 4·分布未成形")
        sys.exit(0)

    dist = compute_distribution(clusters)

    baseline = _read_author_baseline(project_root)
    head_max = DEFAULT_HEAD_SHARE_MAX
    tail_max = DEFAULT_TAIL_SHARE_MAX
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        if isinstance(baseline.get("head_share_max"), (int, float)):
            head_max = float(baseline["head_share_max"])
        if isinstance(baseline.get("tail_share_max"), (int, float)):
            tail_max = float(baseline["tail_share_max"])

    findings = []
    if dist["head_share"] > head_max:
        findings.append({
            "severity": "advisory", "code": ISSUE_HEAD,
            "metrics": {"head_share": dist["head_share"], "head_max": head_max},
            "suggestion": (f"前 25% chapter_position 累计 summary_mass={dist['head_share']:.3f}"
                           f" > {head_max}·开篇摘述过度·中后段叙事质量稀薄")
        })
    if dist["tail_share"] > tail_max:
        findings.append({
            "severity": "advisory", "code": ISSUE_TAIL,
            "metrics": {"tail_share": dist["tail_share"], "tail_max": tail_max},
            "suggestion": (f"末 25% chapter_position 累计 summary_mass={dist['tail_share']:.3f}"
                           f" > {tail_max}·末段堆砌反思·主线 mass 后置")
        })

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "summary_mass_distribution",
        "scan_ts": ts,
        "mode": mode,
        "baseline_source": baseline_source,
        "baseline": {"head_share_max": head_max, "tail_share_max": tail_max},
        "distribution": dist,
        "findings": findings,
        "summary": {
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
        },
        "_placeholder": True,
        "_doc": "SBERT-zh 真嵌入 defer · 当前用 cluster summary 长度近似 mass",
    }
    out_path = out_dir / f"summary_mass_distribution_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    snap = {
        "head_share": dist["head_share"],
        "tail_share": dist["tail_share"],
        "advisory_codes": [f["code"] for f in findings],
    }
    snap_path = out_dir / "summary_mass_distribution_snapshot.json"
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[summary_mass_distribution] clusters={len(clusters)} head={dist['head_share']:.3f}"
          f" tail={dist['tail_share']:.3f} findings={len(findings)}")
    if mode == "shadow":
        for f in findings:
            print(f"[SHADOW] summary_mass: {f['code']} — 不上报", file=sys.stderr)
        sys.exit(0)
    if findings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
