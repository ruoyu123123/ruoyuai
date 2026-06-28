"""cross_cluster_engagement_metrics_aggregate.py — 章末钩子/Golden Three/lazy_spawn 跨章趋势（CCR17）

3 类「单章已扫但跨章未趋势化」检测：

A. HOOK_STRENGTH_TREND
   读 _数据库/.audit/ch_NNN_audit.json 中 hook_strength 评分（如 hook_strength_scanner 输出）
   - HOOK_DECLINE：连续 ≥ 3 章评分下降 = 读者流失风险
   - HOOK_PERSISTENT_LOW：≥ 3 章评分 < 0.4 = 章末钩子持续弱

B. GOLDEN_THREE_TREND
   读 audit 报告中 golden_three（kindling / hook / turn 三大）数据
   - GOLDEN_DEGRADATION：连续 ≥ 3 章某项下降
   - GOLDEN_FLAT：≥ 5 章三项几乎一致 = 缺乏起伏

C. LAZY_SPAWN_PROMOTION
   读 _数据库/角色池.json + 历史 emerged（canonical）
   - SPAWN_NEVER_PROMOTED：spawn_at_ch 后 ≥ 10 章 promoted_to_emerged_at_ch=null
   - PROPOSED_EMERGED_REJECTED_TOO_MANY：writer 提议 _propose_emerged 但被驳回率过高

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



# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

_eng_sys = __import__("sys")
_eng_sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：账本驱动 hook/golden


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_chapters(project_root: Path, last_n: int) -> list[int]:
    chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                 for d in (project_root / "章节").glob("第*章")
                 if re.match(r"第(\d+)章", d.name))
    return chs[-last_n:] if chs else []


# ---------- A + B. 从 audit 报告收集分数 ----------

def collect_audit_scores(project_root: Path, chapters: list[int]) -> dict:
    """返回 {hook: [(ch, score)], golden_kindling: [...], golden_hook: [...], golden_turn: [...]}"""
    audit_dir = project_root / "_数据库" / ".audit"
    out = {"hook": [], "golden_kindling": [], "golden_hook": [], "golden_turn": []}
    if not audit_dir.exists():
        return out
    for ch in chapters:
        # 兼容多种 audit 命名
        for name in [f"ch_{ch:03d}_audit.json", f"第{ch:03d}章_audit.json"]:
            p = audit_dir / name
            if not p.exists():
                continue
            data = load_json(p, {})
            # hook_strength
            hs = (data.get("hook_strength") or
                  (data.get("scanners") or {}).get("hook_strength") or
                  (data.get("scores") or {}).get("hook_strength"))
            if isinstance(hs, dict):
                hs = hs.get("score") or hs.get("overall")
            if isinstance(hs, (int, float)):
                out["hook"].append((ch, float(hs)))
            # golden_three
            gt = (data.get("golden_three") or
                  (data.get("scanners") or {}).get("golden_three") or
                  (data.get("scores") or {}).get("golden_three"))
            if isinstance(gt, dict):
                for k in ["kindling", "hook", "turn"]:
                    v = gt.get(k)
                    if isinstance(v, dict):
                        v = v.get("score")
                    if isinstance(v, (int, float)):
                        out[f"golden_{k}"].append((ch, float(v)))
            break
    return out


# ---------- A. HOOK_STRENGTH_TREND ----------

def scan_hook_trend(scores: list[tuple[int, float]]) -> list[dict]:
    findings = []
    if len(scores) < 3:
        return []
    # HOOK_DECLINE
    decline_streak = 0
    for i in range(1, len(scores)):
        if scores[i][1] < scores[i - 1][1]:
            decline_streak += 1
            if decline_streak >= 3:
                trail = scores[i - 3:i + 1]
                findings.append({
                    "severity": "warning",
                    "code": "HOOK_STRENGTH_DECLINE",
                    "trail": trail,
                    "suggestion": f"hook_strength 连续 {decline_streak + 1} 章下降 → 读者流失风险",
                })
                decline_streak = 0
        else:
            decline_streak = 0
    # HOOK_PERSISTENT_LOW
    low_chs = [ch for ch, s in scores if s < 0.4]
    if len(low_chs) >= 3:
        findings.append({
            "severity": "advisory",
            "code": "HOOK_PERSISTENT_LOW",
            "low_chs": low_chs,
            "suggestion": f"hook_strength 在 {len(low_chs)} 章 < 0.4 → 章末钩子持续弱",
        })
    return findings


# ---------- B. GOLDEN_THREE_TREND ----------

def scan_golden_trend(scores_dict: dict) -> list[dict]:
    findings = []
    for key in ["golden_kindling", "golden_hook", "golden_turn"]:
        scores = scores_dict.get(key, [])
        if len(scores) < 3:
            continue
        # DEGRADATION
        decline_streak = 0
        for i in range(1, len(scores)):
            if scores[i][1] < scores[i - 1][1]:
                decline_streak += 1
                if decline_streak >= 3:
                    findings.append({
                        "severity": "advisory",
                        "code": "GOLDEN_DEGRADATION",
                        "metric": key,
                        "trail": scores[i - 3:i + 1],
                        "suggestion": f"{key} 连续 {decline_streak + 1} 章下降",
                    })
                    decline_streak = 0
            else:
                decline_streak = 0
        # FLAT
        if len(scores) >= 5:
            recent5 = [s for _, s in scores[-5:]]
            if max(recent5) - min(recent5) < 0.1:
                findings.append({
                    "severity": "advisory",
                    "code": "GOLDEN_FLAT",
                    "metric": key,
                    "range": [min(recent5), max(recent5)],
                    "suggestion": f"{key} 近 5 章波动 < 0.1 → 缺乏起伏",
                })
    return findings


# ---------- D. CLIFFHANGER_QUOTA_OVER（R7 W2 Batch-E·2026-06-20）----------
# 章末配比软目标 80:15:5(hook:cliffhanger:scene_end)·>25% 章末 cliffhanger 或 ≥3 连续 → advisory。
# 数据源：账本 ending_type（cluster_summary_reader）/ audit 报告 chapter_ending_type。
# 北极星⑤：纯 advisory · 作者档可豁免 · 不进 HARD_GATE_CODES。
_CLIFF_TYPES = ("cliffhanger", "悬念", "悬念性", "钩子型", "悬念型")
_CLIFF_RATIO_THRESHOLD = 0.25
_CLIFF_STREAK_THRESHOLD = 3


def _is_cliffhanger(end_type: str) -> bool:
    """end_type 字符串归一化判定（容忍中英/大小写/前缀混排）。"""
    if not isinstance(end_type, str):
        return False
    t = end_type.strip().lower()
    if not t:
        return False
    for tag in _CLIFF_TYPES:
        if tag.lower() in t:
            return True
    return False


def collect_ending_types(records) -> list[tuple[int, str]]:
    """从账本 ChapterRecord 取 ending_type → [(ch, type)]·缺字段跳过。"""
    out = []
    for ch, rec in records:
        et = rec.get("ending_type")
        if isinstance(et, str) and et.strip():
            out.append((ch, et.strip()))
    return out


def collect_audit_ending_types(project_root: Path, chapters: list[int]) -> list[tuple[int, str]]:
    """从 _数据库/.audit/ch_NNN_audit.json 取 chapter_ending_type / ending_type → [(ch, type)]。"""
    audit_dir = project_root / "_数据库" / ".audit"
    out = []
    if not audit_dir.exists():
        return out
    for ch in chapters:
        for name in [f"ch_{ch:03d}_audit.json", f"第{ch:03d}章_audit.json"]:
            p = audit_dir / name
            if not p.exists():
                continue
            data = load_json(p, {})
            for key in ("chapter_ending_type", "ending_type"):
                v = data.get(key)
                if isinstance(v, dict):
                    v = v.get("type") or v.get("label")
                if isinstance(v, str) and v.strip():
                    out.append((ch, v.strip()))
                    break
            break
    return out


def scan_cliffhanger_quota(end_types: list[tuple[int, str]]) -> list[dict]:
    """章末 cliffhanger 配比/连续度 advisory（>25% 或 ≥3 连续 → CLIFFHANGER_QUOTA_OVER）。"""
    findings = []
    if not end_types:
        return findings
    n = len(end_types)
    cliff_chs = [ch for ch, t in end_types if _is_cliffhanger(t)]
    ratio = len(cliff_chs) / n if n else 0.0
    if ratio > _CLIFF_RATIO_THRESHOLD and n >= 4:
        findings.append({
            "severity": "advisory",
            "code": "CLIFFHANGER_QUOTA_OVER",
            "metric": "cliffhanger_ratio",
            "ratio": round(ratio, 3),
            "threshold": _CLIFF_RATIO_THRESHOLD,
            "cliffhanger_chs": cliff_chs,
            "chapters_scanned": n,
            "suggestion": (
                f"章末 cliffhanger 占比 {ratio:.0%} > 软目标 25% "
                f"(80 hook : 15 cliffhanger : 5 scene_end)·读者疲劳风险·"
                f"考虑下个 cluster 改用 hook/scene_end 收束"),
        })
    # 连续 ≥3 章 cliffhanger
    streak = 0
    streak_start = None
    seen_streaks = []
    for ch, t in end_types:
        if _is_cliffhanger(t):
            if streak == 0:
                streak_start = ch
            streak += 1
            if streak >= _CLIFF_STREAK_THRESHOLD:
                seen_streaks.append((streak_start, ch, streak))
        else:
            if streak >= _CLIFF_STREAK_THRESHOLD:
                pass   # 已落账·清零
            streak = 0
            streak_start = None
    if seen_streaks:
        start_ch, end_ch, longest = max(seen_streaks, key=lambda x: x[2])
        findings.append({
            "severity": "advisory",
            "code": "CLIFFHANGER_QUOTA_OVER",
            "metric": "consecutive_cliffhanger_streak",
            "streak": longest,
            "threshold": _CLIFF_STREAK_THRESHOLD,
            "chapter_range": [start_ch, end_ch],
            "suggestion": (
                f"连续 {longest} 章末 cliffhanger（ch{start_ch}-ch{end_ch}）·"
                f"过度反转/连环钩子导致读者疲劳失信任（D7「惊讶过量为负效应」）·"
                f"下个 cluster 给一次喘息/收束"),
        })
    return findings


# ---------- C. LAZY_SPAWN_PROMOTION ----------

def scan_lazy_spawn(project_root: Path, chapters: list[int]) -> list[dict]:
    pool_path = project_root / "_数据库" / "角色池.json"
    if not pool_path.exists():
        return []
    pool = load_json(pool_path, {})
    findings = []
    max_ch = max(chapters) if chapters else 0
    # 🔴 2026-06-28 角色池schema统一canonical：读 emerged（不兼容）
    for c in pool.get("emerged", []) or []:
        # 2026-05-29 复审修复 [L17]：emerged 可能混入字符串项（如仅角色名），
        # 直接 c.get() 会 AttributeError 崩溃 → 加 isinstance 守卫跳过非 dict 项。
        if not isinstance(c, dict):
            continue
        spawned = c.get("spawned_at_ch") or 0
        promoted = c.get("promoted_to_emerged_at_ch")
        cid = c.get("id")
        if spawned and promoted is None and max_ch - spawned >= 10:
            findings.append({
                "severity": "advisory",
                "code": "LAZY_SPAWN_NEVER_PROMOTED",
                "character": cid,
                "spawned_at_ch": spawned,
                "current_max_ch": max_ch,
                "gap": max_ch - spawned,
                "suggestion": f"角色「{cid}」spawn 在 ch{spawned}，已过 {max_ch - spawned} 章未 promoted → 应 promote 或归 extras",
            })
    return findings


# ============================================================
# 2026-05-29 cluster 化：账本驱动分支
# CLUSTER_MODE=1 且账本含 hook_score/golden_scores → 从 ChapterRecord 取分数序列，
# 复用既有 scan_hook_trend / scan_golden_trend（纯分数 list 入参，逻辑零改）。
# spawn 检测继续读 角色池.json（spawn_events 是 cluster 级字段，角色池仍是权威）。
# --last-n 在 cluster 模式 = 最后 N 个 cluster。账本缺字段 → 回退逐章 audit（零回归）。
# ============================================================

def collect_ledger_scores(recs) -> dict:
    """从账本 ChapterRecord 取 hook_score / golden_scores → 与 collect_audit_scores 同结构。"""
    out = {"hook": [], "golden_kindling": [], "golden_hook": [], "golden_turn": []}
    for ch, rec in recs:
        hs = rec.get("hook_score")
        if isinstance(hs, (int, float)):
            out["hook"].append((ch, float(hs)))
        gs = rec.get("golden_scores")
        if isinstance(gs, dict):
            for k in ["kindling", "hook", "turn"]:
                v = gs.get(k)
                if isinstance(v, (int, float)):
                    out[f"golden_{k}"].append((ch, float(v)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)

    use_ledger = IS_CLUSTER_MODE and (
        csr.ledger_has_field(project_root, "hook_score")
        or csr.ledger_has_field(project_root, "golden_scores")
    )
    if use_ledger:
        recs = csr.get_chapter_records(project_root, last_n_clusters=args.last_n)
        chapters = [ch for ch, _ in recs]
        if not chapters:
            print("[SKIP] 无账本章记录")
            sys.exit(0)
        scores_dict = collect_ledger_scores(recs)
        findings = []
        findings.extend(scan_hook_trend(scores_dict.get("hook", [])))
        findings.extend(scan_golden_trend(scores_dict))
        findings.extend(scan_lazy_spawn(project_root, chapters))
        # R7 W2 Batch-E：章末 cliffhanger 配比 advisory（账本驱动）
        end_types = collect_ending_types(recs)
        findings.extend(scan_cliffhanger_quota(end_types))
    else:
        chapters = get_chapters(project_root, args.last_n)
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)

        scores_dict = collect_audit_scores(project_root, chapters)
        findings = []
        findings.extend(scan_hook_trend(scores_dict.get("hook", [])))
        findings.extend(scan_golden_trend(scores_dict))
        findings.extend(scan_lazy_spawn(project_root, chapters))
        # R7 W2 Batch-E：章末 cliffhanger 配比 advisory（audit 报告驱动）
        end_types = collect_audit_ending_types(project_root, chapters)
        findings.extend(scan_cliffhanger_quota(end_types))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "engagement_metrics",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "scores_collected": {k: len(v) for k, v in scores_dict.items()},
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"engagement_metrics_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[engagement_metrics] {summary['warning']} warning / {summary['advisory']} advisory")
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
