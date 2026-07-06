#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unreliable_narrator_typology_scanner.py — Phelan 6 轴 × TUNa 4 原型不可靠叙述
+ 8 类 verbal_tic 密度 (advisory · cluster · 2026-06-20 R8 W4 L19)

【缺口】R8 联网调研 (Brei et al. ACL 2025 TUNa·Phelan《Living to Tell about It》·
LHN Unreliability)：不可靠叙述是严肃文学 / 心理悬疑 / 谍战 / 推理 / 政治权谋核心工艺·
全系统零检测·LLM 默认产「全可靠 reliable=1.0 中性叙述」=工艺扁平。

【做法 · 确定性纯规则正则 (不依赖 LLM)】：
  1. 作者档门控：读 unreliable_narrator_profile{axis, archetype,
     signal_intensity_target, allowed_archetype_switch_per_volume}。
     无 profile → skip (北极星②)·reliable_baseline=1.0 (全可靠) → skip。
  2. 8 类 verbal_tic 词典 (TUNa-derived)：
     ① hedge       犹豫/含糊：也许/或许/大概/似乎/我说不准/我也说不清
     ② fault_admission 自承缺陷：我承认/我也不否认/或许我错了/我可能记岔了
     ③ defensive   防御反驳：你别误会/我不是那个意思/不是我说/我可没那么说
     ④ digression  跑题岔题：扯远了/说回正事/对了对了/这事儿暂且不提
     ⑤ inner_inconsistency 自相矛盾：我刚才说.../话又说回来.../也不对/又或者
     ⑥ selective_memory 选择性记忆：我记不太清了/那时太久了/具体怎么回事忘了
     ⑦ disbelief   质疑可信：不知道是不是真的/天知道/谁信谁傻/也未必属实
     ⑧ picaro_cunning 流浪汉狡黠：嘿嘿/老实说/不瞒你说/谁让你这么实诚/反正我又不亏
  3. 计算 verbal_tic_per_1k (8 类合计) + 各类细分计数。
  4. signal_intensity_target = floor 阈值 (per_1k)·当前密度 < floor → advisory「不可靠
     信号稀薄·退化中性叙述」。

【北极星② / ⑤ 顾问非法官】POV 模式由作者档/cluster brief 声明 · verbal_tic 是工艺
advisory · code UNRELIABLE_NARRATOR_SIGNAL_THIN 绝不进 audit_hub.HARD_GATE_CODES。
env UNRELIABLE_NARRATOR_MODE: off / shadow (默认·只记不判) / active。

【与 R7 firstperson_retro_self_gap 去重】：
  - firstperson_retro 查 narrating-self 签到密度 (回溯感)·POV 模式门控 first_retro_*
  - 本 scanner 查 unreliable 信号密度 (可信度漂移)·门控 unreliable_narrator_profile
  - 完全正交：前者管「时态距离」, 本者管「可信度距离」

用法：python unreliable_narrator_typology_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "UNRELIABLE_NARRATOR_SIGNAL_THIN"

# 8 类 verbal_tic 词典 (高确定性·宁可漏报)
VERBAL_TIC_CATEGORIES = {
    "hedge": re.compile(
        r"(也许|或许|大概|似乎|可能|说不准|说不清|不太确定|不大确定|"
        r"我也不晓得|应当是|想必是|约莫|大约|多半)"
    ),
    "fault_admission": re.compile(
        r"(我承认|我也不否认|或许我错了|可能是我记岔了|记错了|是我搞错了|"
        r"我承认自己|我可能错了|我没说清楚|是我没说明白)"
    ),
    "defensive": re.compile(
        r"(你别误会|我不是那个意思|不是我说|我可没|你听我说|你听我解释|"
        r"我没那意思|话不是这么说|我并没有|你别多想)"
    ),
    "digression": re.compile(
        r"(扯远了|说回正事|对了对了|这事儿暂且不提|这话说远了|跑题了|"
        r"先按下不提|不提这茬|不说这个|话说回来)"
    ),
    "inner_inconsistency": re.compile(
        r"(我刚才说|话又说回来|也不对|又或者|不,不对|不对不对|"
        r"等等[，,]我|不[，,]应当是|不[，,]准确说|我重新说)"
    ),
    "selective_memory": re.compile(
        r"(我记不太清|那时太久|具体怎么回事忘了|忘了大半|记不得|记不清|"
        r"印象模糊|具体细节记不清|有些细节忘了|大部分忘了)"
    ),
    "disbelief": re.compile(
        r"(不知道是不是真的|天知道|谁信谁傻|也未必属实|信不信由你|"
        r"真假难辨|未必当真|不知真假|姑妄言之|姑妄听之)"
    ),
    "picaro_cunning": re.compile(
        r"(嘿嘿|老实说|不瞒你说|谁让你这么实诚|反正我又不亏|我才不傻|"
        r"我可精得很|这买卖不亏|何乐而不为|占点便宜)"
    ),
}

