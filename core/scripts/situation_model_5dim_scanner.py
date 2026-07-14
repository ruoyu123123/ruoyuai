#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""situation_model_5dim_scanner.py — Zwaan 5 维读者构建跟踪（time/space/causation/intentionality/protagonist）

【缺口 · R18 W7 Batch-S·P0 · 2026-06-21】Zwaan 1998 event-indexing model +
arxiv 2506.* situation model 2026 + Cognitive Load Theory Sweller 2024：
  读者在阅读时构建 situation model（5 维）：时间/空间/因果/意图/主角。任一维度突
  变而无 transition marker → 读者注意力断裂、需重建心智模型 = 阅读流断。R7-R13
  共 101 条 100%是 craft-output 层（写法/句法/结构），从未触及读者心智 situation
  model 这一认知层。

【5 维突变检测 · 简化占位（场景间）】
  ① time（时间锚突变）：scene 前后段时间锚词突变（明天/三日后/数年后/翌日 / 入夜）
     - 突变定义：前段无该锚 且 后段开头 ±30 CJK 内出现且无 marker
     - marker = {翌日/数日后/三日后/转眼/良久/与此同时/同一时刻}
  ② space（空间锚突变）：场景词突变（城/府/院/山/江/路/室）
     - 突变 = 后段 locations 集合与前段交集为空
     - marker = {来到/抵达/转身离开/沿途/走出/回到}
  ③ causation（因果连词缺失）：scene 内因果连词 density < 0.3/kCJK 且 >800 CJK
     - 连词 = {因为/所以/于是/便/因此/故而/故此/导致}
  ④ intentionality（角色 goal 突变）：scene 内主角动机词缺失（要/想/打算/决意/打定主意）
  ⑤ protagonist（焦点切换）：连续场景 focalizer 切换且无 marker
     - 简化：扫场景主角名出现频次·若 scene 主角占比 < 20% 标 dropout

  每命中一维 → SITUATION_MODEL_DIM_DROPOUT advisory；作者档可声明
  `dim_dropout_tolerance: [time, space, ...]` 旁路对应维度。

【北极星⑤】顾问非法官·全 advisory·env SITUATION_MODEL_5DIM_MODE
  SITUATION_MODEL_DIM_DROPOUT 绝不进 audit_hub.HARD_GATE_CODES。

用法: python situation_model_5dim_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "SITUATION_MODEL_DIM_DROPOUT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 时间锚 / 空间锚 / 因果 / 意图 / 焦点 markers
TIME_ANCHORS = ("明天", "三日后", "数年后", "翌日", "入夜", "次日", "数日后",
                "片刻", "良久", "半晌", "片晌", "晨曦", "黄昏", "深夜")
TIME_MARKERS = ("翌日", "数日后", "三日后", "转眼", "良久", "与此同时", "同一时刻",
                "过了", "之后", "其后", "数日间", "次日清晨")
SPACE_TOKENS = re.compile(r"([一-鿿]{1,3}(?:城|府|院|山|江|路|室|宫|殿|村|镇|楼|阁|寺|庙|林|岭|坡))")
SPACE_MARKERS = ("来到", "抵达", "转身离开", "沿途", "走出", "回到", "进入", "踏入")
CAUSAL_CONJUNCTIONS = ("因为", "所以", "于是", "因此", "故而", "故此", "导致", "便")
INTENT_VERBS = ("要", "想", "打算", "决意", "打定主意", "意欲", "图谋", "想要")
# protagonist：focalizer 占比阈值
FOCALIZER_DROPOUT_RATIO = 0.20  # 主角在场景出现 < 20% → dropout

SCENE_SPLIT = re.compile(r"\n\s*[*◇◆━─=]{3,}\s*\n|\n\s*场景[:：]\s*[^\n]*\n|"
                         r"\n\s*第[一二三四五六七八九十0-9]+幕[^\n]*\n")


