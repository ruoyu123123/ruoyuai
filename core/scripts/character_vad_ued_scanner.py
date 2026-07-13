#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""character_vad_ued_scanner.py — 3D VAD UED per-character 指纹(R19 W8 Batch-V·P0)

【缺口·2026-06-21·临床 affect dynamics + arxiv 2503.23547】
VAD (Valence/Arousal/Dominance) 三维比离散情绪 / 1D sentiment / 2D ousiometric
更精细。UED (Uncertainty/Entropy/Drift) 是 affect dynamics 经典 6 维：
  Inertia(惯性·lag-1 自相关)
  Variability(方差·std)
  Instability(瞬时·相邻 |Δ|)
  Switch(零穿越·情绪极性翻转频率)
  Pulse(脉冲·>1σ 极端值占比)
  Augmentation(高低交互·V*A 共变)

每角色 6 UED × 3 VAD 维 = 18 指标·与作者基线 z-band 比对·drift → advisory。

【与既有 scanner 严格正交】
  - affective_signature_scanner    : 离散情绪标签·正交(本=连续 VAD 三轴)
  - sentiment_arc_fractal          : 1D sentiment 分形·正交(本=3D VAD 6 UED)
  - cross_cluster_ousiometric_emd : 2D ousiometric 卷间分布·正交
  - emotion_curve_rescan          : cluster 级情绪曲线·正交(本=per-character)

【quote 切句】零依赖简化版·复用引号 + 后接「说/道/答/问/喊/喝/叹」+ 前后引号字符
检测，对应 utterance 归属角色。无可归属 → 跳过(精确度优先于召回率)。

【角色名集合】从 _数据库/角色池.json 读·与 active_character_wm_load_scanner 同源。

【词典 · 🔴 2026-06-29 NN情绪VAD集成】
  core/data/nrc_vad_v2_placeholder.json  (40 字 V/A/D·仍占位·中文无 per-char D 真标注)
  core/data/cvaw_cvap_placeholder.json   (7724 词 V/A·Phase-0 已替换为真 CVAW/CVAP 简体词典)
  另：env RUOYU_NN_VAD=1 时 _score_vad 经 nn_vad_bridge 用真 NN 模型(CCC 0.80)·失败兜底本词典。

【北极星⑤】顾问非法官·全 advisory·env CHARACTER_VAD_UED_MODE 默认 shadow·
  VAD_UED_DRIFT 绝不 hard_gate。

【consolidate 加 vad_ued_signature SLOW_UPDATE】另设 compute_vad_ued_signature()
  给 consolidate_author_profile.py 调用·返回作者级 SLOW_UPDATE 段。

