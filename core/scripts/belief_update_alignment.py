#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""belief_update_alignment.py — 信念更新 vs 末段惊讶收束对齐 advisory · R23 W11 Batch-HH · P1

【缺口 · 神经 mnemonic Prediction Error（PE）】神经美学：读者长期记忆形成靠 PE
（违反前置预期）。当前 brief / writer / judge 路径无字段告诉系统「本 cluster
是要『update 读者信念（强 PE 收束）』还是『preserve 信念（红鲱鱼 / 推理悬念
保留）』」→ writer 默认产 average closure，judge 默认放过，PE 路径全 close-loop。

【新字段 · cluster_emergence_engine brief.belief_update_intent ∈ {preserve, update}】
  · cluster_emergence_engine candidate 默认 None（不强制）
  · build_manifest 透传至 writer + judge manifest
  · 本 scanner 末 1500 CJK 扫 PE 信号（惊讶代理词 / 标点突变 / 反转关键词）
    - update + 末段 PE 信号弱 → BELIEF_UPDATE_NO_SURPRISE
    - preserve + 末段 PE 信号过强 → BELIEF_PRESERVE_RED_HERRING_KILLED
    - 无 intent 或 None → 跳过（北极星②不主张）

【2026-07-02 接入真模型】末段惊讶分数优先混入已训练部署的 surprisal_gpt2（经 nn_surprisal_bridge /
feature_cache 二选一）算末段 mean/max surprisal 归一后按 0.4 权重混进词典拼分（词典基线分保留
0.6 权重·`source` 标注切换）；RUOYU_NN_SURPRISAL 未开启/模型未命中时 100% 走词典拼分（不变）。

【三 advisory】
  · BELIEF_UPDATE_NO_SURPRISE              — update intent 但末段无惊讶收束
  · BELIEF_PRESERVE_RED_HERRING_KILLED     — preserve intent 但末段强反转误伤红鲱鱼
  · BELIEF_INTENT_MISSING                  — manifest 字段缺失（info 旁注，不强制）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  BELIEF_* 绝不进 audit_hub.HARD_GATE_CODES。

