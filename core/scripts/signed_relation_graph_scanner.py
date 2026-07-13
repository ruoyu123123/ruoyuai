#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signed_relation_graph_scanner.py — 关系签名图+LLM 抱团正向 bias 哨兵
(advisory · cluster · 2026-06-20 R11 W6 STRONG)

【缺口】NeurIPS 2025 LLM Lifecycle Workshop arXiv:2510.18932 + EMNLP 2022 arXiv:2211.00676
报告：LLM 生成多人物叙事时显著倾向把社交图正极性化（"大家都互相喜欢"），
clustering_coefficient 与正比例同步偏高，与真实作家平均偏中性/负峰分布显著不同。

【做法 · 确定性纯规则·零依赖·零 LLM/零联网】：
  1. 项目人物卡.json 抓 known_names（无 → skip 北极星②）
  2. 草稿正文按段落+句子窗口扫每对人物共现
  3. 共现窗口内查 POS_MARKERS / NEG_MARKERS 极性词 → 边权 +1 / -1 / 0
  4. 算三指标：graph_density / clustering_coefficient / mean_positivity_ratio
  5. vs 作者档 signed_graph_baseline z-band → advisory
     SIGNED_GRAPH_OVERLY_COZY (mean_positivity_ratio 偏高 > z=+1.5)
     SIGNED_GRAPH_OVERLY_HOSTILE (mean_positivity_ratio 偏低 < z=-1.5)
  6. 作者档无基线 → 退兜底带 [0.45, 0.65]（人类作家均值约 0.55）

【北极星】② 作者档第一权威·⑤ 全 advisory shadow 默认·绝不 hard_gate

用法：python signed_relation_graph_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = ("SIGNED_GRAPH_OVERLY_COZY", "SIGNED_GRAPH_OVERLY_HOSTILE")
WINDOW_SENT = 3  # 窗口 = 同段 ±3 句

