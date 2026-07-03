#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dialogue_silence_density.py — 沉默/停顿/失语三档 marker scanner
(advisory · cluster · shadow · 2026-06-20 · R8 W4 Batch-J · L31)

【缺口】R8 W4 联网调研(Heldner & Edlund 2010 pause/gap/lapse 分类 + RB Kelly Power of
Pauses + Write Your Books The Weight of Silence): 真实对话有三档沉默:
  - within-turn pause: turn 内的停顿 (……/…… / 半截话)
  - turn-间 gap: turn 之间的短暂沉默 (一句叙述间隔)
  - ≥2 turn lapse: 持续多 turn 的失语 (沉默/无人回应)

LLM 默认 turn-by-turn 紧凑对答, 沉默/停顿/失语全缺位 → 紧张/告白/审讯场景失温。

【做法 · pause/gap/lapse 主检测维度纯规则正则(不依赖 LLM) + 情绪浓度模型优先】:
  1. within-turn pause: 对话引号内出现 ……/--/、、、/...(三连点)/半截话(以…结尾)。
  2. turn-间 gap: 引号外的描述短语命中 (沉默片刻/沉默良久/沉默不语/无人开口/一阵沉默/
     久久没有回应)。
  3. ≥2 turn lapse: 连续 ≥2 个 gap 标志或长沉默(良久/许久/沉默了好一会儿)。
  4. silence_emotional_context_match: 沉默标志 ±100 字窗口情绪浓度——VAD 模型优先
     (arousal 高强度 H/VH bin 或 valence 偏离中性·RUOYU_NN_VAD 门控)，模型不可用 → 回退
     固定情绪标签词表 (震惊/悲伤/决断/敌意/亲密) 命中度。
  5. 作者档 silence_baseline / cluster manifest expected_silence_marker_min 优先。

【与 R5 dispreferred_turn 正交】: dispreferred 查"裸拒绝缺缓冲", 本 scanner 查沉默/停顿/
失语三档密度。

【北极星② / ⑤ 顾问非法官】沉默是工艺 advisory · 作者档基线第一权威 · 爽文档低基线
合理 · code SILENCE_DENSITY_THIN 绝不进 hard_gate 。
env DIALOGUE_SILENCE_MODE: off / shadow(默认) / active。

用法: python dialogue_silence_density.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "SILENCE_DENSITY_THIN"

WITHIN_TURN_PAUSE = re.compile(r"(……|……|\.\.\.|--|、、、)")
GAP_MARKERS = re.compile(
    r"(沉默片刻|沉默良久|沉默不语|沉默了一会|无人开口|一阵沉默|"
    r"久久没有回应|没人回答|没人开口|没人吭声|寂静无声|寂静片刻)")
LAPSE_MARKERS = re.compile(
    r"(沉默了好一会|沉默良久|许久没有[开作]口|许久无人[作开]?[答声]|"
    r"久久无人回应|长久的沉默|静默良久|沉默如死|相对无言)")
EMOTION_CONTEXT = re.compile(
    r"(震惊|愕然|呆住|目瞪口呆|悲伤|哀伤|痛苦|决断|决意|敌意|杀意|"
    r"亲密|温柔|害怕|畏惧|心虚)")

