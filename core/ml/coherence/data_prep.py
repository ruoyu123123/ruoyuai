# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN连贯性评分集成
"""data_prep.py — 自监督连贯性数据准备（CoUDA 双通道扰动 · NAACL 2024 · arXiv:2404.00681）。

核心思路：自监督构造 — 原文连续段落做正例（连贯），打乱段序/跨文替换段做负例（不连贯），
无需人工标注。只用 stdlib（argparse/json/random/re/hashlib/pathlib），torch 没装也能先跑数据。

正例：原文连续 5-8 个段落（保留天然接续）。label=1（连贯）。
负例（CoUDA 双通道）：
  · 全局扰动 global_shuffle：打乱段落顺序（破坏全局逻辑链）。label=0。
  · 局部扰动 local_replace：随机把 1 段换成**其他文档**的段（破坏局部衔接）。label=0。

标签约定（须与 train.py / coherence_infer 一致）：label=1 连贯 / label=0 不连贯；
打分头 softmax 列 1 = P(连贯)。augment_type ∈ {original, global_shuffle, local_replace, external}。

🔴 防泄漏（与 quality_clf / emotion_vad 同源·source-level split）：
  先按**文档**切 train/val，同一文档的窗口及其派生负例**绝不跨 split**；local_replace 的替换段
  只从**同 split** 的其他文档取。否则「正例在 train、它的打乱负例在 val」会让 val 指标虚高。

外部金标准（可选·--external-dir）：CEDCC（EMNLP2023·github.com/cubenlp/CEDCC_corpus·500 篇作文·
  连贯 1-3 档）/ NLPCC 2024 Shared Task 4。本脚本把 3 档映射为二分类（高→1 / 低→0 / 中档丢），
  .json/.jsonl 带标签的直接并入，.txt 作文当连贯文档源走自监督管线。有就用·没有不报错。

用法：
  python data_prep.py --input-dir workspace/styles --output-dir data
  python data_prep.py --input-dir drafts --output-dir data --min-paras 5 --max-paras 8 --target 25000
  python data_prep.py --input-list files.txt --external-dir D:/CEDCC --output-dir data
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

# 🔴 Windows 控制台默认 GBK，print 中文/emoji/CJK 路径会 UnicodeEncodeError → 强制 UTF-8。
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[3]  # D:/Desktop/ruoyuai

LABEL_COHERENT = 1
LABEL_INCOHERENT = 0

CJK_RE = re.compile(r"[一-鿿]")
TITLE_RE = re.compile(r"^\s*第[0-9一二三四五六七八九十百千零〇]+[章卷回节]")
MD_HEADER_RE = re.compile(r"^\s*#{1,6}\s")
INDENT_RE = re.compile(r"^[　\s]+")

# 外部金标准里候选字段名（best-effort 适配 CEDCC/NLPCC 多种 schema）
EXT_TEXT_KEYS = ["text", "essay", "content", "document", "passage", "article", "正文", "作文"]
EXT_PARTS_KEYS = ["sentences", "paragraphs", "paras", "句子", "段落"]
EXT_LABEL_KEYS = ["coherence", "coherence_grade", "grade", "label", "score",
                  "coherence_score", "rating", "连贯性", "连贯"]


def cjk_len(s: str) -> int:
    return sum(1 for ch in s if CJK_RE.match(ch))


def _rel_id(f: Path) -> str:
    """文档唯一 id = 相对仓库根的正斜杠路径（仓外回退绝对路径）。"""
    try:
        return str(f.resolve().relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(f.resolve()).replace("\\", "/")


def _read_text(f: Path) -> str | None:
    for enc in ("utf-8", "gbk"):
        try:
            return f.read_text(encoding=enc)
        except (OSError, UnicodeDecodeError):
            continue
    return None


def _read_paragraphs(file_path: Path, min_para_cjk: int) -> list[str]:
    """文件 → 段落列表。每非空行视作一段（网文原文每行一段·草稿空行分隔均适用）；
    去标题行 / markdown 头 / 全角缩进；丢弃 CJK 过短的碎段。"""
    text = _read_text(file_path)
    if text is None:
        return []
    paras: list[str] = []
    for line in text.split("\n"):
        line = INDENT_RE.sub("", line).strip()
        if not line or TITLE_RE.match(line) or MD_HEADER_RE.match(line):
            continue
        if cjk_len(line) < min_para_cjk:
            continue
        paras.append(line)
    return paras


def _collect_documents(input_dir: str, input_list: str, min_para_cjk: int,
                       min_paras: int) -> list[tuple[str, list[str]]]:
    """递归收集 .txt → [(doc_id, paras)]。doc_id 保留文档身份（防跨文替换撞自身 + 防泄漏切分）。"""
    files: list[Path] = []
    if input_list:
        lp = Path(input_list)
        if lp.is_file():
            for line in (_read_text(lp) or "").splitlines():
                p = line.strip()
                if not p:
                    continue
                fp = Path(p)
                fp = fp if fp.is_absolute() else (REPO / fp)
                if fp.is_file():
                    files.append(fp)
        else:
            print(f"[WARN] --input-list 不存在: {input_list}", file=sys.stderr)
    if input_dir:
        root = Path(input_dir)
        if root.is_dir():
            files.extend(sorted(root.rglob("*.txt")))
        else:
            print(f"[WARN] --input-dir 不存在: {input_dir}", file=sys.stderr)

    docs: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for fpath in files:
        key = str(fpath.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        paras = _read_paragraphs(fpath, min_para_cjk)
        if len(paras) >= min_paras:
            docs.append((_rel_id(fpath), paras))
    return docs


def _extract_windows(paras: list[str], min_p: int, max_p: int, stride: int,
                     rng: random.Random) -> list[list[str]]:
    """滑窗提取连续段落窗口。stride<=0 → 步长 max(1, min_p//2)（密窗·补样本量·同 split 内不算泄漏）。"""
    windows: list[list[str]] = []
    n = len(paras)
    if n < min_p:
        return windows
    step = stride if stride > 0 else max(1, min_p // 2)
    for start in range(0, n - min_p + 1, step):
        wlen = rng.randint(min_p, min(max_p, n - start))
        windows.append(paras[start:start + wlen])
    return windows


def _global_shuffle(window: list[str], rng: random.Random) -> list[str] | None:
    """CoUDA 全局扰动：打乱段序（破坏全局连贯）。保证 != 原序；全同段落 → None。"""
    if len(window) < 2:
        return None
    shuffled = list(window)
    for _ in range(8):
        rng.shuffle(shuffled)
        if shuffled != window:
            return shuffled
    rev = list(reversed(window))
    return rev if rev != window else None


def _local_replace(window: list[str], pool: list[tuple[str, str]], doc_id: str,
                   min_para_cjk: int, rng: random.Random) -> list[str] | None:
    """CoUDA 局部扰动：随机把 1 段换成**其他文档**的段（破坏局部衔接）。

    pool = 同 split 的 [(doc_id, para)]。替换段须不同文档、够长、不等于原段；随机采样 40 次。
    """
    if not pool or len(window) < 2:
        return None
    new = list(window)
    idx = rng.randrange(len(new))
    for _ in range(40):
        cand_doc, cand_para = rng.choice(pool)
        if cand_doc != doc_id and cjk_len(cand_para) >= min_para_cjk and cand_para != new[idx]:
            new[idx] = cand_para
            return new
    return None


def _record(paras: list[str], label: int, augment_type: str) -> dict:
    # 单 \n 连接 = 与 coherence_scanner._split_paragraphs（按 \n 切段）同口径·防 train/infer skew
    return {"text": "\n".join(paras), "label": label, "augment_type": augment_type}


def _generate_for_docs(docs: list[tuple[str, list[str]]], min_paras: int, max_paras: int,
                       stride: int, min_para_cjk: int, channels: list[str],
                       rng: random.Random) -> list[dict]:
    """对一组文档生成 正例 + 双通道负例。pool 仅来自这组文档（split-local·防泄漏）。"""
    pool: list[tuple[str, str]] = [(did, p) for did, paras in docs for p in paras]
    records: list[dict] = []
    seen: set[str] = set()

    def _add(paras: list[str], label: int, atype: str) -> None:
        text = "\n".join(paras)
        h = hashlib.md5(text.encode("utf-8")).hexdigest()
        if h in seen:
            return
        seen.add(h)
        records.append({"text": text, "label": label, "augment_type": atype})

    for doc_id, paras in docs:
        for w in _extract_windows(paras, min_paras, max_paras, stride, rng):
            _add(w, LABEL_COHERENT, "original")
            if "global" in channels:
                sw = _global_shuffle(w, rng)
                if sw is not None:
                    _add(sw, LABEL_INCOHERENT, "global_shuffle")
            if "local" in channels:
                lr = _local_replace(w, pool, doc_id, min_para_cjk, rng)
                if lr is not None:
                    _add(lr, LABEL_INCOHERENT, "local_replace")
    return records


# --------------------------------------------------------------------------- 外部金标准
def _coerce_label(val, field: str, coherent_min: float, incoherent_max: float) -> int | None:
    """外部连贯标注 → 二分类。**按字段名消歧**（同一数字 1 在两种 schema 里语义相反）：
      · field=="label" → 已二分类（NLPCC 等）：1 连贯 / 0 不连贯，直接用。
      · 其余（coherence/grade/score/rating…）→ 连贯分档（CEDCC 1-3）：>=阈连贯 / <=阈不连贯 / 中档丢。
    """
    if field == "label":
        try:
            iv = int(float(val))
        except (TypeError, ValueError):
            iv = None
        if iv in (0, 1):
            return iv
    try:
        x = float(val)
    except (TypeError, ValueError):
        s = str(val).strip().lower()
        if s in ("coherent", "high", "good", "连贯", "高"):
            return LABEL_COHERENT
        if s in ("incoherent", "low", "bad", "不连贯", "低"):
            return LABEL_INCOHERENT
        return None
    if x >= coherent_min:
        return LABEL_COHERENT
    if x <= incoherent_max:
        return LABEL_INCOHERENT
    return None


def _extract_ext_text(obj: dict) -> str | None:
    for k in EXT_TEXT_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in EXT_PARTS_KEYS:
        v = obj.get(k)
        if isinstance(v, list) and v:
            joined = "\n".join(str(x).strip() for x in v if str(x).strip())
            if joined:
                return joined
    return None


def _iter_ext_objects(path: Path):
    """.jsonl 逐行 / .json（list 或 {data|examples|essays|samples|items: [...]}) → dict。"""
    raw = _read_text(path)
    if raw is None:
        return
    if path.suffix.lower() == ".jsonl":
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return
    if isinstance(data, list):
        for o in data:
            if isinstance(o, dict):
                yield o
    elif isinstance(data, dict):
        for key in ("data", "examples", "essays", "samples", "items"):
            if isinstance(data.get(key), list):
                for o in data[key]:
                    if isinstance(o, dict):
                        yield o
                return
        yield data


def load_external_dataset(dataset_dir: str, min_para_cjk: int, coherent_min: float,
                          incoherent_max: float) -> tuple[list[dict], list[tuple[str, list[str]]]]:
    """扫外部目录：(带标签样本[], 额外 .txt 文档[(doc_id,paras)])。无 → 空·不报错。"""
    root = Path(dataset_dir)
    if not root.is_dir():
        print(f"[NOTE] --external-dir 不存在·跳过: {dataset_dir}", file=sys.stderr)
        return [], []
    labeled: list[dict] = []
    extra_docs: list[tuple[str, list[str]]] = []
    fields = Counter()
    dropped_mid = 0
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.name.lower() in ("readme.md", "license"):
            continue
        suffix = f.suffix.lower()
        if suffix in (".json", ".jsonl"):
            for obj in _iter_ext_objects(f):
                text = _extract_ext_text(obj)
                if not text or cjk_len(text) < min_para_cjk:
                    continue
                label_val, used = None, None
                for k in EXT_LABEL_KEYS:
                    if k in obj:
                        label_val, used = obj[k], k
                        break
                if used is None:
                    continue
                label = _coerce_label(label_val, used, coherent_min, incoherent_max)
                if label is None:
                    dropped_mid += 1
                    continue
                fields[used] += 1
                labeled.append({"text": text, "label": label, "augment_type": "external"})
        elif suffix == ".txt":
            paras = _read_paragraphs(f, min_para_cjk)
            if len(paras) >= 3:
                extra_docs.append((_rel_id(f), paras))
    if labeled or extra_docs:
        print(f"[INFO] 外部：标签样本 {len(labeled)}（字段 {dict(fields)}·丢中档 {dropped_mid}）"
              f" + .txt 文档 {len(extra_docs)}", file=sys.stderr)
    return labeled, extra_docs


# --------------------------------------------------------------------------- 切分 / 平衡
def _balance_and_cap(samples: list[dict], cap: int, rng: random.Random) -> list[dict]:
    """负例下采样到 <=1.2x 正例；总量超 cap 再均匀下采样。"""
    pos = [s for s in samples if s["label"] == LABEL_COHERENT]
    neg = [s for s in samples if s["label"] == LABEL_INCOHERENT]
    if pos and len(neg) > len(pos) * 1.2:
        rng.shuffle(neg)
        neg = neg[:int(len(pos) * 1.2)]
    out = pos + neg
    rng.shuffle(out)
    if cap and len(out) > cap:
        out = out[:cap]
    return out


def _dump_jsonl(path: Path, samples: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


def _aug_counts(samples: list[dict]) -> dict:
    c = Counter(s["augment_type"] for s in samples)
    return {"total": len(samples),
            "coherent": sum(1 for s in samples if s["label"] == LABEL_COHERENT),
            "incoherent": sum(1 for s in samples if s["label"] == LABEL_INCOHERENT),
            "by_augment": dict(c)}


def prepare_data(input_dir: str, output_dir: str, min_paras: int = 5, max_paras: int = 8,
                 target_total: int = 25000, val_ratio: float = 0.3, seed: int = 42,
                 external_dir: str | None = None, input_list: str = "", stride: int = 0,
                 min_para_cjk: int = 4, channels: list[str] | None = None,
                 ext_coherent_min: float = 3.0, ext_incoherent_max: float = 1.0) -> dict:
    """主流程：收集文档 → **doc-level 70/30 split** → 各 split 独立造正负例 → 写 jsonl。"""
    rng = random.Random(seed)
    channels = channels or ["global", "local"]
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    docs = _collect_documents(input_dir, input_list, min_para_cjk, min_paras)
    ext_labeled: list[dict] = []
    if external_dir:
        ext_labeled, ext_docs = load_external_dataset(
            external_dir, min_para_cjk, ext_coherent_min, ext_incoherent_max)
        docs.extend(ext_docs)

    if not docs and not ext_labeled:
        print(f"[ERROR] 无可用文档/外部样本（--input-dir={input_dir} --input-list={input_list} "
              f"--external-dir={external_dir}）", file=sys.stderr)
        return {"error": "no_documents", "n_docs": 0}
    print(f"[INFO] 文档 {len(docs)} 篇·总段落 {sum(len(p) for _, p in docs)}", file=sys.stderr)

    # —— doc-level split（防泄漏核心）——
    rng.shuffle(docs)
    n = len(docs)
    n_val = int(round(n * val_ratio))
    n_val = max(1, min(n - 1, n_val)) if n >= 2 else 0
    val_docs, train_docs = docs[:n_val], docs[n_val:]

    train_samples = _generate_for_docs(train_docs, min_paras, max_paras, stride,
                                       min_para_cjk, channels, rng)
    val_samples = _generate_for_docs(val_docs, min_paras, max_paras, stride,
                                     min_para_cjk, channels, rng)

    # —— 外部带标签样本：自身 70/30 并入 ——
    if ext_labeled:
        rng.shuffle(ext_labeled)
        n_ev = int(round(len(ext_labeled) * val_ratio))
        val_samples.extend(ext_labeled[:n_ev])
        train_samples.extend(ext_labeled[n_ev:])

    # —— 平衡 + 配额（train/val 各按 target 的 70/30 上限）——
    cap_train = int(round(target_total * (1.0 - val_ratio)))
    cap_val = max(0, target_total - cap_train)
    train_samples = _balance_and_cap(train_samples, cap_train, rng)
    val_samples = _balance_and_cap(val_samples, cap_val, rng)
    rng.shuffle(train_samples)
    rng.shuffle(val_samples)

    train_path, val_path = out / "train.jsonl", out / "val.jsonl"
    _dump_jsonl(train_path, train_samples)
    _dump_jsonl(val_path, val_samples)

    combined = train_samples + val_samples
    stats = {
        "_marker": "🔴 2026-06-29 NN连贯性评分集成",
        "method": "CoUDA dual-channel (global_shuffle + local cross-doc replace) · self-supervised",
        "label_scheme": {"1": "coherent(连贯)", "0": "incoherent(不连贯)"},
        "split": "doc-level (防泄漏·同文档窗口不跨 train/val·replace 池 split-local)",
        "research": ["CoUDA NAACL2024 arXiv:2404.00681",
                     "CEDCC EMNLP2023 github.com/cubenlp/CEDCC_corpus",
                     "NLPCC2024 Shared Task4 github.com/cubenlp/NLPCC-2024-Shared-Task4"],
        "n_docs": n, "n_docs_train": len(train_docs), "n_docs_val": len(val_docs),
        "n_positive": sum(1 for s in combined if s["label"] == LABEL_COHERENT),
        "n_neg_shuffle": sum(1 for s in combined if s["augment_type"] == "global_shuffle"),
        "n_neg_replace": sum(1 for s in combined if s["augment_type"] == "local_replace"),
        "n_external": sum(1 for s in combined if s["augment_type"] == "external"),
        "n_train": len(train_samples), "n_val": len(val_samples), "n_total": len(combined),
        "val_ratio": round(len(val_samples) / max(1, len(combined)), 3),
        "train": _aug_counts(train_samples), "val": _aug_counts(val_samples),
        "train_path": str(train_path), "val_path": str(val_path),
        "honest_note": (
            "自监督扰动是连贯性代理信号（shuffle/replace 偏硬破坏）；真实人类连贯判断更细腻。"
            "缓解：--external-dir 并入 CEDCC/NLPCC 金标准做混合监督 + held-out 评测真泛化。"),
    }
    (out / "data_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] train={len(train_samples)} val={len(val_samples)} total={len(combined)} → {out}",
          file=sys.stderr)
    return stats


def main():
    ap = argparse.ArgumentParser(
        description="连贯性二分类自监督数据准备（CoUDA 双通道·stdlib·Windows 兼容）")
    ap.add_argument("--input-dir", default="", help="小说草稿/原文目录（递归搜 .txt）")
    ap.add_argument("--input-list", default="", help="文本清单文件（每行一个 .txt 路径·与 --input-dir 合并）")
    ap.add_argument("--output-dir", default="data", help="输出目录（train.jsonl + val.jsonl）")
    ap.add_argument("--min-paras", type=int, default=5, help="窗口最小段落数")
    ap.add_argument("--max-paras", type=int, default=8, help="窗口最大段落数")
    ap.add_argument("--stride", type=int, default=0, help="滑窗步长段数（<=0=自动 min_paras//2）")
    ap.add_argument("--min-para-cjk", type=int, default=4, help="单段最短 CJK（过滤碎段）")
    ap.add_argument("--target", type=int, default=25000, help="目标样本总数（20000-30000）")
    ap.add_argument("--val-ratio", type=float, default=0.3, help="验证集比例（doc-level·70/30）")
    ap.add_argument("--channels", nargs="+", default=["global", "local"],
                    choices=["global", "local"], help="启用的负样本通道")
    ap.add_argument("--external-dir", default=None, help="外部数据集目录（CEDCC/NLPCC·optional）")
    ap.add_argument("--ext-coherent-min", type=float, default=3.0, help="外部分>=判连贯（CEDCC 1-3→3）")
    ap.add_argument("--ext-incoherent-max", type=float, default=1.0, help="外部分<=判不连贯（→1·中档丢）")
    ap.add_argument("--seed", type=int, default=42, help="随机种子")
    args = ap.parse_args()

    if args.min_paras < 2 or args.max_paras < args.min_paras:
        print("[FATAL] 需 2 <= --min-paras <= --max-paras", file=sys.stderr)
        sys.exit(2)

    stats = prepare_data(
        input_dir=args.input_dir, output_dir=args.output_dir,
        min_paras=args.min_paras, max_paras=args.max_paras,
        target_total=args.target, val_ratio=args.val_ratio, seed=args.seed,
        external_dir=args.external_dir, input_list=args.input_list, stride=args.stride,
        min_para_cjk=args.min_para_cjk, channels=list(dict.fromkeys(args.channels)),
        ext_coherent_min=args.ext_coherent_min, ext_incoherent_max=args.ext_incoherent_max)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
