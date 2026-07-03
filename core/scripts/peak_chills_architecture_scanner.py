#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""peak_chills_architecture_scanner.py — Aesthetic Chills 双相架构 advisory
R21 W10 Batch-DD · R21-NB-02

【缺口·R21 P1】Schoeller 2024 CABN: aesthetic chills 需要 anticipation → violation →
delayed release 双相架构。LLM 通病：emotion peak 突现/突消·缺前向铺垫 + 后向结晶。

【做法 · 确定性 · 零 LLM/零联网】
  · 复用 EMOTION_KEYWORDS 找 |valence|>0.7 top-3 peak
  · 每 peak 双相检：
    (A) ANTICIPATION 前向 200-500 CJK 内 ANTICIPATION_LEX 累积 ≥2
    (B) RELEASE 后向 50-150 CJK 内 RELEASE_CRYSTALLIZATION ≥1
  · 两项任缺 → CHILLS_ARCH_INCOMPLETE
  · 作者档 chills_arch_baseline 旁路

【峰值定位 · 模型优先证据 + 二元关键词保底 · 2026-07-01】
  优先用 emotion_vad 模型(RoBERTa 微调·held-out meanCCC 0.80)按句给连续 valence/arousal，
  定位真实峰值(|V-0.5| 极值) + 前后窗口 arousal 强度梯度(前向须高于全文基线·后向须比峰值回落)；
  模型未启用(RUOYU_NN_VAD!=1)/不可用 → 100% 回退 HIGH_VALENCE_POS/NEG 二元关键词命中(原样保留·零回归)。
  每个 peak 记录 source 字段(model_vad/lexicon_fallback)供训练数据归因。

【单 issue】
  · CHILLS_ARCH_INCOMPLETE — peak 前向铺垫/后向结晶缺失

【与既有 scanner 严格正交】
  · hook_strength_scanner 章末 4 类钩
  · premature_resolution_scanner 冲突→消解距离
  · emotion_curve_rescan 整体曲线
  本者 = peak 双相架构 anticipation/release。

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate

env PEAK_CHILLS_ARCH_MODE: off / shadow(默认) / active
用法: python peak_chills_architecture_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_INCOMPLETE = "CHILLS_ARCH_INCOMPLETE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 占位 emotion lexicon (high valence)·_placeholder=true
HIGH_VALENCE_POS = [
    "狂喜", "欢呼", "泪崩", "笑得直不起", "惊呼", "热血涌", "心花怒放", "拍案",
    "感动得", "如释重负", "热泪盈眶", "颤抖着笑",
]
HIGH_VALENCE_NEG = [
    "崩溃", "嚎啕", "撕心", "哀嚎", "绝望地", "瘫倒在地", "心如刀绞", "嘶吼",
    "失声痛哭", "天塌了", "万念俱灰", "肝肠寸断",
]
ALL_PEAK_WORDS = HIGH_VALENCE_POS + HIGH_VALENCE_NEG

ANTICIPATION_LEX = [
    "即将", "快了", "就在这时", "眼看就要", "差一步", "最后一刻", "数到三",
    "默数", "无声", "屏息", "心跳", "倒数", "马上", "片刻后", "下一秒", "霎那",
]
RELEASE_CRYSTALLIZATION = [
    "落下", "松开", "化作", "破碎", "绽放", "嘶哑", "泪", "笑出", "呼气",
    "长吁", "怔住", "愣住", "定格", "凝固", "颤抖", "脱力", "瘫",
]

DEFAULT_TOP_K = 3
DEFAULT_ANTICIPATION_WINDOW_LOW = 200
DEFAULT_ANTICIPATION_WINDOW_HIGH = 500
DEFAULT_ANTICIPATION_MIN_HITS = 2
DEFAULT_RELEASE_WINDOW_LOW = 50
DEFAULT_RELEASE_WINDOW_HIGH = 150
DEFAULT_RELEASE_MIN_HITS = 1

# emotion_vad 模型连续 valence 阈值(对齐 nrc_vad 词典刻度·"喜"=0.86/"怒"=0.10 级别的强烈度)·
# 及前后窗口 arousal 梯度余量(可被作者档 chills_arch_baseline 覆盖)
DEFAULT_MODEL_VALENCE_POS_THRESHOLD = 0.75
DEFAULT_MODEL_VALENCE_NEG_THRESHOLD = 0.25
DEFAULT_MODEL_GRADIENT_MARGIN = 0.05

_SENT_SPLIT_PAT = re.compile(r"[^。！？\n]+[。！？…]?")


