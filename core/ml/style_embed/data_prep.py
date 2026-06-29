#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:风格声纹嵌入
"""data_prep.py — 风格/声纹嵌入对比学习数据集构建（纯 stdlib · 可直接跑）

目标:把 10 位网文作者的原文章节切成 (text, author) chunk,构建对比学习正负对,
供 train.py 用对比学习(SupCon/MNRL/triplet) fine-tune 出**风格嵌入模型**。
一个模型同时服务:
  ① 作者级 SFS(风格保真度): cosine(生成文, 作者 centroid)
  ② 角色级 千人千面(声纹互距): 角色对话累积向量两两 cosine

SOTA 接地:
  · UAR/LUAR (Rivera-Soto EMNLP2021, LLNL/LUAR): 同作者多文档对比, episode 聚合
  · DramaCV (arXiv:2406.11368): 角色累积台词 → 512-dim, 同段内角色互为 in-batch negative
  · SupCon (arXiv:2004.11362) / SimCSE (arXiv:2104.08821): 监督/自监督对比 loss
  · 同作者 chunk 跨题材为正例 → 模型学到**题材无关的作者风格信号**(去题材, 北极星④)

切分协议(确定性):
  · 逐章读原文(UTF-8, gb18030 兜底), 剥章节标题行, 去全角缩进
  · 按段落累积成 ~chunk_size CJK 的窗口(段落边界切, 不切碎句)
  · 章节级 split: 每作者按章号排序, 前 train_frac 训练 / 中 val / 后 test_seen
  · held-out 作者整本进 test_unseen(衡量对**未见作者**的泛化 = 新书场景)

输出(data/, gitignore):
  author_chunks_{train,val,test_seen,test_unseen}.jsonl  每行 {text,author,author_id,book,chapter,chunk_idx,n_cjk}
  author_pairs_{train,val}.jsonl                          MNRL 正对 {anchor,positive,author_id,author}
  character_dialogues.jsonl                               角色级(phase2) {text,character,book,author,label,n_utt,n_cjk}
  manifest.json                                           数据集统计 + 配置(可复现)

用法:
  python data_prep.py                     # 默认全量(预算不限,不抽样)
  python data_prep.py --chunk-size 768 --holdout-authors 佛本是道,将夜
  python data_prep.py --no-characters     # 跳过角色级抽取(只作者级)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ── 路径 ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[3]
STYLES_DIR = REPO_ROOT / "workspace" / "styles"
OUT_DIR = Path(__file__).resolve().parent / "data"

# 10 位作者(每本书 = 1 位作者; book 名即 author 标签)
DEFAULT_BOOKS = [
    "主神大道", "诡秘之主", "轮回乐园", "遮天", "剑来",
    "将夜", "佛本是道", "人生长恨水长东", "小世界其乐无穷", "惊悚乐园",
]
# held-out 作者(整本不进训练, 用于未见作者泛化 AUC)。可经 --holdout-authors 覆盖。
DEFAULT_HOLDOUT = ["佛本是道", "将夜"]

CJK_RE = re.compile(r"[一-鿿]")
# 章节标题行: 以 "第N章"/"第N卷" 开头(章号 + 可选标题)
TITLE_RE = re.compile(r"^\s*第[0-9一二三四五六七八九十百千零两〇]+[章卷节回篇]")
# 章末作者吆喝/推书噪声(轻度清洗, 命中整行丢弃)
NOISE_RE = re.compile(r"(推荐票|月票|求订阅|求收藏|作者的话|ps[：:]|PS[：:]|^第.{0,6}更$|起点中文网|本章完)")

# ── 角色对话归属正则(复用 character_distinctiveness_scanner 的启发式)──────────
_SAY_VERBS = (r"低声道|冷笑道|沉吟道|轻声道|说道|喊道|问道|笑道|喝道|怒道|叹道|"
              r"低声|冷笑|沉吟|轻声|说|道|喊|问|笑|喝|怒|叹")
_ATTR_RE = re.compile(
    r"([一-鿿]{1,4}?)(?:" + _SAY_VERBS + r")[：:]\s*[\"“「]([^\"”」]{2,120})[\"”」]")
_ATTR_POST_RE = re.compile(
    r"[\"“「]([^\"”」]{2,120})[\"”」]\s*([一-鿿]{1,4}?)(?:" + _SAY_VERBS + r")")


# ── 工具 ────────────────────────────────────────────────────────────────────
def cjk_count(text: str) -> int:
    return len(CJK_RE.findall(text))


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def chapter_num(path: Path) -> int:
    """从 文件名 '第0001章.txt' / '第100章.txt' 抽章号(用于章节级排序 split)。"""
    m = re.search(r"第0*([0-9]+)章", path.stem)
    return int(m.group(1)) if m else 10 ** 9


def clean_chapter(text: str) -> str:
    """剥标题行 + 去全角缩进 + 丢噪声行。返回正文段落以 \n 连接。"""
    lines = text.splitlines()
    out = []
    for i, ln in enumerate(lines):
        s = ln.replace("　", "").strip()  # 去全角空格缩进
        if not s:
            continue
        if i < 3 and TITLE_RE.match(s):       # 开头几行的章节标题
            continue
        if NOISE_RE.search(s):
            continue
        out.append(s)
    return "\n".join(out)


def chunk_by_paragraph(text: str, chunk_size: int, min_chunk: int) -> list[str]:
    """按段落累积成 ~chunk_size CJK 的窗口(段落边界切, 不切碎句子)。

    单段超长(> 1.6*chunk_size)按句末标点再细切, 避免极长段吞掉整窗。
    末窗 < min_chunk 且已有 ≥1 窗 → 并入前一窗(避免碎尾污染)。"""
    paras = [p for p in text.split("\n") if p.strip()]
    # 预拆超长段
    expanded: list[str] = []
    big = int(chunk_size * 1.6)
    for p in paras:
        if cjk_count(p) <= big:
            expanded.append(p)
            continue
        buf = ""
        for seg in re.split(r"(?<=[。！？…”」])", p):
            buf += seg
            if cjk_count(buf) >= chunk_size:
                expanded.append(buf)
                buf = ""
        if buf.strip():
            expanded.append(buf)
    # 累积成窗
    windows: list[str] = []
    buf: list[str] = []
    buf_cjk = 0
    for p in expanded:
        buf.append(p)
        buf_cjk += cjk_count(p)
        if buf_cjk >= chunk_size:
            windows.append("\n".join(buf))
            buf, buf_cjk = [], 0
    if buf:
        tail = "\n".join(buf)
        if cjk_count(tail) < min_chunk and windows:
            windows[-1] = windows[-1] + "\n" + tail
        else:
            windows.append(tail)
    return [w for w in windows if cjk_count(w) >= min_chunk]


# ── 作者级数据集 ─────────────────────────────────────────────────────────────
def build_author_chunks(books: list[str], holdout: list[str],
                        chunk_size: int, min_chunk: int,
                        train_frac: float, val_frac: float) -> dict:
    """构建 {split: [records]} + author_id 映射。"""
    author_ids = {b: i for i, b in enumerate(sorted(books))}
    splits: dict[str, list] = {"train": [], "val": [],
                               "test_seen": [], "test_unseen": []}
    per_book_stats = {}

    for book in books:
        raw_dir = STYLES_DIR / book / "原文"
        if not raw_dir.is_dir():
            print(f"[WARN] 缺原文目录: {raw_dir}", file=sys.stderr)
            continue
        files = sorted(raw_dir.glob("*.txt"), key=chapter_num)
        is_holdout = book in holdout
        n_files = len(files)
        n_train = int(n_files * train_frac)
        n_val = int(n_files * val_frac)

        book_chunks = 0
        for ci, f in enumerate(files):
            body = clean_chapter(read_text(f))
            if cjk_count(body) < min_chunk:
                continue
            chunks = chunk_by_paragraph(body, chunk_size, min_chunk)
            if is_holdout:
                split = "test_unseen"
            elif ci < n_train:
                split = "train"
            elif ci < n_train + n_val:
                split = "val"
            else:
                split = "test_seen"
            ch = chapter_num(f)
            for k, c in enumerate(chunks):
                splits[split].append({
                    "text": c, "author": book, "author_id": author_ids[book],
                    "book": book, "chapter": ch, "chunk_idx": k, "n_cjk": cjk_count(c),
                })
                book_chunks += 1
        per_book_stats[book] = {
            "chapters": n_files, "chunks": book_chunks,
            "author_id": author_ids[book], "holdout": is_holdout,
        }
        print(f"  [OK] {book}: {n_files} 章 → {book_chunks} chunk"
              f"{' (HELD-OUT)' if is_holdout else ''}")
    return {"splits": splits, "author_ids": author_ids, "per_book": per_book_stats}


def build_pairs(chunks: list[dict], pairs_per_author: int, seed: int) -> list[dict]:
    """为 MNRL 构建同作者 (anchor, positive) 正对。in-batch 其它作者天然为负例。

    每作者随机配对其 chunk(不重复同一个), 控制每作者 pairs_per_author 上限以平衡。"""
    rng = random.Random(seed)
    by_author: dict[int, list[str]] = defaultdict(list)
    name = {}
    for r in chunks:
        by_author[r["author_id"]].append(r["text"])
        name[r["author_id"]] = r["author"]
    pairs = []
    for aid, texts in by_author.items():
        if len(texts) < 2:
            continue
        idx = list(range(len(texts)))
        rng.shuffle(idx)
        # 相邻配对成对, 取 pairs_per_author 个
        n = min(pairs_per_author, len(idx) // 2)
        for i in range(n):
            a, b = idx[2 * i], idx[2 * i + 1]
            pairs.append({"anchor": texts[a], "positive": texts[b],
                          "author_id": aid, "author": name[aid]})
    rng.shuffle(pairs)
    return pairs


# ── 角色级数据集(phase2)─────────────────────────────────────────────────────
def extract_character_dialogues(books: list[str], holdout: list[str],
                                min_dialogue_cjk: int, min_utterances: int,
                                max_files_per_book: int | None) -> list[dict]:
    """扫全本抽 (book, character) 累积台词。label = 'book::character'(跨书角色不混)。"""
    records = []
    for book in books:
        raw_dir = STYLES_DIR / book / "原文"
        if not raw_dir.is_dir():
            continue
        files = sorted(raw_dir.glob("*.txt"), key=chapter_num)
        if max_files_per_book:
            files = files[:max_files_per_book]
        bag: dict[str, list[str]] = defaultdict(list)
        for f in files:
            text = read_text(f)
            seen_spans = []
            cands = []
            for m in _ATTR_RE.finditer(text):
                cands.append((m.start(2), m.group(1), m.group(2)))
            for m in _ATTR_POST_RE.finditer(text):
                cands.append((m.start(1), m.group(2), m.group(1)))
            cands.sort(key=lambda x: x[0])
            for pos, nm, content in cands:
                if any(s <= pos < e for s, e in seen_spans):
                    continue
                nm = nm.strip()
                if len(nm) < 2:           # 单字名多为误抽(如"他/她") → 丢
                    continue
                bag[nm].append(content.strip())
                seen_spans.append((pos, pos + len(content)))
        for nm, utts in bag.items():
            total = sum(cjk_count(u) for u in utts)
            if len(utts) < min_utterances or total < min_dialogue_cjk:
                continue
            records.append({
                "text": "\n".join(utts), "character": nm, "book": book,
                "author": book, "label": f"{book}::{nm}",
                "n_utt": len(utts), "n_cjk": total,
            })
        kept = sum(1 for r in records if r["book"] == book)
        print(f"  [char] {book}: {kept} 个角色(≥{min_utterances}句/≥{min_dialogue_cjk}字)")
    return records


# ── 落盘 ────────────────────────────────────────────────────────────────────
def write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="风格/声纹嵌入对比学习数据集构建")
    ap.add_argument("--chunk-size", type=int, default=512, help="每 chunk 目标 CJK 字数")
    ap.add_argument("--min-chunk", type=int, default=256, help="chunk 最小 CJK(短于则丢/并尾)")
    ap.add_argument("--train-frac", type=float, default=0.8)
    ap.add_argument("--val-frac", type=float, default=0.1)  # 余下 = test_seen
    ap.add_argument("--holdout-authors", default=",".join(DEFAULT_HOLDOUT),
                    help="逗号分隔, 整本进 test_unseen(未见作者泛化)")
    ap.add_argument("--pairs-per-author", type=int, default=4000,
                    help="每作者 MNRL 正对数上限(平衡 + 预算不限可调大)")
    ap.add_argument("--no-characters", action="store_true", help="跳过角色级抽取")
    ap.add_argument("--char-min-utt", type=int, default=20)
    ap.add_argument("--char-min-cjk", type=int, default=400)
    ap.add_argument("--char-max-files", type=int, default=None,
                    help="角色抽取每本最多扫几章(None=全量)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    holdout = [b.strip() for b in args.holdout_authors.split(",") if b.strip()]
    print(f"== 作者级 chunk 构建 (chunk={args.chunk_size}CJK, holdout={holdout}) ==")
    res = build_author_chunks(DEFAULT_BOOKS, holdout, args.chunk_size,
                              args.min_chunk, args.train_frac, args.val_frac)
    splits = res["splits"]
    for name, rows in splits.items():
        write_jsonl(OUT_DIR / f"author_chunks_{name}.jsonl", rows)

    print("== MNRL 正对构建 ==")
    pairs_train = build_pairs(splits["train"], args.pairs_per_author, args.seed)
    pairs_val = build_pairs(splits["val"], max(args.pairs_per_author // 8, 200),
                            args.seed + 1)
    write_jsonl(OUT_DIR / "author_pairs_train.jsonl", pairs_train)
    write_jsonl(OUT_DIR / "author_pairs_val.jsonl", pairs_val)

    char_count = 0
    if not args.no_characters:
        print("== 角色级台词抽取 (phase2) ==")
        char_rows = extract_character_dialogues(
            DEFAULT_BOOKS, holdout, args.char_min_cjk, args.char_min_utt,
            args.char_max_files)
        write_jsonl(OUT_DIR / "character_dialogues.jsonl", char_rows)
        char_count = len(char_rows)

    manifest = {
        "_marker": "🔴 2026-06-29 NN训练:风格声纹嵌入",
        "config": vars(args), "holdout_authors": holdout,
        "author_ids": res["author_ids"], "per_book": res["per_book"],
        "split_chunk_counts": {k: len(v) for k, v in splits.items()},
        "total_author_chunks": sum(len(v) for v in splits.values()),
        "pairs_train": len(pairs_train), "pairs_val": len(pairs_val),
        "character_records": char_count,
        "author_chunk_balance": dict(Counter(
            r["author"] for r in splits["train"]).most_common()),
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== 数据集统计 ==")
    print(f"  作者数(train): {len(set(r['author_id'] for r in splits['train']))}")
    for k, v in splits.items():
        print(f"  {k}: {len(v)} chunk")
    print(f"  MNRL 正对: train={len(pairs_train)} val={len(pairs_val)}")
    print(f"  角色级记录: {char_count}")
    print(f"  → {OUT_DIR}")


if __name__ == "__main__":
    main()
