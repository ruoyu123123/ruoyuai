#!/usr/bin/env python3
"""check_quality_judge.py — /check-quality step 3 综合器（synthesize-only）

# 🔴 2026-06-28 移除exe/gen-model梳理方向
原本本脚本"自主把 reading-reflector / voice-checker 两 judge 派 gen-model
(judge_runner.run_judge)"。新架构下 judge/梳理由**主代理 spawn Claude agent**完成：
主代理先 spawn novel-reading-reflector + novel-voice-checker（Claude），各自把裁决
JSON 落盘 `_数据库/.qa/cluster_{key}_audit_judge.json` / `cluster_{key}_voice_judge.json`，
**本脚本只做确定性综合**——读这两份 judge 输出 + step1 audit + step2 validate →
quality_report.json（北极星④：综合/聚合是确定性脚本活，不调 LLM）。

综合内容：
  - 读 _数据库/.audit/cluster_{key}_audit.json（step1 机械层 13+ scanner advisory）
  - 读 _数据库/.qa/cluster_{key}_validate.json（step2 跨章 hard_gate）
  - 读 _数据库/.qa/cluster_{key}_audit_judge.json（主代理 reading-reflector 落盘·缺则标 degraded）
  - 读 _数据库/.qa/cluster_{key}_voice_judge.json（主代理 voice-checker 落盘·缺则标 degraded）
  - 综合 verdict + sample_issues → _数据库/.qa/cluster_{key}_quality_report.json

【北极星】
- 全 advisory shadow · 作者档第一权威 · hard_gate 仅来自 step2 validate_cluster
- 纯确定性聚合·零 LLM 调用·judge JSON 缺失不崩（标 present=False/degraded）

【用法】
  python core/scripts/check_quality_judge.py <项目路径> --cluster <key> --out <相对路径>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _resolve_path(project_root: Path, p: str) -> Path:
    pp = Path(p)
    return pp if pp.is_absolute() else project_root / p


def _load_json_safe(path: Path) -> dict | None:
    """读 JSON · 任何故障返回 None（synthesize 写「子报告缺失」标记不崩）。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _summarize_audit(audit: dict | None) -> dict:
    """step1 audit_hub 报告 → 摘要（不重排原始 issues·只数数 + 摘头 5 条样例）。"""
    if not audit:
        return {"present": False, "verdict": None, "summary": {}, "sample_issues": []}
    issues = audit.get("issues", []) or []
    sample = []
    for i in issues[:5]:
        sample.append({
            "code": i.get("code"),
            "gate_level": i.get("gate_level"),
            "severity": i.get("severity"),
            "dimension": i.get("dimension"),
            "desc": (i.get("desc") or "")[:200],
        })
    return {
        "present": True,
        "verdict": audit.get("verdict"),
        "summary": audit.get("summary", {}),
        "issues_total": len(issues),
        "sample_issues": sample,
    }


def _summarize_validate(validate: dict | None) -> dict:
    """step2 validate_cluster 报告 → 摘要 + 摘头 5 条 hard_gate 错误样例。"""
    if not validate:
        return {"present": False, "passed": None, "sample_errors": []}
    errs = validate.get("errors", []) or []
    sample = []
    for e in errs[:5]:
        sample.append({
            "code": e.get("code"),
            "severity": e.get("severity"),
            "_chapter": e.get("_chapter"),
            "msg": (e.get("msg") or "")[:200],
        })
    return {
        "present": True,
        "passed": validate.get("passed"),
        "fatal_count": validate.get("fatal_count", 0),
        "error_count": validate.get("error_count", 0),
        "warning_count": validate.get("warning_count", 0),
        "chapter_file": validate.get("chapter_file"),
        "sample_errors": sample,
    }


def _summarize_judge(label: str, judge: dict | None, judge_path: Path) -> dict:
    """judge JSON → 摘要（核心字段 verdict/violations/new_issues_this_round/free_notes）。"""
    if not judge:
        return {"present": False, "path": str(judge_path), "label": label}
    out = {
        "present": True,
        "label": label,
        "path": str(judge_path),
        "degraded": bool(judge.get("_degraded")),
        "verdict": judge.get("verdict"),
        "free_notes": (judge.get("free_notes") or "")[:500],
    }
    if "violations" in judge:
        v = judge["violations"] or []
        out["violations_count"] = len(v) if isinstance(v, list) else None
        out["violations_sample"] = v[:5] if isinstance(v, list) else []
    if "new_issues_this_round" in judge:
        ni = judge["new_issues_this_round"] or []
        out["new_issues_count"] = len(ni) if isinstance(ni, list) else None
        out["new_issues_sample"] = ni[:5] if isinstance(ni, list) else []
    return out


