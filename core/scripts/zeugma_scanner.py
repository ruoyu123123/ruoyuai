#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zeugma_scanner.py — 拈连(zeugma) advisory shadow · R22 W10 Batch-FF · P1

【缺口】陈望道 38 格之「拈连」(同一动词跨主语/宾语·V+O1 正常但 V+O2 违反常识=幽默/反讽签名)
搞笑流/讽刺向网文核心修辞·全系统零检测·rhetorical_balance 只查四类分布·不查单格细节。

【做法 · 确定性 · 零 LLM/零联网】
  · 句对级扫 V+O1 ... V+O2 结构(相邻或同段 ≤80 字内)
  · V ∈ core/data/verb_object_collocation_freq.json 8 个动词
  · O1 命中该动词 common_objects(后接 ≤4 字 CJK 宾语切片) + O2 命中 unusual_objects → 拈连候选
  · 按 cluster 草稿统计 zeugma_per_kcj 密度
  · 作者档 zeugma_per_kcj 第一权威(搞笑流 > 0·严肃 ≈ 0)·无 → 兜底 thresh=0.5/kCJK

【三 advisory】
  · ZEUGMA_DETECTED          — 命中 ≥ N 个拈连候选(便于回看)
  · ZEUGMA_OVER_BASELINE     — 密度 > 作者档 + 1σ·堆叠 zeugma 影响节奏
  · ZEUGMA_UNDER_BASELINE    — 搞笑流作者(baseline>0.3)但本 cluster 0 命中=喜剧基底丢失

【与既有 scanner 严格正交】
  · rhetorical_balance       → 四类分布层
  · rhetorical_inventory     → 全格命中清单层
  · anadiplosis_scanner      → 顶针单格(本者拈连单格)

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  ZEUGMA_* 绝不进 audit_hub.HARD_GATE_CODES。

env ZEUGMA_MODE: off / shadow(默认) / active
用法: python zeugma_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import style_analyzer as sa  # noqa: E402

ISSUE_CODE_DETECTED = "ZEUGMA_DETECTED"
ISSUE_CODE_OVER = "ZEUGMA_OVER_BASELINE"
ISSUE_CODE_UNDER = "ZEUGMA_UNDER_BASELINE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "verb_object_collocation_freq.json"

DEFAULT_BASELINE = 0.0          # 默认基线 (严肃作品 0)
DEFAULT_OVER_DELTA = 0.5        # 兜底 over 触发增量 (/kCJK)
DEFAULT_UNDER_MIN_BASELINE = 0.3  # 兜底搞笑流判定门槛
DEFAULT_DETECTED_REPORT_MIN = 1   # ≥1 命中即 DETECTED report

_CJK_RE = re.compile(r"[一-鿿]")