_QUOTE_RE = re.compile(r"[“「]([^“”「」]{1,300}?)[”」]", re.DOTALL)
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("DIALOGUE_SILENCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _count_within_turn_pauses(draft: str) -> int:
    n = 0
    for m in _QUOTE_RE.finditer(draft):
        n += len(WITHIN_TURN_PAUSE.findall(m.group(1)))
    return n


def _count_gaps_and_lapses(draft: str):
    # 仅在 quote 之外计 gap/lapse 标志
    last_end = 0
    out_text_parts = []
    for m in _QUOTE_RE.finditer(draft):
        out_text_parts.append(draft[last_end:m.start()])
        last_end = m.end()
    out_text_parts.append(draft[last_end:])
    out_text = "".join(out_text_parts)
    gaps = len(GAP_MARKERS.findall(out_text))
    lapses = len(LAPSE_MARKERS.findall(out_text))
    return gaps, lapses


# 窗口情绪浓度模型判定阈值：复用 nn_vad_bridge.to_vad_bin 的 (0.2,0.4,0.6,0.8) 分箱边界。
# arousal 落 H/VH bin（强度高）或 valence 偏离中性 M bin（[0.4,0.6)·情绪有明确正/负极性）
# 任一命中 → 判定该窗口情绪浓度足够（对齐原 EMOTION_CONTEXT 词表既含中性强度词也含正负极词）。
WINDOW_AROUSAL_HIGH = 0.6
WINDOW_VALENCE_DEVIATION = 0.2


def _model_window_emotion_matches(windows: list) -> list:
    """批量 VAD 模型判窗口情绪浓度：arousal>=0.6(H/VH) 或 |valence-0.5|>=0.2(超出 M bin) → 1，
    否则 0。模型未启用(RUOYU_NN_VAD!=1)/不可用/异常 → 对应位置 None（调用方回退固定情绪词表·零回归）。"""
    if not windows or os.environ.get("RUOYU_NN_VAD") != "1":
        return [None] * len(windows)
    try:
        preds = None
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch(windows) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch(windows)
    except Exception:
        return [None] * len(windows)
    out = []
    for p in preds or []:
        if not p or (p.get("valence") is None and p.get("arousal") is None):
            out.append(None)
            continue
        try:
            v = float(p["valence"]) if p.get("valence") is not None else 0.5
            a = float(p["arousal"]) if p.get("arousal") is not None else 0.0
        except (TypeError, ValueError):
            out.append(None)
            continue
        hit = a >= WINDOW_AROUSAL_HIGH or abs(v - 0.5) >= WINDOW_VALENCE_DEVIATION
        out.append(1 if hit else 0)
    if len(out) != len(windows):
        return [None] * len(windows)
    return out


def _emotion_context_match(draft: str) -> int:
    """统计沉默标志 ±100 字窗口情绪浓度命中次数：VAD 模型优先(arousal 强度/valence 偏离)，
    不可用 → 回退固定情绪词表命中。"""
    windows = []
    for rx in (GAP_MARKERS, LAPSE_MARKERS):
        for m in rx.finditer(draft):
            lo = max(0, m.start() - 100)
            hi = min(len(draft), m.end() + 100)
            windows.append(draft[lo:hi])
    if not windows:
        return 0
    model_hits = _model_window_emotion_matches(windows)
    total = 0
    for window, model_hit in zip(windows, model_hits):
        if model_hit is not None:
            total += model_hit
        elif EMOTION_CONTEXT.search(window):
            total += 1
    return total


def _resolve_floor(project_root) -> float:
    if not project_root:
        return 0.4  # 通用兜底 (per_1k)
    db = Path(project_root) / "_数据库"
    ec = db / "事件簇.json"
    if ec.exists():
        try:
            data = json.loads(ec.read_text(encoding="utf-8"))
            for c in data.get("clusters") or []:
                if not isinstance(c, dict):
                    continue
                if (c.get("status") or "").strip().lower() in {
                        "in_progress", "active"}:
                    v = c.get("expected_silence_marker_min")
                    if isinstance(v, (int, float)):
                        return float(v)
        except (json.JSONDecodeError, OSError):
            pass
    ap = db / "作者风格.json"
    if ap.exists():
        try:
            obj = json.loads(ap.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                prof = obj.get("silence_baseline") or {}
                v = prof.get("density_per_1k_floor")
                if isinstance(v, (int, float)):
                    return float(v)
        except (json.JSONDecodeError, OSError):
            pass
    return 0.4


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "dialogue_silence_density", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    pause_n = _count_within_turn_pauses(draft)
    gap_n, lapse_n = _count_gaps_and_lapses(draft)
    total = pause_n + gap_n + lapse_n
    per1k = cjk / 1000.0
    density = round(total / per1k, 3) if per1k > 0 else 0.0
    emo_match = _emotion_context_match(draft)

    out.update({
        "within_turn_pause_count": pause_n,
        "turn_gap_count": gap_n,
        "lapse_count": lapse_n,
        "silence_marker_total": total,
        "silence_density_per_1k": density,
        "silence_emotional_context_match": emo_match,
    })

    floor = _resolve_floor(project_root)
    out["expected_min_density"] = floor

    msg = None
    if density < floor:
        msg = (f"沉默/停顿/失语密度偏低: density={density}/千字 < {floor}·"
               f"pause/gap/lapse={pause_n}/{gap_n}/{lapse_n}·"
               f"建议在紧张/告白/审讯/震惊场景补 within-turn pause(……)+turn 间 gap"
               f"(沉默片刻)+≥2 turn lapse(良久无人开口)三档")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "silence_density", "severity": "minor",
                "message": msg, "density_per_1k": density, "floor": floor,
                "pause": pause_n, "gap": gap_n, "lapse": lapse_n,
                "emotion_context_match": emo_match,
                "_doc": ("沉默是工艺 advisory·作者档/cluster manifest 可豁免·"
                         "爽文档低基线天然合理·绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] dialogue_silence_density: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="沉默/停顿/失语三档密度(advisory · cluster · shadow)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
