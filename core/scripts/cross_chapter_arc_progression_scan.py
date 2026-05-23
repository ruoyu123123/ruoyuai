"""cross_chapter_arc_progression_scan.py — 弧光阶段推进合理性（CCR4）

读 character_arc_state.json 中 stages_by_chapter，检测：
- STAGE_JUMP：跨度过大（如 lie 直接到 truth_realized 跳过中间）
- STAGE_REGRESS：倒退（已 want_threatened 又回 lie）
- STAGE_STAGNATION：长期停滞（connected ≥ 10 章 stage 无变化）
- STAGE_NOT_RECORDED：当前 ch 已超 stages 末章但 current_stage_at_ch 未更新

Save the Cat 主弧序：lie → lie_cracking → want_threatened → debate → break_into_two
                  → fun_and_games → false_victory / midpoint_revelation → bad_guys_close_in
                  → all_is_lost → dark_night → break_into_three → truth_realized → final_image

退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# 弧光阶段顺序（数字越大越晚）
ARC_STAGE_ORDER = {
    "lie": 0,
    "lie_cracking": 1,
    "want_threatened": 2,
    "debate": 3,
    "break_into_two": 4,
    "fun_and_games": 5,
    "false_victory": 6,
    "midpoint_revelation": 7,
    "bad_guys_close_in": 8,
    "all_is_lost": 9,
    "dark_night": 10,
    "break_into_three": 11,
    "truth_realized": 12,
    "final_image": 13,
}


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def stage_order(stage: str) -> int:
    """返回 stage 的序号；不识别返回 -1。"""
    if not stage:
        return -1
    base = stage.split(":")[-1].strip().lower()  # 兼容 "5:lie" 形式
    return ARC_STAGE_ORDER.get(base, -1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project)
    arc_path = project_root / "_数据库" / "character_arc_state.json"
    if not arc_path.exists():
        print("[SKIP] character_arc_state.json 不存在")
        sys.exit(0)
    data = load_json(arc_path, {})

    # 已写章节最大值
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    max_written_ch = chapters[-1] if chapters else 0

    findings = []
    for char_name, char_data in (data.get("characters") or {}).items():
        stages = char_data.get("stages_by_chapter", {}) or {}
        if not stages:
            continue
        # 转 [(ch, stage)] 按 ch 排序
        sorted_stages = sorted(
            [(int(c), s) for c, s in stages.items() if str(c).isdigit()],
            key=lambda x: x[0],
        )
        if len(sorted_stages) < 2:
            continue

        # 1. STAGE_JUMP / STAGE_REGRESS
        for i in range(1, len(sorted_stages)):
            prev_ch, prev_stage = sorted_stages[i - 1]
            cur_ch, cur_stage = sorted_stages[i]
            prev_o = stage_order(prev_stage)
            cur_o = stage_order(cur_stage)
            if prev_o < 0 or cur_o < 0:
                continue
            ch_gap = cur_ch - prev_ch
            stage_gap = cur_o - prev_o
            # JUMP：单次跳 ≥ 4 阶且 ch_gap < 50
            if stage_gap >= 4 and ch_gap < 50:
                findings.append({
                    "severity": "warning",
                    "code": "STAGE_JUMP",
                    "character": char_name,
                    "from": {"ch": prev_ch, "stage": prev_stage},
                    "to": {"ch": cur_ch, "stage": cur_stage},
                    "stage_gap": stage_gap,
                    "ch_gap": ch_gap,
                    "suggestion": f"{char_name} 在 ch{prev_ch}→{cur_ch} 弧光从 {prev_stage} 跳到 {cur_stage}（跨 {stage_gap} 阶，章距仅 {ch_gap}）→ 需补中间过渡章",
                })
            # REGRESS：阶序倒退
            if stage_gap < 0:
                findings.append({
                    "severity": "warning",
                    "code": "STAGE_REGRESS",
                    "character": char_name,
                    "from": {"ch": prev_ch, "stage": prev_stage},
                    "to": {"ch": cur_ch, "stage": cur_stage},
                    "suggestion": f"{char_name} 弧光从 {prev_stage}(ch{prev_ch}) 倒退到 {cur_stage}(ch{cur_ch}) → 弧光不应单调倒退（除非 mental_break 触发）",
                })
            # STAGNATION：相同 stage 跨 ≥ 10 章
            if stage_gap == 0 and ch_gap >= 10:
                findings.append({
                    "severity": "advisory",
                    "code": "STAGE_STAGNATION",
                    "character": char_name,
                    "stage": cur_stage,
                    "from_ch": prev_ch,
                    "to_ch": cur_ch,
                    "duration": ch_gap,
                    "suggestion": f"{char_name} 在 stage={cur_stage} 停滞 ≥ {ch_gap} 章 → 应推进到下一阶或加 lie_cracking 过渡",
                })

        # 2. STAGE_NOT_RECORDED：current_stage_at_ch 是否同步
        last_recorded_ch = sorted_stages[-1][0]
        current_stage_label = char_data.get("current_stage_at_ch", "")
        last_updated = char_data.get("_last_updated_at_ch", 0)
        if max_written_ch and max_written_ch - last_updated > 5:
            findings.append({
                "severity": "advisory",
                "code": "STAGE_NOT_UPDATED",
                "character": char_name,
                "last_updated_at_ch": last_updated,
                "max_written_ch": max_written_ch,
                "suggestion": f"{char_name} current_stage_at_ch 上次更新在 ch{last_updated}，已写到 ch{max_written_ch}（差 {max_written_ch-last_updated} 章无更新）",
            })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "arc_progression",
        "scan_ts": ts,
        "max_written_ch": max_written_ch,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"arc_progression_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[arc_progression] {summary['warning']} warning / {summary['advisory']} advisory")
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
