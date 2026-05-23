"""cross_chapter_fate_drift_scan.py — 大势漂移扫描（v20 F8 新增）

复用 fate_engine.drift 检测「prereq 完成 + 超 expected_window_after 仍未触发」事件。
输出报告 + 强烈告警「下章必须推进」。

用法：python cross_chapter_fate_drift_scan.py <project> [--ch N | --auto]
退出码: 0 健康 / 1 advisory / 2 warning 超期严重
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fate_engine  # type: ignore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--auto", action="store_true", help="自动取最近章")
    args = ap.parse_args()

    project_root = Path(args.project)

    # 决定 ch
    ch = args.ch
    if ch is None or args.auto:
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        ch = chapters[-1]

    drift_result = fate_engine.drift(project_root, ch)
    overdue = drift_result.get("overdue_events", [])

    findings = []
    for od in overdue:
        sev = "warning" if od["overdue_by"] >= 5 else "advisory"
        findings.append({
            "severity": sev,
            "code": "FATE_EVENT_OVERDUE",
            "metric": od,
            "message": f"大事件「{od['title']}」({od['event_id']}) 已超 expected_window {od['overdue_by']} 章未触发",
            "suggestion": f"下章 writer 必须推进 {od['event_id']}：{od['title']}",
        })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "fate_drift",
        "scan_ts": ts,
        "ch": ch,
        "findings": findings,
        "summary": {
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "total": len(findings),
        },
    }
    out_path = out_dir / f"fate_drift_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[fate_drift_scan] ch{ch}: {len(findings)} 项漂移")
    for f in findings[:5]:
        print(f"  [{f['severity'].upper()}] {f['message']}")
    print(f"报告: {out_path}")

    if any(f["severity"] == "warning" for f in findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
