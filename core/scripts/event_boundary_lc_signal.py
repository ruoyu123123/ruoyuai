#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""event_boundary_lc_signal.py — 事件边界锐 vs 钝信号 advisory · R23 W11 Batch-HH · P1

【缺口 · 神经 Locus Coeruleus-Norepinephrine（LC-NE）系统】LC-NE 在事件边界
释放 NE → 海马脉冲长记编码（Yu&Dayan 2005 / Bouret 2015）。同 cluster 内
场景切（小边界 → dull）与 cluster / volume_finale 切（大边界 → sharp）
神经层应有不同 surprise/锐度，但 writer / judge 全程无字段约束 → 末段
PE 路径同一调子。

【新字段 · cluster_emergence_engine brief.event_boundary_sharpness ∈ {sharp, dull, default}】
  · volume_finale 强制 sharp
  · 同 cluster 内 scene 切（默认）= dull
  · default = 不指定（默认走 dull 软目标）
  · build_manifest 透传

【本 scanner】扫章末/cluster 末 200 CJK surprise 代理：
  - PE 关键词命中（belief_update_alignment._SURPRISE_LEX subset）
  - 标点突变（！？密集出现 / —— / ……）
  - 主语切换（章末新主语 lead vs 末段 mid）
  - 综合 score ∈ [0, 1]

  - sharpness=sharp 期望 score >= SHARP_THRESHOLD
  - sharpness=dull 期望 score <= DULL_THRESHOLD

【2026-07-02 接入真模型】末段锐度分数优先混入已训练部署的 surprisal_gpt2（经 nn_surprisal_bridge /
feature_cache 二选一）算末段 mean/max surprisal 归一后按 0.4 权重混进词典拼分（词典基线分保留
0.6 权重·`source` 标注切换）；RUOYU_NN_SURPRISAL 未开启/模型未命中时 100% 走词典拼分（不变）。

【三 advisory】
  · EVENT_BOUNDARY_TOO_DULL    — sharp 期望但末段锐度不足
  · EVENT_BOUNDARY_TOO_SHARP   — dull 期望但末段过尖锐
  · EVENT_BOUNDARY_DEFAULT     — 字段缺失（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  EVENT_BOUNDARY_* 绝不进 audit_hub.HARD_GATE_CODES。

