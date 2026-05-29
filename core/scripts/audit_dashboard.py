"""audit_dashboard.py — 聚合所有 audit/scan/judge_reports 输出统一面板（v19.4 新增）

汇集来源：
- _数据库/.audit/ch_NNN_audit.json           ← audit_hub 单章
- _数据库/.cross_chapter_scan/*.json         ← 4 个跨章扫描器
- _数据库/.judge_reports/ch_NNN_*.json       ← 5 类 judge 报告
- _数据库/故事块摘要.json[ch].truth_check     ← writer 撒谎检测
- _数据库/写作经验.json._recurrence_tracker ← learning_loop 复发追踪

输出：
- 跨章统计：每章 grade 分布、每 code 命中次数、Top advisory/warning
- 趋势：grade 滚动趋势 / Top 5 复发问题
- 健康度：tool_calibration / 已豁免 / 待处理 advisory

用法：python audit_dashboard.py <项目>
退出码: 0 健康 / 1 有 warning 待处理 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project)
    db = project_root / "_数据库"
    if not db.is_dir():
        print(f"[FATAL] _数据库 不存在", file=sys.stderr)
        sys.exit(2)

    # 1. audit_hub 单章汇集
    audit_dir = db / ".audit"
    chapter_grades = {}
    chapter_summary = {}
    if audit_dir.is_dir():
        # v2 cluster 化：cluster 报告 cluster_<key>_audit.json 与单章 ch_<n>_audit.json 并存，两种都收
        audit_files = sorted(audit_dir.glob("ch_*_audit.json")) + sorted(audit_dir.glob("cluster_*_audit.json"))
        for f in audit_files:
            m = re.match(r"ch_(\d+)_audit", f.stem)
            if m:
                ch_key = int(m.group(1))
            else:
                cm = re.match(r"cluster_(.+)_audit", f.stem)
                if not cm:
                    continue
                ch_key = f"cluster_{cm.group(1)}"  # 字符串 key，不与章号冲突
            d = load_json(f, {})
            s = d.get("summary", {})
            chapter_summary[ch_key] = s
            # verdict 推算 grade —— audit_hub 真实取值集合（见 audit_hub.py ~1004-1014）：
            # pass / auto_fixed / fixable_pending / waived / needs_agent（+ 历史 passed/advisory）
            # 精确匹配，避免 "passed" in "pass" 之类的子串误判
            v = d.get("verdict", "")
            if v in ("pass", "auto_fixed", "fixable_pending", "waived", "passed", "advisory"):
                chapter_grades[ch_key] = "A/B" if s.get("error", 0) > 0 else "A"
            else:
                chapter_grades[ch_key] = "C+"

    # 2. judge_reports 汇集
    jr_dir = db / ".judge_reports"
    judge_grades_by_ch = defaultdict(dict)
    if jr_dir.is_dir():
        for f in sorted(jr_dir.glob("ch_*_*.json")):
            m = re.match(r"ch_(\d+)_(.+)", f.stem)
            if m:
                ch, jid = int(m.group(1)), m.group(2)
                d = load_json(f, {})
                judge_grades_by_ch[ch][jid] = d.get("overall_grade", "?")

    # 3. cross_chapter_scan 汇集（取最新报告）
    cc_dir = db / ".cross_chapter_scan"
    cc_summary = {}
    if cc_dir.is_dir():
        for f in sorted(cc_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            scan_type = f.stem.split("_")[0]
            if scan_type in cc_summary:
                continue
            d = load_json(f, {})
            cc_summary[scan_type] = {
                "ts": d.get("scan_ts", ""),
                "findings_count": d.get("summary", {}).get("total", 0),
                "warning": d.get("summary", {}).get("warning", 0),
                "advisory": d.get("summary", {}).get("advisory", 0),
            }

    # 4. learning_loop 复发追踪
    exp = load_json(db / "写作经验.json", {})
    recurrence = exp.get("_recurrence_tracker", {})
    top_recur = sorted(recurrence.items(), key=lambda kv: kv[1].get("count", 0), reverse=True)[:10]
    waiver = exp.get("_waiver_tracker", {})
    calib = exp.get("tool_calibration_suggestions", [])

    # 5. 输出统一面板
    print("=" * 70)
    print(f"📊 audit_dashboard · 项目 {project_root.name}")
    print("=" * 70)

    print(f"\n📖 章节 grade 矩阵（共 {len(chapter_summary)} 条 · 含 cluster）")
    print(f"{'ch':>4} | {'audit-hub':<10} | {'audit-summary':<35} | {'judges (writer/foreshadower 等)'}")
    # v2 cluster 化：key 混有 int（章号）和 str（cluster_<key>）→ 先 int 升序，再 str
    for ch in sorted(chapter_summary.keys(), key=lambda k: (isinstance(k, str), k)):
        s = chapter_summary[ch]
        sum_str = f"err={s.get('error', 0)} warn={s.get('warning', 0)} waived={s.get('waived', 0)}"
        jr = judge_grades_by_ch.get(ch, {})
        jr_str = " ".join(f"{k}={v}" for k, v in jr.items() if v not in ("N/A", "?"))[:50]
        print(f"  {str(ch):>6} | {chapter_grades.get(ch, '?'):<10} | {sum_str:<35} | {jr_str}")

    print(f"\n🔍 跨章扫描最新报告")
    for scan_type, info in cc_summary.items():
        flag = "🔴" if info["warning"] else ("⚠️" if info["advisory"] else "✅")
        print(f"  {flag} {scan_type:>20}: {info['findings_count']} 项 (warning={info['warning']}/advisory={info['advisory']})")

    print(f"\n📈 Top 5 复发问题（learning_loop 累积）")
    for code, info in top_recur[:5]:
        chs = info.get("chapters", [])
        print(f"  {code:>40} - 累计 {info.get('count', 0)} 次 · 章节 {chs}")

    print(f"\n🔧 工具校准建议（tool_calibration）")
    if calib:
        for c in calib:
            print(f"  · [{str(c.get('code') or '?'):<25}] 被豁免 {c.get('waived_count')} 次 → {c.get('suggestion_type')} (scene={c.get('scene_type_hint')})")
    else:
        print("  · 暂无建议")

    print(f"\n⚙️ 元健康度")
    total_warning_codes = sum(1 for info in recurrence.values() if info.get("count", 0) >= 4)
    print(f"  · 高复发问题（≥4 章累积）: {total_warning_codes}")
    print(f"  · 已升级为 calibration 的: {len(calib)}")
    print(f"  · 总 audit reports: {len(chapter_summary)} 章")
    print(f"  · 总 judge_reports: {sum(len(jr) for jr in judge_grades_by_ch.values())} 份")
    print()

    # exit code: 任何最新跨章扫描有 warning 就 exit 1
    any_warning = any(info["warning"] > 0 for info in cc_summary.values())
    sys.exit(1 if any_warning else 0)


if __name__ == "__main__":
    main()
