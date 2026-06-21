#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""butler_yearning_4layer_scanner.py — Robert Olen Butler 4 层 yearning advisory · cluster · 2026-06-21 R20 W9 Batch-BB · P2

【缺口 · R20 MFA id 2】Butler《From Where You Dream》核心命题：fiction = yearning。
四层 yearning 嵌套：
  · self     — 想成为本来面目（变回自己）
  · identity — 想被社会承认（出头/正名）
  · place    — 想归属一个地方（回家/故乡）
  · connection — 想和某人重新连接（重逢/团圆）

LLM 草稿默认靠「情绪词」描写情绪 → yearning 全空或单层垄断。

【做法 · 确定性 · 零联网】
  · 词典 core/data/butler_yearning_lexicon_zh.json 四桶（_placeholder=true）
  · 按场景（双换行段落聚合 / scene_storyboard 提示）拆 yearning_hits
  · 每桶 hits + scene_level 标 dominant layer

【两 advisory】
  · SCENE_YEARNING_ABSENT — 总 yearning_hits/kCJK < 兜底 1.5（作者档可改）
  · YEARNING_LAYER_MONOTONE — 任一桶占比 > 0.85 且 active_buckets ≥ 1（单层垄断）
  · 作者档 author_yearning_baseline.dominant_layer = "<bucket>" 时 LAYER_MONOTONE 让位（明示风格）

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  SCENE_YEARNING_ABSENT / YEARNING_LAYER_MONOTONE 绝不进 audit_hub.HARD_GATE_CODES。

env BUTLER_YEARNING_MODE: off / shadow(默认) / active
用法: python butler_yearning_4layer_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_ABSENT = "SCENE_YEARNING_ABSENT"
ISSUE_CODE_MONOTONE = "YEARNING_LAYER_MONOTONE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "butler_yearning_lexicon_zh.json"

DEFAULT_YEARNING_PER_KCJK_LOW = 1.5
DEFAULT_MONOTONE_HIGH_SHARE = 0.85
DEFAULT_MIN_HITS_FOR_MONOTONE = 6


def _mode() -> str:
    m = (os.environ.get("BUTLER_YEARNING_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_lexicon() -> dict:
    try:
        return json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "_placeholder": True,
            "buckets": {
                "self_yearning": ["想成为", "变回自己"],
                "identity_yearning": ["证明自己", "想被承认"],
                "place_yearning": ["回家", "想回"],
                "connection_yearning": ["想见", "重逢"],
            }
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
            bb = obj.get("author_yearning_baseline")
            if isinstance(bb, dict):
                return bb
    return None


def _count_bucket_hits(text: str, terms: list[str]) -> int:
    total = 0
    for term in terms:
        if not term:
            continue
        total += text.count(term)
    return total


def _split_scenes(text: str) -> list[str]:
    parts = re.split(r"\n{2,}", text)
    return [p for p in parts if p.strip()]


def _scene_dominant_layer(scene: str, buckets: dict) -> tuple[str | None, dict]:
    hits = {bn: _count_bucket_hits(scene, terms) for bn, terms in buckets.items()}
    total = sum(hits.values())
    if total == 0:
        return None, hits
    dom_bucket, dom_hits = max(hits.items(), key=lambda kv: kv[1])
    return dom_bucket, hits


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "butler_yearning_4layer", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "_lexicon_placeholder": True}
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

    lex = _load_lexicon()
    buckets = lex.get("buckets") or {}
    bucket_hits = {bn: _count_bucket_hits(text, terms) for bn, terms in buckets.items()}
    total_hits = sum(bucket_hits.values())
    per_kcjk = round(total_hits / (cjk / 1000.0), 3) if cjk else 0.0
    bucket_share = {bn: (h / total_hits if total_hits else 0.0) for bn, h in bucket_hits.items()}

    scenes = _split_scenes(text)
    scene_layers = []
    scene_absent_count = 0
    for s in scenes:
        if _cjk_count(s) < 80:
            continue
        dom, h = _scene_dominant_layer(s, buckets)
        if dom is None:
            scene_absent_count += 1
        scene_layers.append({"dominant": dom, "hits": h})
    scenes_eval = len(scene_layers)
    scene_absent_share = round(scene_absent_count / max(1, scenes_eval), 3)

    baseline = _read_author_baseline(project_root)
    per_kcjk_low = DEFAULT_YEARNING_PER_KCJK_LOW
    monotone_high = DEFAULT_MONOTONE_HIGH_SHARE
    declared_dominant = None
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        if isinstance(baseline.get("per_kcjk_low"), (int, float)):
            per_kcjk_low = float(baseline["per_kcjk_low"])
        if isinstance(baseline.get("monotone_high_share"), (int, float)):
            monotone_high = float(baseline["monotone_high_share"])
        if isinstance(baseline.get("dominant_layer"), str):
            declared_dominant = baseline["dominant_layer"]

    out.update({
        "cjk": cjk,
        "bucket_hits": bucket_hits,
        "bucket_share": {bn: round(v, 3) for bn, v in bucket_share.items()},
        "yearning_per_kcjk": per_kcjk,
        "total_yearning_hits": total_hits,
        "scenes_evaluated": scenes_eval,
        "scene_absent_count": scene_absent_count,
        "scene_absent_share": scene_absent_share,
        "baseline_source": baseline_source,
        "declared_dominant_layer": declared_dominant,
        "baseline": {
            "per_kcjk_low": per_kcjk_low,
            "monotone_high_share": monotone_high,
        }
    })

    flags = []
    if per_kcjk < per_kcjk_low:
        flags.append({"code": ISSUE_CODE_ABSENT,
                      "msg": f"yearning_per_kcjk={per_kcjk} < {per_kcjk_low}·场景缺渴望层·"
                             f"scene_absent_share={scene_absent_share}"})

    # 单层垄断：作者档明示 dominant 时让位
    if total_hits >= DEFAULT_MIN_HITS_FOR_MONOTONE and declared_dominant is None:
        for bn, share in bucket_share.items():
            if share > monotone_high:
                flags.append({"code": ISSUE_CODE_MONOTONE,
                              "msg": f"{bn} 占比={share:.2f} > {monotone_high}·单层垄断·建议混合 4 层"})
                break

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "butler_yearning_4layer", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Butler《From Where You Dream》 4 层 yearning · advisory · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] butler_yearning: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Butler 4 层 yearning advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