def _mode() -> str:
    m = (os.environ.get("PEAK_CHILLS_ARCH_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _find_peaks(text: str, top_k: int = DEFAULT_TOP_K) -> list[dict]:
    """找 |valence|>0.7 的 top-K peak·按出现位置排序（防同位置抖动）"""
    peaks = []
    for w in ALL_PEAK_WORDS:
        idx = 0
        while True:
            i = text.find(w, idx)
            if i < 0:
                break
            peaks.append({"pos": i, "word": w,
                          "valence": "pos" if w in HIGH_VALENCE_POS else "neg"})
            idx = i + len(w)
    # 去重·同位置取第一个
    peaks.sort(key=lambda p: p["pos"])
    seen_pos = set()
    dedup = []
    for p in peaks:
        # 同 100 CJK 内只取一个 peak（防同段聚簇）
        if any(abs(p["pos"] - s) < 100 for s in seen_pos):
            continue
        seen_pos.add(p["pos"])
        dedup.append(p)
    return dedup[:top_k]


def _count_in_window(text: str, terms: list[str], start: int, end: int) -> tuple:
    """窗口内累积命中次数 + 命中词列表"""
    window = text[max(0, start):max(0, end)]
    total = 0
    matched = []
    for t in terms:
        c = window.count(t)
        if c:
            total += c
            matched.append(t)
    return total, matched


# ============ VAD 模型优先(替代二元关键词定位峰值 + 窗口强度梯度) ============
# env 未开(RUOYU_NN_VAD!=1)/任何失败 → None，调用方 100% 回退 _find_peaks/_count_in_window(零回归)。

def _model_vad_sentence_series(text: str) -> list | None:
    """按句切分后一次性批量调 emotion_vad 模型拿 (pos, valence, arousal)；失败 → None。"""
    if os.environ.get("RUOYU_NN_VAD") != "1":
        return None
    sentences = [(m.start(), m.group(0)) for m in _SENT_SPLIT_PAT.finditer(text) if m.group(0).strip()]
    if not sentences:
        return None
    texts = [s for _, s in sentences]
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch(texts) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch(texts)
    except Exception:
        return None
    if not preds or len(preds) != len(sentences):
        return None
    series = [{"pos": pos, "text": sent, "valence": float(pr["valence"]),
              "arousal": float(pr["arousal"]) if pr.get("arousal") is not None else 0.5}
             for (pos, sent), pr in zip(sentences, preds) if pr and pr.get("valence") is not None]
    return series or None


def _model_locate_peaks(series: list, top_k: int, pos_thr: float, neg_thr: float) -> list:
    """按模型 valence 连续值定位真实情绪峰值(替代 HIGH_VALENCE_POS/NEG 二元命中)：
    |V-0.5| 越大越靠前排序去重(同 100 CJK 内只留最强)·再按位置输出。"""
    cands = sorted((s for s in series if s["valence"] >= pos_thr or s["valence"] <= neg_thr),
                  key=lambda s: -abs(s["valence"] - 0.5))
    seen, dedup = [], []
    for c in cands:
        if any(abs(c["pos"] - p) < 100 for p in seen):
            continue
        seen.append(c["pos"])
        dedup.append({"pos": c["pos"], "word": c["text"][:16],
                      "valence": "pos" if c["valence"] >= pos_thr else "neg",
                      "arousal": c["arousal"]})
    dedup.sort(key=lambda c: c["pos"])
    return dedup[:top_k]


def _model_window_arousal(series: list, start: int, end: int) -> tuple:
    """窗口(字符位置)内平均 arousal + 命中句数(连续强度梯度·替代关键词计数)。"""
    vals = [s["arousal"] for s in series if start <= s["pos"] <= end]
    return (round(sum(vals) / len(vals), 3), len(vals)) if vals else (None, 0)


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
            sig = obj.get("chills_arch_baseline")
            if isinstance(sig, dict):
                return sig
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "peak_chills_architecture", "schema_version": "1.0",
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

    baseline = _read_author_baseline(project_root)
    ant_low = DEFAULT_ANTICIPATION_WINDOW_LOW
    ant_high = DEFAULT_ANTICIPATION_WINDOW_HIGH
    ant_min = DEFAULT_ANTICIPATION_MIN_HITS
    rel_low = DEFAULT_RELEASE_WINDOW_LOW
    rel_high = DEFAULT_RELEASE_WINDOW_HIGH
    rel_min = DEFAULT_RELEASE_MIN_HITS
    top_k = DEFAULT_TOP_K
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        if isinstance(baseline.get("anticipation_min_hits"), int):
            ant_min = baseline["anticipation_min_hits"]
        if isinstance(baseline.get("release_min_hits"), int):
            rel_min = baseline["release_min_hits"]
        if isinstance(baseline.get("top_k"), int):
            top_k = baseline["top_k"]

    # 模型优先定位真实情绪峰值(连续 VAD) + 前后窗口 arousal 强度梯度；
    # env 未开/模型不可用 → peaks/peak_source 保持二元关键词命中(零回归默认路径)。
    peaks = _find_peaks(text, top_k=top_k)
    peak_source = "lexicon_fallback"
    model_series = _model_vad_sentence_series(text)
    baseline_arousal = None
    grad_margin = DEFAULT_MODEL_GRADIENT_MARGIN
    if model_series:
        pos_thr = DEFAULT_MODEL_VALENCE_POS_THRESHOLD
        neg_thr = DEFAULT_MODEL_VALENCE_NEG_THRESHOLD
        if isinstance(baseline, dict):
            if isinstance(baseline.get("model_valence_pos_threshold"), (int, float)):
                pos_thr = float(baseline["model_valence_pos_threshold"])
            if isinstance(baseline.get("model_valence_neg_threshold"), (int, float)):
                neg_thr = float(baseline["model_valence_neg_threshold"])
            if isinstance(baseline.get("model_gradient_margin"), (int, float)):
                grad_margin = float(baseline["model_gradient_margin"])
        model_peaks = _model_locate_peaks(model_series, top_k, pos_thr, neg_thr)
        if model_peaks:
            peaks = model_peaks
            peak_source = "model_vad"
            arousals = [s["arousal"] for s in model_series]
            baseline_arousal = sum(arousals) / len(arousals)

    peak_reports = []
    incomplete = 0
    for p in peaks:
        pos = p["pos"]
        if peak_source == "model_vad":
            ant_mean, ant_n = _model_window_arousal(model_series, pos - ant_high, pos - ant_low)
            rel_mean, rel_n = _model_window_arousal(model_series, pos + rel_low, pos + rel_high)
            peak_arousal = p.get("arousal")
            anticipation_total = ant_n
            anticipation_matched = [f"arousal_mean={ant_mean}"] if ant_n else []
            anticipation_ok = bool(ant_n and ant_mean is not None
                                   and ant_mean >= baseline_arousal + grad_margin)
            release_hits = rel_n
            release_matched = ([f"arousal_drop={round(peak_arousal - rel_mean, 3)}"]
                               if rel_n and rel_mean is not None and peak_arousal is not None else [])
            release_ok = bool(rel_n and rel_mean is not None and peak_arousal is not None
                              and (peak_arousal - rel_mean) >= grad_margin)
        else:
            a_hits, a_matched = _count_in_window(text, ANTICIPATION_LEX,
                                                 pos - ant_high, pos - ant_low + 1)
            # 缩短窗口：使用更近的前向 200CJK 作为底线确认
            a_hits_near, a_matched_near = _count_in_window(text, ANTICIPATION_LEX,
                                                          pos - ant_low, pos)
            anticipation_total = a_hits + a_hits_near
            anticipation_matched = list(set(a_matched + a_matched_near))
            anticipation_ok = anticipation_total >= ant_min
            release_hits, release_matched = _count_in_window(text, RELEASE_CRYSTALLIZATION,
                                                             pos + rel_low, pos + rel_high)
            release_ok = release_hits >= rel_min
        report = {
            "pos": pos,
            "word": p["word"],
            "valence": p["valence"],
            "anticipation_hits": anticipation_total,
            "anticipation_matched": anticipation_matched,
            "anticipation_ok": anticipation_ok,
            "release_hits": release_hits,
            "release_matched": release_matched,
            "release_ok": release_ok,
            "complete": bool(anticipation_ok and release_ok),
            "source": peak_source,
        }
        if not report["complete"]:
            incomplete += 1
        peak_reports.append(report)

    out.update({
        "cjk": cjk,
        "peak_count": len(peaks),
        "incomplete_count": incomplete,
        "peaks": peak_reports,
        "baseline_source": baseline_source,
        "peak_source": peak_source,
        "thresholds": {
            "anticipation_window_cjk": [ant_low, ant_high],
            "anticipation_min_hits": ant_min,
            "release_window_cjk": [rel_low, rel_high],
            "release_min_hits": rel_min,
            "top_k": top_k,
        },
    })

    flags = []
    for r in peak_reports:
        if not r["complete"]:
            missing = []
            is_model = r.get("source") == "model_vad"
            if not r["anticipation_ok"]:
                missing.append(f"anticipation arousal 未见前向爬升(样本={r['anticipation_hits']})"
                               if is_model else f"anticipation hits={r['anticipation_hits']}<{ant_min}")
            if not r["release_ok"]:
                missing.append(f"release arousal 未见回落(样本={r['release_hits']})"
                               if is_model else f"release hits={r['release_hits']}<{rel_min}")
            flags.append({"code": ISSUE_CODE_INCOMPLETE,
                          "msg": (f"peak@{r['pos']}({r['word']})·"
                                  + "·".join(missing) + "·双相架构缺失")})

    if flags:
        msg = "·".join(f["msg"] for f in flags[:3])
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "peak_chills_architecture", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Schoeller 2024 CABN aesthetic chills 双相·R21-NB-02·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] peak_chills_architecture: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Aesthetic Chills 双相架构 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
