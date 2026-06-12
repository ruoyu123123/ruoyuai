#!/usr/bin/env python3
"""ingest_author_text.py — 作者作品 raw 文本 → 原文/第N章.txt（蒸馏 phase-0 入口·确定性）。

非技术用户在 GUI 粘贴/上传整本作者作品（一大段文本），本脚本按章标题正则切成
`原文/第N章.txt`（与现有风格库 原文/ 同格式·首行 `第N章 标题`），供 cluster_segmenter /
distill_chapter_metrics / style_analyzer 逐章消费。

设计纪律：
- 纯确定性·零 LLM·零网络。
- 章标题正则：行首 `第<阿拉伯数字|中文数字>章`（兼容「第100章 标题」「第一百章」）。
- UTF-8 强制（codepoint 安全·中文不乱码）·章数 sanity（太少警告·可能没切对）。
- 幂等：原文/ 已有 .txt 且 --no-overwrite（默认）→ 跳过不覆盖（防重复 ingest 毁已有库）。

用法：
  python ingest_author_text.py <project_root> --source <raw.txt> [--overwrite]
退出码：0 成功 / 1 源缺失或切章失败 / 2 章数异常（sanity 未过）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 章标题：行首「第 + (阿拉伯数字 / 中文数字) + 章」+ 可选标题（到行尾）
_CH_RE = re.compile(r"^[\s　]*第\s*([0-9]+|[一二三四五六七八九十百千零两]+)\s*章")

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(s: str) -> int:
    """中文数字 → int（支持到千·一百二十三 / 一千零五）。纯阿拉伯直接 int。"""
    if s.isdigit():
        return int(s)
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in _CN_NUM:
            number = _CN_NUM[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        else:
            number = 0
    return total + section + number


def split_chapters(raw: str) -> list[tuple[int, str]]:
    """raw → [(章号, 整章文本含标题行)]。按章标题行切。"""
    lines = raw.splitlines()
    chapters: list[tuple[int, list[str]]] = []
    cur_num = None
    cur_lines: list[str] = []
    for line in lines:
        m = _CH_RE.match(line)
        if m:
            if cur_num is not None:
                chapters.append((cur_num, cur_lines))
            try:
                cur_num = _cn_to_int(m.group(1))
            except (ValueError, KeyError):
                cur_num = (chapters[-1][0] + 1) if chapters else 1
            cur_lines = [line]
        else:
            if cur_num is None:
                continue  # 第一个章标题前的导言/序 丢弃
            cur_lines.append(line)
    if cur_num is not None:
        chapters.append((cur_num, cur_lines))
    return [(n, "\n".join(ls).strip() + "\n") for n, ls in chapters]


def ingest(project_root: Path, source: Path, overwrite: bool = False) -> int:
    if not source.exists():
        print(f"[ingest] 源文件不存在: {source}", file=sys.stderr)
        return 1
    try:
        raw = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # 兼容 GBK 老文本 → 转 UTF-8
        raw = source.read_text(encoding="gbk", errors="replace")
    raw_dir = project_root / "原文"
    existing = list(raw_dir.glob("*.txt")) if raw_dir.is_dir() else []
    if existing and not overwrite:
        print(f"[ingest] 原文/ 已有 {len(existing)} 章且未 --overwrite → 跳过（防毁已有库）",
              file=sys.stderr)
        return 0
    chapters = split_chapters(raw)
    if not chapters:
        print("[ingest] 未切出任何章（章标题正则未命中·检查源文本格式「第N章」）",
              file=sys.stderr)
        return 2
    # 狩猎修：多卷重新编号的网文（每卷都从第1章起）章号重复 → 同名互覆静默丢大段语料。
    # 检测到重复 → 按出现顺序全局重排 1..N（蒸馏只关心连续语料·不关心原始卷内编号）。
    nums = [n for n, _ in chapters]
    if len(set(nums)) != len(nums):
        print(f"[ingest] ⚠ 检测到重复章号（疑多卷重编号·{len(nums)} 章去重后 "
              f"{len(set(nums))}）→ 按出现顺序重排为 1~{len(chapters)}", file=sys.stderr)
        chapters = [(i + 1, text) for i, (_, text) in enumerate(chapters)]
    raw_dir.mkdir(parents=True, exist_ok=True)
    for num, text in chapters:
        (raw_dir / f"第{num}章.txt").write_text(text, encoding="utf-8")
    print(f"[ingest] 切出 {len(chapters)} 章 → {raw_dir}（第{chapters[0][0]}"
          f"~第{chapters[-1][0]}章）", file=sys.stderr)
    # sanity：太少可能没切对（蒸馏 cluster 级至少需几章）
    if len(chapters) < 3:
        print(f"[ingest] ⚠ 仅 {len(chapters)} 章·偏少（多卷蒸馏建议 ≥ 数十章）", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--source", required=True, help="作者作品 raw 文本（一大段·含「第N章」标题）")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有 原文/（默认跳过保护）")
    args = ap.parse_args()
    pr = Path(args.project_root)
    src = Path(args.source)
    if not src.is_absolute():
        src = pr / args.source
    sys.exit(ingest(pr, src, overwrite=args.overwrite))


if __name__ == "__main__":
    main()
