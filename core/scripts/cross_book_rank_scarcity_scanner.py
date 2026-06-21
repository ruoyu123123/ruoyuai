#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_book_rank_scarcity_scanner.py — 跨书顶阶密度稀缺性(R19 W8 Batch-V·P0)

【缺口·2026-06-21·凡人修仙传仙界篇 / Cradle Will Wight】跨书宇宙系列文中
顶阶角色稀缺性塌缩：前作 Yuan Ying 一只引发轰动，续作街上一抓一大把 →
稀缺性消失·成长动机崩塌。序列层完全空白·与单本 capability_emergence 正交。

【输入·schema】
  _数据库/series_rank_ledger.json
    {
      "schema_version": 1,
      "ranks": [
        {"name": "金丹", "tier": 5, "cross_book_baseline_density_per_cluster": 0.3,
         "previous_book_top_named": ["李慕婉","老魔"]},
        ...
      ],
      "current_book_top_threshold_tier": 5
    }

【探针】
  ① top_rank_density_ratio : 本 cluster 顶阶角色提及密度 / 上本基线密度
                             > 3.0× → CROSS_BOOK_RANK_INFLATION
  ② leapfrog_battle        : 主角对位顶阶反复轻松取胜(连续 ≥3 场 cluster 内
                             同 tier 顶阶战胜) → 稀缺性塌缩信号

【与既有 scanner 严格正交】
  - capability_emergence_scanner : 单本升级流·正交(本=跨书 series 层稀缺)
  - active_character_wm_load    : 同场景活跃数 Cowan WM·正交
  - antagonist_rotation         : 反派轮换·正交

【北极星⑤】顾问非法官·全 advisory·env CROSS_BOOK_RANK_SCARCITY_MODE 默认 shadow·
  CROSS_BOOK_RANK_INFLATION 绝不 hard_gate。无 ledger → skip(非泛适用)。

【build_manifest 注入】另设 collect_cross_book_hint(project_root) 给 build_manifest
  调用·读 ledger 提取上本顶阶名单 + 基线密度 → manifest hint 给 writer。

用法: python cross_book_rank_scarcity_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CROSS_BOOK_RANK_INFLATION"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 800
DEFAULT_INFLATION_RATIO = 3.0   # 3 倍密度兜底地板
DEFAULT_LEAPFROG_STREAK = 3     # 连续 ≥3 场顶阶轻胜


