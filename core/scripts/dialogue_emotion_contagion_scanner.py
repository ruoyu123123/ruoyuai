#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dialogue_emotion_contagion_scanner.py — Hatfield 情感传染+Gottman 4 阶段(R19 W8 Batch-X·P1)

【缺口·2026-06-21·BSETD 2026 M3ED + Plutchik adjacency】
LLM 写双人对话时常出现两类异常：
  (a) 情感传染同步失衡(Hatfield 1994 emotional contagion baseline)：双人主导对话
      场景两人 VAD 序列 lagged cross-correlation 不在生理同步窗 [0.30, 0.65]
      内 → 过紧(机械同情)或过松(对话断裂)。
  (b) 冲突场景 Gottman 4 阶段(criticism → contempt → defensiveness → stonewalling)
      级联缺失或顺序颠倒 → 冲突缺乏真实感。

【探针】
  1. 复用 character_vad_ued_scanner._split_utterances 切引语 + 归属
  2. 按场景(双换行 + 1500 CJK 兜底)切
  3. 双人主导场景(top-2 speaker 占场景 utterance ≥ 70%)激活：
     - 算两人 utterance 的 (V, A, D) 序列(用 _score_vad)
     - lagged cross-correlation lag∈[-2, 2] · 取 max |r|
     - max |r| < 0.30 → too_loose(对话断裂)
     - max |r| > 0.65 → too_tight(机械同情)
  4. 冲突场景识别(关键词命中 ≥ 3 个：吵架/争执/翻脸/怒/恨/敌/质问/讥讽/反驳/沉默)：
     - 检测 4 阶段标志词序列(criticism="指责|批评|总是|从不"·contempt="蔑视|嘲讽|讥|哼"·
       defensiveness="不是我|不公|你才"·stonewalling="不说|沉默|不回|转身")
     - 阶段缺位 ≥ 2 → CONTAGION_GOTTMAN_INCOMPLETE
     - 顺序颠倒(后阶段先出现) → CONTAGION_GOTTMAN_REVERSED

【consolidate 加 dialogue_contagion_signature】
  compute_dialogue_contagion_signature() 给 consolidate SLOW_UPDATE 段·按作者
  原文统计 sync_window_baseline(p20/p80) + gottman_cascade_freq。

【与既有 scanner 严格正交】
  - character_vad_ued       : per-character UED·正交(本=对偶同步)
  - narration_dialogue_vad  : 旁白vs对话跨通道·正交(本=对话内双人对偶)
  - emotion_curve_rescan    : cluster 级情感曲线·正交
  - OmniToM/belief_ledger   : 信念维度·正交(本=情感同步维度)

【北极星⑤】顾问非法官·全 advisory·env DIALOGUE_CONTAGION_MODE 默认 shadow·
  DIALOGUE_CONTAGION_ABNORMAL 绝不 hard_gate。

