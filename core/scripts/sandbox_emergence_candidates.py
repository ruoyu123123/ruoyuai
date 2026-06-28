#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sandbox_emergence_candidates.py — StoryBox 沙盒涌现包裹(R19 W8 Batch-X·P2)

【缺口·2026-06-21·StoryBox sandbox emergence】
当前 cluster_emergence_engine 是「ME 池 + 涟漪规则 + arc 阶段」打分式涌现·
偏 top-down。StoryBox 的补充范式是 bottom-up 沙盒仿真：K=3-5 角色 agent +
1 world agent 在 T=8-12 turn 内自由互动·harvest 涌现 stake / 势力翻转 /
伏笔意外触发 → 转 1-2 个 candidate brief。

【北极星纪律】
  - 不替代 cluster_emergence_engine·包裹增强(top-down + bottom-up 并行)
  - shadow 段标 sandbox_origin = "bottom_up"·真 agent 协议 defer(占位脚本)
  - 默认 mode=off·env SANDBOX_EMERGENCE_MODE 控制
  - cluster 单位·全 advisory·绝不 hard_gate

【数据流】
  emerge_with_sandbox(project_root, after_cluster_id)
    1. 跑 cluster_emergence_engine.emerge_next_cluster()(top-down)
    2. mode∈{shadow, active} 时跑 _sandbox_simulate()(占位)
    3. 合并：top-down candidates + sandbox candidates(标 sandbox_origin)
    4. active 模式才把 sandbox candidates append 进结果
    5. shadow 模式只 print stderr · 不污染产出

【占位 agent 协议】
  K=3-5 角色 agent: from 角色池.json 取 top-K · 每 turn 输出 1 个 action+stake
  1 world agent: 给 stake 加涟漪/外部事件
  T=8-12 turn: 累计 stake 翻转 / 势力洗牌 / 伏笔触发
  harvest: 抽 stake 强度 top-2 转 candidate brief
  真实现 defer 到独立 multi-agent 框架·当前用 deterministic 启发式占位。

【与既有 scanner 严格正交】
  - cluster_emergence_engine : top-down·包裹关系
  - capability_emergence     : 单本数值阶梯·正交(本=stake 涌现)
  - signed_relation_graph    : 关系正负·正交
  - 涟漪规则                  : 外部副作用·world agent 复用
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

ISSUE_CODE = "SANDBOX_EMERGENCE_ABNORMAL"
DEFAULT_K = 4
DEFAULT_T = 10
K_MIN, K_MAX = 3, 5
T_MIN, T_MAX = 8, 12


def _mode() -> str:
    m = (os.environ.get("SANDBOX_EMERGENCE_MODE") or "off").strip().lower()
    return m if m in ("off", "shadow", "active") else "off"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _load_characters(project_root):
    p = Path(project_root) / "_数据库" / "角色池.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        # 🔴 2026-06-28 角色池schema统一canonical：core/emerged（不兼容）
        chars = (d.get("core") or []) + (d.get("emerged") or [])
        return [e.get("name") for e in chars
                if isinstance(e, dict) and e.get("name")]
    except (OSError, json.JSONDecodeError):
        return []