def _mode() -> str:
    m = (os.environ.get("SITUATION_MODEL_5DIM_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源
import protagonist_lookup  # noqa: E402 主角反查单一真理源


def _split_scenes(text: str):
    parts = SCENE_SPLIT.split(text)
    out = [p.strip() for p in parts if p and p.strip()]
    return out if out else [text]


def _load_protagonist_and_tolerance(project_root):
    """返回 (protagonist_name, tolerance_set)·主角走 protagonist_lookup 唯一反查。"""
    tol = set()
    if not project_root:
        return None, tol
    protag = protagonist_lookup.resolve_protagonist(project_root)
    for fname in ("作者风格_FINAL.json", "作者风格.json"):
        ap = Path(project_root) / "_数据库" / fname
        if not ap.exists():
            continue
        try:
            obj = json.loads(ap.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                t = obj.get("dim_dropout_tolerance") or []
                if isinstance(t, list):
                    tol = {str(x).lower() for x in t}
                    break
        except (OSError, json.JSONDecodeError):
            pass
    return protag, tol


def _scene_signals(scene_text):
    cjk = _cjk_count(scene_text)
    head = scene_text[:60]   # 场景开头窗口
    time_in_head = [t for t in TIME_ANCHORS if t in head]
    has_time_marker = any(m in head for m in TIME_MARKERS)
    locations = set(SPACE_TOKENS.findall(scene_text))
    has_space_marker = any(m in head for m in SPACE_MARKERS)
    causal_hits = sum(scene_text.count(c) for c in CAUSAL_CONJUNCTIONS)
    causal_density = causal_hits / max(cjk, 1) * 1000.0
    intent_hits = sum(scene_text.count(v) for v in INTENT_VERBS)
    return {
        "cjk": cjk,
        "time_anchors_head": time_in_head,
        "has_time_marker": has_time_marker,
        "locations": locations,
        "has_space_marker": has_space_marker,
        "causal_density_per_kcjk": round(causal_density, 3),
        "intent_hits": intent_hits,
    }


def _detect_dropouts(scenes, protag, tol):
    """返回 {dim: hit_count, samples}·dim ∈ time/space/causation/intentionality/protagonist"""
    sigs = [_scene_signals(s) for s in scenes]
    dropouts = {"time": [], "space": [], "causation": [], "intentionality": [],
                "protagonist": []}
    for i, (prev, cur, scene) in enumerate(zip(sigs, sigs[1:], scenes[1:]), start=1):
        # ① time
        if "time" not in tol:
            if cur["time_anchors_head"] and not prev["time_anchors_head"]\
                    and not cur["has_time_marker"]:
                dropouts["time"].append({"scene_idx": i,
                                         "anchor": cur["time_anchors_head"][0]})
        # ② space
        if "space" not in tol:
            if cur["locations"] and prev["locations"]\
                    and not (cur["locations"] & prev["locations"])\
                    and not cur["has_space_marker"]:
                dropouts["space"].append({
                    "scene_idx": i,
                    "prev_loc": next(iter(prev["locations"]), ""),
                    "cur_loc": next(iter(cur["locations"]), ""),
                })
        # ③ causation
        if "causation" not in tol:
            if cur["cjk"] > 800 and cur["causal_density_per_kcjk"] < 0.3:
                dropouts["causation"].append({"scene_idx": i,
                                              "density": cur["causal_density_per_kcjk"]})
        # ④ intentionality
        if "intentionality" not in tol:
            if cur["cjk"] > 600 and cur["intent_hits"] == 0:
                dropouts["intentionality"].append({"scene_idx": i,
                                                   "intent_hits": 0})
        # ⑤ protagonist
        if "protagonist" not in tol and protag:
            count_p = scene.count(protag)
            ratio = count_p / max(cur["cjk"] / 100.0, 1)   # 每 100 CJK 出现次数
            if cur["cjk"] > 600 and ratio < FOCALIZER_DROPOUT_RATIO:
                dropouts["protagonist"].append({"scene_idx": i,
                                                "focalizer_ratio": round(ratio, 3)})
    return dropouts


def _scene_coherence_evidence(scenes: list[str]) -> dict:
    """旁挂相邻场景 coherence 分数；只做 evidence，不改变 dropout 裁决。"""
    pairs = [(scenes[i], scenes[i + 1]) for i in range(len(scenes) - 1)]
    evidence = {
        "status": "unavailable",
        "source": None,
        "boundary_scores": [],
    }
    if not pairs or os.environ.get("RUOYU_NN_COHERENCE") != "1":
        return evidence
    preds = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled as feature_store_enabled
        if feature_store_enabled():
            preds = FeatureStore.get().compute_coherence_pairs(pairs)
            evidence["source"] = "feature_store_coherence"
    except Exception:
        preds = None
    if preds is None:
        try:
            import nn_coherence_bridge
            preds = nn_coherence_bridge.predict_pairs(pairs)
            evidence["source"] = "nn_coherence_bridge"
        except Exception:
            return evidence
    scores = []
    for idx, pred in enumerate(preds or [], start=1):
        if not pred:
            continue
        score = pred.get("coherence")
        if score is None:
            score = pred.get("coherence_score")
        if score is None:
            score = pred.get("score")
        try:
            scores.append({"boundary_idx": idx, "coherence": round(float(score), 4)})
        except (TypeError, ValueError):
            continue
    if scores:
        evidence["status"] = "ok"
        evidence["boundary_scores"] = scores
        evidence["min_coherence"] = min(s["coherence"] for s in scores)
        evidence["mean_coherence"] = round(
            sum(s["coherence"] for s in scores) / len(scores), 4)
    return evidence


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "situation_model_5dim", "schema_version": "1.0",
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
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    protag, tol = _load_protagonist_and_tolerance(project_root)
    out["protagonist"] = protag
    out["dim_tolerance"] = sorted(tol)
    scenes = _split_scenes(text)
    out["scene_count"] = len(scenes)
    if len(scenes) < 2:
        out["note"] = "场景<2·无法对比·跳过"
        return out

    dropouts = _detect_dropouts(scenes, protag, tol)
    out["model_coherence_at_boundaries"] = _scene_coherence_evidence(scenes)
    total_hits = sum(len(v) for v in dropouts.values())
    out["dropout_summary"] = {k: len(v) for k, v in dropouts.items()}
    out["dropout_samples"] = {k: v[:3] for k, v in dropouts.items() if v}
    out["total_dropout_hits"] = total_hits

    if total_hits > 0:
        msg = ("situation_model dropout: "
               + "·".join(f"{k}={len(v)}" for k, v in dropouts.items() if v))
        if mode == "active":
            out["violations"].append({
                "kind": "situation_model_dim_dropout", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "dropout_summary": out["dropout_summary"],
                "_doc": "Zwaan 1998·读者 situation model·梦境/蒙太奇可豁免·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] situation_model_5dim: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Zwaan 5 维 situation model dropout·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
