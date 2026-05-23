"""maybe_judge_consensus.py — 关键章节判定 + judge_consensus 条件触发（v19.3 新增）"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


KEY_TURNING_POINT_KW = ["高潮", "反转", "触发", "觉醒", "崩溃", "牺牲", "宣战", "复仇", "终局"]
KEY_ENDING_TYPES = {"信息炸弹", "POV切换收尾", "悬念断章"}
STC_KEY_CHAPTERS = {5, 14, 50, 100, 175, 250, 300, 375, 395, 420, 475, 500}


def is_key_chapter(project_root: Path, ch: int) -> tuple[bool, list[str]]:
    reasons = []
    progress = load_json(project_root / "_数据库" / "进度.json", {"chapter_plan": [], "volumes": []})

    if ch in STC_KEY_CHAPTERS:
        reasons.append(f"STC 节点 ch{ch}")

    for v in progress.get("volumes", []):
        ch_range = v.get("chapter_range", [])
        if len(ch_range) == 2:
            if ch == ch_range[0]:
                reasons.append(f"卷起始 vol{v.get('vol')}")
            elif ch == ch_range[1]:
                reasons.append(f"卷末 vol{v.get('vol')}")

    for cp in progress.get("chapter_plan", []):
        if cp.get("ch") != ch:
            continue
        tp = cp.get("turning_point", "") or ""
        for kw in KEY_TURNING_POINT_KW:
            if kw in tp:
                reasons.append(f"turning_point 含'{kw}'")
                break
        emo = cp.get("emotion", {}).get("value", 0)
        if abs(emo) >= 7:
            reasons.append(f"强情绪 emotion={emo}")
        break

    changes = load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})
    et = changes.get("self_eval", {}).get("applied_style", {}).get("ending_type", "")
    if et in KEY_ENDING_TYPES:
        reasons.append(f"ending_type={et}")

    return (len(reasons) > 0, reasons)


def trigger_consensus(project_root: Path, ch: int) -> int:
    judge_dir = project_root / "_数据库" / ".judge_reports"
    if not judge_dir.is_dir():
        print(f"[SKIP] .judge_reports/ 不存在")
        return 0
    reports = list(judge_dir.glob(f"ch_{ch:03d}_*.json"))
    if len(reports) < 2:
        print(f"[SKIP] ch{ch} 仅 {len(reports)} 份 report，<2 不需 consensus")
        return 0
    consensus_script = Path(__file__).parent / "judge_consensus.py"
    if not consensus_script.exists():
        print(f"[SKIP] judge_consensus.py 不存在")
        return 0
    cmd = ["python", str(consensus_script), "merge"] + [str(p) for p in reports]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
        out_path = judge_dir / f"ch_{ch:03d}_consensus.json"
        if result.returncode == 0 and result.stdout.strip().startswith("{"):
            out_path.write_text(result.stdout, encoding="utf-8")
            print(f"[OK] consensus 合并 {len(reports)} 份 → {out_path.name}")
        else:
            print(f"[WARN] consensus 输出异常 (exit={result.returncode})")
        return 0  # 不阻塞主流水线
    except subprocess.TimeoutExpired:
        print(f"[WARN] consensus 超时")
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("chapter", type=int)
    args = ap.parse_args()
    project_root = Path(args.project)
    ch = args.chapter
    is_key, reasons = is_key_chapter(project_root, ch)
    if not is_key:
        print(f"[SKIP] ch{ch} 非关键章节")
        sys.exit(0)
    print(f"[KEY] ch{ch} 关键章节: {reasons}")
    trigger_consensus(project_root, ch)
    sys.exit(0)


if __name__ == "__main__":
    main()
