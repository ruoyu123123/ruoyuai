#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""causal_connector_scanner.py — But-Therefore 因果连接器 scanner（advisory · cluster · 2026-06-29）

🔴 2026-06-29 But-Therefore因果连接器+Swain场景骨架（事件 P0·治流水账·涟漪微观可执行化）

【缺口】South Park『But/Therefore 法则』(beat 间只能 but 冲突 / therefore 后果·禁 and_then 平铺)
是若渝涟漪『因果+信息触发』的天然微观同构。outline-planner(A agent) 在 scene_storyboard 每个
scene 标注 link_to_prev ∈ {but|therefore|and_then}（相邻 beat 衔接类型）。本 scanner 扫相邻
scene 的衔接类型，命中 and_then 平铺（或缺 but/therefore 衔接）→ emit WEAK_CAUSAL_LINK。

【做法 · 确定性纯规则 · 零 LLM/零联网】
  1. 读 _数据库/事件簇.json，取目标 cluster 的 scene_storyboard。
  2. annotated = 含 scene_type / link_to_prev / result_type / Swain beats(goal..decision) 任一的 scene。
     storyboard 完全未标注（旧书 / 旧 storyboard）→ skip（默认安全闸·向后兼容·零行为变化）。
  3. 相邻 scene 衔接评估：
     · link_to_prev == "and_then"          → 平铺弱衔接（flat）
     · link_to_prev 缺失/空 且 scene 已标注 → 缺 but/therefore 衔接（missing）
     · link_to_prev ∈ {but, therefore}     → 强因果衔接（strong）
  4. 弱衔接数 ≥ FLAT_THRESHOLD 或 连续 and_then run ≥ 2 → WEAK_CAUSAL_LINK(advisory)。

【北极星⑤ 顾问非法官】only 查『是否 and-then 平铺』·**绝不卡 but/therefore 比例**（70/30 是英美
影视经验值·不当跨题材硬指标）。场景骨架是参考模板非硬模具·writer 有理由可豁免。
code WEAK_CAUSAL_LINK **绝不进 audit_hub.HARD_GATE_CODES**（违北极星⑤会扼杀 gemini 自由）。
env CAUSAL_CONNECTOR_MODE: off / shadow(默认·只记不判) / active。

用法：
  python causal_connector_scanner.py --project <root> [--cluster <cluster_id>]
  --cluster 省略 → 扫所有含标注 storyboard 的 cluster。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "WEAK_CAUSAL_LINK"

_VALID_SCENE_TYPES = {"proactive_scene", "reactive_sequel"}
_VALID_LINK_TYPES = {"but", "therefore", "and_then"}
_VALID_RESULT_TYPES = {"yes_but", "no_and", "yes_and"}
_SWAIN_BEAT_KEYS = ("goal", "conflict", "disaster", "reaction", "dilemma", "decision")

FLAT_THRESHOLD = 2   # 弱衔接数 ≥ 此值即提示（含 and_then 平铺 + 缺衔接）
AND_THEN_RUN_THRESHOLD = 2   # 连续 and_then run ≥ 此值即提示（真平铺『然后…然后…』）


