"""hub_rhythm_check.py — Hub 节奏分布扫描（v21 R2.1 新增）

借鉴 IF Hub + Quest 模式：检查最近 N 章 role 分布是否偏离 rhythm_targets。
偏离过多 → 告警下章应转什么 role。

输出：_数据库/.cross_chapter_scan/hub_rhythm_<ts>.json

退出码: 0 健康 / 1 advisory 节奏偏离 / 2 致命
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)
    hubs_path = project_root / "_数据库" / "枢纽场景.json"
    if not hubs_path.exists():
        print("[SKIP] 枢纽场景.json 不存在")
        sys.exit(0)
    data = load_json(hubs_path, {})

    log = data.get("chapter_hub_log", [])
    targets = data.get("rhythm_targets", {})
    tol = targets.get("tolerance", 0.15)

    recent = log[-args.last_n:]
    if not recent:
        print("[SKIP] 章节 hub log 为空")
        sys.exit(0)

    counts = Counter(e.get("role") for e in recent)
    total = len(recent)
    actual = {k: counts.get(k, 0) / total for k in ["depart", "quest", "return", "idle"]}

    findings = []
    for role, target_pct in [
        ("depart", targets.get("depart_pct_target", 0.2)),
        ("quest", targets.get("quest_pct_target", 0.5)),
        ("return", targets.get("return_pct_target", 0.2)),
        ("idle", targets.get("idle_pct_target", 0.1)),
    ]:
        diff = actual[role] - target_pct
        if abs(diff) > tol:
            findings.append({
                "severity": "advisory",
                "code": "HUB_RHYTHM_DEVIATION",
                "role": role,
                "actual_pct": round(actual[role], 2),
                "target_pct": target_pct,
                "diff": round(diff, 2),
                "suggestion": f"近 {total} 章 {role} 占比 {actual[role]:.0%}，目标 {target_pct:.0%}（容忍 ±{tol:.0%}）。{'建议下章选 ' + role if diff < 0 else '建议下章不再 ' + role}",
            })

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"hub_rhythm_{ts}.json"
    report = {
        "scan_type": "hub_rhythm",
        "scan_ts": ts,
        "last_n": total,
        "actual_distribution": actual,
        "targets": {
            "depart": targets.get("depart_pct_target"),
            "quest": targets.get("quest_pct_target"),
            "return": targets.get("return_pct_target"),
            "idle": targets.get("idle_pct_target"),
        },
        "tolerance": tol,
        "findings": findings,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[hub_rhythm] {total} 章: depart={actual['depart']:.0%} quest={actual['quest']:.0%} return={actual['return']:.0%} idle={actual['idle']:.0%}")
    for f in findings:
        print(f"  [{f['severity'].upper()}] {f['suggestion']}")
    print(f"报告: {out_path}")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