env BELIEF_UPDATE_ALIGNMENT_MODE: off / shadow（默认） / active
用法: python belief_update_alignment.py <draft> [--manifest <path>] [--intent <preserve|update>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_NO_SURPRISE = "BELIEF_UPDATE_NO_SURPRISE"
ISSUE_CODE_RED_HERRING = "BELIEF_PRESERVE_RED_HERRING_KILLED"
ISSUE_CODE_INTENT_MISSING = "BELIEF_INTENT_MISSING"

VALID_INTENTS = {"preserve", "update"}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

TAIL_CJK = 1500
TAIL_MIN_CJK = 300

# PE / 惊讶 / 反转代理词（高确定性·剔除多义）
_SURPRISE_LEX = [
    "原来", "竟然", "没想到", "万万没想到", "出乎意料",
    "翻转", "反转", "真相竟是", "意外", "突如其来",
    "猛地一惊", "瞳孔骤缩", "猛然", "一瞬间", "陡然",
    "蓦地", "霎时", "刹那", "陡变", "骤变",
]
# 排除「红鲱鱼合理保留」的预期信号（高 PE 但 update 应有的真反转）
_REAL_TWIST_LEX = ["真相", "真凶", "幕后", "假象", "假死",
                    "其实是", "竟是", "原来是", "始作俑者"]
# 标点突变代理（密集 ！？……）
_EMOTION_PUNCT_RE = re.compile(r"[！？]+|……+|—{2,}")
# 主语切换代理（末段段首主语 vs 末段中段主语）
_LEAD_NAME_RE = re.compile(r"[一-鿿]{2,4}")


def _mode() -> str:
    m = (os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _tail_slice(text: str, target_cjk: int = TAIL_CJK) -> str:
    n = len(text)
    if n == 0:
        return ""
    lo, hi = 0, n
    # 二分定位末段恰好 target_cjk CJK
    while lo < hi:
        mid = (lo + hi) // 2
        if _cjk_count(text[mid:]) >= target_cjk:
            lo = mid + 1
        else:
            hi = mid
    start = max(0, lo - 1)
    return text[start:]


def _read_intent_from_manifest(manifest_path) -> str | None:
    if not manifest_path:
        return None
    try:
        mf = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(mf, dict):
        return None
    ec = mf.get("event_cluster_context")
    if isinstance(ec, dict):
        v = ec.get("belief_update_intent")
        if isinstance(v, str) and v.strip().lower() in VALID_INTENTS:
            return v.strip().lower()
    v = mf.get("belief_update_intent")
    if isinstance(v, str) and v.strip().lower() in VALID_INTENTS:
        return v.strip().lower()
    return None


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


def _surprise_signal_score(text: str) -> dict:
    """末段惊讶信号三项归一组合分（0..1）· 词典基线(始终计算) + 真模型 surprisal 归一分(可用时
    按 0.4 权重混入·mean/max 各半)。模型不可用 → score 与词典基线完全一致(零回归)。"""
    cjk = _cjk_count(text) or 1
    surprise_hits = sum(text.count(w) for w in _SURPRISE_LEX)
    twist_hits = sum(text.count(w) for w in _REAL_TWIST_LEX)
    punct_burst = len(_EMOTION_PUNCT_RE.findall(text))
    # 主语切换代理：段首 vs 段中（取末段第一 4 字 token 与中段第一 4 字 token 比较）
    head_token = ""
    m = _LEAD_NAME_RE.search(text[:80])
    if m:
        head_token = m.group(0)
    mid_token = ""
    mid_slice = text[len(text) // 2: len(text) // 2 + 80]
    m2 = _LEAD_NAME_RE.search(mid_slice)
    if m2:
        mid_token = m2.group(0)
    subj_switch = 1 if head_token and mid_token and head_token != mid_token else 0

    # 归一密度（per 1k CJK）
    surprise_per_1k = surprise_hits / (cjk / 1000)
    twist_per_1k = twist_hits / (cjk / 1000)
    punct_per_1k = punct_burst / (cjk / 1000)
    # 词典基线分数（按 PE 权重·始终计算·模型不可用时即最终 score）
    lexicon_score = min(1.0,
                0.4 * min(1.0, surprise_per_1k / 5.0) +
                0.3 * min(1.0, twist_per_1k / 3.0) +
                0.2 * min(1.0, punct_per_1k / 8.0) +
                0.1 * subj_switch)

    source = "heuristic"
    surprisal_norm = None
    score = lexicon_score
    model_stat = _predict_surprisal_one(text)
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
        "surprise_hits": surprise_hits,
        "twist_hits": twist_hits,
        "punct_burst": punct_burst,
        "subject_switch": subj_switch,
        "surprise_per_1k": round(surprise_per_1k, 3),
        "twist_per_1k": round(twist_per_1k, 3),
        "punct_per_1k": round(punct_per_1k, 3),
        "lexicon_score": round(lexicon_score, 3),
        "surprisal_norm": surprisal_norm,
        "score": round(score, 3),
        "source": source,
    }


SURPRISE_LOW = 0.20    # update 期望 score >= 0.20
SURPRISE_HIGH = 0.55   # preserve 期望 score <= 0.55


def scan(draft_path, manifest_path=None, intent=None) -> dict:
    mode = _mode()
    out = {"scanner": "belief_update_alignment", "schema_version": "1.0",
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
    if cjk < 2000:
        out["note"] = "草稿太短·跳过"
        return out

    tail = _tail_slice(text, TAIL_CJK)
    tail_cjk = _cjk_count(tail)
    if tail_cjk < TAIL_MIN_CJK:
        out["note"] = "末段不足·跳过"
        return out

    declared_intent = intent
    if declared_intent and declared_intent.strip().lower() in VALID_INTENTS:
        declared_intent = declared_intent.strip().lower()
    else:
        declared_intent = _read_intent_from_manifest(manifest_path)

    sig = _surprise_signal_score(tail)
    out.update({
        "cjk": cjk,
        "tail_cjk": tail_cjk,
        "declared_intent": declared_intent,
        "signal": sig,
        "thresholds": {"low": SURPRISE_LOW, "high": SURPRISE_HIGH},
    })

    flags = []
    if declared_intent == "update" and sig["score"] < SURPRISE_LOW:
        flags.append({
            "code": ISSUE_CODE_NO_SURPRISE,
            "msg": (f"belief_update_intent=update·末段惊讶分 {sig['score']:.2f} < {SURPRISE_LOW}"
                    f"·读者长记 PE 路径过弱"),
            "severity": "minor",
        })
    elif declared_intent == "preserve" and sig["score"] > SURPRISE_HIGH:
        flags.append({
            "code": ISSUE_CODE_RED_HERRING,
            "msg": (f"belief_update_intent=preserve·末段惊讶分 {sig['score']:.2f} > {SURPRISE_HIGH}"
                    f"·过强反转可能误伤红鲱鱼"),
            "severity": "minor",
        })
    elif declared_intent is None:
        flags.append({
            "code": ISSUE_CODE_INTENT_MISSING,
            "msg": "manifest 缺 belief_update_intent 字段·跳过对齐检测",
            "severity": "info",
        })

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "belief_update_alignment",
                    "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "neural mnemonic PE·R23 W11 Batch-HH·advisory·绝不 hard_gate"})
            out["verdict"] = ("FAIL_MINOR" if any(
                v["severity"] == "minor" for v in out["violations"]) else "PASS")
            out["warning"] = msg
        else:
            print(f"[SHADOW] belief_update_alignment: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="信念更新 vs 末段惊讶 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--intent", default=None,
                    help="显式覆盖 belief_update_intent ∈ {preserve, update}")
    args = ap.parse_args()
    rep = scan(args.draft_path, args.manifest, args.intent)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
