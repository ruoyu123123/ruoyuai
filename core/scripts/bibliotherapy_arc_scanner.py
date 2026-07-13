#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bibliotherapy_arc_scanner.py — Shrodes 三阶段读者转化(R19 W8 Batch-W·P1)

【缺口·2026-06-21·Shrodes 1949 三阶段 bibliotherapy】
Shrodes 提出读者通过文学转化的三阶段：
  ① identification (认同)    : 读者与角色/处境共鸣
  ② catharsis      (宣泄)    : 情绪释放/共情爆发
  ③ insight        (洞察)    : 反思/获得新理解·价值观重塑

【输入】cluster 草稿(CLUSTER_MODE=1 env)+ cluster brief
  cluster.bibliotherapy_arc = {
    "identification": [0, 1],   // scene 索引(0-based)
    "catharsis":      [2, 3],
    "insight":        [4],
  }

【探针】
  - 三相全填 → 校验各相 scene 索引在 storyboard 范围内
  - 任一相缺 → BIBLIOTHERAPY_ARC_TRIAD_MISSING advisory
  - 三相顺序错位(identification 在 catharsis 之后) → 同 code

【北极星⑤】顾问非法官·全 advisory·env BIBLIOTHERAPY_ARC_MODE 默认 shadow·
  BIBLIOTHERAPY_ARC_TRIAD_MISSING 绝不 hard_gate。

【与既有 scanner 严格正交】
  - EC vs PD                  : 共情 vs 个人痛苦(scene 级·正交)
  - thematic_argument         : 主题论证(论点级·正交)
  - opening_window_milestone  : 开场钩(章前 1500 CJK·正交)
  - motif_recurrence          : 母题再现(意象级·正交)
  - Hurst exponent            : 长记忆(全文统计·正交)

用法: python bibliotherapy_arc_scanner.py <draft> [--project <root>] [--cluster-brief <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "BIBLIOTHERAPY_ARC_TRIAD_MISSING"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 800


def _mode() -> str:
    m = (os.environ.get("BIBLIOTHERAPY_ARC_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_brief(project_root, cluster_brief_path):
    """读 cluster brief·从中提取 bibliotherapy_arc 字段。"""
    data = None
    if cluster_brief_path and Path(cluster_brief_path).exists():
        try:
            data = json.loads(Path(cluster_brief_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None and project_root:
        # 兜底从 _数据库/事件簇.json clusters[0] 取
        p = Path(project_root) / "_数据库" / "事件簇.json"
        if p.exists():
            try:
                ec = json.loads(p.read_text(encoding="utf-8"))
                clusters = (ec or {}).get("clusters") or []
                if clusters:
                    data = clusters[0]
            except (OSError, json.JSONDecodeError):
                data = None
    if not isinstance(data, dict):
        return None
    arc = data.get("bibliotherapy_arc")
    storyboard = data.get("scene_storyboard") or []
    return {"arc": arc if isinstance(arc, dict) else None,
            "storyboard_len": len(storyboard) if isinstance(storyboard, list) else 0}


def _validate_arc(arc, storyboard_len):
    """三相全填 + scene 索引合法 + 顺序正确。返回 (missing_phases, out_of_range, order_err)。"""
    missing = []
    out_of_range = []
    order_err = None
    if not isinstance(arc, dict):
        return ["identification", "catharsis", "insight"], [], None

    phase_maxes = {}
    for phase in ("identification", "catharsis", "insight"):
        idx_list = arc.get(phase)
        if not isinstance(idx_list, list) or not idx_list:
            missing.append(phase)
            continue
        # 索引合法性
        valid_ids = [i for i in idx_list if isinstance(i, int)]
        if storyboard_len > 0:
            bad = [i for i in valid_ids if i < 0 or i >= storyboard_len]
            if bad:
                out_of_range.append({"phase": phase, "bad": bad})
        if valid_ids:
            phase_maxes[phase] = max(valid_ids)

    # 顺序：identification 最大值 <= catharsis 最小值 <= insight 最小值
    if not missing:
        id_max = max(arc["identification"])
        ca_min = min(arc["catharsis"])
        ca_max = max(arc["catharsis"])
        in_min = min(arc["insight"])
        if id_max > ca_min:
            order_err = f"identification(max={id_max}) 在 catharsis(min={ca_min}) 之后"
        elif ca_max > in_min:
            order_err = f"catharsis(max={ca_max}) 在 insight(min={in_min}) 之后"

    return missing, out_of_range, order_err


def scan(draft_path, project_root=None, cluster_brief_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "bibliotherapy_arc", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
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

    brief = _load_brief(project_root, cluster_brief_path)
    if brief is None:
        out["note"] = "无 cluster brief·skip"
        return out

    arc = brief["arc"]
    storyboard_len = brief["storyboard_len"]
    missing, out_of_range, order_err = _validate_arc(arc, storyboard_len)

    out["metrics"] = {
        "storyboard_len": storyboard_len,
        "has_arc": arc is not None,
        "missing_phases": missing,
        "out_of_range_phases": out_of_range,
        "order_error": order_err,
    }

    findings = []
    if missing:
        findings.append(f"三相缺位: {missing}")
    if out_of_range:
        findings.append(f"scene 索引越界: {out_of_range}")
    if order_err:
        findings.append(f"三相顺序错位: {order_err}")

    if findings:
        msg = " · ".join(findings)
        if mode == "active":
            out["violations"].append({
                "kind": "bibliotherapy_arc", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "metrics": out["metrics"],
                "_doc": "R19 W8 Batch-W·Shrodes 三阶段·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] bibliotherapy_arc[{ISSUE_CODE}]: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-W·Shrodes 三阶段·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-brief", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.cluster_brief)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
