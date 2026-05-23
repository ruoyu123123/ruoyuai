"""agent_drift_monitor.py — Agent Stability Index（v22.5 L19）

业界 arxiv 2601.04170 Agent Drift + MI9 framework：
监测 agent 行为随时间漂移（不是角色漂移，是 agent 自己的行为）。

6 维度 ASI（简化版）：
1. OUTPUT_LENGTH_STABILITY: agent 输出长度趋势是否稳
2. STRUCTURED_FIELD_COMPLETENESS: _changes.json schema 字段完整率
3. ERROR_RATE_TREND: agent 失败/超时率
4. WAIVER_RATE_TREND: writer 用 waiver 频次趋势
5. RETRY_RATE_TREND: 同章节 audit 次数趋势
6. RESPONSE_TIME_TREND（路线图，需 God Log）

输出：_数据库/.learning/agent_drift_<ts>.json
退出码: 0 健康 / 1 advisory drift / 2 warning 显著漂移
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check_output_length_stability(project_root: Path, chs: list[int]) -> dict:
    """维度 1: writer 输出长度稳定性"""
    lengths = []
    for ch in chs:
        text_p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
        if text_p.exists():
            cn = len(re.findall(r"[一-鿿]", text_p.read_text(encoding="utf-8")))
            lengths.append((ch, cn))
    if len(lengths) < 5:
        return {"status": "insufficient_data"}
    counts = [c for _, c in lengths]
    mean = sum(counts) / len(counts)
    std = (sum((c - mean) ** 2 for c in counts) / len(counts)) ** 0.5
    cv = std / mean if mean > 0 else 0
    finding = None
    if cv > 0.35:
        finding = {
            "code": "OUTPUT_LENGTH_DRIFT",
            "cv": round(cv, 2),
            "msg": f"writer 字数 CV {cv:.2f} > 0.35 → 输出长度不稳",
        }
    return {"cv": round(cv, 2), "mean": round(mean), "finding": finding}


def check_schema_completeness(project_root: Path, chs: list[int]) -> dict:
    """维度 2: _changes.json 关键字段完整率"""
    required_fields_factual = ["locked_facts", "relationships"]
    required_fields_self_eval = ["applied_style"]
    total = 0
    missing = Counter()
    for ch in chs:
        cp = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
        if cp.exists():
            total += 1
            changes = load_json(cp, {})
            fact = changes.get("factual", {}) or {}
            seval = changes.get("self_eval", {}) or {}
            for f in required_fields_factual:
                if f not in fact:
                    missing[f"factual.{f}"] += 1
            for f in required_fields_self_eval:
                if f not in seval:
                    missing[f"self_eval.{f}"] += 1
    if total < 3:
        return {"status": "insufficient_data"}
    completeness = 1.0 - sum(missing.values()) / (total * (len(required_fields_factual) + len(required_fields_self_eval)))
    finding = None
    if completeness < 0.8:
        finding = {
            "code": "SCHEMA_COMPLETENESS_DRIFT",
            "completeness": round(completeness, 2),
            "missing_fields": dict(missing),
            "msg": f"changes schema 完整率 {completeness:.0%} < 80% → writer 越来越偷懒",
        }
    return {"completeness": round(completeness, 2), "missing": dict(missing), "finding": finding}


def check_waiver_trend(project_root: Path, chs: list[int]) -> dict:
    """维度 4: waiver 用量趋势"""
    if len(chs) < 6:
        return {"status": "insufficient_data"}
    waivers_per_ch = []
    for ch in chs:
        cp = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
        if cp.exists():
            d = load_json(cp, {})
            w = (d.get("self_eval", {}) or {}).get("waivers", []) or []
            waivers_per_ch.append((ch, len(w)))
    if len(waivers_per_ch) < 6:
        return {"status": "insufficient_data"}
    half = len(waivers_per_ch) // 2
    first = sum(w for _, w in waivers_per_ch[:half]) / half
    second = sum(w for _, w in waivers_per_ch[half:]) / (len(waivers_per_ch) - half)
    finding = None
    if second > first + 2:
        finding = {
            "code": "WAIVER_RATE_INFLATION",
            "first_half_avg": round(first, 1),
            "second_half_avg": round(second, 1),
            "msg": f"waiver 使用从 {first:.1f}/章 升到 {second:.1f}/章 → writer 越来越用 waiver 逃避",
        }
    return {"first_half_avg": round(first, 1), "second_half_avg": round(second, 1), "finding": finding}


def check_retry_trend(project_root: Path, chs: list[int]) -> dict:
    """维度 5: audit 重试率趋势"""
    audit_dir = project_root / "_数据库" / ".audit"
    if not audit_dir.exists():
        return {"status": "no_audit_data"}
    per_ch_audit = Counter()
    for f in audit_dir.glob("ch_*_audit*.json"):
        m = re.match(r"ch_(\d+)", f.name)
        if m:
            ch = int(m.group(1))
            if ch in chs:
                per_ch_audit[ch] += 1
    if not per_ch_audit or len(per_ch_audit) < 4:
        return {"status": "insufficient_data"}
    sorted_chs = sorted(per_ch_audit.items())
    half = len(sorted_chs) // 2
    first = sum(c for _, c in sorted_chs[:half]) / half
    second = sum(c for _, c in sorted_chs[half:]) / (len(sorted_chs) - half)
    finding = None
    if second > first * 1.5 and second >= 2.5:
        finding = {
            "code": "RETRY_RATE_INFLATION",
            "first_half_avg": round(first, 1),
            "second_half_avg": round(second, 1),
            "msg": f"audit 次数从 {first:.1f}/章 升 {second:.1f}/章 → writer 不稳定",
        }
    return {"first_half_avg": round(first, 1), "second_half_avg": round(second, 1), "finding": finding}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=20)
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    recent = chapters[-args.last_n:] if chapters else []
    if not recent:
        print("[SKIP] 无章节")
        sys.exit(0)

    findings = []
    dimensions = {
        "output_length": check_output_length_stability(project_root, recent),
        "schema_completeness": check_schema_completeness(project_root, recent),
        "waiver_trend": check_waiver_trend(project_root, recent),
        "retry_trend": check_retry_trend(project_root, recent),
    }
    for d in dimensions.values():
        if d.get("finding"):
            findings.append(d["finding"])

    out = {
        "scan_type": "agent_drift_monitor",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "chs_window": recent,
        "dimensions": dimensions,
        "findings": findings,
        "asi_score": max(0, 1.0 - len(findings) * 0.2),  # 简化 ASI
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"agent_drift_{ts}.json"
    save_json(out_path, out)
    print(f"[agent_drift_monitor] ASI={out['asi_score']:.2f} findings={len(findings)}")
    for f in findings:
        print(f"  [{f['code']}] {f.get('msg', '')[:80]}")
    print(f"  报告: {out_path}")
    sys.exit(2 if len(findings) >= 3 else (1 if findings else 0))


if __name__ == "__main__":
    main()
