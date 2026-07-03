#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""narration_dialogue_vad_coherence_scanner.py — 旁白↔对话 VAD 跨通道一致(R19 W8 Batch-X·P1)

【缺口·2026-06-21·Vishnubhotla baseline 0.06-0.09 旁白对话 VAD 相关】
LLM 写作时旁白和对话情感同步过紧(VAD per 维度 |r|>0.50) → 失去叙述视点张力
(narrator 的「转述-评价」分层)·与好作品旁白「轻描淡写」+ 对话「火药味」拉开
张力相反。

【探针】
  1. 复用 character_vad_ued_scanner 的引语切片(_QUOTE_PAT + 后置说道答)
  2. 旁白通道 = 非引号文本(裁掉引号内容剩余)
  3. 对话通道 = 引号内文本拼接
  4. 按场景(双换行段 + 1500 CJK 兜底)切·两通道分别算 (V, A, D) 序列
  5. Pearson per V/A/D · |r|>0.50 → NARR_DIAL_VAD_OVERCOUPLED advisory

【VAD 打分 · 模型优先证据 + 占位词典保底 · 2026-07-01】
  每场景两通道优先用 emotion_vad 模型(RoBERTa 微调·held-out meanCCC 0.80)批量打分 V/A，
  D 轴模型诚实无标注·走词典兜底；模型未启用(RUOYU_NN_VAD!=1)/不可用 → 全走占位词典
  (与旧版逐字节一致)。metrics.vad_source 标 model_vad/lexicon_fallback/mixed 供训练归因。

【作者档 override】
  quantitative.dial_narr_vad_target_corr = {"V": 0.40, "A": 0.50, "D": 0.30}
  作者档值 > 默认时放宽阈值(只在 |r|>author_corr+0.15 时报)。

【与既有 scanner 严格正交】
  - character_vad_ued       : per-character UED·正交(本=旁白 vs 对话两通道)
  - cross_scene_voice_drift : 跨场景同角色 voice·正交
  - voice_pack             : 角色档·正交
  - affective_signature    : 离散情绪·正交

【北极星⑤】顾问非法官·全 advisory·env NARR_DIAL_VAD_MODE 默认 shadow·
  NARR_DIAL_VAD_OVERCOUPLED 绝不 hard_gate。

