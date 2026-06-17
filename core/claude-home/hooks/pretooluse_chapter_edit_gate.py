#!/usr/bin/env python3
"""pretooluse_chapter_edit_gate.py — L1 章节正文守卫 hook

防御目标（cluster_001 ch4 三次翻车 sediment）:
1. 主代理 / sub-agent 在写章节正文时不慎写入剧本体 / 文学过渡 / 章末抽象 cliffhanger
2. 这类内容无 scanner 拦 → 出货后用户察觉打回 → 浪费两轮迭代

拦截策略:
- tool ∈ {Write, Edit, MultiEdit}
- file_path 落在小说项目 章节/ 或 cluster_*_draft/ 路径下
- 检查 content / new_string 含 banned_patterns:
  · 剧本体（（镜头XX）/（旁白）/（音效）等）→ exit 2 hard_gate
  · 章末文学过渡 * 分隔符 / 收束句 → exit 2 hard_gate（仅检测靠近文末的）
- 旁路: 项目 _数据库/.chapter_edit_bypass.flag 存在 → 全放行

权威来源:
- core/claude-home/lessons/feedback_no_screenplay_stage_directions_in_novels.md
- memory feedback_no_screenplay_stage_directions_in_novels.md
"""
import json
import re
import sys
import os
from pathlib import Path

# ============ banned patterns（来自 lesson 权威清单）============

# 剧本体（任何位置出现都拦）
SCREENPLAY_PATTERNS = [
    (r"（镜头[^）]{0,15}）", "剧本体镜头指令"),
    (r"（切镜[^）]{0,10}）", "剧本体切镜"),
    (r"（旁白[^）]{0,15}）", "剧本体旁白"),
    (r"（画外音[^）]{0,15}）", "剧本体画外音"),
    (r"（音效[^)]{0,15}）", "剧本体音效"),
    (r"（背景音[^）]{0,15}）", "剧本体背景音"),
    (r"（[^）]{0,15}的视角[^）]{0,5}）", "剧本体 POV 指令"),
    (r"（[^）]{0,10}离开[^）]{0,10}视角[^）]{0,5}）", "剧本体 POV 切换"),
    (r"\bCUT TO\b", "英文剧本切镜"),
    (r"\bFADE\s+(IN|OUT)\b", "英文剧本淡入淡出"),
    (r"\(V\.O\.\)", "英文画外音标记"),
    (r"\(O\.S\.\)", "英文 off-screen 标记"),
]

# 章末过渡（仅文末 30 行拦）
# 2026-05-29 北极星 P4 [H2-dont]：落字瞬间 hard 拦（exit 2）只保留【物理分隔符 + 剧本体】
# ——这些是格式污染，任何风格都不该有。原先一并 hard 拦的【语义收束句】（一切安静下来/
# 灯熄了/画面渐暗…）是读者体验偏好，落字瞬间硬拦比顾问制更刚、连「先写后豁免」都剥夺，
# 且正则误杀场景中段正常句 → 移出 hook 硬拦，下放给 audit 的 CHAPTER_END_CLOSURE_ADVISORY
# （advisory 可豁免）。守原则5「不干涉模型判断」。
CHAPTER_END_PATTERNS = [
    (r"^\s*\*{1,3}\s*$", "章末单独 * 分隔符 · 大结局感"),
    (r"^\s*[·]{3,}\s*$", "章末单独 ··· 分隔符"),
    (r"^\s*—{3,}\s*$", "章末单独 ——— 分隔符"),
]


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


def check_screenplay_patterns(content: str) -> list[tuple[str, str]]:
    """扫剧本体（任意位置）。返回 [(matched_text, reason)]。"""
    hits = []
    for pattern, reason in SCREENPLAY_PATTERNS:
        for m in re.finditer(pattern, content, re.IGNORECASE | re.MULTILINE):
            hits.append((m.group(0), reason))
    return hits


def check_chapter_end_transitions(content: str) -> list[tuple[str, str]]:
    """扫章末过渡（仅文末 30 行）。"""
    hits = []
    lines = content.split("\n")
    tail = "\n".join(lines[-30:])
    for pattern, reason in CHAPTER_END_PATTERNS:
        for m in re.finditer(pattern, tail, re.MULTILINE):
            hits.append((m.group(0), reason))
    return hits


def has_bypass(project_root: str | None) -> bool:
    # 🔴 opt-out 持久性是设计如此·非 bug：.chapter_edit_bypass.flag 存在即旁路·刻意无自动过期
    # （本地单用户工具·用户显式建/删·自动失效会在编辑中途突然重新拦截）。重启拦截=删该 flag。
    if not project_root:
        return False
    flag = Path(project_root) / "_数据库" / ".chapter_edit_bypass.flag"
    return flag.exists()


def main():
    try:
        payload = json.loads(sys.stdin.read())
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

    if has_bypass(project_root):
        sys.exit(0)

    content = extract_content(tool_name, tool_input)
    if not content:
        sys.exit(0)

    screenplay_hits = check_screenplay_patterns(content)
    end_hits = check_chapter_end_transitions(content)

    all_hits = screenplay_hits + end_hits
    if not all_hits:
        sys.exit(0)

    # 命中 → exit 2 hard_gate 拦截
    print(f"❌ [Hook chapter_edit_gate] 章节正文检测到禁用 pattern", file=sys.stderr)
    print(f"   文件: {file_path}", file=sys.stderr)
    print(f"   命中:", file=sys.stderr)
    for matched, reason in all_hits[:8]:
        snippet = matched.strip()[:60]
        print(f"     - [{reason}] {snippet!r}", file=sys.stderr)
    if len(all_hits) > 8:
        print(f"     ... 还有 {len(all_hits) - 8} 处", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"   📝 权威 lesson: memory/feedback_no_screenplay_stage_directions_in_novels.md", file=sys.stderr)
    print(f"   📝 修复:", file=sys.stderr)
    print(f"     · 剧本体 → 删掉，POV 不切换让角色全程在场", file=sys.stderr)
    print(f"     · 章末过渡 → 删掉，最后一句 = 心理悬念峰值（具体可验证的异常事实）", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"   旁路（仅紧急）：touch <项目>/_数据库/.chapter_edit_bypass.flag", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
