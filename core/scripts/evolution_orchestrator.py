"""evolution_orchestrator.py — 三角共演化 orchestrator（v22 SE4）

业界 arxiv 2510.23595 Multi-Agent Evolve：Proposer + Solver + Judge 三角共演化。
我们已有三角：
  - Proposer = novel-outline-planner（出走向卡）
  - Solver = novel-writer（写章节）
  - Judge = audit_hub + judge_consensus（评估）

但当前是**单向链**：Proposer → Solver → Judge
缺：Judge 反馈 → 反向校准 Proposer + Solver

本 orchestrator 每 10 章触发：
1. 三角分析：Proposer 卡质量 / Solver 章节质量 / Judge 一致性
2. 反向校准信号：
   - Judge 评分连续低 → outline-planner 卡设计有问题 → 调用 meta-prompt-optimizer
   - 同 finding 重复 → writer 没消费 prev_findings → 加强 manifest 注入
   - judge 间分歧大 → meta-judge 校准
3. 输出 evolution_report.json + 触发 skill_evolver / meta-prompt-optimizer

用法：python evolution_orchestrator.py <project> [--ch N] [--cycle 10]
退出码: 0 ok / 1 需人工审查 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# 2026-05-29 cluster 化：cluster 模式下触发单位从「每 10 章」改为「每 N 个 cluster」，
# 三角共演化分析窗口用 cluster_summary_reader 取最近 N cluster 的章 + cluster judge_grade。
# cluster_lookup 把 --cluster {key} 解析成末章/序号锚点。
try:
    import cluster_summary_reader as _csr  # noqa: E402
except Exception:  # pragma: no cover - 防御性
    _csr = None
try:
    import cluster_lookup as _cl  # noqa: E402
except Exception:  # pragma: no cover - 防御性
    _cl = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


# 2026-05-29 复审修复（L10）：judge 报告实际用字母评级 overall_grade（无数值 score），
# analyze_solver 旧读法 data.get("score") 恒 None → 质量下滑信号永不触发。
# 加 grade→score 兜底（5.0 制 · 与 evolution_canary / gepa_prompt_optimizer 同制，
# 便于跨脚本阈值一致）。
GRADE_TO_SCORE = {
    "A": 5.0, "A-": 4.5, "B+": 4.0, "B": 3.5, "B-": 3.0,
    "C+": 2.5, "C": 2.0, "C-": 1.5, "D": 1.0, "F": 0.0,
}


def _grade_to_score(grade):
    """字母评级 → 5.0 制分数。非法/缺失返回 None。"""
    if isinstance(grade, str):
        return GRADE_TO_SCORE.get(grade.strip().upper())
    return None


def _expand_cluster_chapters(cluster: dict) -> list[int]:
    """2026-05-29 复审修复（L9）：把一个 cluster 展开成它包含的章号列表。

    优先用 chapter_range [lo, hi] 全展开（splitter 切定后的权威范围）；range 缺/非法时
    回退 chapters{} 的 key（builder 已写章记录）。两者都无 → 空列表。
    SC-3：不反查目标 cluster 自身尚未回填的 range —— 这里只读已落账 cluster 自带的 range，
    不做跨 cluster 推算，故安全。
    """
    chs: list[int] = []
    cr = cluster.get("chapter_range")
    if isinstance(cr, list) and len(cr) == 2 \
            and isinstance(cr[0], int) and isinstance(cr[1], int) and cr[0] <= cr[1]:
        chs = list(range(cr[0], cr[1] + 1))
    if not chs:
        for k in (cluster.get("chapters") or {}).keys():
            try:
                chs.append(int(k))
            except (ValueError, TypeError):
                pass
    return sorted(set(chs))


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def analyze_proposer(project_root: Path, recent_chs: list[int]) -> dict:
    """分析 outline-planner 出卡质量：fate_cards_compliance + user 选择分布"""
    wal_dir = project_root / "_数据库" / ".wal"
    if not wal_dir.exists():
        return {"signal": "no_data"}
    findings = []
    cards_with_violation = 0
    label_dist = Counter()
    for ch in recent_chs:
        cards_path = wal_dir / f"第{ch:03d}章_fate_cards.json"
        if not cards_path.exists():
            continue
        cards = load_json(cards_path, {})
        for c in cards.get("cards", []) or []:
            cd = c.get("character_driven", {})
            if not cd or not cd.get("aspect_compatibility_check", True):
                cards_with_violation += 1
            label_dist[c.get("label", "?")] += 1

    if cards_with_violation > len(recent_chs) * 0.3:
        findings.append({
            "signal": "PROPOSER_LOW_QUALITY",
            "evidence": f"{cards_with_violation} 张卡含 character_driven 缺失/违反",
            "suggestion": "调用 meta-prompt-optimizer 改进 outline-planner prompt",
        })
    return {"signal": "ok" if not findings else "low_quality", "findings": findings, "label_distribution": dict(label_dist)}


def analyze_solver(project_root: Path, recent_chs: list[int]) -> dict:
    """分析 writer 章节质量：judge score 趋势 + 重复 finding"""
    judge_dir = project_root / "_数据库" / ".judge_reports"
    if not judge_dir.exists():
        return {"signal": "no_data"}
    findings = []
    finding_codes = Counter()
    judge_scores = []
    for ch in recent_chs:
        for name in [f"ch_{ch:03d}_audit-hub.json", f"ch_{ch:03d}_consensus.json"]:
            f = judge_dir / name
            if not f.exists():
                continue
            data = load_json(f, {})
            score = (data.get("score") or data.get("overall_score") or
                     (data.get("aggregated") or {}).get("score") or
                     (data.get("scores") or {}).get("overall"))
            # L10：无数值 score 时回退字母评级 overall_grade/grade → 5.0 制分数。
            if not isinstance(score, (int, float)):
                score = _grade_to_score(data.get("overall_grade") or data.get("grade"))
            if isinstance(score, (int, float)):
                judge_scores.append((ch, float(score)))
            for issue in data.get("issues", []) or []:
                if isinstance(issue, dict):
                    finding_codes[issue.get("code", "?")] += 1
            break

    # 连续 ≥ 3 章重复 finding
    repeated = [(code, c) for code, c in finding_codes.items() if c >= 3]
    if repeated:
        findings.append({
            "signal": "SOLVER_REPEATED_ERRORS",
            "evidence": f"finding codes 重复 ≥ 3 章: {repeated[:3]}",
            "suggestion": "writer prompt 加强对应纪律 / manifest 注入 prev_findings 强调",
        })

    # 评分下降
    if len(judge_scores) >= 3:
        if judge_scores[-1][1] < judge_scores[0][1] - 0.5:
            findings.append({
                "signal": "SOLVER_QUALITY_DECLINE",
                "evidence": f"评分从 {judge_scores[0][1]:.1f} → {judge_scores[-1][1]:.1f}",
                "suggestion": "触发 meta-prompt-optimizer 全面分析",
            })

    return {"signal": "ok" if not findings else "needs_evolve", "findings": findings,
            "judge_scores": judge_scores, "repeated_codes": dict(repeated)}


def analyze_judge(project_root: Path, recent_chs: list[int]) -> dict:
    """分析 judge 一致性 + waiver 分布"""
    findings = []
    waiver_codes = Counter()
    for ch in recent_chs:
        changes = load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})
        for w in (changes.get("self_eval", {}) or {}).get("waivers", []) or []:
            if isinstance(w, dict):
                waiver_codes[w.get("code", "?")] += 1

    persistent = [(code, c) for code, c in waiver_codes.items() if c >= 3]
    if persistent:
        findings.append({
            "signal": "JUDGE_PERSISTENT_WAIVER",
            "evidence": f"waiver code 连续 ≥ 3 章被豁免: {persistent[:3]}",
            "suggestion": "调高对应 scanner 阈值 / 改 rule",
        })

    return {"signal": "ok" if not findings else "tool_calibration",
            "findings": findings, "persistent_waivers": dict(persistent)}


def analyze_judge_cluster(project_root: Path, recent_clusters: list[dict]) -> dict:
    """2026-05-29 cluster 化：cluster 视野的 Judge 分析 —— 用 cluster 账本 judge_grade
    取代逐章 changes.waivers 重扫。grade 连续偏低（C/D）→ 一致性/质量信号。
    账本无 judge_grade（builder 未填）→ 回退逐章 analyze_judge（向后兼容）。
    """
    findings = []
    grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
    graded = [(c.get("cluster_id"), c.get("judge_grade")) for c in recent_clusters
              if isinstance(c.get("judge_grade"), str) and c.get("judge_grade") in grade_rank]
    if not graded:
        # 回退：把最近 cluster 的章拍平走逐章 waiver 分析
        # 2026-05-29 复审修复（L9）：优先 chapter_range 展开（_expand_cluster_chapters），
        # 不再只认 chapters{} key。
        chs = []
        for c in recent_clusters:
            chs.extend(_expand_cluster_chapters(c))
        if chs:
            return analyze_judge(project_root, sorted(set(chs)))
        return {"signal": "no_data", "findings": [], "cluster_grades": []}

    low = [(cid, g) for cid, g in graded if grade_rank[g] <= 2]
    if len(low) >= 2:
        findings.append({
            "signal": "JUDGE_CLUSTER_LOW_GRADE",
            "evidence": f"{len(low)} 个 cluster judge_grade ≤ C: {low[:3]}",
            "suggestion": "outline-planner 卡设计 / writer 工艺反向校准（meta-prompt-optimizer）",
        })
    return {"signal": "ok" if not findings else "needs_evolve",
            "findings": findings, "cluster_grades": graded}


def trigger_cascade(project_root: Path, signals: list[str], cluster_key: str | None = None) -> dict:
    """根据信号触发对应工具。

    2026-05-29 cluster 化：cluster 模式（cluster_key 非空）下把级联的 skill_evolver
    evolve/retire 也按 cluster 调（`--cluster {key}`），与 skill_evolver 的 cluster 阈值配套；
    chapter 模式保持 `--ch {cur_ch}` 不变。
    """
    triggered = []
    if "SOLVER_REPEATED_ERRORS" in signals or "SOLVER_QUALITY_DECLINE" in signals \
            or "JUDGE_CLUSTER_LOW_GRADE" in signals:
        triggered.append("meta-prompt-optimizer agent 建议主代理 spawn")
    if "JUDGE_PERSISTENT_WAIVER" in signals:
        triggered.append("learning_loop --scan-recurring 已建议跑")
    # 自动跑 skill_evolver evolve
    script_dir = Path(__file__).parent
    try:
        if cluster_key:
            unit_args = ["--cluster", cluster_key.replace("cluster_", "")]
        else:
            chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                         for d in (project_root / "章节").glob("第*章")
                         if re.match(r"第(\d+)章", d.name))
            cur_ch = chs[-1] if chs else 0
            unit_args = ["--ch", str(cur_ch)]
        subprocess.run([sys.executable, str(script_dir / "skill_evolver.py"),
                       str(project_root), "evolve"] + unit_args,
                       capture_output=True, timeout=60, encoding="utf-8")
        triggered.append("skill_evolver evolve 已自动执行")
        subprocess.run([sys.executable, str(script_dir / "skill_evolver.py"),
                       str(project_root), "retire"] + unit_args,
                       capture_output=True, timeout=60, encoding="utf-8")
        triggered.append("skill_evolver retire 已自动执行")
        subprocess.run([sys.executable, str(script_dir / "skill_evolver.py"),
                       str(project_root), "promote"],
                       capture_output=True, timeout=60, encoding="utf-8")
        triggered.append("skill_evolver promote 已自动执行（→ universal_skill_pool）")
    except Exception as e:
        triggered.append(f"skill_evolver 触发失败: {e}")
    return {"triggered": triggered}


def _run_cluster(project_root: Path, cluster_key: str, cluster_cycle: int) -> int:
    """2026-05-29 cluster 化（主路径）：触发单位「每 N 个 cluster」。

    分析窗口 = cluster_summary_reader 取最近 N 个 cluster；把这些 cluster 的章拍平给
    Proposer/Solver 逐章分析器复用，Judge 走 cluster 级 judge_grade 分析。
    plan cluster-save-state.plan.json:126 以 `--cluster {key} || true` 调用，故绝不能崩。
    """
    if _csr is None:
        print("[SKIP] cluster_summary_reader 不可用，cluster 模式无法分析")
        return 0
    recent_clusters = _csr.get_clusters(project_root, last_n=cluster_cycle)
    cur_cid = "cluster_" + cluster_key.replace("cluster_", "")
    if not recent_clusters:
        print(f"[SKIP] cluster 账本无落账 cluster（{cur_cid} 可能账本未建），跳过三角分析")
        return 0

    # 把最近 cluster 的章拍平给逐章分析器复用
    # 2026-05-29 复审修复（L9）：优先用 chapter_range 全展开（_expand_cluster_chapters），
    # 旧实现只认 chapters{} key → splitter 已回填 range 但 builder 章记录稀疏时漏章。
    recent_chs = sorted({
        ch for c in recent_clusters for ch in _expand_cluster_chapters(c)
    })

    proposer_r = analyze_proposer(project_root, recent_chs)
    solver_r = analyze_solver(project_root, recent_chs)
    judge_r = analyze_judge_cluster(project_root, recent_clusters)

    all_signals = []
    for r in [proposer_r, solver_r, judge_r]:
        for f in r.get("findings", []):
            all_signals.append(f.get("signal"))
    cascade = trigger_cascade(project_root, all_signals, cluster_key=cluster_key)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    cluster_window = [c.get("cluster_id") for c in recent_clusters]
    report = {
        "scan_type": "evolution_orchestrator",
        "scan_ts": ts,
        "mode": "cluster",
        "current_cluster": cur_cid,
        "cluster_cycle_window": cluster_window,
        "chapter_window": recent_chs,
        "proposer": proposer_r,
        "solver": solver_r,
        "judge": judge_r,
        "cascade_triggered": cascade,
        "signals_summary": all_signals,
    }
    out_path = project_root / "_数据库" / ".evolution" / f"orchestrator_{ts}.json"
    save_json(out_path, report)
    print(f"[evolution_orchestrator · cluster mode] {cur_cid} cycle={len(recent_clusters)} clusters {cluster_window}")
    print(f"  Proposer: {proposer_r.get('signal')} ({len(proposer_r.get('findings', []))} findings)")
    print(f"  Solver:   {solver_r.get('signal')} ({len(solver_r.get('findings', []))} findings)")
    print(f"  Judge:    {judge_r.get('signal')} ({len(judge_r.get('findings', []))} findings)")
    print(f"  Cascade triggered: {len(cascade.get('triggered', []))} 项")
    for t in cascade.get("triggered", [])[:5]:
        print(f"    - {t}")
    print(f"  报告: {out_path}")
    return 1 if all_signals else 0


def main():
    ap = argparse.ArgumentParser(
        description="evolution_orchestrator · cluster 模式（--cluster）为 v26 主路径 / --ch 向后兼容"
    )
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--cycle", type=int, default=10, help="chapter 模式：每 N 章窗口")
    # 2026-05-29 cluster 化：plan cluster-save-state.plan.json:126 以 `--cluster {key} || true`
    # 调用。argparse 不认 --cluster 会被吞 → 三角共演化静默不跑。
    ap.add_argument("--cluster", type=str, default=None,
                    help="cluster key（'001' / 'cluster_001'）· 主路径：每 N 个 cluster 触发")
    ap.add_argument("--cluster-cycle", type=int, default=3,
                    help="cluster 模式：最近 N 个 cluster 作分析窗口（默认 3）")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()

    # cluster 模式优先（plan 主路径）
    if args.cluster:
        sys.exit(_run_cluster(project_root, args.cluster, args.cluster_cycle))

    # chapter 兼容模式
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    if not chapters:
        print("[SKIP] 无已写章节")
        sys.exit(0)
    cur_ch = args.ch or chapters[-1]
    recent = [c for c in chapters if c <= cur_ch][-args.cycle:]

    proposer_r = analyze_proposer(project_root, recent)
    solver_r = analyze_solver(project_root, recent)
    judge_r = analyze_judge(project_root, recent)

    # 触发 cascade
    all_signals = []
    for r in [proposer_r, solver_r, judge_r]:
        for f in r.get("findings", []):
            all_signals.append(f.get("signal"))
    cascade = trigger_cascade(project_root, all_signals)

    # 输出
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "evolution_orchestrator",
        "scan_ts": ts,
        "current_ch": cur_ch,
        "cycle_window": recent,
        "proposer": proposer_r,
        "solver": solver_r,
        "judge": judge_r,
        "cascade_triggered": cascade,
        "signals_summary": all_signals,
    }
    out_dir = project_root / "_数据库" / ".evolution"
    out_path = out_dir / f"orchestrator_{ts}.json"
    save_json(out_path, report)
    print(f"[evolution_orchestrator] ch{cur_ch} cycle={len(recent)}")
    print(f"  Proposer: {proposer_r.get('signal')} ({len(proposer_r.get('findings', []))} findings)")
    print(f"  Solver:   {solver_r.get('signal')} ({len(solver_r.get('findings', []))} findings)")
    print(f"  Judge:    {judge_r.get('signal')} ({len(judge_r.get('findings', []))} findings)")
    print(f"  Cascade triggered: {len(cascade.get('triggered', []))} 项")
    for t in cascade.get("triggered", [])[:5]:
        print(f"    - {t}")
    print(f"  报告: {out_path}")
    sys.exit(1 if all_signals else 0)


if __name__ == "__main__":
    main()
