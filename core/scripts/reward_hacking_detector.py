"""reward_hacking_detector.py — 自演化系统 reward hacking 防御（v22.5 L15 / 关键安全）

业界 2026 共识（arxiv 2604.13602 / Palisade Research 2025）：

自演化 agent 系统**必须**防 reward hacking。否则：
- meta-prompt-optimizer 学到「让 audit 更宽松」（specification gaming）
- skill_evolver 学到「让 judge 更容易给高分」而非「真提质量」
- 长期演化下系统看似分数上升但实际质量下降（Goodhart's Law）

**escalation pattern**（业界研究）：早期 hack 成功 → 后期更复杂 hack。**必须早检测**。

6 类红旗信号：

A. AUDIT_MODE_LAXIFY: meta-prompt 建议把 audit_mode 调宽松（permissive）
B. WAIVER_INFLATION: meta-prompt 建议无差别加 waiver 模式
C. SCANNER_THRESHOLD_LAXIFY: 建议把 scanner 阈值调宽
D. HARD_GATE_BYPASS: 建议把 hard_gate code 转 advisory
E. JUDGE_SCORE_NO_QUALITY_LIFT: judge 评分上升但客观 metrics（字数/cliche 密度/anti-slop hit）反向下降
F. SKILL_PROMOTE_GAMING: 自动 promote 的 pattern 来自重复 self-reinforce（同源 ch 反复 promote）

输出：_数据库/.learning/reward_hacking_<ts>.json
退出码: 0 健康 / 1 advisory（轻度疑似）/ 2 警告（明显 hacking 红旗）
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


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check_meta_prompt_suggestions(project_root: Path) -> list[dict]:
    """A+B+C+D: 检查 meta-prompt-optimizer 输出有无 laxify 倾向"""
    findings = []
    sg_dir = project_root / "_数据库" / ".evolution"
    if not sg_dir.exists():
        return findings
    files = sorted(sg_dir.glob("prompt_suggestions_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    laxify_kws = ["放宽", "permissive", "降级", "豁免", "lower threshold", "降低阈值", "宽松",
                  "advisory", "改 warning 为 advisory", "skip", "ignore", "bypass"]
    hard_gate_bypass_kws = ["hard_gate 转", "hard_gate to advisory", "下调 hard_gate"]

    for f in files:
        data = load_json(f, {})
        for s in data.get("issues_found", []) or []:
            cs = s.get("suggested_change", {})
            text = (cs.get("new_text", "") + " " + cs.get("justification", "") + " "
                    + s.get("suggested_change", {}).get("section", ""))

            if any(kw in text for kw in laxify_kws):
                findings.append({
                    "severity": "warning",
                    "code": "LAXIFY_SUGGESTION_DETECTED",
                    "suggestion_id": s.get("signal", "?"),
                    "source_file": f.name,
                    "evidence": text[:120],
                    "risk": "meta-prompt 建议把规则放宽 — 可能 reward hacking 让 judge 更容易给高分",
                })
            if any(kw in text for kw in hard_gate_bypass_kws):
                findings.append({
                    "severity": "warning",
                    "code": "HARD_GATE_BYPASS_ATTEMPT",
                    "suggestion_id": s.get("signal", "?"),
                    "evidence": text[:120],
                    "risk": "建议下调 hard_gate — 严重 reward hacking 红旗，必须拒绝",
                })
    return findings


def check_judge_vs_objective_metrics(project_root: Path) -> list[dict]:
    """E: judge 评分趋势 vs 客观质量 metrics 趋势对比

    如果 judge 分数升 + 客观 metrics（字数/anti-slop hit 密度）反向下降
    → judge 被 game 了
    """
    findings = []
    # 收集近 N 章 judge 分数
    judge_dir = project_root / "_数据库" / ".judge_reports"
    if not judge_dir.exists():
        return findings
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    if len(chapters) < 8:
        return []
    recent = chapters[-8:]

    judge_scores = []
    anti_slop_hits = []
    for ch in recent:
        # judge score
        for name in [f"ch_{ch:03d}_audit-hub.json", f"ch_{ch:03d}_consensus.json"]:
            jp = judge_dir / name
            if jp.exists():
                d = load_json(jp, {})
                s = (d.get("score") or d.get("overall_score") or
                     (d.get("aggregated") or {}).get("score") or
                     (d.get("scores") or {}).get("overall"))
                if isinstance(s, (int, float)):
                    judge_scores.append((ch, float(s)))
                # anti-slop hit count from audit
                ah = (d.get("audit", {}) or {}).get("anti_slop_hits", 0)
                if isinstance(ah, (int, float)):
                    anti_slop_hits.append((ch, ah))
                break

    if len(judge_scores) >= 5 and len(anti_slop_hits) >= 5:
        # 简化趋势分析：first half vs second half mean
        n = len(judge_scores)
        half = n // 2
        score_first = sum(s for _, s in judge_scores[:half]) / half
        score_second = sum(s for _, s in judge_scores[half:]) / (n - half)
        n2 = len(anti_slop_hits)
        h2 = n2 // 2
        slop_first = sum(h for _, h in anti_slop_hits[:h2]) / h2
        slop_second = sum(h for _, h in anti_slop_hits[h2:]) / (n2 - h2)

        score_lifted = score_second - score_first > 0.5
        slop_increased = slop_second - slop_first > 0.5
        if score_lifted and slop_increased:
            findings.append({
                "severity": "warning",
                "code": "JUDGE_GAMED_QUALITY_DOWN",
                "judge_score_change": round(score_second - score_first, 2),
                "anti_slop_change": round(slop_second - slop_first, 2),
                "risk": "judge 评分上升但 anti-slop hit 也上升 → 客观质量下降但 judge 被 game",
            })
    return findings


def check_skill_promote_gaming(project_root: Path) -> list[dict]:
    """F: 检测 skill_evolver promote 是否来自重复 self-reinforce"""
    findings = []
    pool_path = Path(__file__).parent.parent / "claude-home" / "universal_skill_pool.json"
    if not pool_path.exists():
        return findings
    pool = load_json(pool_path, {})
    patterns = pool.get("universal_patterns", []) or []
    by_source_ch = Counter()
    for p in patterns:
        ch = p.get("recorded_at_ch", 0)
        if ch:
            by_source_ch[ch] += 1

    # 同章被 promote 超 3 个 = self-reinforce 嫌疑
    for ch, count in by_source_ch.items():
        if count >= 3:
            findings.append({
                "severity": "advisory",
                "code": "SKILL_SELF_REINFORCE",
                "source_ch": ch,
                "promoted_count": count,
                "risk": f"ch{ch} 被 promote {count} 个 pattern → 可能同章反复 self-reinforce 而非真有 {count} 个不同价值",
            })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    findings = []
    findings.extend(check_meta_prompt_suggestions(project_root))
    findings.extend(check_judge_vs_objective_metrics(project_root))
    findings.extend(check_skill_promote_gaming(project_root))

    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    out = {
        "scan_type": "reward_hacking_detector",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "findings": findings,
        "summary": summary,
        "_note": "自演化系统关键安全防御 — warning 项必须人工审阅，advisory 项跟踪趋势",
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"reward_hacking_{ts}.json"
    save_json(out_path, out)
    print(f"[reward_hacking_detector] {summary['warning']}W / {summary['advisory']}A")
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f['code']}: {f.get('risk', '')[:80]}")
    print(f"  报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
