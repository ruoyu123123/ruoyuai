#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""integration_ridge_density_scanner.py — DMN 整合 ridge 密度 advisory (cross-cluster)
R21 W10 Batch-DD · R21-NB-03

【缺口·R21 P2】Kaneshiro 2024 EJN ISC+meta 14 研究 27 effect r=0.65：DMN 整合在
culmination 处出现 ridge·LLM 通病：缺密集回指 + 伏笔回收同窗·或 ridge 出现过早。

【做法 · 确定性 · 零 LLM/零联网】
  · 200 CJK 滑窗扫·四 integration_signals 命中：
    (1) 多次回指既有人物名（≥2 不同 cluster 已建立人物）
    (2) 既有 locked_fact 关键名词回引
    (3) 多伏笔回收点同窗（foreshadowing_handoff 输出）
    (4) 多线索因果合流标志：终于明白/原来如此/这一切/串到一起/对得上/线索拼合/前后呼应
  · 任窗 ≥3 项命中 = integration_ridge
  · ridge_count + ridge_density (per 10kCJK)
  · 无 ridge → INTEGRATION_RIDGE_ABSENT
  · 单 ridge 出现位置 < 60% → INTEGRATION_RIDGE_TOO_EARLY (DMN 实证在 culmination)

【与既有 scanner 严格正交】
  · cross_cluster_engagement_metrics / retention_proxy / paragraph_engagement_heat
  本者 = DMN 整合 ridge 位置 + 密度。

【北极星】②④⑤ 作者档第一权威 · cross-cluster · advisory shadow · 绝不 hard_gate
  INTEGRATION_RIDGE_* 绝不进 HARD_GATE_CODES。

env INTEGRATION_RIDGE_MODE: off / shadow(默认) / active
用法: python integration_ridge_density_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE_ABSENT = "INTEGRATION_RIDGE_ABSENT"
ISSUE_CODE_TOO_EARLY = "INTEGRATION_RIDGE_TOO_EARLY"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

CONFLUENCE_MARKERS = [
    "终于明白", "原来如此", "这一切", "串到一起", "对得上", "线索拼合",
    "前后呼应", "豁然开朗", "拼图凑齐", "全都连上",
]

DEFAULT_WINDOW_CJK = 200
DEFAULT_STEP_CJK = 100
DEFAULT_SIGNAL_THRESHOLD = 3
DEFAULT_CULMINATION_RATIO = 0.60
DEFAULT_RIDGE_PER_10K_LOW = 1.0
DEFAULT_RIDGE_PER_10K_HIGH = 10.0