def _mode() -> str:
    m = (os.environ.get("CAUSAL_CONNECTOR_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _scene_is_annotated(sc: dict) -> bool:
    """scene 是否含 Swain/But-Therefore 标注（任一字段命中）。未标注 → 不参与评估（向后兼容）。"""
    if not isinstance(sc, dict):
        return False
    st = sc.get("scene_type")
    if isinstance(st, str) and st.strip().lower() in _VALID_SCENE_TYPES:
        return True
    ltp = sc.get("link_to_prev")
    if isinstance(ltp, str) and ltp.strip().lower() in _VALID_LINK_TYPES:
        return True
    rt = sc.get("result_type")
    if isinstance(rt, str) and rt.strip().lower() in _VALID_RESULT_TYPES:
        return True
    # Swain beats（顶层或嵌 proactive/reactive 子 dict）
    for src in (sc, sc.get("proactive"), sc.get("reactive")):
        if isinstance(src, dict):
            for k in _SWAIN_BEAT_KEYS:
                v = src.get(k)
                if isinstance(v, str) and v.strip():
                    return True
    return False


def _link_of(sc: dict) -> str | None:
    ltp = sc.get("link_to_prev") if isinstance(sc, dict) else None
    if isinstance(ltp, str) and ltp.strip().lower() in _VALID_LINK_TYPES:
        return ltp.strip().lower()
    return None


def analyze_storyboard(storyboard: list) -> dict:
    """评估一个 cluster 的 scene_storyboard 相邻衔接。返回弱衔接统计。

    annotated_count < 2 → {"skip": True}（无足够标注·向后兼容）。
    """
    if not isinstance(storyboard, list) or len(storyboard) < 2:
        return {"skip": True, "reason": "storyboard 不足 2 scene"}
    annotated = [bool(_scene_is_annotated(sc)) for sc in storyboard]
    if sum(annotated) < 2:
        return {"skip": True, "reason": "未标注 scene_type/link_to_prev/Swain（旧 storyboard·向后兼容）"}

    flat_links: list[dict] = []     # and_then 平铺
    missing_links: list[dict] = []  # 缺 but/therefore 衔接
    strong_links = 0
    eval_links = 0
    run = 0
    max_and_then_run = 0
    for i in range(1, len(storyboard)):
        sc = storyboard[i]
        # 仅当本 scene 或前一 scene 已标注时才评估该衔接（向后兼容混合 storyboard）
        if not (annotated[i] or annotated[i - 1]):
            run = 0
            continue
        eval_links += 1
        link = _link_of(sc)
        if link == "and_then":
            flat_links.append({"between": [i - 1, i], "title": sc.get("title", "")})
            run += 1
            max_and_then_run = max(max_and_then_run, run)
        elif link in ("but", "therefore"):
            strong_links += 1
            run = 0
        else:
            # 已标注 scene 却缺 link_to_prev = 缺 but/therefore 衔接
            missing_links.append({"between": [i - 1, i], "title": sc.get("title", "")})
            run = 0

    weak_count = len(flat_links) + len(missing_links)
    return {
        "skip": False,
        "scene_count": len(storyboard),
        "eval_links": eval_links,
        "strong_links": strong_links,
        "flat_links": flat_links,
        "missing_links": missing_links,
        "weak_count": weak_count,
        "max_and_then_run": max_and_then_run,
        "triggered": (weak_count >= FLAT_THRESHOLD
                      or max_and_then_run >= AND_THEN_RUN_THRESHOLD),
    }


def _iter_clusters(project_root, cluster_id=None):
    data = _load_json(Path(project_root) / "_数据库" / "事件簇.json", {}) or {}
    for c in data.get("clusters", []) or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("cluster_id") or ""
        if cluster_id and not (cid == cluster_id or cid.endswith(str(cluster_id))
                               or str(cluster_id).endswith(cid)):
            continue
        yield cid, c


def scan(project_root, cluster_id=None) -> dict:
    """But-Therefore 因果连接器检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "causal_connector", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    if not project_root or not Path(project_root).exists():
        out["note"] = "无项目根·跳过"
        return out

    per_cluster = []
    flagged = []
    for cid, c in _iter_clusters(project_root, cluster_id):
        res = analyze_storyboard(c.get("scene_storyboard"))
        if res.get("skip"):
            continue
        per_cluster.append({"cluster_id": cid, **{k: res[k] for k in
                            ("eval_links", "strong_links", "weak_count", "max_and_then_run")}})
        if res.get("triggered"):
            samples = (res["flat_links"] + res["missing_links"])[:4]
            msg = (f"{cid}: scene_storyboard 因果衔接偏弱（and_then 平铺 {len(res['flat_links'])} 处"
                   f"·缺 but/therefore 衔接 {len(res['missing_links'])} 处"
                   f"·最长连续 and_then run={res['max_and_then_run']}）。"
                   f"相邻 scene 用 but(冲突)/therefore(后果)衔接·避免『然后…然后…』平铺(流水账)")
            flagged.append({"cluster_id": cid, "message": msg, "samples": samples,
                            "metrics": {k: res[k] for k in
                                        ("flat_links", "missing_links", "weak_count",
                                         "max_and_then_run")}})

    out["per_cluster"] = per_cluster
    if not flagged:
        out["note"] = out.get("note") or "无标注 storyboard 或衔接健康"
        out["violations_count"] = 0
        return out

    if mode == "active":
        for f in flagged:
            out["violations"].append({
                "kind": "causal_connector", "severity": "minor",
                "code": ISSUE_CODE, "cluster_id": f["cluster_id"],
                "message": f["message"], "samples": f["samples"],
                "_doc": ("South Park But/Therefore 法则·只查 and_then 平铺不卡 but/therefore 比例·"
                         "场景骨架是参考模板·writer 有理由可豁免·advisory·绝不 hard_gate")})
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = " · ".join(f["message"] for f in flagged)
    else:
        print("[SHADOW] causal_connector: "
              + " · ".join(f["message"] for f in flagged) + " — 不上报",
              file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="But-Therefore 因果连接器 scanner（advisory · cluster）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--cluster", default=None, help="cluster_id（省略=扫所有标注 storyboard 的 cluster）")
    args = ap.parse_args()
    rep = scan(args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