用法: python dialogue_emotion_contagion_scanner.py <draft> [--project <root>] [--style <path>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "DIALOGUE_CONTAGION_ABNORMAL"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

_SCRIPT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _SCRIPT_DIR.parent / "data"

MIN_CJK = 800
MIN_SCENES = 2
SYNC_WINDOW_LO = 0.30
SYNC_WINDOW_HI = 0.65
DOMINANT_SHARE_THRESHOLD = 0.70
MAX_LAG = 2

_QUOTE_PAT = re.compile(r"[“「]([^”」]{1,300})[”」]")
_SAY_PAT = re.compile(r"([一-鿿]{1,8})(?:说|道|答|喊|叫|问|笑道|冷笑|低声|怒道)")

_CONFLICT_KW = ("吵架", "争执", "翻脸", "怒", "恨", "敌", "质问", "讥讽", "反驳", "沉默",
                "怒目", "拍桌", "甩门", "冷战")

_GOTTMAN_STAGES = (
    ("criticism", ("指责", "批评", "总是", "从不", "永远", "你怎么")),
    ("contempt", ("蔑视", "嘲讽", "讥", "哼", "鄙夷", "翻白眼")),
    ("defensiveness", ("不是我", "不公", "你才", "凭什么", "我哪有", "都是你")),
    ("stonewalling", ("不说", "沉默", "不回", "转身", "懒得", "不想说")),
)


def _mode() -> str:
    m = (os.environ.get("DIALOGUE_CONTAGION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


# ============ 占位 VAD 词典(复用 batch-V) ============

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
    """返回 (V, A, D) 平均·全空返回 None。"""
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


# ============ 引语切片(复用 quote_attributor 占位) ============

def _split_utterances(text, names=None):
    """返回 [(speaker, utterance_text), ...]·后置说道答 + names 兜底。"""
    out = []
    names = set(names or [])
    pos = 0
    for m in _QUOTE_PAT.finditer(text):
        utter = m.group(1)
        tail = text[m.end(): m.end() + 30]
        speaker = None
        sm = _SAY_PAT.search(tail)
        if sm:
            cand = sm.group(1)
            if not names or cand in names:
                speaker = cand
        if speaker is None and names:
            head = text[max(0, m.start() - 30): m.start()]
            for name in names:
                if name in head:
                    speaker = name
                    break
        out.append((speaker or "UNK", utter))
        pos = m.end()
    return out


def _split_scenes(text):
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


# ============ Lagged cross-correlation ============

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


def _max_lagged_corr(xs, ys, max_lag=MAX_LAG):
    """对齐两序列长度后算 lag∈[-max_lag, max_lag] 的最大 |r|。"""
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    xs = xs[:n]; ys = ys[:n]
    best = 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            a = xs[-lag:]; b = ys[:n + lag]
        elif lag > 0:
            a = xs[:n - lag]; b = ys[lag:]
        else:
            a = xs; b = ys
        if len(a) < 3:
            continue
        r = _pearson(a, b)
        if r is None:
            continue
        if abs(r) > abs(best):
            best = r
    return best


# ============ Gottman 4 阶段 ============

def _detect_gottman_sequence(scene_text):
    """扫描 scene 内 4 阶段触发位置·返回 [(stage, pos), ...]按出现序排。"""
    hits = []
    for stage, kws in _GOTTMAN_STAGES:
        for kw in kws:
            j = scene_text.find(kw)
            if j >= 0:
                hits.append((stage, j))
                break
    hits.sort(key=lambda x: x[1])
    return hits


def _is_conflict_scene(scene_text):
    cnt = sum(1 for kw in _CONFLICT_KW if kw in scene_text)
    return cnt >= 3


def _load_characters(project_root):
    if not project_root:
        return set()
    p = Path(project_root) / "_数据库" / "角色池.json"
    if not p.exists():
        return set()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        emerged = d.get("emerged_characters") or []
        return {e.get("name") for e in emerged if isinstance(e, dict) and e.get("name")}
    except (OSError, json.JSONDecodeError):
        return set()


# ============ Public API: consolidate signature ============

def compute_dialogue_contagion_signature(draft_paths, names=None) -> dict:
    """consolidate 入口·汇总作者级 sync_window_baseline + gottman_cascade_freq。"""
    sync_scores = []
    cascade_complete = 0
    cascade_total = 0
    for fp in draft_paths or []:
        try:
            text = Path(fp).read_text(encoding="utf-8")
        except OSError:
            continue
        text = _strip_changes(text)
        for sc in _split_scenes(text):
            uts = _split_utterances(sc, names)
            if not uts:
                continue
            counts = {}
            for sp, _ in uts:
                counts[sp] = counts.get(sp, 0) + 1
            top2 = sorted(counts.items(), key=lambda x: -x[1])[:2]
            if len(top2) < 2:
                continue
            total = sum(counts.values())
            share = sum(c for _, c in top2) / total if total else 0
            if share < DOMINANT_SHARE_THRESHOLD:
                continue
            n1, n2 = top2[0][0], top2[1][0]
            xs, ys = [], []
            for sp, ut in uts:
                v = _score_vad(ut)
                if v is None:
                    continue
                if sp == n1:
                    xs.append(v[0])
                elif sp == n2:
                    ys.append(v[0])
            if len(xs) >= 3 and len(ys) >= 3:
                r = _max_lagged_corr(xs, ys)
                if r is not None:
                    sync_scores.append(abs(r))
            if _is_conflict_scene(sc):
                cascade_total += 1
                stages = _detect_gottman_sequence(sc)
                if len({s for s, _ in stages}) >= 3:
                    cascade_complete += 1

    def _pct(xs, p):
        if not xs:
            return None
        xs = sorted(xs)
        k = max(0, min(len(xs) - 1, int(round(p * (len(xs) - 1)))))
        return xs[k]

    return {
        "schema_version": "1.0",
        "_doc": "R19 W8 Batch-X·dialogue contagion signature·SLOW_UPDATE",
        "sync_window_baseline": {
            "p20": _pct(sync_scores, 0.20),
            "p80": _pct(sync_scores, 0.80),
            "sample_n": len(sync_scores),
        },
        "gottman_cascade_freq": (
            cascade_complete / cascade_total if cascade_total else None
        ),
        "gottman_cascade_n": cascade_total,
    }


def _load_author_targets(project_root, style_path):
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
    return q.get("dialogue_contagion_signature")


def scan(draft_path, project_root=None, style_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "dialogue_emotion_contagion", "schema_version": "1.0",
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
    scenes = _split_scenes(text)
    if len(scenes) < MIN_SCENES:
        out["note"] = f"场景数 {len(scenes)} < {MIN_SCENES}·跳过"
        return out

    targets = _load_author_targets(project_root, style_path) or {}
    sync_window_lo = SYNC_WINDOW_LO
    sync_window_hi = SYNC_WINDOW_HI
    if isinstance(targets.get("sync_window_baseline"), dict):
        b = targets["sync_window_baseline"]
        if isinstance(b.get("p20"), (int, float)):
            sync_window_lo = float(b["p20"])
        if isinstance(b.get("p80"), (int, float)):
            sync_window_hi = float(b["p80"])

    findings = []
    sync_results = []
    cascade_results = []
    for idx, sc in enumerate(scenes):
        uts = _split_utterances(sc, names)
        if not uts:
            continue
        counts = {}
        for sp, _ in uts:
            counts[sp] = counts.get(sp, 0) + 1
        top2 = sorted(counts.items(), key=lambda x: -x[1])[:2]
        if len(top2) < 2:
            continue
        total = sum(counts.values())
        share = sum(c for _, c in top2) / total if total else 0
        if share < DOMINANT_SHARE_THRESHOLD:
            continue
        n1, n2 = top2[0][0], top2[1][0]
        xs, ys = [], []
        for sp, ut in uts:
            v = _score_vad(ut)
            if v is None:
                continue
            if sp == n1:
                xs.append(v[0])
            elif sp == n2:
                ys.append(v[0])
        if len(xs) >= 3 and len(ys) >= 3:
            r = _max_lagged_corr(xs, ys)
            if r is not None:
                ar = abs(r)
                sync_results.append({"scene": idx, "n1": n1, "n2": n2, "max_abs_r": round(ar, 3)})
                if ar < sync_window_lo:
                    findings.append({"scene": idx, "kind": "sync_too_loose",
                                     "max_abs_r": round(ar, 3),
                                     "window_lo": round(sync_window_lo, 3)})
                elif ar > sync_window_hi:
                    findings.append({"scene": idx, "kind": "sync_too_tight",
                                     "max_abs_r": round(ar, 3),
                                     "window_hi": round(sync_window_hi, 3)})

        if _is_conflict_scene(sc):
            stages = _detect_gottman_sequence(sc)
            stage_set = {s for s, _ in stages}
            cascade_results.append({"scene": idx, "stages_hit": list(stage_set)})
            missing = [s for s, _ in _GOTTMAN_STAGES if s not in stage_set]
            if len(missing) >= 2:
                findings.append({"scene": idx, "kind": "gottman_incomplete",
                                 "missing": missing})
            # 检测严重顺序颠倒：stonewalling 在 criticism 前
            order_idx = {s: i for i, (s, _) in enumerate(_GOTTMAN_STAGES)}
            for i in range(len(stages) - 1):
                a, b = stages[i][0], stages[i + 1][0]
                if order_idx.get(a, 0) > order_idx.get(b, 0) + 1:
                    findings.append({"scene": idx, "kind": "gottman_reversed",
                                     "order": [s for s, _ in stages]})
                    break

    out["metrics"] = {
        "total_scenes": len(scenes),
        "sync_evaluated_scenes": len(sync_results),
        "conflict_scenes": len(cascade_results),
        "sync_window": [round(sync_window_lo, 3), round(sync_window_hi, 3)],
    }
    out["sync_results"] = sync_results[:8]
    out["cascade_results"] = cascade_results[:8]
    out["author_targets"] = targets

    if findings:
        kinds = sorted({f["kind"] for f in findings})
        msg = (f"双人对话情感传染/Gottman 级联异常·{len(findings)} 处·类型: {','.join(kinds)}")
        if mode == "active":
            out["violations"].append({
                "kind": "dialogue_contagion_abnormal",
                "severity": "minor",
                "code": ISSUE_CODE,
                "message": msg,
                "metrics": out["metrics"],
                "findings": findings[:10],
                "_doc": "R19 W8 Batch-X·情感传染同步窗 + Gottman 4 阶段·advisory",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] dialogue_emotion_contagion: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-X 对话情感传染+Gottman·advisory·shadow")
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
