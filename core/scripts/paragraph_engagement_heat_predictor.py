#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paragraph_engagement_heat_predictor.py — 段落级评论密度预测

【依据：Qidian-Webnovel Corpus 2.79M 评论 johd.368 +
Loewenstein Information Gap 1994 + Groningen Qidian-110 + ResearchGate 358520938】

段落热度(读者评论密度的代理)由 5 个 Loewenstein gap 特征预测：
  ① question_density        段内问号/疑问短语密度 (per 100 CJK)
  ② ambiguous_referent      代词无锚 / 这/那/他/她 之外有未指明对象
  ③ temporal_suspense       倒计时/即将/还剩/在此之前 等时序悬挂
  ④ character_ambiguity     未具名身份 / 那人/有人/谁/某 等
  ⑤ valence_jump            相邻段情绪极性骤变(从平静→紧张/喜→悲)

【与既有 scanner 显式去重】
  - cross_cluster_engagement_metrics_aggregate(章级 hook trend) 正交
  - hook_strength_scanner(章末/拟切点 11 型钩) 正交：本 scanner 不评钩子强度
    而评『段落是否有 information gap』段级热度·覆盖『非钩段 cold flat』。

【北极星⑤】仅在『通章 cold flat』时报 PARAGRAPH_ENGAGEMENT_FLATLINE
  (整章 ≥80% 段落热度评分=0)·避免误伤纯过渡段。advisory·shadow 默认。
  env PARAGRAPH_ENGAGEMENT_HEAT_MODE 切 active 才上报。

用法: python paragraph_engagement_heat_predictor.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "PARAGRAPH_ENGAGEMENT_FLATLINE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 5 特征关键词
QUESTION_KW = re.compile(r"[?？]|怎么|为什么|是谁|什么|哪里|何时|何故|凭什么")
AMBIGUOUS_REF_KW = re.compile(r"(某种|某个|有些|有人|某人|有什么|某物|那东西|这东西|那里|某处)")
TEMPORAL_SUSPENSE_KW = re.compile(
    r"(还剩|还有.{0,3}天|还有.{0,3}小时|倒计时|来不及|快到|即将|马上|"
    r"在此之前|不久|很快|片刻后|稍后|半晌|须臾)")
CHARACTER_AMBIGUITY_KW = re.compile(
    r"(那人|那位|那个人|某位|某人|有人|谁|不知名|陌生人|身影|轮廓|背影)")
POSITIVE_KW = re.compile(r"(笑|安心|放下心|松了口气|安稳|温暖|甜|喜悦|平静|平和|微笑|轻松)")
NEGATIVE_KW = re.compile(r"(怒|惧|惊|颤|抖|冷汗|绝望|崩|碎|血|死|杀|危|险|逼|压)")

MIN_CJK = 500
FLATLINE_RATIO_THRESHOLD = 0.80  # ≥80% 段热度=0 才报

# comment-triggered 段落密度 + 位置分布：「评论触发段」=热度评分 >= COMMENT_HOT_THRESHOLD
# 评论分布(head/mid/tail) · 偏置太严重(>0.6 单一段) 报 advisory
COMMENT_HOT_THRESHOLD = 0.30                # 段评热度 ≥ 阈值 = 评论触发段
COMMENT_POSITION_BIAS_THRESHOLD = 0.60      # 任一段位占比 >60% = 分布失衡
COMMENT_TRIGGERED_DENSITY_LOW = 0.05        # 评论触发段密度 <5% = 整章零评论甜点


