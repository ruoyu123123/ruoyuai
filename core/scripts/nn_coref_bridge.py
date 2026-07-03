#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN角色网络/共指集成
"""nn_coref_bridge.py — 共指消解桥（纯规则后端 · 中文代词→角色解析）。

【目标】将代词（他/她/那个人/少年）解析到具体角色，辅助角色关系提取和一致性检测。

【🔴 2026-07-03 HanLP 后端路径整体拆除（清旧码·实证支撑）】
  历史上本桥曾设 COREF_BACKEND=hanlp 后端（先系统 py 直接 import、后改 venv subprocess 桥），
  但 2026-07-03 真装 hanlp==2.1.3 后实锤：**HanLP 开源版从未提供过任何本地共指消解模型**
  （`hanlp.pretrained` 无 coref 子模块·全部 99 个预训练 key 零命中·GitHub 历史里
  `pretrained/coref.py` 从未存在过；唯一的共指能力在其付费云端 RESTful API，与本桥离线
  subprocess 架构不符）。该路径永远点不亮，按「不兼容不降级/清旧码」原则整体删除
  （连同 core/ml/coref/coref_infer.py）。真中文共指模型是独立 Tier A 调研项
  （见 core/ml/LEARNABLE_BACKLOG.md），若落地将另起新后端名，不复用 hanlp。

【默认安全铁律（北极星⑤·零回归）】
  · RUOYU_NN_COREF != "1"（默认 off·门控未开）→ 返回 []
  · 任何异常 → 返回 []·不崩主流水线
  · 旧 COREF_BACKEND env 已无意义（设成任何值都走 rule·不读取）

Env 门控: RUOYU_NN_COREF（默认 off）

用法：python nn_coref_bridge.py "他走到窗前，张三已经等了很久。他叹了口气。"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# ── 常量 ────────────────────────────────────────────────────

# 代词列表（中文常见三人称/指示代词）
_PRONOUNS_MALE = {"他", "他们", "这个男人", "那个男人", "少年", "男子", "那人", "此人"}
_PRONOUNS_FEMALE = {"她", "她们", "这个女人", "那个女人", "少女", "女子", "那女子"}
_PRONOUNS_NEUTRAL = {"那个人", "此人", "这人", "那人", "对方", "来人", "那家伙"}
_ALL_PRONOUNS = _PRONOUNS_MALE | _PRONOUNS_FEMALE | _PRONOUNS_NEUTRAL

# 性别推断关键词（用于 known_characters 的性别判断）
# 多字优先·单字只保留无歧义的（"王"是常姓不算·"后"可表"之后"不算）
_MALE_INDICATORS = ("先生", "公子", "少爷", "帝君", "兄长", "哥哥", "男", "帝", "哥", "兄", "父", "爷", "叔")
_FEMALE_INDICATORS = ("小姐", "姑娘", "夫人", "妹妹", "女", "娘", "妃", "姐", "嫂", "婶")

# 说话动词（用于对话段落中的人名检测）
_SAY_VERBS_PATTERN = re.compile(
    r"([一-鿿]{1,5})(?:低声道|冷笑道|沉吟道|轻声道|说道|喊道|问道|笑道|"
    r"喝道|怒道|叹道|低声|冷笑|沉吟|轻声|说|道|喊|问|笑|喝|怒|叹)"
)

# changes 分隔符
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


# ── 门控 ────────────────────────────────────────────────────

def enabled() -> bool:
    """门控总开关：RUOYU_NN_COREF=1。"""
    return os.environ.get("RUOYU_NN_COREF") == "1"


# ── 性别推断 ────────────────────────────────────────────────

def _infer_gender(name: str) -> str | None:
    """从角色名推断性别：male / female / None（未知）。

    子串匹配·长指示词优先（"小姐">"姐"）·女性先查（避免"王小姐"被"王"误判男）。
    """
    # 女性指示词优先（长→短自然排序已由 tuple 定义顺序保证）
    for indicator in _FEMALE_INDICATORS:
        if indicator in name:
            return "female"
    for indicator in _MALE_INDICATORS:
        if indicator in name:
            return "male"
    return None


def _pronoun_gender(pronoun: str) -> str | None:
    """代词的性别暗示。"""
    if pronoun in _PRONOUNS_MALE:
        return "male"
    if pronoun in _PRONOUNS_FEMALE:
        return "female"
    return None


# ── 规则后端 ────────────────────────────────────────────────

def _find_character_mentions(text: str, known: list[str]) -> list[dict]:
    """在文本中定位所有已知角色名出现的位置。"""
    mentions = []
    for name in known:
        if not name:
            continue
        for m in re.finditer(re.escape(name), text):
            mentions.append({
                "name": name,
                "start": m.start(),
                "end": m.end(),
                "gender": _infer_gender(name),
            })
    # 按位置排序
    mentions.sort(key=lambda x: x["start"])
    return mentions


def _find_pronoun_mentions(text: str) -> list[dict]:
    """在文本中定位所有代词。"""
    pronouns = []
    for pron in sorted(_ALL_PRONOUNS, key=len, reverse=True):  # 长优先避免子串
        for m in re.finditer(re.escape(pron), text):
            # 排除已被更长代词覆盖的位置
            overlap = False
            for existing in pronouns:
                if (m.start() >= existing["start"] and m.start() < existing["end"]) or \
                   (m.end() > existing["start"] and m.end() <= existing["end"]):
                    overlap = True
                    break
            if not overlap:
                pronouns.append({
                    "mention": pron,
                    "start": m.start(),
                    "end": m.end(),
                    "gender": _pronoun_gender(pron),
                })
    pronouns.sort(key=lambda x: x["start"])
    return pronouns


def _resolve_rule(text: str, known: list[str] | None) -> list[dict]:
    """纯规则共指消解：最近先行词 + 性别匹配。

    策略：
    1. 对每个代词，向前搜索最近的角色名（先行词）
    2. 性别匹配：如果代词有性别暗示，优先匹配同性别角色
    3. 如果无性别匹配，取最近的任意角色
    4. 置信度：距离越近越高（线性衰减）
    """
    if not known:
        return []

    text = _strip_changes(text)
    char_mentions = _find_character_mentions(text, known)
    pron_mentions = _find_pronoun_mentions(text)

    if not char_mentions or not pron_mentions:
        return []

    results = []
    for pron in pron_mentions:
        # 向前找最近的角色名（先行词搜索窗口 200 字）
        candidates = []
        pron_gender = pron["gender"]
        for cm in char_mentions:
            if cm["end"] <= pron["start"]:
                distance = pron["start"] - cm["end"]
                if distance > 200:
                    continue
                # 性别匹配强度：精确匹配(2) > 未知兼容(1) > 冲突(0)
                if pron_gender is not None and cm["gender"] is not None:
                    gender_score = 2 if pron_gender == cm["gender"] else 0
                else:
                    gender_score = 1  # 一方未知·兼容
                candidates.append({
                    "name": cm["name"],
                    "distance": distance,
                    "gender_score": gender_score,
                })

        if not candidates:
            continue

        # 排序：性别匹配强度降序 → 距离近优先
        candidates.sort(key=lambda c: (-c["gender_score"], c["distance"]))
        best = candidates[0]

        # 置信度：距离越近越高（200 字内线性衰减，性别精确匹配 +0.1 加成）
        base_conf = max(0.3, 1.0 - best["distance"] / 200)
        if best["gender_score"] == 2:
            base_conf = min(1.0, base_conf + 0.1)
        # 多候选 → 降低置信度
        if len(candidates) > 1 and candidates[1]["distance"] - best["distance"] < 20:
            base_conf *= 0.7  # 两个候选距离很近 → 模糊

        results.append({
            "mention": pron["mention"],
            "span": [pron["start"], pron["end"]],
            "resolved_to": best["name"],
            "confidence": round(base_conf, 2),
            "backend": "rule",
            "ambiguous": len([c for c in candidates if c["distance"] < 50]) > 1,
        })

    return results


# ── 主函数 ────────────────────────────────────────────────

def resolve_coreferences(
    text: str,
    known_characters: list[str] | None = None,
) -> list[dict]:
    """共指消解：将代词解析到具体角色。

    返回: [{"mention": "他", "span": [45, 46], "resolved_to": "张三",
            "confidence": 0.8, "backend": "rule", "ambiguous": bool}, ...]

    门控关闭 / 任何失败 → 返回 []·不崩。
    """
    if not enabled():
        return []

    try:
        return _resolve_rule(text, known_characters)
    except Exception as e:  # noqa: BLE001 — 绝不崩主流水线
        print(f"[nn_coref_bridge] 异常·返回空列表："
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return []


# ── CLI ────────────────────────────────────────────────────

def main():
    """CLI 自测：python nn_coref_bridge.py "文本" [--characters 张三,李四]"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="共指消解桥（rule 后端）")
    ap.add_argument("text", nargs="?",
                    default="张三走到窗前。他叹了口气。李四看着她，那个人似乎很疲惫。",
                    help="输入文本")
    ap.add_argument("--characters", default=None,
                    help="已知角色（逗号分隔）")
    args = ap.parse_args()

    known = args.characters.split(",") if args.characters else None
    print(f"enabled={enabled()}  backend=rule")
    results = resolve_coreferences(args.text, known)
    for r in results:
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