def _load_world_state(project_root):
    p = Path(project_root) / "_数据库" / "世界状态.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _sandbox_simulate(characters, world_state, k=DEFAULT_K, t=DEFAULT_T, seed=None) -> list:
    """占位 bottom-up 仿真·返回 [candidate_brief, ...]。

    确定性启发式：取 top-K 角色名按字典序·按 turn 轮询输出 stake delta
    (基于 world_state factions 数值)·累积 turn 末两高 stake 转 brief。
    真 agent 协议 defer。
    """
    k = _clamp(k, K_MIN, K_MAX)
    t = _clamp(t, T_MIN, T_MAX)
    if not characters:
        return []
    chars = sorted(set(characters))[:k]
    factions = []
    if isinstance(world_state, dict):
        fs = world_state.get("factions") or []
        if isinstance(fs, list):
            factions = fs[:3]
    # 每角色 stake 累计
    stakes = {c: 0.0 for c in chars}
    events = []
    for turn in range(t):
        actor = chars[turn % len(chars)]
        delta = 0.5 + 0.1 * (turn % 5)
        # world agent: 偶数 turn 加涟漪
        if turn % 2 == 0 and factions:
            f = factions[turn % len(factions)]
            f_name = (f.get("name") if isinstance(f, dict) else str(f)) or "未知势力"
            events.append({
                "turn": turn + 1, "actor": actor,
                "type": "world_ripple",
                "stake_delta": round(delta, 2),
                "narration": f"{actor} 与 {f_name} 立场摩擦（占位）",
            })
        else:
            events.append({
                "turn": turn + 1, "actor": actor,
                "type": "char_action",
                "stake_delta": round(delta, 2),
                "narration": f"{actor} 推进自身利益（占位）",
            })
        stakes[actor] += delta
    # harvest top-2 stake 角色
    ranked = sorted(stakes.items(), key=lambda x: -x[1])[:2]
    candidates = []
    for ord_, (name, score) in enumerate(ranked, 1):
        candidates.append({
            "cluster_id_proposed": f"sandbox_candidate_{ord_}",
            "title": f"{name} 的下一步走向（沙盒涌现）",
            "scope_summary": f"由 K={k}/T={t} 沙盒涌现·{name} 累计 stake={round(score, 2)}",
            "stake_score": round(score, 2),
            "primary_actor": name,
            "events_log": events[:6],
            "sandbox_origin": "bottom_up",
            "_doc": "R19 W8 Batch-X·StoryBox sandbox·占位 agent·真协议 defer",
        })
    return candidates


def emerge_with_sandbox(project_root, after_cluster_id,
                        k=DEFAULT_K, t=DEFAULT_T) -> dict:
    """主入口·包裹 cluster_emergence_engine + 沙盒涌现。

    返回 dict:
      {"top_down": <emerge_next_cluster 结果 dict>,
       "sandbox": [candidate, ...],
       "mode": "off"|"shadow"|"active",
       "merged_candidates": [...]  (active 才并入)}
    """
    mode = _mode()
    out = {"mode": mode, "top_down": None, "sandbox": [],
           "merged_candidates": []}

    project_root = Path(project_root) if project_root else None
    if project_root is None or not project_root.exists():
        out["error"] = "project_root 不存在"
        return out

    # 1. 跑 top-down
    try:
        import cluster_emergence_engine as cee
        out["top_down"] = cee.emerge_next_cluster(project_root, after_cluster_id)
    except Exception as e:
        out["error"] = f"top-down emergence 失败: {str(e)[:200]}"
        out["top_down"] = {"ok": False, "error": str(e)[:200]}

    if mode == "off":
        return out

    # 2. 跑 sandbox(shadow/active)
    chars = _load_characters(project_root)
    ws = _load_world_state(project_root)
    sandbox_candidates = _sandbox_simulate(chars, ws, k=k, t=t)
    out["sandbox"] = sandbox_candidates

    if mode == "shadow":
        if sandbox_candidates:
            print(f"[SHADOW] sandbox_emergence: K={k}/T={t} → "
                  f"{len(sandbox_candidates)} 沙盒候选 — 不并入", file=sys.stderr)
        return out

    # 3. active 才合并
    top_candidates = []
    if isinstance(out["top_down"], dict):
        top_candidates = out["top_down"].get("candidates") or []
        if not isinstance(top_candidates, list):
            top_candidates = []
    merged = list(top_candidates) + list(sandbox_candidates)
    out["merged_candidates"] = merged
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-X StoryBox 沙盒涌现包裹·advisory")
    ap.add_argument("--project", required=True)
    ap.add_argument("--after", required=True, help="after_cluster_id 比如 cluster_003")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--t", type=int, default=DEFAULT_T)
    args, _ = ap.parse_known_args()
    rep = emerge_with_sandbox(args.project, args.after, k=args.k, t=args.t)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