# 🔬 2026-07-04 zero_shot_prototype 语义补召回原型例句（军火库 3.3 节·复用已上线基建）
# 每类 4 条典型例句·措辞刻意区别于上面的正则词表→抓正则漏掉的同义改写。
# 内容后端不可用（content_backend_available False·测试默认）→ classify_batch 返 all-None
# → 无补召回·纯正则·逐字节零回归。命中标 source="zero_shot" 可追溯（与正则命中并集非替换）。
_VERBAL_TIC_PROTOTYPES = {
    "hedge": ["这事儿我也拿不太准", "谁知道是不是真会那样呢", "兴许吧，我也说不好", "八成是这么回事，但也难讲"],
    "fault_admission": ["好吧这次确实是我判断失误", "我得认之前那话说得太满", "回头想想是我理解偏了", "算我看走眼了"],
    "defensive": ["你可别往那上头想", "我压根没那个心思", "这话传到你耳朵里就变味了", "我说这些不是要跟你争"],
    "digression": ["哎扯哪儿去了接着说正事", "这个先搁一边回到刚才", "咱不聊这个了说回主线", "岔了岔了说到哪儿了"],
    "inner_inconsistency": ["不对我刚才那么说不准确", "等一下应该反过来才对", "唔好像也不全是这样", "让我重新捋一遍"],
    "selective_memory": ["年头太久细节记不真切了", "那天具体怎么着我印象很淡", "大半都忘干净了", "只记得个大概别的想不起来"],
    "disbelief": ["这话你信几分自己掂量", "真真假假谁分得清", "反正我是半信半疑", "当个乐子听听就好别当真"],
    "picaro_cunning": ["嘿这点便宜不占白不占", "我可没那么实在", "这笔账怎么算我都不亏", "傻子才吃这个亏"],
    # 中性对照类：nearest-centroid 必须有负类·否则中性句被强分到最近 tic 类（floor=0.5 过火）。
    # "other" 不在 hits 键里·augment 的 `cat not in hits: continue` 自动跳过（不计入任何 tic）。
    "other": ["他走进房间坐下", "外面下起了小雨", "桌上摆着一杯凉茶", "她翻开手里的书", "夜色渐渐深了"],
}

_SENT_SPLIT_RE = re.compile(r"[^。！？!?\n]+[。！？!?\n]?")


def _split_sentences_with_offsets(text: str) -> "list[tuple[str, int]]":
    """切句 + 记录每句起始字符偏移（供语义命中定位·dedup 用）。"""
    out = []
    for m in _SENT_SPLIT_RE.finditer(text):
        s = m.group(0).strip()
        if len(s) >= 4:
            out.append((s, m.start()))
    return out


def _augment_verbal_tics_semantic(text: str, hits: dict) -> int:
    """zero_shot 语义补召回：对 hits 就地并集补入正则漏掉的同义表达（标 source=zero_shot）。
    返回补入条数。内容后端不可用/异常 → classify_batch 返 all-None → 补 0（零回归）。"""
    sents = _split_sentences_with_offsets(text)
    if not sents:
        return 0
    try:
        import zero_shot_prototype
        results = zero_shot_prototype.classify_batch(
            [s for s, _ in sents], _VERBAL_TIC_PROTOTYPES, floor=0.5)
    except Exception:
        return 0
    if not results:
        return 0
    added = 0
    for (sent, off), res in zip(sents, results):
        if not res:
            continue
        cat = res.get("label")
        if cat not in hits:
            continue
        span_end = off + len(sent)
        # dedup：该句区间内该类已有正则命中 → 跳过（并集去重）
        if any(off <= h["pos"] < span_end for h in hits[cat]):
            continue
        hits[cat].append({"term": sent[:24], "pos": off, "source": "zero_shot"})
        added += 1
    return added


