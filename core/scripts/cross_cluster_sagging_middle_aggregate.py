"""cross_cluster_sagging_middle_aggregate.py — Sagging Middle 检测（R7 Batch-D · 2026-06-20）

【缺口】调研 Save the Cat / Story Grid 实证：中段塌陷（sagging middle）是 LLM/新手
通病——故事 40-60% 区间常常变成「重复刷怪/无升级」，缺反转 + 缺质变 + 缺新目标。

本 aggregator 在「全书 40-60% 区段」（按 cluster 序）三规则联合判断：
  ① 反转零命中：区段所有 cluster 的 golden_scores.turn / hook_score 均值偏低 + 无 cluster 命中
     turning_point / structure_compliance.beat_signal_hit
  ② stakes 仅量变：区段 stress_total 仅波动不抬升 + scene_type 多样性低（重复打怪场景）
  ③ Scene Purpose 五选一全 0：每 cluster 检查 scene_type/beats_addressed/beat 是否落在
     {reveal, reversal, escalate, decision, transformation} 五大「推动型 purpose」之一·
     连续 ≥3 cluster 全 0 = 推进力归零

满足任一 → advisory · 满足 ≥2 → warning · 建议 needs_midpoint_bomb 标志（manifest 用）。

env SAGGING_MIDDLE_MODE：off / shadow（默认·零回归）/ active。

退出码: 0 健康 / 1 advisory（shadow 不上报） / 2 warning
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402

IS_CLUSTER_MODE = os.environ.get("CLUSTER_MODE") == "1"

CODE_REVERSAL_VOID = "SAGGING_MIDDLE_REVERSAL_VOID"
CODE_STAKES_FLAT = "SAGGING_MIDDLE_STAKES_FLAT"
CODE_PURPOSE_VOID = "SAGGING_MIDDLE_PURPOSE_VOID"
CODE_NEEDS_BOMB = "SAGGING_MIDDLE_NEEDS_MIDPOINT_BOMB"

# Scene Purpose 五选一推动型动作
DRIVE_PURPOSES = {
    "reveal", "reversal", "escalate", "decision", "transformation",
    "揭露", "反转", "升级", "抉择", "转化",
}

MIDDLE_LO = 0.40
MIDDLE_HI = 0.60
TURN_SCORE_FLOOR = 0.50         # golden_scores.turn 均值低于此 = 反转弱
HOOK_SCORE_FLOOR = 0.45         # hook_score 均值低于此 = 钩子弱
PURPOSE_VOID_STREAK = 3         # 连续 N cluster 全 0 推动 purpose


def _mode() -> str:
    m = (os.environ.get("SAGGING_MIDDLE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _middle_slice(clusters: list[dict]) -> list[dict]:
    """全书 cluster 序中 40-60% 区段。"""
    n = len(clusters)
    if n < 5:
        return []  # 太短无法判中段
    lo = int(n * MIDDLE_LO)
    hi = max(lo + 1, int(n * MIDDLE_HI) + 1)
    return clusters[lo:hi]


def _cluster_chapter_records(c: dict) -> list[dict]:
    chs = c.get("chapters") or {}
    if not isinstance(chs, dict):
        return []
    return [r for r in chs.values() if isinstance(r, dict)]


def _has_drive_purpose(rec: dict) -> bool:
    """检查 ChapterRecord 是否有推动型 scene purpose。"""
    # scene_type / beat / beats_addressed 任一命中 DRIVE_PURPOSES
    st = (rec.get("scene_type") or "").strip().lower()
    if any(p in st for p in DRIVE_PURPOSES):
        return True
    beat = (rec.get("beat") or "").strip().lower()
    if any(p in beat for p in DRIVE_PURPOSES):
        return True
    for b in rec.get("beats_addressed", []) or []:
        if isinstance(b, str) and any(p in b.lower() for p in DRIVE_PURPOSES):
            return True
    # turning_point 非空也算（写了显式转折）
    tp = (rec.get("turning_point") or "").strip()
    if tp:
        return True
    # structure_compliance: beat_signal_hit
    if rec.get("beat_signal_hit") is True:
        return True
    return False


def _cluster_has_drive(c: dict) -> bool:
    return any(_has_drive_purpose(r) for r in _cluster_chapter_records(c))


def _avg(values: list[float]) -> float:
    vs = [float(v) for v in values if isinstance(v, (int, float))]
    return sum(vs) / len(vs) if vs else 0.0


def detect_reversal_void(middle: list[dict]) -> dict:
    """① 反转零命中：goldens.turn / hook_score 均值偏低 + 无 cluster 命中 turning_point。"""
    turn_scores = []
    hook_scores = []
    cluster_turn_hits = 0
    for c in middle:
        for rec in _cluster_chapter_records(c):
            gs = rec.get("golden_scores") or {}
            if isinstance(gs, dict):
                t = gs.get("turn")
                if isinstance(t, (int, float)):
                    turn_scores.append(float(t))
            h = rec.get("hook_score")
            if isinstance(h, (int, float)):
                hook_scores.append(float(h))
        if _cluster_has_drive(c):
            cluster_turn_hits += 1
    turn_avg = _avg(turn_scores)
    hook_avg = _avg(hook_scores)
    hit = (cluster_turn_hits == 0
           and (turn_avg < TURN_SCORE_FLOOR or hook_avg < HOOK_SCORE_FLOOR
                or (not turn_scores and not hook_scores)))
    return {
        "hit": hit,
        "turn_avg": round(turn_avg, 3),
        "hook_avg": round(hook_avg, 3),
        "cluster_turn_hits": cluster_turn_hits,
        "samples_turn": len(turn_scores),
        "samples_hook": len(hook_scores),
    }


def detect_stakes_flat(middle: list[dict]) -> dict:
    """② stakes 仅量变：stress 均值低 + scene_type 多样性低。"""
    stress_values: list[int] = []
    scene_types: list[str] = []
    for c in middle:
        for rec in _cluster_chapter_records(c):
            s = rec.get("stress_total")
            if isinstance(s, (int, float)):
                stress_values.append(int(s))
            st = (rec.get("scene_type") or "").strip()
            if st:
                scene_types.append(st)
    # stress trend: 看 max-min 是否大（>0 抬升）
    stress_range = (max(stress_values) - min(stress_values)) if stress_values else 0
    stress_rising = any(
        stress_values[i] > stress_values[0] + 1
        for i in range(1, len(stress_values))
    ) if stress_values else False
    distinct_types = len(set(scene_types))
    # 触发：stress 不抬升 + 场景类型 <=2 种（高重复）
    hit = (not stress_rising and distinct_types <= 2 and len(scene_types) >= 3)
    return {
        "hit": hit,
        "stress_samples": len(stress_values),
        "stress_range": stress_range,
        "stress_rising": stress_rising,
        "scene_type_distinct": distinct_types,
        "scene_type_samples": len(scene_types),
    }


def detect_purpose_void(middle: list[dict]) -> dict:
    """③ Scene Purpose 五选一全 0：连续 ≥3 cluster 无推动 purpose 命中。"""
    streak = 0
    max_streak = 0
    voids = []
    for c in middle:
        if _cluster_has_drive(c):
            streak = 0
        else:
            streak += 1
            max_streak = max(max_streak, streak)
            if streak >= PURPOSE_VOID_STREAK:
                voids.append(c.get("cluster_id"))
    return {
        "hit": max_streak >= PURPOSE_VOID_STREAK,
        "max_void_streak": max_streak,
        "void_clusters": voids,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    mode = _mode()
    project_root = Path(args.project)
    if mode == "off":
        print("[OFF] SAGGING_MIDDLE_MODE=off")
        sys.exit(0)

    clusters = csr.get_clusters(project_root)
    if not clusters:
        print("[SKIP] 账本无 cluster 记录")
        sys.exit(0)
    middle = _middle_slice(clusters)
    if not middle:
        print(f"[SKIP] 全书 cluster 太少（{len(clusters)} < 5）·中段未成形")
        sys.exit(0)

    rev = detect_reversal_void(middle)
    sta = detect_stakes_flat(middle)
    pur = detect_purpose_void(middle)

    findings = []
    if rev["hit"]:
        findings.append({
            "severity": "advisory", "code": CODE_REVERSAL_VOID,
            "metrics": rev,
            "suggestion": (f"中段 {len(middle)} 个 cluster 反转零命中（turn={rev['turn_avg']} "
                           f"hook={rev['hook_avg']}）·安排 midpoint reversal/揭露"),
        })
    if sta["hit"]:
        findings.append({
            "severity": "advisory", "code": CODE_STAKES_FLAT,
            "metrics": sta,
            "suggestion": (f"中段 stakes 仅量变·scene_type 仅 {sta['scene_type_distinct']} 种·"
                           f"安排质变升级（新对手/新规则/身份转变）"),
        })
    if pur["hit"]:
        findings.append({
            "severity": "advisory", "code": CODE_PURPOSE_VOID,
            "metrics": pur,
            "suggestion": (f"连续 {pur['max_void_streak']} 个 cluster Scene Purpose 五选一全 0"
                           f"（无揭露/反转/升级/抉择/转化）·推进力归零"),
        })

    hit_count = sum(1 for f in findings if f["severity"] == "advisory")
    needs_bomb = hit_count >= 1
    if hit_count >= 2:
        findings.append({
            "severity": "warning", "code": CODE_NEEDS_BOMB,
            "hit_signals": hit_count,
            "suggestion": "≥2 项中段塌陷信号·强烈建议下个 cluster 插入 midpoint bomb（重大反转/角色死亡/秘密揭露）",
        })

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "sagging_middle",
        "scan_ts": ts,
        "mode": mode,
        "clusters_total": len(clusters),
        "middle_cluster_ids": [c.get("cluster_id") for c in middle],
        "reversal": rev, "stakes": sta, "purpose": pur,
        "needs_midpoint_bomb": needs_bomb,
        "findings": findings,
        "summary": {
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
        },
    }
    # 写 needs_bomb 给 build_manifest 注入用
    snap = {
        "needs_midpoint_bomb": needs_bomb,
        "hit_signals": hit_count,
        "middle_cluster_ids": [c.get("cluster_id") for c in middle],
        "advisory_codes": [f["code"] for f in findings if f["severity"] == "advisory"],
    }
    out_path = out_dir / f"sagging_middle_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    snap_path = out_dir / "sagging_middle_snapshot.json"
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[sagging_middle] middle={len(middle)} cluster · hit_signals={hit_count} · needs_bomb={needs_bomb}")
    for f in findings[:4]:
        print(f"  [{f['severity'].upper()}] {f['code']}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")

    if mode == "shadow":
        for f in findings:
            print(f"[SHADOW] sagging_middle: {f['code']} — 不上报", file=sys.stderr)
        sys.exit(0)
    if report["summary"]["warning"] > 0:
        sys.exit(2)
    if report["summary"]["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
