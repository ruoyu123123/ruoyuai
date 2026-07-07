#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""locked_fact_cross_scene_scanner.py — 锁定事实跨场景引用一致性检测

v2 cluster 化方案 Phase 3（2026-05-28）·
检测 人物卡.locked_facts 中的事实在 cluster 不同场景的引用是否一致，两条通路：

  · 恒定数值通路（确定性·始终执行·历史行为零变动）：fact 显式声明「N<恒定单位>」（如 N 岁）
    时，正文同角色同句的同单位数值必须一致 → LOCKED_FACT_CROSS_SCENE_CONFLICT (hard_gate)。

  · 描述类通路（NLI 语义·门控执行·2026-07-07 落地，此前 docstring 曾承诺「描述类矛盾陈述」
    但代码未实现——ConStory 金 fixture 盲区5 实证）：**非数值描述类 fact**（生死/亲缘/出身/
    身份等明文互斥，如锁定「满门尽灭只剩一人」vs 正文「兄长推门而入」）× 正文中**与人名同句
    共现**的句子做候选配对（粗筛控制调用量），经 nn_nli_bridge（Erlangshen-110M 中文 NLI·
    Wave-3 落地·Wave-5 daemon-first）判 contradiction 高置信（≥ _NLI_CONTRA_THRESHOLD）→
    LOCKED_FACT_DESCRIPTIVE_CONTRADICTION（**advisory·永不 hard_gate**——北极星⑤：NLI 是
    概率判定，只有确定性一致性才配 hard；绝不复用/升格 hard 码）。
    执行前提（缺一即**诚实 skip**·note 写明原因·绝不用关键词匹配假冒语义判定）：
      1. LOCKED_FACT_DESCRIPTIVE_MODE ∈ {shadow(默认·只记不判·violations 恒空), active}；off 关闭
      2. nn_nli_bridge.enabled()（RUOYU_NN_NLI=1 + venv/checkpoint 齐备·默认 off）
    结果写在报告**独立字段 `descriptive`**：顶层数值通路字段（code/gate_level/conflicts/
    warning/exit code）逐字节不变，audit_hub._parse_locked_fact_cross_scene 现有解析零影响，
    描述类 violations 由主代理另行接线消费。
    🔴 真机能力边界（2026-07-07 Erlangshen-110M 实测·勿高估检出面）：
      · 直接改写型矛盾（「他已死」vs「他还活着」/数量互斥/天气互斥）contradiction 0.99+ 稳判 ✓
      · **多跳实体推理型矛盾**（「满门尽灭只剩沈昭一人」vs「兄长沈铖推门而入」——需推断
        沈铖∈家人且活着）实测 entailment(contradiction 仅 0.166) ✗——110M 模型能力边界，
        阈值 0.80 下此类恒漏（勿降阈值硬凑：0.166 档放行=误报洪水）。ConStory 盲区3 测试
        用 mock NLI 锁的是**接线契约**非真模型召回。
      · 多跳类承接方（2026-07-07 接入）：novel-reading-reflector 维度 9「锁定事实语义一致性」
        ——cluster-write step3 每 cluster 必跑的 Claude 系 judge 读 locked_facts 做多跳核查，
        issue 驱动修复轮（advisory 待裁决项·刻意伏笔可豁免）。三层互补：数值确定性=scanner
        hard / 直接改写型=NLI advisory / 多跳推理型=reflector 维度 9。

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

输出 code（不动 audit_hub.HARD_GATE_CODES / STRUCTURE.md §11 的 19 码清单）：
  LOCKED_FACT_CROSS_SCENE_CONFLICT (hard_gate·恒定数值通路·报告顶层)
  LOCKED_FACT_DESCRIPTIVE_CONTRADICTION (advisory·描述类 NLI 通路·报告 `descriptive` 字段·
    绝不进 HARD_GATE_CODES)

Env 门控：LOCKED_FACT_DESCRIPTIVE_MODE = off / shadow(默认) / active；
  NLI 后端另受 RUOYU_NN_NLI=1 门控（见 nn_nli_bridge.py·daemon-first 三层降级）。
  S4 高熵段优先粗筛（2026-07-07·ConStory arXiv:2603.05890「一致性错误集中在高熵段」）：
  RUOYU_NN_SURPRISAL=1 且 nn_surprisal_bridge 全段命中时，描述类候选配对按所在段
  GPT-2 surprisal 降序重排后再截断 64 对上限（高熵段优先送 NLI）；surprisal 不可用
  （默认）→ 文档序逐字节不变。留痕 descriptive.pair_selection。

