#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_cluster_reader_retention_proxy_aggregate.py — 完读率反向代理
(R18 W7 Batch-U·P2 · cross-cluster shadow aggregate)

【缺口·2026-06-21·番茄完读率公开数据 + 起点新书期攻略 + Kindle Direct
Publishing 留存数据 + Webnovel platform locked chapters】

读者完读率(retention curve)是网文平台最权威的留存指标·但是事后数据。
本 aggregate 用 4 个已有 cluster 级指标合成 retention proxy 反向工艺指标：

  R = w1·hook_strength_norm + w2·(1-sagging_middle) + w3·cliffhanger_quota
      + w4·section_word_count_health

权重(默认 0.35 / 0.25 / 0.25 / 0.15)。

【与既有 scanner 显式去重】
  - W6 cross_cluster_engagement_metrics_aggregate(章级 hook trend)
    本 aggregate = retention 综合代理(覆盖 hook + sagging + cliffhanger + 长度)·
    正交输出。
  - cross_cluster_sagging_middle / cross_cluster_engagement_metrics
    是本 aggregate 的输入·不是替代品。

【北极星⑤】顾问非法官·全 advisory·env READER_RETENTION_PROXY_MODE
  RETENTION_PROXY_LOW 绝不 hard_gate·shadow 默认。

用法: python cross_cluster_reader_retention_proxy_aggregate.py <project> [--last-n 10]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ISSUE_CODE = "RETENTION_PROXY_LOW"

DEFAULT_WEIGHTS = {"hook": 0.35, "sagging": 0.25,
                   "cliffhanger": 0.25, "length": 0.15}

# proxy 警示阈值
RETENTION_LOW_FLOOR = 0.45


def _mode() -> str:
    m = (os.environ.get("READER_RETENTION_PROXY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_latest(scan_dir: Path, prefix: str):
    if not scan_dir.exists():
        return None
    files = sorted(scan_dir.glob(f"{prefix}_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_chapters(project_root: Path):
    chs = []
    if (project_root / "章节").exists():
        for d in (project_root / "章节").glob("第*章"):
            m = re.match(r"第(\d+)章", d.name)
            if m:
                chs.append(int(m.group(1)))
    return sorted(chs)


def _hook_score(scan_dir):
    """从 engagement_metrics 最近报告读 hook 均值 (0-10 → 0-1)。"""
    r = _read_latest(scan_dir, "engagement_metrics")
    if not r:
        return 0.5
    # 找 hook 类 finding
    scores = r.get("scores_collected") or {}
    hk = scores.get("hook")
    findings = r.get("findings") or []
    decline = sum(1 for f in findings
                  if (f.get("code") or "").startswith("HOOK"))
    base = 0.7 - 0.1 * decline
    return max(0.0, min(1.0, base))


def _sagging_score(scan_dir):
    """从 sagging_middle 报告·findings 数量越多分越低。"""
    r = _read_latest(scan_dir, "sagging_middle")
    if not r:
        return 0.5
    fnd = r.get("findings") or r.get("summary") or {}
    if isinstance(fnd, dict):
        n_findings = fnd.get("advisory", 0) + fnd.get("warning", 0) * 2
    elif isinstance(fnd, list):
        n_findings = len(fnd)
    else:
        n_findings = 0
    # 分=1-饱和(n_findings·0.15)
    return max(0.0, 1.0 - min(0.9, n_findings * 0.15))


def _cliffhanger_score(scan_dir):
    """从 engagement_metrics findings 里 cliffhanger quota 类。"""
    r = _read_latest(scan_dir, "engagement_metrics")
    if not r:
        return 0.5
    findings = r.get("findings") or []
    bad = sum(1 for f in findings if "CLIFFHANGER" in (f.get("code") or ""))
    return max(0.0, 1.0 - bad * 0.15)


def _length_health(project_root, chapters, last_n):
    """章字数集中度·过短过长拉低。"""
    if not chapters:
        return 0.5
    sample = chapters[-last_n:]
    lengths = []
    for ch in sample:
        cdir = project_root / "章节" / f"第{ch:03d}章"
        if not cdir.exists():
            cdir = project_root / "章节" / f"第{ch}章"
        if not cdir.exists():
            continue
        for fn in ("body.txt", "正文.txt"):
            p = cdir / fn
            if p.exists():
                try:
                    body = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                cjk = sum(1 for c in body if "一" <= c <= "鿿")
                lengths.append(cjk)
                break
    if not lengths:
        return 0.5
    # 3000-4500 健康
    ok = sum(1 for L in lengths if 3000 <= L <= 4500)
    return ok / len(lengths)


def aggregate(project_root: Path, last_n: int = 10, weights=None):
    weights = weights or DEFAULT_WEIGHTS
    scan_dir = project_root / "_数据库" / ".cross_chapter_scan"
    chapters = _list_chapters(project_root)
    if not chapters:
        return None, []

    h = _hook_score(scan_dir)
    s = _sagging_score(scan_dir)
    c = _cliffhanger_score(scan_dir)
    L = _length_health(project_root, chapters, last_n)

    proxy = (weights["hook"] * h + weights["sagging"] * s
             + weights["cliffhanger"] * c + weights["length"] * L)

    summary = {
        "chapters_examined": chapters[-last_n:],
        "hook_norm": round(h, 3),
        "sagging_inverse": round(s, 3),
        "cliffhanger": round(c, 3),
        "length_health": round(L, 3),
        "retention_proxy": round(proxy, 3),
        "weights": weights,
    }
    findings = []
    if proxy < RETENTION_LOW_FLOOR:
        findings.append({
            "severity": "advisory",
            "code": ISSUE_CODE,
            "suggestion": (f"retention proxy {round(proxy,3)} < {RETENTION_LOW_FLOOR}·"
                           f"hook={summary['hook_norm']} sagging_inv={summary['sagging_inverse']} "
                           f"cliff={summary['cliffhanger']} length={summary['length_health']}·"
                           f"建议优先修最低维度"),
            "metrics": summary,
        })
    return summary, findings


def main():
    ap = argparse.ArgumentParser(
        description="reader retention proxy · cross-cluster shadow")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    mode = _mode()
    project_root = Path(args.project).resolve()
    if mode == "off":
        print("[SKIP] READER_RETENTION_PROXY_MODE=off")
        sys.exit(0)

    summary, findings = aggregate(project_root, args.last_n)
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "reader_retention_proxy",
        "scan_ts": ts, "mode": mode,
        "gate_level": "advisory",
        "summary": summary, "findings": findings,
    }
    out_path = out_dir / f"reader_retention_proxy_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[reader_retention_proxy] findings={len(findings)} → {out_path}")
    if mode == "shadow":
        sys.exit(0)
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