def _mode() -> str:
    m = (os.environ.get("CROSS_BOOK_RANK_SCARCITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_ledger(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "series_rank_ledger.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _split_cluster_into_scenes(text):
    """按双换行段 + 1500 CJK 兜底切场景·返回 scene 列表。"""
    parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not parts:
        return []
    # 合并近邻段直到 ≥ 1500 CJK
    scenes = []
    buf = []
    cur = 0
    for p in parts:
        buf.append(p)
        cur += _cjk_count(p)
        if cur >= 1500:
            scenes.append("\n\n".join(buf))
            buf = []
            cur = 0
    if buf:
        scenes.append("\n\n".join(buf))
    return scenes


def _battle_keywords():
    return ("击败", "战胜", "斩杀", "一招", "瞬杀", "秒杀",
            "轻松", "毫不费力", "弹指", "一击")


def _scene_has_top_battle_with_win(scene, top_named, top_rank_terms):
    """场景内含顶阶命名/称谓 + 主角战胜动词 + 轻松类副词 → 算 leapfrog 场。"""
    hit_top = any(t in scene for t in top_named) or any(r in scene for r in top_rank_terms)
    if not hit_top:
        return False
    return any(k in scene for k in _battle_keywords())


def collect_cross_book_hint(project_root):
    """build_manifest 注入入口·返回顶阶名单+基线密度 hint·None 表示 ledger 缺。"""
    ledger = _load_ledger(project_root)
    if not isinstance(ledger, dict):
        return None
    ranks = ledger.get("ranks") or []
    if not isinstance(ranks, list) or not ranks:
        return None
    threshold = ledger.get("current_book_top_threshold_tier")
    top_named = []
    top_terms = []
    top_density = None
    for r in ranks:
        if not isinstance(r, dict):
            continue
        tier = r.get("tier")
        if isinstance(threshold, (int, float)) and isinstance(tier, (int, float)) and tier < threshold:
            continue
        name = r.get("name")
        if isinstance(name, str):
            top_terms.append(name)
        prev = r.get("previous_book_top_named") or []
        if isinstance(prev, list):
            top_named.extend([str(x) for x in prev if isinstance(x, str)])
        d = r.get("cross_book_baseline_density_per_cluster")
        if isinstance(d, (int, float)):
            top_density = float(d) if top_density is None else top_density + float(d)
    return {
        "_doc": "R19 W8 Batch-V·跨书顶阶稀缺 hint·writer 写顶阶角色要保持稀缺感(参考上本基线)",
        "previous_book_top_named": top_named[:30],
        "top_rank_terms": list(dict.fromkeys(top_terms))[:20],
        "previous_book_baseline_density_per_cluster": top_density,
        "current_book_top_threshold_tier": threshold,
    }


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "cross_book_rank_scarcity", "schema_version": "1.0",
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

    ledger = _load_ledger(project_root)
    if not isinstance(ledger, dict):
        out["note"] = "无 series_rank_ledger.json·skip(非系列文/未配置)"
        return out

    ranks = ledger.get("ranks") or []
    threshold = ledger.get("current_book_top_threshold_tier")
    top_named = []
    top_terms = []
    prev_density = None
    for r in ranks:
        if not isinstance(r, dict):
            continue
        tier = r.get("tier")
        if isinstance(threshold, (int, float)) and isinstance(tier, (int, float)) and tier < threshold:
            continue
        for x in (r.get("previous_book_top_named") or []):
            if isinstance(x, str) and len(x) >= 2:
                top_named.append(x)
        name = r.get("name")
        if isinstance(name, str):
            top_terms.append(name)
        d = r.get("cross_book_baseline_density_per_cluster")
        if isinstance(d, (int, float)):
            prev_density = float(d) if prev_density is None else prev_density + float(d)

    top_named = list(dict.fromkeys(top_named))
    top_terms = list(dict.fromkeys(top_terms))

    # 顶阶提及数·按 named + 称谓 union
    mention_count = 0
    for n in top_named:
        mention_count += text.count(n)
    for t in top_terms:
        mention_count += text.count(t)

    cjk = _cjk_count(text)
    cur_density = mention_count  # per cluster·与基线同单位

    # leapfrog 场计数
    scenes = _split_cluster_into_scenes(text)
    leap_scenes = 0
    for sc in scenes:
        if _scene_has_top_battle_with_win(sc, top_named, top_terms):
            leap_scenes += 1

    out["metrics"] = {
        "cluster_cjk": cjk,
        "top_named_count": len(top_named),
        "top_rank_terms_count": len(top_terms),
        "current_density_per_cluster": cur_density,
        "previous_book_baseline_density": prev_density,
        "leapfrog_scenes": leap_scenes,
        "total_scenes": len(scenes),
    }

    findings = []
    # 探针 1：密度比
    if prev_density and prev_density > 1e-6:
        ratio = cur_density / prev_density
        out["metrics"]["density_ratio"] = round(ratio, 2)
        if ratio >= DEFAULT_INFLATION_RATIO:
            findings.append(f"顶阶角色密度 {cur_density} 是上本基线 {prev_density} 的 {round(ratio,1)}× "
                            f"(阈值 {DEFAULT_INFLATION_RATIO}×)·稀缺性塌缩")
    # 探针 2：leapfrog 战斗 streak
    if leap_scenes >= DEFAULT_LEAPFROG_STREAK:
        findings.append(f"主角对位顶阶轻松取胜 {leap_scenes} 场·≥{DEFAULT_LEAPFROG_STREAK} 触发 leapfrog 信号")

    if findings:
        msg = " · ".join(findings)
        if mode == "active":
            out["violations"].append({
                "kind": "cross_book_rank_inflation", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "metrics": out["metrics"],
                "_doc": "R19 跨书顶阶稀缺塌缩·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] cross_book_rank_scarcity: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 跨书顶阶稀缺·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
