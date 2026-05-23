"""cross_chapter_judge_quality_scan.py — Judge 评分趋势 + waiver 累计 + prev_findings 消费率（CCR13）

3 类跨章 judge/waiver 健康检测：

A. JUDGE_SCORE_TREND
   读 _数据库/.judge_reports/ch_*_audit-hub.json 的 score 字段（如有）
   - SCORE_DECLINE：连续 ≥ 3 章评分下降 = 质量下滑
   - SCORE_PLATEAU：≥ 5 章评分波动 < 0.3 = judge 漂泊（无区分度）
   - SCORE_VOLATILITY：≥ 5 章评分标准差 > 1.5 = judge 不稳定

B. WAIVER_ACCUMULATION
   扫 _changes.json.self_eval.waivers + .judge_reports[].waivers
   - WAIVER_RUNAWAY：单章 waiver ≥ 5 = 工具校准失准
   - WAIVER_PERSISTENT_CODE：同 code 连续 ≥ 3 章被豁免 = 应永久关闭/调整阈值

C. PREV_FINDINGS_CONSUMPTION
   扫 prev_judge_findings 注入后 writer 是否消费（_changes.json.self_eval 是否引用上章 finding 关键词）
   - PREV_FINDINGS_IGNORED：连续 ≥ 3 章 prev_judge_findings 未被任何引用

退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
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


def get_chapters(project_root: Path, last_n: int) -> list[int]:
    chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                 for d in (project_root / "章节").glob("第*章")
                 if re.match(r"第(\d+)章", d.name))
    return chs[-last_n:] if chs else []


def read_changes(project_root: Path, ch: int) -> dict:
    return load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})


# ---------- A. JUDGE_SCORE_TREND ----------

def scan_judge_scores(project_root: Path, chapters: list[int]) -> list[dict]:
    judge_dir = project_root / "_数据库" / ".judge_reports"
    if not judge_dir.exists():
        return []
    findings = []
    # 收集每章 audit-hub score
    scores = []  # [(ch, score)]
    for ch in chapters:
        # 几种命名兼容
        for name in [f"ch_{ch:03d}_audit-hub.json", f"ch_{ch:03d}_consensus.json"]:
            f = judge_dir / name
            if not f.exists():
                continue
            data = load_json(f, {})
            # score 字段位置兼容
            score = (data.get("score") or
                     data.get("overall_score") or
                     (data.get("aggregated") or {}).get("score") or
                     (data.get("scores") or {}).get("overall"))
            if score is not None and isinstance(score, (int, float)):
                scores.append((ch, float(score)))
                break

    if len(scores) < 3:
        return []

    # SCORE_DECLINE
    decline_streak = 0
    for i in range(1, len(scores)):
        if scores[i][1] < scores[i - 1][1]:
            decline_streak += 1
            if decline_streak >= 3:
                trail = [(c, s) for c, s in scores[i - 3:i + 1]]
                findings.append({
                    "severity": "warning",
                    "code": "JUDGE_SCORE_DECLINE",
                    "trail": trail,
                    "suggestion": f"judge 评分连续 {decline_streak + 1} 章下降 ({trail[0][1]:.1f} → {trail[-1][1]:.1f}) → 质量下滑",
                })
                decline_streak = 0
        else:
            decline_streak = 0

    # SCORE_PLATEAU
    if len(scores) >= 5:
        recent5 = scores[-5:]
        smin = min(s for _, s in recent5)
        smax = max(s for _, s in recent5)
        if smax - smin < 0.3:
            findings.append({
                "severity": "advisory",
                "code": "JUDGE_SCORE_PLATEAU",
                "range": [smin, smax],
                "trail_chs": [c for c, _ in recent5],
                "suggestion": f"近 5 章 judge 评分波动 < 0.3（{smin:.1f}-{smax:.1f}）→ judge 失去区分度",
            })

    # SCORE_VOLATILITY
    if len(scores) >= 5:
        recent5 = [s for _, s in scores[-5:]]
        mean = sum(recent5) / len(recent5)
        var = sum((s - mean) ** 2 for s in recent5) / len(recent5)
        std = var ** 0.5
        if std > 1.5:
            findings.append({
                "severity": "advisory",
                "code": "JUDGE_SCORE_VOLATILITY",
                "std": round(std, 2),
                "mean": round(mean, 2),
                "suggestion": f"近 5 章 judge 评分标准差 {std:.2f}（>1.5）→ judge 不稳定，建议 meta-judge 校准",
            })
    return findings


# ---------- B. WAIVER_ACCUMULATION ----------

def scan_waiver_accumulation(project_root: Path, chapters: list[int]) -> list[dict]:
    findings = []
    per_ch_waiver_count = {}
    code_per_ch = defaultdict(set)  # code -> {ch...}
    for ch in chapters:
        changes = read_changes(project_root, ch)
        waivers = (changes.get("self_eval", {}) or {}).get("waivers", []) or []
        # 也扫 judge_reports 中的 waivers
        judge_dir = project_root / "_数据库" / ".judge_reports"
        if judge_dir.exists():
            for f in judge_dir.glob(f"ch_{ch:03d}_*.json"):
                jdata = load_json(f, {})
                jw = jdata.get("waivers", []) or []
                if isinstance(jw, list):
                    waivers.extend(jw)
        per_ch_waiver_count[ch] = len(waivers)
        for w in waivers:
            if isinstance(w, dict):
                code = w.get("code")
                if code:
                    code_per_ch[code].add(ch)

    # WAIVER_RUNAWAY
    for ch, n in per_ch_waiver_count.items():
        if n >= 5:
            findings.append({
                "severity": "warning",
                "code": "WAIVER_RUNAWAY",
                "ch": ch,
                "waiver_count": n,
                "suggestion": f"ch{ch} 单章 {n} 个 waiver → 工具校准失准 / writer 在硬抗规则",
            })

    # WAIVER_PERSISTENT_CODE
    for code, chs in code_per_ch.items():
        chs_list = sorted(chs)
        if len(chs_list) < 3:
            continue
        # 找连续段
        max_streak = 1
        cur_streak = 1
        for i in range(1, len(chs_list)):
            if chs_list[i] - chs_list[i - 1] == 1:
                cur_streak += 1
                max_streak = max(max_streak, cur_streak)
            else:
                cur_streak = 1
        if max_streak >= 3:
            findings.append({
                "severity": "advisory",
                "code": "WAIVER_PERSISTENT_CODE",
                "waived_code": code,
                "consecutive_chs": max_streak,
                "all_chs": chs_list,
                "suggestion": f"waiver code {code} 连续 ≥ 3 章被豁免 → 应永久关闭该规则或调整阈值",
            })
    return findings


# ---------- C. PREV_FINDINGS_CONSUMPTION ----------

def scan_prev_findings_consumption(project_root: Path, chapters: list[int]) -> list[dict]:
    findings = []
    no_consume_streak = 0
    streak_chs = []
    for ch in chapters:
        # 取 prev_judge_findings：从 manifest（如有）获取本章注入的上章发现
        manifest_path = project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json"
        if not manifest_path.exists():
            continue
        manifest = load_json(manifest_path, {})
        prev_findings = manifest.get("prev_judge_findings", []) or []
        if not prev_findings:
            no_consume_streak = 0
            continue
        # 提取 prev findings 中的关键词
        finding_kws = []
        for f in prev_findings:
            if isinstance(f, dict):
                msg = f.get("message", "") or f.get("suggestion", "") or f.get("description", "")
                finding_kws.extend(re.findall(r"[一-鿿]{3,5}", msg)[:3])
        if not finding_kws:
            continue
        # 检查 changes 是否引用
        changes = read_changes(project_root, ch)
        changes_dump = json.dumps(changes, ensure_ascii=False)
        consume_hits = sum(1 for kw in finding_kws if kw in changes_dump)
        if consume_hits == 0:
            no_consume_streak += 1
            streak_chs.append(ch)
            if no_consume_streak >= 3:
                findings.append({
                    "severity": "advisory",
                    "code": "PREV_FINDINGS_IGNORED",
                    "consecutive_chs": streak_chs[-3:],
                    "suggestion": f"近 {no_consume_streak} 章 prev_judge_findings 注入但 writer 未引用任何关键词 → 反馈环空转",
                })
                no_consume_streak = 0
                streak_chs = []
        else:
            no_consume_streak = 0
            streak_chs = []
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)
    chapters = get_chapters(project_root, args.last_n)
    if not chapters:
        print("[SKIP] 无已写章节")
        sys.exit(0)

    findings = []
    findings.extend(scan_judge_scores(project_root, chapters))
    findings.extend(scan_waiver_accumulation(project_root, chapters))
    findings.extend(scan_prev_findings_consumption(project_root, chapters))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "judge_quality",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"judge_quality_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[judge_quality] {summary['warning']} warning / {summary['advisory']} advisory")
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
