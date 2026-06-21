#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""acw_drift_scanner.py — Activity-Centric Writing 段中心活动漂移 advisory · cluster · 2026-06-21 R20 W9 Batch-CC · P2

【缺口 · R20 Q3-Q4 id 21】Activity-Centric Writing(ACW)：好段落每段有一个
中心活动(动作/对话/感官 sequence)·LLM 默认段内多个独立子动作堆叠=游走感
(reader 注意力被分散)·此前 narrative_short_sentence + repeat_noun_density
+ paragraph_engagement_heat 都不直接查段内中心活动一致性。

【做法 · 占位 · 启发式】
  · 全 cluster 草稿按空行切段
  · 每段按句末符号切句·取每句前 4 CJK 作为「句首主语候选」
  · distinct_head_ratio = 句首主语去重数 / 句子总数
    - 高比 → 段内主语频繁切换 → 中心活动漂移
    - 低比 → 段内主语稳定 → 有明确中心活动
  · 段判 drift 条件: sentences ≥ 3 且 distinct_head_ratio > 0.75
  · 整 cluster 统计 drift 段占比 → drift_paragraph_ratio
  · drift_paragraph_ratio > 阈值 → ACW_DRIFT_FROM_CENTER

【作者档第一权威】
  · 作者风格.json.acw_baseline = {drift_paragraph_ratio_max} 第一权威
  · 缺 → 兜底 drift_paragraph_ratio_max = 0.30

【与既有 scanner 严格正交】
  · narrative_short_sentence 查句长偏短(碎句)
  · paragraph_engagement_heat 查段 Loewenstein gap 热度
  · prose_rhythm 查主语流水账 streak(连续句首主语)
  · 本 scanner = ACW 段内中心活动一致性唯一覆盖

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  ACW_DRIFT_FROM_CENTER 绝不进 audit_hub.HARD_GATE_CODES。

env ACW_MODE: off / shadow(默认) / active
用法: python acw_drift_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ACW_DRIFT_FROM_CENTER"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?…])")

DEFAULT_DRIFT_PARA_RATIO_MAX = 0.30
PARA_HEAD_DISTINCT_RATIO_DRIFT = 0.75
MIN_SENTENCES_PER_PARA = 3
MIN_CJK = 300
# 句首 head 长度 = 1 CJK·主语 proxy(pronoun/proper noun 首字)
# 取 1 → 同主语段稳定 (如"他X"+"他X"+"他X"=>{他}) · 不同主语 (他/她/茶/猫) => distinct=4
HEAD_LEN = 1


def _mode() -> str:
    m = (os.environ.get("ACW_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            mb = obj.get("acw_baseline")
            if isinstance(mb, dict):
                return mb
    return None


def _paragraph_sentences(p: str) -> list[str]:
    sents = [s.strip() for s in _SENTENCE_SPLIT.split(p) if s.strip()]
    return [s for s in sents if _cjk_count(s) >= 2]


def _sentence_head(s: str, n: int = HEAD_LEN) -> str:
    s = s.lstrip(" 　\"'“”‘’「『（(")
    cjk_only = "".join(ch for ch in s if "一" <= ch <= "鿿")
    return cjk_only[:n]


def _paragraph_drift(p: str) -> dict:
    sents = _paragraph_sentences(p)
    if len(sents) < MIN_SENTENCES_PER_PARA:
        return {"sentences": len(sents), "distinct_heads": len(sents),
                "distinct_head_ratio": 0.0, "is_drift": False}
    heads = [_sentence_head(s) for s in sents]
    heads = [h for h in heads if h]
    if not heads:
        return {"sentences": len(sents), "distinct_heads": 0,
                "distinct_head_ratio": 0.0, "is_drift": False}
    distinct = len(set(heads))
    ratio = distinct / len(heads)
    return {
        "sentences": len(sents),
        "distinct_heads": distinct,
        "distinct_head_ratio": round(ratio, 3),
        "is_drift": ratio > PARA_HEAD_DISTINCT_RATIO_DRIFT,
    }


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "acw_drift", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE,
           "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) < 5:
        out["note"] = "段落不足·跳过"
        return out

    para_results = [_paragraph_drift(p) for p in paragraphs]
    eligible = [r for r in para_results if r["sentences"] >= MIN_SENTENCES_PER_PARA]
    drift_count = sum(1 for r in eligible if r["is_drift"])
    drift_ratio = round(drift_count / len(eligible), 4) if eligible else 0.0

    baseline = _read_author_baseline(project_root)
    use_max = DEFAULT_DRIFT_PARA_RATIO_MAX
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        m = baseline.get("drift_paragraph_ratio_max")
        if isinstance(m, (int, float)):
            use_max = float(m)

    out.update({
        "cjk": cjk,
        "paragraphs_total": len(paragraphs),
        "paragraphs_eligible": len(eligible),
        "drift_paragraph_count": drift_count,
        "drift_paragraph_ratio": drift_ratio,
        "baseline_source": baseline_source,
        "baseline": {"drift_paragraph_ratio_max": use_max},
        "_placeholder": True,
        "_doc_placeholder": "句首 head 去重比 proxy·真版 SBERT 段中心活动 cluster defer",
    })

    flags = []
    if drift_ratio > use_max:
        flags.append({
            "code": ISSUE_CODE,
            "msg": (f"ACW drift_paragraph_ratio={round(drift_ratio*100,1)}% > "
                    f"{round(use_max*100,1)}%·段内主语频繁切换=中心活动漂移"
                    f"·{drift_count}/{len(eligible)} 段命中")
        })

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "acw_drift", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Activity-Centric Writing · advisory · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] acw_drift: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="ACW 段中心活动漂移 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
