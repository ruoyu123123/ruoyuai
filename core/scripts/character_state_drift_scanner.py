#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""character_state_drift_scanner.py — 时态可分 entity profile 漂移(R20 W9 Batch-Z·P0)

【缺口·2026-06-21·R20 STRONG Q3-Q4 论文 id 19】
R12 的 contradiction scanner 把「稳定身份变化（=真矛盾）」和「动态状态变化
（=正常剧情·从家走到酒馆/心情从喜到悲/HP 满到濒死）」混在一起·导致大量假阳。
NKW(Narrative Knowledge World) 框架主张时态分离：
  - stable_identity   SLOW_UPDATE 慢变身份维度（姓名/性别/籍贯/瞳色/血型 …）
  - dynamic_state    FAST_UPDATE 快变状态维度（位置/心情/伤势/同伴/装备 …）
变化属性 ∈ dynamic_state 白名单 = 合法剧情进展·不报；
变化属性 ∈ stable_identity = 角色档真矛盾·报 CHARACTER_STATE_DRIFT_DETECTED。

【数据锚 · 🔴 2026-06-29 清假producer口径(防误导)】
  _数据库/character_state_ledger.json（per-project ledger 当前【无专用 producer】·
    全仓零 producer·distill-character / cluster-save-state 均【无填充此文件的步骤】
    (曾误称「scaffold 由 distill-character / cluster-save-state step 12 填充」=假口径·
    该 step 不存在·已清)·故实际恒走 DEFAULT_LEDGER 通用兜底=白名单角色状态词典·
    这是【合法通用基线·非降级】。
    ⚠ 后果：stable_identity drift 检测需 per-project ground-truth ledger 才能点火·
    DEFAULT 兜底下 char_ledger 为空 → stable_drift 恒 0（即 CHARACTER_STATE_DRIFT_DETECTED
    在无 per-project ledger 时永不触发）；但属性 stable/dynamic 分类、dynamic_state_updates、
    filter_dynamic_state_changes 二筛仅靠通用白名单仍有效。
    若需 per-project 精度·可后续由 archivist 产 character_state baseline 落此文件·TODO·暂缺）
  {
    "schema_version": 1,
    "_doc": "时态可分 entity profile·stable_identity SLOW_UPDATE / dynamic_state FAST_UPDATE",
    "characters": {
      "张三": {
        "stable_identity": {"性别": "男", "瞳色": "黑色", "籍贯": "江南"},
        "dynamic_state":   {"位置": "客栈", "心情": "凝重", "伤势": "完好", "同伴": "李四"}
      }
    }
  }

【做法·确定性占位（零 LLM）】
  1. 加载 ledger（缺则用 DEFAULT_LEDGER 通用白名单）
  2. 扫描草稿：对每个角色名，提取附近窗口（±20 CJK）内 "属性[：是为]" 值
     语法：「<char>(的)?(瞳色|心情|位置|伤势|身高|年龄|籍贯|...) [是为：]? <值>」
  3. 判定：
     - 属性 ∈ stable_identity 且 ledger value ≠ 检出 value
       → stable_identity_drift（CHARACTER_STATE_DRIFT_DETECTED）
     - 属性 ∈ dynamic_state（任何值变化都合法）→ 二筛白名单·不报
     - 属性既不在 stable 又不在 dynamic（=未登记）·不报（保守）
  4. 输出 dynamic_state_updates[]（供 cluster-save-state 反哺 ledger FAST_UPDATE）

【scene_storyboard paragraph_function tag】（R20 蓝图）
  scaffold paragraph_function ∈ {establish/develop/turn/payoff/transition/voiceover}
  作 storyboard beat 字段·writer manifest 注入·与本 scanner 解耦（仅辅写）。

【与 R12 contradiction 二筛接口】
  R12 contradiction scanner 输出 contradiction_candidates → 本 scanner 调
  `filter_dynamic_state_changes(candidates, ledger)` 把属性∈dynamic 的剔除·剩
  下报 CHARACTER_STATE_DRIFT_DETECTED（避开 R12 假阳风暴）。

【北极星⑤】顾问非法官·全 advisory·env CHARACTER_STATE_DRIFT_MODE 默认 shadow·
  CHARACTER_STATE_DRIFT_DETECTED 绝不 hard_gate。

