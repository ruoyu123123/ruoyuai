#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emotion_granularity_scanner.py — 情绪颗粒度粗（粗类情绪大词裸用）检测（advisory · cluster）

【缺口】SAGE (Emotional Granularity) 实证：高情绪颗粒度 = 用细分情绪词（不甘/讪讪/悻悻/
怅惘/悸动…）精准命名感受；低颗粒度 = 用四大类粗情绪大词（愤怒/悲伤/高兴/害怕）直陈。
弱模型/AI 草稿偏好低颗粒度大词。全库已有 subtext_rescan 查「引导词+情绪」on-the-nose 结构，
但【没有】scanner 查【裸粗情绪大词词频】本身——维度不同（词汇颗粒度 vs 直陈结构）。本 scanner 补这一格。

【与 subtext_rescan 去重 · 维度正交】：
  · subtext_rescan = 「引导词 + 情绪名词」紧邻（感到愤怒 / 心中充满悲伤）= on-the-nose **结构**。
  · 本 scanner = 粗情绪大词【裸词频】（不论有无引导词·愤怒/悲伤/高兴/害怕 等四大类粗类裸用）
    = 词汇**颗粒度**。同一句「他愤怒」无引导词 → subtext_rescan 不计 · 本 scanner 计。
  两者交集（感到愤怒）会被各自按各自维度计 · 但判据不同（结构密度 vs 颗粒度词频）·不重复门禁。

【做法 · 确定性可算半边】（北极星守卫：情绪表达质量是语义判断·只做可算的裸词频·裁决留 judge/作者）：
  统计四大类粗情绪大词（COARSE_EMOTION）裸用次数 → coarse_emotion_per_1k。超 floor = 情绪靠
  粗大词直陈·缺细分词（不甘/讪讪/悻悻/怅惘/悸动）→ advisory「建议换细分词或结构化呈现」。
  单向检测（只报偏高·偏低永不报·北极星③）。有作者档读基线 z-band·无档用通用 floor。

【北极星⑤ 顾问非法官】粗大词有时合理（高潮直给/快节奏短打）·writer 有理由可偏离 → 永远
  advisory，code EMOTION_GRANULARITY_COARSE **绝不进 audit_hub.HARD_GATE_CODES**。
  env EMOTION_GRANULARITY_MODE: off / shadow(默认·只记不判) / active。
  🔬 阈值 COARSE_EMOTION_PER_1K_FLOOR 待金标准校准（真作者原文喂自身 PASS·防矫枉过正）。

【NN 增强路径（env RUOYU_NN_VAD=1·默认 off）】裸关键词词频之外另开真 NN VAD 路径：
  段级 (V,A,D) → vad_variance（各轴方差之和·越大颗粒度越高=好）+ vad_coverage（各轴极差之积·
  情绪丰富度）。方差低于 floor = 情绪平铺(颗粒度粗) → 同 code EMOTION_GRANULARITY_COARSE（details
  多带 vad_variance）。NN 优先·桥失败/未启用 → 回退关键词词频（零回归·默认安全·不崩）·仍全 advisory。

