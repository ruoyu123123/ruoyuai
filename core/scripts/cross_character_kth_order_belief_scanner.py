#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_character_kth_order_belief_scanner.py — K-order(K=2) 嵌套信念扫描器
(R20 W9 Batch-Z·P0)

【缺口·2026-06-21·R20 STRONG Q3-Q4 论文 id 15】
R18 OmniToM 1-order belief_state[char] 只查「A 知不知道事实」，不查 K=2 嵌套
信念：「A 以为 B 知道 X」「A 不知道 B 已经知道 X」——这是网文最常制造 dramatic
irony 的层级（信息差喜剧/反转）。LLM 写作时常错把 K-2 信念当 ground truth，
末段 reveal 时与 K-1 实际不符 → 读者看出来 BUG。

【做法·确定性占位（零 LLM）】
  1. 复用 character_belief_ledger 的人物卡 + locked_fact_lexicon 加载
  2. 按 scene 遍历，先维护 K-1 belief_state[char] = set(fact_ref)（同 R18）
  3. 再扫每个 scene 内的 K-2 nesting pattern：
       <A> + (以为|认为|觉得|猜|猜测|相信|怀疑) + <B> + (知道|了解|不知道|不晓得|相信) + <fact_ref>
     建立 nested_belief[A][B][fact_ref] ∈ {"thinks_knows", "thinks_unknown"}
  4. 末轮 K-order vs K-1 矛盾校验：
     - thinks_knows 但 B 实际未在 belief_state[B] → DRIFT（A 高估 B）
     - thinks_unknown 但 B 在 belief_state[B] → DRIFT（A 不知 B 已知 = dramatic
       irony anchor，若 outline-planner 在 dramatic_irony_anchor 显式声明则
       合法不报；缺声明且后段 reveal 同 topic → 报 drift）
  5. outline-planner 通过 manifest.dramatic_irony_anchor=[{a,b,topic,scenes:[i,j]}]
     白名单声明的合法 K-order 不对称不报。

【与既有 scanner 严格正交】
  - character_belief_ledger : K=1 一阶（A 知不知道 fact）·正交（本=K=2 嵌套）
  - dramatic_irony_scanner  : narrator vs 角色 TELL 词·正交
  - focalizer_perception_bounds : narrator 越界·正交
  - locked_fact_cross_scene : 恒定事实·正交

【北极星⑤】顾问非法官·全 advisory·env CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE
  默认 shadow·CHARACTER_KTH_ORDER_BELIEF_DRIFT 绝不 hard_gate。

用法: python cross_character_kth_order_belief_scanner.py <draft> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CHARACTER_KTH_ORDER_BELIEF_DRIFT"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 复用 R18 的 K-1 知识动词
KNOWLEDGE_VERBS = ("知道", "听说", "明白", "记起", "得知", "晓得", "了解到", "意识到", "察觉")
# K-2 嵌套元动词：A *以为/认为/觉得* B *知道/不知道* X
META_VERBS = ("以为", "认为", "觉得", "猜", "猜测", "相信", "怀疑", "笃定", "深信", "断定")
# K-2 嵌套谓语动词：B 知道/不知道
NESTED_KNOW_VERBS = ("知道", "了解", "晓得", "明白", "察觉", "察知", "意识到")
NESTED_UNKNOW_VERBS = ("不知道", "不晓得", "不明白", "未察觉", "毫无察觉", "毫不知情",
                      "蒙在鼓里")

FACT_REF_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "facts": [
        "身世", "真名", "真相", "秘密", "暗号", "暗记",
        "下落", "藏身", "底细", "出身", "血脉", "宝藏",
        "阴谋", "计划", "病情", "婚事",
    ],
}

SCENE_SPLIT = re.compile(r"\n\s*[*◇◆━─=]{3,}\s*\n|\n\s*场景[:：]\s*[^\n]*\n|"
                         r"\n\s*第[一二三四五六七八九十0-9]+幕[^\n]*\n")


