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


def trigger_cascade(project_root: Path, signals: list[str]) -> dict:
    """根据信号触发对应工具"""
    triggered = []
    if "SOLVER_REPEATED_ERRORS" in signals or "SOLVER_QUALITY_DECLINE" in signals:
        triggered.append("meta-prompt-optimizer agent 建议主代理 spawn")
    if "JUDGE_PERSISTENT_WAIVER" in signals:
        triggered.append("learning_loop --scan-recurring 已建议跑")
    # 自动跑 skill_evolver evolve
    script_dir = Path(__file__).parent
    try:
        chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                     for d in (project_root / "章节").glob("第*章")
                     if re.match(r"第(\d+)章", d.name))
        cur_ch = chs[-1] if chs else 0
        subprocess.run([sys.executable, str(script_dir / "skill_evolver.py"),
                       str(project_root), "evolve", "--ch", str(cur_ch)],
                       capture_output=True, timeout=60, encoding="utf-8")
        triggered.append("skill_evolver evolve 已自动执行")
        subprocess.run([sys.executable, str(script_dir / "skill_evolver.py"),
                       str(project_root), "retire", "--ch", str(cur_ch)],
                       capture_output=True, timeout=60, encoding="utf-8")
        triggered.append("skill_evolver retire 已自动执行")
        subprocess.run([sys.executable, str(script_dir / "skill_evolver.py"),
                       str(project_root), "promote"],
                       capture_output=True, timeout=60, encoding="utf-8")
        triggered.append("skill_evolver promote 已自动执行（→ universal_skill_pool）")
    except Exception as e:
        triggered.append(f"skill_evolver 触发失败: {e}")
    return {"triggered": triggered}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--cycle", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
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
