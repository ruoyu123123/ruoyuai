"""cross_cluster_arc_progression_aggregate.py — 弧光阶段推进合理性（CCR4）

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


def _build_char_stages_from_ledger(project_root):
    """2026-05-29 cluster 化：从账本逐章 arc_stage 重建
    {char_name: {"stages_by_chapter": {ch: stage}}}，复用下游同一套阶段判定逻辑。

    ledger 的 arc_stage = {char: stage_str}（per-chapter）。STAGE_NOT_UPDATED 在账本
    模式下无 _last_updated_at_ch 元信息，故仅产出 JUMP/REGRESS/STAGNATION（与逐章模式
    的核心阶序检测一致），不伪造 NOT_UPDATED。
    """
    char_stages: dict[str, dict[int, str]] = {}
    for ch, rec in csr.get_chapter_records(project_root):
        stage_map = rec.get("arc_stage") or {}
        if not isinstance(stage_map, dict):
            continue
        for char_name, stage in stage_map.items():
            if not stage:
                continue
            char_stages.setdefault(char_name, {})[ch] = stage
    return {
        cn: {"stages_by_chapter": {str(ch): s for ch, s in sb.items()}}
        for cn, sb in char_stages.items()
    }


def _stage_at_ch(sorted_stages: list, ch: int) -> str | None:
    """返回某章「生效中」的预设 stage —— 取最后一个 stage_ch <= ch 的 stage（step 函数）。
    sorted_stages = [(stage_ch, stage_str), ...] 已按 ch 升序。"""
    eff = None
    for sch, st in sorted_stages:
        if sch <= ch:
            eff = st
        else:
            break
    return eff


def _read_declared_mckee_beats(project_root: Path, max_ch: int) -> dict:
    """读各章 _changes.json 里 writer 申报的 self_eval.mckee_truby_alignment。
    返回 {ch: {desire_pursued_this_ch, need_glimpsed_this_ch, ghost_triggered, moral_argument_advanced}}。

    #5 孤儿契约修复：此前弧光仅按预设 stages_by_chapter 推进（character_arc_update 写），
    从不读 writer 申报的实际节拍 —— 预设排期与实际写出的 McKee/Truby 节拍可能背离却无人核对。
    本函数把 writer 申报取出供下游做 advisory 一致性核对（北极星⑤：不读 writer 申报 = 丢弃第一权威）。
    """
    out: dict[int, dict] = {}
    chapters_dir = project_root / "章节"
    if not chapters_dir.exists():
        return out
    hi = max(max_ch, 0) + 1
    for ch in range(1, hi + 50):  # 兜底多扫 50 章（max_ch 可能滞后）
        cp = chapters_dir / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
        if not cp.exists():
            continue
        changes = load_json(cp, {})
        if not isinstance(changes, dict):
            continue
        mck = ((changes.get("self_eval") or {}).get("mckee_truby_alignment") or {})
        if isinstance(mck, dict) and mck:
            out[ch] = mck
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project)

    # ===== 2026-05-29 cluster 化分支：账本有逐章 arc_stage → 走账本 =====
    use_ledger = csr.is_cluster_mode() and csr.ledger_has_field(project_root, "arc_stage")
    if use_ledger:
        characters_iter = _build_char_stages_from_ledger(project_root)
        # cluster 模式锚点：用 cluster 末章
        cluster_list = csr.get_clusters(project_root)
        max_written_ch = 0
        for c in cluster_list:
            cr = c.get("chapter_range")
            end = c.get("cluster_end_ch")
            if isinstance(cr, list) and len(cr) >= 2 and isinstance(cr[1], int):
                max_written_ch = max(max_written_ch, cr[1])
            elif isinstance(end, int):
                max_written_ch = max(max_written_ch, end)
        if not characters_iter:
            print("[SKIP] cluster 账本无 arc_stage 记录")
            sys.exit(0)
    else:
        # ===== 原逐章磁盘逻辑（非 cluster 模式 / 账本缺字段 → 零回归）=====
        arc_path = project_root / "_数据库" / "character_arc_state.json"
        if not arc_path.exists():
            print("[SKIP] character_arc_state.json 不存在")
            sys.exit(0)
        data = load_json(arc_path, {})
        characters_iter = data.get("characters") or {}

        # 已写章节最大值
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        max_written_ch = chapters[-1] if chapters else 0

    # #5 孤儿契约修复：读 writer 申报的 mckee_truby 节拍（按章），供下游 advisory 核对预设 stage。
    # 申报存于 _changes.json（磁盘），账本/磁盘模式都能读 → 两路都核对，不破坏 use_ledger 分支。
    declared_beats = _read_declared_mckee_beats(project_root, max_written_ch)

    findings = []
    # 主角预设阶序（取 stages_by_chapter 最长者 = 主弧），供 mckee 节拍 advisory 核对用
    protagonist_stages: list = []
    for char_name, char_data in characters_iter.items():
        stages = char_data.get("stages_by_chapter", {}) or {}
        if not stages:
            continue
        # 转 [(ch, stage)] 按 ch 排序
        sorted_stages = sorted(
            [(int(c), s) for c, s in stages.items() if str(c).isdigit()],
            key=lambda x: x[0],
        )
        if len(sorted_stages) > len(protagonist_stages):
            protagonist_stages = sorted_stages
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
        # 2026-05-29 cluster 化：账本模式无 _last_updated_at_ch 元信息（per-chapter
        # arc_stage 本就逐章同步），跳过此检测，不伪造 NOT_UPDATED。
        if use_ledger:
            continue
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

    # 3. BEAT_STAGE_DIVERGENCE（#5 孤儿契约修复 · 全 advisory 不硬锁）
    # 核对 writer 申报的 McKee/Truby 深层节拍 vs 预设 Save-the-Cat stage 排期是否背离。
    # 映射：need_glimpsed / moral_argument_advanced = 深层 need/truth 弧的节拍，按 Save-the-Cat
    # 排期应在中后段（midpoint_revelation 及以后，order ≥ 7）才大量出现；若 writer 在仍处早期
    # lie/lie_cracking（order ≤ 1）阶段就申报「主角已窥见 need + 道德论点已推进」→ 实际节拍跑在
    # 预设排期前面，二者背离。
    # 北极星⑤：预设**不是法律**，writer 申报是第一手；这里只 surface 二者差异交模型裁量（advisory），
    # **绝不据此硬锁/倒退 stage**（北极星③软牵引）。预设缺失或 writer 未申报 → 自然不产 finding。
    for ch, mck in sorted(declared_beats.items()):
        if not protagonist_stages:
            break
        preset = _stage_at_ch(protagonist_stages, ch)
        preset_o = stage_order(preset)
        if preset_o < 0:
            continue
        need_glimpsed = bool(mck.get("need_glimpsed_this_ch"))
        moral_advanced = bool(mck.get("moral_argument_advanced"))
        # 深层 need/truth 弧节拍在「仍是早期 lie 区（order ≤ 1）」就被 writer 申报推进
        if (need_glimpsed and moral_advanced) and preset_o <= ARC_STAGE_ORDER["lie_cracking"]:
            findings.append({
                "severity": "advisory",
                "code": "BEAT_STAGE_DIVERGENCE",
                "ch": ch,
                "preset_stage": preset,
                "declared_beats": {
                    "need_glimpsed_this_ch": mck.get("need_glimpsed_this_ch"),
                    "moral_argument_advanced": moral_advanced,
                    "desire_pursued_this_ch": mck.get("desire_pursued_this_ch"),
                    "ghost_triggered": bool(mck.get("ghost_triggered")),
                },
                "suggestion": (
                    f"ch{ch} writer 申报主角已窥见 need + 道德论点已推进，但预设 Save-the-Cat 排期此章"
                    f"仍在早期 {preset} 阶段 → 实际节拍跑在预设前面。可考虑把预设 stages_by_chapter 上调对齐"
                    f"（或确认 writer 是有意提前埋深度，此为 advisory 仅供裁量，不硬锁/倒退 stage）"
                ),
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