用法: python character_vad_ued_scanner.py <draft> [--project <root>] [--style <path>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "VAD_UED_DRIFT"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

_SCRIPT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _SCRIPT_DIR.parent / "data"

MIN_CJK = 600
ROLLING_WINDOW = 10           # rolling 10 utterance / scene
MIN_UTTERANCES_FOR_UED = 6    # 至少 6 utterance 才算 UED
PULSE_SIGMA = 1.0


def _mode() -> str:
    m = (os.environ.get("CHARACTER_VAD_UED_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


# ============ 词典加载 ============

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


# 🔴 2026-06-29 NN情绪VAD集成 — 进程级 NN VAD 缓存（text→(V,A,D)）。
# 占位词典浅扫 → 真模型升级（advisory·env RUOYU_NN_VAD=1 门控·默认 off·失败兜底词典·零回归）。
# 每次 scan 经 _nn_vad_prime 一次性批量推理（一个 subprocess·摊薄模型加载），_score_vad 仅查缓存，
# 杜绝 per-utterance spawn。
_NN_VAD_CACHE: dict = {}


def _lexicon_dominance(text):
    """从占位 VAD 词典(per-char D)算 D 均值·缺命中→0.5 中性（模型为 VA 时退词典 D 保 UED D 轴可算）。"""
    vad = _load_vad()
    ds = [float(v[2]) for ch in text
          if isinstance((v := vad.get(ch)), list) and len(v) >= 3]
    return sum(ds) / len(ds) if ds else 0.5


def _nn_vad_prime(texts):
    """批量预热 NN VAD（env 门控·一次 subprocess）。失败/未启用 → 不缓存（_score_vad 自动退词典）。"""
    if os.environ.get("RUOYU_NN_VAD") != "1":
        return
    uniq = [t for t in dict.fromkeys(texts) if t and t not in _NN_VAD_CACHE]
    if not uniq:
        return
    try:
        sys.path.insert(0, str(_SCRIPT_DIR))
        import nn_vad_bridge
        preds = nn_vad_bridge.predict_batch(uniq)
    except Exception:  # noqa: BLE001 NN 不可用 → 退词典（不缓存·不崩）
        return
    for t, r in zip(uniq, preds):
        if r and r.get("valence") is not None and r.get("arousal") is not None:
            d = r.get("dominance")
            d = float(d) if d is not None else _lexicon_dominance(t)  # 模型 VA → 退词典 D
            _NN_VAD_CACHE[t] = (float(r["valence"]), float(r["arousal"]), d)


def _score_vad(text):
    """对一段文本算 (V, A, D)·NN 模型优先(env 门控·经 _nn_vad_prime 批量缓存)·否则占位词典平均·缺命中 None。"""
    # 🔴 2026-06-29 NN情绪VAD集成 — 模型读数优先（命中缓存才用·未命中→占位词典兜底·零回归）
    if os.environ.get("RUOYU_NN_VAD") == "1":
        hit = _NN_VAD_CACHE.get(text)
        if hit is not None:
            return hit
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
    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.5
    return (_mean(vs), _mean(as_), _mean(ds) if ds else 0.5)


# ============ 角色 + 引语切片 ============

def _load_characters(project_root):
    names = set()
    if not project_root:
        return names
    p = Path(project_root) / "_数据库" / "角色池.json"
    if not p.exists():
        return names
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return names
    # 🔴 2026-06-28 角色池schema统一canonical：只读 core/emerged（不兼容·删多 key 兜底）
    sources = []
    for k in ("core", "emerged"):
        v = data.get(k)
        if isinstance(v, list):
            sources.extend(v)
    for entry in sources:
        if isinstance(entry, dict):
            for fk in ("name", "姓名", "id"):
                n = entry.get(fk)
                if isinstance(n, str) and len(n) >= 2:
                    names.add(n)
            for fk in ("aliases", "别名"):
                aliases = entry.get(fk) or []
                if isinstance(aliases, list):
                    for a in aliases:
                        if isinstance(a, str) and len(a) >= 2:
                            names.add(a)
        elif isinstance(entry, str) and len(entry) >= 2:
            names.add(entry)
    return names


_QUOTE_PAT = re.compile(r"[“「]([^”」]{1,200})[”」]")
_SPEECH_VERBS = ("说", "道", "答", "问", "喊", "喝", "叹", "嘀咕", "低声")


def _attribute_quote(prefix, suffix, names):
    """在引号前后 15 字内匹配角色名·返回归属或 None。"""
    window = prefix[-15:] + suffix[:15]
    has_speech_verb = any(v in window for v in _SPEECH_VERBS)
    if not has_speech_verb:
        return None
    # 取 window 内出现的角色名(优先靠近引号的)
    best = None
    best_dist = 999
    for n in names:
        idx = window.find(n)
        if idx >= 0 and idx < best_dist:
            best = n
            best_dist = idx
    return best


def _split_utterances(text, names):
    """切引语·返回 [{speaker, text, char_idx}]。"""
    out = []
    for m in _QUOTE_PAT.finditer(text):
        u = m.group(1)
        prefix = text[max(0, m.start() - 30): m.start()]
        suffix = text[m.end(): m.end() + 30]
        spk = _attribute_quote(prefix, suffix, names) if names else None
        out.append({"speaker": spk, "text": u, "char_idx": m.start()})
    return out


# ============ UED 6 维 ============

def _ued_for_series(series):
    """series = [(V, A, D), ...]·算 6 UED × 3 VAD = 18 指标。"""
    if len(series) < MIN_UTTERANCES_FOR_UED:
        return None
    axes = ("V", "A", "D")
    result = {}
    for ax_idx, ax in enumerate(axes):
        xs = [s[ax_idx] for s in series]
        n = len(xs)
        mean = sum(xs) / n
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
        # Inertia: lag-1 autocorrelation
        if n >= 3 and std > 1e-6:
            num = sum((xs[i] - mean) * (xs[i - 1] - mean) for i in range(1, n))
            den = sum((x - mean) ** 2 for x in xs)
            inertia = num / den if den > 1e-6 else 0.0
        else:
            inertia = 0.0
        # Variability
        variability = std
        # Instability: mean |Δ|
        instability = sum(abs(xs[i] - xs[i - 1]) for i in range(1, n)) / max(1, n - 1)
        # Switch: 极性翻转 (相对 0.5 阈值)
        signs = [1 if x > 0.5 else -1 for x in xs]
        switch = sum(1 for i in range(1, n) if signs[i] != signs[i - 1]) / max(1, n - 1)
        # Pulse: |x - mean| > sigma 占比
        pulse = sum(1 for x in xs if abs(x - mean) > PULSE_SIGMA * std) / n if std > 1e-6 else 0.0
        # Augmentation: 与 A 的 Pearson (V*A 共变)
        as_xs = [s[1] for s in series]
        as_mean = sum(as_xs) / n
        num = sum((xs[i] - mean) * (as_xs[i] - as_mean) for i in range(n))
        den_v = sum((x - mean) ** 2 for x in xs)
        den_a = sum((x - as_mean) ** 2 for x in as_xs)
        denom = math.sqrt(den_v * den_a)
        augmentation = (num / denom) if denom > 1e-6 else 0.0
        result[ax] = {
            "inertia": round(inertia, 4),
            "variability": round(variability, 4),
            "instability": round(instability, 4),
            "switch": round(switch, 4),
            "pulse": round(pulse, 4),
            "augmentation": round(augmentation, 4),
        }
    return result


# ============ baseline ============

def _load_baseline(project_root, style_path):
    out = {"per_character": {}, "from_author_profile": False}
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
    if isinstance(data, dict):
        q = data.get("quantitative") or {}
        sig = q.get("vad_ued_signature") or {}
        per_char = sig.get("per_character") or {}
        if isinstance(per_char, dict) and per_char:
            out["per_character"] = per_char
            out["from_author_profile"] = True
    return out


def _drift_score(cur_ued, base_ued):
    """对每角色比对 18 指标·返回偏离最大者的描述 + 计数。"""
    drifts = []
    for ax in ("V", "A", "D"):
        cur_ax = cur_ued.get(ax) or {}
        base_ax = base_ued.get(ax) or {}
        for key, val in cur_ax.items():
            bval = base_ax.get(key)
            if isinstance(bval, dict):
                m = bval.get("mean"); s = bval.get("std")
                if isinstance(m, (int, float)) and isinstance(s, (int, float)) and s > 1e-6:
                    z = (val - m) / s
                    if abs(z) >= 2.0:
                        drifts.append({"axis": ax, "metric": key, "z": round(z, 2),
                                       "value": val, "baseline_mean": m})
    return drifts


def compute_vad_ued_signature(utterances, character_filter=None):
    """consolidate_author_profile 用入口·给定切好的 utterances 算每角色 UED 指纹。"""
    # 🔴 2026-06-29 NN情绪VAD集成 — 批量预热 NN VAD（env 门控·一次 subprocess·失败 no-op 退词典）
    _nn_vad_prime([u.get("text", "") for u in utterances if isinstance(u, dict)])
    by_char = {}
    for u in utterances:
        spk = u.get("speaker")
        if not spk:
            continue
        if character_filter is not None and spk not in character_filter:
            continue
        sc = _score_vad(u.get("text", ""))
        if sc is None:
            continue
        by_char.setdefault(spk, []).append(sc)
    out = {}
    for spk, series in by_char.items():
        ued = _ued_for_series(series)
        if ued is not None:
            out[spk] = ued
    return out


def scan(draft_path, project_root=None, style_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "character_vad_ued", "schema_version": "1.0",
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

    names = _load_characters(project_root)
    utterances = _split_utterances(text, names)
    out["utterance_count"] = len(utterances)
    # 切角色 UED
    per_char_ued = compute_vad_ued_signature(utterances)
    out["per_character_ued"] = per_char_ued
    out["author_baseline"] = {"from_author_profile": False, "characters_count": 0}

    if not per_char_ued:
        out["note"] = "无可归属角色 utterance 满足 UED 最小样本(≥6)·skip"
        return out

    baseline = _load_baseline(project_root, style_path)
    out["author_baseline"]["from_author_profile"] = baseline["from_author_profile"]
    out["author_baseline"]["characters_count"] = len(baseline["per_character"])

    drift_findings = []
    for spk, cur_ued in per_char_ued.items():
        base_ued = baseline["per_character"].get(spk)
        if not isinstance(base_ued, dict):
            continue
        ds = _drift_score(cur_ued, base_ued)
        if ds:
            drift_findings.append({"character": spk, "drifts": ds[:6]})

    out["metrics"] = {
        "characters_examined": len(per_char_ued),
        "drift_characters": len(drift_findings),
    }
    if drift_findings:
        names_list = [d["character"] for d in drift_findings]
        msg = f"{len(drift_findings)} 个角色 VAD UED 偏离作者基线·{names_list[:4]}"
        if mode == "active":
            out["violations"].append({
                "kind": "vad_ued_drift", "severity": "minor", "code": ISSUE_CODE,
                "message": msg, "metrics": out["metrics"],
                "drift_findings": drift_findings,
                "_doc": "R19 affect dynamics 3D VAD × 6 UED per-character·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] character_vad_ued: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 3D VAD × 6 UED per-character·advisory·shadow")
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