用法: python narration_dialogue_vad_coherence_scanner.py <draft> [--project <root>] [--style <path>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "NARR_DIAL_VAD_OVERCOUPLED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

_SCRIPT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _SCRIPT_DIR.parent / "data"

MIN_CJK = 800
MIN_SCENES_FOR_PEARSON = 4
DEFAULT_OVERCOUPLE_THRESHOLD = 0.50

_QUOTE_PAT = re.compile(r"[“「]([^”」]{1,300})[”」]")


def _mode() -> str:
    m = (os.environ.get("NARR_DIAL_VAD_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


# ============ VAD 词典(复用 character_vad_ued 占位词典) ============

_VAD_CACHE = None
_CVAW_CACHE = None


def _load_vad():
    global _VAD_CACHE
    if _VAD_CACHE is not None:
        return _VAD_CACHE
    p = _DATA_DIR / "nrc_vad_v2_placeholder.json"
    if not p.exists():
        _VAD_CACHE = {}
        return _VAD_CACHE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        _VAD_CACHE = data.get("entries", {}) or {}
    except (OSError, json.JSONDecodeError):
        _VAD_CACHE = {}
    return _VAD_CACHE


def _load_cvaw():
    global _CVAW_CACHE
    if _CVAW_CACHE is not None:
        return _CVAW_CACHE
    p = _DATA_DIR / "cvaw_cvap_placeholder.json"
    if not p.exists():
        _CVAW_CACHE = {}
        return _CVAW_CACHE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        _CVAW_CACHE = data.get("entries", {}) or {}
    except (OSError, json.JSONDecodeError):
        _CVAW_CACHE = {}
    return _CVAW_CACHE


def _score_vad(text):
    """对文本算 (V, A, D) 平均·命中加权·空命中返回 None。"""
    vad = _load_vad()
    cvaw = _load_cvaw()
    vs, as_, ds = [], [], []
    for ch in text:
        if ch in vad:
            v = vad[ch]
            if isinstance(v, list) and len(v) >= 3:
                vs.append(float(v[0])); as_.append(float(v[1])); ds.append(float(v[2]))
    for term, scores in cvaw.items():
        if term in text and isinstance(scores, list) and len(scores) >= 2:
            vs.append(float(scores[0])); as_.append(float(scores[1]))
    if not vs:
        return None
    def _mean(xs): return sum(xs) / len(xs) if xs else 0.5
    return (_mean(vs), _mean(as_), _mean(ds) if ds else 0.5)


# ============ VAD 模型优先(emotion_vad RoBERTa 微调·held-out meanCCC 0.80) ============
# env 未开(RUOYU_NN_VAD!=1)/任何失败 → 全 None，调用方 100% 回退占位词典逻辑(零回归)。
# 模型只出 V/A(诚实·中文无 D 真标注)，D 轴永远走词典/默认 0.5 兜底。

def _model_vad_batch(texts: list) -> list:
    """批量走 FeatureStore(缓存优先)→nn_vad_bridge 拿模型 VAD，一次性摊薄 subprocess 开销。"""
    n = len(texts)
    if n == 0 or os.environ.get("RUOYU_NN_VAD") != "1":
        return [None] * n
    try:
        sys.path.insert(0, str(_SCRIPT_DIR))
        try:
            sys.path.insert(0, str(_SCRIPT_DIR.parent / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch(texts) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch(texts)
    except Exception:
        return [None] * n
    if not preds or len(preds) != n:
        return [None] * n
    return preds


def _score_vad_hybrid(text, model_pred=None):
    """优先模型 V/A + 词典 D 兜底；模型未命中 → 纯词典(与旧版 _score_vad 完全一致·零回归)。
    返回 (vad_tuple|None, source)。"""
    lex = _score_vad(text)
    if model_pred and model_pred.get("valence") is not None and model_pred.get("arousal") is not None:
        d = model_pred.get("dominance")
        d_val = float(d) if d is not None else (lex[2] if lex else 0.5)
        return (float(model_pred["valence"]), float(model_pred["arousal"]), d_val), "model_vad"
    if lex is not None:
        return lex, "lexicon_fallback"
    return None, "none"


# ============ 两通道切分 ============

def _split_narration_dialogue(text):
    """返回 (narration_text, dialogue_text)。引号内 = 对话；剩余 = 旁白。"""
    dialogue_parts = []
    narration_parts = []
    last = 0
    for m in _QUOTE_PAT.finditer(text):
        narration_parts.append(text[last: m.start()])
        dialogue_parts.append(m.group(1))
        last = m.end()
    narration_parts.append(text[last:])
    return "".join(narration_parts), "".join(dialogue_parts)


def _split_scenes(text):
    """按双换行 + 1500 CJK 兜底切。"""
    parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not parts:
        return []
    scenes, buf, cur = [], [], 0
    for p in parts:
        buf.append(p)
        cur += _cjk_count(p)
        if cur >= 1500:
            scenes.append("\n\n".join(buf))
            buf, cur = [], 0
    if buf:
        scenes.append("\n\n".join(buf))
    return scenes


# ============ Pearson ============

def _pearson(xs, ys):
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    if dx < 1e-9 or dy < 1e-9:
        return 0.0
    return num / math.sqrt(dx * dy)


def _load_author_targets(project_root, style_path):
    """读作者档 dial_narr_vad_target_corr override。"""
    data = None
    if style_path and Path(style_path).exists():
        try:
            data = json.loads(Path(style_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None and project_root:
        for fname in ("作者风格_FINAL.json", "作者风格.json"):
            p = Path(project_root) / "_数据库" / fname
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    break
                except (OSError, json.JSONDecodeError):
                    continue
    if not isinstance(data, dict):
        return None
    q = data.get("quantitative") or {}
    target = q.get("dial_narr_vad_target_corr")
    if isinstance(target, dict):
        return target
    return None


def scan(draft_path, project_root=None, style_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "narration_dialogue_vad_coherence", "schema_version": "1.0",
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

    scenes = _split_scenes(text)
    if len(scenes) < MIN_SCENES_FOR_PEARSON:
        out["note"] = f"场景数 {len(scenes)} < {MIN_SCENES_FOR_PEARSON}·跳过"
        return out

    narr_texts, dial_texts = [], []
    for sc in scenes:
        narr, dial = _split_narration_dialogue(sc)
        narr_texts.append(narr)
        dial_texts.append(dial)

    # 一次性批量模型调用(narr+dial 合批减少 subprocess 次数)；env 未开时直接全 None(零回归)
    model_preds = _model_vad_batch(narr_texts + dial_texts)
    narr_preds = model_preds[:len(narr_texts)]
    dial_preds = model_preds[len(narr_texts):]

    narr_series = []
    dial_series = []
    vad_sources = set()
    for i in range(len(scenes)):
        n_vad, n_src = _score_vad_hybrid(narr_texts[i], narr_preds[i])
        d_vad, d_src = _score_vad_hybrid(dial_texts[i], dial_preds[i])
        if n_vad is not None and d_vad is not None:
            narr_series.append(n_vad)
            dial_series.append(d_vad)
            vad_sources.add(n_src)
            vad_sources.add(d_src)

    if "model_vad" in vad_sources and "lexicon_fallback" in vad_sources:
        vad_source = "mixed"
    elif "model_vad" in vad_sources:
        vad_source = "model_vad"
    else:
        vad_source = "lexicon_fallback"

    out["metrics"] = {
        "total_scenes": len(scenes),
        "paired_scenes": len(narr_series),
        "vad_source": vad_source,
    }

    if len(narr_series) < MIN_SCENES_FOR_PEARSON:
        out["note"] = "无足够配对场景算 Pearson·跳过"
        return out

    targets = _load_author_targets(project_root, style_path) or {}
    corrs = {}
    findings = []
    for idx, ax in enumerate(("V", "A", "D")):
        nxs = [s[idx] for s in narr_series]
        dys = [s[idx] for s in dial_series]
        r = _pearson(nxs, dys)
        if r is None:
            continue
        corrs[ax] = round(r, 3)
        thr = DEFAULT_OVERCOUPLE_THRESHOLD
        if isinstance(targets.get(ax), (int, float)):
            thr = max(thr, float(targets[ax]) + 0.15)
        if abs(r) > thr:
            findings.append({"axis": ax, "pearson": round(r, 3), "threshold": round(thr, 3)})

    out["metrics"]["correlations"] = corrs
    out["author_targets"] = targets

    if findings:
        axes_hit = "/".join(f["axis"] for f in findings)
        msg = (f"旁白↔对话 VAD 跨通道过紧耦合·维度 {axes_hit}·"
               f"高于阈值表示叙述视点缺乏张力分层")
        if mode == "active":
            out["violations"].append({
                "kind": "narr_dial_vad_overcoupled",
                "severity": "minor",
                "code": ISSUE_CODE,
                "message": msg,
                "metrics": out["metrics"],
                "findings": findings,
                "_doc": "R19 W8 Batch-X·旁白对话 VAD 过紧耦合·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] narration_dialogue_vad: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-X 旁白对话 VAD 一致·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.style)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
