#!/usr/bin/env python3
"""机械汇编开发机 memory/feedback_*.md → core/claude-home/lessons/global_feedback_rules.md。

背景（2026-06-13 frozen 断裂修复）：gen_writer._collect_feedback_rules 与 build_manifest
的 v19.3 全局 MEMORY feedback 注入都读开发机
~/.claude/projects/D--Desktop-ruoyuai/memory/feedback_*.md ——
frozen exe 用户机上该路径不存在 → writer 防御层整层静默为空。
本脚本把全部 feedback 规则机械汇编成单文件随 exe 出货
（ruoyu_gui.spec 6b 段整目录收 lessons/*.md 自动带上），
gen_writer/build_manifest 在 home 路径 miss/为空时 fallback 读它。

🔴 纪律：只做格式搬运 —— 剥 frontmatter 框架（name/metadata/originSessionId 行），
保留 description + 正文原文逐字节不动，不增删改任何规则语义。

用法（开发机重跑汇编）：
    python core/scripts/assemble_global_feedback_rules.py
    python core/scripts/assemble_global_feedback_rules.py --memory-dir <PATH> --output <PATH>
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# 每条规则节的机器可读定位锚（build_manifest fallback 解析 digest 用·对 LLM 不可见噪音最小）
RULE_MARKER = "<!-- FEEDBACK_RULE: {fname} -->"

DEFAULT_MEMORY_DIR = (
    Path.home() / ".claude" / "projects" / "D--Desktop-ruoyuai" / "memory"
)
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "core" / "claude-home" / "lessons" / "global_feedback_rules.md"
)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """剥 YAML frontmatter（--- ... ---）→ (字段 dict, 正文)。

    只抽顶层单行字段（name/description）；memory 文件的 description 均为单行（已实测）。
    无 frontmatter → 字段空 dict + 全文当正文。
    """
    fields: dict[str, str] = {}
    if not text.startswith("---"):
        return fields, text
    end = text.find("\n---", 3)
    if end < 0:
        return fields, text
    fm = text[3:end]
    body = text[end + len("\n---"):].lstrip("\n")
    for ln in fm.splitlines():
        # 顶层字段（不缩进）·metadata 子项缩进不抽
        if ln.startswith((" ", "\t")) or ":" not in ln:
            continue
        key, _, val = ln.partition(":")
        fields[key.strip()] = val.strip()
    return fields, body


def assemble(memory_dir: Path, output: Path) -> int:
    files = sorted(memory_dir.glob("feedback_*.md"))
    if not files:
        print(f"[FATAL] {memory_dir} 下没有 feedback_*.md（路径错 / 非开发机？）")
        return 1

    sections = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        fields, body = _split_frontmatter(text)
        title = fields.get("name") or f.stem.replace("_", "-")
        desc = fields.get("description", "")
        chunk = [RULE_MARKER.format(fname=f.name), f"## {title}", ""]
        if desc:
            chunk.append(f"> description: {desc}")
            chunk.append("")
        chunk.append(body.rstrip())
        sections.append("\n".join(chunk))

    header = (
        "# 🔴 全局 feedback 规则汇编（随 exe 出货）\n\n"
        f"> 机械汇编自开发机 memory（~/.claude/projects/D--Desktop-ruoyuai/memory/"
        f"feedback_*.md · 共 {len(files)} 条）。\n"
        "> 随 exe 出货：frozen 环境无开发机 memory 路径时，"
        "gen_writer/build_manifest fallback 读本文件。\n"
        "> 更新方式 = 重跑汇编：`python core/scripts/assemble_global_feedback_rules.py`"
        "（🔴 不要手改本文件——改源 memory 后重新汇编）。\n"
        "> 规则为用户定稿原文照搬·汇编只做格式搬运不增删改语义。\n"
        ">\n"
        "> 以下是历史用户反馈沉淀的全局禁令/规则，写作时**逐条遵守**。"
        "违反 = 出货后被打回 + lesson 复发。\n"
    )
    out_text = header + "\n---\n\n" + "\n\n---\n\n".join(sections) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(out_text, encoding="utf-8")
    print(f"[OK] 汇编 {len(files)} 条 feedback 规则 → {output}"
          f"（{len(out_text)} chars）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-dir", type=Path, default=DEFAULT_MEMORY_DIR,
                    help=f"feedback_*.md 源目录（默认 {DEFAULT_MEMORY_DIR}）")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                    help=f"汇编产物路径（默认 {DEFAULT_OUTPUT}）")
    args = ap.parse_args()
    return assemble(args.memory_dir, args.output)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