用法：python locked_fact_cross_scene_scanner.py <project> <cluster_draft_path>
"""
from __future__ import annotations
import json
import os
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


# ══════════════════ 描述类通路（NLI 语义·advisory·2026-07-07）══════════════════
# 🔴 纪律：本通路 code 永远 advisory（NLI 概率判定 · 北极星⑤只有确定性一致性才 hard）；
#         NLI 后端不可用 → 诚实 skip（note 说明），绝不用关键词匹配假冒语义判定。
#
# S4 高熵段优先粗筛（2026-07-07 二轮移植·ConStory-Checker arXiv:2603.05890 实证
# 「一致性错误集中在 token 熵高的文本段」）：
#   · surprisal 可用（nn_surprisal_bridge.enabled()=RUOYU_NN_SURPRISAL=1 + venv/checkpoint 齐备·
#     桥内部 daemon-first ~0.1s / 回退 subprocess）且候选句所在段**全部**拿到 mean_surprisal
#     → 候选配对按所在段 surprisal 降序重排后再截断 _MAX_NLI_PAIRS（高熵段优先送 NLI）。
#   · 任一条件不满足（默认环境即此态）→ 保持既有文档序**逐字节不变**（诚实降级不伪装）。
#   报告 `descriptive.pair_selection` 留痕："surprisal_ranked" | "document_order"。
#   零新模型·纯接线：只改「64 对上限内选哪些」的优先级，阈值/上限/判定逻辑零变动。

DESCRIPTIVE_CODE = "LOCKED_FACT_DESCRIPTIVE_CONTRADICTION"
_NLI_CONTRA_THRESHOLD = 0.80     # contradiction 概率高置信地板（低于此不报·宁漏勿误）
_MAX_NLI_PAIRS = 64              # 单次 scan 送 NLI 的配对总量上限（控制调用量）
_MAX_SENTS_PER_FACT = 16         # 单条 fact 最多配对的句子数
_MIN_SENT_CJK = 6                # 过短句子不送 NLI（无判定价值）


def _descriptive_mode() -> str:
    """LOCKED_FACT_DESCRIPTIVE_MODE：off / shadow(默认·只记不判) / active。非法值退 shadow。"""
    m = (os.environ.get("LOCKED_FACT_DESCRIPTIVE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _nli_bridge():
    """惰性取 nn_nli_bridge 模块（同目录·复用不重造）。import 失败 → None（诚实 skip）。"""
    try:
        import nn_nli_bridge
        return nn_nli_bridge
    except Exception:
        return None


def _split_sentences(text: str) -> list:
    """按句末符（_SENT_SEP）切句，返回 [(绝对起始位置, 句子)]。空段丢弃。"""
    out = []
    start = 0
    for i, ch in enumerate(text):
        if ch in _SENT_SEP:
            seg = text[start:i].strip()
            if seg:
                out.append((start, seg))
            start = i + 1
    seg = text[start:].strip()
    if seg:
        out.append((start, seg))
    return out


def _surprisal_bridge():
    """惰性取 nn_surprisal_bridge 模块（同目录·复用不重造）。import 失败 → None（诚实降级）。"""
    try:
        import nn_surprisal_bridge
        return nn_surprisal_bridge
    except Exception:
        return None


def _line_paragraph_spans(text: str) -> list:
    """非空行 = 段（网文一行一段惯例·_SENT_SEP 含 \\n 故句子绝不跨行）。
    返回 [(start, end)] 绝对区间（升序）。"""
    spans = []
    pos = 0
    for line in text.split("\n"):
        if line.strip():
            spans.append((pos, pos + len(line)))
        pos += len(line) + 1
    return spans


def _surprisal_rank_candidates(text: str, candidates: list) -> "tuple[list, bool]":
    """S4（ConStory arXiv:2603.05890）：候选配对按所在段 GPT-2 surprisal 降序重排
    （高熵段优先送 NLI），返回 (排序后候选, True)。

    🔴 诚实降级铁律：以下任一情况 → 返回 (原候选列表**原对象·零改动**, False)，
    调用方保持文档序逐字节不变（不伪装成 surprisal_ranked）：
      · nn_surprisal_bridge import 失败 / enabled()=False（RUOYU_NN_SURPRISAL 默认 off）
      · 候选定位不到所在段 / 任一所在段未拿到 mean_surprisal（对齐
        entropy_hotspot_consistency_probe「任一 block 未命中 → 整体回退」纪律）
    排序稳定（同段/同分保持文档序）。只重排不增删——上限/阈值零变动。"""
    if not candidates:
        return candidates, False
    bridge = _surprisal_bridge()
    try:
        if bridge is None or not bridge.enabled():
            return candidates, False
    except Exception:
        return candidates, False
    spans = _line_paragraph_spans(text)
    if not spans:
        return candidates, False

    def _span_idx(pos: int):
        for i, (s, e) in enumerate(spans):
            if s <= pos < e:
                return i
        return None

    idxs = [_span_idx(c["position"]) for c in candidates]
    if any(i is None for i in idxs):
        return candidates, False
    needed = sorted({i for i in idxs})
    texts = [text[spans[i][0]:spans[i][1]].strip() for i in needed]
    try:
        results = bridge.predict_batch(texts, ids=[f"lf_seg_{i:04d}" for i in needed])
    except Exception:
        return candidates, False
    if not isinstance(results, list) or len(results) != len(texts):
        return candidates, False
    scores = {}
    for i, r in zip(needed, results):
        if not isinstance(r, dict) or r.get("mean_surprisal") is None:
            return candidates, False   # 任一段未命中 → 整体诚实回退（不产半吊子排序）
        scores[i] = float(r["mean_surprisal"])
    order = sorted(range(len(candidates)), key=lambda k: -scores[idxs[k]])  # 稳定·降序
    return [candidates[k] for k in order], True


def _scan_descriptive(text: str, desc_facts: list) -> dict:
    """描述类锁定事实 × 人名共现句 → NLI contradiction 高置信 → advisory violation。

    desc_facts: [(name, fact)]（scan() 分流出的非数值描述类锁定事实）。
    返回独立 `descriptive` 报告块——不触碰顶层数值通路任何字段。
    shadow 模式：命中只进 shadow_observations，violations 恒空（只记不判）。
    """
    mode = _descriptive_mode()
    block = {
        "mode": mode,
        "code": DESCRIPTIVE_CODE,
        "gate_level": "advisory",          # 🔴 永不 hard_gate（北极星⑤·概率判定）
        "nli_available": False,
        "executed": False,
        "note": None,
        "facts_checked": len(desc_facts),
        "pairs_sent": 0,
        "nli_threshold": _NLI_CONTRA_THRESHOLD,
        # S4 留痕：候选配对选择策略（surprisal_ranked=高熵段优先 / document_order=文档序）
        "pair_selection": "document_order",
        "violations": [],
        "shadow_observations": [],
    }
    if mode == "off":
        block["note"] = "描述类通路关闭（LOCKED_FACT_DESCRIPTIVE_MODE=off）"
        return block
    if not desc_facts:
        block["note"] = "无描述类锁定事实（人物卡为空或锁定事实全为恒定数值类）"
        return block
    bridge = _nli_bridge()
    if bridge is None or not bridge.enabled():
        block["note"] = ("NLI 后端未启用·描述类通路未执行"
                         "（需 RUOYU_NN_NLI=1 且 venv/checkpoint 齐备·见 nn_nli_bridge.py；"
                         "绝不用关键词匹配假冒语义判定）")
        return block
    block["nli_available"] = True

    # 候选配对粗筛：只有 fact 所属角色名与句子共现才成为候选（控制调用量）。
    # premise = 锁定事实（权威陈述），hypothesis = 正文句 → contradiction = 正文违背锁定事实。
    # 先全量收集（fact 主序 + 文档序），再决定截断顺序：
    #   S4：surprisal 可用 → 按所在段 surprisal 降序（高熵段优先·ConStory arXiv:2603.05890）；
    #       不可用 → 保持本收集序（与历史嵌套循环截断结果逐字节一致·诚实降级）。
    candidates = []
    sentences = _split_sentences(text)
    for fact_i, (name, fact) in enumerate(desc_facts):
        for pos, sent in sentences:
            if name not in sent or len(sent) < _MIN_SENT_CJK:
                continue
            # _fact_i = 内部截断记账键（按 desc_facts 条目而非值去重·防同值 fact 串账），
            # 落 meta/报告前剥除。
            candidates.append({"character": name, "fact": fact,
                               "sentence": sent, "position": pos, "_fact_i": fact_i})
    ranked, surprisal_used = _surprisal_rank_candidates(text, candidates)
    block["pair_selection"] = "surprisal_ranked" if surprisal_used else "document_order"

    # 截断：全局上限 _MAX_NLI_PAIRS + 单 fact 上限 _MAX_SENTS_PER_FACT（阈值零变动）。
    # document_order 时 candidates 按 fact 分组连续，本循环与历史嵌套循环选出的
    # pairs/meta 逐字节相同；surprisal_ranked 时同两上限按高熵优先序生效。
    pairs, meta = [], []
    per_fact_count = {}
    for c in ranked:
        if len(pairs) >= _MAX_NLI_PAIRS:
            break
        fkey = c["_fact_i"]
        if per_fact_count.get(fkey, 0) >= _MAX_SENTS_PER_FACT:
            continue
        per_fact_count[fkey] = per_fact_count.get(fkey, 0) + 1
        pairs.append({"premise": c["fact"], "hypothesis": c["sentence"]})
        meta.append({"character": c["character"], "fact": c["fact"],
                     "sentence": c["sentence"], "position": c["position"]})
    block["pairs_sent"] = len(pairs)
    if not pairs:
        block["executed"] = True
        block["note"] = "粗筛后无候选配对（角色名与正文句子无共现）"
        return block

    results = bridge.predict_batch(pairs)
    hits = []
    got_any = False
    for res, m in zip(results, meta):
        if res is None:          # 单条不可用 → 跳过（桥契约：None ≠ 判定）
            continue
        got_any = True
        prob = float((res.get("probs") or {}).get("contradiction", 0.0))
        if res.get("label") == "contradiction" and prob >= _NLI_CONTRA_THRESHOLD:
            hits.append({**m, "contradiction_prob": round(prob, 4)})
    if not got_any:
        # enabled() 过了但推理全失败（daemon/subprocess 均挂）→ 仍是诚实 skip，不产半吊子判定
        block["note"] = "NLI 推理未产出判定（daemon/subprocess 均失败）·描述类通路未执行"
        return block

    block["executed"] = True
    if mode == "shadow":
        block["shadow_observations"] = hits[:10]
        block["note"] = ("shadow 模式·只记不判（violations 恒空）"
                         + (f"·观察到 {len(hits)} 处疑似矛盾" if hits else ""))
    else:  # active
        block["violations"] = hits[:10]
        if hits:
            block["note"] = (f"⚠️ {len(hits)} 处描述类锁定事实疑似矛盾"
                             f"（NLI contradiction ≥ {_NLI_CONTRA_THRESHOLD}·advisory 可豁免）")
    return block


def scan(project_root: Path, draft_path: Path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")

    cards = load(project_root / "_数据库" / "人物卡.json").get("characters", [])
    units = _load_unit_set(project_root)          # 北极星②第一权威单位集
    unit_re = _make_unit_re(units)

    conflicts = []
    checked_count = 0
    descriptive_facts = []   # (name, fact)·非数值描述类 → 描述类 NLI 通路（advisory·门控）
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
            numeric_engaged = False   # fact 是否进入了恒定数值通路（含可解析「N<单位>」）
            for fact_m in unit_re.finditer(fact_body):
                fact_unit = fact_m.group(2)
                fact_val = _cn_to_int(fact_m.group(1))
                if fact_val is None:
                    continue
                numeric_engaged = True
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
            if not numeric_engaged:
                # 非数值描述类（无可解析「N<恒定单位>」）→ 分流描述类 NLI 通路
                descriptive_facts.append((name, fact))

    return {
        "schema_version": "1.2",
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
        # 描述类 NLI 通路（advisory·独立字段·不进顶层 code/warning/exit code——
        # audit_hub._parse_locked_fact_cross_scene 只读顶层，主代理另行接线消费）
        "descriptive": _scan_descriptive(text, descriptive_facts),
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
