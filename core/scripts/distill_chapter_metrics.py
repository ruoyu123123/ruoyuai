#!/usr/bin/env python3
"""distill_chapter_metrics.py — 逐章定量 metrics（蒸馏 phase-0·确定性·喂 consolidate）。

🔴 为什么新建（真 distill 设计 code-verified·must_fix#3）：style_analyzer.py --batch 读整目录
写**一个 aggregate JSON**（无逐章 ch{N}_metrics.json·无顶层 profile 键），但 consolidate_author_
profile.py:81-88 按**逐章** `蒸馏进度/ch{N}_metrics.json` 读 `["profile"]["sentence_stats"]`
消费。故新建本脚本：逐章调 style_analyzer.analyze_text（in-process·非 subprocess·1000 章不炸）
写 `蒸馏进度/ch{N}_metrics.json = {"file","profile"}`（契约对齐 consolidate :85）。

设计纪律：纯确定性·零 LLM·幂等（已有 metrics 跳过·除非 --overwrite）。

用法：
  python distill_chapter_metrics.py <project_root> [--overwrite]
退出码：0 成功 / 1 原文/ 缺失或空
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import style_analyzer  # noqa: E402

_CH_NUM_RE = re.compile(r"第(\d+)章")


def run(project_root: Path, overwrite: bool = False) -> int:
    raw_dir = project_root / "原文"
    if not raw_dir.is_dir():
        print(f"[chapter_metrics] 原文/ 不存在: {raw_dir}（先跑 ingest_author_text）")
        return 1
    files = sorted(raw_dir.glob("第*章.txt"),
                   key=lambda p: int(_CH_NUM_RE.search(p.stem).group(1))
                   if _CH_NUM_RE.search(p.stem) else 0)
    if not files:
        print(f"[chapter_metrics] 原文/ 无 第N章.txt", file=sys.stderr)
        return 1
    dist = project_root / "蒸馏进度"
    dist.mkdir(parents=True, exist_ok=True)
    done, skipped = 0, 0
    for f in files:
        m = _CH_NUM_RE.search(f.stem)
        if not m:
            continue
        n = int(m.group(1))
        out = dist / f"ch{n}_metrics.json"
        if out.exists() and not overwrite:
            skipped += 1
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = f.read_text(encoding="gbk", errors="replace")
        try:
            profile = style_analyzer.analyze_text(text)
        except Exception as e:        # 单章坏不拖垮全批
            print(f"[chapter_metrics] 第{n}章 analyze 失败（跳过）: {e}", file=sys.stderr)
            continue
        out.write_text(json.dumps({"file": str(f), "profile": profile},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        done += 1
    print(f"[chapter_metrics] 写 {done} 章 metrics·跳过 {skipped}（已存在）→ {dist}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    sys.exit(run(Path(args.project_root), overwrite=args.overwrite))


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
