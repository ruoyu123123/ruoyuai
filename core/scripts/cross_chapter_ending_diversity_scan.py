"""cross_chapter_ending_diversity_scan.py — ending_type 多样性跨章扫（CCR18）

读 _changes.self_eval.applied_style.ending_type（writer 自评章节收束类型），
检测跨章 ending_type 分布是否单一化。

ending_type 候选：cliffhanger / emotional_pivot / revelation / resolution
                  question / ambient / time_jump / dialogue_close / image_close

- ENDING_TYPE_MONOTONE：>50% 是同一 ending_type（连续 ≥ 3 章）
- ENDING_TYPE_LOW_DIVERSITY：近 N 章只用了 ≤ 2 种 ending_type
- ENDING_TYPE_MISSING：≥ 3 章无 ending_type 标记 = writer 漏填

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
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    recent = chapters[-args.last_n:] if chapters else []
    if not recent:
        print("[SKIP] 无已写章节")
        sys.exit(0)

    # 收集 ending_type
    per_ch = []  # [(ch, ending_type)]
    for ch in recent:
        p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
        changes = load_json(p, {})
        applied = ((changes.get("self_eval") or {}).get("applied_style") or {})
        et = applied.get("ending_type", "")
        per_ch.append((ch, et))

    # 缺失检测
    missing_chs = [ch for ch, et in per_ch if not et]
    findings = []
    if len(missing_chs) >= 3:
        findings.append({
            "severity": "warning",
            "code": "ENDING_TYPE_MISSING",
            "missing_chs": missing_chs,
            "suggestion": f"近 {len(recent)} 章中 {len(missing_chs)} 章 _changes.self_eval.applied_style.ending_type 未填 → writer 漏标，无法跨章分析",
        })

    # 仅含 ending_type 的章做分析
    valid = [(ch, et) for ch, et in per_ch if et]
    if len(valid) < 3:
        # 输出 report
        out_dir = project_root / "_数据库" / ".cross_chapter_scan"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report = {
            "scan_type": "ending_diversity",
            "scan_ts": ts,
            "chapters_scanned": recent,
            "findings": findings,
            "summary": {"warning": sum(1 for f in findings if f["severity"] == "warning"),
                        "advisory": sum(1 for f in findings if f["severity"] == "advisory")},
        }
        out_path = out_dir / f"ending_diversity_{ts}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ending_diversity] {len(valid)} 章有 ending_type — 不足 3 章无法分析")
        for f in findings:
            print(f"  [{f['severity'].upper()}] {f.get('suggestion', '')[:80]}")
        sys.exit(0)

    # ENDING_TYPE_MONOTONE
    counts = Counter(et for _, et in valid)
    total = len(valid)
    most_et, most_count = counts.most_common(1)[0]
    if most_count / total > 0.5:
        findings.append({
            "severity": "warning",
            "code": "ENDING_TYPE_MONOTONE",
            "dominant_ending_type": most_et,
            "pct": round(most_count / total, 2),
            "distribution": dict(counts),
            "suggestion": f"近 {total} 章中 {round(most_count/total*100)}% 是 ending_type=「{most_et}」→ 章节收束单一化",
        })

    # ENDING_TYPE_LOW_DIVERSITY
    unique_count = len(counts)
    if total >= 5 and unique_count <= 2:
        findings.append({
            "severity": "advisory",
            "code": "ENDING_TYPE_LOW_DIVERSITY",
            "unique_count": unique_count,
            "distribution": dict(counts),
            "suggestion": f"近 {total} 章只用了 {unique_count} 种 ending_type → 多样性不足，应至少 3 种",
        })

    # 连续同 ending_type
    streak = 1
    streak_chs = [valid[0][0]]
    streak_et = valid[0][1]
    for i in range(1, len(valid)):
        if valid[i][1] == streak_et:
            streak += 1
            streak_chs.append(valid[i][0])
            if streak >= 4:
                findings.append({
                    "severity": "advisory",
                    "code": "ENDING_TYPE_RUN",
                    "ending_type": streak_et,
                    "consecutive_chs": streak_chs[-4:],
                    "suggestion": f"近 ≥ 4 章 ending_type 都是「{streak_et}」→ 应换一种",
                })
                streak = 1
                streak_chs = [valid[i][0]]
        else:
            streak_et = valid[i][1]
            streak = 1
            streak_chs = [valid[i][0]]

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "ending_diversity",
        "scan_ts": ts,
        "chapters_scanned": recent,
        "ending_distribution": dict(counts),
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"ending_diversity_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ending_diversity] {total} 章 distribution: " + ", ".join(f"{k}={v}" for k, v in counts.most_common(5)))
    for f in findings[:5]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
