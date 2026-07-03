#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emotion_curve_rescan_scanner.py — W3 情绪曲线 live 回查（advisory · cluster 视野 · 2026-06-15）

【缺口】build_manifest 注入 target 情绪曲线（emotion_curve_full + matched_reagan_shape）给 writer，
但全系统从不回查 writer 写完的草稿【实际】情绪曲线 vs【目标】。现有 cross_cluster_emotion 只做
关键词情绪【占比/单一化/爆发周期】，不做 Reagan 形状对账（记忆调研 W3 实证：增量真实正交）。
本 scanner 补这个 actual-vs-target 闭环。

【做法 · 确定性可算半边】（北极星守卫：情绪只做可算的，语义裁决留 judge/作者）：
  1. 草稿分 N 段，每段用 EMOTION_KEYWORDS（复用 cross_cluster_emotion）映射 valence → actual 曲线
  2. resample + match_reagan_shape（复用 arc_aggregator·零重造）→ actual 形状
  3. 对比 manifest 注入的 target（cosine + Reagan 形状）→ 偏离则 advisory

【北极星⑤ 顾问非法官】情绪曲线是创作工艺，writer 有理由可偏离（如作者档本就反 Reagan / 缓冲章
  故意压平）→ 永远 advisory，code EMOTION_CURVE_RESCAN_DRIFT **绝不进 audit_hub.HARD_GATE_CODES**。
  env EMOTION_RESCAN_MODE: off / shadow(默认·只记不判) / active(超阈值顶层 warning 上报)。

