#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""character_belief_ledger_scanner.py — 角色信念账本(OmniToM 7 维)·跨场景信念结构

【缺口 · R18 W7 Batch-S·P0 · 2026-06-21】arxiv 2605.26322 OmniToM 2026-05-25
+ arxiv 2506.13641 EvolvTrip 2025-06 + arxiv 2601.12410 LLM-vs-Chimps 2026-01：
  多角色叙事最易翻车的不是 narrator 的越界（focalizer_perception_bounds 已查），也不
  是 dramatic_irony 的 TELL 词（dramatic_irony_scanner 已查），而是【角色 A 在场景 N
  里用了 ta 不可能知道的事实】——OmniToM 把它拆成 7 维信念结构：
    ① first-order：A 知道 fact
    ② second-order：A 知道 B 知道 fact
    ③ false belief：A 以为 fact 是 X 但实际是 Y
    ④ knowledge transfer：A 何时获得 fact
    ⑤ shared common ground：A B 共同知识
    ⑥ private knowledge：fact 只在 A 处
    ⑦ propagation lag：fact 从 A 到 B 的延迟

  本占位版只查 ①+④ 最常翻车那条：character_name + 知识动词(知道/听说/明白/记起)
  + fact_ref 出现在 scene_storyboard 里【character 尚未在场】的位置。简化但确定性。

【🔴 2026-06-29 角色信息差(per-character belief)·接真升级】
  优先读持久化 _数据库/character_belief_ledger.json（Phase A/B 产·schema:
    {characters:{<char_id>:{known_facts:[{fact_id,content,learned_at_cluster,can_speak,...}],
     unaware_of:[fact_id]}}, facts:{<fact_id>:{content}}}）：
    检测正文中角色名 + 知识动词窗口内提及【自己 ledger 里没有(unaware_of)或 can_speak=false】的
    fact content → CHARACTER_KNOWLEDGE_LEAK。ledger 不存在 → 退回占位词典逻辑(向后兼容·零行为变化)。
  生成层注入(build_manifest._sanitize_character_belief + gen_writer H7)是重心·本 scanner 是检测兜底。

【与既有 scanner 显式去重】
  - focalizer_perception_bounds：narrator 层 (focalizer 自体不可见/他人内心/空间不在场)
    本 scanner = character 层跨场景信念传播（同一 narrator 不变也会翻）·正交
  - dramatic_irony_scanner：TELL 词 (反讽信号 saying_doing/style_fact)
    本 scanner = 信念-知识传播 (不依赖 saying_doing 矛盾)·正交
  - locked_fact_cross_scene_scanner：恒定事实数值（年龄/日期）的恒定性
    本 scanner = 角色对事实的知情状态（信念）·正交

【做法 · 确定性占位（零 LLM）】
  1. 读 _数据库/人物卡.json → 角色名集合 + alias
  2. 读 _数据库/locked_fact.json (若存在) → fact[i] = {key, character_id?, source_scene?}
     缺则从草稿 harvest「reveal/真相/秘密/告诉」前后 ±50 CJK 抓 fact_ref 候选
  3. belief_state[char] = set()，按 storyboard 顺序遍历每个 scene：
     - scene 出现 char → 把该 scene 的 "公开 reveals" 加进 belief_state[char]
     - 同时扫该 scene 内 char_name + KNOWLEDGE_VERB + fact_ref 段：
       fact_ref 不在 belief_state[char] → CHARACTER_KNOWLEDGE_LEAK
  4. 占位简化：不读 manifest scene_storyboard，按章节/段落+「场景：」/分隔符切场景。

【北极星⑤】顾问非法官·全 advisory·env CHARACTER_BELIEF_LEDGER_MODE
  CHARACTER_KNOWLEDGE_LEAK 绝不进 audit_hub.HARD_GATE_CODES。

用法: python character_belief_ledger_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CHARACTER_KNOWLEDGE_LEAK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 知识动词（角色 + 这些词 + 紧邻 fact_ref → 信念主张）
KNOWLEDGE_VERBS = ("知道", "听说", "明白", "记起", "得知", "晓得", "了解到", "意识到", "察觉")

# fact_ref 候选词典占位（_placeholder=true）：常见涉密名词
FACT_REF_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "facts": [
        "身世", "真名", "真相", "秘密", "暗号", "暗记",
        "下落", "藏身", "底细", "出身", "血脉", "宝藏",
        "阴谋", "计划", "病情", "婚事",
    ],
}

# 场景切分锚（与其他 scanner 保持一致）
SCENE_SPLIT = re.compile(r"\n\s*[*◇◆━─=]{3,}\s*\n|\n\s*场景[:：]\s*[^\n]*\n|"
                         r"\n\s*第[一二三四五六七八九十0-9]+幕[^\n]*\n")


