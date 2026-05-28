"""judge_reports_archive.py — JudgeReport 归档脚本（v19.2 新增）

从多个来源汇集 judge 信号，组装成统一 JudgeReport schema 存盘：
1. `_数据库/.audit/ch_{ch:03d}_audit.json`     ← validator + 4 scanner 程序化分数
2. `_数据库/.wal/第{ch:03d}章_reflection.json`  ← reflector 经验记录
3. `_数据库/.wal/第{ch:03d}章_summary.json`     ← summarizer 摘要
4. `章节/第{ch:03d}章/第{ch:03d}章_changes.json` ← writer self_eval + factual
5. `章节/第{ch:03d}章/第{ch:03d}章.repair.json` ← validator-repair 修复痕迹

存盘位置：
- `_数据库/.judge_reports/ch_{ch:03d}_{judge_id}.json`（独立文件，便于 grep / consensus）
- 同时 append 摘要到 `故事块摘要.json[ch].judge_reports[]`（轻量索引）

为 meta-judge / judge_consensus 建数据基础。

用法：python judge_reports_archive.py <项目路径> <章节号> [--dry-run]
退出码：0 成功 / 1 部分缺失 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import sys
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


def build_writer_self_eval_report(changes: dict, ch: int) -> dict | None:
    """从 _changes.json 提取 writer 自评信号。"""
    se = changes.get("self_eval", {})
    if not se:
        return None
    applied = se.get("applied_style", {})
    return {
        "judge_id": "writer-self-eval",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": "N/A",  # writer 不自评 grade
        "confidence": 1.0,
        "specific_findings": {
            "opening_type": applied.get("opening_type"),
            "ending_type": applied.get("ending_type"),
            "anchors_hit_count": len(applied.get("anchors_hit", [])),
            "core_techniques_count": len(applied.get("core_techniques_applied", [])),
            "subtext_count": applied.get("subtext_count", 0),
            "hooks_count": applied.get("hooks_count", 0),
            "waivers": se.get("waivers", []),
            "continuity_check": se.get("continuity_check"),
            "offscreen_actions_executed": se.get("offscreen_actions_executed", []),
        },
        "uncertainty_flags": [],
        "waivers": se.get("waivers", []),
    }


def build_validator_report_from_audit(audit: dict, ch: int) -> dict | None:
    """从 audit_hub 报告组装 validator 维度的 JudgeReport。"""
    if not audit:
        return None
    summary = audit.get("summary", {})
    fatal = summary.get("fatal", 0)
    error = summary.get("error", 0)
    warning = summary.get("warning", 0)
    waived = summary.get("waived", 0)
    grade = "A" if fatal == 0 and error == 0 else "B" if fatal == 0 and error <= 2 else "C" if fatal == 0 else "D"
    return {
        "judge_id": "audit-hub-aggregator",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": grade,
        "confidence": 0.9,
        "specific_findings": {
            "fatal": fatal,
            "error": error,
            "warning": warning,
            "waived": waived,
            "verdict": audit.get("verdict"),
            "issues_summary": [{"code": i.get("code"), "severity": i.get("severity"), "dimension": i.get("dimension")} for i in audit.get("issues", [])[:10]],
            "auto_fixed": [a.get("code") for a in audit.get("auto_fixed", [])],
            "pending_agent": [{"code": p.get("code"), "agent": p.get("agent")} for p in audit.get("pending_agent", [])],
        },
        "uncertainty_flags": [],
        "waivers": [],
    }


def build_summarizer_report(summary_data: dict, ch: int) -> dict | None:
    if not summary_data:
        return None
    return {
        "judge_id": "summarizer",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": "N/A",
        "confidence": 0.9,
        "specific_findings": {
            "summary_words": summary_data.get("summary_words"),
            "key_details_count": len(summary_data.get("key_details", [])),
            "emotion_value": summary_data.get("emotion", {}).get("value"),
            "emotion_trend": summary_data.get("emotion", {}).get("trend"),
        },
        "uncertainty_flags": [],
        "waivers": [],
    }


def build_reflector_report(reflection: dict, ch: int) -> dict | None:
    if not reflection:
        return None
    entries = reflection.get("entries", [])
    success = [e for e in entries if e.get("category") == "success"]
    failure = [e for e in entries if e.get("category") == "failure"]
    return {
        "judge_id": "reflector",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": "N/A",
        "confidence": 0.85,
        "specific_findings": {
            "new_success_patterns": len(success),
            "new_failure_patterns": len(failure),
            "experience_ids": [e.get("id") for e in entries],
            "note": reflection.get("note", "")[:120],
        },
        "uncertainty_flags": [],
        "waivers": [],
    }


def build_truth_check_report(ch_summary_entry: dict, ch: int) -> dict | None:
    tc = ch_summary_entry.get("truth_check")
    if not tc:
        return None
    return {
        "judge_id": "writer-truth-check",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": "B" if tc.get("lie_count", 0) >= 1 else "A",
        "confidence": 0.95,
        "specific_findings": tc,
        "uncertainty_flags": [],
        "waivers": [],
    }


def _resolve_chapter_range(project_root: Path, cluster_key: str) -> list[int]:
    """v26: 从 事件簇.json.clusters[N].chapter_range 取章节范围。"""
    import json as _json
    shi_path = project_root / "_数据库" / "事件簇.json"
    if not shi_path.exists():
        return []
    try:
        shi = _json.loads(shi_path.read_text(encoding="utf-8"))
        for c in shi.get("clusters", []):
            cid = c.get("cluster_id", "").replace("cluster_", "")
            target = cluster_key.replace("cluster_", "")
            if cid == target:
                cr = c.get("chapter_range") or []
                if isinstance(cr, list) and len(cr) == 2:
                    return list(range(cr[0], cr[1] + 1))
    except Exception:
        pass
    return []


def _archive_one_chapter(project_root: Path, ch: int, dry_run: bool) -> dict:
    """对单章跑归档。返回 {ch, written_count, judges}."""
    db = project_root / "_数据库"

    # 收集源数据
    audit = load_json(db / ".audit" / f"ch_{ch:03d}_audit.json", {})
    reflection = load_json(db / ".wal" / f"第{ch:03d}章_reflection.json", {})
    summary = load_json(db / ".wal" / f"第{ch:03d}章_summary.json", {})
    changes = load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})
    ch_summary_full = load_json(db / "故事块摘要.json", {"chapters": []})
    ch_entry = next((c for c in ch_summary_full.get("chapters", []) if c.get("ch") == ch), {})

    judges = {
        "audit-hub": build_validator_report_from_audit(audit, ch),
        "writer-self-eval": build_writer_self_eval_report(changes, ch),
        "summarizer": build_summarizer_report(summary, ch),
        "reflector": build_reflector_report(reflection, ch),
        "writer-truth-check": build_truth_check_report(ch_entry, ch),
    }
    valid_judges = {k: v for k, v in judges.items() if v is not None}
    print(f"[judge_reports_archive] ch{ch}: 汇集 {len(valid_judges)} 个 judge 信号")

    archive_dir = db / ".judge_reports"
    archive_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for judge_id, report in valid_judges.items():
        archive_path = archive_dir / f"ch_{ch:03d}_{judge_id}.json"
        if dry_run:
            print(f"  [DRY] would write: {archive_path}")
        else:
            save_json(archive_path, report)
            written.append(judge_id)
        grade = report.get("overall_grade", "?")
        conf = report.get("confidence", "?")
        print(f"  [{judge_id}] grade={grade} confidence={conf}")

    # 累积摘要到 故事块摘要[ch].judge_reports[]
    if not dry_run and ch_entry:
        summaries = []
        for jid, r in valid_judges.items():
            summaries.append({
                "judge_id": jid,
                "grade": r.get("overall_grade"),
                "confidence": r.get("confidence"),
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
        ch_entry["judge_reports"] = summaries
        save_json(db / "故事块摘要.json", ch_summary_full)
        print(f"  [OK] 故事块摘要 ch{ch}.judge_reports 已更新（{len(summaries)} 条摘要）")

    return {"ch": ch, "written": len(written), "judges": list(valid_judges.keys())}


def main():
    ap = argparse.ArgumentParser(description="judge_reports_archive · v26 双模式 (chapter or cluster)")
    ap.add_argument("project")
    # v26: chapter 改 optional · 加 --cluster 互斥模式
    ap.add_argument("chapter", type=int, nargs="?", default=None,
                    help="单章号 (chapter mode) · 与 --cluster 互斥")
    ap.add_argument("--cluster", type=str, default=None,
                    help="cluster key (e.g. '001' 或 'cluster_001') · 自动展开本 cluster 全部章节归档")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)

    # v26: cluster mode 优先
    if args.cluster:
        chapters = _resolve_chapter_range(project_root, args.cluster)
        if not chapters:
            print(f"[FATAL] cluster {args.cluster} 未在 事件簇.json 找到 chapter_range", file=sys.stderr)
            return 2
        print(f"[judge_reports_archive · cluster mode] cluster_{args.cluster.replace('cluster_', '')} 展开 {len(chapters)} 章: {chapters}")
        for ch in chapters:
            _archive_one_chapter(project_root, ch, args.dry_run)
        print(f"\n[OK] cluster {args.cluster} judge_reports 归档完成 {len(chapters)} 章")
        return 0

    if args.chapter is None:
        print("[FATAL] 必须指定 chapter 章号 或 --cluster <key>", file=sys.stderr)
        return 2

    # chapter mode (向后兼容)
    ch = args.chapter
    db = project_root / "_数据库"
    # 收集源数据
    audit = load_json(db / ".audit" / f"ch_{ch:03d}_audit.json", {})
    reflection = load_json(db / ".wal" / f"第{ch:03d}章_reflection.json", {})
    summary = load_json(db / ".wal" / f"第{ch:03d}章_summary.json", {})
    changes = load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})
    ch_summary_full = load_json(db / "故事块摘要.json", {"chapters": []})
    ch_entry = next((c for c in ch_summary_full.get("chapters", []) if c.get("ch") == ch), {})

    # 组装 JudgeReports
    judges = {
        "audit-hub": build_validator_report_from_audit(audit, ch),
        "writer-self-eval": build_writer_self_eval_report(changes, ch),
        "summarizer": build_summarizer_report(summary, ch),
        "reflector": build_reflector_report(reflection, ch),
        "writer-truth-check": build_truth_check_report(ch_entry, ch),
    }

    # 过滤 None
    valid_judges = {k: v for k, v in judges.items() if v is not None}

    print(f"[judge_reports_archive] ch{ch}: 汇集 {len(valid_judges)} 个 judge 信号")

    # 存盘
    archive_dir = db / ".judge_reports"
    archive_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for judge_id, report in valid_judges.items():
        archive_path = archive_dir / f"ch_{ch:03d}_{judge_id}.json"
        if args.dry_run:
            print(f"  [DRY] would write: {archive_path}")
        else:
            save_json(archive_path, report)
            written.append(judge_id)
        grade = report.get("overall_grade", "?")
        conf = report.get("confidence", "?")
        print(f"  [{judge_id}] grade={grade} confidence={conf}")

    # 累积摘要到 故事块摘要[ch].judge_reports[]
    if not args.dry_run and ch_entry:
        summaries = []
        for jid, r in valid_judges.items():
            summaries.append({
                "judge_id": jid,
                "grade": r.get("overall_grade"),
                "confidence": r.get("confidence"),
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
        ch_entry["judge_reports"] = summaries
        save_json(db / "故事块摘要.json", ch_summary_full)
        print(f"  [OK] 故事块摘要 ch{ch}.judge_reports 已更新（{len(summaries)} 条摘要）")

    print(f"\n报告目录: {archive_dir}")
    if not valid_judges:
        print("[WARN] 0 个 judge 信号，章节可能未完成 save-state 前 8 步")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
