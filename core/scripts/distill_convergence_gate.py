#!/usr/bin/env python3
"""distill_convergence_gate.py — 🔴 2026-06-27 C01 蒸馏收敛闸

step6（finalize 出货）前的收敛判定：v1 复刻 SFS 是否【严格优于】v0（含相对 floor=v0×0.97）。
落实命令文档 L194『连续 2 轮无新差距 + cluster SFS ≥ 80』的 bounded（单轮）实现——
把 v0→reflect→v1→FINAL 的单遍管线升级为「v1 严格优于才采纳，否则回退 v0」的收敛环。

链路：
  step5 reflect 产 skill_v1 → 本闸前先 distill_replicate(skill_v1) 再 style_evaluator → eval_v1.json
  → 本脚本：parse v0/v1 SFS → validation_gate.decide(rewards_before=[v0], rewards_after=[v1],
    floor=v0×0.97) → 严格优于则采纳 v1，否则回退 v0 → 选中的 skill 写到 --ship-out（=最新版
    skill_v2.md，finalize 的 _latest_skill 出货它）。

北极星护栏（铁律）：
  ① 复用 skill_opt.validation_gate.decide —— reward 只读现有 SFS（binary/连续信号），不引入新 hard_gate。
  ② floor 用相对锚 v0×0.97（validation_gate L6 地板·防长期漂移），不用平直 ΔSFS<ε。
  ③ 收敛闸只【选 skill】不【阻断 plan】—— 永远 exit 0。SFS 跑飞的真出货拦截在 step7
     distill_finalize_verify 的 SFS 灾难闸（<55/grade D/相对 v0 暴跌·--strict exit 2）。
  ④ SFS 必走 multi-ref（铁律 feedback_distill_sfs_multi_ref）—— 由上游 style_evaluator 调用保证。
  ⑤ infra 失败（任一 eval SFS 不可解析）→ 不回退、出货 v1（reflect 改进版）+ WARN，
     不让评分崩溃丢弃 reflect 工作（真灾难闸仍在 step7）。

用法：
  python core/scripts/distill_convergence_gate.py \\
    --before-eval <对比报告/eval_v0.json> --after-eval <对比报告/eval_v1.json> \\
    --skill-before <skill_v0.md> --skill-after <skill_v1.md> \\
    --ship-out <skill_v2.md> [--ship-sfs-out <对比报告/eval_ship.json>] [--floor-ratio 0.97]
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from pathlib import Path

CWD = Path(__file__).resolve().parent
if str(CWD) not in sys.path:
    sys.path.insert(0, str(CWD))

from skill_opt.validation_gate import decide  # noqa: E402 — 复用·不改


def parse_sfs_eval(path: Path) -> tuple[float | None, str | None]:
    """从 style_evaluator eval JSON 解析 (total_sfs, grade)。口径对齐 finalize_distill.py。"""
    try:
        ev = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    ps = ev.get("programmatic_score") or {}
    score = ev.get("sfs_quick")
    if score is None:
        score = ev.get("total")
    if score is None:
        score = ps.get("total")
    grade = ev.get("grade") or ps.get("grade")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return score, (str(grade) if grade else None)


def converge_decision(v0_sfs: float | None, v1_sfs: float | None,
                      floor_ratio: float = 0.97) -> tuple[bool, str, dict]:
    """收敛判定（纯函数·可测）。

    返回 (accept_v1, ship_label, detail)：
      · 两 SFS 均可解析 → validation_gate.decide([v0],[v1], floor=v0×floor_ratio)：
        严格优于（含未跌破 floor）→ accept_v1=True 出货 v1；否则回退 v0。
      · 任一不可解析（infra 失败）→ accept_v1=True 出货 v1（不回退·不丢 reflect 工作·北极星⑤）。
    ship_label ∈ {"v1","v0"}（人读日志用）。
    """
    if v0_sfs is None or v1_sfs is None:
        return True, "v1", {
            "accepted": True,
            "reason": (f"SFS 不可解析（v0={v0_sfs} / v1={v1_sfs}）· 出货 reflect 改进版 v1 不回退 · "
                       f"真灾难闸在 step7（北极星⑤）"),
            "v0_sfs": v0_sfs, "v1_sfs": v1_sfs, "floor": None, "infra_skip": True,
        }
    floor = round(v0_sfs * floor_ratio, 4)
    res = decide(rewards_before=[v0_sfs], rewards_after=[v1_sfs], floor=floor)
    return res.accepted, ("v1" if res.accepted else "v0"), {
        "accepted": res.accepted,
        "reason": res.reason,
        "v0_sfs": v0_sfs, "v1_sfs": v1_sfs,
        "floor": floor, "floor_ratio": floor_ratio,
        "delta": res.delta, "infra_skip": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="蒸馏收敛闸（v1 SFS 严格优于 v0 才采纳·否则回退）")
    ap.add_argument("--before-eval", required=True, type=Path, help="v0 SFS eval JSON")
    ap.add_argument("--after-eval", required=True, type=Path, help="v1 SFS eval JSON")
    ap.add_argument("--skill-before", required=True, type=Path, help="skill_v0.md")
    ap.add_argument("--skill-after", required=True, type=Path, help="skill_v1.md")
    ap.add_argument("--ship-out", required=True, type=Path,
                    help="选中的 skill 写到此路径（=最新版·finalize 出货它）")
    ap.add_argument("--ship-sfs-out", type=Path,
                    help="选中 skill 对应的 eval JSON 拷到此处（供 finalize --sfs 写 log）")
    ap.add_argument("--floor-ratio", type=float, default=0.97,
                    help="相对 floor 比例（validation_gate L6 地板·默认 0.97）")
    args = ap.parse_args(argv)

    v0_sfs, v0_grade = parse_sfs_eval(args.before_eval)
    v1_sfs, v1_grade = parse_sfs_eval(args.after_eval)
    print(f"[converge] v0 SFS={v0_sfs}({v0_grade}) · v1 SFS={v1_sfs}({v1_grade})", file=sys.stderr)

    accept_v1, ship_label, detail = converge_decision(v0_sfs, v1_sfs, args.floor_ratio)
    print(f"[converge] {detail['reason']}", file=sys.stderr)

    src_skill = args.skill_after if accept_v1 else args.skill_before
    src_eval = args.after_eval if accept_v1 else args.before_eval
    if not src_skill.exists():
        # 选中的 skill 文件不存在 → 退另一个能用的（绝不让收敛闸把出货卡死·北极星③不阻断）
        fallback = args.skill_before if accept_v1 else args.skill_after
        print(f"[converge][WARN] 选中 skill 不存在: {src_skill} · 退 {fallback}", file=sys.stderr)
        src_skill, src_eval = fallback, (args.before_eval if accept_v1 else args.after_eval)
    if not src_skill.exists():
        print(f"[converge][ERROR] 两个 skill 都不存在 · 无法出货", file=sys.stderr)
        return 0  # 仍不阻断 plan（真闸在 step7）·但不产 ship-out → expected_output 缺会被监控捞

    args.ship_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_skill, args.ship_out)
    print(f"[converge] 采纳 {ship_label}（{src_skill.name}）→ {args.ship_out}", file=sys.stderr)
    if args.ship_sfs_out and src_eval.exists():
        args.ship_sfs_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_eval, args.ship_sfs_out)
        print(f"[converge] 选中 eval（{src_eval.name}）→ {args.ship_sfs_out}", file=sys.stderr)

    # 收敛闸只选 skill 不阻断 plan（北极星③）—— 永远 exit 0
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
