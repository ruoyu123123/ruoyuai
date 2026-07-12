#!/usr/bin/env python3
"""pretooluse_chapter_edit_gate.py — L1 章节正文守卫 hook

防御目标:
1. 主代理 / sub-agent 在写章节正文时不慎写入剧本体 / 文学过渡 / 章末抽象 cliffhanger
2. 这类内容无 scanner 拦，出货后才会被用户察觉打回——必须在写入时拦截

拦截策略:
- tool ∈ {Write, Edit, MultiEdit}
- file_path 落在小说项目 章节/ 或 cluster_*_draft/ 路径下
- 检查 content / new_string 含 banned_patterns:
  · 剧本体（（镜头XX）/（旁白）/（音效）等）→ exit 2 hard_gate
  · 章末文学过渡 * 分隔符 / 收束句 → exit 2 hard_gate（仅检测靠近文末的）

权威来源:
- core/claude-home/lessons/feedback_no_screenplay_stage_directions_in_novels.md
- memory feedback_no_screenplay_stage_directions_in_novels.md
"""
import json
import re
import sys
import os
from pathlib import Path

# banned pattern + 扫描判定在共享库 plan_step_gates（单一真相源）。
# 本 hook 是薄 wrapper：解析 stdin → 取 content/project_root → 调 check_chapter_edit
# → ok?exit0:exit2。SCREENPLAY_PATTERNS/CHAPTER_END_PATTERNS/scan_* 以 lib 为准。
_SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from plan_step_gates import check_chapter_edit  # noqa: E402


def is_chapter_file(file_path: str) -> tuple[bool, str | None]:
    """判断是否为小说章节正文文件。返回 (is_chapter, project_root)。"""
    p = Path(file_path).as_posix() if file_path else ""
    if not p:
        return False, None
    # 匹配 workspace/novels/<书名>/章节/...txt
    m = re.search(r"(.+/workspace/novels/[^/]+)/章节/[^/]+/.+\.txt$", p)
    if m:
        return True, m.group(1)
    # cluster_*_draft 草稿
    m = re.search(r"(.+/workspace/novels/[^/]+)/章节/cluster_[^/]+_draft/.+\.txt$", p)
    if m:
        return True, m.group(1)
    return False, None


def extract_content(tool_name: str, tool_input: dict) -> str:
    """从 tool_input 取要写入/编辑的文本内容。"""
    if tool_name == "Write":
        return tool_input.get("content", "") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string", "") or ""
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits", []) or []
        return "\n".join(e.get("new_string", "") for e in edits)
    return ""


def main():
    try:
        # stdin 按 bytes 读·json 自动 UTF-8 解码（文本模式在 GBK 控制台会把载荷读花）
        payload = json.loads(sys.stdin.buffer.read())
    except Exception:
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "")

    is_ch, project_root = is_chapter_file(file_path)
    if not is_ch:
        sys.exit(0)

    content = extract_content(tool_name, tool_input)
    if not content:
        sys.exit(0)

    # 判定走 check_chapter_edit（共享库唯一真相源）。
    result = check_chapter_edit(content)
    if result["ok"]:
        sys.exit(0)

    # 命中 → exit 2 hard_gate 拦截
    print(f"❌ [Hook chapter_edit_gate] 文件: {file_path}", file=sys.stderr)
    print(f"   {result['msg']}", file=sys.stderr)
    print(f"   📝 权威 lesson: memory/feedback_no_screenplay_stage_directions_in_novels.md",
          file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
