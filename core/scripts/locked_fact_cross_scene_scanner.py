#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""locked_fact_cross_scene_scanner.py — 锁定事实跨场景引用一致性检测

v2 cluster 化方案 Phase 3（2026-05-28）·
检测 人物卡.locked_facts 中的事实在 cluster 不同场景的引用是否一致：
  · 数值类（年龄/时长/年份等**恒定量**）：所有场景引用必须一致
  · 描述类（外貌/出身）：cluster 内不能出现矛盾陈述

输出 issue code: LOCKED_FACT_CROSS_SCENE_CONFLICT (hard_gate)

────────────────────────────────────────────────────────────────────────
2026-06-16 盲区落地（consistency_19_subtypes · B 件 · ConStory 时间线&因果一致性）：
把「年龄专用」泛化为「**恒定数值类锁定事实**通用对账」——纯确定性、零新依赖、必真阳的部分。
覆盖 ConStory「绝对时间矛盾（Absolute Time Contradiction）」的**确定性子集**：
  fact 含「N岁 / 第N天 / N年(寿命/恒定纪年) …」且正文同角色**同句**出现冲突绝对值 → 报。

🔴 北极星铁律 —— 单位集只收「恒定量（invariant）」，**绝不收单调递增的修真品级**（品/阶/层/级/段/重）：
   角色从「斗之气三段」练到「九段」是合法成长，不是穿帮；对其做 M≠N 判定会制造**假 hard_gate**
   （test_plan 金标准核心反例）。境界/品级的「同一参照系顺序矛盾」需要语义推理（FlawedFictions 实证
   连 o1 都做不好），交给 A 件 LLM 判官（av_judge timeline_causality_consistency），**确定性层不碰**。
   确定性层只抓「白纸黑字同一恒定字段两个值打架」。

单位集来源（作者档/项目第一权威 · 北极星②）：
  1. 项目可选覆盖 `_数据库/locked_fact_units.json` 的 `invariant_units: [...]`（opt-in·世界观若真有恒定
     纪年单位可在此声明）——实地核查 8 本项目的 世界观.json **均无结构化等级体系字段**（只有
     era/location/rules/factions/entries），故不臆造「从世界观读等级」的不存在通路。
  2. 缺该文件 → 退保底恒定单位集 `_DEFAULT_INVARIANT_UNITS`（仅「岁」·与历史行为完全兼容）。

跨场景的时间**推算**（第3天+5天=第8天对不对）不在确定性层——交给 A 件判官（语义）。
────────────────────────────────────────────────────────────────────────

输出 code（沿用既有·不新增 hard_gate code·不动 audit_hub.HARD_GATE_CODES / STRUCTURE.md §11）：
  LOCKED_FACT_CROSS_SCENE_CONFLICT (hard_gate)

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

# ── 恒定数值单位集（北极星②作者/项目第一权威 · 北极星铁律：只收 invariant，绝不收单调递增品级）──
# 「岁」= 历史唯一单位（保底·与 2026-05-28 起的行为完全兼容 → 老 case 回归不破）。
# 项目可在 _数据库/locked_fact_units.json 里 opt-in 扩展恒定单位（如世界观确有恒定纪年单位「天/日/年」）。
_DEFAULT_INVARIANT_UNITS = ("岁",)

# 单调递增品级黑名单：即便项目 opt-in 误填，也强制剔除（永不对成长性数值报 hard_gate）。
# 🔴 故意拦下 品/阶/层/级/段/重/境/星… —— 这些是单调递增的修真境界，
#    角色升阶是合法成长（三段→九段），做 M≠N 会制造假 hard_gate（金标准核心反例）。
_MONOTONIC_BLOCKLIST = frozenset({
    "品", "阶", "层", "级", "段", "重", "境", "星", "纹", "环", "转",
})


def _make_unit_re(units) -> re.Pattern:
    """构造「数字（阿拉伯 或 纯中文·互斥）+ 单位」正则。

    互斥分支（不写成 [\\d中文]+）—— 否则「张三52岁」会贪婪吃进名字里的「三」匹配出「三52」，
    _cn_to_int 解析失败 → 整条校验被跳过 → 真矛盾漏报（hard_gate 真阳性丢失，最坏）。
    单位用 re.escape 防元字符（虽已校验为 CJK，仍稳妥）。units 空 → 退保底「岁」。"""
    unit_alt = "|".join(re.escape(u) for u in units) if units else "岁"
    return re.compile(r"(\d+|[零一二三四五六七八九十百]+)\s*(" + unit_alt + r")")


