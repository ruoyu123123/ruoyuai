"""prompt_shadow_test.py — Prompt 改进 A/B Shadow 测试（v22.5 L12）

业界共识（tianpan.co 2026 / Braintrust / LangSmith）：
**「Prompt changes are #1 source of LLM regressions in production」**

meta-prompt-optimizer 建议直接 apply = 风险大。必须 shadow test：
1. 把 suggestion 应用到 prompt 的临时 fork
2. 用 gold suite 跑 N 个 representative case
3. 对比 baseline prompt vs suggested prompt 输出
4. 评分（用 LLM-as-judge 或简单契约校验）
5. 显著 win → 自动 apply / 显著 lose → 拒绝 / 平局 → 用户审

本版本：**只做 schema/契约层 shadow**（不实际 spawn writer，太贵）。
实际行为对比留路线图（需 Agent tool 并行 spawn）。

输出：_数据库/.learning/prompt_shadow_<ts>.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
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


def load_latest_suggestions(project_root: Path) -> list[dict]:
    """读 meta-prompt-optimizer 最新输出"""
    sg_dir = project_root / "_数据库" / ".evolution"
    if not sg_dir.exists():
        return []
    files = sorted(sg_dir.glob("prompt_suggestions_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return []
    data = load_json(files[0], {})
    return data.get("issues_found", []) or []


def static_review(suggestion: dict) -> dict:
    """对单条 suggestion 做静态审查（不实际跑），评 safety/impact/diff"""
    cs = suggestion.get("suggested_change", {})
    review = {
        "suggestion_id": suggestion.get("signal", "?"),
        "target_agent": suggestion.get("target_agent"),
        "static_checks": {
            "has_evidence": bool(suggestion.get("evidence")),
            "has_justification": bool(cs.get("justification")),
            "change_type_valid": cs.get("type") in ("elevate_priority", "add_directive", "reword", "remove"),
            "auto_apply_safe_declared": "auto_apply_safe" in suggestion,
        },
        "impact_estimate": {
            "confidence": suggestion.get("confidence", 0),
            "expected_impact": suggestion.get("expected_impact", ""),
        },
        "diff_preview": "",
    }

    # 计算 prompt diff（如有 current_text + new_text）
    current = cs.get("current_text", "")
    new = cs.get("new_text", "")
    if current and new:
        diff_lines = list(difflib.unified_diff(current.split("\n"), new.split("\n"),
                                                lineterm="", n=2))[:20]
        review["diff_preview"] = "\n".join(diff_lines)
        review["diff_line_count"] = len([l for l in diff_lines if l.startswith(("+", "-"))])
    else:
        review["diff_preview"] = "（无 current_text/new_text，无法 diff）"

    # 决策：是否 safe_to_auto_apply
    safe_to_apply = (
        review["static_checks"]["has_evidence"] and
        review["static_checks"]["has_justification"] and
        suggestion.get("confidence", 0) >= 0.8 and
        suggestion.get("auto_apply_safe") is True
    )
    review["decision"] = "auto_apply" if safe_to_apply else "needs_human_review"
    return review


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--apply-safe", action="store_true",
                    help="自动 apply decision=auto_apply 的建议（生成 .prompt_apply_log/）")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    suggestions = load_latest_suggestions(project_root)
    if not suggestions:
        print("[SKIP] 无最新 prompt suggestions（先跑 meta-prompt-optimizer agent）")
        sys.exit(0)

    reviews = [static_review(s) for s in suggestions]
    auto_apply_count = sum(1 for r in reviews if r["decision"] == "auto_apply")
    human_review_count = sum(1 for r in reviews if r["decision"] == "needs_human_review")

    out = {
        "scan_type": "prompt_shadow_test",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "suggestions_total": len(suggestions),
        "reviews": reviews,
        "decision_summary": {
            "auto_apply": auto_apply_count,
            "needs_human_review": human_review_count,
        },
        "_note": "本版本仅 schema/契约静态审查。实际跑 shadow（spawn 2 writer 并行写同章对比）需 Agent tool 协同。",
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"prompt_shadow_{ts}.json"
    save_json(out_path, out)
    print(f"[prompt_shadow_test] {len(suggestions)} 建议 → {auto_apply_count} 可自动 / {human_review_count} 需人审")
    for r in reviews[:5]:
        print(f"  [{r['decision'].upper()}] {r['suggestion_id']}")
    print(f"  报告: {out_path}")

    if args.apply_safe and auto_apply_count > 0:
        print(f"\n  ⚠️  --apply-safe 模式：建议主代理 spawn meta-prompt-optimizer 跑实际 prompt diff apply")
        print(f"     （当前版本未自动改 agent 文件，由用户审阅 reviews 后手动 git apply diff）")
    sys.exit(0)


if __name__ == "__main__":
    main()
