#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sdt_motivation_regulation_advisory.py — SDT 六级动机调节连续体追踪·shadow

【缺口 · R22 W10 Batch-EE·P1 · 2026-06-21】Deci & Ryan SDT 2024：
  内驱-外驱不是二元，是 6 级连续体（OIT 续）：
    intrinsic / integrated / identified / introjected / external / amotivation
  每级对应不同心理 token 语言指纹：
    intrinsic    = 兴趣词 (有趣/喜欢/想/好奇/玩/乐)
    integrated   = 自我认同 (一直/本来/天性/就是这种人/我是/我向来)
    identified   = 价值认同 (重要/应该/值得/必须做)
    introjected  = 羞耻/愧疚/面子 (丢脸/羞愧/对不起/愧疚/良心)
    external     = 奖惩 (赏/罚/赚/钱/逼/不得不/被迫/命令)
    amotivation  = 无意义/麻木 (无所谓/随便/不在乎/反正/算了)

  角色相邻 cluster 主导调节跨度≥2 级且中间无 on-page 触发
  (重大事件/创伤/觉醒/丧失) → 漂移 advisory（动机突变穿帮）。

【与既有 scanner 显式去重】
  - character_state_drift_scanner：状态(情绪/姿态)漂移
    本 scanner = SDT 6 级动机层（更深层·非情绪）·正交
  - character_arc_update：弧线进度
    本 scanner = 当前 cluster 主导调节 + 跨 cluster 跨度·正交

【做法 · 确定性占位（零 LLM）】
  1. 读 _数据库/人物卡.json → 主要角色名
  2. 扫每个角色 ±30 CJK 上下文窗 SDT 6 类 lexicon 命中频次
  3. 该 cluster 该角色 dominant_regulation = top-1 类
  4. 读 _数据库/sdt_regulation_profile.json (若存在) → 上一 cluster
     dominant_regulation·若跨度≥2 级（按 6 级排序）且本 cluster 无触发词
     → DRIFT advisory
  5. 写回 sdt_regulation_profile.json 当前 cluster 的 dominant_regulation
     （cluster-save-state step 9 时合并）

【🔴 2026-07-03 zero_shot_prototype 模型优先路径】每扇 ±30 CJK 窗口真后端可用时优先用
  zero_shot_prototype.classify() 做语义分类（该窗口记 1 票）；否则/置信不足 → 100% 走
  原词典逐词累加（默认无真后端时逐字节零回归）。用了模型的角色 distribution dict 混入
  `_source=zero_shot_embedding` 标记键（不参与 top-1 计票）。

【🔴 2026-07-03 Wave-4 性能层】scan() 内不再逐角色调 _dominant_regulation()（其内部逐
  窗口触发子进程）——改用 _dominant_regulations_batch() 把本次全部角色 × 全部窗口一次
  交给 zero_shot_prototype.classify_batch()（单角色 _dominant_regulation() 仍保留供单
  角色场景/测试直接调用，行为不变）。

【北极星⑤】顾问非法官·全 advisory·env SDT_REGULATION_MODE
  SDT_REGULATION_DRIFT 绝不进 audit_hub.HARD_GATE_CODES。
  作者档『_sdt_regulation_override=true』可豁免（反类型作者刻意）。

用法: python sdt_motivation_regulation_advisory.py <draft> [--project <root>] [--cluster <id>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ISSUE_CODE = "SDT_REGULATION_DRIFT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# SDT 6 级 lexicon 占位（_placeholder=true）·真词典 defer
SDT_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "regulation_order": [
        "intrinsic", "integrated", "identified",
        "introjected", "external", "amotivation",
    ],
    "tokens": {
        "intrinsic":    ["有趣", "喜欢", "好奇", "想要", "乐意", "享受", "着迷"],
        "integrated":   ["一直", "本来", "天性", "向来", "我是这种", "本性"],
        "identified":   ["重要", "应该", "值得", "必须", "意义"],
        "introjected":  ["丢脸", "羞愧", "对不起", "愧疚", "良心", "面子"],
        "external":     ["赏赐", "惩罚", "赚钱", "被迫", "不得不", "命令", "逼"],
        "amotivation":  ["无所谓", "随便", "不在乎", "反正", "算了", "麻木"],
    },
}

# 触发词词典占位：on-page 重大事件→允许跨度
TRIGGER_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "tokens": [
        "觉醒", "突破", "死了", "背叛", "真相", "顿悟",
        "重创", "重生", "失去", "崩塌", "灾", "诀别",
    ],
}

