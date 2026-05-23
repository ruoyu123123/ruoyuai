"""run_cross_chapter_scans.py — 按 user_preferences.cross_chapter_scan_intensity 选择性跑 scanner（v21 UX6）

代替 save-state plan 中 18 行的 scanner 调用，统一通过本 wrapper：
- full_18：全部 18 个跨章 scanner（默认）
- core_10：仅核心 10 个（去多样性/趋势类 advisory）
- minimal_5：仅 5 个最关键（continuity/pattern/fate_drift/persona_drift/data_consumption）
- off：全不跑（紧急快速出稿）

用法：python run_cross_chapter_scans.py <project> --ch <ch>
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
        "cross_chapter_continuity_scan",
        "cross_chapter_pattern_scan",
        "cross_chapter_fate_drift_scan",
        "cross_chapter_persona_drift_scan",
        "cross_chapter_data_consumption_scan",
    ],
    "core_10_extra": [
        "cross_chapter_offscreen_scan",
        "cross_chapter_declarative_data_scan",
        "cross_chapter_arc_progression_scan",
        "cross_chapter_throughline_balance_scan",
        "cross_chapter_foreshadow_rhythm_scan",
    ],
    "full_18_extra": [
        "cross_chapter_emotion_pattern_scan",
        "cross_chapter_character_dynamics_scan",
        "cross_chapter_world_dynamics_scan",
        "cross_chapter_judge_quality_scan",
        "cross_chapter_timeline_item_location_scan",
        "cross_chapter_meta_quality_scan",
        "cross_chapter_structure_compliance_scan",
        "cross_chapter_engagement_metrics_scan",
        "cross_chapter_ending_diversity_scan",
        "cross_chapter_scene_pov_diversity_scan",
        "cross_chapter_relationship_trend_scan",
        "cross_chapter_will_learn_scan",
    ],
}

# 哪些 scanner 接 --ch 参数（其他用 --last-n 或全自动）
SCANNERS_WITH_CH = {
    "cross_chapter_fate_drift_scan",
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, required=True)
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    intensity = get_intensity(project_root)
    print(f"[run_cross_chapter_scans] ch{args.ch} intensity={intensity}")

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
        elif sc in ("cross_chapter_arc_progression_scan", "cross_chapter_world_dynamics_scan",
                    "cross_chapter_foreshadow_rhythm_scan", "cross_chapter_will_learn_scan",
                    "cross_chapter_structure_compliance_scan"):
            cmd = [sys.executable, str(sc_path), str(project_root)]
        else:
            cmd = [sys.executable, str(sc_path), str(project_root), "--last-n", str(args.last_n)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
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