def _mode() -> str:
    m = (os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_characters(project_root):
    if not project_root:
        return set()
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return set()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    names = set()
    for c in (obj.get("characters", []) if isinstance(obj, dict) else []):
        if not isinstance(c, dict):
            continue
        nm = c.get("name", "")
        if nm:
            names.add(nm)
        for a in (c.get("aliases") or []):
            if a:
                names.add(a)
    return names


# 🔴 2026-06-29 孤儿scanner重接线(名字错配·指向真数据源)
# 旧读 _数据库/locked_fact.json = 零 producer 幻影文件（永远缺失 → 永远兜底占位）。真 locked
# facts 在 _数据库/事件簇.json.clusters[].locked_facts[].fact（producer: apply_archive.
# apply_locked_facts）。改读它提精度·保留占位词典兜底（向后兼容·缺则兜底）。
def _load_locked_facts_from_clusters(project_root):
    """读 事件簇.json.clusters[].locked_facts[].fact（producer: apply_locked_facts）。
       聚合全 cluster 的硬事实字符串（去重保序）。无文件/破损/无 locked_facts → []。"""
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "事件簇.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(obj, dict):
        return []
    out, seen = [], set()
    for c in (obj.get("clusters") or []):
        if not isinstance(c, dict):
            continue
        for lf in (c.get("locked_facts") or []):
            fact = lf.get("fact") if isinstance(lf, dict) else (lf if isinstance(lf, str) else None)
            if fact and fact not in seen:
                seen.add(fact)
                out.append(str(fact))
    return out


def _load_fact_refs(project_root):
    """事件簇.json.clusters[].locked_facts fact_ref 候选·缺则用占位词典兜底"""
    refs = _load_locked_facts_from_clusters(project_root)
    if refs:
        return refs
    return list(FACT_REF_LEXICON_PLACEHOLDER["facts"])


def _load_anchors(manifest_path):
    """读 manifest.dramatic_irony_anchor 白名单。
    返回 set of (a, b, topic) 元组（合法的 K-order 不对称）。"""
    if not manifest_path:
        return set()
    p = Path(manifest_path)
    if not p.exists():
        return set()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    anchors = obj.get("dramatic_irony_anchor") if isinstance(obj, dict) else None
    if not isinstance(anchors, list):
        return set()
    out = set()
    for it in anchors:
        if not isinstance(it, dict):
            continue
        a = (it.get("a") or it.get("character_a") or "").strip()
        b = (it.get("b") or it.get("character_b") or "").strip()
        topic = (it.get("topic") or it.get("fact_ref") or "").strip()
        if a and b and topic:
            out.add((a, b, topic))
    return out


def _split_scenes(text):
    parts = SCENE_SPLIT.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _chars_in_scene(scene_text, all_names):
    return {n for n in all_names if n in scene_text}


# ============ K=2 嵌套匹配 ============

def _extract_nested_beliefs(scene_text, all_names, fact_refs):
    """从场景文本抽 K=2 嵌套信念断言。
    匹配 A + META_VERB + ... + B + NESTED_(KNOW|UNKNOW)_VERB + fact_ref 窗口。
    返回 list[{a,b,fact,polarity,span}]·span = (lo, hi) 嵌套整段在 scene_text 中
    的字符区间·供 K-1 belief 更新阶段 mask 排除（B 仅作为 A 的嵌套对象提及不算
    实际在场习得 fact）。
    """
    nestings = []
    if len(all_names) < 2:
        return nestings
    names_list = list(all_names)
    for mv in META_VERBS:
        for m in re.finditer(re.escape(mv), scene_text):
            left_lo = max(0, m.start() - 6)
            right_hi = m.end() + 40
            left = scene_text[left_lo:m.start()]
            right = scene_text[m.end():right_hi]
            a = None
            for nm in names_list:
                if nm in left:
                    a = nm
                    break
            if not a:
                continue
            b = None
            b_pos = -1
            for nm in names_list:
                if nm == a:
                    continue
                p = right.find(nm)
                if p >= 0 and (b_pos < 0 or p < b_pos):
                    b = nm
                    b_pos = p
            if not b:
                continue
            polarity = None
            for v in NESTED_UNKNOW_VERBS:
                if v in right:
                    polarity = "thinks_unknown"
                    break
            if polarity is None:
                for v in NESTED_KNOW_VERBS:
                    if v in right:
                        polarity = "thinks_knows"
                        break
            if polarity is None:
                continue
            fact = None
            for f in fact_refs:
                if f in right:
                    fact = f
                    break
            if not fact:
                continue
            nestings.append({
                "a": a, "b": b, "fact_ref": fact, "polarity": polarity,
                "span": (left_lo, right_hi),
            })
    return nestings


def _mask_spans(text, spans, mask_char="·"):
    """把 spans 区间内字符替换为 mask_char·保持长度·供 K-1 in-scene presence 排除嵌套引用。"""
    if not spans:
        return text
    arr = list(text)
    for lo, hi in spans:
        lo = max(0, lo)
        hi = min(len(arr), hi)
        for i in range(lo, hi):
            arr[i] = mask_char
    return "".join(arr)


def _detect_drifts(scenes, all_names, fact_refs, anchors):
    """主循环：维护 K-1 belief_state + 收集 K-2 断言 + 末轮校验。

    K-1 belief 更新只看「嵌套以外」的部分（北极星⑤·防止 A 嵌套提 B 被算作 B 实际
    在场习得 fact·overestimate 才有效）。
    """
    belief_state = {n: set() for n in all_names}
    nested_assertions = []
    for idx, scene in enumerate(scenes):
        nestings = _extract_nested_beliefs(scene, all_names, fact_refs)
        for nst in nestings:
            a, b, fact, pol = nst["a"], nst["b"], nst["fact_ref"], nst["polarity"]
            nested_assertions.append({
                "scene_idx": idx, "a": a, "b": b,
                "fact_ref": fact, "polarity": pol,
                "b_knows_at_assertion": fact in belief_state.get(b, set()),
            })
        # K-1 belief 更新：mask 掉嵌套 span·B 仅作 nested object 不计入 in-scene
        masked = _mask_spans(scene, [nst["span"] for nst in nestings])
        scene_chars = _chars_in_scene(masked, all_names)
        present_refs = {f for f in fact_refs if f in masked}
        for ch in scene_chars:
            belief_state[ch] |= present_refs

    drifts = []
    for ass in nested_assertions:
        a, b, fact, pol = ass["a"], ass["b"], ass["fact_ref"], ass["polarity"]
        if (a, b, fact) in anchors:
            continue
        b_final = fact in belief_state.get(b, set())
        if pol == "thinks_knows" and not b_final:
            drifts.append({**ass, "kind": "a_overestimates_b",
                           "msg": f"{a} 以为 {b} 知道「{fact}」·B 实际全程未知"})
        elif pol == "thinks_unknown" and ass["b_knows_at_assertion"]:
            drifts.append({**ass, "kind": "a_underestimates_b",
                           "msg": (f"{a} 以为 {b} 不知道「{fact}」·B 当时已知"
                                   "·若是 dramatic irony 请在 manifest "
                                   "dramatic_irony_anchor 声明")})
    return drifts, nested_assertions, belief_state


def scan(draft_path, project_root=None, manifest_path=None):
    mode = _mode()
    out = {"scanner": "cross_character_kth_order_belief",
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
    all_names = _load_characters(project_root)
    if len(all_names) < 2:
        out["note"] = "人物卡 <2 名角色·K=2 不适用·跳过"
        out["character_count"] = len(all_names)
        return out
    fact_refs = _load_fact_refs(project_root)
    anchors = _load_anchors(manifest_path)
    out["character_count"] = len(all_names)
    out["fact_ref_count"] = len(fact_refs)
    out["dramatic_irony_anchor_count"] = len(anchors)

    scenes = _split_scenes(text)
    out["scene_count"] = len(scenes)
    drifts, assertions, _ = _detect_drifts(scenes, all_names, fact_refs, anchors)
    out["nested_assertion_count"] = len(assertions)
    out["drift_count"] = len(drifts)
    out["drift_samples"] = drifts[:5]

    if drifts:
        msg = (f"K-order(K=2) 信念漂移 {len(drifts)} 处："
               + "·".join(d["msg"] for d in drifts[:2]))
        if mode == "active":
            out["violations"].append({
                "kind": "kth_order_belief_drift", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "drift_count": len(drifts),
                "_doc": ("OmniToM K-2 嵌套信念·dramatic_irony 请显式 anchor 声明·"
                         "advisory·绝不 hard_gate"),
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] cross_character_kth_order_belief: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="K=2 嵌套信念扫描器(OSCToM)·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None,
                    help="manifest.json 路径·读 dramatic_irony_anchor 白名单")
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
