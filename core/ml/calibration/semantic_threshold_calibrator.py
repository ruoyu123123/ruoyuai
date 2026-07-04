#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-04 Wave-6 语义阈值校准 harness
"""semantic_threshold_calibrator.py — ruoyu_style 真机内容/风格可分性测量。

【核心疑问】ruoyu_style（`core/ml/style_embed/runs/style_embed_v1/final`）是**作者判别**模型
（训练目标=同作者 vs 异作者），而全仓一批语义阈值（`SEMANTIC_RESONANCE_SIM_THRESHOLD` /
`SEMANTIC_ANCHOR_SIM_THRESHOLD` / `SEMANTIC_THREAD_MATCH_THRESHOLD` / `MILESTONE_SEMANTIC_TOUCH_FLOOR`
/ `topic_drift_scanner` 的 scope_summary 校验等）守的是**内容**语义关系（这段话是否呼应那段摘要/
milestone）。如果模型只认得「同一个作者」而认不出「内容是否相关」，拿它标定内容类阈值就是自欺。

【五类样本对】（段落=原文按行切分，见 `load_book_paragraphs`；每类默认 ≥150 对）
  1. pos_adjacent                    同章相邻段落对（强内容相关）
  2. pos_same_chapter_far            同章相距 ≥`FAR_PARA_DISTANCE` 段的段落对（中等相关）
  3. probe_same_book_diff_chapter    同书相距 ≥`FAR_CHAPTER_DISTANCE` 章的段落对
                                     （同风格·不同内容——风格 vs 内容探针）
  4. neg_cross_book                  跨书随机段落对（不同风格+不同内容）
  5. summary_vs_body_{pos,neg}       章首 `SUMMARY_CJK_TARGET` 字当摘要代理 vs 同章后部（pos）/
                                     跨书段落（neg）——直接代理 content_echo 关系族

【测量】全部文本一次性喂 `embedding_store.compute_embeddings_batch`（真后端下单次 venv 子进程/
daemon 调用编完，逐对本地算 cosine）。daemon 生命周期：`main()` 里显式 ensure_daemon() +
finally shutdown_daemon()——绝不残留常驻进程（历史曾因遗漏 shutdown 积出 24 个僵尸进程）。

用法：
  py core/ml/calibration/semantic_threshold_calibrator.py \\
      --books 主神大道,诡秘之主,轮回乐园 --pairs 150 --seed 20260704
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_SCRIPTS = _REPO_ROOT / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from embedding_store import (  # noqa: E402
    compute_embeddings_batch, cosine_similarity, embedding_method, ruoyu_style_dim,
)
import nn_daemon_client  # noqa: E402

# 冻结引用：monkeypatch 只会重绑 `compute_embeddings_batch` 这个公开名字（供 run_calibration
# 兜底/main() 用），不会动到这个私有引用——用它做「是否真后端」的身份判定，不受测试打桩影响。
_REAL_COMPUTE_EMBEDDINGS_BATCH = compute_embeddings_batch

# ── 采样参数（module-level 常量·测试可 monkeypatch 缩小以跑小型合成语料）──────────
MIN_PARA_CJK = 30            # 段落最短 CJK 字数（低于此过滤·避免退化向量）
FAR_PARA_DISTANCE = 10       # pos_same_chapter_far 段落索引最小间距
FAR_CHAPTER_DISTANCE = 50    # probe_same_book_diff_chapter 章号最小间距
SUMMARY_CJK_TARGET = 300     # 章首摘要代理累计目标 CJK 字数
AUC_ACCEPTABLE = 0.70        # Hosmer-Lemeshow 惯例：0.7-0.8=acceptable discrimination
AUC_GOOD = 0.65              # 内容敏感度判定阈（探针任务更难分·适度放宽）

CHAPTER_NUM_RE = re.compile(r"第0*(\d+)章")
TITLE_LINE_RE = re.compile(r"^第[0-9〇一二三四五六七八九十百千两]+[章回节卷]")


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


# ════════════════════════════ 语料加载 ════════════════════════════

def load_book_paragraphs(book_dir: Path) -> dict:
    """book_dir(原文目录) → {章号: [段落, ...]}。实地校验：本仓 原文/*.txt 是「每行一段」
    格式（无空行分隔），故按 \\n 切行，首行若形似章节标题则跳过，段落再按 CJK 字数过滤。"""
    chapters: dict = {}
    for fp in sorted(book_dir.glob("*.txt")):
        m = CHAPTER_NUM_RE.search(fp.stem)
        if not m:
            continue
        ch_num = int(m.group(1))
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        paras = []
        for i, line in enumerate(text.split("\n")):
            line = line.strip().lstrip("　").strip()
            if i == 0 and TITLE_LINE_RE.match(line):
                continue
            if _cjk_count(line) >= MIN_PARA_CJK:
                paras.append(line)
        if paras:
            chapters[ch_num] = paras
    return chapters


def load_corpus(styles_root: Path, book_names) -> dict:
    """{书名: {章号: [段落,...]}}。书目缺 原文 目录 → 跳过并 stderr 提示。"""
    corpus: dict = {}
    for name in book_names:
        book_dir = styles_root / name / "原文"
        if not book_dir.is_dir():
            print(f"[calibrator] 跳过《{name}》：无原文目录 {book_dir}", file=sys.stderr)
            continue
        chapters = load_book_paragraphs(book_dir)
        if chapters:
            corpus[name] = chapters
        else:
            print(f"[calibrator] 跳过《{name}》：原文目录无可用段落", file=sys.stderr)
    return corpus


# ════════════════════════════ 样本对构造（确定性 rng） ════════════════════════════

def _sample(pool: list, rng: random.Random, n: int) -> list:
    return pool if len(pool) <= n else rng.sample(pool, n)


def _build_pos_adjacent(corpus: dict, rng: random.Random, n: int) -> list:
    pool = []
    for book in sorted(corpus):
        for ch in sorted(corpus[book]):
            paras = corpus[book][ch]
            if len(paras) < 2:
                continue
            i = rng.randrange(0, len(paras) - 1)
            pool.append((paras[i], paras[i + 1]))
    return _sample(pool, rng, n)


def _build_pos_same_chapter_far(corpus: dict, rng: random.Random, n: int) -> list:
    pool = []
    for book in sorted(corpus):
        for ch in sorted(corpus[book]):
            paras = corpus[book][ch]
            if len(paras) < FAR_PARA_DISTANCE + 1:
                continue
            i = rng.randrange(0, len(paras) - FAR_PARA_DISTANCE)
            j = rng.randrange(i + FAR_PARA_DISTANCE, len(paras))
            pool.append((paras[i], paras[j]))
    return _sample(pool, rng, n)


def _build_probe_same_book_diff_chapter(corpus: dict, rng: random.Random, n: int) -> list:
    pool = []
    for book in sorted(corpus):
        ch_nums = sorted(corpus[book])
        for ch_a in ch_nums:
            candidates = [c for c in ch_nums if abs(c - ch_a) >= FAR_CHAPTER_DISTANCE]
            if not candidates:
                continue
            ch_b = rng.choice(candidates)
            pa = rng.choice(corpus[book][ch_a])
            pb = rng.choice(corpus[book][ch_b])
            pool.append((pa, pb))
    return _sample(pool, rng, n)


def _build_neg_cross_book(corpus: dict, rng: random.Random, n: int) -> list:
    books = sorted(corpus)
    if len(books) < 2:
        return []
    pool = []
    for i, book_a in enumerate(books):
        for book_b in books[i + 1:]:
            chs_a, chs_b = sorted(corpus[book_a]), sorted(corpus[book_b])
            for ch in chs_a:
                ch_b = rng.choice(chs_b)
                pa = rng.choice(corpus[book_a][ch])
                pb = rng.choice(corpus[book_b][ch_b])
                pool.append((pa, pb))
    return _sample(pool, rng, n)


def _chapter_summary(paras: list, target_cjk: int = SUMMARY_CJK_TARGET):
    """章首段落累加到 >=target_cjk 当摘要代理。返回 (summary_text, 已消耗段落数 k)。"""
    acc, cjk_total = [], 0
    k = len(paras)
    for idx, p in enumerate(paras):
        acc.append(p)
        cjk_total += _cjk_count(p)
        if cjk_total >= target_cjk:
            k = idx + 1
            break
    return "\n".join(acc), k


def _build_summary_vs_body(corpus: dict, rng: random.Random, n: int):
    pos_pool, neg_pool = [], []
    books = sorted(corpus)
    for book in books:
        for ch in sorted(corpus[book]):
            paras = corpus[book][ch]
            if len(paras) < 2:
                continue
            summary, k = _chapter_summary(paras)
            if k < len(paras):
                pos_pool.append((summary, rng.choice(paras[k:])))
            others = [b for b in books if b != book]
            if others:
                ob = rng.choice(others)
                ob_ch = rng.choice(sorted(corpus[ob]))
                neg_pool.append((summary, rng.choice(corpus[ob][ob_ch])))
    return _sample(pos_pool, rng, n), _sample(neg_pool, rng, n)


def build_all_pairs(corpus: dict, rng: random.Random, n: int) -> dict:
    """固定顺序依次消费 rng——同 seed 必产同对集（决定性）。"""
    summary_pos, summary_neg = _build_summary_vs_body(corpus, rng, n)
    return {
        "pos_adjacent": _build_pos_adjacent(corpus, rng, n),
        "pos_same_chapter_far": _build_pos_same_chapter_far(corpus, rng, n),
        "probe_same_book_diff_chapter": _build_probe_same_book_diff_chapter(corpus, rng, n),
        "neg_cross_book": _build_neg_cross_book(corpus, rng, n),
        "summary_vs_body_pos": summary_pos,
        "summary_vs_body_neg": summary_neg,
    }


# ════════════════════════════ 统计（纯 stdlib） ════════════════════════════

def percentile(xs: list, p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    if n == 1:
        return s[0]
    pos = p * (n - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def distribution_stats(xs: list) -> dict:
    return {
        "n": len(xs),
        "mean": round(sum(xs) / len(xs), 4) if xs else 0.0,
        "p5": round(percentile(xs, 0.05), 4), "p25": round(percentile(xs, 0.25), 4),
        "p50": round(percentile(xs, 0.50), 4), "p75": round(percentile(xs, 0.75), 4),
        "p95": round(percentile(xs, 0.95), 4),
    }


def roc_auc(pos: list, neg: list) -> float:
    """rank-based AUC(Mann-Whitney)·pos 应更高·含 tie 平均秩
    （同 core/ml/style_embed/eval.py::roc_auc 算法·此处独立复现供本脚本自包含）。"""
    n_pos, n_neg = len(pos), len(neg)
    if n_pos == 0 or n_neg == 0:
        return 0.5
    scores = pos + neg
    labels = [1] * n_pos + [0] * n_neg
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    pos_rank_sum = sum(r for r, l in zip(ranks, labels) if l == 1)
    return (pos_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def youden_threshold(pos: list, neg: list) -> dict:
    """ROC 上 TPR-FPR 最大点（Youden's J）。候选阈值=pos∪neg 全部取值（降序扫）。"""
    if not pos or not neg:
        return {"threshold": None, "tpr": 0.0, "fpr": 0.0, "j": 0.0}
    n_pos, n_neg = len(pos), len(neg)
    candidates = sorted(set(pos) | set(neg), reverse=True)
    best = {"threshold": candidates[0], "tpr": 0.0, "fpr": 0.0, "j": -1.0}
    for t in candidates:
        tpr = sum(1 for s in pos if s >= t) / n_pos
        fpr = sum(1 for s in neg if s >= t) / n_neg
        j = tpr - fpr
        if j > best["j"]:
            best = {"threshold": round(t, 4), "tpr": round(tpr, 4), "fpr": round(fpr, 4), "j": round(j, 4)}
    return best


# ════════════════════════════ 打分 + 报告 ════════════════════════════

def score_all_pairs(pair_groups: dict, embed_batch_fn) -> dict:
    """全部文本拉平成一个 list 一次性喂 embed_batch_fn（内部自带 dedup+缓存），逐对本地 cosine。"""
    flat_texts, layout = [], {}
    for cls, pairs in pair_groups.items():
        idxs = []
        for a, b in pairs:
            ia = len(flat_texts); flat_texts.append(a)
            ib = len(flat_texts); flat_texts.append(b)
            idxs.append((ia, ib))
        layout[cls] = idxs
    vecs = embed_batch_fn(flat_texts) if flat_texts else []
    return {cls: [cosine_similarity(vecs[ia], vecs[ib]) for ia, ib in idxs]
            for cls, idxs in layout.items()}


def build_report(pair_groups: dict, scores: dict, meta: dict) -> dict:
    dist = {cls: distribution_stats(scores[cls]) for cls in pair_groups}
    auc_adj_vs_neg = roc_auc(scores["pos_adjacent"], scores["neg_cross_book"])
    auc_adj_vs_probe = roc_auc(scores["pos_adjacent"], scores["probe_same_book_diff_chapter"])
    auc_probe_vs_neg = roc_auc(scores["probe_same_book_diff_chapter"], scores["neg_cross_book"])
    auc_echo = roc_auc(scores["summary_vs_body_pos"], scores["summary_vs_body_neg"])

    families = {
        "content_relatedness": {
            "desc": "相邻强相关内容 vs 完全无关跨书内容——基础可分性上限",
            "pos": "pos_adjacent", "neg": "neg_cross_book", "auc": round(auc_adj_vs_neg, 4)},
        "content_vs_style_confound": {
            "desc": "相邻强相关内容 vs 同书同风格但内容无关的远章节——内容敏感度（核心疑问）",
            "pos": "pos_adjacent", "neg": "probe_same_book_diff_chapter", "auc": round(auc_adj_vs_probe, 4)},
        "style_signal_strength": {
            "desc": "同书跨章（同风格·内容不同）vs 跨书（异风格）——风格信号是否存在",
            "pos": "probe_same_book_diff_chapter", "neg": "neg_cross_book", "auc": round(auc_probe_vs_neg, 4)},
        "content_echo": {
            "desc": ("章首摘要代理 vs 正文——对应 SEMANTIC_RESONANCE_SIM_THRESHOLD"
                     "(choice_consequence_ledger.py) / SEMANTIC_ANCHOR_SIM_THRESHOLD"
                     "(deus_ex_solution_audit.py) / SEMANTIC_THREAD_MATCH_THRESHOLD"
                     "(subplot_progress_update.py) / MILESTONE_SEMANTIC_TOUCH_FLOOR"
                     "(volume_arc_drift_scanner.py) / topic_drift_scanner.py 的 scope_summary 校验"),
            "pos": "summary_vs_body_pos", "neg": "summary_vs_body_neg", "auc": round(auc_echo, 4)},
    }
    for fam in families.values():
        pos_s, neg_s = scores[fam["pos"]], scores[fam["neg"]]
        fam["usable"] = fam["auc"] >= AUC_GOOD
        fam["neg_p95"] = round(percentile(neg_s, 0.95), 4)
        fam["youden"] = youden_threshold(pos_s, neg_s)

    content_sensitive = auc_adj_vs_probe >= AUC_GOOD
    content_separable = auc_adj_vs_neg >= AUC_ACCEPTABLE
    if content_separable and content_sensitive:
        label, reason = "A", (
            f"内容可分：AUC(adjacent vs neg_cross_book)={auc_adj_vs_neg:.3f}≥{AUC_ACCEPTABLE} 且 "
            f"AUC(adjacent vs probe)={auc_adj_vs_probe:.3f}≥{AUC_GOOD}——模型把「内容相关」和"
            "「同作者但内容无关」分得开，非单纯风格混淆。")
    elif content_separable or auc_echo >= AUC_GOOD:
        label, reason = "B", (
            f"部分可分：AUC(adjacent vs neg)={auc_adj_vs_neg:.3f}、"
            f"AUC(adjacent vs probe)={auc_adj_vs_probe:.3f}（内容敏感度"
            f"{'达标' if content_sensitive else '不足'}）、AUC(content_echo)={auc_echo:.3f}——"
            "部分族可用，部分族的高分可能是风格混淆而非真内容相关，需按族取舍。")
    else:
        label, reason = "C", (
            f"基本不可分：AUC(adjacent vs neg)={auc_adj_vs_neg:.3f} 接近随机(0.5)——"
            "ruoyu_style 是作者判别模型非内容语义模型，用它守内容类阈值是自欺，"
            "需要专门的内容嵌入后端（如 EMBED_BACKEND=local/mstyle 或 API）。")

    return {
        "meta": meta,
        "distributions": dist,
        "auc": {
            "pos_adjacent_vs_neg_cross_book": round(auc_adj_vs_neg, 4),
            "pos_adjacent_vs_probe_same_book_diff_chapter": round(auc_adj_vs_probe, 4),
            "probe_vs_neg_cross_book": round(auc_probe_vs_neg, 4),
            "summary_vs_body_pos_vs_neg": round(auc_echo, 4),
        },
        "probe_minus_neg_mean": round(dist["probe_same_book_diff_chapter"]["mean"] - dist["neg_cross_book"]["mean"], 4),
        "relation_families": families,
        "verdict": {"label": label, "reason": reason},
    }


def render_markdown(report: dict) -> str:
    m = report["meta"]
    lines = [
        "# ruoyu_style 语义可分性校准报告", "",
        f"- 生成时间：{m['generated_at']}",
        f"- 书目：{', '.join(m['books'])}" + (f"（缺失：{', '.join(m['books_missing'])}）" if m.get("books_missing") else ""),
        f"- seed={m['seed']} · 每类目标对数={m['pairs_per_class']}",
        f"- embedding 后端：{m.get('embed_method', '?')}（dim={m.get('embed_dim', '?')}）",
        "", "## 五类样本分布", "",
        "| 类别 | n | p5 | p25 | p50 | p75 | p95 | mean |", "|---|---|---|---|---|---|---|---|",
    ]
    for cls, s in report["distributions"].items():
        lines.append(f"| {cls} | {s['n']} | {s['p5']} | {s['p25']} | {s['p50']} | {s['p75']} | {s['p95']} | {s['mean']} |")
    lines += ["", "## 关键 AUC", ""]
    for k, v in report["auc"].items():
        lines.append(f"- {k} = **{v}**")
    lines += ["", f"probe 与 neg 均值差 = {report['probe_minus_neg_mean']}（风格敏感度：越大说明模型对「同作者」信号越敏感）", ""]
    lines += ["## 裁决", "", f"**{report['verdict']['label']}** —— {report['verdict']['reason']}", ""]
    lines += ["## 各 relation_family 建议 operating point", ""]
    for name, fam in report["relation_families"].items():
        lines.append(f"### {name}（AUC={fam['auc']} · {'可用' if fam['usable'] else '不建议用'}）")
        lines.append(fam["desc"])
        y = fam["youden"]
        lines.append(f"- neg p95（低误报候选）= {fam['neg_p95']}")
        lines.append(f"- Youden 最优点 = {y['threshold']}（TPR={y['tpr']} FPR={y['fpr']} J={y['j']}）")
        lines.append("")
    return "\n".join(lines)


# ════════════════════════════ 内容嵌入后端（🔴 2026-07-04 W6-C·bge-small-zh） ════════════════════════════

def _content_embed_batch(texts: list) -> list:
    """内容语义嵌入批量编码：daemon task=content_embed 优先 → venv subprocess 调
    content_infer.py --batch 兜底 → 都不可用则 **响亮失败**（校准必须真向量，绝不 hash 假装）。"""
    texts = [str(t) for t in texts]
    try:
        res = nn_daemon_client.infer("content_embed", texts, timeout=600)
        if res is not None and len(res) == len(texts):
            embs = [r.get("embedding") if isinstance(r, dict) else None for r in res]
            if all(e for e in embs):
                return embs
    except Exception:
        pass
    # subprocess 兜底（daemon 未启用/不可达）
    import subprocess, tempfile
    venv_py = _REPO_ROOT / "core" / "ml" / ".venv" / "Scripts" / "python.exe"
    infer = _REPO_ROOT / "core" / "ml" / "content_embed" / "content_infer.py"
    if not venv_py.exists() or not infer.exists():
        raise RuntimeError("content_embed 后端不可用（venv/content_infer 缺）——校准需要真向量，中止")
    with tempfile.TemporaryDirectory() as td:
        inp, outp = Path(td) / "in.jsonl", Path(td) / "out.jsonl"
        inp.write_text("\n".join(json.dumps({"text": t}, ensure_ascii=False) for t in texts) + "\n",
                       encoding="utf-8")
        proc = subprocess.run([str(venv_py), str(infer), "--batch", str(inp), "--out", str(outp)],
                              capture_output=True, timeout=1800,
                              creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        if proc.returncode != 0 or not outp.exists():
            raise RuntimeError(f"content_infer subprocess 失败 rc={proc.returncode}: "
                               f"{(proc.stderr or b'').decode('utf-8', 'replace')[-200:]}")
        embs = []
        for line in outp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                embs.append(json.loads(line).get("embedding"))
    if len(embs) != len(texts) or any(not e for e in embs):
        raise RuntimeError(f"content_infer 输出不齐（got={len(embs)} want={len(texts)}）——中止")
    return embs


# ════════════════════════════ 编排 ════════════════════════════

def run_calibration(books: list, n_pairs: int, seed: int, styles_root: Path,
                    embed_batch_fn=None, backend: str = "ruoyu_style") -> dict:
    """可测试入口：embed_batch_fn=None → 真后端(embedding_store.compute_embeddings_batch)；
    传入 mock → 零 daemon/venv 接触（供测试用）。

    🔴 「是否真后端」判定必须对齐 `_REAL_COMPUTE_EMBEDDINGS_BATCH`（导入时冻结的引用），
    不能只看调用方是否传了 embed_batch_fn 参数——main() 不传参数走默认值，但若测试把模块级
    公开名字 `compute_embeddings_batch` monkeypatch 成假函数，默认值这条路径解析到的就是假
    函数而非真后端；用「调用方传没传参」当判据会在这种场景下误判成真后端并触发真 embedding_method()
    探测（虽不崩·但 meta 会错报真后端名字）。"""
    if embed_batch_fn is not None:
        resolved_fn = embed_batch_fn
    elif backend == "content":
        resolved_fn = _content_embed_batch   # 🔴 W6-C：bge 内容嵌入（daemon→subprocess·失败响亮中止）
    else:
        resolved_fn = compute_embeddings_batch
    using_real_backend = resolved_fn is _REAL_COMPUTE_EMBEDDINGS_BATCH

    rng = random.Random(seed)
    corpus = load_corpus(styles_root, books)
    missing = [b for b in books if b not in corpus]
    pair_groups = build_all_pairs(corpus, rng, n_pairs)
    scores = score_all_pairs(pair_groups, resolved_fn)

    if resolved_fn is _content_embed_batch:
        method, dim = "content:bge-small-zh-v1.5", 512
    elif using_real_backend:
        try:
            method, dim = embedding_method(), ruoyu_style_dim("author")
        except Exception:
            method, dim = "unknown", None
    else:
        method, dim = "injected_mock", None

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "books": books, "books_missing": missing,
        "seed": seed, "pairs_per_class": n_pairs,
        "embed_method": method, "embed_dim": dim,
        "chapters_loaded": {b: len(corpus[b]) for b in corpus},
    }
    return build_report(pair_groups, scores, meta)


def main() -> int:
    ap = argparse.ArgumentParser(description="语义阈值校准 harness（Wave-6·ruoyu_style/content 双后端）")
    ap.add_argument("--books", required=True, help="逗号分隔书名（对应 workspace/styles/<书名>/原文）")
    ap.add_argument("--pairs", type=int, default=150, help="每类目标样本对数（默认 150）")
    ap.add_argument("--seed", type=int, default=20260704)
    ap.add_argument("--styles-root", default=None, help="默认 workspace/styles")
    ap.add_argument("--output-dir", default=None, help="默认 core/ml/calibration/reports")
    ap.add_argument("--backend", default="ruoyu_style", choices=("ruoyu_style", "content"),
                    help="ruoyu_style=风格声纹（经 embedding_store）；content=bge 内容嵌入（W6-C）")
    args = ap.parse_args()

    if args.backend == "ruoyu_style":
        os.environ.setdefault("EMBED_BACKEND", "ruoyu_style")
    os.environ.setdefault("RUOYU_NN_DAEMON", "1")

    books = [b.strip() for b in args.books.split(",") if b.strip()]
    styles_root = Path(args.styles_root) if args.styles_root else _REPO_ROOT / "workspace" / "styles"
    out_dir = Path(args.output_dir) if args.output_dir else _HERE / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    nn_daemon_client.ensure_daemon()   # 门控/venv 缺失时安全返回 False，不影响后续（回退子进程冷启动）
    try:
        report = run_calibration(books, args.pairs, args.seed, styles_root, backend=args.backend)
    finally:
        nn_daemon_client.shutdown_daemon()   # 绝不残留常驻进程（历史 24 僵尸事故）

    date_str = datetime.now().strftime("%Y%m%d")
    prefix = "content_embed" if args.backend == "content" else "ruoyu_style"
    json_path = out_dir / f"{prefix}_separability_{date_str}.json"
    md_path = out_dir / f"{prefix}_separability_{date_str}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"[calibrator] json → {json_path}")
    print(f"[calibrator] md   → {md_path}")
    print(json.dumps(report["verdict"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
