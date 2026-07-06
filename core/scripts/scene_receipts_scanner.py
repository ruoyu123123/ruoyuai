#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scene_receipts_scanner.py — 场景回执：storyboard → 草稿覆盖证据（确定性·零 LLM）

【出处 · P1 移植 · 2026-07-06】borrowed from LongWriter/AgentWrite「plan-then-write
回执」（先规划后写作，写完逐 plan 项核对产出）+ moyin-creator「场景校准」（分场景
校准产物与分镜对齐）。见 research/open_source_writing_systems.md P1。

【闭环缺口】outline-planner 产 scene_storyboard、build_manifest 注入 writer——
「storyboard 教了」；但 writer freestyle 整块一把梭写完后**没有人查覆盖**。本 scanner
在 cluster 草稿写完后做确定性后置扫描：把每个 storyboard 场景映射到草稿中的覆盖
证据（角色名 + 锚点名词共现窗口），产出回执 artifact。

【北极星⑤ · 绝不 hard_gate】writer freestyle 合并/改编/重排场景是创作自由——
疑似未覆盖只产 advisory issue（SCENE_RECEIPT_COVERAGE_GAP），且阈值保守：
场景数≥2 且 coverage_ratio < 0.6 才报。env SCENE_RECEIPTS_MODE 三态
（off/shadow/active·默认 shadow·shadow 只 stderr 不产 violations）。

【做法 · 确定性匹配（纯字符串/正则·不用嵌入）】
  - cluster 反查：cluster_lookup.normalize_cluster_id 权威归一
    （禁 f"cluster_{ch:03d}" 机械拼接）；--cluster 缺省时从 draft 路径推 key。
  - 场景锚点：characters/participants/focal_character（角色名）+ location/hub
    （地点）+ props（道具名词）+ scene_goal/summary/description/title 的 CJK
    2-gram 集合（无分词依赖的关键词近似）。
  - 覆盖判定：草稿按空行段落块聚合 → 相邻双段窗口滑动 → 首个「角色名 + 锚点
    名词（地点/道具/goal-gram 重叠达阈值）共现」的窗口 = 覆盖证据（match_pos）。
  - 输出：stdout JSON + 落盘 _数据库/.audit/cluster_<key>_scene_receipts.json。

storyboard 为空 / 无 --project / 反查不到 = 合法 fluid（cluster_002+ 骨架），
优雅 skip（exit 0 + note），绝不硬失败。

