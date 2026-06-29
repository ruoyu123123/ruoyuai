# 🔴 2026-06-29 NN训练:质量AI腔判别
"""data_prep.py — 构造「人感(human) vs AI腔(ai)」二分类数据集（纯 stdlib·可直接跑）

【数据来源·诚实标注】
  正样本 human（真人写·人感）= workspace/styles/<作者>/原文/第NNNN章.txt
      —— 10 位网文作者原文，磁盘 present，海量（~7800 章）。
  负样本 ai（gemini 生成·AI腔）= 两路：
      (a) workspace/styles/<作者>/复刻测试/**/​*replica*.txt
          —— 蒸馏闭环里 gen-model(gemini) 复刻该作者风格的产物。
          ★ 这是**最理想的硬负样本**：同一生产生成器(gemini)、同题材(网文)、
            刻意模仿目标作者 —— 正是线上要识别的「AI 装人」case。
      (b) workspace/novels/<书>/章节/**/*draft*.txt —— 系统自产正文草稿。

【严格过滤·防数据污染】（实地核查 sediment）
  从负样本里**剔除**：
    · `_原文_*_concat.txt`  —— 其实是真人原文拼接（SFS multi-ref 用），是 human 不是 ai！
    · `*_eval_prompt.txt` / `*_llm_eval*` —— 评分 prompt 元文本，不是正文。
    · 拒答文件（首段含「我无法/抱歉/更改主题/as an AI」）—— gen-model 拒答残片。
    · 过短文件（< --min-file-chars）—— 截断残片。

【方法学纪律】（调研接地·见 README §研究结论 / arXiv:2509.00731 / 2503.00258）
  1. **source-level split**：同一文件的 chunk 不跨 train/val/test（防泄漏作弊）。
  2. **by_author split 模式**：可整体留出 N 位作者当 test —— 测「学的是人感 vs AI腔，
     还是作者身份捷径」。强烈建议至少跑一次 by_author 看泛化。
  3. **长度归一**：chunk 定长切（默认 480 CJK），消除「人样本更长」的长度捷径。
  4. **eval 均衡**：val/test 默认下采样多数类(human)到与 ai 等量 → 指标(AUC/F1/PR)可信。
     train **保留全部**（不丢数据·respects 别抽样）；类不均衡交 train.py 的 focal loss +
     加权采样器在训练时处理，绝不靠丢训练数据来"平衡"。

【用法】
    python data_prep.py                       # 默认全量·random_file 切分
    python data_prep.py --split-mode by_author --holdout-authors 惊悚乐园 剑来
    python data_prep.py --human-cap-per-author 40   # 想要小/快的均衡集时再开
输出：data/{train,val,test}.jsonl + data/meta.json
"""
from __future__ import annotations
import argparse
import json
import random
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import extract_features  # noqa: E402

REPO = Path(__file__).resolve().parents[3]  # D:/Desktop/ruoyuai
DEFAULT_STYLES = REPO / "workspace" / "styles"
DEFAULT_NOVELS = REPO / "workspace" / "novels"
OUT_DIR = Path(__file__).resolve().parent / "data"

# —— 负样本过滤规则 ——
AI_INCLUDE_GLOBS = ["复刻测试/**/*replica*.txt", "复刻测试/**/replica.txt"]
NEG_EXCLUDE_SUBSTR = ["_原文_", "_concat", "_eval_prompt", "_llm_eval", "sfs_multiref"]
REFUSAL_MARKERS = ["我无法", "我不能", "抱歉，我", "无法就此", "更改主题",
                   "as an ai", "i cannot", "i can't", "i'm sorry", "对不起，我"]

TITLE_RE = re.compile(r"^\s*第[0-9一二三四五六七八九十百千零〇]+[章卷回节]")
MD_HEADER_RE = re.compile(r"^\s*#{1,6}\s")
INDENT_RE = re.compile(r"^[　\s]+")
CJK_RE = re.compile(r"[一-鿿]")
PARA_SPLIT = re.compile(r"\n\s*\n")


def cjk_len(s: str) -> int:
    return sum(1 for ch in s if CJK_RE.match(ch))