用法：python emotion_curve_rescan_scanner.py <draft_path> [--manifest <manifest.json>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import arc_aggregator as arc  # match_reagan_shape / resample_to_n / cosine_similarity / _walk_nested

try:
    from cross_cluster_emotion_pattern_aggregate import EMOTION_KEYWORDS
except Exception:  # noqa: BLE001 · import 链断时 fallback（保 scanner 独立可跑）
    EMOTION_KEYWORDS = {
        "calm": ["平静", "镇定", "冷静", "压抑", "克制"],
        "anxious": ["紧张", "担心", "忐忑", "心跳", "汗"],
        "angry": ["愤怒", "怒", "咬牙", "拳头", "暴怒"],
        "sad": ["悲伤", "难过", "心痛", "泪", "哽咽"],
        "joyful": ["高兴", "笑", "兴奋", "欣喜", "畅快"],
        "curious": ["好奇", "疑惑", "纳闷", "想知道"],
        "fearful": ["恐惧", "害怕", "战栗", "颤抖"],
    }

ISSUE_CODE = "EMOTION_CURVE_RESCAN_DRIFT"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES
COSINE_FLOOR = 0.55                          # actual vs target 余弦（report 参考·裸余弦对全正值向量不敏感趋势）
CORR_FLOOR = 0.30                            # Pearson 趋势相关低于此 = 趋势偏离（主判据·捕捉「该升却降」）

# 情绪 → valence 映射（0=最负 · 1=最正 · 0.5=中性）。映射 EMOTION_KEYWORDS 的 7 类。
EMOTION_VALENCE = {
    "joyful": 1.0, "calm": 0.6, "curious": 0.55,
    "anxious": 0.35, "angry": 0.28, "fearful": 0.2, "sad": 0.12,
}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    """EMOTION_RESCAN_MODE：shadow（默认·只记不判） / active / off。非法值回退 shadow。"""
    m = (os.environ.get("EMOTION_RESCAN_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    """剥离系统写作时拼在正文尾部的 CHANGES 段（幂等安全）。"""
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _nn_segment_valence(chunks: "list[str]") -> "list[float | None]":
    """🔴 2026-06-29 NN情绪VAD集成 — 批量取每段真 valence（env RUOYU_NN_VAD=1 门控·一次 subprocess）。
    失败/未启用 → 全 None（调用方退关键词兜底·不崩·零回归）。保序一一对应。"""
    n = len(chunks)
    if os.environ.get("RUOYU_NN_VAD") != "1" or n == 0:
        return [None] * n
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled as feature_store_enabled
        if feature_store_enabled():
            preds = FeatureStore.get().compute_vad_batch(chunks)
        else:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch(chunks)
    except Exception:  # noqa: BLE001 NN 不可用 → 退关键词（不崩）
        return [None] * n
    if len(preds) != n:
        return [None] * n
    return [(p.get("valence") if (p and p.get("valence") is not None) else None) for p in preds]


def segment_valence_curve(text: str, n_seg: int = 10) -> "list[float]":
    """草稿分 n_seg 段·每段算情绪 valence(0-1)。无情绪词的段 → 0.5 中性。

    NN 模型优先（env RUOYU_NN_VAD=1·桥成功 → 每段真 valence）；否则确定性可算半边（关键词加权·
    非真情绪分析）——粗糙哨兵·只够判「曲线形状漂移」advisory，绝不当情绪判决（语义裁决留 judge/作者）。
    """
    text = _strip_changes(text)
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return []
    seg_size = max(1, math.ceil(len(paras) / n_seg))
    chunks = ["".join(paras[i:i + seg_size]) for i in range(0, len(paras), seg_size)]
    nn_vals = _nn_segment_valence(chunks)   # 模型 valence·同序·未启用→全 None
    curve: "list[float]" = []
    for idx, chunk in enumerate(chunks):
        if nn_vals[idx] is not None:
            curve.append(round(float(nn_vals[idx]), 4))   # 模型真 valence
            continue
        total_w = 0.0
        total_v = 0.0
        for emotion, kws in EMOTION_KEYWORDS.items():
            cnt = sum(chunk.count(kw) for kw in kws)
            if cnt:
                total_w += cnt
                total_v += cnt * EMOTION_VALENCE.get(emotion, 0.5)
        curve.append(round(total_v / total_w, 4) if total_w else 0.5)
    return curve


def _pearson(a, b):
    """Pearson 趋势相关（中心化·捕捉升降一致性）。常数曲线/长度不一/太短 → None（不判）。"""
    n = len(a)
    if n != len(b) or n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 1e-9 or vb <= 1e-9:
        return None   # 常数曲线无趋势·不判 drift
    return cov / (va ** 0.5 * vb ** 0.5)


def _find_target_from_manifest(manifest_path) -> "tuple[list, str | None]":
    """从 manifest 递归找 target 情绪曲线(emotion_curve_full) + matched_reagan_shape。

    用 arc._walk_nested 递归（robust·不依赖具体 slot key·build_manifest 注入位置可能变）。
    """
    if not manifest_path or not Path(manifest_path).exists():
        return [], None
    try:
        m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], None
    curve = arc._walk_nested(m, "emotion_curve_full")
    shape = arc._walk_nested(m, "matched_reagan_shape")
    curve = curve if isinstance(curve, list) and curve else []
    shape = shape if isinstance(shape, str) else None
    return curve, shape


def scan(draft_path, manifest_path=None) -> dict:
    """情绪曲线 actual-vs-target 回查。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {
        "scanner": "emotion_curve_rescan",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 北极星⑤ · 绝不 hard_gate
        "warning": None,
        "violations": [],           # 对齐 audit_hub._parse_violations_scanner（drift→1条·shadow 空→零回归）
        "verdict": "PASS",
    }
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out

    actual = segment_valence_curve(draft, 10)
    if len(actual) < 3:
        out["note"] = "草稿段落太少·情绪曲线不可估·跳过"
        return out
    actual_rs = arc.resample_to_n(actual, 10)
    actual_shape, actual_conf = arc.match_reagan_shape(actual_rs)
    out["actual_curve"] = [round(x, 4) for x in actual_rs]
    out["actual_reagan_shape"] = actual_shape

    target_curve, target_shape = _find_target_from_manifest(manifest_path)
    if not target_curve:
        out["note"] = "manifest 无 target 情绪曲线(emotion_curve_full)·只记 actual 不对账"
        return out
    target_rs = arc.resample_to_n([float(x) for x in target_curve], 10)
    cos = arc.cosine_similarity(actual_rs, target_rs)
    pear = _pearson(actual_rs, target_rs)
    shape_match = (target_shape is not None and actual_shape == target_shape)
    out["target_reagan_shape"] = target_shape
    out["cosine_actual_vs_target"] = round(cos, 4)
    out["pearson_trend_corr"] = round(pear, 4) if pear is not None else None
    out["shape_match"] = shape_match

    # 偏离判据：Pearson 趋势相关低（升降不一致）= 情绪曲线偏离 target。
    # 改用中心化 Pearson 而非裸 cosine —— 裸余弦对全正值向量不敏感趋势（升/降曲线余弦仍 ~0.6），
    # Pearson 中心化后能捕捉「该升却降」（实测升 vs 降 Pearson 强负）。常数曲线 → None 不判（无趋势）。
    drift = (pear is not None) and (pear < CORR_FLOOR)
    if drift:
        msg = (f"实际情绪曲线趋势偏离注入 target（Pearson {pear:.2f} < {CORR_FLOOR}·"
               f"实际形状 {actual_shape} vs 目标 {target_shape}）")
        if mode == "active":
            # 对齐 narrative_rhythm violations 格式（severity minor·advisory·audit_hub 合成聚合 issue）
            out["violations"].append({
                "kind": "emotion_curve_drift", "severity": "minor",
                "message": msg, "pearson": round(pear, 4),
                "actual_shape": actual_shape, "target_shape": target_shape,
                "_doc": "情绪曲线是创作工艺·writer 有理由可偏离(作者档反 Reagan / 缓冲章故意压平)"
                        "→advisory 待裁决非判决·关键词 valence 粗糙哨兵语义裁决留 judge/作者",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归 · audit_hub 收不到）
            print(f"[SHADOW] emotion_curve_rescan: {msg} — 不上报判决", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="W3 情绪曲线 live 回查(actual vs 注入 target·advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--manifest", default=None, help="manifest.json 路径(读 target emotion_curve_full)")
    args = ap.parse_args()
    report = scan(args.draft_path, args.manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·恒 exit 0(不阻断流水线·北极星⑤)·active 模式有 warning 才 exit 1
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
