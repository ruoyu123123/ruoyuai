#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pace_callback_density.py — 钩子双侧 ±300 CJK 长期道具挂载密度 · R23 W11 Batch-GG · P0

【缺口 · neural PACE 框架】钩子（cliffhanger / 高情绪 beat）双侧 ±300 CJK 是
读者「长记区」(扣减阅读后保留率最强 window)·此区放设定/伏笔/物件 → 后续 cluster
召回率显著高于章中段 baseline。当前系统钩子两侧无指引·容易把长记设定散落于章中段
被节奏覆盖。

【做法 · 确定性 · 零 LLM/零联网】
  · build_manifest 注入 pace_carrier_window 字段（writer prompt 提示钩子两侧 300CJK 内放）
  · 本 scanner 测钩子窗口 locked_fact / foreshadowing_to_plant 关键词出现密度
    与后续 cluster 召回率（cross-cluster aggregator 未来 wire-up · 占位 callback_ratio 字段）
  · 与章中段 baseline 对比 · shadow N≥6 cluster 采纳

【三 advisory】
  · PACE_CARRIER_WINDOW_EMPTY        — 钩子两侧 ±300 CJK 无 locked_fact / foreshadowing 命中
  · PACE_CARRIER_WINDOW_OVER_LOADED  — 命中密度 > 章中段 baseline 5×（占位告警 · 防过载）
  · PACE_CARRIER_PURE_RHYTHM_HOOK    — 纯节奏型钩子（无 lexical anchor）豁免 · info 旁注

【北极星】②④⑤ 全 advisory · 作者档第一权威 · cluster · 占位 _placeholder=true
  PACE_* 绝不进 audit_hub.HARD_GATE_CODES。

env PACE_CARRIER_WINDOW_MODE: off / shadow（默认） / active
用法: python pace_callback_density.py <draft> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_EMPTY = "PACE_CARRIER_WINDOW_EMPTY"
ISSUE_CODE_OVER = "PACE_CARRIER_WINDOW_OVER_LOADED"
ISSUE_CODE_PURE_RHYTHM = "PACE_CARRIER_PURE_RHYTHM_HOOK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 钩子启发 regex（hook_strength 子集 · 简化）
_HOOK_PATTERNS = [
    r"[！？]{1,3}\s*$",          # 强情绪句末
    r"……\s*$",                  # 悬念省略号
    r"门.{0,3}开",
    r"出现",
    r"突然",
    r"忽然",
    r"猛地",
    r"霎时",
    r"竟然",
    r"原来",
]
_HOOK_RE = re.compile("|".join(_HOOK_PATTERNS))

# 长记 lexical anchor（占位 · 真版从作者档锚词 + locked_fact 实体词读）
_DEFAULT_ANCHOR_LEX = ["规则", "信物", "印记", "诅咒", "契约", "钥匙", "玉佩", "令牌",
                       "图谱", "密码", "暗号", "誓言", "封印", "禁令", "条款"]

WINDOW_RADIUS = 300
PURE_RHYTHM_OVER_RATIO = 5.0  # 章中段 baseline 5× → over loaded