# 🔴 2026-07-03 zero_shot_prototype 模型优先路径·SDT 六级调节 embedding 原型例句
# （占位·3 条/类·待金标准校准·真后端不可用时 100% 走词典逐词累加兜底）
_SDT_REGULATION_PROTOTYPES = {
    "intrinsic": ["他觉得这件事很有趣，忍不住想多做一会儿", "她喜欢这种感觉，纯粹是因为好奇",
                  "他乐在其中，根本不需要理由"],
    "integrated": ["他一直是这样的人，这就是他的天性", "这本来就是他的本性，从未改变过",
                   "他向来如此，仿佛生来就该这么做"],
    "identified": ["他觉得这件事很重要，必须做好", "这是应该做的，值得付出代价",
                   "他认定这有意义，才咬牙坚持"],
    "introjected": ["他觉得丢脸，愧疚得抬不起头", "不这么做他会良心不安",
                    "对不起大家，他心里满是羞愧"],
    "external": ["他是被逼的，不得不照命令去做", "为了赚钱，他只能忍着去做",
                 "上头下了命令，他没有选择"],
    "amotivation": ["他觉得无所谓，反正怎样都一样", "随便吧，他懒得再多想",
                    "算了，他心里已经麻木"],
}


def _classify_sdt_window(ctx: str) -> "str | None":
    """真后端优先用 zero_shot_prototype 分类窗口调节类型·否则 None（调用方 100% 走词典累加）。"""
    try:
        import zero_shot_prototype
        result = zero_shot_prototype.classify(ctx, _SDT_REGULATION_PROTOTYPES, floor=0.5)
        if result is not None:
            return result["label"]
    except Exception:
        pass
    return None


def _mode() -> str:
    m = (os.environ.get("SDT_REGULATION_MODE") or "shadow").strip().lower()
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
    """作者档 _sdt_regulation_override=true → 反类型豁免"""
    if not project_root:
        return False
    p = Path(project_root) / "作者风格.json"
    if not p.exists():
        return False
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(obj.get("_sdt_regulation_override"))


def _dominant_regulation(text: str, char_name: str, window: int = 30) -> tuple[str, dict]:
    """扫角色名 ±window CJK 上下文 6 类调节类型 → top-1。

    🔴 2026-07-03 模型优先：每扇窗口真后端可用时优先用 zero_shot_prototype 分类
    （该窗口记 1 票）；否则/置信不足 → 100% 走原词典逐词累加（同窗口可能命中多词多类，
    默认无真后端时逐字节零回归）。任一窗口用了模型 → 返回的 distribution dict 混入
    `_source` 标记键（不参与 top-1 计票，同 `_BURST_LEXICONS._placeholder` 记法）。
    """
    if not char_name or char_name not in text:
        return ("", {})
    counts: Counter = Counter()
    tokens_map = SDT_LEXICON_PLACEHOLDER["tokens"]
    used_model = False
    for m in re.finditer(re.escape(char_name), text):
        lo = max(0, m.start() - window)
        hi = min(len(text), m.end() + window)
        ctx = text[lo:hi]
        model_label = _classify_sdt_window(ctx)
        if model_label is not None:
            counts[model_label] += 1
            used_model = True
            continue
        for reg, lex in tokens_map.items():
            for w in lex:
                if w in ctx:
                    counts[reg] += 1
    if not counts:
        return ("", {})
    top = counts.most_common(1)[0][0]
    dist = dict(counts)
    if used_model:
        dist["_source"] = "zero_shot_embedding"
    return top, dist


def _classify_windows_batch(windows: list) -> list:
    """真后端优先批量分类全部窗口语义（Wave-4：一次 classify_batch 取代逐窗口子进程调用）。

    返回与 windows 等长的 label list（每项 str 或 None）。异常/无真后端 → 全 None
    （调用方 100% 回退词典逐词累加，零回归）。
    """
    if not windows:
        return []
    try:
        import zero_shot_prototype
        results = zero_shot_prototype.classify_batch(windows, _SDT_REGULATION_PROTOTYPES, floor=0.5)
        return [r["label"] if r is not None else None for r in results]
    except Exception:
        return [None] * len(windows)