# Phelan 6 轴
PHELAN_AXES = {
    "knowledge", "perception", "value", "ethics", "narration_reliability", "intent",
}
# TUNa 4 原型
TUNA_ARCHETYPES = {"picaro", "madman", "naif", "clown"}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("UNRELIABLE_NARRATOR_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_unreliable_profile(project_root) -> dict | None:
    """读作者档 unreliable_narrator_profile。

    schema: {
      axis: str/list (PHELAN_AXES 之一或子集),
      archetype: str (TUNA_ARCHETYPES 之一),
      signal_intensity_target: float (per_1k·下限),
      allowed_archetype_switch_per_volume: int,
      reliable: 0/1 (1.0=全可靠 → skip)
    }
    无 → None。
    """
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    prof = obj.get("unreliable_narrator_profile")
    if not isinstance(prof, dict):
        return None
    return prof


def _is_reliable_default(profile: dict) -> bool:
    """reliable=1.0 (全可靠 / 爽文默认) → 应 skip。"""
    rel = profile.get("reliable")
    try:
        return rel is not None and float(rel) >= 1.0
    except (TypeError, ValueError):
        return False


def detect_verbal_tics(text: str) -> dict:
    """扫 8 类 verbal_tic 命中。返回 {category: [{term, pos}, ...]}。"""
    text = _strip_changes(text)
    hits: dict = {}
    for cat, rx in VERBAL_TIC_CATEGORIES.items():
        items = []
        for m in rx.finditer(text):
            items.append({"term": m.group(0), "pos": m.start()})
        hits[cat] = items
    return hits


def scan(draft_path, project_root=None) -> dict:
    """不可靠叙述 verbal_tic 密度检测。永远 advisory。"""
    mode = _mode()
    out = {
        "scanner": "unreliable_narrator_typology",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "warning": None,
        "violations": [],
        "verdict": "PASS",
    }
    if mode == "off":
        return out

    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    profile = _read_unreliable_profile(project_root)
    out["unreliable_narrator_profile"] = profile
    if not profile:
        out["note"] = "无 unreliable_narrator_profile·跳过(北极星②:作者未声明不可靠模式不擅判)"
        return out
    if _is_reliable_default(profile):
        out["note"] = "reliable=1.0 全可靠基线·跳过(爽文档默认旁路)"
        return out

    floor = profile.get("signal_intensity_target")
    try:
        floor = float(floor) if floor is not None else 0.5
    except (TypeError, ValueError):
        floor = 0.5
    out["signal_intensity_floor"] = floor

    hits = detect_verbal_tics(draft)
    semantic_added = _augment_verbal_tics_semantic(draft, hits)  # zero_shot 补召回·并集·标 source
    out["semantic_augmented_hits"] = semantic_added
    category_counts = {cat: len(v) for cat, v in hits.items()}
    total_hits = sum(category_counts.values())
    per_1k = round(total_hits / (cjk / 1000.0), 3) if cjk > 0 else 0.0

    out["verbal_tic_per_1k"] = per_1k
    out["verbal_tic_total"] = total_hits
    out["category_counts"] = category_counts
    # 样本（每类前 3 个命中）
    sample_per_cat = {}
    for cat, items in hits.items():
        if items:
            sample_per_cat[cat] = [it["term"] for it in items[:3]]
    out["sample_per_category"] = sample_per_cat

    # 轴 / 原型回显
    axis = profile.get("axis")
    archetype = profile.get("archetype")
    out["declared_axis"] = axis
    out["declared_archetype"] = archetype
    # 原型有效性提示
    if isinstance(archetype, str) and archetype.strip().lower() not in TUNA_ARCHETYPES:
        out["archetype_note"] = (
            f"archetype={archetype!r} 不在 TUNa 4 原型 ({sorted(TUNA_ARCHETYPES)})·"
            f"建议归一·非阻断")

    msg = None
    if per_1k < floor:
        msg = (
            f"不可靠叙述信号偏稀: verbal_tic 密度 {per_1k}/千字 < 目标 {floor}/千字 "
            f"(命中 {total_hits} 处·分类 "
            f"{', '.join(f'{c}={n}' for c, n in category_counts.items() if n)})·"
            f"declared axis={axis} archetype={archetype}·"
            f"建议加 hedge/fault_admission/defensive 等口吻信号"
        )
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "unreliable_narrator_typology",
                "severity": "minor",
                "message": msg,
                "verbal_tic_per_1k": per_1k,
                "floor": floor,
                "category_counts": category_counts,
                "declared_axis": axis,
                "declared_archetype": archetype,
                "_doc": (
                    "不可靠叙述是工艺 advisory·全可靠 (reliable=1.0) 风格作者可豁免·"
                    "绝不 hard_gate·与 firstperson_retro 正交 (前者查时态距离 · 本者查可信度距离)"
                ),
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow
            print(f"[SHADOW] unreliable_narrator_typology: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Phelan 6 轴 × TUNa 4 原型 + 8 类 verbal_tic 不可靠叙述检测 (advisory)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 unreliable_narrator_profile 门控")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
