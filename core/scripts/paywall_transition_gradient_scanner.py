#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paywall_transition_gradient_scanner.py — 入V过墙 stake 递增 + mega-reveal 末段

【缺口 · R18 W7 Batch-S·P0 · 2026-06-21】tomenovel.com web-novel-cliffhanger-economy
+ 知乎 681376328 起点新书期 + 橙瓜 chenggua + Qidian-Webnovel Corpus 2.79M 评论：
  网文「入V」（免费章→付费章）是一个**独立的结构位元**，业界公认要做「双峰钩」：
  ① stake 递增曲线（最后 N 场景每一场利害都比上一场重）
  ② mega-reveal 末段（卷尾爆点把核心悬念翻面/抬高一个数量级）
  R7-R13 共 101 条无任何 scanner 把『入V/paywall 转折』作独立结构位。

【做法 · 确定性占位】
  1. 读 _数据库/用户偏好.json workflow_preferences[paywall_transition_cluster_id]
     —— 用户/编辑手填·绝不自动推断（用户偏好.json 是第一权威）
  2. 如果当前 cluster_id 不在该名单 → skip（is_paywall_transition_cluster=False）
  3. 否则切场景，对每场算 stake_score：
       stake_keywords = {生死/性命/濒死/重伤/濒临/绝境/最后/血/死/危/陷阱/伏杀/绝路/末日}
       stake_score[i] = stake_keywords 命中数 / scene_cjk * 1000
  4. 末段（最后场景）mega_reveal_score：
       mega_reveal_keywords = {真相/原来/竟然/震惊/揭穿/反转/翻面/惊雷/惊变}
  5. 判定 BRIDGE_PAYWALL_HOOK_GRADIENT_OFF：
       (a) 倒数 3 场 stake_score 非单调递增（最后场 < 中间场 < 第一场） OR
       (b) 末场 mega_reveal_score==0
  6. 全 advisory · gen_writer 不强制（北极星⑤）

【与 R7-R13 现有 scanner 正交去重】
  - hook_strength_scanner: 单章末钩子强度（不是 cluster 末位元结构位）
  - paragraph_engagement_heat_predictor (R18 P2): 段落热度（粒度不同）
  - cross_cluster_engagement_metrics: cluster 间钩子趋势（不锁 paywall）

env PAYWALL_TRANSITION_GRADIENT_MODE: off / shadow(默认) / active
用法: python paywall_transition_gradient_scanner.py <draft> --project <root> --cluster-id <id>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "BRIDGE_PAYWALL_HOOK_GRADIENT_OFF"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

STAKE_KEYWORDS = (
    "生死", "性命", "濒死", "重伤", "濒临", "绝境", "最后", "死", "危", "陷阱",
    "伏杀", "绝路", "末日", "崩塌", "破灭", "暴毙", "灭顶", "凶险",
)
MEGA_REVEAL_KEYWORDS = (
    "真相", "原来", "竟然", "震惊", "揭穿", "反转", "翻面", "惊雷", "惊变",
    "竟是", "原是", "真身", "终于明白",
)

SCENE_SPLIT = re.compile(r"\n\s*[*◇◆━─=]{3,}\s*\n|\n\s*场景[:：]\s*[^\n]*\n|"
                         r"\n\s*第[一二三四五六七八九十0-9]+幕[^\n]*\n")


def _mode() -> str:
    m = (os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _is_paywall_cluster(project_root, cluster_id):
    """读 用户偏好.json workflow_preferences 是否声明本 cluster 为 paywall transition"""
    if not project_root or not cluster_id:
        return False
    p = Path(project_root) / "_数据库" / "用户偏好.json"
    if not p.exists():
        return False
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(obj, dict):
        return False
    prefs = obj.get("workflow_preferences", [])
    if not isinstance(prefs, list):
        return False
    for pref in prefs:
        if not isinstance(pref, dict):
            continue
        if pref.get("key") == "paywall_transition_cluster_id":
            v = pref.get("value")
            if isinstance(v, str) and v == cluster_id:
                return True
            if isinstance(v, list) and cluster_id in v:
                return True
    return False


def _split_scenes(text: str):
    parts = SCENE_SPLIT.split(text)
    out = [p.strip() for p in parts if p and p.strip()]
    return out if out else [text]


def _score(scene, keywords):
    cjk = _cjk_count(scene)
    if cjk < 1:
        return 0.0, 0
    hits = sum(scene.count(k) for k in keywords)
    return round(hits / cjk * 1000.0, 3), hits


def scan(draft_path, project_root=None, cluster_id: str = "") -> dict:
    mode = _mode()
    out = {"scanner": "paywall_transition_gradient", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "is_paywall_transition_cluster": False}
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

    is_paywall = _is_paywall_cluster(project_root, cluster_id)
    out["is_paywall_transition_cluster"] = is_paywall
    if not is_paywall:
        out["note"] = "非 paywall transition cluster（用户偏好未声明）·跳过"
        return out

    scenes = _split_scenes(text)
    out["scene_count"] = len(scenes)
    stakes = [_score(s, STAKE_KEYWORDS)[0] for s in scenes]
    out["stake_score_per_scene"] = stakes
    mega_reveal_score, mega_hits = _score(scenes[-1], MEGA_REVEAL_KEYWORDS)
    out["mega_reveal_score_last_scene"] = mega_reveal_score
    out["mega_reveal_hits_last_scene"] = mega_hits

    flags = []
    # (a) 末段 stake 非递增（取倒数 3 场看趋势·不足 3 场跳）
    if len(stakes) >= 3:
        s_a, s_b, s_c = stakes[-3], stakes[-2], stakes[-1]
        if not (s_c >= s_b and s_b >= s_a):
            flags.append(f"stake_gradient_off(last3={s_a}|{s_b}|{s_c}·非单调递增)")
    elif len(stakes) >= 2:
        if stakes[-1] < stakes[-2]:
            flags.append(f"stake_gradient_off(last2={stakes[-2]}|{stakes[-1]})")
    # (b) 末段 mega-reveal 缺
    if mega_hits == 0:
        flags.append("mega_reveal_missing(末场零 reveal 关键词)")

    if flags:
        msg = "·".join(flags)
        if mode == "active":
            out["violations"].append({
                "kind": "paywall_transition_gradient_off", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "stake_score_per_scene": stakes,
                "mega_reveal_score_last_scene": mega_reveal_score,
                "_doc": "入V双峰钩·gen_writer 不强制·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] paywall_transition_gradient: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="paywall 过墙双峰钩(stake+mega-reveal)·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-id", default="")
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, cluster_id=args.cluster_id)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
