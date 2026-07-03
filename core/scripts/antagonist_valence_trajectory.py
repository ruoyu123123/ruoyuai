#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""antagonist_valence_trajectory.py — 反派情感重充电监控 (advisory · cross-cluster · R23 W11 Batch-II · P2)

【缺口】fanfic 群体生态：反派被 LLM 隐性「情感重充电」(逐 cluster 越写越正向 / 同情)
最终偏离 outline 原始反派定位 (未授权 redemption_arc) → 主线反派塌方。系统零检测。

【做法 · 模型优先证据 + 确定性词典保底】
  · outline 阶段 user 标反派 list (人物卡 role∈{反派/antagonist/BBEG/主反} 自动识别) +
    redemption_arc_authorized:bool (默认 false · 走 _数据库/用户偏好.json 或人物卡字段)
  · 每 cluster 算反派 valence：优先用 emotion_vad 模型读取反派 ±80 CJK 窗口 valence；
    模型未启用/不可用时走 lexicon-based sentiment (positive_lex - negative_lex) / 窗口个数。
  · 未授权但单调正向 5 cluster 连涨 > +0.3 → ANTAGONIST_VALENCE_DRIFT_UNAUTHORIZED advisory
  · 已授权 → 跳过

【北极星 ②④⑤】
  · cluster 单位 · 作者授权可豁免 · advisory · 绝不 hard_gate
  · lexicon 是 VAD 不可用时的弱标签来源；真版可继续叠加 Riveter power score / NRC-VAD 中文词表

