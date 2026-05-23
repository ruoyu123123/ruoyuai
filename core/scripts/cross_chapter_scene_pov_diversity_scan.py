"""cross_chapter_scene_pov_diversity_scan.py — scene_type + POV 多样性跨章扫（CCR19）

A. SCENE_TYPE_DIVERSITY
   读 _数据库/章纲摘要.json[ch].scene_type
   - SCENE_TYPE_RUN：连续 ≥ 4 章同 scene_type（如全办公室对话）
   - SCENE_TYPE_LOW_DIVERSITY：近 N 章 ≤ 2 种 scene_type

B. POV_ROTATION
   读 章纲摘要.json[ch].characters 取首角色 = 本章主 POV
   - POV_LOCKED：≥ 8 章连续同一 POV（缺角色切换）
   - POV_OVERCONCENTRATED：近 N 章主 POV 分布 >85% 是同一角色

退出码: 0 健康 / 1 advisory / 2 warning
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
    summary_path = project_root / "_数据库" / "章纲摘要.json"
    if not summary_path.exists():
        print("[SKIP] 章纲摘要.json 不存在")
        sys.exit(0)
    summary = load_json(summary_path, {})
    plan = summary.get("chapter_plan", {}) or {}
    if not plan:
        print("[SKIP] chapter_plan 为空")
        sys.exit(0)

    # 已写章节
    chapters_written = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                              for d in (project_root / "章节").glob("第*章")
                              if re.match(r"第(\d+)章", d.name))
    recent = chapters_written[-args.last_n:] if chapters_written else []
    if not recent:
        print("[SKIP] 无已写章节")
        sys.exit(0)

    findings = []

    # 收集每章 scene_type + 主 POV
    per_ch = []  # [(ch, scene_type, pov)]
    for ch in recent:
        entry = plan.get(str(ch)) or plan.get(ch) or {}
        if not isinstance(entry, dict):
            continue
        st = entry.get("scene_type", "")
        chars = entry.get("characters", []) or []
        pov = chars[0] if chars else ""
        per_ch.append((ch, st, pov))

    valid_scenes = [(c, s) for c, s, _ in per_ch if s]
    valid_povs = [(c, p) for c, _, p in per_ch if p]

    # A. SCENE_TYPE
    if len(valid_scenes) >= 3:
        # RUN
        streak = 1
        streak_chs = [valid_scenes[0][0]]
        cur_st = valid_scenes[0][1]
        for i in range(1, len(valid_scenes)):
            if valid_scenes[i][1] == cur_st:
                streak += 1
                streak_chs.append(valid_scenes[i][0])
                if streak >= 4:
                    findings.append({
                        "severity": "advisory",
                        "code": "SCENE_TYPE_RUN",
                        "scene_type": cur_st,
                        "consecutive_chs": streak_chs[-4:],
                        "suggestion": f"连续 ≥ 4 章 scene_type=「{cur_st}」→ 应换场景类型",
                    })
                    streak = 1
                    streak_chs = [valid_scenes[i][0]]
            else:
                cur_st = valid_scenes[i][1]
                streak = 1
                streak_chs = [valid_scenes[i][0]]
        # LOW_DIVERSITY
        counts_st = Counter(s for _, s in valid_scenes)
        if len(valid_scenes) >= 5 and len(counts_st) <= 2:
            findings.append({
                "severity": "advisory",
                "code": "SCENE_TYPE_LOW_DIVERSITY",
                "distribution": dict(counts_st),
                "suggestion": f"近 {len(valid_scenes)} 章只用了 {len(counts_st)} 种 scene_type → 场景单调",
            })

    # B. POV
    if len(valid_povs) >= 4:
        # LOCKED
        streak = 1
        streak_chs = [valid_povs[0][0]]
        cur_pov = valid_povs[0][1]
        for i in range(1, len(valid_povs)):
            if valid_povs[i][1] == cur_pov:
                streak += 1
                streak_chs.append(valid_povs[i][0])
                if streak >= 8:
                    findings.append({
                        "severity": "advisory",
                        "code": "POV_LOCKED",
                        "pov": cur_pov,
                        "consecutive_chs": streak_chs[-8:],
                        "suggestion": f"主 POV 连续 ≥ 8 章是「{cur_pov}」→ 缺角色切换，应插入其他视角章",
                    })
                    streak = 1
                    streak_chs = [valid_povs[i][0]]
            else:
                cur_pov = valid_povs[i][1]
                streak = 1
                streak_chs = [valid_povs[i][0]]
        # OVERCONCENTRATED
        counts_pov = Counter(p for _, p in valid_povs)
        total = len(valid_povs)
        most_pov, most_count = counts_pov.most_common(1)[0]
        if most_count / total > 0.85 and total >= 5:
            findings.append({
                "severity": "advisory",
                "code": "POV_OVERCONCENTRATED",
                "dominant_pov": most_pov,
                "pct": round(most_count / total, 2),
                "distribution": dict(counts_pov),
                "suggestion": f"近 {total} 章中 {round(most_count/total*100)}% 主 POV 是「{most_pov}」→ 应插入其他 POV",
            })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_obj = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "scene_pov_diversity",
        "scan_ts": ts,
        "chapters_scanned": recent,
        "scene_distribution": dict(Counter(s for _, s in valid_scenes)),
        "pov_distribution": dict(Counter(p for _, p in valid_povs)),
        "findings": findings,
        "summary": summary_obj,
    }
    out_path = out_dir / f"scene_pov_diversity_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[scene_pov_diversity] {summary_obj['warning']} warning / {summary_obj['advisory']} advisory")
    for f in findings[:5]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary_obj["warning"] > 0:
        sys.exit(2)
    if summary_obj["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