def _rel_source(f: Path) -> str:
    """相对仓库根的路径（仓外文件回退绝对路径）·正斜杠统一。"""
    try:
        return str(f.resolve().relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(f.resolve()).replace("\\", "/")


def is_refusal(text: str) -> bool:
    head = text[:300].lower()
    return any(m in head for m in REFUSAL_MARKERS)


def clean_text(text: str, drop_titles: bool = True) -> str:
    """去标题行 / markdown 头 / 全角缩进，保留段落空行结构。"""
    out_lines = []
    for line in text.split("\n"):
        if drop_titles and TITLE_RE.match(line):
            continue
        if MD_HEADER_RE.match(line):
            continue
        out_lines.append(INDENT_RE.sub("", line))
    # 折叠多空行为段落分隔
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()


def chunk_text(text: str, target: int, min_chars: int) -> list[str]:
    """按段落累积切成 ~target CJK 的窗口；不足 min_chars 的尾块并入上一块或丢弃。

    🔴 段落单元 = 按**任意换行**切（网文原文每行(　　前缀)即一段·单换行分隔；
    复刻/草稿用空行分隔——`\\n+` 两者通吃）。早期用空行(`\\n\\s*\\n`)切会把整章当一段。
    """
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks, buf, buf_len = [], [], 0
    for p in paras:
        pl = cjk_len(p)
        if buf_len + pl > target and buf_len >= min_chars:
            chunks.append("\n\n".join(buf))
            buf, buf_len = [], 0
        buf.append(p)
        buf_len += pl
        # 单段就超长 → 直接独立成块
        if buf_len >= target:
            chunks.append("\n\n".join(buf))
            buf, buf_len = [], 0
    if buf and buf_len >= min_chars:
        chunks.append("\n\n".join(buf))
    elif buf and chunks:  # 短尾并入上一块
        chunks[-1] = chunks[-1] + "\n\n" + "\n\n".join(buf)
    return [c for c in chunks if cjk_len(c) >= min_chars]


def discover_human(styles_dir: Path) -> dict[str, list[Path]]:
    """{author: [chapter.txt, ...]}（按章号排序）。"""
    out = {}
    for author_dir in sorted(styles_dir.iterdir()):
        if not author_dir.is_dir():
            continue
        raw = author_dir / "原文"
        if not raw.is_dir():
            continue
        files = sorted(raw.glob("*.txt"))
        if files:
            out[author_dir.name] = files
    return out


def discover_ai(styles_dir: Path, novels_dir: Path, extra_dir: Path | None
                ) -> list[tuple[str, Path]]:
    """[(author_or_book, ai_file)]，已过滤拒答/元文本/原文拼接。

    三路负样本：
      · 复刻测试 replica（gemini 模仿作者·硬负样本·但单生成器·见 honest_note）
      · 小说草稿 draft（系统自产正文）
      · --ai-extra-dir（gen_negatives.py 产的多生成器/同题材改写负样本·推荐主力）
    """
    out = []
    for author_dir in sorted(styles_dir.iterdir()):
        if not author_dir.is_dir():
            continue
        seen = set()
        for pat in AI_INCLUDE_GLOBS:
            for f in author_dir.glob(pat):
                if f in seen:
                    continue
                seen.add(f)
                name = str(f).lower()
                if any(x.lower() in name for x in NEG_EXCLUDE_SUBSTR):
                    continue
                out.append((author_dir.name, f))
    # 小说草稿（系统自产 gemini 正文）
    if novels_dir.is_dir():
        for f in novels_dir.glob("**/*draft*.txt"):
            out.append((f.parts[len(novels_dir.parts)] if len(f.parts) > len(novels_dir.parts) else "novel", f))
    # 外部生成负样本（多生成器·gen_negatives.py 产出）
    if extra_dir and extra_dir.is_dir():
        for f in sorted(extra_dir.glob("**/*.txt")):
            # 约定文件名前缀 = 来源作者/题材组（gen_negatives.py 写 <author>__<gen>__NN.txt）
            group = f.stem.split("__")[0] if "__" in f.stem else "synthetic"
            out.append((group, f))
    return out


def read_file(f: Path) -> str | None:
    try:
        return f.read_text(encoding="utf-8")
    except Exception:
        try:
            return f.read_text(encoding="gbk")
        except Exception:
            return None


def build_records(files, label, kind, cap_per_group, chunk_chars, min_chunk,
                  min_file_chars, max_chunks_per_file):
    """files: [(group, path)] → records[]，按文件累积 chunk。"""
    records = []
    per_group = Counter()
    skipped = {"refusal": 0, "tooshort": 0, "unreadable": 0}
    for group, f in files:
        if cap_per_group and per_group[group] >= cap_per_group:
            continue
        raw = read_file(f)
        if raw is None:
            skipped["unreadable"] += 1
            continue
        if is_refusal(raw):
            skipped["refusal"] += 1
            continue
        cleaned = clean_text(raw)
        if cjk_len(cleaned) < min_file_chars:
            skipped["tooshort"] += 1
            continue
        chunks = chunk_text(cleaned, chunk_chars, min_chunk)
        if max_chunks_per_file:
            chunks = chunks[:max_chunks_per_file]
        for i, ch in enumerate(chunks):
            if cap_per_group and per_group[group] >= cap_per_group:
                break
            records.append({
                "text": ch,
                "label": label,          # 0=human, 1=ai
                "kind": kind,            # human_chapter | ai_replica | ai_draft
                "author": group,
                "source": _rel_source(f),
                "chunk_idx": i,
                "cjk_len": cjk_len(ch),
                "feats": extract_features(ch),
            })
            per_group[group] += 1
    return records, skipped


def group_split(records, val_frac, test_frac, rng):
    """按 source 文件分组切分（同文件 chunk 不跨 split）。"""
    by_src = defaultdict(list)
    for r in records:
        by_src[r["source"]].append(r)
    srcs = list(by_src.keys())
    rng.shuffle(srcs)
    n = len(srcs)
    n_test = max(1, int(n * test_frac)) if n > 2 else 0
    n_val = max(1, int(n * val_frac)) if n > 2 else 0
    test_src = set(srcs[:n_test])
    val_src = set(srcs[n_test:n_test + n_val])
    splits = {"train": [], "val": [], "test": []}
    for s, rs in by_src.items():
        key = "test" if s in test_src else "val" if s in val_src else "train"
        splits[key].extend(rs)
    return splits


def author_split(human_recs, ai_recs, holdout, val_frac, rng):
    """整作者留出当 test（泛化测试）：holdout 作者全部进 test，其余 train/val。"""
    holdout = set(holdout)
    train_val = [r for r in human_recs + ai_recs if r["author"] not in holdout]
    test = [r for r in human_recs + ai_recs if r["author"] in holdout]
    # train_val 再按 source 切出 val
    sub = group_split(train_val, val_frac, 0.0, rng)
    return {"train": sub["train"], "val": sub["val"], "test": test}


def balance_eval(split_recs, rng):
    """val/test 下采样多数类到与少数类等量（指标可信）。返回均衡后的 list。"""
    by_label = defaultdict(list)
    for r in split_recs:
        by_label[r["label"]].append(r)
    if not by_label.get(0) or not by_label.get(1):
        return split_recs  # 缺类则不动
    k = min(len(by_label[0]), len(by_label[1]))
    out = rng.sample(by_label[0], k) + rng.sample(by_label[1], k)
    rng.shuffle(out)
    return out


def write_jsonl(path: Path, records):
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="构造 human/ai 二分类数据集")
    ap.add_argument("--styles-dir", default=str(DEFAULT_STYLES))
    ap.add_argument("--novels-dir", default=str(DEFAULT_NOVELS))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--chunk-chars", type=int, default=480)
    ap.add_argument("--min-chunk", type=int, default=200)
    ap.add_argument("--min-file-chars", type=int, default=2000,
                    help="负样本最短文件 CJK·过滤截断残片")
    ap.add_argument("--human-cap-per-author", type=int, default=0,
                    help="0=用全部人样本(默认·别抽样)；>0=每作者最多 N 个 chunk(要小/快均衡集时开)")
    ap.add_argument("--max-chunks-per-ai-file", type=int, default=0,
                    help="0=用整文件；>0=每 AI 文件最多取前 N chunk")
    ap.add_argument("--ai-extra-dir", default="",
                    help="额外负样本目录（gen_negatives.py 产的多生成器/同题材改写·推荐主力训练源）")
    ap.add_argument("--replicas-heldout-test", action="store_true",
                    help="把 41 个 gemini 复刻样本整体路由到 test 当『对抗硬集』（研究建议·"
                         "训练负样本则用 --ai-extra-dir）·防止检测器只学单生成器指纹")
    ap.add_argument("--split-mode", choices=["random_file", "by_author"], default="random_file")
    ap.add_argument("--holdout-authors", nargs="*", default=["惊悚乐园", "剑来"],
                    help="by_author 模式留出当 test 的作者")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--test-frac", type=float, default=0.1)
    ap.add_argument("--no-balance-eval", action="store_true",
                    help="不均衡 val/test（默认会均衡多数类）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    styles_dir = Path(args.styles_dir)
    novels_dir = Path(args.novels_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # —— 发现 ——
    human_map = discover_human(styles_dir)
    human_files = [(a, f) for a, fs in human_map.items() for f in fs]
    extra_dir = Path(args.ai_extra_dir) if args.ai_extra_dir else None
    ai_files = discover_ai(styles_dir, novels_dir, extra_dir)
    print(f"[发现] 人样本作者 {len(human_map)} 位·章节 {len(human_files)} 个；"
          f"AI 文件 {len(ai_files)} 个", file=sys.stderr)

    # —— 负样本先建（决定规模），再据其量控正样本 ——
    ai_recs, ai_skip = build_records(
        ai_files, label=1, kind="ai", cap_per_group=0,
        chunk_chars=args.chunk_chars, min_chunk=args.min_chunk,
        min_file_chars=args.min_file_chars,
        max_chunks_per_file=args.max_chunks_per_ai_file)
    extra_name = extra_dir.name if extra_dir else None
    for r in ai_recs:
        fname = r["source"].rsplit("/", 1)[-1]
        # gen_negatives.py 写 <author>__<gen>__NN.txt；或落在 --ai-extra-dir 目录下
        if "__" in fname or (extra_name and f"/{extra_name}/" in r["source"]):
            r["kind"] = "ai_generated"
        elif "draft" in r["source"]:
            r["kind"] = "ai_draft"
        else:
            r["kind"] = "ai_replica"
    print(f"[AI] chunk {len(ai_recs)} 个（跳过：拒答 {ai_skip['refusal']}/"
          f"过短 {ai_skip['tooshort']}/不可读 {ai_skip['unreadable']}）", file=sys.stderr)

    human_recs, h_skip = build_records(
        human_files, label=0, kind="human_chapter",
        cap_per_group=args.human_cap_per_author,
        chunk_chars=args.chunk_chars, min_chunk=args.min_chunk,
        min_file_chars=200, max_chunks_per_file=0)
    print(f"[HUMAN] chunk {len(human_recs)} 个", file=sys.stderr)

    # —— 切分 ——
    if args.split_mode == "by_author":
        splits = author_split(human_recs, ai_recs, args.holdout_authors,
                              args.val_frac, rng)
    else:
        all_recs = human_recs + ai_recs
        splits = group_split(all_recs, args.val_frac, args.test_frac, rng)

    # —— 复刻样本整体路由到 test 当对抗硬集（研究建议·防单生成器指纹污染训练）——
    if args.replicas_heldout_test:
        for name in ("train", "val"):
            keep, moved = [], []
            for r in splits[name]:
                (moved if r["kind"] == "ai_replica" else keep).append(r)
            splits[name] = keep
            splits["test"].extend(moved)

    # —— eval 均衡 ——
    if not args.no_balance_eval:
        splits["val"] = balance_eval(splits["val"], rng)
        splits["test"] = balance_eval(splits["test"], rng)

    # —— 落盘 ——
    for name in ("train", "val", "test"):
        rng.shuffle(splits[name])
        write_jsonl(out_dir / f"{name}.jsonl", splits[name])

    def label_counts(rs):
        c = Counter(r["label"] for r in rs)
        return {"human": c.get(0, 0), "ai": c.get(1, 0)}

    meta = {
        "_marker": "🔴 2026-06-29 NN训练:质量AI腔判别",
        "config": vars(args),
        "splits": {n: label_counts(splits[n]) for n in ("train", "val", "test")},
        "totals": {"human_chunks": len(human_recs), "ai_chunks": len(ai_recs)},
        "imbalance_ratio_human_to_ai": round(
            len(human_recs) / max(1, len(ai_recs)), 2),
        "human_authors": {a: len(fs) for a, fs in human_map.items()},
        "ai_sources_by_group": dict(Counter(r["author"] for r in ai_recs)),
        "ai_skipped": ai_skip,
        "feature_count": len(extract_features("测试")),
        "honest_note": (
            "AI 负样本仅来自 gemini（线上同一生成器）且都在模仿这 10 位作者——"
            "检测器可能学到的是『gemini 腔』而非泛化『AI 腔』，且存在作者身份捷径风险。"
            "缓解：①train.py 用 focal loss+加权采样不丢数据；②跑 --split-mode by_author "
            "看跨作者泛化；③后续可用 gen_negatives.py 引入多生成器/同题材改写负样本。"),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 数据集构造完成 ===", file=sys.stderr)
    print(json.dumps(meta["splits"], ensure_ascii=False), file=sys.stderr)
    print(f"不均衡比 human:ai = {meta['imbalance_ratio_human_to_ai']}:1", file=sys.stderr)
    print(f"输出 → {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
