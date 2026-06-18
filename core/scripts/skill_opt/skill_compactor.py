"""skill_opt.skill_compactor — 切现有 skill_FINAL.md 为 SLOW/FAST/REFERENCE 三段

业界源 (arXiv 2605.23904 §3.3 SLOW_UPDATE 受保护段):
- SLOW_UPDATE = 作者量化指纹 (句长/段长/标点基线) → 锁死,optimizer 不许动
- FAST_UPDATE = 硬规则 + golden few-shot → SkillOpt 训练对象,可 patch
- REFERENCE  = 开发者注释/历史校准/版本演变 → 移到 作者风格_FINAL.json,不进 prompt

启发式分类 (E4 调研 + 真实样本 grep):
- SLOW: 标题含 "量化"/"基线"/"数值"/"标点节奏"/"虚词"/"TTR"/"per 1k 字"
- REFERENCE: 标题含 "v0"/"v1 校准"/"v1 复刻校准"/"风格演变"/"历史"/"设计说明"/"开发者"
- 其余 → FAST

输出:
- skill_FAST.md      → 进 writer/optimizer prompt
- skill_SLOW.md      → 进 prompt + 标记 PROTECTED
- skill_REFERENCE.md → 不进 prompt,留档
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path


# 启发式标记词
SLOW_KEYWORDS = [
    "量化", "基线", "数值", "标点节奏", "虚词", "TTR", "per 1k", "per 千字",
    "字/章", "字 /章", "签名搭配", "命名指纹", "数值契约",
]
REFERENCE_KEYWORDS = [
    "v0 自检", "v1 复刻校准", "v1 校准", "v2 校准", "v3 校准",
    "风格演变", "设计说明", "开发者", "历史",
    "为什么这么做", "之前的", "升级痕迹",
]


@dataclass
class Section:
    title: str           # 含 # 标题行
    level: int           # 1=#  2=##  3=### ...
    body: str            # 标题下的正文 (含 \n,不含标题本身)
    category: str = "FAST"

    @property
    def full(self) -> str:
        return self.title + "\n" + self.body if self.body else self.title

    @property
    def chars(self) -> int:
        return len(self.full)


@dataclass
class CompactResult:
    slow: list[Section] = field(default_factory=list)
    fast: list[Section] = field(default_factory=list)
    reference: list[Section] = field(default_factory=list)

    @property
    def stats(self) -> dict:
        def _sum(items: list[Section]) -> int:
            return sum(s.chars for s in items)
        return {
            "slow_chars": _sum(self.slow),
            "fast_chars": _sum(self.fast),
            "reference_chars": _sum(self.reference),
            "slow_count": len(self.slow),
            "fast_count": len(self.fast),
            "reference_count": len(self.reference),
        }


def _split_sections(skill_text: str) -> list[Section]:
    """按 markdown 标题切段。一级 / 二级 / 三级标题都当一个 Section,正文跟到下一标题。

    顶部无标题正文 → 单独一个 Section(title=空,level=0)。
    """
    lines = skill_text.split("\n")
    sections: list[Section] = []
    current_title = ""
    current_level = 0
    current_body: list[str] = []

    def _flush():
        if current_title or current_body:
            sections.append(
                Section(
                    title=current_title,
                    level=current_level,
                    body="\n".join(current_body).rstrip(),
                )
            )

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            _flush()
            current_level = len(m.group(1))
            current_title = line
            current_body = []
        else:
            current_body.append(line)
    _flush()
    return sections


def _classify(section: Section) -> str:
    """启发式分类。"""
    title_l = section.title.lower()
    title_full = section.title

    # 优先看 REFERENCE 关键词 (校准段比量化段优先识别为废料)
    for kw in REFERENCE_KEYWORDS:
        if kw in title_full:
            return "REFERENCE"

    # SLOW 关键词
    for kw in SLOW_KEYWORDS:
        if kw in title_full or kw.lower() in title_l:
            return "SLOW"

    return "FAST"


def compact_skill(skill_text: str) -> CompactResult:
    """切现有 skill_FINAL.md。"""
    sections = _split_sections(skill_text)
    result = CompactResult()
    for s in sections:
        # 顶部无标题的段(身份/yaml frontmatter)归 FAST(必保留)
        if not s.title:
            s.category = "FAST"
            result.fast.append(s)
            continue
        cat = _classify(s)
        s.category = cat
        if cat == "SLOW":
            result.slow.append(s)
        elif cat == "REFERENCE":
            result.reference.append(s)
        else:
            result.fast.append(s)
    return result


def emit_files(result: CompactResult, out_dir: Path) -> dict:
    """落盘三个 md 文件。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, items in (
        ("FAST", result.fast),
        ("SLOW", result.slow),
        ("REFERENCE", result.reference),
    ):
        if not items:
            continue
        p = out_dir / f"skill_{name}.md"
        body = "\n\n".join(s.full for s in items)
        if name == "SLOW":
            body = "<!-- PROTECTED · optimizer 不许动 · 作者量化指纹 -->\n\n" + body
        if name == "REFERENCE":
            body = "<!-- 仅留档 · 不进 writer/optimizer prompt -->\n\n" + body
        p.write_text(body, encoding="utf-8")
        paths[name] = str(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(
        description="切现有 skill_FINAL.md 为 SLOW/FAST/REFERENCE 三段 (SkillOpt 范式)"
    )
    ap.add_argument(
        "--skill", required=True, help="workspace/styles/<书名>/skill_FINAL.md"
    )
    ap.add_argument(
        "--out-dir", default=None,
        help="默认 <skill 同目录>/skill_compact/"
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="只看分类统计不落盘"
    )
    args = ap.parse_args()

    skill_path = Path(args.skill)
    if not skill_path.exists():
        print(f"[ERR] skill 文件不存在: {skill_path}")
        return 2

    text = skill_path.read_text(encoding="utf-8")
    result = compact_skill(text)
    stats = result.stats

    print(f"[输入] {skill_path} ({len(text)} chars)")
    print(f"[分类] SLOW={stats['slow_count']} 段 {stats['slow_chars']} chars (受保护)")
    print(f"       FAST={stats['fast_count']} 段 {stats['fast_chars']} chars (可改)")
    print(f"       REFERENCE={stats['reference_count']} 段 {stats['reference_chars']} chars (移走)")
    total = stats["slow_chars"] + stats["fast_chars"]
    reduction = 100 * (1 - total / len(text)) if text else 0
    print(f"[预期] 进 prompt: {total} chars (原 {len(text)} chars · 减 {reduction:.1f}%)")

    if args.dry_run:
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else skill_path.parent / "skill_compact"
    paths = emit_files(result, out_dir)
    print(f"[落盘] {out_dir}")
    for n, p in paths.items():
        print(f"  - {n}: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