def _load_unit_set(project_root: Path) -> list:
    """读单位集：项目 opt-in 覆盖优先，缺则退保底 `_DEFAULT_INVARIANT_UNITS`。北极星②第一权威。

    `_数据库/locked_fact_units.json` 形态：{"invariant_units": ["岁", "天", ...]}。
    校验：单位必须是 1-3 个 CJK 字（防注入正则元字符）·非空·剔除单调递增品级 → 退保底。
    🔴 即便项目误写单调递增品级（品/阶/层…），也由 `_MONOTONIC_BLOCKLIST` 兜底剔除——
       确定性层永不对成长性数值报 hard_gate（北极星③不干涉创作 + 金标准防矫枉过正）。"""
    cfg = load(project_root / "_数据库" / "locked_fact_units.json")
    units = []
    if isinstance(cfg, dict):
        raw = cfg.get("invariant_units")
        if isinstance(raw, list):
            for u in raw:
                if isinstance(u, str):
                    u = u.strip()
                    if 1 <= len(u) <= 3 and all("一" <= c <= "鿿" for c in u) \
                            and u not in _MONOTONIC_BLOCKLIST:
                        units.append(u)
    # 始终包含保底单位（岁）·去重保序
    merged = list(_DEFAULT_INVARIANT_UNITS) + units
    seen = set()
    out = []
    for u in merged:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# 默认（保底·岁）正则——保留模块级常量供存量测试 / 调用 `_AGE_RE` 引用（向后兼容·单 group 旧形态）。
_AGE_RE = re.compile(r"(\d+|[零一二三四五六七八九十百]+)\s*岁")
# 双 group（数字 + 单位）的「岁」正则——供 extract_numeric_facts_near 用（它取 group(2) 单位）。
_AGE_UNIT_RE = _make_unit_re(["岁"])


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


_SENT_SEP = "。！？；\n"


def extract_numeric_facts_near(text: str, keyword: str, unit_re: re.Pattern,
                               window: int = 50) -> list:
    """找 keyword 同句、且**紧邻恒定单位**的数值实例（如「三十八岁」「第三天」）。
    返回 [(数字字符串绝对起始位置, 数字字符串, 单位)]。

    2026-05-30 修假阳性：只认「数字+单位」实例，从源头杜绝距离/数量/年份串味。
    同句锚定（_SENT_SEP 切小句）：杜绝相邻句里**另一个角色**的数值被误归到本角色。
    2026-06-16 泛化：unit 从硬编码「岁」扩成可配置恒定单位集（unit_re 由 _make_unit_re 给）。"""
    results = []
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
        for um in unit_re.finditer(ctx):
            if um.start(1) < seg_start or um.start(1) >= seg_end:
                continue  # 数值不在 keyword 同句 → 大概率是别人的，跳过
            # group(1)=数字部分；group(2)=单位；记录数字在全文的绝对起始位置
            results.append((s + um.start(1), um.group(1), um.group(2)))
    return results


# 向后兼容别名：存量测试 / audit_hub 可能引用 extract_ages_near（保底「岁」单位）。
def extract_ages_near(text: str, keyword: str, window: int = 50) -> list:
    """历史接口（仅「岁」）——返回 [(pos, 数字)]，丢弃单位维度（向后兼容存量调用/测试）。"""
    return [(pos, num) for pos, num, _u in
            extract_numeric_facts_near(text, keyword, _AGE_UNIT_RE, window=window)]


def scan(project_root: Path, draft_path: Path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")

    cards = load(project_root / "_数据库" / "人物卡.json").get("characters", [])
    units = _load_unit_set(project_root)          # 北极星②第一权威单位集
    unit_re = _make_unit_re(units)

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
            # 恒定数值一致性：仅当 fact 显式声明「N<恒定单位>」时启用（如「N岁」「第N天」）。
            # 正文中只比对**同单位真值**（数字紧邻该单位），M ≠ N → 冲突。
            # 距离（三十里）/数量（三十个）/年份等无关数字不参与，杜绝 hard_gate 假阳性。
            # 北极星⑥ 对齐 context 侧锚定：name 以中文数字结尾(张三/周七)时，
            # 直接对整条 fact 跑贪婪 [零一二...百]+ 会把名字尾字吃进数字
            # （张三三十八岁→'三三十八'→None 静默跳过 / 周七十八岁→78 错值）。
            # 先剥掉 name 前缀再抽，杜绝 fact 侧名字尾字串味。
            fact_body = fact[len(name):] if fact.startswith(name) else fact
            # fact 可能含多个恒定数值（少见，但稳妥支持）→ 逐单位独立比对，单位必须相同才算矛盾。
            matched = False
            for fact_m in unit_re.finditer(fact_body):
                fact_unit = fact_m.group(2)
                fact_val = _cn_to_int(fact_m.group(1))
                if fact_val is None:
                    continue
                ctx_nums = extract_numeric_facts_near(text, name, unit_re, window=50)
                for pos, ctx_num, ctx_unit in ctx_nums:
                    if ctx_unit != fact_unit:
                        continue  # 单位不同（岁 vs 天）→ 不可比，跳过
                    ctx_val = _cn_to_int(ctx_num)
                    if ctx_val is not None and ctx_val != fact_val:
                        conflicts.append({
                            "character": name,
                            "fact": fact,
                            "unit": fact_unit,
                            "conflict_value": f"{ctx_num}{ctx_unit}",
                            "position": pos,
                            "preview": text[max(0, pos - 30):pos + 30],
                        })
                        matched = True
                        break
                if matched:
                    break  # 该 fact 已找到一处矛盾，不重复报同一 fact

    return {
        "schema_version": "1.1",
        "scanner": "locked_fact_cross_scene_scanner",
        "cluster_mode": True,
        "gate_level": "hard_gate" if conflicts else "advisory",
        "facts_checked": checked_count,
        "invariant_units": units,
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