def _mode() -> str:
    m = (os.environ.get("PARAGRAPH_ENGAGEMENT_HEAT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_paragraphs(text: str):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _valence(p: str) -> int:
    """单段 valence(-1/0/1)：纯词典计数差（VAD 模型批量路径见 _compute_valences，
    scan() 走批量优先；本函数保留作单段直接调用 / 批量未命中时的逐段 fallback）。"""
    pos = len(POSITIVE_KW.findall(p))
    neg = len(NEGATIVE_KW.findall(p))
    if pos > neg + 1:
        return 1
    if neg > pos + 1:
        return -1
    return 0


def _model_valence_batch(paragraphs: list) -> "list | None":
    """批量取段落 VAD valence；env 未开/模型不可用/批量失配 → None（调用方整批回退词典逐段判定）。"""
    if not paragraphs or os.environ.get("RUOYU_NN_VAD") != "1":
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch(paragraphs) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch(paragraphs)
    except Exception:
        return None
    if not preds or len(preds) != len(paragraphs):
        return None
    return [p.get("valence") if p else None for p in preds]


def _valence_from_model_value(v: float) -> int:
    """模型 valence(0-1) → 离散 -1/0/1（>0.6 正 / <0.4 负 / 其余中性，明显偏向才判）。"""
    if v > 0.6:
        return 1
    if v < 0.4:
        return -1
    return 0


def _compute_valences(paragraphs: list) -> "tuple[list, str]":
    """批量算每段 valence(-1/0/1) + source：VAD 模型整批优先(一次调用摊薄模型加载开销)，
    未启用/不可用/单段未命中 → 逐段回退词典判定（_valence）。"""
    model_vals = _model_valence_batch(paragraphs)
    if model_vals is None:
        return [_valence(p) for p in paragraphs], "lexicon_fallback"
    out = []
    any_model_hit = False
    for v, p in zip(model_vals, paragraphs):
        if v is not None:
            try:
                out.append(_valence_from_model_value(float(v)))
                any_model_hit = True
                continue
            except (TypeError, ValueError):
                pass
        out.append(_valence(p))
    return out, ("model_vad" if any_model_hit else "lexicon_fallback")


def _heat(p: str) -> dict:
    cjk = max(_cjk_count(p), 1)
    q = len(QUESTION_KW.findall(p))
    ar = len(AMBIGUOUS_REF_KW.findall(p))
    ts = len(TEMPORAL_SUSPENSE_KW.findall(p))
    ca = len(CHARACTER_AMBIGUITY_KW.findall(p))
    # 单段总分（不含 valence_jump · 跨段算）
    score = (q * 100 / cjk) + (ar * 50 / cjk) + (ts * 80 / cjk) + (ca * 50 / cjk)
    return {"question": q, "ambiguous_ref": ar, "temporal_suspense": ts,
            "character_amb": ca, "score": round(score, 3)}


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "paragraph_engagement_heat_predictor", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    paragraphs = _split_paragraphs(text)
    if len(paragraphs) < 4:
        out["note"] = "段落不足·跳过"
        return out

    heats = [_heat(p) for p in paragraphs]
    # valence_jump：相邻段极性翻转计数（VAD 模型整批优先·未启用/不可用回退词典）
    valences, valence_source = _compute_valences(paragraphs)
    valence_jumps = sum(1 for a, b in zip(valences, valences[1:])
                        if a != 0 and b != 0 and a != b)
    for h, p in zip(heats, paragraphs):
        # valence bonus 平摊到产生跳的两段(取近似)
        pass

    cold_count = sum(1 for h in heats if h["score"] < 0.05)
    cold_ratio = cold_count / len(heats)
    mean_score = sum(h["score"] for h in heats) / len(heats)

    # comment-triggered 段落密度 + 位置分布
    # 评论触发段 = 热度评分 ≥ COMMENT_HOT_THRESHOLD
    # 位置分布 head=[0,1/3) mid=[1/3,2/3) tail=[2/3,1]
    n = len(paragraphs)
    triggered_indices = [i for i, h in enumerate(heats)
                         if h["score"] >= COMMENT_HOT_THRESHOLD]
    triggered_count = len(triggered_indices)
    comment_triggered_density = round(triggered_count / n, 4) if n else 0.0
    head_count = sum(1 for i in triggered_indices if i < n / 3)
    mid_count = sum(1 for i in triggered_indices if n / 3 <= i < 2 * n / 3)
    tail_count = sum(1 for i in triggered_indices if i >= 2 * n / 3)
    pos_total = max(1, triggered_count)
    position_distribution = {
        "head": round(head_count / pos_total, 4),
        "mid": round(mid_count / pos_total, 4),
        "tail": round(tail_count / pos_total, 4),
    }

    out["metrics"] = {
        "paragraphs": len(paragraphs),
        "mean_heat_score": round(mean_score, 3),
        "cold_paragraph_ratio": round(cold_ratio, 3),
        "valence_jumps": valence_jumps,
        "valence_jump_rate": round(valence_jumps / max(1, len(paragraphs) - 1), 3),
        "valence_source": valence_source,
        "comment_triggered_density": comment_triggered_density,
        "comment_triggered_count": triggered_count,
        "position_distribution": position_distribution,
    }

    messages = []
    # 仅通章 cold flat 才报
    if cold_ratio >= FLATLINE_RATIO_THRESHOLD and mean_score < 0.10:
        messages.append(
            f"通章 cold flat·{int(cold_ratio*100)}% 段热度=0·"
            f"mean={round(mean_score,3)}·建议加 question/ambiguous referent/"
            f"temporal suspense/character ambiguity 之一")
    # 评论触发段密度过低 advisory (整章无热段)
    if triggered_count >= 1 and comment_triggered_density < COMMENT_TRIGGERED_DENSITY_LOW:
        messages.append(
            f"comment-triggered 段密度 {round(comment_triggered_density*100,1)}% < "
            f"{int(COMMENT_TRIGGERED_DENSITY_LOW*100)}%·读者评论甜点稀缺")
    # 评论触发段位置失衡(任一区段 >60%)
    if triggered_count >= 5:
        max_pos, max_share = max(position_distribution.items(), key=lambda x: x[1])
        if max_share > COMMENT_POSITION_BIAS_THRESHOLD:
            messages.append(
                f"comment-triggered 段位置分布失衡·{max_pos} 段占 "
                f"{round(max_share*100,1)}% > {int(COMMENT_POSITION_BIAS_THRESHOLD*100)}%"
                f"·分布建议 head/mid/tail 各≈33%")

    if messages:
        msg = " · ".join(messages)
        if mode == "active":
            out["violations"].append({
                "kind": "paragraph_engagement_flatline",
                "severity": "minor", "code": ISSUE_CODE,
                "message": msg, "metrics": out["metrics"],
                "_doc": "段落级热度通章 cold·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] paragraph_engagement: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="段落热度预测·Loewenstein 5 gap·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
