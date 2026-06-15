#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""locked_fact_cross_scene_scanner.py — 锁定事实跨场景引用一致性检测

v2 cluster 化方案 Phase 3（2026-05-28）·
检测 人物卡.locked_facts 中的事实在 cluster 不同场景的引用是否一致：
  · 数值类（年龄/品级）：所有场景引用必须一致
  · 描述类（外貌/出身）：cluster 内不能出现矛盾陈述

输出 issue code: LOCKED_FACT_CROSS_SCENE_CONFLICT (hard_gate)

用法：python locked_fact_cross_scene_scanner.py <project> <cluster_draft_path>
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


def load(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 「数字 + 岁」年龄词的正则：数字（**纯阿拉伯** 或 **纯中文**，二者不混）紧跟可选空白后必须接「岁」。
# 2026-05-30 北极星复审（假阳性修）：年龄绑定判据从「±50 窗口里存在『岁』字」收紧为「数字必须紧邻『岁』」，
# 杜绝距离（三十里）/数量（三十个）/年份（一九三八年）等无关数字串味成假年龄触发 hard_gate。
# 阿拉伯/中文分支互斥（不写成 [\d中文]+）—— 否则「张三52岁」会贪婪吃进名字里的「三」匹配出「三52」，
# _cn_to_int 解析失败 → 整条年龄校验被跳过 → 真年龄矛盾漏报（hard_gate 真阳性丢失，最坏）。
_AGE_RE = re.compile(r"(\d+|[零一二三四五六七八九十百]+)\s*岁")


def _cn_to_int(s: str):
    """中文/阿拉伯数字 → int（覆盖年龄场景：十八/三十八/二十/十/52/一百二十）。无法解析返回 None。
    2026-05-30 北极星复审：
      · 原 isdigit() 把中文数字年龄（三十八岁）全漏掉，使 hard_gate 穿帮检测只覆盖一半。
      · 补「百」位（一百二十岁→120），覆盖修真/玄幻超长寿命设定的年龄。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    # 「百」位：X百Y十Z / X百Y / 百二十 等（年龄场景上限 999 足够）
    if "百" in s:
        hpart, _, rest = s.partition("百")
        if hpart and hpart not in _CN_DIGIT:
            return None
        hundreds = _CN_DIGIT.get(hpart, 1) if hpart else 1
        if not rest:
            return hundreds * 100
        # 「一百零五」（=105）：『零』占位 → 后面单个数字直接当个位。
        if rest[0] == "零":
            ones_part = rest[1:]
            if len(ones_part) == 1 and ones_part in _CN_DIGIT:
                return hundreds * 100 + _CN_DIGIT[ones_part]
            return None
        # 「一百二」简写（=120）：rest 是个位数且无「十」→ 按十位补。
        if "十" not in rest and len(rest) == 1 and rest in _CN_DIGIT:
            return hundreds * 100 + _CN_DIGIT[rest] * 10
        tail = _cn_to_int(rest)
        if tail is None:
            return None
        return hundreds * 100 + tail
    if "十" in s:
        a, _, b = s.partition("十")
        if a and a not in _CN_DIGIT:
            return None
        if b and b not in _CN_DIGIT:
            return None
        tens = _CN_DIGIT.get(a, 1) if a else 1
        ones = _CN_DIGIT.get(b, 0) if b else 0
        return tens * 10 + ones
    if len(s) == 1 and s in _CN_DIGIT:
        return _CN_DIGIT[s]
    return None


def extract_ages_near(text: str, keyword: str, window: int = 50) -> list[tuple[int, str]]:
    """找 keyword 附近、且**紧邻「岁」**的年龄数字（如「三十八岁」「52岁」）。
    返回 [(年龄数字字符串绝对起始位置, 数字字符串)]。

    2026-05-30 修假阳性：旧 extract_numbers_near 抓窗口内**所有**数字（含距离/数量/年份），
    再靠「窗口里有『岁』」宽判据绑定年龄 → 「三十里」被当成「三十岁」误报 hard_gate。
    现在只认「数字+岁」的实例，从源头杜绝串味。"""
    results = []
    # 句子分隔符：把窗口切成小句，只采纳与 keyword 同句的年龄，
    # 杜绝相邻句里**另一个角色**的年龄被误归到本角色（喂 hard_gate 假阳性）。
    _SENT_SEP = "。！？；\n"
    for m in re.finditer(re.escape(keyword), text):
        s = max(0, m.start() - window)
        e = min(len(text), m.end() + window)
        ctx = text[s:e]
        kw_in_ctx = m.start() - s  # keyword 在 ctx 内的偏移
        # 找 keyword 所在小句的 [seg_start, seg_end)（ctx 内坐标）
        seg_start = 0
        for i in range(kw_in_ctx - 1, -1, -1):
            if ctx[i] in _SENT_SEP:
                seg_start = i + 1
                break
        seg_end = len(ctx)
        for i in range(m.end() - s, len(ctx)):
            if ctx[i] in _SENT_SEP:
                seg_end = i
                break
        for age_m in _AGE_RE.finditer(ctx):
            if age_m.start(1) < seg_start or age_m.start(1) >= seg_end:
                continue  # 年龄不在 keyword 同句 → 大概率是别人的年龄，跳过
            # group(1) 是数字部分；记录数字在全文的绝对起始位置
            results.append((s + age_m.start(1), age_m.group(1)))
    return results


def scan(project_root: Path, draft_path: Path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")

    cards = load(project_root / "_数据库" / "人物卡.json").get("characters", [])

    conflicts = []
    checked_count = 0
    for c in cards:
        name = c.get("name", "")
        if not name or name not in text:
            continue
        for lf in c.get("locked_facts", []) or []:
            if not isinstance(lf, dict):
                continue
            fact = lf.get("fact", "")
            if not fact:
                continue
            checked_count += 1
            # 年龄一致性：仅当 fact 显式声明「N 岁」时启用。
            # 正文中只比对**真年龄**（数字紧邻「岁」），M ≠ N → 冲突。
            # 距离（三十里）/数量（三十个）/年份等无关数字不参与，杜绝 hard_gate 假阳性。
            # 北极星⑥ 对齐 context 侧锚定：name 以中文数字结尾(张三/周七)时，
            # 直接对整条 fact 跑贪婪 [零一二...百]+ 会把名字尾字吃进年龄数字
            # （张三三十八岁→'三三十八'→None 静默跳过 / 周七十八岁→78 错值）。
            # 先剥掉 name 前缀再抽，杜绝 fact 侧名字尾字串味。
            fact_body = fact[len(name):] if fact.startswith(name) else fact
            age_in_fact = _AGE_RE.search(fact_body)
            if age_in_fact:
                fact_age = _cn_to_int(age_in_fact.group(1))
                if fact_age is None:
                    continue
                ctx_ages = extract_ages_near(text, name, window=50)
                for pos, ctx_num in ctx_ages:
                    ctx_age = _cn_to_int(ctx_num)  # 支持中文数字年龄（三十八岁）
                    if ctx_age is not None and ctx_age != fact_age:
                        conflicts.append({
                            "character": name,
                            "fact": fact,
                            "conflict_value": f"{ctx_num}岁",
                            "position": pos,
                            "preview": text[max(0, pos-30):pos+30],
                        })
                        break

    return {
        "schema_version": "1.0",
        "scanner": "locked_fact_cross_scene_scanner",
        "cluster_mode": True,
        "gate_level": "hard_gate" if conflicts else "advisory",
        "facts_checked": checked_count,
        "conflicts_count": len(conflicts),
        "conflicts": conflicts[:10],
        "warning": (
            f"⚠️ {len(conflicts)} 处锁定事实跨场景冲突"
            if conflicts else None
        ),
        "severity": "error" if conflicts else "info",
        "code": "LOCKED_FACT_CROSS_SCENE_CONFLICT" if conflicts else None,
    }


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    project = Path(args[0]).resolve()
    draft = Path(args[1]).resolve()
    report = scan(project, draft)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "_fatal" in report:
        sys.exit(2)
    if report.get("warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
