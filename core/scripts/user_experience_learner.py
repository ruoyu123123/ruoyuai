"""user_experience_learner.py — 用户行为 + 痛点学习（v22.5 L1+L6）

业界 2026 SaaS analytics：捕获 silent failures（用户不反馈直接 give up）。

3 类信号采集 + 分析：

A. 用户行为模式
   - 走向卡选择倾向（A 激进 / B 保守 / C 意外占比）
   - 章节写作间隔（推测写作时段）
   - 中断点（save-state 未完成 / write-chapter 半途断）

B. 用户痛点信号（silent failures）
   - 同 reconcile 命令 ≥ 3 次（设定反复改 = 设计缺陷）
   - 同章节 retry ≥ 2 次（writer 输出反复不满意）
   - 用户长间隔后回归（兴趣下降信号）

C. 显式偏好（与 user_preferences_v21 对账）
   - 实际行为 vs wizard 配置偏好的偏离
   - 偏离大 → 偏好不准确，建议重跑 /wizard

输出：_数据库/.learning/user_experience_<ts>.json
退出码: 0 / 1 advisory / 2 严重痛点
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


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_card_choices(project_root: Path) -> dict:
    """收集走向卡用户选择历史"""
    progress = load_json(project_root / "_数据库" / "进度.json", {})
    plan = progress.get("chapter_plan", {}) or {}
    choices = Counter()
    for ch_key, ch_data in plan.items() if isinstance(plan, dict) else []:
        if isinstance(ch_data, dict):
            uc = ch_data.get("user_choice")
            if uc:
                choices[uc] += 1
    return dict(choices)


def collect_chapter_timestamps(project_root: Path) -> list[dict]:
    """收集每章 _changes.json 写入时间戳，推测写作间隔"""
    out = []
    ch_dir = project_root / "章节"
    if not ch_dir.exists():
        return []
    for cd in ch_dir.glob("第*章"):
        m = re.match(r"第(\d+)章", cd.name)
        if not m:
            continue
        ch = int(m.group(1))
        changes_path = cd / f"第{ch:03d}章_changes.json"
        if changes_path.exists():
            mtime = changes_path.stat().st_mtime
            out.append({"ch": ch, "mtime": mtime, "dt": datetime.fromtimestamp(mtime).isoformat(timespec="seconds")})
    out.sort(key=lambda x: x["ch"])
    return out


def detect_retry_patterns(project_root: Path) -> list[dict]:
    """检测章节高 retry 模式（WAL 重启次数 / .audit 多次 audit）"""
    findings = []
    wal_dir = project_root / "_数据库" / ".wal"
    if wal_dir.exists():
        # WAL 文件多版本 = retry
        wal_files = list(wal_dir.glob("第*章_save_state.json"))
        # 简化：直接看 audit 文件多少
        audit_dir = project_root / "_数据库" / ".audit"
        if audit_dir.exists():
            per_ch = defaultdict(int)
            for f in audit_dir.glob("ch_*_audit*.json"):
                m = re.match(r"ch_(\d+)", f.name)
                if m:
                    per_ch[int(m.group(1))] += 1
            for ch, cnt in per_ch.items():
                if cnt >= 3:
                    findings.append({
                        "code": "HIGH_RETRY",
                        "ch": ch,
                        "audit_count": cnt,
                        "signal": f"ch{ch} 已 audit {cnt} 次 → writer 输出反复不满意",
                    })
    return findings


def detect_reconcile_patterns(project_root: Path) -> list[dict]:
    """检测重复 reconcile（设定反复改）"""
    findings = []
    git_log_dir = project_root / ".git" / "logs"
    if not git_log_dir.exists():
        return []
    head_log = project_root / ".git" / "logs" / "HEAD"
    if not head_log.exists():
        return []
    try:
        log_lines = head_log.read_text(encoding="utf-8", errors="ignore").split("\n")
    except Exception:
        return []
    reconcile_count = sum(1 for line in log_lines if "reconcile" in line.lower())
    if reconcile_count >= 3:
        findings.append({
            "code": "REPEATED_RECONCILE",
            "count": reconcile_count,
            "signal": f"git log 含 {reconcile_count} 次 reconcile → 设定反复改",
            "suggestion": "可能：①初始 outline 设定不够稳 ②角色/世界观需重蒸馏",
        })
    return findings


def analyze_writing_intervals(timestamps: list[dict]) -> dict:
    """分析写作时段 + 间隔模式"""
    if len(timestamps) < 3:
        return {"signal": "insufficient_data"}
    intervals = []
    hours = Counter()
    for i in range(1, len(timestamps)):
        delta_sec = timestamps[i]["mtime"] - timestamps[i - 1]["mtime"]
        intervals.append(delta_sec)
        dt = datetime.fromtimestamp(timestamps[i]["mtime"])
        hours[dt.hour] += 1
    avg_interval_hr = sum(intervals) / len(intervals) / 3600
    long_gaps = sum(1 for x in intervals if x > 7 * 24 * 3600)  # > 1 周
    peak_hour = hours.most_common(1)[0] if hours else (0, 0)
    return {
        "avg_interval_hours": round(avg_interval_hr, 1),
        "long_gaps_over_1week": long_gaps,
        "peak_writing_hour": peak_hour[0],
        "peak_writing_count": peak_hour[1],
    }


def detect_preference_divergence(project_root: Path, card_choices: Counter) -> list[dict]:
    """实际选择 vs user_preferences 配置的偏离"""
    findings = []
    prefs = load_json(project_root / "_数据库" / "用户偏好.json", {})
    if not prefs.get("_meta", {}).get("wizard_completed_at"):
        return [{"code": "WIZARD_NEVER_RUN",
                "signal": "用户从未跑 /wizard，无法对账实际偏好。建议跑 /wizard 让系统知道你"}]

    pacing = prefs.get("narrative_pacing", {}) or {}
    happy_target = pacing.get("happy_vs_dark_ratio", 0.6)

    # 用户实际选 A 激进 vs B 保守 vs C 意外的比例
    total = sum(card_choices.values())
    if total >= 5:
        a_pct = card_choices.get("A", 0) / total
        b_pct = card_choices.get("B", 0) / total
        # 简化判断：A 比例高 = 偏好爽快 = happy ratio 应高
        implied_happy = 0.5 + (a_pct - 0.33) * 0.5  # A 占 0.33 = 平均
        if abs(implied_happy - happy_target) > 0.25:
            findings.append({
                "code": "PREFERENCE_DIVERGENCE",
                "configured_happy": happy_target,
                "implied_from_choices": round(implied_happy, 2),
                "card_distribution": dict(card_choices),
                "signal": f"配置 happy_ratio={happy_target} 但实际选择隐含 {implied_happy:.2f}",
                "suggestion": "建议跑 /wizard --update narrative_pacing 重新校准",
            })

    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    card_choices = collect_card_choices(project_root)
    timestamps = collect_chapter_timestamps(project_root)
    intervals = analyze_writing_intervals(timestamps)
    retry_findings = detect_retry_patterns(project_root)
    reconcile_findings = detect_reconcile_patterns(project_root)
    pref_findings = detect_preference_divergence(project_root, Counter(card_choices))

    all_pain_points = retry_findings + reconcile_findings + pref_findings

    out = {
        "scan_type": "user_experience_learner",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "user_behavior": {
            "card_choice_distribution": card_choices,
            "writing_intervals": intervals,
            "total_chapters_written": len(timestamps),
        },
        "pain_points": all_pain_points,
        "pain_points_count": len(all_pain_points),
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"user_experience_{ts}.json"
    save_json(out_path, out)
    print(f"[user_experience_learner] choices={card_choices} intervals={intervals.get('avg_interval_hours')}h 痛点={len(all_pain_points)}")
    for p in all_pain_points[:5]:
        print(f"  [PAIN] {p.get('code')}: {p.get('signal')}")
    print(f"  报告: {out_path}")
    if any(p.get("code") in ("REPEATED_RECONCILE", "HIGH_RETRY") for p in all_pain_points):
        sys.exit(2)
    if all_pain_points:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