用法: python character_state_drift_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CHARACTER_STATE_DRIFT_DETECTED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 通用兜底（_placeholder=true·合法通用基线·非降级；per-project 真清单当前无 producer·
# 🔴 2026-06-29 清假producer口径(防误导)·详见模块 docstring 数据锚·archivist baseline 为 TODO）
DEFAULT_LEDGER = {
    "_placeholder": True,
    "stable_identity_attrs": [
        "性别", "瞳色", "眼睛颜色", "发色", "肤色", "血型", "籍贯", "出生地",
        "生肖", "星座", "年龄", "身高", "本名", "真名", "民族", "宗派",
    ],
    "dynamic_state_attrs": [
        "位置", "地点", "处境", "心情", "情绪", "状态",
        "伤势", "气血", "灵力", "法力", "魔力", "体力",
        "同伴", "装备", "穿着", "持有", "财产", "存款",
        "气色", "脸色", "神情",
    ],
}

# 属性提取正则：<char>(的)? <attr> (是|为|：|:)? <value>
_VAL_STOP = "。！？，、；,!?;\n"


def _mode() -> str:
    m = (os.environ.get("CHARACTER_STATE_DRIFT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text):
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text):
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_characters(project_root):
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for c in (obj.get("characters", []) if isinstance(obj, dict) else []):
        if not isinstance(c, dict):
            continue
        nm = c.get("name", "")
        if nm:
            out.append({
                "name": nm,
                "aliases": list(c.get("aliases") or []),
            })
    return out


def _load_ledger(project_root):
    """读 character_state_ledger.json·缺则用 DEFAULT_LEDGER 占位通用白名单。"""
    placeholder = False
    raw = None
    if project_root:
        p = Path(project_root) / "_数据库" / "character_state_ledger.json"
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = None
    if not isinstance(raw, dict) or "characters" not in raw:
        placeholder = True
        return DEFAULT_LEDGER, {}, placeholder
    chars = raw.get("characters") or {}
    if not isinstance(chars, dict):
        chars = {}
    # 全局通用 attr 白名单（取每角色 keys 的并集）
    stable_attrs = set(DEFAULT_LEDGER["stable_identity_attrs"])
    dynamic_attrs = set(DEFAULT_LEDGER["dynamic_state_attrs"])
    for cname, prof in chars.items():
        if not isinstance(prof, dict):
            continue
        si = prof.get("stable_identity") or {}
        ds = prof.get("dynamic_state") or {}
        if isinstance(si, dict):
            stable_attrs.update(si.keys())
        if isinstance(ds, dict):
            dynamic_attrs.update(ds.keys())
    return ({
        "_placeholder": False,
        "stable_identity_attrs": sorted(stable_attrs),
        "dynamic_state_attrs": sorted(dynamic_attrs),
    }, chars, placeholder)


def _classify_attr(attr, ledger):
    if attr in ledger["stable_identity_attrs"]:
        return "stable"
    if attr in ledger["dynamic_state_attrs"]:
        return "dynamic"
    return "unknown"


def _extract_attr_value_claims(text, char_names, ledger):
    """从 text 抽 <char>(的)?<attr>(是|为|：|:)?<value> 断言。

    返回 list[{char, attr, value, classification, span}]。
    """
    claims = []
    all_attrs = (set(ledger["stable_identity_attrs"]) |
                 set(ledger["dynamic_state_attrs"]))
    if not all_attrs or not char_names:
        return claims
    # 按属性长度逆序避免 substring 误匹配（例如 "瞳色" vs "色"）
    attrs_sorted = sorted(all_attrs, key=len, reverse=True)
    attr_pat = "|".join(re.escape(a) for a in attrs_sorted)
    name_pat = "|".join(re.escape(n) for n in char_names)
    # <name>(的)?<attr>(是|为|：|:)? <value 直到结束符>
    full_pat = re.compile(
        rf"(?P<name>{name_pat})的?(?P<attr>{attr_pat})"
        rf"\s*(?:是|为|：|:)\s*(?P<val>[^{re.escape(_VAL_STOP)}]{{1,12}})"
    )
    for m in full_pat.finditer(text):
        name = m.group("name")
        attr = m.group("attr")
        val = m.group("val").strip()
        if not val:
            continue
        cls = _classify_attr(attr, ledger)
        claims.append({
            "char": name, "attr": attr, "value": val,
            "classification": cls, "span": [m.start(), m.end()],
        })
    return claims


def _detect_stable_drifts(claims, char_ledger):
    """对 classification=stable 的断言 vs ledger value 比对。

    char_ledger: {name: {stable_identity: {...}, dynamic_state: {...}}}。
    缺角色或缺该 attr → 不报（保守·属于「首次声明」）。
    """
    drifts = []
    for c in claims:
        if c["classification"] != "stable":
            continue
        prof = char_ledger.get(c["char"]) if isinstance(char_ledger, dict) else None
        if not isinstance(prof, dict):
            continue
        si = prof.get("stable_identity") or {}
        if not isinstance(si, dict):
            continue
        ground = si.get(c["attr"])
        if ground is None:
            continue
        if str(ground).strip() != c["value"]:
            drifts.append({
                "char": c["char"], "attr": c["attr"],
                "ledger_value": str(ground), "drafted_value": c["value"],
                "msg": (f"{c['char']} 的 {c['attr']} 从 {ground} → {c['value']}"
                        f"·属 stable_identity·疑似真矛盾"),
            })
    return drifts


def _collect_dynamic_updates(claims):
    """供 cluster-save-state 反哺 ledger.dynamic_state FAST_UPDATE。"""
    return [
        {"char": c["char"], "attr": c["attr"], "new_value": c["value"]}
        for c in claims if c["classification"] == "dynamic"
    ]


# ============ 对 R12 contradiction 的二筛接口 ============

def filter_dynamic_state_changes(candidates, project_root):
    """供 R12 contradiction scanner 二筛使用。

    candidates: list[{char, attr, ...}]
    返回未被白名单吸收的 list（仅保留 stable / unknown）。
    """
    ledger, _char_ledger, _ph = _load_ledger(project_root)
    kept = []
    for cand in candidates or []:
        attr = cand.get("attr") if isinstance(cand, dict) else None
        if attr and _classify_attr(attr, ledger) == "dynamic":
            continue  # 动态属性·正常剧情·剔除
        kept.append(cand)
    return kept


# ============ 主流程 ============

def scan(draft_path, project_root=None):
    mode = _mode()
    out = {"scanner": "character_state_drift",
           "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory",
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

    chars = _load_characters(project_root)
    if not chars:
        out["note"] = "无人物卡·跳过（北极星②）"
        out["character_count"] = 0
        return out
    char_names = []
    for c in chars:
        char_names.append(c["name"])
        for a in c.get("aliases", []):
            if a:
                char_names.append(a)
    char_names = list(dict.fromkeys(char_names))  # 保持顺序去重

    ledger, char_ledger, ledger_placeholder = _load_ledger(project_root)
    out["ledger_placeholder"] = ledger_placeholder
    out["character_count"] = len(chars)
    out["stable_attr_count"] = len(ledger["stable_identity_attrs"])
    out["dynamic_attr_count"] = len(ledger["dynamic_state_attrs"])

    claims = _extract_attr_value_claims(text, char_names, ledger)
    out["claim_count"] = len(claims)
    out["stable_claim_count"] = sum(1 for c in claims if c["classification"] == "stable")
    out["dynamic_claim_count"] = sum(1 for c in claims if c["classification"] == "dynamic")

    drifts = _detect_stable_drifts(claims, char_ledger)
    out["stable_drift_count"] = len(drifts)
    out["stable_drift_samples"] = drifts[:5]

    out["dynamic_state_updates"] = _collect_dynamic_updates(claims)[:50]

    if drifts:
        msg = (f"稳定身份漂移 {len(drifts)} 处："
               + "·".join(d["msg"] for d in drifts[:2]))
        if mode == "active":
            out["violations"].append({
                "kind": "stable_identity_drift", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "drift_count": len(drifts),
                "_doc": ("NKW 时态分离·stable_identity 异动 = 角色档真矛盾·"
                         "dynamic_state 已二筛剔除·advisory·绝不 hard_gate"),
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] character_state_drift: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="NKW 时态分离·stable_identity drift 扫描器·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