def _dominant_regulations_batch(text: str, char_names: list, window: int = 30) -> dict:
    """批量版 _dominant_regulation：本次全部角色 × 全部窗口一次 classify_batch。

    🔴 2026-07-03 Wave-4：先收集全部角色的全部 ±window CJK 窗口，一次交给
    zero_shot_prototype.classify_batch()（取代逐窗口调 _classify_sdt_window 触发子进程），
    再按角色拆回 top-1 + distribution——数学结果与逐角色调 _dominant_regulation 完全一致。

    返回 {char_name: (dominant, distribution)}（角色名不在文本中 → ("", {})，同
    _dominant_regulation 语义对齐）。
    """
    tokens_map = SDT_LEXICON_PLACEHOLDER["tokens"]
    window_owners: list = []   # 与 all_windows 等长·记录该窗口属于哪个角色
    all_windows: list = []
    matches_by_char: "dict[str, list]" = {}
    for char_name in char_names:
        if not char_name or char_name not in text:
            continue
        spans = list(re.finditer(re.escape(char_name), text))
        matches_by_char[char_name] = spans
        for m in spans:
            lo = max(0, m.start() - window)
            hi = min(len(text), m.end() + window)
            window_owners.append(char_name)
            all_windows.append(text[lo:hi])

    model_labels = _classify_windows_batch(all_windows)

    counts_by_char: "dict[str, Counter]" = {c: Counter() for c in matches_by_char}
    used_model_by_char: "dict[str, bool]" = {c: False for c in matches_by_char}
    for char_name, ctx, model_label in zip(window_owners, all_windows, model_labels):
        counts = counts_by_char[char_name]
        if model_label is not None:
            counts[model_label] += 1
            used_model_by_char[char_name] = True
            continue
        for reg, lex in tokens_map.items():
            for w in lex:
                if w in ctx:
                    counts[reg] += 1

    out: dict = {}
    for char_name in char_names:
        if char_name not in matches_by_char:
            out[char_name] = ("", {})
            continue
        counts = counts_by_char[char_name]
        if not counts:
            out[char_name] = ("", {})
            continue
        top = counts.most_common(1)[0][0]
        dist = dict(counts)
        if used_model_by_char[char_name]:
            dist["_source"] = "zero_shot_embedding"
        out[char_name] = (top, dist)
    return out


def _has_trigger(text: str) -> bool:
    for w in TRIGGER_LEXICON_PLACEHOLDER["tokens"]:
        if w in text:
            return True
    return False


def _regulation_distance(prev: str, curr: str) -> int:
    order = SDT_LEXICON_PLACEHOLDER["regulation_order"]
    if prev not in order or curr not in order:
        return 0
    return abs(order.index(prev) - order.index(curr))


def _load_prev_profile(project_root) -> dict:
    if not project_root:
        return {}
    p = Path(project_root) / "_数据库" / "sdt_regulation_profile.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {"scanner": "sdt_motivation_regulation_advisory", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "drift_count": 0, "drift_samples": []}
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
        out["note"] = "作者档 _sdt_regulation_override=true · 反类型豁免"
        return out

    chars = _load_characters(project_root)
    if not chars:
        out["note"] = "无人物卡或角色名空·跳过（北极星②）"
        out["character_count"] = 0
        return out
    out["character_count"] = len(chars)

    prev_profile = _load_prev_profile(project_root)
    prev_doms = prev_profile.get("by_character", {}) if isinstance(prev_profile, dict) else {}
    has_trig = _has_trigger(text)

    curr_doms: dict = {}
    drifts = []
    # 🔴 2026-07-03 Wave-4：全部角色一次 classify_batch（取代逐角色 _dominant_regulation 内部
    # 逐窗口子进程调用）
    dom_by_char = _dominant_regulations_batch(text, chars)
    for ch in chars:
        dom, dist = dom_by_char.get(ch, ("", {}))
        if not dom:
            continue
        curr_doms[ch] = {"dominant": dom, "distribution": dist}
        prev_dom = (prev_doms.get(ch) or {}).get("dominant", "")
        if prev_dom:
            d = _regulation_distance(prev_dom, dom)
            if d >= 2 and not has_trig:
                drifts.append({"character": ch, "prev": prev_dom, "curr": dom,
                               "distance": d, "on_page_trigger": False})

    out["dominant_by_character"] = curr_doms
    out["drift_count"] = len(drifts)
    out["drift_samples"] = drifts[:5]

    if drifts:
        msg = (f"SDT 调节漂移 {len(drifts)} 处："
               + "·".join(f"{d['character']} {d['prev']}→{d['curr']}(Δ{d['distance']})"
                          for d in drifts[:3]))
        if mode == "active":
            out["violations"].append({
                "kind": "sdt_regulation_drift", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "drift_count": len(drifts),
                "_doc": "SDT 6 级跨度≥2 无 on-page 触发·梦/通灵/补叙可豁免·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] sdt_motivation_regulation_advisory: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="SDT 六级动机调节·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
