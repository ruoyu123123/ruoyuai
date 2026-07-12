#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plot_armor_tracker.py — Plot Armor Stakes Erosion 跟踪（advisory · cluster · 2026-06-20 R9 W5 Batch-N P1）

【缺口】R9 W5 联网调研：plot armor（主角光环）是 LLM 长篇叙事系统性 bug——威胁连发但
0 后果 = stakes_credibility 坍塌，读者下意识知道主角不会真受伤。此前全系统【零覆盖】。
debt_ledger 查的是叙事承诺·sagging_middle 查的是事件烈度·**没有任何 scanner 跟踪"威胁
是否兑现成实际代价"**。本 scanner 补此缺口：跨 3 cluster 滚动窗口看 threat_density vs
cost_persistence——威胁高 + 实际代价低 = plot armor 通胀。

【做法 · 确定性零依赖纯规则】：
  1. 读 cluster 草稿统计 threat 三档命中（轻伤/重伤/濒死·中文词典）。
  2. 从 _数据库/故事块摘要.json 读最近 N=3 cluster 的 changes.json（含本 cluster）找 cost_events
     ——身份伤亡持久化、道具丢失未追回、关系破裂未修复（state_delta + 道具.json holder
     + 角色档 status 三处采证）。
  3. 计算 stakes_credibility = cost_events_count / max(threat_count, 1)。
     · threat_count < 3 → 样本不足跳过
     · stakes_credibility < 0.2 → advisory PLOT_ARMOR_INFLATION
  4. 题材门控：轻喜剧/slice_of_life/搞笑日常默认 skip（喜剧本身就不该有真伤亡感）。
  5. 输出 snapshot 到 _数据库/.cross_cluster_scan/plot_armor_snapshot.json
     供 build_manifest 下卷注入软提示。

【北极星② / ⑤ 顾问非法官】危险/伤亡是创作选择·作者档 plot_armor_profile.allow_high_armor=True
  可豁免（无敌流/爽文）·全 advisory，code PLOT_ARMOR_INFLATION **绝不进 audit_hub.HARD_GATE_CODES**。
  env PLOT_ARMOR_MODE: off / shadow(默认·只记不判·零回归) / active。

用法：python plot_armor_tracker.py <draft_path> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "PLOT_ARMOR_INFLATION"  # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 威胁词典三档（中文·高确定性·剔除虚指比喻类）
THREAT_LIGHT = re.compile(
    r"(擦伤|淤青|皮肉伤|轻伤|挂彩|流了点血|划破|蹭破|崴脚|扭伤)")
THREAT_HEAVY = re.compile(
    r"(重伤|骨折|断了.{0,3}骨|内脏受损|大量失血|失明|残废|致残|"
    r"刺穿|贯穿|断肢|削去.{0,3}肢|血流如注|奄奄一息)")
THREAT_LETHAL = re.compile(
    r"(濒死|垂死|断气|没了呼吸|心脉断绝|当场毙命|身亡|阵亡|殒命|魂飞魄散|"
    r"必死无疑|九死一生|险些丧命|与死神擦肩)")


# 轻喜剧/日常题材门控（这些类型本就不该有真伤亡感·skip）
COMEDY_GENRES = {"comedy_light", "slice_of_life", "fluff", "daily_comedy",
                 "school_comedy", "romcom"}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
WINDOW_N = 3  # 滚动窗口 cluster 数
MIN_THREAT_COUNT = 3  # 样本下限
CREDIBILITY_FLOOR = 0.2  # 低于此判 plot armor inflation


