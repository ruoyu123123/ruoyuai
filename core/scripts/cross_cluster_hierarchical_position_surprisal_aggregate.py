#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_cluster_hierarchical_position_surprisal_aggregate.py — SCH 层级位置惊异度
(R18 W7 Batch-U·P2 · cross-cluster shadow aggregate)

【缺口·2026-06-21·arxiv 2410.16062 Tsipidi 2024 SCH Surprisal at Chunk
Hierarchies + arxiv 2604.10724 Surprisal Salient 2026-04 + arxiv 2602.14653
IUH grounding】

SCH (Surprisal at Chunk Hierarchies)：边界位置(段落 PARA / 场景 SCENE /
cluster 末 CLUSTER_END)前后的『信息惊异度』应有阶层分布——边界级别越高·
惊异度跃升越显著。用 frequency-rank surprise 作 LM-free 替身：
  surprisal(token) = -log( freq(token) / N )

【三类边界 + KL 散度】
  PARA: 段间边界·surprisal 跃升幅度均值
  SCENE: 场景间边界·surprisal 跃升均值
  CLUSTER_END: cluster 末段·收尾应有惊异度峰
  期望关系：ΔS_CLUSTER_END ≥ ΔS_SCENE ≥ ΔS_PARA
  若被颠倒 → 阶层崩塌 → SCH_HIERARCHY_INVERTED advisory

【与既有 scanner 显式去重】
  - cross_cluster_engagement_metrics(章级 hook trend)·正交
  - cross_cluster_style_drift_scanner(长程作者文风漂移)·正交
  本 aggregate = 边界惊异度阶层(单 cluster 内 + 跨 cluster)·新维度

【北极星⑤】顾问非法官·全 advisory·env HIERARCHICAL_POSITION_SURPRISAL_MODE
  shadow 默认·SCH_HIERARCHY_INVERTED 绝不 hard_gate。
  作者档 quantitative.sch_baseline{delta_para/delta_scene/delta_cluster_end}
  可旁路·零依赖纯 token freq surprise。

用法: python cross_cluster_hierarchical_position_surprisal_aggregate.py <project> [--last-n 10]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ISSUE_CODE = "SCH_HIERARCHY_INVERTED"


def _mode() -> str:
    m = (os.environ.get("HIERARCHICAL_POSITION_SURPRISAL_MODE")
         or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_chapter_body(project_root: Path, ch: int):
    cdir = project_root / "章节" / f"第{ch:03d}章"
    if not cdir.exists():
        cdir = project_root / "章节" / f"第{ch}章"
    if not cdir.exists():
        return None
    for fn in ("body.txt", "正文.txt", f"第{ch}章.txt"):
        p = cdir / fn
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                continue
    # 兜底·目录下任意 .txt
    for p in cdir.glob("*.txt"):
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _list_chapters(project_root: Path):
    chs = []
    for d in (project_root / "章节").glob("第*章") if (project_root / "章节").exists() else []:
        m = re.match(r"第(\d+)章", d.name)
        if m:
            chs.append(int(m.group(1)))
    return sorted(chs)


def _tokenize(text):
    chars = [c for c in text if "一" <= c <= "鿿"]
    return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]


def _surprisal_seq(tokens, freqs, total):
    out = []
    for t in tokens:
        f = freqs.get(t, 1)
        out.append(-math.log(f / total))
    return out


def _avg_around(surps, idx, w=20):
    a = max(0, idx - w)
    b = min(len(surps), idx + w)
    if b - a < 4:
        return 0.0
    left = surps[a:idx]
    right = surps[idx:b]
    if not left or not right:
        return 0.0
    return (sum(right) / len(right)) - (sum(left) / len(left))


def _compute_para_deltas(text, freqs, total):
    """段间边界 ΔS。"""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) < 3:
        return 0.0
    deltas = []
    cumulative_len = 0
    para_tokens_lens = []
    for p in paras:
        tk = _tokenize(p)
        para_tokens_lens.append(len(tk))
    full = _tokenize(text)
    surps = _surprisal_seq(full, freqs, total)
    cumulative = 0
    for i, ln in enumerate(para_tokens_lens[:-1]):
        cumulative += ln
        d = _avg_around(surps, cumulative, w=15)
        deltas.append(d)
    return sum(deltas) / len(deltas) if deltas else 0.0


