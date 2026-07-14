#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-07 S5 FlawedFictions 反向校准 runner（测漏报召回矩阵）
"""flawed_fiction_runner.py — 把 flawed_fiction_maker 产出的受控穿帮样本喂给
三层一致性栈的**可脚本化两层**，产「各层 × 各注入类型」召回矩阵。

【被测层】（第三层 Claude judge / reflector 维度9 不可脚本化——由主代理亲自
人工核查多跳样本并把结果并进报告，不在本脚本内假冒）：
  L1a locked_fact_numeric   locked_fact_cross_scene_scanner 顶层恒定数值通路（hard_gate）
  L1b locked_fact_nli       同 scanner `descriptive` NLI 通路（advisory·需 RUOYU_NN_NLI=1
                            + LOCKED_FACT_DESCRIPTIVE_MODE=active·缺环境则诚实记 skip）
  L2a temporal_order        draft_temporal_order_scanner（active）
  L2b spatial_continuity    spatial_continuity_scanner（active）

【判中规则】（对 ground_truth 锚精确匹配，不数「随便报了点什么」——防假召回）：
  numeric   : conflicts[] 中存在 fact == violated_fact 且 conflict_value == expect 值
  nli       : descriptive.violations[] 中存在 fact == violated_fact 且命中句落在注入区间
  temporal  : violations[] 中存在 anchor_pair[1] 含 expect.reversal_anchor
  spatial   : violations[] 中存在 (from_location,to_location) ∈ expect.teleport_pairs
  同时记录 off_target 命中（报了但不是注入点——按误报计入 baseline/所有样本）。

用法：
  py core/ml/calibration/flawed_fiction_runner.py --samples-dir <maker 输出目录>
     --out <report.json> [--nli]
  --nli：设 RUOYU_NN_NLI=1 + LOCKED_FACT_DESCRIPTIVE_MODE=active 真跑 Erlangshen NLI
        （venv/checkpoint 缺失时 scanner 自身会诚实 skip，报告如实透传 note）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

LAYERS = ("locked_fact_numeric", "locked_fact_nli", "temporal_order", "spatial_continuity")
FLAW_TYPES = ("numeric", "direct_rewrite", "multi_hop", "temporal_reversal",
              "spatial_teleport", "baseline")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _in_span(pos, gt) -> bool:
    inj = gt.get("injection") or {}
    start = inj.get("char_offset")
    text = inj.get("injected_text") or ""
    if start is None or not isinstance(pos, int):
        return False
    return start <= pos <= start + len(text)


def _match_numeric(report: dict, gt: dict) -> tuple[bool, int]:
    """→ (注入点命中, off_target 命中数)。"""
    hits = report.get("conflicts") or []
    want_fact = gt.get("violated_fact")
    want_val = (gt.get("expect") or {}).get("conflict_value")
    on = 0
    for c in hits:
        if c.get("fact") == want_fact and c.get("conflict_value") == want_val \
                and _in_span(c.get("position"), gt):
            on += 1
    return on > 0, len(hits) - on


def _match_nli(report: dict, gt: dict) -> tuple[bool, int, str | None]:
    """→ (注入点命中, off_target 数, skip 原因)。shadow/未执行时 violations 恒空，
    以 executed/note 区分「真没检出」vs「环境没跑」——绝不把 skip 记成漏报以外的东西，
    但 skip 原因必须透传（诚实报告）。"""
    desc = report.get("descriptive") or {}
    if not desc.get("executed"):
        return False, 0, desc.get("note") or "descriptive path not executed"
    hits = desc.get("violations") or []
    want_fact = gt.get("violated_fact")
    on = 0
    for v in hits:
        if v.get("fact") == want_fact and _in_span(v.get("position"), gt):
            on += 1
    return on > 0, len(hits) - on, None


def _match_temporal(report: dict, gt: dict) -> tuple[bool, int]:
    hits = report.get("violations") or []
    anchor = (gt.get("expect") or {}).get("reversal_anchor")
    on = 0
    for v in hits:
        pair = v.get("anchor_pair") or []
        if anchor and len(pair) == 2 and anchor in str(pair[1]):
            on += 1
    return on > 0, len(hits) - on


def _match_spatial(report: dict, gt: dict) -> tuple[bool, int]:
    hits = report.get("violations") or []
    want = {tuple(p) for p in (gt.get("expect") or {}).get("teleport_pairs") or []}
    on = 0
    for v in hits:
        if (v.get("from_location"), v.get("to_location")) in want:
            on += 1
    return on > 0, len(hits) - on


def run_sample(sample_dir: Path) -> dict:
    """单样本过 4 个可脚本化层。project_root = 样本目录自身（fixture _数据库 在其下）。"""
    import locked_fact_cross_scene_scanner as lf
    import draft_temporal_order_scanner as dt
    import spatial_continuity_scanner as sp

    gt = _load_json(sample_dir / "ground_truth.json")
    draft = sample_dir / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"

    lf_report = lf.scan(sample_dir, draft)
    dt_report = dt.scan(draft, project_root=sample_dir, cluster_arg="cluster_001")
    sp_report = sp.scan(draft, project_root=sample_dir, cluster_arg="cluster_001")

    num_hit, num_off = _match_numeric(lf_report, gt)
    nli_hit, nli_off, nli_skip = _match_nli(lf_report, gt)
    t_hit, t_off = _match_temporal(dt_report, gt)
    s_hit, s_off = _match_spatial(sp_report, gt)

    return {
        "sample_id": gt["sample_id"],
        "flaw_type": gt["flaw_type"],
        "expected_layer": gt.get("expected_layer"),
        "expected_deterministic_detect": gt.get("expected_deterministic_detect"),
        "detected": {"locked_fact_numeric": num_hit, "locked_fact_nli": nli_hit,
                     "temporal_order": t_hit, "spatial_continuity": s_hit},
        "off_target": {"locked_fact_numeric": num_off, "locked_fact_nli": nli_off,
                       "temporal_order": t_off, "spatial_continuity": s_off},
        "nli_skip_reason": nli_skip,
        "nli_pair_selection": (lf_report.get("descriptive") or {}).get("pair_selection"),
        "nli_pairs_sent": (lf_report.get("descriptive") or {}).get("pairs_sent"),
        "raw_counts": {
            "numeric_conflicts": lf_report.get("conflicts_count", 0),
            "nli_violations": len((lf_report.get("descriptive") or {}).get("violations") or []),
            "temporal_violations": len(dt_report.get("violations") or []),
            "spatial_violations": len(sp_report.get("violations") or []),
        },
    }


def build_matrix(results: list[dict]) -> dict:
    """召回矩阵：flaw_type × layer → hit/total。baseline 行给出误报计数。"""
    matrix: dict[str, dict] = {}
    for ft in FLAW_TYPES:
        rows = [r for r in results if r["flaw_type"] == ft]
        if not rows:
            continue
        if ft == "baseline":
            matrix[ft] = {"n": len(rows), "false_positive_counts": {
                layer: sum(r["raw_counts"][k] for r in rows)
                for layer, k in (("locked_fact_numeric", "numeric_conflicts"),
                                 ("locked_fact_nli", "nli_violations"),
                                 ("temporal_order", "temporal_violations"),
                                 ("spatial_continuity", "spatial_violations"))}}
            continue
        matrix[ft] = {"n": len(rows)}
        for layer in LAYERS:
            hits = sum(1 for r in rows if r["detected"][layer])
            matrix[ft][layer] = {"hit": hits, "total": len(rows),
                                 "recall": round(hits / len(rows), 3)}
    return matrix


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FlawedFictions 反向校准 runner（漏报召回矩阵）")
    ap.add_argument("--samples-dir", required=True, help="flawed_fiction_maker --out-dir")
    ap.add_argument("--out", required=True, help="报告 JSON 输出路径")
    ap.add_argument("--nli", action="store_true",
                    help="开 NLI 描述类通路（RUOYU_NN_NLI=1 + LOCKED_FACT_DESCRIPTIVE_MODE=active）")
    args = ap.parse_args(argv)

    os.environ["DRAFT_TEMPORAL_ORDER_MODE"] = "active"
    os.environ["SPATIAL_CONTINUITY_MODE"] = "active"
    if args.nli:
        os.environ["RUOYU_NN_NLI"] = "1"
        os.environ["LOCKED_FACT_DESCRIPTIVE_MODE"] = "active"
    else:
        os.environ.setdefault("LOCKED_FACT_DESCRIPTIVE_MODE", "shadow")

    root = Path(args.samples_dir)
    manifest = _load_json(root / "manifest.json")
    results = []
    for entry in manifest["samples"]:
        s_dir = Path(entry["dir"])
        if not s_dir.is_absolute():
            s_dir = root / "samples" / entry["sample_id"]
        print(f"[runner] {entry['sample_id']} ...", file=sys.stderr)
        results.append(run_sample(s_dir))

    report = {
        "schema": "flawed_fiction_recall_report_v1",
        "nli_enabled": bool(args.nli),
        "base_draft_sha256": manifest.get("base_draft_sha256"),
        "matrix": build_matrix(results),
        "per_sample": results,
        "judge_layer_note": ("多跳型的承接层是 Claude judge（reflector 维度9），不可脚本化；"
                            "其人工核查结果由主代理并入最终报告，本 JSON 只含可脚本化层。"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["matrix"], ensure_ascii=False, indent=2))
    print(f"[runner] report → {out}")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
