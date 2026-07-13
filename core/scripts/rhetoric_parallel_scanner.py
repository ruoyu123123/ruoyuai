#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rhetoric_parallel_scanner.py — 排比/反复修辞密度
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L61 P1)

【缺口】R10 联网调研(arXiv 2312.00100 中文修辞 parallelism F1=0.43 +
Wikipedia 古典骈散排比)：排比 anaphora / epistrophe / 并列从句 / 连词反复
为重要文学声音指纹(王安忆/张爱玲/沈从文 vs 番茄/起点头部)。此前【0 检测】。

【做法 · 确定性纯规则】：
  四子 metric：
   · anaphora_density：连续 ≥3 句首相同 2-4 字 token。
   · epistrophe_density：连续 ≥3 句末相同 2-4 字 token(逗号/句末符号前)。
   · parallel_clause_density：含『也』『又』『再』『一会儿…一会儿』排比连词的
     近邻句对。
   · polysyndeton_run：单段内连续 ≥4 个『又…又…又…』『一边…一边…一边』
     『或…或…或』。

  聚合 rhetoric_parallel_signature。
  作者档 author_rhetoric_parallel_signature 基线 → 草稿低于 0.5x →
  RHETORIC_PARALLEL_GAP advisory。

【北极星② / ⑤】纯 advisory · 作者档第一权威 · 绝不 hard_gate。
  env RHETORIC_PARALLEL_MODE: off / shadow(默认) / active。

用法：python rhetoric_parallel_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from atomic_json import load_json  # 读侧单一真理源

ISSUE_CODE = "RHETORIC_PARALLEL_GAP"
MIN_CJK = 500

SENT_SPLIT = re.compile(r"[。！？；]+|\n")
COMMA_SPLIT = re.compile(r"[，,]+")
CJK_PAT = re.compile(r"[一-鿿]")
PARALLEL_CONJ = re.compile(r"也|又|再|还|更|且")
POLYSYNDETON_PAT = re.compile(
    r"(又[一-龥]{1,8}){4,}|(一边[一-龥]{1,8}){3,}|"
    r"(或[一-龥]{1,8}){3,}|(一会儿[一-龥]{1,8}){3,}")


def _mode() -> str:
    m = (os.environ.get("RHETORIC_PARALLEL_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(p: Path):
    return load_json(p)


def _author_baseline(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return None
    return obj.get("author_rhetoric_parallel_signature")


def _head_token(s, n=3):
    chars = CJK_PAT.findall(s)
    return "".join(chars[:n])


def _tail_token(s, n=3):
    chars = CJK_PAT.findall(s)
    return "".join(chars[-n:])


def detect_anaphora(sents, min_run=3):
    runs = []
    cur_head = None
    cur_count = 0
    cur_start = 0
    for i, s in enumerate(sents):
        h2 = _head_token(s, 2)
        if not h2:
            cur_head = None
            cur_count = 0
            continue
        if cur_head and h2 == cur_head:
            cur_count += 1
        else:
            if cur_count >= min_run:
                runs.append({"token": cur_head, "count": cur_count,
                             "start": cur_start})
            cur_head = h2
            cur_count = 1
            cur_start = i
    if cur_count >= min_run:
        runs.append({"token": cur_head, "count": cur_count,
                     "start": cur_start})
    return runs


def detect_epistrophe(sents, min_run=3):
    runs = []
    cur_tail = None
    cur_count = 0
    cur_start = 0
    for i, s in enumerate(sents):
        t2 = _tail_token(s, 2)
        if not t2:
            cur_tail = None
            cur_count = 0
            continue
        if cur_tail and t2 == cur_tail:
            cur_count += 1
        else:
            if cur_count >= min_run:
                runs.append({"token": cur_tail, "count": cur_count,
                             "start": cur_start})
            cur_tail = t2
            cur_count = 1
            cur_start = i
    if cur_count >= min_run:
        runs.append({"token": cur_tail, "count": cur_count,
                     "start": cur_start})
    return runs


def detect_parallel_clauses(text):
    """同段内多个 PARALLEL_CONJ 出现：每段计数 >=3 算并列从句结构。"""
    hits = []
    for para in text.split("\n"):
        cnt = len(PARALLEL_CONJ.findall(para))
        clauses = COMMA_SPLIT.split(para)
        if cnt >= 3 and len(clauses) >= 3:
            hits.append({"para_len": len(para), "conj_count": cnt})
    return hits


def detect_polysyndeton(text):
    return [{"match": m.group(0)[:40]}
            for m in POLYSYNDETON_PAT.finditer(text)]


def scan(draft_path, project_root=None) -> dict:
    mode_env = _mode()
    out = {"scanner": "rhetoric_parallel", "schema_version": "1.0",
           "mode": mode_env, "code": ISSUE_CODE, "gate_level": "advisory",
           "verdict": "PASS", "violations": [], "warning": None}
    if mode_env == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out
    sents = [s for s in SENT_SPLIT.split(text) if s.strip()]
    anaphora = detect_anaphora(sents)
    epistrophe = detect_epistrophe(sents)
    parallel_clauses = detect_parallel_clauses(text)
    polysyndeton = detect_polysyndeton(text)

    per_1k = 1000.0 / cjk if cjk else 0
    sig = {
        "anaphora_density": round(len(anaphora) * per_1k, 4),
        "epistrophe_density": round(len(epistrophe) * per_1k, 4),
        "parallel_clause_density": round(len(parallel_clauses) * per_1k, 4),
        "polysyndeton_run_density": round(len(polysyndeton) * per_1k, 4),
    }
    out["rhetoric_parallel_signature"] = sig
    out["sample_anaphora"] = anaphora[:3]
    out["sample_epistrophe"] = epistrophe[:3]
    out["sample_polysyndeton"] = polysyndeton[:3]

    baseline = _author_baseline(project_root)
    if baseline is None:
        out["note"] = "无作者档 author_rhetoric_parallel_signature · 仅记录"
        out["baseline_source"] = "none"
        return out
    out["baseline_source"] = "author_profile"
    out["author_baseline"] = baseline

    # 多个维度低于基线 50%
    gaps = []
    for k, v in sig.items():
        b = baseline.get(k)
        if isinstance(b, (int, float)) and b > 0:
            if v < 0.5 * b:
                gaps.append({"metric": k, "actual": v, "baseline": b})
    out["gap_metrics"] = gaps
    msg = None
    if len(gaps) >= 2:
        msg = (f"排比/反复密度低于作者基线 50%(共 {len(gaps)} 维): "
               f"{','.join(g['metric'] for g in gaps[:3])}")
    if msg:
        if mode_env == "active":
            out["violations"].append({
                "code": ISSUE_CODE, "kind": "rhetoric_parallel_gap",
                "severity": "minor", "message": msg,
                "gap_metrics": gaps,
                "_doc": "advisory · 作者档第一权威 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] rhetoric_parallel: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="排比/反复密度 (advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
