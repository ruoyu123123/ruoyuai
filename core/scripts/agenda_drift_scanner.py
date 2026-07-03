#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agenda_drift_scanner.py — writer intent agenda drift · R24 W12 Batch-KK · P1

【缺口 · AGI 协作 reflexivity / 反算法议程占领】
配合 writer_intent_anchor.py：草稿落地后比对盲意图卡 4 维（want/antagonist/
stake/tone-word）与草稿正文的相似度。任一维度 < 0.62 → WRITER_INTENT_AGENDA_DRIFT。

【做法 · 双路（2026-07-01 语义路补齐 · 取代字面重叠对同义改写零容错）】
  · 读 writer_intent_anchor.load_anchor(project, cluster_key) → 4 字段
  · 草稿 strip CHANGES
  · 真语义 embedding 后端就绪（_has_real_embedding_backend()）时：4 字段文本与草稿整体
    分别 compute_embedding，用余弦相似度做覆盖分——同义改写（如「决战」vs「殊死一战」
    字面零重叠）也能正确识别为一致；embedding 不可用/维度不一致/字段为空 → 回退字面
    Jaccard，绝不拿默认 hash 假语义袋冒充。
  · 默认（无真后端配置）· 全字面 Jaccard：char 集 S_f → Jaccard = |S_f ∩ S_draft| / |S_f|
    即「字段中字符在草稿中出现的覆盖率」（非对称 · 解决草稿远长于字段失真）
  · 任一字段 < 0.62 → WRITER_INTENT_AGENDA_DRIFT advisory（minor）
  · 缺 anchor → WRITER_INTENT_NO_ANCHOR info
  · 输出 match_method 字段标注本次实际用的是 "embedding_cosine" 还是 "char_jaccard"

【三 advisory】
  · WRITER_INTENT_AGENDA_DRIFT     — 任一维度覆盖率 < 0.62
  · WRITER_INTENT_NO_ANCHOR        — 缺盲意图卡 / SHA-256 失败（info）
  · WRITER_INTENT_OK               — 全 4 维度 ≥ 0.62（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  WRITER_INTENT_* 绝不进 audit_hub.HARD_GATE_CODES。

env AGENDA_DRIFT_MODE: off / shadow（默认） / active
env EMBED_BACKEND（embedding_store 消费）非空非 hash · 或配 .env GEN_EMBED__* → 启用语义路
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


def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。

    与 topic_drift_scanner._has_real_embedding_backend 同口径（本仓约定：每个消费
    embedding 的 scanner 自带一份，不互相 import）。也检查 .env 的 GEN_EMBED__* API 配置
    （由 embedding_store._load_embed_profile 消费）。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_charset(text: str) -> set:
    """取 CJK 字符集（去标点 / 空白 / 非 CJK · 字面 Jaccard 路径专用，语义路径见 _semantic_coverage）"""
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


def _safe_compute_embedding(text: str):
    """草稿整体 embedding（真后端就绪时只算一次·4 字段复用）。

    embedding_store 不可用/编码异常/空结果 → None（调用方回退字面 Jaccard·绝不用
    降级 hash 冒充真语义）。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import compute_embedding
        emb = compute_embedding(text)
        return emb if emb else None
    except Exception:
        return None


def _semantic_coverage(field_text: str, draft_embedding) -> "float | None":
    """真语义覆盖分：意图卡字段文本 embedding 与草稿整体 embedding 的余弦相似度。

    字段为空 / 无草稿 embedding / 字段编码失败 / 维度不一致 → None
    （调用方回退 _coverage 字面 Jaccard）。
    """
    if not field_text.strip() or draft_embedding is None:
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import compute_embedding, cosine_similarity
        field_emb = compute_embedding(field_text)
    except Exception:
        return None
    if not field_emb or len(field_emb) != len(draft_embedding):
        return None
    return cosine_similarity(field_emb, draft_embedding)


def _safe_prefetch(texts: list) -> None:
    """批量预热 embedding 缓存（Wave-4 2026-07-03）：草稿 + 4 字段一次性灌缓存，随后
    draft/field 的逐条 compute_embedding 全部命中。prefetch 失败不影响主流程（回退逐条现算）。"""
    if not texts:
        return
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import prefetch_embeddings
        prefetch_embeddings(texts)
    except Exception:
        pass


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

    # 真语义后端就绪 → 草稿整体只编码一次（4 字段复用余弦相似度）；否则保留字面 Jaccard 原样
    match_method = "char_jaccard"
    draft_embedding = None
    if _has_real_embedding_backend():
        # Wave-4 2026-07-03：草稿 + 4 字段一次性批量预热·下面 draft/field embedding 全部命中缓存
        field_vals = [str(anchor.get(f, "")) for f in _FIELDS]
        _safe_prefetch([draft] + [v for v in field_vals if v.strip()])
        draft_embedding = _safe_compute_embedding(draft)
        if draft_embedding is not None:
            match_method = "embedding_cosine"

    field_scores = {}
    drift_fields = []
    for f in _FIELDS:
        val = str(anchor.get(f, ""))
        sim = _semantic_coverage(val, draft_embedding)
        score = round(sim, 4) if sim is not None else round(_coverage(val, draft), 4)
        field_scores[f] = score
        if score < DRIFT_THRESHOLD:
            drift_fields.append({"field": f, "score": score, "text": val})

    out.update({
        "cluster_key": cluster_key,
        "anchor_sha256": anchor.get("_sha256"),
        "field_scores": field_scores,
        "drift_threshold": DRIFT_THRESHOLD,
        "drift_fields": drift_fields,
        "match_method": match_method,
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
