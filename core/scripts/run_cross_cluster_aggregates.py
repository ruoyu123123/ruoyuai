"""run_cross_cluster_aggregates.py — 按 user_preferences.cross_chapter_scan_intensity 选择性跑 scanner（v21 UX6）

代替 save-state plan 中 18 行的 scanner 调用，统一通过本 wrapper：
- full_18：全部 18 个跨章 scanner（默认）
- core_10：仅核心 10 个（去多样性/趋势类 advisory）
- minimal_5：仅 5 个最关键（continuity/pattern/fate_drift/persona_drift/data_consumption）
- off：全不跑（紧急快速出稿）

用法：python run_cross_cluster_aggregates.py <project> --ch <ch>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# 18 个 scanner 分级（按用户感知重要度）
SCAN_TIERS = {
    "core_5": [
        "cross_cluster_continuity_aggregate",
        "cross_cluster_pattern_aggregate",
        "cross_cluster_fate_drift_aggregate",
        "cross_cluster_persona_drift_aggregate",
        "cross_cluster_data_consumption_aggregate",
    ],
    "core_10_extra": [
        "cross_cluster_offscreen_aggregate",
        "cross_cluster_declarative_data_aggregate",
        "cross_cluster_arc_progression_aggregate",
        "cross_cluster_throughline_balance_aggregate",
        "cross_cluster_foreshadow_rhythm_aggregate",
    ],
    "full_18_extra": [
        "cross_cluster_emotion_pattern_aggregate",
        "cross_cluster_character_dynamics_aggregate",
        "cross_cluster_world_dynamics_aggregate",
        "cross_cluster_judge_quality_aggregate",
        "cross_cluster_timeline_item_location_aggregate",
        "cross_cluster_meta_quality_aggregate",
        "cross_cluster_structure_compliance_aggregate",
        "cross_cluster_engagement_metrics_aggregate",
        "cross_cluster_ending_diversity_aggregate",
        "cross_cluster_scene_pov_diversity_aggregate",
        "cross_cluster_relationship_trend_aggregate",
        "cross_cluster_will_learn_aggregate",
    ],
}

# 哪些 scanner 接 --ch 参数（其他用 --last-n 或全自动）
SCANNERS_WITH_CH = {
    "cross_cluster_fate_drift_aggregate",
}


def get_intensity(project_root: Path) -> str:
    prefs_path = project_root / "_数据库" / "用户偏好.json"
    if not prefs_path.exists():
        return "full_18"
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        return (prefs.get("quality_control") or {}).get("cross_chapter_scan_intensity", "full_18")
    except Exception:
        return "full_18"


def get_cluster_last_ch(project_root: Path, cluster_key: str) -> int | None:
    """从 事件簇.json 找 cluster 的最后一章号。"""
    shijianji_path = project_root / "_数据库" / "事件簇.json"
    if not shijianji_path.exists():
        return None
    try:
        data = json.loads(shijianji_path.read_text(encoding="utf-8"))
        for c in data.get("clusters", []):
            cid = c.get("cluster_id", "")
            if cid == cluster_key or cid.replace("cluster_", "") == cluster_key.replace("cluster_", ""):
                cr = c.get("chapter_range")
                if isinstance(cr, list) and len(cr) == 2:
                    return cr[1]
                elif isinstance(cr, str) and "-" in cr:
                    return int(cr.split("-")[1])
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, help="单章模式（与 --cluster 二选一）")
    ap.add_argument("--cluster", help="cluster 模式：扫该 cluster 末章上下文")
    ap.add_argument("--last-n", type=int, default=10)
    ap.add_argument("--tier", choices=["core_5", "core_10", "full_18", "off"], help="覆盖 user_preferences intensity")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()

    # cluster mode：用 cluster 末章作为 --ch
    if args.cluster:
        ch = get_cluster_last_ch(project_root, args.cluster)
        if ch is None:
            print(f"[FATAL] cluster {args.cluster} 未找到 chapter_range", file=sys.stderr)
            sys.exit(2)
        args.ch = ch
        print(f"[cluster {args.cluster}] 用末章 ch{ch} 作为扫描锚点")
    elif args.ch is None:
        print(f"[FATAL] 必须指定 --ch 或 --cluster", file=sys.stderr)
        sys.exit(2)

    intensity = args.tier if args.tier else get_intensity(project_root)
    print(f"[run_cross_cluster_aggregates] ch{args.ch} intensity={intensity}")

    if intensity == "off":
        print("[SKIP] user_preferences.cross_chapter_scan_intensity=off")
        sys.exit(0)

    # 选 scanner 集合
    scanners = list(SCAN_TIERS["core_5"])
    if intensity in ("core_10", "full_18"):
        scanners.extend(SCAN_TIERS["core_10_extra"])
    if intensity == "full_18":
        scanners.extend(SCAN_TIERS["full_18_extra"])

    script_dir = Path(__file__).parent
    summary = {"ran": 0, "skipped": 0, "errors": []}
    for sc in scanners:
        sc_path = script_dir / f"{sc}.py"
        if not sc_path.exists():
            summary["skipped"] += 1
            continue
        # 构造 cmd
        if sc in SCANNERS_WITH_CH:
            cmd = [sys.executable, str(sc_path), str(project_root), "--ch", str(args.ch)]
        elif sc in ("cross_cluster_arc_progression_aggregate", "cross_cluster_world_dynamics_aggregate",
                    "cross_cluster_foreshadow_rhythm_aggregate", "cross_cluster_will_learn_aggregate",
                    "cross_cluster_structure_compliance_aggregate"):
            cmd = [sys.executable, str(sc_path), str(project_root)]
        else:
            cmd = [sys.executable, str(sc_path), str(project_root), "--last-n", str(args.last_n)]
        try:
            # v2 cluster 化（2026-05-28）：cluster 模式给子进程透传 CLUSTER_MODE=1 env
            import os as _os
            _env = None
            if args.cluster:
                _env = {**_os.environ, "CLUSTER_MODE": "1", "CLUSTER_ID": args.cluster}
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", env=_env)
            summary["ran"] += 1
            if r.returncode >= 2:
                summary["errors"].append({"scanner": sc, "exit": r.returncode})
        except Exception as e:
            summary["errors"].append({"scanner": sc, "exception": str(e)[:80]})

    print(f"[OK] 跑了 {summary['ran']}/{len(scanners)} 个 scanner（intensity={intensity}）")
    if summary["errors"]:
        print(f"[WARN] {len(summary['errors'])} 个 scanner 报错（不打断）")
    sys.exit(0)


if __name__ == "__main__":
    main()
