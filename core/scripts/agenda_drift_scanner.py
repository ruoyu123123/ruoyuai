#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agenda_drift_scanner.py — writer intent agenda drift · R24 W12 Batch-KK · P1

【缺口 · AGI 协作 reflexivity / 反算法议程占领】
配合 writer_intent_anchor.py：草稿落地后比对盲意图卡 4 维（want/antagonist/
stake/tone-word）与草稿正文的 char-Jaccard 相似度（占位用 char Jaccard 替身·
真版可换 embedding cosine）。任一维度 < 0.62 → WRITER_INTENT_AGENDA_DRIFT。

【做法 · 确定性 · 零 LLM/零联网】
  · 读 writer_intent_anchor.load_anchor(project, cluster_key) → 4 字段
  · 草稿 strip CHANGES → 全文 char 集合 S_draft
  · 对每 field：char 集 S_f → Jaccard = |S_f ∩ S_draft| / |S_f ∪ S_draft 在 S_f|
    即「字段中字符在草稿中出现的覆盖率」（非对称 · 解决草稿远长于字段失真）
  · 任一字段 < 0.62 → WRITER_INTENT_AGENDA_DRIFT advisory（minor）
  · 缺 anchor → WRITER_INTENT_NO_ANCHOR info

【三 advisory】
  · WRITER_INTENT_AGENDA_DRIFT     — 任一维度覆盖率 < 0.62
  · WRITER_INTENT_NO_ANCHOR        — 缺盲意图卡 / SHA-256 失败（info）
  · WRITER_INTENT_OK               — 全 4 维度 ≥ 0.62（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  WRITER_INTENT_* 绝不进 audit_hub.HARD_GATE_CODES。

env AGENDA_DRIFT_MODE: off / shadow（默认） / active
用法: python agenda_drift_scanner.py <draft> --project <root> --cluster <key>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE_DRIFT = "WRITER_INTENT_AGENDA_DRIFT"
ISSUE_CODE_NO_ANCHOR = "WRITER_INTENT_NO_ANCHOR"
ISSUE_CODE_OK = "WRITER_INTENT_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

DRIFT_THRESHOLD = 0.62

_FIELDS = ("want", "antagonist", "stake", "tone_word")


def _mode() -> str:
    m = (os.environ.get("AGENDA_DRIFT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_charset(text: str) -> set:
    """取 CJK 字符集（去标点 / 空白 / 非 CJK · 占位真版可换 embedding）"""
    return {ch for ch in text if "一" <= ch <= "鿿"}


def _coverage(field_text: str, draft_text: str) -> float:
    """非对称覆盖率：field 的 char 在 draft 中出现的比例 → [0, 1]
    field 多字均落到 draft = 1.0；全不落 = 0.0。
    """
    f_set = _cjk_charset(field_text)
    d_set = _cjk_charset(draft_text)
    if not f_set:
        return 1.0  # 空字段不算 drift
    inter = f_set & d_set
    return len(inter) / len(f_set)


def scan(draft_path, project_root, cluster_key) -> dict:
    mode = _mode()
    out = {
        "scanner": "agenda_drift_scanner",
        "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
    }
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(raw)

    # 延迟 import 让单测能 monkeypatch
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import writer_intent_anchor as wia
    anchor = wia.load_anchor(project_root, cluster_key) if project_root else None

    if not anchor:
        if mode == "active":
            out["violations"].append({
                "kind": "agenda_drift",
                "severity": "info",
                "code": ISSUE_CODE_NO_ANCHOR,
                "message": ("缺 writer_intent anchor 或 SHA-256 校验失败"
                            "·跳过 drift 对账"),
                "_doc": "R24 W12 Batch-KK·advisory·绝不 hard_gate",
            })
        out["note"] = "no_anchor"
        out["violations_count"] = len(out["violations"])
        return out

    field_scores = {}
    drift_fields = []
    for f in _FIELDS:
        val = anchor.get(f, "")
        score = round(_coverage(str(val), draft), 4)
        field_scores[f] = score
        if score < DRIFT_THRESHOLD:
            drift_fields.append({"field": f, "score": score, "text": str(val)})

    out.update({
        "cluster_key": cluster_key,
        "anchor_sha256": anchor.get("_sha256"),
        "field_scores": field_scores,
        "drift_threshold": DRIFT_THRESHOLD,
        "drift_fields": drift_fields,
    })

    if drift_fields:
        msg = (f"writer intent agenda drift: {len(drift_fields)} 维 < "
               f"{DRIFT_THRESHOLD} → "
               + "·".join(f"{d['field']}={d['score']}" for d in drift_fields))
        if mode == "active":
            out["violations"].append({
                "kind": "agenda_drift", "severity": "minor",
                "code": ISSUE_CODE_DRIFT,
                "message": msg,
                "drift_fields": drift_fields,
                "_doc": ("R24 W12 Batch-KK·reflexivity 反议程·advisory"
                         "·绝不 hard_gate"),
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        elif mode == "shadow":
            print(f"[SHADOW] agenda_drift: {msg} — 不上报", file=sys.stderr)
    elif mode == "active":
        out["violations"].append({
            "kind": "agenda_drift", "severity": "info",
            "code": ISSUE_CODE_OK,
            "message": f"4 维度全 ≥ {DRIFT_THRESHOLD}·议程对齐",
            "_doc": "R24 W12 Batch-KK·advisory·info",
        })

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="writer intent agenda drift advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", required=True)
    ap.add_argument("--cluster", required=True, help="cluster_key (e.g. 001)")
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
