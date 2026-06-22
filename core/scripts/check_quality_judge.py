#!/usr/bin/env python3
"""check_quality_judge.py — /check-quality step 3 wrapper（2026-06-22 G2 P0a）

step 3 三件事一气呵成（orchestrator 主体执行顺序 scripts→judges→...，scripts 跑在
judges 之前；想在同 step 里"先 judges 再 synthesize"必须把整段都打包成单脚本）：

  1. 真 API 调 judge_runner.run_judge(novel-reading-reflector, ROUND=1)
     —— 阅读体验 advisory · 作者档第一权威（needs_author_profile=True）
     —— 落盘 _数据库/.qa/cluster_{key}_audit_judge.json
  2. 真 API 调 judge_runner.run_judge(novel-voice-checker)
     —— cluster 整声纹审 · 角色 voice_pack 校验 + 跨场景漂移
     —— 落盘 _数据库/.qa/cluster_{key}_voice_judge.json
  3. 综合 step1 audit + step2 validate + 两 judge 输出 → quality_report.json
     —— verdict / advisory_count / hard_gate_count / 子模块 sample_issues
     —— 落盘 _数据库/.qa/cluster_{key}_quality_report.json

【为什么不让 orchestrator 自己派 judges】
orchestrator must_spawn_agent 派完 judges 之后就到 step_complete，没有"judges
后置 hook"放综合脚本。改 orchestrator 加新生命周期是更大动作（北极星⑥宁可单点
wrapper 不动主框架）；本 wrapper 在单脚本里串完所有依赖更稳。

【北极星】
- 全 advisory shadow · hard_gate 12 码不变 · 作者档第一权威
- judge 真 API 真烧钱 · 不省 max_tokens · 不省 retry（feedback_real_api_tests_no_economize）
- 任一 judge 抛 block 异常 → 即时停（写明确错·让用户复跑而非吞错糊弄）
- soft 降级（reading-reflector）→ JSON 写入 _degraded=true · synthesize 仍出报告

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

import judge_runner as jr  # noqa: E402


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
        description="check-quality step 3 · 双 judge 真 API + 综合 quality_report")
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

    # judge agent_input · 与 cluster-write step3/4 同款契约（北极星④格式层不参与判断）
    qa_dir = project_root / "_数据库" / ".qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    cluster_draft = (project_root / "章节" / f"cluster_{key}_draft"
                     / f"cluster_{key}_draft.txt")
    context_files: list[tuple[str, Path]] = []
    if cluster_draft.exists():
        context_files.append(("CLUSTER_DRAFT_PATH", cluster_draft))
    else:
        # cluster draft 已归档/未保留 → 尝试拼接已切的章节正文（splitter 后形态）
        # 不存在 cluster_draft.txt 不阻断 · judge 凭 manifest + 章节扫描凭借
        # （context_files 为空 judge 仍可凭 system prompt 给方向性 advisory）
        print(f"[WARN] cluster draft 未找到: {cluster_draft}（judge 将无 draft 上下文）",
              file=sys.stderr)

    audit_judge_out = qa_dir / f"cluster_{key}_audit_judge.json"
    voice_judge_out = qa_dir / f"cluster_{key}_voice_judge.json"
    quality_report_out = _resolve_path(project_root, args.out)
    quality_report_out.parent.mkdir(parents=True, exist_ok=True)

    judge_errors: list[str] = []

    # —— 1) novel-reading-reflector ROUND=1 ——
    print(f"[1/3] judge: novel-reading-reflector (ROUND=1 · 真 API)", file=sys.stderr)
    try:
        rr_outcome = jr.run_judge(
            "novel-reading-reflector",
            project_root,
            params={
                "PROJECT": str(project_root),
                "CLUSTER_ID": cluster_id,
                "MODE": "cluster",
                "ROUND": "1",
            },
            context_files=context_files,
            output_path=audit_judge_out,
        )
        print(f"      ok={rr_outcome.ok} degraded={rr_outcome.degraded} "
              f"retries={rr_outcome.retries}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        judge_errors.append(f"reading-reflector: {type(e).__name__}: {e}")
        print(f"      [ERROR] {e}", file=sys.stderr)
        # 兜底写空壳让 expected_outputs 通过 · synthesize 标 degraded
        audit_judge_out.write_text(json.dumps(
            {"_judge_exception": str(e),
             "_judge_exception_type": type(e).__name__,
             "_degraded": True,
             "verdict": "exception"},
            ensure_ascii=False, indent=2), encoding="utf-8")

    # —— 2) novel-voice-checker ——
    print(f"[2/3] judge: novel-voice-checker (真 API)", file=sys.stderr)
    try:
        vc_outcome = jr.run_judge(
            "novel-voice-checker",
            project_root,
            params={
                "PROJECT": str(project_root),
                "CLUSTER_ID": cluster_id,
                "MODE": "cluster",
            },
            context_files=context_files,
            output_path=voice_judge_out,
        )
        print(f"      ok={vc_outcome.ok} degraded={vc_outcome.degraded} "
              f"retries={vc_outcome.retries}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        judge_errors.append(f"voice-checker: {type(e).__name__}: {e}")
        print(f"      [ERROR] {e}", file=sys.stderr)
        voice_judge_out.write_text(json.dumps(
            {"_judge_exception": str(e),
             "_judge_exception_type": type(e).__name__,
             "_degraded": True,
             "violations": []},
            ensure_ascii=False, indent=2), encoding="utf-8")

    # —— 3) 综合 quality_report.json ——
    print(f"[3/3] synthesize quality_report.json", file=sys.stderr)
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
