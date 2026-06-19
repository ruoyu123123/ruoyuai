"""skill_rewrite_advisor.py — 检查 skill_rewrite_suggestions, 非空时 advisory 提示

F2+G4 调研发现: learning_loop.reflect_attribution 已在产"该改哪条 skill"建议,
但写进 写作经验.json.skill_rewrite_suggestions 后无人消费。
本脚本读该字段, 非空时打印 advisory 播报, 告诉用户/主代理可以跑 /distill-style-skillopt。

接入点: cluster-save-state step9 末尾跑 (零 LLM · 纯读打印)
exit 0: 无论有无建议都不阻断 (advisory only · 北极星⑤)

用法:
  python core/scripts/skill_rewrite_advisor.py <project_root>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def check(project_root: Path) -> list[dict]:
    """读写作经验.json 的 skill_rewrite_suggestions,返回非空建议列表。"""
    exp_path = project_root / "_数据库" / "写作经验.json"
    if not exp_path.exists():
        return []
    try:
        d = json.loads(exp_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    suggestions = d.get("skill_rewrite_suggestions", [])
    if not isinstance(suggestions, list):
        return []
    return [s for s in suggestions if s]


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python skill_rewrite_advisor.py <project_root>", file=sys.stderr)
        return 2

    project_root = Path(sys.argv[1])
    suggestions = check(project_root)

    if not suggestions:
        return 0

    # advisory 播报
    print(f"\n{'=' * 60}")
    print(f"📋 [SkillOpt advisory] 发现 {len(suggestions)} 条 skill 改写建议")
    print(f"{'=' * 60}")
    for i, s in enumerate(suggestions[:5], 1):
        if isinstance(s, dict):
            seg = s.get("segment", s.get("section", "?"))
            reason = s.get("reason", s.get("issue", "?"))
            print(f"  {i}. 段: {seg}")
            print(f"     原因: {reason}")
        else:
            print(f"  {i}. {str(s)[:100]}")
    if len(suggestions) > 5:
        print(f"  ... 还有 {len(suggestions) - 5} 条")
    print()
    print("  💡 建议: 跑 /distill-style-skillopt 精化 skill")
    print(f"     python core/scripts/skill_opt/train.py --project <风格库> --skill <skill_FINAL.md> --reward-route distill")
    print(f"{'=' * 60}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