def _mode() -> str:
    m = (os.environ.get("PACE_CARRIER_WINDOW_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_anchors(project_root, manifest_path) -> list[str]:
    anchors = list(_DEFAULT_ANCHOR_LEX)
    if manifest_path:
        try:
            mf = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            ec = mf.get("event_cluster_context") if isinstance(mf, dict) else None
            if isinstance(ec, dict):
                for k in ("foreshadowing_to_plant", "foreshadowing_to_callback"):
                    items = ec.get(k) or []
                    for item in items:
                        if isinstance(item, dict):
                            for fk in ("name", "label", "tag"):
                                v = item.get(fk)
                                if isinstance(v, str) and v.strip():
                                    anchors.append(v.strip())
                        elif isinstance(item, str):
                            anchors.append(item.strip())
        except (OSError, json.JSONDecodeError):
            pass
    if project_root:
        lf = Path(project_root) / "_数据库" / "锁定事实.json"
        if lf.exists():
            try:
                obj = json.loads(lf.read_text(encoding="utf-8"))
                facts = obj.get("facts") if isinstance(obj, dict) else None
                if isinstance(facts, list):
                    for f in facts:
                        if isinstance(f, dict):
                            for fk in ("entity", "label", "name"):
                                v = f.get(fk)
                                if isinstance(v, str) and v.strip():
                                    anchors.append(v.strip())
            except (OSError, json.JSONDecodeError):
                pass
    # 去重保序
    seen = set()
    out = []
    for a in anchors:
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _find_hooks(text: str) -> list[int]:
    """以 paragraph 段末为 hook candidate · 返回 hook 中心 char index 列表"""
    hooks: list[int] = []
    pos = 0
    for line in text.split("\n"):
        end_pos = pos + len(line)
        if line.strip() and _HOOK_RE.search(line):
            hooks.append(end_pos)
        pos = end_pos + 1
    return hooks


def _count_anchor_hits(text: str, anchors: list[str]) -> int:
    if not text or not anchors:
        return 0
    c = 0
    for a in anchors:
        if not a:
            continue
        c += text.count(a)
    return c


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "pace_callback_density", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "_placeholder": True}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 1000:
        out["note"] = "草稿太短·跳过"
        return out

    anchors = _load_anchors(project_root, manifest_path)
    hooks = _find_hooks(text)
    if not hooks:
        out["note"] = "未识别钩子位置·跳过"
        return out

    # 中段 baseline = 中间 40-60% 字符段密度（每 1000 CJK 命中数）
    mid_lo = int(len(text) * 0.40)
    mid_hi = int(len(text) * 0.60)
    mid_text = text[mid_lo:mid_hi]
    mid_cjk = _cjk_count(mid_text)
    mid_hits = _count_anchor_hits(mid_text, anchors)
    mid_density = (mid_hits / mid_cjk) if mid_cjk else 0.0

    window_reports = []
    pure_rhythm_count = 0
    over_loaded_count = 0
    empty_count = 0
    for h in hooks:
        lo = max(0, h - WINDOW_RADIUS)
        hi = min(len(text), h + WINDOW_RADIUS)
        win = text[lo:hi]
        win_cjk = _cjk_count(win)
        hits = _count_anchor_hits(win, anchors)
        density = (hits / win_cjk) if win_cjk else 0.0
        ratio = (density / mid_density) if mid_density > 0 else (float("inf") if hits else 0.0)
        record = {"hook_pos": h, "anchor_hits": hits, "window_cjk": win_cjk,
                  "density": round(density, 5), "vs_mid_ratio": round(ratio, 2) if ratio != float("inf") else "inf"}
        if hits == 0:
            empty_count += 1
            pure_rhythm_count += 1
            record["type"] = "pure_rhythm"
        elif ratio != float("inf") and ratio > PURE_RHYTHM_OVER_RATIO and hits >= 3:
            over_loaded_count += 1
            record["type"] = "over_loaded"
        else:
            record["type"] = "normal"
        window_reports.append(record)

    out.update({
        "cjk": cjk,
        "hook_count": len(hooks),
        "mid_baseline_density": round(mid_density, 5),
        "anchor_pool_size": len(anchors),
        "window_radius": WINDOW_RADIUS,
        "windows": window_reports[:10],
        "empty_count": empty_count,
        "over_loaded_count": over_loaded_count,
        "pure_rhythm_count": pure_rhythm_count,
        # 占位 · cross-cluster aggregator 未来回填 callback_ratio
        "callback_ratio": None,
    })

    flags = []
    if empty_count and empty_count == len(hooks):
        flags.append({"code": ISSUE_CODE_EMPTY,
                      "msg": f"全 {len(hooks)} 钩子两侧 ±{WINDOW_RADIUS} CJK 无 anchor 命中"})
    elif empty_count >= 2:
        flags.append({"code": ISSUE_CODE_EMPTY,
                      "msg": f"{empty_count} 个钩子两侧 ±{WINDOW_RADIUS} CJK 无 anchor 命中"})
    if over_loaded_count:
        flags.append({"code": ISSUE_CODE_OVER,
                      "msg": f"{over_loaded_count} 钩子窗口密度 > 章中段 baseline ×{PURE_RHYTHM_OVER_RATIO}"})
    if pure_rhythm_count and pure_rhythm_count < len(hooks):
        # 旁注：纯节奏钩子豁免（info）
        flags.append({"code": ISSUE_CODE_PURE_RHYTHM,
                      "msg": f"{pure_rhythm_count} 纯节奏钩子（豁免）"})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                sev = "info" if f["code"] == ISSUE_CODE_PURE_RHYTHM else "minor"
                out["violations"].append({
                    "kind": "pace_carrier", "severity": sev,
                    "code": f["code"], "message": f["msg"],
                    "_doc": "R23 W11 Batch-GG·P0·neural PACE·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] pace_callback_density: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="pace_callback_density advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