def _mode() -> str:
    m = (os.environ.get("PLOT_ARMOR_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_genre(project_root) -> str:
    """读 genre · 优先 manifest > 用户偏好 > 作者档。"""
    if not project_root:
        return ""
    db = Path(project_root) / "_数据库"
    for fname in ("用户偏好.json", "作者风格.json"):
        p = db / fname
        if not p.exists():
            continue
        obj = _read_json(p)
        if isinstance(obj, dict):
            g = obj.get("genre") or obj.get("primary_genre")
            if isinstance(g, str) and g.strip():
                return g.strip()
    return ""


def _read_author_armor_profile(project_root):
    if not project_root:
        return {}
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return {}
    prof = obj.get("plot_armor_profile")
    return prof if isinstance(prof, dict) else {}


def detect_threats(text: str) -> dict:
    """统计三档威胁命中。"""
    text = _strip_changes(text)
    light = [m.group(0) for m in THREAT_LIGHT.finditer(text)]
    heavy = [m.group(0) for m in THREAT_HEAVY.finditer(text)]
    lethal = [m.group(0) for m in THREAT_LETHAL.finditer(text)]
    return {"light": light, "heavy": heavy, "lethal": lethal,
            "total": len(light) + len(heavy) + len(lethal)}


def _cost_events_from_changes(changes: dict) -> int:
    """从 changes.json 抽 cost_events：cluster_constraints_violated/factual 里的持久化代价。"""
    if not isinstance(changes, dict):
        return 0
    cnt = 0
    # 显式 cost_events 字段（如果未来有）
    ce = changes.get("cost_events")
    if isinstance(ce, list):
        cnt += sum(1 for e in ce if e)
    # factual 段（cluster 摘要里的事实变更）
    fact = changes.get("factual")
    if isinstance(fact, dict):
        for key in ("character_state_changes", "deaths", "injuries",
                    "items_lost", "relations_broken"):
            v = fact.get(key)
            if isinstance(v, list):
                cnt += len(v)
            elif isinstance(v, dict):
                cnt += len(v)
    return cnt


def _recent_clusters(project_root, current_cluster_id, n=WINDOW_N) -> list:
    """读最近 N 个 cluster 的 changes.json 路径。"""
    if not project_root:
        return []
    proj = Path(project_root)
    summary = _read_json(proj / "_数据库" / "故事块摘要.json")
    cluster_ids = []
    if isinstance(summary, dict):
        clusters = summary.get("clusters")
        if isinstance(clusters, list):
            cluster_ids = [c.get("cluster_id") for c in clusters
                           if isinstance(c, dict) and c.get("cluster_id")]
        elif isinstance(clusters, dict):
            cluster_ids = list(clusters.keys())
    # 兜底：扫 章节/ 下面的 cluster_*_draft 目录
    if not cluster_ids:
        chapters = proj / "章节"
        if chapters.exists():
            cluster_ids = sorted([d.name.replace("_draft", "")
                                  for d in chapters.iterdir()
                                  if d.is_dir() and d.name.startswith("cluster_")
                                  and d.name.endswith("_draft")])
    # 锁定当前及之前的
    if current_cluster_id and current_cluster_id in cluster_ids:
        idx = cluster_ids.index(current_cluster_id)
        window = cluster_ids[max(0, idx - n + 1): idx + 1]
    else:
        window = cluster_ids[-n:]
    return window


def _changes_for_cluster(project_root, cluster_id) -> dict:
    """读 cluster changes.json。"""
    if not (project_root and cluster_id):
        return {}
    proj = Path(project_root)
    candidates = [
        proj / "章节" / f"{cluster_id}_draft" / f"{cluster_id}_changes.json",
        proj / "章节" / f"{cluster_id}_draft" / f"{cluster_id}_changes.flash.json",
    ]
    for p in candidates:
        if p.exists():
            obj = _read_json(p)
            if isinstance(obj, dict):
                return obj
    return {}


def _write_snapshot(project_root, payload):
    if not project_root:
        return
    out_dir = Path(project_root) / "_数据库" / ".cross_cluster_scan"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "plot_armor_snapshot.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "plot_armor_tracker", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out

    # 题材门控
    genre = _read_genre(project_root).lower()
    if genre and any(g in genre for g in COMEDY_GENRES):
        out["note"] = f"轻喜剧/日常题材({genre})·跳过(plot armor 在此非 bug)"
        return out

    # 作者档豁免（无敌流/爽文）
    armor_prof = _read_author_armor_profile(project_root)
    if armor_prof.get("allow_high_armor") is True:
        out["note"] = "作者档 plot_armor_profile.allow_high_armor=True·跳过"
        return out

    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft_body = _strip_changes(draft)
    cjk = _cjk_count(draft_body)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    # 本 cluster 威胁
    threats = detect_threats(draft_body)
    out["current_threats"] = {"light": len(threats["light"]),
                              "heavy": len(threats["heavy"]),
                              "lethal": len(threats["lethal"]),
                              "total": threats["total"]}

    # 读 manifest 拿 cluster_id
    manifest = _read_json(Path(manifest_path)) if manifest_path else None
    cluster_id = ""
    if isinstance(manifest, dict):
        cluster_id = (manifest.get("cluster_id") or manifest.get("cluster_key")
                      or "")

    # 滚动窗口 N=3 cluster cost_events
    window = _recent_clusters(project_root, cluster_id, n=WINDOW_N)
    cost_total = 0
    window_threat_total = threats["total"]
    for cid in window:
        ch = _changes_for_cluster(project_root, cid)
        cost_total += _cost_events_from_changes(ch)
    out["window_clusters"] = window
    out["window_cost_events"] = cost_total
    out["window_threat_total"] = window_threat_total

    if window_threat_total < MIN_THREAT_COUNT:
        out["note"] = (f"威胁样本不足({window_threat_total}<{MIN_THREAT_COUNT})·"
                       f"跳过 plot armor 判定")
        # 仍写 snapshot 给 build_manifest（便于历史比对）
        _write_snapshot(project_root, {"cluster_id": cluster_id,
                                       "threat_total": window_threat_total,
                                       "cost_total": cost_total,
                                       "stakes_credibility": None,
                                       "verdict": "skip_insufficient_sample"})
        return out

    credibility = round(cost_total / max(window_threat_total, 1), 3)
    out["stakes_credibility"] = credibility

    if credibility < CREDIBILITY_FLOOR and window_threat_total >= MIN_THREAT_COUNT:
        msg = (f"Plot Armor 通胀: 滚动 {len(window)} cluster 窗口威胁 "
               f"{window_threat_total} 次 vs 持久化代价 {cost_total} 次"
               f"(credibility={credibility} < {CREDIBILITY_FLOOR})·"
               f"建议下一 cluster 让某次威胁真正兑现为不可逆代价")
        violation = {
            "code": ISSUE_CODE, "kind": "plot_armor_inflation",
            "severity": "minor", "message": msg,
            "stakes_credibility": credibility,
            "threat_total": window_threat_total,
            "cost_total": cost_total,
            "window_clusters": window,
            "_doc": "advisory·作者档 plot_armor_profile.allow_high_armor 可豁免·绝不 hard_gate",
        }
        if mode == "active":
            out["violations"].append(violation)
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] plot_armor: {msg} — 不上报", file=sys.stderr)

    # snapshot 给 build_manifest 下卷注入
    _write_snapshot(project_root, {
        "cluster_id": cluster_id,
        "window_clusters": window,
        "threat_total": window_threat_total,
        "cost_total": cost_total,
        "stakes_credibility": credibility,
        "verdict": out["verdict"],
    })

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Plot Armor Stakes Erosion 跟踪(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