POS_MARKERS = re.compile(
    r"(笑|拥抱|搂|抱|扶|帮|护|救|赞|夸|敬|爱|喜欢|信任|温柔|和气|"
    r"感激|安慰|关切|呵护|理解|支持|默契)"
)
NEG_MARKERS = re.compile(
    r"(怒|骂|斥|打|杀|害|恨|怨|讥|讽|嘲|冷笑|鄙夷|敌视|杀意|"
    r"威胁|逼问|争执|翻脸|背叛|出卖|阴险|狞笑)"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_SENT_SPLIT = re.compile(r"(?<=[。！？……?!])")


def _mode() -> str:
    m = (os.environ.get("SIGNED_RELATION_GRAPH_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_known_names(project_root) -> list:
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    names = set()
    # 兼容多种 schema
    if isinstance(obj, dict):
        for k in ("known_names", "人物", "characters", "entries"):
            v = obj.get(k)
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, str):
                        names.add(it)
                    elif isinstance(it, dict):
                        n = it.get("name") or it.get("姓名") or it.get("名字")
                        if isinstance(n, str) and n.strip():
                            names.add(n.strip())
        # 顶层 key=name 形式
        for k, v in obj.items():
            if isinstance(v, dict) and ("name" in v or "姓名" in v):
                n = v.get("name") or v.get("姓名")
                if isinstance(n, str):
                    names.add(n.strip())
    return [n for n in names if 1 <= len(n) <= 8]


def _read_signed_baseline(project_root):
    """作者档 signed_graph_baseline {density, clustering, mean_positivity_ratio, sd}"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj.get("signed_graph_baseline")


def _split_sentences(text):
    parts = [s for s in _SENT_SPLIT.split(text) if s.strip()]
    return parts


def _co_occurrence_windows(text, names):
    """返回 [(name_a, name_b, polarity), ...]，polarity∈{+1,-1,0}"""
    edges = []
    # 段落
    paragraphs = [p for p in text.split("\n") if p.strip()]
    for para in paragraphs:
        sents = _split_sentences(para)
        for i, s in enumerate(sents):
            window = "".join(sents[max(0, i - WINDOW_SENT):i + WINDOW_SENT + 1])
            present = [n for n in names if n in window]
            if len(present) < 2:
                continue
            pos_hit = bool(POS_MARKERS.search(window))
            neg_hit = bool(NEG_MARKERS.search(window))
            if pos_hit and not neg_hit:
                pol = +1
            elif neg_hit and not pos_hit:
                pol = -1
            elif pos_hit and neg_hit:
                pol = 0  # 混合
            else:
                pol = 0  # 中性共现
            for a, b in itertools.combinations(sorted(set(present)), 2):
                edges.append((a, b, pol))
    return edges


def _aggregate_edges(edges):
    """聚合多窗口边为最终极性（多数胜出，等 → 0）"""
    agg = {}
    for a, b, p in edges:
        key = (a, b)
        if key not in agg:
            agg[key] = []
        agg[key].append(p)
    final = {}
    for k, ps in agg.items():
        pos = sum(1 for x in ps if x > 0)
        neg = sum(1 for x in ps if x < 0)
        if pos > neg:
            final[k] = +1
        elif neg > pos:
            final[k] = -1
        else:
            final[k] = 0
    return final


def _graph_metrics(final_edges, names_present):
    n = len(names_present)
    if n < 3 or not final_edges:
        return None
    max_edges = n * (n - 1) / 2
    density = round(len(final_edges) / max_edges, 4) if max_edges else 0.0
    polarities = list(final_edges.values())
    nonzero = [p for p in polarities if p != 0]
    if not nonzero:
        mean_pos = 0.5  # 全中性
    else:
        mean_pos = sum(1 for p in nonzero if p > 0) / len(nonzero)
    # 局部聚集系数(近似·三角形/路径)
    adj = {n_: set() for n_ in names_present}
    for (a, b) in final_edges:
        adj[a].add(b)
        adj[b].add(a)
    cc_sum = 0.0
    counted = 0
    for v in names_present:
        nbrs = adj.get(v, set())
        if len(nbrs) < 2:
            continue
        k = len(nbrs)
        links = 0
        for x, y in itertools.combinations(nbrs, 2):
            if y in adj.get(x, ()):
                links += 1
        cc_sum += 2 * links / (k * (k - 1))
        counted += 1
    cc = round(cc_sum / counted, 4) if counted else 0.0
    return {
        "graph_density": density,
        "clustering_coefficient": cc,
        "mean_positivity_ratio": round(mean_pos, 4),
        "n_chars_in_graph": n,
        "n_edges": len(final_edges),
    }


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "signed_relation_graph", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "violations": [], "verdict": "PASS",
           "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out
    names = _read_known_names(project_root)
    if not names:
        out["note"] = "无人物卡 known_names·跳过(北极星②)"
        return out
    edges = _co_occurrence_windows(text, names)
    final = _aggregate_edges(edges)
    present = sorted({n for e in final for n in e[:2]})
    metrics = _graph_metrics(final, present)
    if not metrics:
        out["note"] = "共现角色不足·跳过"
        return out
    out["metrics"] = metrics
    baseline = _read_signed_baseline(project_root) or {
        "mean_positivity_ratio": 0.55, "sd": 0.10,
        "_doc": "兜底带·人类作家均值约 0.55±0.10",
    }
    out["baseline"] = baseline
    mu = baseline.get("mean_positivity_ratio", 0.55)
    sd = max(baseline.get("sd", 0.10), 0.02)
    z = (metrics["mean_positivity_ratio"] - mu) / sd
    out["z_positivity"] = round(z, 3)
    msg = None
    code = None
    if z > 1.5:
        code = "SIGNED_GRAPH_OVERLY_COZY"
        msg = (f"关系图 mean_positivity_ratio={metrics['mean_positivity_ratio']} > 作者基线均值+{1.5}σ "
               f"(mu={mu}, sd={sd})·疑似 LLM 抱团正向 bias")
    elif z < -1.5:
        code = "SIGNED_GRAPH_OVERLY_HOSTILE"
        msg = (f"关系图 mean_positivity_ratio={metrics['mean_positivity_ratio']} < 作者基线均值-{1.5}σ "
               f"(mu={mu}, sd={sd})·疑似敌意过载")
    if msg and code:
        if mode == "active":
            out["violations"].append({
                "kind": "signed_relation_graph", "severity": "minor",
                "code": code, "message": msg,
                "metrics": metrics, "z_positivity": round(z, 3),
                "_doc": "关系签名图·advisory 待裁决·北极星⑤·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] signed_relation_graph: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="关系签名图+LLM 抱团正向 bias 哨兵(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