用法: python antagonist_valence_trajectory.py <project_root> [--out <json_path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_DRIFT = "ANTAGONIST_VALENCE_DRIFT_UNAUTHORIZED"

# 弱标签 lexicon：VAD 不可用时的训练/扫描证据；后续可替换为 NRC-VAD-CN / Riveter power score
# [G2 P2] 外部化到 core/data/antagonist_valence_lexicon.json (lexicon_path 字段)·内嵌为 fallback 向后兼容
_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "antagonist_valence_lexicon.json"

_POSITIVE_FALLBACK = (
    "温柔", "善良", "怜悯", "悔恨", "懊悔", "释然", "微笑", "温暖", "宽恕", "理解",
    "同情", "柔软", "歉意", "无奈", "苦笑", "叹息", "保护", "守护",
)
_NEGATIVE_FALLBACK = (
    "残忍", "冷血", "暴虐", "狠厉", "狰狞", "凶狠", "嗜血", "贪婪", "阴险", "狡诈",
    "恶毒", "邪恶", "癫狂", "扭曲", "毒辣", "凶残",
)


def _load_valence_lexicon() -> tuple[tuple, tuple]:
    """[G2 P2] 外部 lexicon 优先·缺失/损坏 fallback 内嵌(向后兼容)"""
    try:
        data = json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
        pos = tuple(data.get("positive_lex") or ())
        neg = tuple(data.get("negative_lex") or ())
        if pos and neg:
            return pos, neg
    except (OSError, json.JSONDecodeError):
        pass
    return _POSITIVE_FALLBACK, _NEGATIVE_FALLBACK


POSITIVE_LEX, NEGATIVE_LEX = _load_valence_lexicon()

VALENCE_WINDOW = 80          # 反派提及 ±80 CJK 上下文窗口
TREND_MIN_CLUSTERS = 5       # ≥5 cluster 单调正向才报
TREND_DELTA_THRESHOLD = 0.3  # 末-首 > +0.3 才报


def _load(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _cjk_count(text: str) -> int:
    return sum(1 for c in text if "一" <= c <= "鿿")


def load_antagonists(project_root: Path) -> list:
    """从人物卡 role∈{反派/antagonist/BBEG/主反} 自动识别。"""
    pc = _load(project_root / "_数据库" / "人物卡.json", {}) or {}
    out = []
    chars = pc.get("characters", []) if isinstance(pc, dict) else []
    for c in chars:
        if not isinstance(c, dict):
            continue
        if c.get("role") in ("反派", "antagonist", "BBEG", "主反"):
            name = c.get("name")
            if isinstance(name, str) and len(name) >= 2:
                out.append({
                    "name": name,
                    "redemption_authorized": bool(c.get("redemption_arc_authorized", False)),
                })
    return out


def is_redemption_authorized(project_root: Path, char_entries) -> bool:
    """全局豁免：用户偏好 antagonist_redemption_arc_authorized=true → 全部 antagonist 豁免。
    或 char_entries 全部已授权。"""
    pref = _load(project_root / "_数据库" / "用户偏好.json", {}) or {}
    if isinstance(pref, dict):
        v = pref.get("antagonist_redemption_arc_authorized")
        if isinstance(v, bool) and v:
            return True
    if char_entries and all(c.get("redemption_authorized") for c in char_entries):
        return True
    return False


def _antagonist_windows(text: str, antagonist_names) -> list[str]:
    """提取每个反派名周围窗口，统一供词典和 VAD 模型消费。"""
    if not antagonist_names or not text:
        return []
    windows: list[str] = []
    for name in antagonist_names:
        i = 0
        while True:
            j = text.find(name, i)
            if j < 0:
                break
            start = max(0, j - VALENCE_WINDOW)
            end = min(len(text), j + len(name) + VALENCE_WINDOW)
            windows.append(text[start:end])
            i = j + len(name)
    return windows


def _lexicon_window_valence(windows: list[str]) -> float:
    scores = []
    for window in windows:
        pos = sum(window.count(w) for w in POSITIVE_LEX)
        neg = sum(window.count(w) for w in NEGATIVE_LEX)
        scores.append(pos - neg)
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 3)


def _model_window_valence(windows: list[str]) -> tuple[float | None, int]:
    """用现有 VAD 模型算反派窗口 valence；失败只返回 None，不改变 advisory 主链。"""
    if not windows or os.environ.get("RUOYU_NN_VAD") != "1":
        return None, 0
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch(windows) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch(windows)
    except Exception:
        return None, 0
    vals = []
    for p in preds or []:
        if p and p.get("valence") is not None:
            try:
                vals.append(float(p["valence"]))
            except (TypeError, ValueError):
                pass
    if not vals:
        return None, 0
    return round(sum(vals) / len(vals), 3), len(vals)


def compute_cluster_valence_detail(text: str, antagonist_names) -> dict:
    """返回 valence + source，便于训练池区分模型证据和词典证据。"""
    windows = _antagonist_windows(text, antagonist_names)
    if not windows:
        return {"valence": 0.0, "source": "none", "window_count": 0}
    model_valence, model_count = _model_window_valence(windows)
    if model_valence is not None:
        return {
            "valence": model_valence,
            "source": "model_vad",
            "window_count": len(windows),
            "model_window_count": model_count,
        }
    return {
        "valence": _lexicon_window_valence(windows),
        "source": "lexicon_fallback",
        "window_count": len(windows),
    }


def compute_cluster_valence(text: str, antagonist_names) -> float:
    """对每个反派名提取 ±80 CJK 窗口，返回平均 valence（兼容旧测试/调用）。"""
    return float(compute_cluster_valence_detail(text, antagonist_names)["valence"])


def _collect_cluster_drafts(project_root: Path):
    drafts = {}
    chap_dir = project_root / "章节"
    if not chap_dir.exists():
        return drafts
    for f in sorted(chap_dir.glob("cluster_*_draft.txt")):
        m = re.match(r"cluster_(\w+)_draft\.txt", f.name)
        if not m:
            continue
        try:
            drafts[m.group(1)] = f.read_text(encoding="utf-8")
        except OSError:
            continue
    return drafts


def detect_drift(valence_series) -> dict | None:
    """单调正向 5 cluster 连涨 > +0.3 → 候选。
    返回 {monotone, delta} 或 None。"""
    if len(valence_series) < TREND_MIN_CLUSTERS:
        return None
    # 取末 N 段
    tail = valence_series[-TREND_MIN_CLUSTERS:]
    diff = [tail[i] - tail[i - 1] for i in range(1, len(tail))]
    monotone = all(d >= 0 for d in diff)
    delta = tail[-1] - tail[0]
    if monotone and delta > TREND_DELTA_THRESHOLD:
        return {"monotone": True, "delta": round(delta, 3),
                "head": tail[0], "tail": tail[-1]}
    return None


def scan(project_root) -> dict:
    project_root = Path(project_root)
    out = {
        "schema_version": "1.0",
        "scanner": "antagonist_valence_trajectory",
        "gate_level": "advisory",
        "_doc": "反派情感重充电监控·R23 W11·model_vad|lexicon_fallback·绝不 hard_gate",
        "antagonists": [],
        "valence_per_cluster": [],
        "advisories": [],
    }
    antags = load_antagonists(project_root)
    out["antagonists"] = antags
    if not antags:
        out["note"] = "无反派登记 (人物卡 role=反派/antagonist)·跳过"
        return out
    if is_redemption_authorized(project_root, antags):
        out["note"] = "redemption_arc 已授权·跳过监控"
        out["authorized"] = True
        return out

    antag_names = [c["name"] for c in antags]
    drafts = _collect_cluster_drafts(project_root)
    series = []
    for cid in sorted(drafts):
        detail = compute_cluster_valence_detail(drafts[cid], antag_names)
        series.append({"cluster_id": cid, **detail})
    out["valence_per_cluster"] = series

    valence_only = [r["valence"] for r in series]
    drift = detect_drift(valence_only)
    if drift:
        out["advisories"].append({
            "code": ISSUE_DRIFT,
            "msg": (f"反派 valence 末 {TREND_MIN_CLUSTERS} cluster 单调正向 Δ=+{drift['delta']} > "
                    f"+{TREND_DELTA_THRESHOLD}·未授权 redemption_arc·反派塌方风险"
                    f"·授权请置 用户偏好.json.antagonist_redemption_arc_authorized=true"),
            "head": drift["head"],
            "tail": drift["tail"],
        })

    return out


def main():
    ap = argparse.ArgumentParser(description="antagonist valence trajectory (R23 W11 Batch-II)")
    ap.add_argument("project_root")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    rep = scan(args.project_root)
    s = json.dumps(rep, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(s, encoding="utf-8")
    else:
        print(s)
    sys.exit(0)


if __name__ == "__main__":
    main()