def _mode() -> str:
    m = (os.environ.get("ZEUGMA_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def load_lexicon() -> dict:
    """读动宾搭配占位词典·缺失/损坏 → 极简兜底(2 动词)"""
    try:
        return json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "_placeholder": True,
            "verbs": {
                "飞": {"common_objects": ["鸟", "蝴蝶"], "unusual_objects": ["阿Q", "梦想"]},
                "走": {"common_objects": ["路", "人"], "unusual_objects": ["神", "心"]},
            },
            "_match_policy": {"max_inter_sentence_chars": 80, "min_obj_chars": 1, "max_obj_chars": 4},
        }


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
            for key in ("zeugma_per_kcj", "zeugma_baseline"):
                v = obj.get(key)
                if isinstance(v, (int, float)):
                    return {"baseline": float(v), "source": "author_profile"}
                if isinstance(v, dict) and isinstance(v.get("mean"), (int, float)):
                    return {"baseline": float(v["mean"]),
                            "std": float(v.get("std", 0)) if isinstance(v.get("std"), (int, float)) else 0.0,
                            "source": "author_profile"}
    return None


def _find_verb_obj_hits(sentence: str, verb: str, candidates: list[str], max_obj_chars: int) -> list[str]:
    """句中找 verb 紧邻其后(≤max_obj_chars 字 CJK)的 candidates 命中·返回命中宾语列表"""
    if not verb or not sentence or not candidates:
        return []
    hits: list[str] = []
    idx = 0
    while True:
        i = sentence.find(verb, idx)
        if i < 0:
            break
        tail = sentence[i + len(verb): i + len(verb) + max_obj_chars + 4]  # 留余量
        # 只在 verb 直接后接 CJK 时检测
        for obj in candidates:
            if obj and tail.startswith(obj):
                hits.append(obj)
                break
            # 容忍中间可能有「了 / 着 / 过」单字虚词
            if obj and re.match(r"^[了着过]" + re.escape(obj), tail):
                hits.append(obj)
                break
        idx = i + len(verb)
    return hits


def detect_zeugma_pairs(text: str, lexicon: dict) -> list[dict]:
    """扫文本·返回拈连候选列表 [{verb, sent1, obj1, sent2, obj2}]"""
    verbs_map = lexicon.get("verbs") or {}
    policy = lexicon.get("_match_policy") or {}
    max_obj = int(policy.get("max_obj_chars") or 4)
    max_gap = int(policy.get("max_inter_sentence_chars") or 80)
    sents = sa.split_sentences(text)
    if len(sents) < 2:
        return []
    candidates: list[dict] = []
    for verb, info in verbs_map.items():
        if not isinstance(info, dict):
            continue
        common = info.get("common_objects") or []
        unusual = info.get("unusual_objects") or []
        if not (common and unusual):
            continue
        # 标记每句的 common / unusual 命中
        marks = []
        for s_idx, s in enumerate(sents):
            c_hits = _find_verb_obj_hits(s, verb, common, max_obj)
            u_hits = _find_verb_obj_hits(s, verb, unusual, max_obj)
            marks.append({"idx": s_idx, "sent": s, "common": c_hits, "unusual": u_hits})
        # 句对扫描：i common · j unusual · i<j 且 i 到 j 间累计 CJK ≤ max_gap
        for i in range(len(marks)):
            if not marks[i]["common"]:
                continue
            cum = 0
            for j in range(i + 1, len(marks)):
                cum += _cjk_count(sents[j])
                if cum > max_gap:
                    break
                if marks[j]["unusual"]:
                    candidates.append({
                        "verb": verb,
                        "sent1_idx": i, "sent1": sents[i],
                        "obj1": marks[i]["common"][0],
                        "sent2_idx": j, "sent2": sents[j],
                        "obj2": marks[j]["unusual"][0],
                        "gap_cjk": cum,
                    })
                    # 一对足够·跳出避免一 common 配多 unusual 爆样本
                    break
    # 同句对去重
    seen = set()
    deduped: list[dict] = []
    for c in candidates:
        key = (c["sent1_idx"], c["sent2_idx"], c["verb"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "zeugma", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    lexicon = load_lexicon()
    pairs = detect_zeugma_pairs(text, lexicon)
    count = len(pairs)
    per_kcjk = round(count / (cjk / 1000.0), 3) if cjk else 0.0

    baseline_info = _read_author_baseline(project_root) or {}
    baseline = float(baseline_info.get("baseline", DEFAULT_BASELINE))
    sigma = float(baseline_info.get("std", 0.0))
    baseline_source = baseline_info.get("source", "fallback")
    over_thresh = baseline + (sigma if sigma > 0 else DEFAULT_OVER_DELTA)
    under_min_baseline = DEFAULT_UNDER_MIN_BASELINE

    out.update({
        "cjk": cjk,
        "lexicon_placeholder": bool(lexicon.get("_placeholder")),
        "zeugma_count": count,
        "zeugma_per_kcjk": per_kcjk,
        "baseline": baseline,
        "baseline_source": baseline_source,
        "thresholds": {
            "over_per_kcjk": round(over_thresh, 3),
            "under_min_baseline_per_kcjk": under_min_baseline,
            "detected_report_min": DEFAULT_DETECTED_REPORT_MIN,
        },
        "samples": pairs[:5],  # 头 5 例·避免 prompt 噪声
    })

    flags = []
    if count >= DEFAULT_DETECTED_REPORT_MIN:
        flags.append({"code": ISSUE_CODE_DETECTED,
                      "msg": f"拈连命中 {count} 例·密度={per_kcjk}/kCJK"})
    if per_kcjk > over_thresh and count >= 2:
        flags.append({"code": ISSUE_CODE_OVER,
                      "msg": f"拈连密度={per_kcjk}/kCJK > 作者档+σ={round(over_thresh, 3)}·堆叠影响节奏"})
    if baseline >= under_min_baseline and count == 0:
        flags.append({"code": ISSUE_CODE_UNDER,
                      "msg": f"作者档拈连基线={baseline}/kCJK ≥ {under_min_baseline}(搞笑流) 但本 cluster 0 命中·喜剧基底丢失"})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "zeugma", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "陈望道《修辞学发凡》拈连格·R22 W10 Batch-FF·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] zeugma: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="拈连(zeugma)单格 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
