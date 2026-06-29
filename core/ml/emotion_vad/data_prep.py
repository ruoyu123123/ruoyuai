# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:情绪VAD回归
"""data_prep.py — 中文 VAD 回归数据准备（**零三方依赖·stdlib only·可直接跑**）。

下载 + 解析 + 归一 + 切分 train/val/test，产出 JSONL 给 train.py。
故意只用 urllib/csv/json/random/argparse —— 这样在 torch/datasets 还没装完时也能先跑通数据。

数据源（全部联网核验过·见 README §数据）：
  1. Chinese EmoBank (NYCU-NLP, GitHub)  ── 句级/篇章级/词级 VA（无 D）·UTF-8 繁体·scale 1-9
       CVAW(5512 词) / CVAP(2250 短语) / CVAS(2583 句) / CVAT(2971 篇)
       列(TAB 分隔)：[No.]  text  Valence_Mean  Arousal_Mean  Valence_SD  Arousal_SD  [Category]
  2. DimABSA 2026 (CC BY 4.0, GitHub)     ── aspect 级 VA·JSONL·繁体·scale 1-9（默认关·--with-dimabsa 开）
       句级 VA = 该句所有 Quadruplet[].VA 的均值

归一：valence/arousal 1-9 → (x-1)/8 → [0,1]（与系统 _score_vad / vad_bin 的 0-1 量纲对齐）。

Dominance（D 维·诚实）：中文无原生句级 D 标注。默认只产 V/A。
  --nrc-vad <path> 传入 NRC-VAD 中文词典（需自行向 NRC 申请·见 README）→ 词级 D 聚合成句级 D 弱标签。

繁→简（--simplify）：EmoBank/DimABSA 是繁体，系统正文是简体。生产建议转简体（需 opencc）。
  缺 opencc 又传 --simplify → 硬报错（不静默跳过·北极星不降级）。默认不转·保留繁体并显式告警。

用法：
  python data_prep.py                         # 下载 EmoBank + 产 V/A 句级数据集
  python data_prep.py --with-dimabsa          # 追加 DimABSA 中文
  python data_prep.py --simplify              # 转简体（需 opencc）
  python data_prep.py --nrc-vad nrc_vad_zh.tsv  # 加 D 维弱标签
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import ssl
import sys
import urllib.request
from pathlib import Path

# 🔴 lesson(2026-06-29)：Windows 控制台默认 GBK，print 繁体会 UnicodeEncodeError → 强制 UTF-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RAW = DATA / "raw"
PROC = DATA / "processed"

EMOBANK_BASE = "https://raw.githubusercontent.com/NYCU-NLP/Chinese-EmoBank/main/ChineseEmoBank/"
EMOBANK_FILES = {
    "CVAW": ("CVAW_SD/CVAW_all_SD.csv", "word", "Word"),
    "CVAP": ("CVAP_SD/CVAP_all_SD.csv", "phrase", "Phrase"),
    "CVAS": ("CVAS_SD/CVAS_all.csv", "sentence", "Text"),
    "CVAT": ("CVAT_SD/CVAT_all_SD.csv", "text", "Text"),
}
DIMABSA_BASE = "https://raw.githubusercontent.com/DimABSA/DimABSA2026/main/task-dataset/track_a/subtask_1/zho/"
DIMABSA_FILES = ["zho_restaurant_train_alltasks.jsonl",
                 "zho_laptop_train_alltasks.jsonl",
                 "zho_finance_train_task1.jsonl"]

SCALE_MIN, SCALE_MAX = 1.0, 9.0  # EmoBank/DimABSA 标尺


def _norm(x: float) -> float:
    """1-9 → [0,1]，clamp。"""
    return max(0.0, min(1.0, (x - SCALE_MIN) / (SCALE_MAX - SCALE_MIN)))


def _download(url: str, dest: Path) -> bytes:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [cache] {dest.name}")
        return dest.read_bytes()
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ruoyu-emotion-vad)"})
    print(f"  [GET ] {url}")
    raw = urllib.request.urlopen(req, timeout=60, context=ctx).read()
    dest.write_bytes(raw)
    return raw


def _decode_robust(raw: bytes) -> tuple[str, int]:
    """UTF-8 优先，失败用 errors=replace 并返回坏行将被丢弃的标记。"""
    try:
        return raw.decode("utf-8"), 0
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), 1


# --------------------------------------------------------------------------- 繁→简
_OPENCC = None


def _get_converter(enabled: bool):
    global _OPENCC
    if not enabled:
        return None
    if _OPENCC is not None:
        return _OPENCC
    try:
        from opencc import OpenCC  # opencc-python-reimplemented
    except ImportError:
        # 🔴 北极星不降级：显式要 --simplify 又没 opencc → 硬失败，不静默保留繁体
        print("[FATAL] --simplify 需要 opencc：pip install opencc-python-reimplemented", file=sys.stderr)
        sys.exit(2)
    _OPENCC = OpenCC("t2s")
    return _OPENCC


def _conv(text: str, converter) -> str:
    return converter.convert(text) if converter else text


# --------------------------------------------------------------------------- EmoBank
def parse_emobank(name: str, raw: bytes, text_col: str, converter) -> list[dict]:
    text, bad_file = _decode_robust(raw)
    rows = []
    dropped = 0
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    header = next(reader, None)
    if not header:
        return rows
    # 定位列
    try:
        ci_text = header.index(text_col)
        ci_v = header.index("Valence_Mean")
        ci_a = header.index("Arousal_Mean")
    except ValueError:
        print(f"  [WARN] {name} 表头不符：{header}", file=sys.stderr)
        return rows
    for r in reader:
        if len(r) <= max(ci_text, ci_v, ci_a):
            dropped += 1
            continue
        t = r[ci_text].strip()
        if not t or "�" in t:  # 丢弃含坏字节的行（CVAT 有 2 行）
            dropped += 1
            continue
        try:
            v, a = float(r[ci_v]), float(r[ci_a])
        except ValueError:
            dropped += 1
            continue
        rows.append({"text": _conv(t, converter), "valence": round(_norm(v), 6),
                     "arousal": round(_norm(a), 6), "source": name})
    print(f"  {name}: {len(rows)} 条（丢弃 {dropped}）")
    return rows


# --------------------------------------------------------------------------- DimABSA
def parse_dimabsa(raw: bytes, fname: str, converter) -> list[dict]:
    text, _ = _decode_robust(raw)
    rows, dropped = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            dropped += 1
            continue
        t = (obj.get("Text") or "").strip()
        quads = obj.get("Quadruplet") or []
        vs, as_ = [], []
        for q in quads:
            va = q.get("VA")
            if isinstance(va, str) and "#" in va:
                try:
                    vv, aa = va.split("#")
                    vs.append(float(vv)); as_.append(float(aa))
                except ValueError:
                    continue
        if not t or not vs or "�" in t:
            dropped += 1
            continue
        v = sum(vs) / len(vs)
        a = sum(as_) / len(as_)
        rows.append({"text": _conv(t, converter), "valence": round(_norm(v), 6),
                     "arousal": round(_norm(a), 6), "source": f"dimabsa:{fname.split('_')[1]}"})
    print(f"  dimabsa/{fname}: {len(rows)} 条（丢弃 {dropped}）")
    return rows


# --------------------------------------------------------------------------- D 维弱标签
def load_nrc_vad(path: str) -> dict:
    """NRC-VAD 中文词典 → {word: dominance(0-1)}。容忍 TSV: word V A D（或仅 word D）。"""
    out = {}
    p = Path(path)
    if not p.exists():
        print(f"[FATAL] --nrc-vad 文件不存在：{path}", file=sys.stderr)
        sys.exit(2)
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 2:
            continue
        w = parts[0].strip()
        try:
            d = float(parts[-1])  # 末列当 dominance（NRC-VAD 顺序 V A D，末列即 D）
        except ValueError:
            continue
        if w:
            out[w] = max(0.0, min(1.0, d))
    print(f"  NRC-VAD: {len(out)} 词（D 维弱标签源）")
    return out


def add_dominance(rows: list[dict], nrc: dict):
    """句级 D 弱标签 = 句中命中 NRC-VAD 词的 D 均值（词级聚合·SO-CAL 简化版）。无命中→None。"""
    labeled = 0
    for r in rows:
        t = r["text"]
        hits = [d for w, d in nrc.items() if w and w in t]
        if hits:
            r["dominance"] = round(sum(hits) / len(hits), 6)
            labeled += 1
        else:
            r["dominance"] = None
    print(f"  D 维弱标签：{labeled}/{len(rows)} 句命中（其余 dominance=null·训练时按掩码忽略）")


# --------------------------------------------------------------------------- split
def split_rows(rows: list[dict], seed: int, ratios=(0.8, 0.1, 0.1)):
    rnd = random.Random(seed)
    rows = list(rows)
    rnd.shuffle(rows)
    n = len(rows)
    n_tr = int(n * ratios[0])
    n_va = int(n * ratios[1])
    return rows[:n_tr], rows[n_tr:n_tr + n_va], rows[n_tr + n_va:]


def write_jsonl(rows: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="中文 VAD 回归数据准备（stdlib·可直接跑）")
    ap.add_argument("--with-dimabsa", action="store_true", help="追加 DimABSA 中文（CC BY 4.0·aspect 聚合到句级）")
    ap.add_argument("--simplify", action="store_true", help="繁→简（需 opencc·生产建议开）")
    ap.add_argument("--nrc-vad", default=None, help="NRC-VAD 中文词典 TSV 路径 → 加 D 维弱标签")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-cjk", type=int, default=2, help="句级最短 CJK 字数（过短样本丢弃）")
    args = ap.parse_args()

    converter = _get_converter(args.simplify)
    if not args.simplify:
        print("[NOTE] 未转简体：训练数据保留繁体。生产（系统正文为简体）建议 --simplify（装 opencc）。")

    print("== 1. 下载 + 解析 Chinese EmoBank ==")
    sentence_rows: list[dict] = []   # 句级/篇章级（CVAS+CVAT[+DimABSA]）→ 句级回归主集
    word_rows: list[dict] = []       # 词级/短语级（CVAW+CVAP）→ lexicon/辅助
    provenance = []
    for name, (rel, gran, tcol) in EMOBANK_FILES.items():
        raw = _download(EMOBANK_BASE + rel, RAW / "emobank" / Path(rel).name)
        parsed = parse_emobank(name, raw, tcol, converter)
        provenance.append({"set": name, "granularity": gran, "rows": len(parsed),
                           "source": "Chinese-EmoBank", "url": EMOBANK_BASE + rel})
        if gran in ("sentence", "text"):
            sentence_rows.extend(parsed)
        else:
            word_rows.extend(parsed)

    if args.with_dimabsa:
        print("== 1b. 下载 + 解析 DimABSA 中文 ==")
        for fname in DIMABSA_FILES:
            try:
                raw = _download(DIMABSA_BASE + fname, RAW / "dimabsa" / fname)
            except Exception as e:  # noqa: BLE001 单个域名失败不阻断
                print(f"  [WARN] dimabsa/{fname} 下载失败：{type(e).__name__}: {e}", file=sys.stderr)
                continue
            parsed = parse_dimabsa(raw, fname, converter)
            provenance.append({"set": fname, "granularity": "sentence(aspect-agg)",
                               "rows": len(parsed), "source": "DimABSA2026", "license": "CC BY 4.0"})
            sentence_rows.extend(parsed)

    # 句级过滤：去重 + 最短字数
    def _cjk(s):
        return sum(1 for ch in s if "一" <= ch <= "鿿")
    seen = set()
    filt = []
    for r in sentence_rows:
        key = r["text"]
        if key in seen or _cjk(key) < args.min_cjk:
            continue
        seen.add(key)
        filt.append(r)
    print(f"\n句级合计：{len(sentence_rows)} → 去重/过滤后 {len(filt)}")
    sentence_rows = filt

    dims = "va"
    if args.nrc_vad:
        print("== 2. 加 Dominance 弱标签（NRC-VAD 词级聚合）==")
        nrc = load_nrc_vad(args.nrc_vad)
        add_dominance(sentence_rows, nrc)
        dims = "vad"

    print("\n== 3. 切分 train/val/test (8/1/1·seed=%d) ==" % args.seed)
    tr, va, te = split_rows(sentence_rows, args.seed)
    write_jsonl(tr, PROC / "sentence_train.jsonl")
    write_jsonl(va, PROC / "sentence_val.jsonl")
    write_jsonl(te, PROC / "sentence_test.jsonl")
    write_jsonl(word_rows, PROC / "word_lexicon.jsonl")
    print(f"  sentence: train={len(tr)} val={len(va)} test={len(te)}")
    print(f"  word_lexicon(辅助): {len(word_rows)}")

    manifest = {
        "_doc": "🔴 2026-06-29 NN训练:情绪VAD回归 数据清单",
        "dims": dims,
        "scale_normalization": "valence/arousal 1-9 → (x-1)/8 → [0,1]",
        "simplified": bool(args.simplify),
        "splits": {"train": len(tr), "val": len(va), "test": len(te), "word_lexicon": len(word_rows)},
        "dominance": ("weak_label(NRC-VAD word-agg)" if args.nrc_vad else
                      "NOT_AVAILABLE(中文无原生句级 D·见 README §数据局限)"),
        "provenance": provenance,
        "licenses": {
            "Chinese-EmoBank": "repo 无显式 LICENSE·学术引用 ACM-TALLIP-2022/NAACL-2016·商用需邮件 NYCU-NLP",
            "DimABSA2026": "CC BY 4.0",
            "NRC-VAD": "研究免费·商用需联系 NRC（需自行申请下载）",
        },
    }
    (PROC / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 清单 → {PROC / 'manifest.json'}")
    print("[OK] data_prep 完成。下一步：python train.py（主代理集中跑·12GB GPU）")


if __name__ == "__main__":
    main()