def _mode() -> str:
    m = (os.environ.get("CHARACTER_BELIEF_LEDGER_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_characters(project_root):
    """读人物卡 → 名字+alias 集合"""
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


def _load_fact_refs(project_root):
    """读 locked_fact.json fact_ref 候选·缺则用占位词典"""
    if project_root:
        p = Path(project_root) / "_数据库" / "locked_fact.json"
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    items = obj.get("facts") or obj.get("items") or []
                    refs = []
                    for it in items:
                        if isinstance(it, dict) and it.get("key"):
                            refs.append(str(it["key"]))
                        elif isinstance(it, str):
                            refs.append(it)
                    if refs:
                        return refs
            except (OSError, json.JSONDecodeError):
                pass
    return list(FACT_REF_LEXICON_PLACEHOLDER["facts"])


def _split_scenes(text: str):
    """按分隔符切场景·至少 1 段"""
    parts = SCENE_SPLIT.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _chars_in_scene(scene_text: str, all_names):
    """该场景里出现的角色名集合"""
    return {n for n in all_names if n in scene_text}


def _detect_leaks(scenes, all_names, fact_refs):
    """简化 belief ledger：
       belief_state[char] = {fact_ref ...}
       每场景：(a) char 出现 → fact_refs 在该场景里出现的 → 加入该 char belief
              (b) 检查 char_name + KNOWLEDGE_VERB + fact_ref 窗口
                  fact_ref 不在 belief_state[char] → leak
    """
    belief_state = {n: set() for n in all_names}
    leaks = []
    for idx, scene in enumerate(scenes):
        scene_chars = _chars_in_scene(scene, all_names)
        # 先查 leak（在更新 belief 之前·防止 self-update 把当场 reveal 算合法）
        for char in scene_chars:
            # 找 char_name 后 ±20 CJK 内 KNOWLEDGE_VERB + fact_ref
            for m in re.finditer(re.escape(char), scene):
                window = scene[m.end(): m.end() + 30]
                if not any(v in window for v in KNOWLEDGE_VERBS):
                    continue
                for fref in fact_refs:
                    if fref in window:
                        if fref not in belief_state[char]:
                            leaks.append({
                                "scene_idx": idx,
                                "character": char,
                                "fact_ref": fref,
                                "context": (scene[max(0, m.start()-10):
                                                  m.end()+30]).replace("\n", " ")[:60],
                            })
        # 然后再更新 belief：当场出现的 fact_refs → 在场角色的 belief
        present_refs = {fref for fref in fact_refs if fref in scene}
        for char in scene_chars:
            belief_state[char] |= present_refs
    return leaks, belief_state


# 🔴 2026-06-29 角色信息差(per-character belief)·接真持久化 ledger（Phase A/B 产）
# scanner 升级：优先读 character_belief_ledger.json，检测正文中角色提及/基于自己 ledger 里
# 没有（unaware_of/未在 known_facts）或 can_speak=false 的 fact → CHARACTER_KNOWLEDGE_LEAK。
# 保持 advisory·绝不进 HARD_GATE_CODES（提案 open_q① 先 advisory 观察期）。
# ledger 不存在 → 退回占位逻辑（_detect_leaks·向后兼容·零行为变化）。
_LEDGER_VERB_WINDOW = 40  # ledger fact content 可能较长·窗口比占位版(30)略宽


def _load_belief_ledger(project_root):
    """读持久化 character_belief_ledger.json。无文件 / 破损 / 无 characters → None（退回占位·向后兼容）。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "character_belief_ledger.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(obj, dict) and isinstance(obj.get("characters"), dict) and obj["characters"]:
        return obj
    return None


def _build_charid_name_map(project_root):
    """char_id → 在 prose 里匹配的名字集合（人物卡 id/name 命中则用 name+aliases·否则 char_id 自身）。"""
    m = {}
    if not project_root:
        return m
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return m
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return m
    for c in (obj.get("characters", []) if isinstance(obj, dict) else []):
        if not isinstance(c, dict):
            continue
        names = set()
        if c.get("name"):
            names.add(c["name"])
        for a in (c.get("aliases") or []):
            if a:
                names.add(a)
        cid = c.get("id") or c.get("name")
        if cid:
            m[cid] = names or {cid}
    return m


def _fact_phrase_by_id(fact_id, facts_index):
    """ledger.facts 索引解 fact_id → 可读短语（content/summary 优先·缺则 fact_id 本身）。"""
    f = facts_index.get(fact_id) if isinstance(facts_index, dict) else None
    if isinstance(f, dict):
        return str(f.get("content") or f.get("summary") or fact_id or "")
    if isinstance(f, str):
        return f
    return str(fact_id) if fact_id else ""


def _fact_phrase(kf, facts_index):
    """known_fact 条目 → 可读短语（content 优先·否则经 fact_id 解 facts 索引）。"""
    if isinstance(kf, dict):
        c = kf.get("content")
        if c:
            return str(c)
        return _fact_phrase_by_id(kf.get("fact_id"), facts_index)
    if isinstance(kf, str):
        return kf
    return ""


def _detect_leaks_from_ledger(scenes, ledger, charid_names):
    """接真 ledger 检测：对每个 ledger 角色，构造其【不可引用】短语集——
       ① unaware_of 的 fact（不知情）② known_facts 中 can_speak=false 的（知道但不能说出口）。
       在该角色出场的场景里，角色名 + KNOWLEDGE_VERB 窗口内出现不可引用短语 → leak。
       角色自己 known_facts 里 can_speak!=false 的短语永不算违规（先扣除）。"""
    chars_ledger = ledger.get("characters") or {}
    facts_index = ledger.get("facts") if isinstance(ledger.get("facts"), dict) else {}
    leaks = []
    for char_id, cl in chars_ledger.items():
        if not isinstance(cl, dict):
            continue
        search_names = charid_names.get(char_id) or {char_id}
        speakable, cannot_speak = set(), set()
        for kf in (cl.get("known_facts") or []):
            ph = _fact_phrase(kf, facts_index)
            if not ph:
                continue
            if isinstance(kf, dict) and kf.get("can_speak", True) is False:
                cannot_speak.add(ph)
            else:
                speakable.add(ph)
        forbidden = set(cannot_speak)
        for fid in (cl.get("unaware_of") or []):
            ph = _fact_phrase_by_id(fid, facts_index)
            if ph:
                forbidden.add(ph)
        forbidden -= speakable  # 角色也确知（可说）的短语不算违规
        if not forbidden:
            continue
        for idx, scene in enumerate(scenes):
            if not any(nm in scene for nm in search_names):
                continue
            for nm in search_names:
                for mt in re.finditer(re.escape(nm), scene):
                    window = scene[mt.end(): mt.end() + _LEDGER_VERB_WINDOW]
                    if not any(v in window for v in KNOWLEDGE_VERBS):
                        continue
                    for ph in forbidden:
                        if ph and ph in window:
                            leaks.append({
                                "scene_idx": idx,
                                "character": char_id,
                                "fact_ref": ph,
                                "reason": ("known_but_cannot_speak" if ph in cannot_speak
                                           else "unaware_of"),
                                "context": (scene[max(0, mt.start() - 10):
                                                  mt.end() + _LEDGER_VERB_WINDOW]
                                            ).replace("\n", " ")[:60],
                                "_source": "ledger",
                            })
    return leaks


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "character_belief_ledger", "schema_version": "1.0",
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
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    all_names = _load_characters(project_root)
    if not all_names:
        out["note"] = "无人物卡或角色名空·跳过（北极星②）"
        out["character_count"] = 0
        return out

    out["character_count"] = len(all_names)
    scenes = _split_scenes(text)
    out["scene_count"] = len(scenes)

    # 🔴 2026-06-29 接真：优先读持久化 character_belief_ledger.json（Phase A/B 产）·
    # 缺则退回占位词典逻辑（向后兼容·今天所有旧书无 ledger → 零行为变化）。
    ledger = _load_belief_ledger(project_root)
    if ledger is not None:
        out["ledger_source"] = "persistent"
        out["ledger_character_count"] = len(ledger.get("characters") or {})
        charid_names = _build_charid_name_map(project_root)
        leaks = _detect_leaks_from_ledger(scenes, ledger, charid_names)
    else:
        out["ledger_source"] = "placeholder"
        fact_refs = _load_fact_refs(project_root)
        out["fact_ref_count"] = len(fact_refs)
        leaks, _ = _detect_leaks(scenes, all_names, fact_refs)
    out["leak_count"] = len(leaks)
    out["leak_samples"] = leaks[:5]

    if leaks:
        msg = (f"角色越权知识 {len(leaks)} 处："
               + "·".join(f"{lk['character']}/{lk['fact_ref']}@scene{lk['scene_idx']}"
                          for lk in leaks[:3]))
        if mode == "active":
            out["violations"].append({
                "kind": "character_knowledge_leak", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "leak_count": len(leaks),
                "_doc": "OmniToM 信念账本·梦境/通灵/补叙可豁免·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] character_belief_ledger: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="角色信念账本(OmniToM)·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