用法: python scene_receipts_scanner.py <draft> --project <root> [--cluster cluster_<key>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import cluster_lookup

ISSUE_CODE = "SCENE_RECEIPT_COVERAGE_GAP"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 保守阈值：freestyle 合并/改编场景是常态，只有覆盖率明显偏低才报
MIN_SCENES_TO_REPORT = 2
COVERAGE_RATIO_FLOOR = 0.6

_KEY_FROM_PATH = re.compile(r"cluster_([0-9]{3}[a-z]?)_draft")
_CJK = re.compile(r"[一-鿿]")


def _mode() -> str:
    m = (os.environ.get("SCENE_RECEIPTS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _as_str_list(value) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _cjk_bigrams(text: str) -> set[str]:
    """纯 CJK 2-gram 集合（跨标点不成 gram·无分词依赖的关键词近似）。"""
    grams: set[str] = set()
    run: list[str] = []
    for ch in text:
        if _CJK.match(ch):
            run.append(ch)
        else:
            for i in range(len(run) - 1):
                grams.add(run[i] + run[i + 1])
            run = []
    for i in range(len(run) - 1):
        grams.add(run[i] + run[i + 1])
    return grams


def _resolve_cluster_id(cluster_arg, draft_path) -> str | None:
    """--cluster 优先（cluster_lookup 权威归一）；缺省时从 draft 路径推 key。"""
    cid = cluster_lookup.normalize_cluster_id(cluster_arg) if cluster_arg else None
    if cid:
        return cid
    m = _KEY_FROM_PATH.search(str(draft_path))
    if m:
        return cluster_lookup.normalize_cluster_id(f"cluster_{m.group(1)}")
    return None


def _load_storyboard(project_root, cluster_id) -> list[dict]:
    """从 事件簇.json 反查该 cluster 的 scene_storyboard（查不到返回 []）。"""
    path = Path(project_root) / "_数据库" / "事件簇.json"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    clusters = obj.get("clusters") if isinstance(obj, dict) else None
    if not isinstance(clusters, list):
        return []
    for cl in clusters:
        if not isinstance(cl, dict):
            continue
        if cluster_lookup.normalize_cluster_id(cl.get("cluster_id")) == cluster_id:
            sb = cl.get("scene_storyboard")
            return [s for s in sb if isinstance(s, dict)] if isinstance(sb, list) else []
    return []


def _scene_anchors(scene: dict, index: int) -> dict:
    """从场景 dict 常见字段抽取可匹配锚点（schema/example 双口径兼容：
    scene_index/scene_n · scene_goal/summary/description/title ·
    characters/participants/focal_character · location/hub · props）。"""
    names = _as_str_list(scene.get("characters")) + _as_str_list(scene.get("participants"))
    focal = str(scene.get("focal_character") or "").strip()
    if focal:
        names.append(focal)
    names = list(dict.fromkeys(n for n in names if len(n) >= 2))

    locations = list(dict.fromkeys(
        _as_str_list(scene.get("location")) + _as_str_list(scene.get("hub"))))
    props = list(dict.fromkeys(
        _as_str_list(scene.get("props")) + _as_str_list(scene.get("key_props"))
        + _as_str_list(scene.get("anchor_props"))))

    goal_parts = [str(scene.get(k) or "").strip()
                  for k in ("scene_goal", "summary", "description", "title", "scene_title")]
    goal_text = " ".join(p for p in goal_parts if p)
    goal_grams = _cjk_bigrams(goal_text)
    # 短 goal 降阈值、长 goal 封顶，防「越详细越难命中」
    gram_min = max(3, min(6, len(goal_grams) // 4)) if goal_grams else 0

    raw_idx = scene.get("scene_index")
    if raw_idx is None:
        raw_idx = scene.get("scene_n")
    try:
        scene_index = int(raw_idx)
    except (TypeError, ValueError):
        scene_index = index

    preview_src = str(scene.get("scene_goal") or scene.get("summary")
                      or scene.get("description") or scene.get("title") or "")
    return {
        "scene_index": scene_index,
        "preview": preview_src.strip()[:60],
        "names": names,
        "locations": locations,
        "props": props,
        "goal_grams": goal_grams,
        "gram_min": gram_min,
        "evaluable": bool(names or locations or props or len(goal_grams) >= 4),
    }


def _paragraph_windows(text: str) -> list[dict]:
    """空行段落块聚合 → 相邻双段滑动窗口（带 char 偏移）。"""
    blocks: list[dict] = []
    pos = 0
    for chunk in re.split(r"\n\s*\n", text):
        start = text.find(chunk, pos)
        if start < 0:
            start = pos
        if chunk.strip():
            blocks.append({"start": start, "text": chunk})
        pos = start + len(chunk)
    windows: list[dict] = []
    for i, blk in enumerate(blocks):
        nxt = blocks[i + 1]["text"] if i + 1 < len(blocks) else ""
        windows.append({"start": blk["start"], "text": blk["text"] + "\n" + nxt})
    return windows


def _match_scene(anchors: dict, windows: list[dict]) -> tuple[int | None, list[str]]:
    """定位首个强命中窗口：角色名 + 锚点名词（地点/道具/goal-gram 重叠）共现。
    返回 (match_pos | None, anchor_hits)。"""
    gram_min = anchors["gram_min"]
    for win in windows:
        wt = win["text"]
        name_hits = [n for n in anchors["names"] if n in wt]
        loc_hits = [t for t in anchors["locations"] if t in wt]
        prop_hits = [t for t in anchors["props"] if t in wt]
        gram_hits = 0
        if gram_min:
            win_grams = _cjk_bigrams(wt)
            gram_hits = len(anchors["goal_grams"] & win_grams)

        strong = False
        if name_hits and (loc_hits or prop_hits or (gram_min and gram_hits >= gram_min)):
            strong = True
        elif not anchors["names"] and loc_hits and (
                prop_hits or (gram_min and gram_hits >= gram_min)):
            strong = True
        elif gram_min and gram_hits >= gram_min * 2:
            # 高 gram 重叠兜底：writer 改名/换地点但内容明显在写这一场（freestyle 改编常态）
            strong = True
        if strong:
            hits = name_hits[:3] + loc_hits[:2] + prop_hits[:2]
            if gram_min and gram_hits >= gram_min:
                hits.append(f"goal_grams×{gram_hits}")
            return win["start"], hits
    return None, []


def _write_artifact(project_root, cluster_id, report) -> None:
    if not project_root or not cluster_id:
        return
    key = cluster_id[len("cluster_"):] if cluster_id.startswith("cluster_") else cluster_id
    path = Path(project_root) / "_数据库" / ".audit" / f"cluster_{key}_scene_receipts.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def scan(draft_path, project_root=None, cluster_arg=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "scene_receipts",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "receipts": [],
        "coverage_ratio": None,
        "violations": [],
        "verdict": "PASS",
        "warning": None,
    }
    if mode == "off":
        return out

    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as exc:
        out["note"] = f"draft read failed: {str(exc)[:120]}"
        return out
    text = _strip_changes(raw)

    if not project_root:
        out["note"] = "no --project; storyboard unavailable; skipped"
        return out
    cluster_id = _resolve_cluster_id(cluster_arg, draft_path)
    out["cluster_id"] = cluster_id
    if not cluster_id:
        out["note"] = "cluster id unresolved (no --cluster and draft path has no cluster_<key>_draft); skipped"
        return out

    storyboard = _load_storyboard(project_root, cluster_id)
    if not storyboard:
        # cluster_002+ 骨架空 storyboard = 合法 fluid（涌现未详化），优雅 skip
        out["note"] = "empty scene_storyboard (legal fluid skeleton); skipped"
        _write_artifact(project_root, cluster_id, out)
        return out

    windows = _paragraph_windows(text)
    receipts = []
    evaluable = 0
    matched = 0
    for i, scene in enumerate(storyboard):
        anchors = _scene_anchors(scene, i)
        receipt = {
            "scene_index": anchors["scene_index"],
            "scene_goal_preview": anchors["preview"],
            "matched": None,
            "match_pos": None,
            "anchor_hits": [],
        }
        if anchors["evaluable"]:
            evaluable += 1
            pos, hits = _match_scene(anchors, windows)
            receipt["matched"] = pos is not None
            receipt["match_pos"] = pos
            receipt["anchor_hits"] = hits
            if pos is not None:
                matched += 1
        else:
            receipt["note"] = "no extractable anchors; not counted"
        receipts.append(receipt)

    out["receipts"] = receipts
    out["scene_count"] = len(storyboard)
    out["evaluable_count"] = evaluable
    if evaluable:
        out["coverage_ratio"] = round(matched / evaluable, 3)

    if (len(storyboard) >= MIN_SCENES_TO_REPORT and evaluable >= MIN_SCENES_TO_REPORT
            and out["coverage_ratio"] is not None
            and out["coverage_ratio"] < COVERAGE_RATIO_FLOOR):
        missing = [r["scene_index"] for r in receipts if r["matched"] is False]
        msg = (f"storyboard 覆盖回执偏低：{matched}/{evaluable} 场找到覆盖证据"
               f"（coverage={out['coverage_ratio']} < {COVERAGE_RATIO_FLOOR}）·"
               f"疑似未覆盖场景 index={missing}·freestyle 合并/改编可豁免")
        if mode == "active":
            out["violations"].append({
                "kind": "scene_receipt_coverage_gap",
                "severity": "minor",
                "code": ISSUE_CODE,
                "message": msg,
                "coverage_ratio": out["coverage_ratio"],
                "missing_scene_indices": missing,
                "_doc": ("LongWriter/AgentWrite plan-then-write 回执 + moyin-creator 场景校准·"
                         "advisory·writer freestyle 改编是创作自由·绝不 hard_gate"),
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] scene_receipts: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    _write_artifact(project_root, cluster_id, out)
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="场景回执：storyboard → 草稿覆盖证据（确定性·advisory·shadow 默认）")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
