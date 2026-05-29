"""evolution_canary.py — Evolution 改动章节级 canary + 用户隐式不满信号（v22.5 L16+L17）

业界 2026 共识：
- LLM features 必须 gradual rollout（1% → 5% → 20% → 100%）
- NPS = Feedback Theater（Goodhart 受害者），implicit signals 3x 更预测

我们 single-user 系统不能按 user 分流，改按**章节级 canary**：
- evolution 改动 → 标 canary_window=N 章
- N 章内监测：judge 分数 / 用户重写 / 用户 reconcile / implicit 不满
- N 章后评估：通过 → graduate（永久）/ 失败 → revert

implicit dissatisfaction signals：
1. RESILIENT_USER_REWRITE: 用户手动 Edit 章节 .txt （writer 输出不满意）
2. RECONCILE_TRIGGERED_POST_EVOLUTION: 改动后 N 章内触发 reconcile
3. CHAPTER_RETRY_RATE_UP: 改动后章节重试率上升
4. USER_REGRESSION_TO_OLD_BEHAVIOR: 用户改 用户偏好.json 回滚某偏好

输出：_数据库/.learning/evolution_canary_<ts>.json

2026-05-29 cluster 化：
  v27 freestyle 浮动章数下「最近 5 章 canary 窗口」失准（章字数被 splitter 均一化，
  章数不再反映 evolution 影响量）。canary 窗口改 **最近 N 个 cluster**（默认 2 个），
  窗口章 = 这些 cluster 的物理章并集；评估优先用 cluster 级 judge_grade，回退逐章
  judge 分数。cluster 账本缺失时回退原逐章窗口（向后兼容，能力不删）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_summary_reader as csr  # 2026-05-29 cluster 化：cluster 窗口
except Exception:  # 防御：缺模块退回逐章窗口
    csr = None

# 2026-05-29 cluster 化：grade → 数值（与 judge_score_normalize 同表，0-5 区间）
GRADE_TO_SCORE = {"A": 5.0, "A-": 4.5, "B+": 4.0, "B": 3.5, "B-": 3.0,
                  "C+": 2.5, "C": 2.0, "C-": 1.5, "D": 1.0, "F": 0.0}


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_evolution_log(project_root: Path) -> list[dict]:
    """读 evolution 历史（skill_evolver._evolution_log + meta-prompt suggestions）"""
    log = []
    exp = load_json(project_root / "_数据库" / "写作经验.json", {})
    for e in exp.get("_evolution_log", []) or []:
        log.append({**e, "source": "skill_evolver"})
    # 加 meta-prompt suggestions（视为 evolution proposal）
    sg_dir = project_root / "_数据库" / ".evolution"
    if sg_dir.exists():
        for f in sg_dir.glob("prompt_suggestions_*.json"):
            data = load_json(f, {})
            log.append({
                "ts": data.get("ts"),
                "source": "meta-prompt-optimizer",
                "issues_found": len(data.get("issues_found", []) or []),
            })
    return sorted(log, key=lambda x: x.get("ts", ""), reverse=True)[:10]


def detect_implicit_dissatisfaction(project_root: Path, window_chs: list[int]) -> list[dict]:
    """detect implicit unhappy signals in canary window"""
    findings = []

    # 1. 用户 Edit 章节 .txt（mtime vs save-state mtime 差异）
    # 简化：检测章节 .txt 比对应 _changes.json 新
    for ch in window_chs:
        text_p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
        changes_p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
        if text_p.exists() and changes_p.exists():
            if text_p.stat().st_mtime > changes_p.stat().st_mtime + 300:  # 5min 容忍
                findings.append({
                    "code": "USER_EDITED_TEXT_POST_WRITE",
                    "ch": ch,
                    "signal": f"ch{ch} .txt 在 _changes.json 写后被用户编辑 → writer 输出不满意"
                })

    # 2. reconcile 触发频次（git log）
    try:
        result = subprocess.run(["git", "-C", str(project_root), "log", "--all", "--oneline", "-30"],
                                capture_output=True, text=True, timeout=15, encoding="utf-8")
        recent_log = result.stdout
        reconcile_count = sum(1 for line in recent_log.split("\n") if "reconcile" in line.lower())
        if reconcile_count >= 2:
            findings.append({
                "code": "RECONCILE_FREQUENT_POST_EVOLUTION",
                "count": reconcile_count,
                "signal": f"近期 {reconcile_count} 次 reconcile → evolution 后设定反复改"
            })
    except Exception:
        pass

    # 3. chapter retry rate（同章多次 audit）
    audit_dir = project_root / "_数据库" / ".audit"
    if audit_dir.exists():
        per_ch_audit = Counter()
        for f in audit_dir.glob("ch_*_audit*.json"):
            m = re.match(r"ch_(\d+)", f.name)
            if m:
                ch = int(m.group(1))
                if ch in window_chs:
                    per_ch_audit[ch] += 1
        high_retry = {ch: c for ch, c in per_ch_audit.items() if c >= 3}
        if high_retry:
            findings.append({
                "code": "HIGH_RETRY_IN_CANARY",
                "details": high_retry,
                "signal": f"canary 窗口内 {len(high_retry)} 章 audit ≥ 3 次 → writer 不稳定"
            })

    # 4. 用户偏好回滚（用户改 用户偏好.json 后某字段）
    # 简化：检测 用户偏好.json 是否近期被改
    prefs_p = project_root / "_数据库" / "用户偏好.json"
    if prefs_p.exists():
        latest_ch_time = 0
        for ch in window_chs[-3:]:
            cp = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
            if cp.exists():
                latest_ch_time = max(latest_ch_time, cp.stat().st_mtime)
        if prefs_p.stat().st_mtime > latest_ch_time + 60:  # 偏好比最近章节新 = 用户调整了
            findings.append({
                "code": "USER_ADJUSTED_PREFERENCES",
                "signal": "近期用户改了 用户偏好.json → evolution 偏好与用户期望不符"
            })

    return findings


def _resolve_canary_window(project_root: Path, canary_window_clusters: int):
    """2026-05-29 cluster 化：返回 (unit, window_chs, cluster_ids, cluster_grade_scores)。

    优先取最近 N 个 cluster 的物理章并集 + cluster 级 judge_grade；
    cluster 账本缺失则回退最近 (N×4) 章窗口（unit='chapter'）。
    """
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))

    if csr is not None:
        clusters = csr.get_clusters(project_root, last_n=canary_window_clusters)
        if clusters:
            window_chs = []
            cluster_ids = []
            grade_scores = []
            for c in clusters:
                cluster_ids.append(c.get("cluster_id"))
                cr = c.get("chapter_range")
                if isinstance(cr, list) and len(cr) == 2:
                    window_chs.extend(range(cr[0], cr[1] + 1))
                else:
                    window_chs.extend(int(k) for k in (c.get("chapters") or {}) if str(k).isdigit())
                g = c.get("judge_grade")
                if isinstance(g, str) and g in GRADE_TO_SCORE:
                    grade_scores.append((c.get("cluster_id"), GRADE_TO_SCORE[g]))
            window_chs = sorted(set(window_chs)) or chapters[-(canary_window_clusters * 4):]
            return "cluster", window_chs, cluster_ids, grade_scores

    # 回退逐章窗口
    fallback_n = max(canary_window_clusters * 4, 5)
    return "chapter", chapters[-fallback_n:] if chapters else [], [], []


def canary_evaluate(project_root: Path, canary_window: int = 2) -> dict:
    """对最近一次 evolution 做 canary 评估（2026-05-29 cluster 化：窗口单位 = cluster）"""
    evolution_log = get_evolution_log(project_root)
    if not evolution_log:
        return {"status": "no_evolution_yet", "msg": "无 evolution 历史可评估"}

    last_evol = evolution_log[0]
    unit, window_chs, cluster_ids, grade_scores = _resolve_canary_window(project_root, canary_window)
    if not window_chs:
        return {"status": "insufficient_chapters", "need_clusters": canary_window, "have_chs": 0}

    # judge 分数趋势（逐章 0-10 分；cluster 级用 judge_grade 0-5 锚点）
    judge_dir = project_root / "_数据库" / ".judge_reports"
    canary_scores = []
    for ch in window_chs:
        for name in [f"ch_{ch:03d}_audit-hub.json", f"ch_{ch:03d}_consensus.json"]:
            p = judge_dir / name
            if p.exists():
                d = load_json(p, {})
                s = (d.get("score") or d.get("overall_score") or
                     (d.get("aggregated") or {}).get("score"))
                if isinstance(s, (int, float)):
                    canary_scores.append((ch, float(s)))
                break

    avg_score = sum(s for _, s in canary_scores) / len(canary_scores) if canary_scores else 0
    # cluster 级 grade 均值（0-5）作为补充信号
    avg_grade = (sum(s for _, s in grade_scores) / len(grade_scores)) if grade_scores else None
    dissatisfaction = detect_implicit_dissatisfaction(project_root, window_chs)

    # 评估
    verdict = "graduate"  # 默认通过
    reasons = []
    if dissatisfaction:
        verdict = "needs_review"
        reasons.append(f"{len(dissatisfaction)} 个 implicit dissatisfaction signals")
    if avg_score and avg_score < 6.0:
        verdict = "revert_recommended"
        reasons.append(f"canary avg score {avg_score:.1f} < 6.0")
    # cluster 级 grade 兜底（无逐章 0-10 分时用 grade ≤ C 即 2.0 触发）
    elif not avg_score and avg_grade is not None and avg_grade < 2.5:
        verdict = "revert_recommended"
        reasons.append(f"canary cluster grade 均值 {avg_grade:.1f}/5 < 2.5（≈C-）")

    return {
        "scan_type": "evolution_canary",
        "analysis_unit": unit,  # 2026-05-29 cluster 化
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "last_evolution": last_evol,
        "canary_window_clusters": cluster_ids,
        "canary_window_chs": window_chs,
        "judge_scores": canary_scores,
        "avg_score": round(avg_score, 2) if avg_score else None,
        "avg_cluster_grade": round(avg_grade, 2) if avg_grade is not None else None,
        "dissatisfaction_signals": dissatisfaction,
        "verdict": verdict,
        "verdict_reasons": reasons,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    # 2026-05-29 cluster 化：--window 单位从「章」改「cluster」（默认 2 cluster ≈ 旧 5 章）
    ap.add_argument("--window", type=int, default=2,
                    help="canary 窗口 = 最近 N 个 cluster（账本缺失回退 N×4 章）")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    result = canary_evaluate(project_root, args.window)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"evolution_canary_{ts}.json"
    save_json(out_path, result)
    print(f"[evolution_canary] verdict={result.get('verdict')} reasons={result.get('verdict_reasons')}")
    for f in result.get("dissatisfaction_signals", [])[:5]:
        print(f"  [DISSAT] {f.get('code')}: {f.get('signal', '')[:80]}")
    print(f"  报告: {out_path}")
    if result.get("verdict") == "revert_recommended":
        sys.exit(2)
    if result.get("verdict") == "needs_review":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