env EVENT_BOUNDARY_LC_MODE: off / shadow（默认） / active
用法: python event_boundary_lc_signal.py <draft> [--manifest <path>] [--sharpness <sharp|dull|default>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_TOO_DULL = "EVENT_BOUNDARY_TOO_DULL"
ISSUE_CODE_TOO_SHARP = "EVENT_BOUNDARY_TOO_SHARP"
ISSUE_CODE_DEFAULT = "EVENT_BOUNDARY_DEFAULT"

VALID_SHARPNESS = {"sharp", "dull", "default"}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

TAIL_CJK = 200
TAIL_MIN_CJK = 80

SHARP_THRESHOLD = 0.45
DULL_THRESHOLD = 0.55

# 末段 surprise 代理词（与 belief_update_alignment._SURPRISE_LEX **完全无交集**）
# belief: 原来/竟然/没想到/万万没想到/出乎意料/翻转/反转/真相竟是/意外/突如其来/
#         猛地一惊/瞳孔骤缩/猛然/一瞬间/陡然/蓦地/霎时/刹那/陡变/骤变
# event_boundary: 边界标记词独立词典（动作锐 / 视觉锐 / 转向锐 / 物理锐）
_PE_LEX = ["猛地", "倏然", "豁然", "电光石火", "措手不及",
            "豁地", "啪嗒", "咔嚓", "猝不及防", "戛然而止",
            "戛然", "霍然", "蓦然回首", "登时", "顿时拔起",
            "蓦然"]
_EMOTION_PUNCT_RE = re.compile(r"[！？]+|……+|—{2,}")
_LEAD_NAME_RE = re.compile(r"[一-鿿]{2,4}")


def _mode() -> str:
    m = (os.environ.get("EVENT_BOUNDARY_LC_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _tail_slice(text: str, target_cjk: int = TAIL_CJK) -> str:
    n = len(text)
    if n == 0:
        return ""
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if _cjk_count(text[mid:]) >= target_cjk:
            lo = mid + 1
        else:
            hi = mid
    start = max(0, lo - 1)
    return text[start:]


def _read_sharpness(manifest_path) -> tuple[str | None, bool]:
    """返回（sharpness, is_volume_finale）"""
    if not manifest_path:
        return None, False
    try:
        mf = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, False
    if not isinstance(mf, dict):
        return None, False
    finale = False
    ec = mf.get("event_cluster_context") or {}
    if isinstance(ec, dict):
        if ec.get("is_volume_finale"):
            finale = True
        v = ec.get("event_boundary_sharpness")
        if isinstance(v, str) and v.strip().lower() in VALID_SHARPNESS:
            return v.strip().lower(), finale
    v = mf.get("event_boundary_sharpness")
    if isinstance(v, str) and v.strip().lower() in VALID_SHARPNESS:
        return v.strip().lower(), finale
    return None, finale


def _predict_surprisal_batch(texts: list[str]) -> "list[dict | None]":
    """批量取完整 surprisal 统计量(经 FeatureStore 缓存优先→退 nn_surprisal_bridge 直连)。
    全不可用 → 全 None(调用方整体回退词典拼分·不变)。"""
    if not texts:
        return []
    preds = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled as feature_store_enabled
        if feature_store_enabled():
            preds = FeatureStore.get().compute_surprisal_batch(texts)
    except Exception:  # noqa: BLE001 FeatureStore 故障 → 退 bridge，绝不影响 scanner
        preds = None
    if preds is None:
        try:
            import nn_surprisal_bridge as bridge
        except ImportError:
            return [None] * len(texts)
        preds = bridge.predict_batch(texts)
    if len(preds) != len(texts):
        return [None] * len(texts)
    return preds


def _predict_surprisal_one(text: str) -> "dict | None":
    return _predict_surprisal_batch([text])[0]


def _signal_score(tail: str) -> dict:
    """末段锐度：词典/标点/主语切换拼分(始终计算) + 真模型 surprisal 归一分(可用时按 0.4 权重混入)。
    模型不可用 → score 与词典基线完全一致(零回归)。"""
    cjk = _cjk_count(tail) or 1
    pe_hits = sum(tail.count(w) for w in _PE_LEX)
    punct_hits = len(_EMOTION_PUNCT_RE.findall(tail))
    head_token = ""
    m = _LEAD_NAME_RE.search(tail[:60])
    if m:
        head_token = m.group(0)
    mid_token = ""
    mid_slice = tail[len(tail) // 2: len(tail) // 2 + 60]
    m2 = _LEAD_NAME_RE.search(mid_slice)
    if m2:
        mid_token = m2.group(0)
    subj_switch = 1 if head_token and mid_token and head_token != mid_token else 0

    pe_per_1k = pe_hits / (cjk / 1000)
    punct_per_1k = punct_hits / (cjk / 1000)
    # 词典基线分数：sharp 要重 PE + 标点突变（始终计算·模型不可用时即最终 score）
    lexicon_score = min(1.0,
                0.5 * min(1.0, pe_per_1k / 8.0) +
                0.3 * min(1.0, punct_per_1k / 15.0) +
                0.2 * subj_switch)

    source = "heuristic"
    surprisal_norm = None
    score = lexicon_score
    model_stat = _predict_surprisal_one(tail)
    if model_stat is not None:
        mean_s = model_stat.get("mean_surprisal")
        max_s = model_stat.get("max_surprisal")
        if mean_s is not None and max_s is not None:
            # base-2 bits 量纲(surprisal_infer.py base_two=True)·mean/max 各半归一(启发式上限)
            surprisal_norm = round(min(1.0,
                        0.6 * min(1.0, mean_s / 10.0) +
                        0.4 * min(1.0, max_s / 18.0)), 4)
            score = round(0.6 * lexicon_score + 0.4 * surprisal_norm, 4)
            source = "model"

    return {
        "pe_hits": pe_hits,
        "punct_hits": punct_hits,
        "subject_switch": subj_switch,
        "pe_per_1k": round(pe_per_1k, 3),
        "punct_per_1k": round(punct_per_1k, 3),
        "lexicon_score": round(lexicon_score, 3),
        "surprisal_norm": surprisal_norm,
        "score": round(score, 3),
        "source": source,
    }


def scan(draft_path, manifest_path=None, sharpness=None) -> dict:
    mode = _mode()
    out = {"scanner": "event_boundary_lc_signal", "schema_version": "1.0",
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
    if cjk < 1500:
        out["note"] = "草稿太短·跳过"
        return out

    tail = _tail_slice(text, TAIL_CJK)
    tail_cjk = _cjk_count(tail)
    if tail_cjk < TAIL_MIN_CJK:
        out["note"] = "末段不足·跳过"
        return out

    explicit_sharpness = (sharpness.strip().lower()
                          if isinstance(sharpness, str)
                          and sharpness.strip().lower() in VALID_SHARPNESS
                          else None)
    declared, is_finale = _read_sharpness(manifest_path)
    final_sharpness = explicit_sharpness or declared
    # volume_finale 强制 sharp（即使 manifest 写其他·北极星④格式服从语义）
    if is_finale:
        final_sharpness = "sharp"

    sig = _signal_score(tail)
    out.update({
        "cjk": cjk,
        "tail_cjk": tail_cjk,
        "declared_sharpness": declared,
        "is_volume_finale": is_finale,
        "effective_sharpness": final_sharpness,
        "signal": sig,
        "thresholds": {"sharp_min": SHARP_THRESHOLD, "dull_max": DULL_THRESHOLD},
    })

    flags = []
    if final_sharpness == "sharp" and sig["score"] < SHARP_THRESHOLD:
        flags.append({
            "code": ISSUE_CODE_TOO_DULL,
            "msg": (f"sharpness=sharp{'(volume_finale)' if is_finale else ''}"
                    f"·末段锐度 {sig['score']:.2f} < {SHARP_THRESHOLD}·LC-NE 边界标记不足"),
            "severity": "minor",
        })
    elif final_sharpness == "dull" and sig["score"] > DULL_THRESHOLD:
        flags.append({
            "code": ISSUE_CODE_TOO_SHARP,
            "msg": (f"sharpness=dull·末段锐度 {sig['score']:.2f} > {DULL_THRESHOLD}"
                    f"·同 cluster 内 scene 切不应当作 cluster 末"),
            "severity": "minor",
        })
    elif final_sharpness is None:
        flags.append({
            "code": ISSUE_CODE_DEFAULT,
            "msg": "manifest 缺 event_boundary_sharpness 字段·走默认 dull 软目标",
            "severity": "info",
        })

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "event_boundary_lc",
                    "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "neural LC-NE 边界·R23 W11 Batch-HH·advisory·绝不 hard_gate"})
            out["verdict"] = ("FAIL_MINOR" if any(
                v["severity"] == "minor" for v in out["violations"]) else "PASS")
            out["warning"] = msg
        else:
            print(f"[SHADOW] event_boundary_lc: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="事件边界 LC-NE advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--sharpness", default=None,
                    help="显式覆盖 event_boundary_sharpness ∈ {sharp, dull, default}")
    args = ap.parse_args()
    rep = scan(args.draft_path, args.manifest, args.sharpness)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