def _compute_verdict(audit_sum: dict, val_sum: dict,
                     audit_judge_sum: dict, voice_judge_sum: dict) -> str:
    """综合 verdict（粗粒度·机械层 + LLM judge 合议）。

    - fatal/hard_gate 残留 → fail
    - 任一 judge verdict=fail → fail
    - audit verdict=needs_agent → needs_agent
    - 其余 → pass（含 waived/auto_fixed 等 advisory 都消化掉的情况）
    """
    if val_sum.get("fatal_count", 0) > 0:
        return "fail"
    if val_sum.get("present") and not val_sum.get("passed", True):
        return "fail"
    if audit_sum.get("verdict") == "needs_agent":
        return "needs_agent"
    for js in (audit_judge_sum, voice_judge_sum):
        if js.get("verdict") in ("fail", "block"):
            return "fail"
    return "pass"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="check-quality step 3 · 综合 quality_report（synthesize-only · "
                    "judge 由主代理 spawn Claude agent 预落盘）")
    ap.add_argument("project", help="项目路径（workspace/novels/<书名>）")
    ap.add_argument("--cluster", required=True,
                    help="cluster key（纯数字或 cluster_NNN 形态）")
    ap.add_argument("--out", required=True,
                    help="quality_report.json 输出路径（相对项目根或绝对）")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {project_root}", file=sys.stderr)
        return 2

    key = args.cluster
    if key.startswith("cluster_"):
        key = key[len("cluster_"):]
    cluster_id = f"cluster_{key}"

    # judge 输出由**主代理 spawn Claude agent**（novel-reading-reflector + novel-voice-checker）
    # 预落盘到下面两个路径·本脚本只读不派单（北极星④综合是确定性脚本活）。
    qa_dir = project_root / "_数据库" / ".qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    audit_judge_out = qa_dir / f"cluster_{key}_audit_judge.json"
    voice_judge_out = qa_dir / f"cluster_{key}_voice_judge.json"
    quality_report_out = _resolve_path(project_root, args.out)
    quality_report_out.parent.mkdir(parents=True, exist_ok=True)

    judge_errors: list[str] = []
    for label, jp in (("reading-reflector", audit_judge_out),
                      ("voice-checker", voice_judge_out)):
        if not jp.exists():
            judge_errors.append(f"{label}: judge 输出缺失 {jp.name}（主代理未 spawn 该 agent？）")
            print(f"[WARN] {label} judge 输出缺失: {jp}（synthesize 标 degraded）",
                  file=sys.stderr)

    # —— 综合 quality_report.json ——
    print(f"[synthesize] quality_report.json", file=sys.stderr)
    audit_path = project_root / "_数据库" / ".audit" / f"cluster_{key}_audit.json"
    validate_path = project_root / "_数据库" / ".qa" / f"cluster_{key}_validate.json"
    audit_sum = _summarize_audit(_load_json_safe(audit_path))
    val_sum = _summarize_validate(_load_json_safe(validate_path))
    audit_judge_sum = _summarize_judge(
        "novel-reading-reflector", _load_json_safe(audit_judge_out), audit_judge_out)
    voice_judge_sum = _summarize_judge(
        "novel-voice-checker", _load_json_safe(voice_judge_out), voice_judge_out)

    verdict = _compute_verdict(audit_sum, val_sum, audit_judge_sum, voice_judge_sum)

    quality_report = {
        "schema_version": 1,
        "command": "check-quality",
        "cluster_id": cluster_id,
        "project_root": str(project_root),
        "verdict": verdict,
        "judge_errors": judge_errors,
        "modules": {
            "mechanical_audit_step1": audit_sum,
            "cross_chapter_validate_step2": val_sum,
            "llm_reading_reflector_judge": audit_judge_sum,
            "llm_voice_checker_judge": voice_judge_sum,
        },
        "_program_driven_note": (
            "/check-quality v2 program-driven · advisory-only shadow · "
            "作者档第一权威 · hard_gate 仅来自 step2 validate_cluster"),
    }
    quality_report_out.write_text(
        json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 摘要到 stderr
    print(f"== cluster_{key} quality report ==", file=sys.stderr)
    print(f"  verdict:        {verdict}", file=sys.stderr)
    print(f"  audit issues:   {audit_sum.get('issues_total', '?')}", file=sys.stderr)
    print(f"  validate fatal: {val_sum.get('fatal_count', '?')}", file=sys.stderr)
    print(f"  judge errors:   {len(judge_errors)}", file=sys.stderr)
    print(f"  报告:           {quality_report_out}", file=sys.stderr)

    # 退出码：fatal/judge exception → 1；其余 0（reporting tool · plan control_flow 决定）
    if verdict == "fail" or judge_errors:
        return 1
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
