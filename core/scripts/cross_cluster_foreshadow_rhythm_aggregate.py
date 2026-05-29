"""cross_cluster_foreshadow_rhythm_aggregate.py — 伏笔跨章节奏扫（CCR20）

读 _数据库/伏笔表.json 中所有 foreshadowings，对照已写章节：

- FORESHADOW_OVERDUE：initiated_at_ch + due_by < 当前章 但 paid_at_ch=null
- FORESHADOW_NO_REINFORCEMENT：initiated 后 ≥ 5 章无任何铺垫（reinforced_chs[] 为空且 due_by 还有 ≥ 3 章）
- FORESHADOW_OVER_REINFORCEMENT：reinforced_chs[] ≥ 8 但 paid_at_ch=null（读者疲劳）
- FORESHADOW_NEVER_INITIATED：伏笔 declared 但 initiated_at_ch=null 已 ≥ 10 章

退出码: 0 健康 / 1 advisory / 2 warning
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
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动


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
    args = ap.parse_args()

    project_root = Path(args.project)
    fs_path = project_root / "_数据库" / "伏笔表.json"
    if not fs_path.exists():
        print("[SKIP] 伏笔表.json 不存在")
        sys.exit(0)
    data = load_json(fs_path, {})
    fss = data.get("foreshadowings", []) or data.get("items", []) or []
    if not fss:
        print("[SKIP] 伏笔表为空")
        sys.exit(0)

    # 当前已写最大章
    # 2026-05-29 cluster 化（轻改造）：伏笔表.json 仍是权威来源（保留），cluster 模式
    # 仅把 cur_ch 锚点改用末 cluster 的 chapter_range[1]，避免回退逐章 glob 文件夹。
    # 另可选叠加账本 foreshadow_planted/paid 作为已落账增量（账本无则纯走伏笔表）。
    cur_ch = 0
    ledger_planted = set()
    ledger_paid = set()
    if csr.is_cluster_mode():
        clusters = csr.get_clusters(project_root)
        for c in clusters:
            cr = c.get("chapter_range")
            end = c.get("cluster_end_ch")
            if isinstance(cr, list) and len(cr) >= 2 and isinstance(cr[1], int):
                cur_ch = max(cur_ch, cr[1])
            elif isinstance(end, int):
                cur_ch = max(cur_ch, end)
            for fid in c.get("foreshadow_planted") or []:
                ledger_planted.add(fid)
            for fid in c.get("foreshadow_paid") or []:
                ledger_paid.add(fid)
    if not cur_ch:
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        cur_ch = chapters[-1] if chapters else 0
    if not cur_ch:
        print("[SKIP] 无已写章节")
        sys.exit(0)

    findings = []
    for fs in fss:
        if not isinstance(fs, dict):
            continue
        fid = fs.get("id") or fs.get("name", "?")
        initiated = fs.get("initiated_at_ch") or fs.get("set_at_ch") or 0
        paid = fs.get("paid_at_ch") or fs.get("resolved_at_ch")
        # 账本已记录该伏笔回收（增量补强，伏笔表漏标时兜底）
        if not paid and fid in ledger_paid:
            paid = cur_ch
        due_by = fs.get("due_by") or 0
        reinforced = fs.get("reinforced_chs") or fs.get("reinforced_at") or []

        if paid:
            continue  # 已回收

        # FORESHADOW_NEVER_INITIATED
        if not initiated and cur_ch >= 10:
            # declared_at_ch 取记录时间，若有
            declared = fs.get("declared_at_ch") or 1
            if cur_ch - declared >= 10:
                findings.append({
                    "severity": "advisory",
                    "code": "FORESHADOW_NEVER_INITIATED",
                    "id": fid,
                    "declared_at_ch": declared,
                    "gap": cur_ch - declared,
                    "suggestion": f"伏笔 {fid} declared 在 ch{declared} 后 ≥ {cur_ch - declared} 章未 initiated → 伏笔被遗忘",
                })
            continue

        if not initiated:
            continue

        # FORESHADOW_OVERDUE
        if due_by and cur_ch > initiated + due_by:
            overdue = cur_ch - (initiated + due_by)
            severity = "warning" if overdue >= 5 else "advisory"
            findings.append({
                "severity": severity,
                "code": "FORESHADOW_OVERDUE",
                "id": fid,
                "initiated_at_ch": initiated,
                "due_by": due_by,
                "overdue_by": overdue,
                "current_ch": cur_ch,
                "suggestion": f"伏笔 {fid} 设置 ch{initiated} due_by {due_by} → 应在 ch{initiated+due_by} 前回收，已超 {overdue} 章",
            })

        # FORESHADOW_NO_REINFORCEMENT
        if cur_ch - initiated >= 5 and len(reinforced) == 0 and (not due_by or cur_ch < initiated + due_by - 2):
            findings.append({
                "severity": "advisory",
                "code": "FORESHADOW_NO_REINFORCEMENT",
                "id": fid,
                "initiated_at_ch": initiated,
                "current_ch": cur_ch,
                "suggestion": f"伏笔 {fid} 在 ch{initiated} 设置后 {cur_ch - initiated} 章无任何 reinforced 提及 → 读者会忘",
            })

        # FORESHADOW_OVER_REINFORCEMENT
        if len(reinforced) >= 8:
            findings.append({
                "severity": "advisory",
                "code": "FORESHADOW_OVER_REINFORCEMENT",
                "id": fid,
                "reinforced_count": len(reinforced),
                "suggestion": f"伏笔 {fid} 已 reinforced {len(reinforced)} 次仍未回收 → 读者疲劳，应尽快兑现",
            })

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "foreshadow_rhythm",
        "scan_ts": ts,
        "current_ch": cur_ch,
        "total_foreshadowings": len(fss),
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"foreshadow_rhythm_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[foreshadow_rhythm] {len(fss)} 个伏笔: {summary['warning']} warning / {summary['advisory']} advisory")
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