def _mode() -> str:
    m = (os.environ.get("INTEGRATION_RIDGE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _collect_established_names(project_root) -> list[str]:
    """从角色池.json 收集已建立人物名"""
    if not project_root:
        return []
    pool = Path(project_root) / "_数据库" / "角色池.json"
    obj = _read_json(pool)
    if not isinstance(obj, dict):
        return []
    names: list[str] = []
    for k in ("emerged", "main", "characters"):
        v = obj.get(k)
        if isinstance(v, list):
            for c in v:
                if isinstance(c, str):
                    names.append(c)
                elif isinstance(c, dict):
                    n = c.get("name") or c.get("id")
                    if isinstance(n, str):
                        names.append(n)
    # 去重 + 长度过滤
    seen = set()
    out = []
    for n in names:
        if n and len(n) <= 8 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _collect_locked_facts(project_root) -> list[str]:
    """从锁定事实账本.json 提取关键名词"""
    if not project_root:
        return []
    db = Path(project_root) / "_数据库"
    nouns: list[str] = []
    for fname in ("锁定事实账本.json", "锁定事实.json"):
        obj = _read_json(db / fname)
        if not isinstance(obj, dict):
            continue
        for k, v in obj.items():
            if isinstance(v, dict):
                for kk in ("key_noun", "name", "entity"):
                    if isinstance(v.get(kk), str):
                        nouns.append(v[kk])
            elif isinstance(v, str):
                if 2 <= len(v) <= 8:
                    nouns.append(v)
        break
    seen = set()
    out = []
    for n in nouns:
        if n and 2 <= len(n) <= 8 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _collect_foreshadow_payoffs(project_root) -> list[str]:
    """从 foreshadowing_handoff 输出或 伏笔账本 提取本 cluster 回收的伏笔关键词"""
    if not project_root:
        return []
    db = Path(project_root) / "_数据库"
    keys: list[str] = []
    for fname in ("伏笔账本.json", "伏笔.json"):
        obj = _read_json(db / fname)
        if not isinstance(obj, dict):
            continue
        items = obj.get("items") or obj.get("foreshadowings") or []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("status") in ("paid", "回收", "resolved"):
                    kw = it.get("key_noun") or it.get("name") or it.get("title")
                    if isinstance(kw, str) and 2 <= len(kw) <= 12:
                        keys.append(kw)
        break
    seen = set()
    out = []
    for n in keys:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _count_window_signals(window: str, names: list[str], facts: list[str],
                          payoffs: list[str]) -> dict:
    """单窗 4 信号命中数 + 各项 hit"""
    # 1) 回指 ≥2 不同人物名
    name_hits = sum(1 for n in names if n and n in window)
    sig1 = name_hits >= 2
    # 2) 锁定事实关键名词回引
    fact_hits = sum(1 for n in facts if n and n in window)
    sig2 = fact_hits >= 1
    # 3) 多伏笔回收同窗 (≥2)
    payoff_hits = sum(1 for n in payoffs if n and n in window)
    sig3 = payoff_hits >= 2
    # 4) 合流标志
    conf_hits = sum(1 for m in CONFLUENCE_MARKERS if m in window)
    sig4 = conf_hits >= 1
    triggered = int(sig1) + int(sig2) + int(sig3) + int(sig4)
    return {
        "name_hits": name_hits,
        "fact_hits": fact_hits,
        "payoff_hits": payoff_hits,
        "confluence_hits": conf_hits,
        "signals_triggered": triggered,
        "sig1_names": sig1,
        "sig2_facts": sig2,
        "sig3_payoffs": sig3,
        "sig4_confluence": sig4,
    }


def _scan_windows(text: str, names: list[str], facts: list[str], payoffs: list[str],
                  window_cjk: int, step_cjk: int, threshold: int) -> list[dict]:
    """滑窗扫·返回 ridge 列表"""
    ridges = []
    n = len(text)
    if n <= 0:
        return ridges
    pos = 0
    while pos < n:
        end = min(pos + window_cjk, n)
        window = text[pos:end]
        sig = _count_window_signals(window, names, facts, payoffs)
        if sig["signals_triggered"] >= threshold:
            ridges.append({
                "start": pos,
                "end": end,
                "position_ratio": round(pos / max(1, n), 4),
                **sig,
            })
        pos += step_cjk
    # 合并相邻 ridge（同方向连续）
    merged = []
    for r in ridges:
        if merged and r["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], r["end"])
            merged[-1]["signals_triggered"] = max(merged[-1]["signals_triggered"],
                                                  r["signals_triggered"])
        else:
            merged.append(dict(r))
    return merged


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            sig = obj.get("integration_ridge_baseline")
            if isinstance(sig, dict):
                return sig
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "integration_ridge_density", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory", "layer": "cross-cluster",
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
    if cjk < 800:
        out["note"] = "草稿太短·跳过"
        return out

    baseline = _read_author_baseline(project_root)
    window_cjk = DEFAULT_WINDOW_CJK
    step_cjk = DEFAULT_STEP_CJK
    threshold = DEFAULT_SIGNAL_THRESHOLD
    culmination_ratio = DEFAULT_CULMINATION_RATIO
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        if isinstance(baseline.get("window_cjk"), int):
            window_cjk = baseline["window_cjk"]
        if isinstance(baseline.get("signal_threshold"), int):
            threshold = baseline["signal_threshold"]
        if isinstance(baseline.get("culmination_ratio"), (int, float)):
            culmination_ratio = float(baseline["culmination_ratio"])

    names = _collect_established_names(project_root)
    facts = _collect_locked_facts(project_root)
    payoffs = _collect_foreshadow_payoffs(project_root)
    ridges = _scan_windows(text, names, facts, payoffs,
                           window_cjk, step_cjk, threshold)
    ridge_count = len(ridges)
    ridge_density_per_10k = round(ridge_count / max(1, cjk / 10000.0), 3)

    out.update({
        "cjk": cjk,
        "established_names_count": len(names),
        "locked_facts_count": len(facts),
        "foreshadow_payoffs_count": len(payoffs),
        "ridge_count": ridge_count,
        "ridge_density_per_10k": ridge_density_per_10k,
        "ridges": ridges,
        "baseline_source": baseline_source,
        "thresholds": {
            "window_cjk": window_cjk,
            "step_cjk": step_cjk,
            "signal_threshold": threshold,
            "culmination_ratio": culmination_ratio,
        },
    })

    flags = []
    if ridge_count == 0:
        flags.append({"code": ISSUE_CODE_ABSENT,
                      "msg": "ridge_count=0·DMN integration ridge 缺失·缺密集回指/伏笔回收/合流标志"})
    elif ridge_count == 1:
        only = ridges[0]
        if only["position_ratio"] < culmination_ratio:
            flags.append({"code": ISSUE_CODE_TOO_EARLY,
                          "msg": (f"唯一 ridge 位置={only['position_ratio']:.2f} < "
                                  f"{culmination_ratio:.2f}·DMN integration 应靠 culmination")})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "integration_ridge_density", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": ("Kaneshiro 2024 EJN ISC+meta r=0.65·R21-NB-03·"
                             "advisory·绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] integration_ridge_density: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="DMN integration ridge density advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