用法：python emotion_granularity_scanner.py <draft_path> [--manifest m.json] [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "EMOTION_GRANULARITY_COARSE"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 四大类粗情绪大词（怒/悲/喜/惧 + 痛苦/绝望/愤恨等强度变体）—— 低颗粒度直陈
# vs 细分词（不甘/讪讪/悻悻/怅惘/悸动…·这些是高颗粒度·本 scanner 不计=正向）
COARSE_EMOTION = re.compile(
    r"(愤怒|恼怒|生气|悲伤|伤心|难过|高兴|开心|快乐|喜悦|"
    r"害怕|恐惧|惊恐|愤恨|痛苦|绝望|happy|sad)"
)
# 🔬 待金标准校准（真作者原文喂自身）：粗情绪大词裸词频超此/千字 = 颗粒度偏粗。
# 保守占位（宁可漏报不误报）·真作者基线实测后下调收紧。
COARSE_EMOTION_PER_1K_FLOOR = 3.0
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    # 默认 active（金标准校准：真作者粗情绪密度远低于 floor·零误报·安全放量）。
    m = (os.environ.get("EMOTION_GRANULARITY_MODE") or "active").strip().lower()
    return m if m in ("off", "shadow", "active") else "active"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def detect_coarse_emotions(text: str) -> list:
    """检测粗情绪大词裸用 hits（不论有无引导词·与 subtext_rescan 结构维度正交）。"""
    text = _strip_changes(text)
    return [{"word": m.group(0), "pos": m.start()} for m in COARSE_EMOTION.finditer(text)]


def _author_floor(project_root):
    """读作者档 emotion_granularity_profile 的 z-band 上沿作 floor。无档/无字段 → None。

    参考模板：作者档若有 coarse_emotion_per_1k_mean / _std → floor = mean + 2σ（z-band 上沿）。
    无作者档则调用方回退通用 COARSE_EMOTION_PER_1K_FLOOR。
    """
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(prof, dict):
        return None
    eg = prof.get("emotion_granularity_profile")
    if not isinstance(eg, dict):
        return None
    mean = eg.get("coarse_emotion_per_1k_mean")
    std = eg.get("coarse_emotion_per_1k_std")
    if not isinstance(mean, (int, float)):
        return None
    sigma = std if isinstance(std, (int, float)) else 0.0
    return round(float(mean) + 2.0 * float(sigma), 3)


# ── NN增强路径 — 段级 VAD 方差/覆盖量化情绪颗粒度（env RUOYU_NN_VAD=1 门控）─────
# 思路（北极星⑤·全 advisory）：高情绪颗粒度 = 段与段之间 VAD 读数有起伏（精准命名不同感受）；
# 低颗粒度 = 所有段 VAD 平铺一个调子。真 NN VAD（CCC0.80）取每段 (V,A,D)：
#   · vad_variance = 各轴方差之和（越大=颗粒度越高=好）→ 低于 floor = 情绪平铺(颗粒度粗)
#   · vad_coverage = 各轴极差之积（bounding box 体积·凸包体积的零依赖稳健代理）= 情绪丰富度
# 比裸关键词词频更精确（量化真实情绪起伏·非数大词）。env 默认 off → predict_batch 返全 None →
# 本函数返 None → scan 回退关键词路径（零回归·默认安全·失败不崩）。
MIN_VAD_SEGMENTS = 5          # 至少 5 段有效 VAD 读数才算方差（样本足·否则退关键词）
MIN_SEG_CJK_FOR_VAD = 8       # 段 < 8 CJK 噪声大·丢弃
# 🔬 待金标准校准（真作者原文喂自身 PASS·防矫枉过正）：段级 VAD 各轴方差之和低于此 = 情绪平铺。
# 保守占位（VAD∈[0,1]·单轴方差≤0.25·健康文本 2-3 轴和 ~0.04-0.13）·宁可漏报不误报（北极星⑤）。
VAD_VARIANCE_FLOOR = 0.02


def _variance(xs) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return sum((x - m) ** 2 for x in xs) / n


def _nn_vad_granularity(text: str):
    """NN增强路径 — 段级 VAD 方差/覆盖（env 门控·一次 subprocess·失败/未启用→None）。

    返回 {vad_variance, vad_coverage, per_axis_variance, axes, segments_scored} 或 None
    （RUOYU_NN_VAD≠1 / 有效段不足 / 桥失败/条数失配 → None → 调用方回退关键词·零回归·不崩）。
    """
    if os.environ.get("RUOYU_NN_VAD") != "1":
        return None
    paras = [p.strip() for p in re.split(r"\n\s*\n", _strip_changes(text)) if p.strip()]
    segs = [p for p in paras if _cjk_count(p) >= MIN_SEG_CJK_FOR_VAD]
    if len(segs) < MIN_VAD_SEGMENTS:
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import nn_vad_bridge
        preds = nn_vad_bridge.predict_batch(segs)
    except Exception:  # noqa: BLE001 NN 不可用 → 退关键词（不崩）
        return None
    if not preds or len(preds) != len(segs):
        return None
    vs, as_, ds = [], [], []
    for p in preds:
        if p and p.get("valence") is not None and p.get("arousal") is not None:
            vs.append(float(p["valence"]))
            as_.append(float(p["arousal"]))
            d = p.get("dominance")
            if d is not None:
                ds.append(float(d))
    if len(vs) < MIN_VAD_SEGMENTS:
        return None
    has_d = len(ds) == len(vs)
    var_v, var_a = _variance(vs), _variance(as_)
    var_d = _variance(ds) if has_d else None
    vad_variance = round(var_v + var_a + (var_d or 0.0), 5)
    rng_d = (max(ds) - min(ds)) if has_d else None
    vad_coverage = round((max(vs) - min(vs)) * (max(as_) - min(as_))
                         * (rng_d if rng_d is not None else 1.0), 5)
    return {
        "vad_variance": vad_variance,
        "vad_coverage": vad_coverage,
        "per_axis_variance": {"V": round(var_v, 5), "A": round(var_a, 5),
                              "D": round(var_d, 5) if var_d is not None else None},
        "axes": 3 if has_d else 2,
        "segments_scored": len(vs),
    }


def scan(draft_path, project_root=None) -> dict:
    """情绪颗粒度粗（粗类情绪大词裸用密度）检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "emotion_granularity", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    hits = detect_coarse_emotions(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["coarse_emotion_count"] = len(hits)
    out["cjk_count"] = cjk
    out["coarse_emotion_per_1k"] = per_1k
    out["sample_words"] = [h["word"] for h in hits[:8]]

    # 有作者档读 z-band 上沿作 floor·无档用通用 floor
    author_floor = _author_floor(project_root)
    floor = author_floor if author_floor is not None else COARSE_EMOTION_PER_1K_FLOOR
    out["floor_used"] = floor
    out["author_baseline"] = author_floor

    # 🔴 NN增强路径 — RUOYU_NN_VAD=1 时段级 VAD 方差量化颗粒度(更精确·NN 优先)·失败兜底关键词
    nn = _nn_vad_granularity(draft)
    out["detection_method"] = "nn_vad_variance" if nn else "keyword_density"

    msg = None
    if nn is not None:
        out["vad_variance"] = nn["vad_variance"]
        out["vad_coverage"] = nn["vad_coverage"]
        out["vad_per_axis_variance"] = nn["per_axis_variance"]
        out["vad_segments_scored"] = nn["segments_scored"]
        if nn["vad_variance"] < VAD_VARIANCE_FLOOR:
            msg = (f"情绪颗粒度偏粗：段级 VAD 方差 {nn['vad_variance']} < {VAD_VARIANCE_FLOOR}"
                   f"（{nn['segments_scored']} 段情绪平铺缺起伏·VAD 覆盖 {nn['vad_coverage']}）·"
                   f"建议增强情绪层次/换细分词精准命名感受")
    elif per_1k > floor:
        msg = (f"情绪颗粒度偏粗：粗情绪大词裸用 {per_1k}/千字 > {floor}"
               f"（{len(hits)} 处·愤怒/悲伤/高兴/害怕 等四大类直陈）·"
               f"缺细分词（不甘/讪讪/悻悻/怅惘/悸动）·建议换细分词或结构化呈现")
    if msg:
        if mode == "active":
            violation = {
                "kind": "emotion_granularity_coarse", "severity": "minor",
                "message": msg, "per_1k": per_1k, "count": len(hits),
                "floor_used": floor,
                "detection_method": out["detection_method"],
                "_doc": "情绪颗粒度是创作判断·粗大词有时合理(高潮直给/快节奏短打)→advisory 待裁决·"
                        "裸词频/段级 VAD 方差是可算半边粗糙哨兵·真情绪表达质量留 judge/作者",
            }
            if nn is not None:   # NN 路径 details 多带 vad_variance（团队约定）
                violation["vad_variance"] = nn["vad_variance"]
                violation["vad_coverage"] = nn["vad_coverage"]
                violation["vad_variance_floor"] = VAD_VARIANCE_FLOOR
            out["violations"].append(violation)
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] emotion_granularity: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="情绪颗粒度粗(粗类情绪大词裸用)检测(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读作者 emotion_granularity 基线 z-band")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·active 有 warning 才 exit 1(不阻断·北极星⑤)
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
