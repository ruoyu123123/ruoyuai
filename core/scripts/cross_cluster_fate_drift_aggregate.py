"""cross_cluster_fate_drift_aggregate.py — 大势漂移扫描（v20 F8 新增）

复用 fate_engine.drift 检测「prereq 完成 + 超 expected_window_after 仍未触发」事件。
输出报告 + 强烈告警「下章必须推进」。

用法：python cross_cluster_fate_drift_aggregate.py <project> [--ch N | --auto]
退出码: 0 健康 / 1 advisory / 2 warning 超期严重
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).parent))
import fate_engine  # type: ignore
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：账本取末 cluster 锚点章


def _anchor_ch_from_ledger(project_root: Path) -> int | None:
    """2026-05-29 cluster 化：从账本取末 cluster 的 chapter_range[1] 作为漂移锚点 ch。

    fate_drift 改造最轻 —— drift 逻辑全委托 fate_engine.drift，cluster 模式只是换了
    「锚点 ch 从哪来」：逐章模式 glob 章目录取最大章号，cluster 模式取账本末 cluster 末章。
    取不到（账本无 cluster / 末 cluster 未切章）返回 None → 调用方回退逐章 glob。
    """
    clusters = csr.get_clusters(project_root, last_n=1)
    if not clusters:
        return None
    last = clusters[-1]
    end = last.get("cluster_end_ch")
    if isinstance(end, int):
        return end
    cr = last.get("chapter_range")
    if isinstance(cr, list) and len(cr) == 2 and isinstance(cr[1], int):
        return cr[1]
    return None


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
        # 2026-05-29 cluster 化：cluster 模式优先用账本末 cluster 末章当锚点
        ledger_ch = _anchor_ch_from_ledger(project_root) if IS_CLUSTER_MODE else None
        if ledger_ch is not None:
            ch = ledger_ch
        else:
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
