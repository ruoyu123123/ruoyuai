#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_acr_frustration_consistency.py — BPNSFS A/C/R 受挫一致性·shadow

【缺口 · R22 W10 Batch-EE·P1 · 2026-06-21】BPNSFS（Basic Psychological Need
Satisfaction and Frustration Scale, Chen et al. 2015 + 2024 实证）：
  三需求受挫 → 典型反应映射（认知行为治疗 + 自我决定理论交叉证据）：
    autonomy 受挫    → 反抗 / 僵化 (抗拒命令·钻牛角尖·绝对化)
    competence 受挫  → 虚张 / 退缩 (吹嘘掩饰·回避困难·自贬)
    relatedness 受挫 → 攻击 / 讨好 (敌意外攻·过度依附·迎合)

  主要角色受挫需求标定后反应行为不匹配 → 动机错位（人物失真感）。
  作者反类型可豁免（_acr_frustration_override=true）。

【与既有 scanner 显式去重】
  - sdt_motivation_regulation_advisory：6 级动机调节
    本 scanner = 需求受挫 → 反应类目一致性·正交（一个查动机类型·一个查受挫反应）
  - character_state_drift_scanner：情绪/姿态漂移
    本 scanner = 受挫-反应映射·正交

【做法 · 确定性占位（零 LLM）】
  1. 读 _数据库/人物卡.json → 主要角色
  2. 扫每个角色 ±50 CJK 上下文窗：
     A/C/R 受挫词命中 + 反应词命中
  3. 命中受挫但反应类目不在映射表 → 不一致 advisory

【北极星⑤】顾问非法官·全 advisory·env ACR_FRUSTRATION_MODE
  ACR_FRUSTRATION_MISMATCH 绝不进 audit_hub.HARD_GATE_CODES。

用法: python check_acr_frustration_consistency.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ACR_FRUSTRATION_MISMATCH"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 受挫词典占位
ACR_FRUSTRATION_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "autonomy": ["被迫", "强迫", "命令", "不得不", "无法选择", "被规定", "压制"],
    "competence": ["失败", "做不到", "搞砸", "没用", "废物", "丢人", "笨"],
    "relatedness": ["孤独", "被抛弃", "无人懂", "冷漠", "排挤", "疏远", "断绝"],
}

# 反应词典占位
ACR_REACTION_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "autonomy_expected": {
        "反抗": ["顶撞", "抗拒", "拒绝", "对着干", "造反", "硬碰"],
        "僵化": ["死守", "钻牛角尖", "绝对", "一定要", "非要"],
    },
    "competence_expected": {
        "虚张": ["吹嘘", "夸口", "炫耀", "装", "假装"],
        "退缩": ["逃避", "躲", "缩", "不敢", "退后"],
    },
    "relatedness_expected": {
        "攻击": ["怒斥", "咆哮", "敌意", "仇视", "怨"],
        "讨好": ["迎合", "讨好", "巴结", "卑微", "求"],
    },
}


def _mode() -> str:
    m = (os.environ.get("ACR_FRUSTRATION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_characters(project_root) -> list:
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    names = []
    seen = set()
    for c in (obj.get("characters", []) if isinstance(obj, dict) else []):
        if not isinstance(c, dict):
            continue
        nm = c.get("name", "")
        if nm and nm not in seen:
            names.append(nm)
            seen.add(nm)
    return names


def _override_flag(project_root) -> bool:
    if not project_root:
        return False
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return False
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(obj.get("_acr_frustration_override"))


def _scan_character_windows(text: str, char: str, window: int = 50) -> dict:
    """返回该角色每次出场窗口内受挫/反应命中"""
    frus = ACR_FRUSTRATION_LEXICON_PLACEHOLDER
    rxs = ACR_REACTION_LEXICON_PLACEHOLDER
    hits = {"autonomy_frus": 0, "competence_frus": 0, "relatedness_frus": 0,
            "autonomy_expected_rx": 0, "competence_expected_rx": 0, "relatedness_expected_rx": 0,
            "wrong_rx_windows": []}
    if char not in text:
        return hits
    for m in re.finditer(re.escape(char), text):
        lo = max(0, m.start() - window)
        hi = min(len(text), m.end() + window)
        ctx = text[lo:hi]
        a_f = any(w in ctx for w in frus["autonomy"])
        c_f = any(w in ctx for w in frus["competence"])
        r_f = any(w in ctx for w in frus["relatedness"])
        a_rx = any(w in ctx for cat in rxs["autonomy_expected"].values() for w in cat)
        c_rx = any(w in ctx for cat in rxs["competence_expected"].values() for w in cat)
        r_rx = any(w in ctx for cat in rxs["relatedness_expected"].values() for w in cat)
        if a_f:
            hits["autonomy_frus"] += 1
            if a_rx:
                hits["autonomy_expected_rx"] += 1
            elif c_rx or r_rx:
                hits["wrong_rx_windows"].append(
                    {"need": "autonomy", "got": ("competence" if c_rx else "relatedness"),
                     "ctx": ctx.replace("\n", " ")[:60]})
        if c_f:
            hits["competence_frus"] += 1
            if c_rx:
                hits["competence_expected_rx"] += 1
            elif a_rx or r_rx:
                hits["wrong_rx_windows"].append(
                    {"need": "competence", "got": ("autonomy" if a_rx else "relatedness"),
                     "ctx": ctx.replace("\n", " ")[:60]})
        if r_f:
            hits["relatedness_frus"] += 1
            if r_rx:
                hits["relatedness_expected_rx"] += 1
            elif a_rx or c_rx:
                hits["wrong_rx_windows"].append(
                    {"need": "relatedness", "got": ("autonomy" if a_rx else "competence"),
                     "ctx": ctx.replace("\n", " ")[:60]})
    return hits


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "check_acr_frustration_consistency", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "mismatch_count": 0, "mismatch_samples": []}
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

    if _override_flag(project_root):
        out["note"] = "作者档 _acr_frustration_override=true · 反类型豁免"
        return out

    chars = _load_characters(project_root)
    if not chars:
        out["note"] = "无人物卡或角色名空·跳过（北极星②）"
        out["character_count"] = 0
        return out
    out["character_count"] = len(chars)

    all_mismatch = []
    by_char = {}
    for ch in chars:
        hits = _scan_character_windows(text, ch)
        by_char[ch] = {
            "autonomy_frus": hits["autonomy_frus"],
            "competence_frus": hits["competence_frus"],
            "relatedness_frus": hits["relatedness_frus"],
        }
        for w in hits["wrong_rx_windows"]:
            w["character"] = ch
            all_mismatch.append(w)

    out["by_character_frustration"] = by_char
    out["mismatch_count"] = len(all_mismatch)
    out["mismatch_samples"] = all_mismatch[:5]

    if all_mismatch:
        msg = (f"BPNSFS 受挫-反应错配 {len(all_mismatch)} 处："
               + "·".join(f"{m['character']}/{m['need']}受挫→{m['got']}反应"
                          for m in all_mismatch[:3]))
        if mode == "active":
            out["violations"].append({
                "kind": "acr_frustration_mismatch", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "mismatch_count": len(all_mismatch),
                "_doc": "BPNSFS A/C/R 三需求受挫反应映射·反类型作者可豁免·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] check_acr_frustration_consistency: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="BPNSFS A/C/R 受挫一致性·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
