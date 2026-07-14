#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:风格声纹嵌入
"""eval.py — 风格/声纹嵌入评估 + 对标现有 SFS / char-3gram 基线

评估指标(对标 UAR/DramaCV 的 AUC 协议):
  1. 作者验证 AUC/EER/acc —— 同作者 vs 异作者 chunk 对的 cosine 分离度
     · test_seen  : 训练作者的**留出章节**(SFS 用: 生成文 vs 作者 centroid 同分布)
     · test_unseen: **未见作者**整本(泛化: 新书用没训过的作者 → 直接服务北极星)
  2. 作者检索 acc —— 每 chunk 按最近 centroid 归属, top-1 命中率
  3. 角色声纹 AUC(phase2) —— 同角色 view vs 异角色 view 的 cosine(DramaCV 协议)
  4. SFS 对标 —— embedding-SFS = cosine(复刻文 centroid, 作者 centroid) vs
     现有启发式 SFS(style_evaluator sfs_quick) 的相关性/区分度

基线对照(证明 NN 模型优于现状):
  --baseline char3gram : 字符 3-gram 袋(= character_distinctiveness_scanner 现用法的代理)
  --baseline <hf_id>   : 任意 SentenceTransformer 零样本(如 StyleDistance/mstyledistance)
  默认 --model <训练产物> : fine-tune 后的风格嵌入

用法:
  python eval.py --model runs/style_embed_v1/final
  python eval.py --baseline char3gram                       # 现状基线
  python eval.py --baseline StyleDistance/mstyledistance    # 零样本基座
  python eval.py --model runs/.../final --character         # 角色声纹
  python eval.py --model runs/.../final --sfs-compare <复刻目录>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
REPO_ROOT = HERE.parents[2]

sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))
from proc_utils import run_utf8  # noqa: E402 · 子进程 UTF-8 单一真理源
CJK_RE = re.compile(r"[一-鿿]")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"[FATAL] 缺 {path} — 先跑 data_prep.py")
    return [json.loads(l) for l in path.open(encoding="utf-8")]


# ── 编码器抽象(NN 模型 / char-3gram 基线 统一成定长向量)─────────────────────
class Encoder:
    def __init__(self, model=None, kind="nn", dim=4096):
        self.model, self.kind, self.dim = model, kind, dim

    @classmethod
    def nn(cls, name_or_path: str):
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(name_or_path)
        print(f"[encoder] NN {name_or_path} dim={m.get_sentence_embedding_dimension()}")
        return cls(model=m, kind="nn")

    @classmethod
    def char3gram(cls, dim=4096):
        print(f"[encoder] char-3gram 袋 (hashed {dim}-dim · 现状基线代理)")
        return cls(kind="char3gram", dim=dim)

    def embed(self, texts: list[str]):
        import numpy as np
        if self.kind == "nn":
            return np.asarray(self.model.encode(
                texts, normalize_embeddings=True, batch_size=64,
                show_progress_bar=False), dtype="float32")
        # char-3gram hashed bag, L2-normalized
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            s = re.sub(r"\s+", "", t)
            for j in range(len(s) - 2):
                g = s[j:j + 3]
                h = int(hashlib.md5(g.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
            n = math.sqrt(float((out[i] ** 2).sum()))
            if n > 0:
                out[i] /= n
        return out


# ── 指标 ─────────────────────────────────────────────────────────────────────
def roc_auc(scores, labels) -> float:
    """rank-based AUC(Mann-Whitney), 含 tie 平均秩。scores 高 = 更可能同作者。"""
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
    pos = [r for r, l in zip(ranks, labels) if l == 1]
    n_pos, n_neg = len(pos), len(labels) - sum(1 for l in labels if l == 1)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return (sum(pos) - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def eer_and_acc(scores, labels):
    """等错误率 EER + 最佳阈值 accuracy。"""
    thrs = sorted(set(scores))
    best_acc, eer = 0.0, 1.0
    n = len(labels)
    npos = sum(labels)
    nneg = n - npos
    for t in thrs:
        tp = sum(1 for s, l in zip(scores, labels) if s >= t and l == 1)
        fp = sum(1 for s, l in zip(scores, labels) if s >= t and l == 0)
        fn = npos - tp
        far = fp / nneg if nneg else 0.0
        frr = fn / npos if npos else 0.0
        acc = (tp + (nneg - fp)) / n
        best_acc = max(best_acc, acc)
        if abs(far - frr) < eer:
            eer = abs(far - frr)
            eer_val = (far + frr) / 2.0
    return locals().get("eer_val", 1.0), best_acc


def cos(a, b):
    import numpy as np
    return float(np.dot(a, b))  # 已 L2 归一化


def build_pairs(by_label: dict, max_pairs: int, seed: int):
    rng = random.Random(seed)
    labels = [k for k, v in by_label.items() if len(v) >= 1]
    s_idx, lab = [], []
    half = max_pairs // 2
    multi = [k for k in labels if len(by_label[k]) >= 2]
    for _ in range(half):
        k = rng.choice(multi)
        x, y = rng.sample(range(len(by_label[k])), 2)
        s_idx.append((k, x, k, y)); lab.append(1)
    for _ in range(half):
        a, b = rng.sample(labels, 2)
        s_idx.append((a, rng.randrange(len(by_label[a])),
                      b, rng.randrange(len(by_label[b])))); lab.append(0)
    return s_idx, lab


def verification(enc: Encoder, rows: list[dict], label_key: str, name: str,
                 max_pairs=6000, seed=42):
    import numpy as np
    by_label: dict = {}
    for r in rows:
        by_label.setdefault(r[label_key], []).append(r["text"])
    # 编码每 label 下文本(去重索引)
    flat, index = [], {}
    for k, texts in by_label.items():
        index[k] = []
        for t in texts:
            index[k].append(len(flat)); flat.append(t)
    vecs = enc.embed(flat)
    by_vec = {k: [vecs[i] for i in idxs] for k, idxs in index.items()}
    s_idx, labels = build_pairs(by_vec, max_pairs, seed)
    scores = [cos(by_vec[a][i], by_vec[b][j]) for (a, i, b, j) in s_idx]
    auc = roc_auc(scores, labels)
    eer, acc = eer_and_acc(scores, labels)
    pos = [s for s, l in zip(scores, labels) if l == 1]
    neg = [s for s, l in zip(scores, labels) if l == 0]
    print(f"  [{name}] AUC={auc:.4f} EER={eer:.4f} acc={acc:.4f} "
          f"| cos same={np.mean(pos):.3f} diff={np.mean(neg):.3f} "
          f"Δ={np.mean(pos)-np.mean(neg):.3f} (n_pairs={len(labels)})")
    return {"auc": auc, "eer": eer, "acc": acc,
            "cos_same": float(np.mean(pos)), "cos_diff": float(np.mean(neg))}


def author_retrieval(enc: Encoder, gallery: list[dict], query: list[dict], name: str):
    """每 query chunk 归到最近作者 centroid, top-1 命中率。"""
    import numpy as np
    g_by = {}
    for r in gallery:
        g_by.setdefault(r["author"], []).append(r["text"])
    cents, names = [], []
    for a, texts in g_by.items():
        v = enc.embed(texts).mean(0)
        v = v / (np.linalg.norm(v) + 1e-9)
        cents.append(v); names.append(a)
    cents = np.asarray(cents)
    qv = enc.embed([r["text"] for r in query])
    qv = qv / (np.linalg.norm(qv, axis=1, keepdims=True) + 1e-9)
    pred = (qv @ cents.T).argmax(1)
    hit = sum(1 for p, r in zip(pred, query) if names[p] == r["author"])
    acc = hit / len(query)
    print(f"  [{name}] 作者检索 top-1 acc={acc:.4f} ({hit}/{len(query)}, {len(names)} 作者)")
    return {"retrieval_acc": acc, "n_authors": len(names)}


# ── SFS 对标 ─────────────────────────────────────────────────────────────────
def sfs_compare(enc: Encoder, replica_dir: Path, seed=42):
    """对比 embedding-SFS vs 启发式 SFS。

    replica_dir 下每个 *.txt 是某书复刻文; 书名经 --author 或目录名推断。
    embedding-SFS = cosine(复刻文 chunk 均值, 该作者原文 centroid)。
    """
    import numpy as np
    files = sorted(replica_dir.glob("*.txt"))
    if not files:
        print(f"[sfs] {replica_dir} 无 .txt, 跳过"); return {}
    rows = []
    for f in files:
        # 作者推断: 文件名/父目录含书名
        author = next((b for b in [p.name for p in (REPO_ROOT/"workspace"/"styles").iterdir()]
                       if b in str(f)), None)
        if not author:
            print(f"  [skip] 无法推断作者: {f.name}"); continue
        ref_dir = REPO_ROOT / "workspace" / "styles" / author / "原文"
        ref_files = sorted(ref_dir.glob("*.txt"))[:200]
        ref_texts = [c for rf in ref_files
                     for c in [_clean(rf.read_text(encoding="utf-8", errors="replace"))]]
        ref_cent = enc.embed(ref_texts).mean(0)
        ref_cent /= (np.linalg.norm(ref_cent) + 1e-9)
        gen = _clean(f.read_text(encoding="utf-8", errors="replace"))
        gen_v = enc.embed([gen]).mean(0)
        gen_v /= (np.linalg.norm(gen_v) + 1e-9)
        emb_sfs = float(np.dot(gen_v, ref_cent))
        # 启发式 SFS
        heur = _run_style_evaluator(f, ref_dir)
        rows.append({"file": f.name, "author": author,
                     "embedding_sfs": round(emb_sfs, 4), "heuristic_sfs": heur})
        print(f"  {f.name[:40]:40s} author={author} emb_sfs={emb_sfs:.3f} heur_sfs={heur}")
    # 相关性
    es = [r["embedding_sfs"] for r in rows if r["heuristic_sfs"] is not None]
    hs = [r["heuristic_sfs"] for r in rows if r["heuristic_sfs"] is not None]
    corr = _pearson(es, hs) if len(es) >= 3 else None
    print(f"[sfs] embedding-SFS vs heuristic-SFS pearson={corr}")
    return {"rows": rows, "pearson": corr}


def _clean(t: str) -> str:
    lines = [l.replace("　", "").strip() for l in t.splitlines()]
    return "\n".join(l for l in lines if l and not re.match(r"^第.{0,12}[章卷]", l))


def _run_style_evaluator(gen: Path, ref_dir: Path):
    import tempfile
    se = REPO_ROOT / "core" / "scripts" / "style_evaluator.py"
    if not se.exists():
        return None
    out = Path(tempfile.gettempdir()) / f"sfs_{gen.stem}.json"
    cmd = [sys.executable, str(se), "--gen", str(gen),
           "--multi-ref-from-dir", str(ref_dir), "--multi-ref-count", "5",
           "--output", str(out)]
    try:
        run_utf8(cmd, timeout=300, text=False)
        d = json.loads(out.read_text(encoding="utf-8"))
        return d.get("sfs_quick") or d.get("sfs_score") or d.get("overall_score")
    except Exception:
        return None


def _pearson(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    return round(num / (dx * dy), 4) if dx and dy else None


def main():
    ap = argparse.ArgumentParser(description="风格/声纹嵌入评估")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--model", help="训练产物路径(SentenceTransformer)")
    g.add_argument("--baseline", help="char3gram 或 HF 模型 id(零样本对照)")
    ap.add_argument("--character", action="store_true", help="跑角色声纹评估(phase2)")
    ap.add_argument("--sfs-compare", default=None, help="复刻文目录 → 对标启发式 SFS")
    ap.add_argument("--max-pairs", type=int, default=6000)
    args = ap.parse_args()

    if args.baseline == "char3gram":
        enc = Encoder.char3gram()
    else:
        enc = Encoder.nn(args.model or args.baseline)

    report = {"_marker": "🔴 2026-06-29 NN训练:风格声纹嵌入",
              "encoder": args.model or args.baseline}

    if args.character:
        rows = load_jsonl(DATA / "character_dialogues.jsonl")
        # 把每角色台词切 view 当样本
        views = []
        for r in rows:
            utts = r["text"].split("\n")
            for i in range(0, len(utts), 16):
                v = "\n".join(utts[i:i + 16])
                if len(v) >= 30:
                    views.append({"text": v, "label": r["label"]})
        print("== 角色声纹验证(phase2) ==")
        report["character_voiceprint"] = verification(
            enc, views, "label", "character", args.max_pairs)
    elif args.sfs_compare:
        print("== SFS 对标 ==")
        report["sfs_compare"] = sfs_compare(enc, Path(args.sfs_compare))
    else:
        print("== 作者验证(SFS 核心指标) ==")
        report["verif_test_seen"] = verification(
            enc, load_jsonl(DATA / "author_chunks_test_seen.jsonl"),
            "author", "test_seen", args.max_pairs)
        report["verif_test_unseen"] = verification(
            enc, load_jsonl(DATA / "author_chunks_test_unseen.jsonl"),
            "author", "test_unseen(未见作者)", args.max_pairs)
        report["retrieval"] = author_retrieval(
            enc, load_jsonl(DATA / "author_chunks_train.jsonl")[::10],  # gallery 抽样
            load_jsonl(DATA / "author_chunks_test_seen.jsonl"), "test_seen")

    out = HERE / "runs" / "eval_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] → {out}")


if __name__ == "__main__":
    main()