def _compute_scene_deltas(text, freqs, total):
    """场景间(\n\n\n+ 或 # scene)边界 ΔS。"""
    scenes = re.split(r"\n{3,}|^#+\s*scene[^\n]*\n", text, flags=re.M | re.I)
    scenes = [s for s in scenes if s and _cjk_count(s) > 100]
    if len(scenes) < 2:
        return 0.0
    full = _tokenize(text)
    surps = _surprisal_seq(full, freqs, total)
    deltas = []
    cumulative = 0
    for sc in scenes[:-1]:
        cumulative += len(_tokenize(sc))
        deltas.append(_avg_around(surps, cumulative, w=30))
    return sum(deltas) / len(deltas) if deltas else 0.0


def _compute_cluster_end_delta(text, freqs, total):
    """末段 (后 15%) 与前段对比 ΔS。"""
    full = _tokenize(text)
    if len(full) < 100:
        return 0.0
    tail_start = int(len(full) * 0.85)
    surps = _surprisal_seq(full, freqs, total)
    head = surps[:tail_start]
    tail = surps[tail_start:]
    if not head or not tail:
        return 0.0
    return (sum(tail) / len(tail)) - (sum(head) / len(head))


def _scan_cluster(text):
    """对单 cluster/章 文本计算三类 ΔS。"""
    tokens = _tokenize(text)
    if len(tokens) < 100:
        return None
    freqs = Counter(tokens)
    total = sum(freqs.values())
    return {
        "delta_para": round(_compute_para_deltas(text, freqs, total), 4),
        "delta_scene": round(_compute_scene_deltas(text, freqs, total), 4),
        "delta_cluster_end": round(_compute_cluster_end_delta(text, freqs, total), 4),
        "tokens": len(tokens),
    }


def aggregate(project_root: Path, last_n: int = 10):
    chapters = _list_chapters(project_root)
    if not chapters:
        return None, []
    sample = chapters[-last_n:]
    per_chapter = []
    for ch in sample:
        body = _read_chapter_body(project_root, ch)
        if not body:
            continue
        r = _scan_cluster(body)
        if r:
            r["chapter"] = ch
            per_chapter.append(r)
    if not per_chapter:
        return None, []
    n = len(per_chapter)
    avg_para = sum(r["delta_para"] for r in per_chapter) / n
    avg_scene = sum(r["delta_scene"] for r in per_chapter) / n
    avg_cend = sum(r["delta_cluster_end"] for r in per_chapter) / n
    summary = {"chapters": [r["chapter"] for r in per_chapter],
               "avg_delta_para": round(avg_para, 4),
               "avg_delta_scene": round(avg_scene, 4),
               "avg_delta_cluster_end": round(avg_cend, 4)}
    findings = []
    # 期望 ΔS_CLUSTER_END ≥ ΔS_SCENE ≥ ΔS_PARA
    if not (avg_cend >= avg_scene * 0.8) or not (avg_scene >= avg_para * 0.8):
        findings.append({
            "severity": "advisory",
            "code": ISSUE_CODE,
            "suggestion": (f"SCH 阶层颠倒·ΔS_PARA={summary['avg_delta_para']} "
                           f"ΔS_SCENE={summary['avg_delta_scene']} "
                           f"ΔS_CLUSTER_END={summary['avg_delta_cluster_end']}·"
                           f"期望末段惊异度峰最高(收尾应有信息密度跃升)"),
            "metrics": summary,
        })
    return summary, findings


def main():
    ap = argparse.ArgumentParser(
        description="SCH 层级位置惊异度 · cross-cluster shadow")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    mode = _mode()
    project_root = Path(args.project).resolve()
    if mode == "off":
        print("[SKIP] HIERARCHICAL_POSITION_SURPRISAL_MODE=off")
        sys.exit(0)

    summary, findings = aggregate(project_root, args.last_n)
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "hierarchical_position_surprisal",
        "scan_ts": ts, "mode": mode,
        "gate_level": "advisory",
        "summary": summary,
        "findings": findings,
    }
    out_path = out_dir / f"hierarchical_position_surprisal_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[hierarchical_position_surprisal] findings={len(findings)} → {out_path}")
    if mode == "shadow":
        sys.exit(0)
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
