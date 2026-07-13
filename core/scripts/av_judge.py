#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""av_judge.py — AV-judge 解耦特质·作者验证确定性层（配对判别 rubric 单一真理源 · advisory）

【是什么】Author-Verification 判别的**纯确定性库**：给 1 段**作者原文** + 1 段**仿写**，
  渲染**逐维度配对判别** prompt——把 A（作者）当锚、B（仿写）当待验，逐维点名「B 在哪个维度露馅
  （走味，读起来不像作者）」。4 个解耦特质维度（authorship verification 文献的
  stylometric 拆解 · 不混在一起打一个总分）：

    1. 词汇选择（lexical choice）—— 用词层（具体名词偏好 / 动词色彩 / 书面 vs 口语）
    2. 句法（syntax）—— 句长节奏 / 长短句交错 / 单句独行 / 流水句 vs 复句
    3. 话语连接词（discourse connectives）—— 衔接套路（转/承/因果连接词偏好 + AI 套话过渡）
    4. 语用语气（pragmatic tone）—— 叙事口吻 / 距离感 / 反讽 / 留白 vs 说破

【为什么填空档】治「SFS 数值过了但读着不像」：
  · SFS（style_evaluator.py）是**统计指纹**——句长/段长/虚词分布对齐了，但隐性的「读者一眼
    认不认得出是这个作者」抓不住（统计同分布 ≠ 读者感知同作者）。
  · 黑箱 LLM-judge（judge_consensus.py）给**整体分**——分高分低不解释「哪个维度露馅」，
    无法定位回改。
  AV-judge 站中间：**读者视角** + **解耦 4 维** + **配对判别**（A 锚 B 验，比绝对打分可靠——
  Catch Me If You GAN / Are We There Yet 等实证：LLM 对单段绝对风格打分方差大，配对相对判别稳）。

【执行分工（scene_jobs 范式 · 本模块零模型调用）】
  判别由 novel-av-judge agent 亲笔完成，本模块只做确定性层：
  · distill_av_verify.py 用 build_av_judge_prompt 渲染投票任务 prompt（每配对 N 票 ·
    env AV_JUDGE_N_SAMPLES 默认 3 · AV_JUDGE_POSITION_SWAP=on 时半数任务换序呈现），
    写 av_judge_jobs.json manifest 供主代理 spawn novel-av-judge 补件；
  · novel-av-judge 逐票**独立**判别，verdict JSON 落盘；
  · 本模块 parse_av_verdicts 逐票解析 → aggregate_verdicts 4 维**各取多数票** robust 聚合
    （平票偏命中 · 方差透明 agreement / unstable_dims）→ build_report 组装 advisory 报告。

【配对判别 > 绝对打分】(authorship verification 范式)
  不问「B 像不像作者（打 1-10）」，问「给定 A 是作者真迹，B 在哪几维露馅」——
  相对锚定把「这个作者基线长什么样」交给样本 A 决定，绕开 LLM-judge 对网文隐性风格的绝对标尺漂移。

【绝不 hard_gate】(北极星⑤ + 共同纪律 2)
  实证（Catch Me If You GAN / Are We There Yet On Detecting LLM Texts）：LLM-judge 对网文
  隐性风格会**失准**——创意写作域约 1/4 难例判别翻转。故永远 advisory：
  code AV_TRAIT_DRIFT **绝不进 audit_hub.HARD_GATE_CODES**，走味维度只作 advisory
  待裁决项上报（仍可豁免），结论不改变 SFS 收敛闸或任何 hard_gate。

`/distill-style` 和 `/distill-style-skillopt` 的复刻验证步骤经 distill_av_verify.py
required 消费本模块；测试只验确定性层（rubric / prompt 构造 / 解析 / 聚合 / 报告）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# advisory 专用 issue code · ⚠️ 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤ · 共同纪律 2）
ISSUE_CODE = "AV_TRAIT_DRIFT"

# ── 自一致性投票（self-consistency · 稳单次判别方差）─────────────────
# 根因（arxiv 实证）：LLM-judge 对网文隐性风格会失准（创意写作域约 1/4 难例翻转），单次配对
#   判别方差大，同一对 (A,B) 判两次可能一次「走味」一次「命中」。治法 = self-consistency：
#   同一配对渲染 N 个独立投票任务（novel-av-judge 逐票独立判别 · 不许互相参考），
#   4 维**各取多数票**做 robust 聚合，把单次噪声平滑掉。
# env AV_JUDGE_N_SAMPLES：默认 3（质量优先 · N≥2 真聚合生效）· 设 1 = 关（退回单票 ·
#   关聚合逃生口）· 钳到 [1, AV_JUDGE_N_SAMPLES_MAX]（防投票任务数失控）。
# ⚠️ 仍 advisory：聚合只稳方差、不强判——多数票 + 方差透明上报，仍可豁免、永不 hard_gate。
AV_JUDGE_N_SAMPLES_DEFAULT = 3
AV_JUDGE_N_SAMPLES_MAX = 7

# 走味判定阈值：维度判别 verdict ∈ {命中, 走味}；命中 = 读者认得出是作者，走味 = 露馅。
# 这是**读者视角的定性判别**（不是 1-10 打分），阈值即「这一维 LLM 判定走味」。
DRIFT_VERDICT = "走味"
MATCH_VERDICT = "命中"


# ════════════════════════════════════════════════════════════════
# 4 维解耦特质 rubric（authorship verification stylometric 拆解 · 单一来源）
# ════════════════════════════════════════════════════════════════
# 每维：(维度名, 读者视角说明, 配对判别问法——给定 A 是作者真迹，B 在该维是否露馅)
AV_TRAIT_DIMS = [
    (
        "词汇选择",
        "用词层：具体名词偏好、动词色彩、书面 vs 口语、专有词汇密度",
        "仿写段的用词读起来像不像作者真迹？具体名词/动词的选择是否露出非作者的痕迹（如更书面、更泛化、更 AI）？",
    ),
    (
        "句法",
        "句子层：句长节奏、长短句交错、单句独行占比、流水句 vs 复句结构",
        "仿写段的句子骨架像不像作者真迹？句长节奏 / 长短交错 / 单句成段的习惯是否和作者错位？",
    ),
    (
        "话语连接词",
        "衔接层：段落/句间过渡套路、转承因果连接词偏好、是否冒出 AI 套话过渡",
        "仿写段在衔接上像不像作者真迹？是否出现「与此同时/然而/值得一提的是」等作者不用的 AI 过渡，或丢了作者的衔接习惯？",
    ),
    (
        "语用语气",
        "语用层：叙事口吻、与读者的距离感、反讽/克制/留白 vs 说破的倾向",
        "仿写段的口吻像不像作者真迹？叙事距离、反讽与留白、是否把情绪说破——这些语用习惯有没有走味？",
    ),
]

# ── intent_recovery 扩展维（experiment · 默认不进 AV_TRAIT_DIMS 主列表）──────────
# 「作者思维」维（决策骨）= 反推作者在岔路口的取舍倾向，**不是表层文体**。仅在
#   build_av_judge_prompt(include_intent_dim=True) 时追加渲染（默认 False · 零回归 · 不污染纯文体 4 维）。
# 🔴 grounding 切断复述捷径：ask 只给「不含作者档 rationale 原文的中性维度定义」，
#   绝不出现「母题/胜利代价藏悲凉」等已聚合的 author_decision_principles 文案——让 judge 自己反推，
#   防它照抄作者档原文造成虚假高余弦。判决权**不在此维 verdict**（它仅出 advisory 文本）——真判决
#   交确定性 mstyle 余弦（replication_fidelity_check.intent_recovery_cosine·embedding_store）。
# ⚠️ 弱模型对「决策倾向」抽象维执行力弱 → 默认关·仅 experiment 开。
INTENT_DIM = (
    "作者思维",
    "决策层：作者在岔路口的取舍倾向——代价/奖惩怎么排、贴近还是拉远叙事距离、说破还是留白",
    "仿写段在「遇到价值取舍 / 情绪处理 / 信息释放」时的决策走向，像不像作者真迹这位作者的思维习惯？（只看决策倾向不看辞藻）",
)


def _active_dims(include_intent_dim: bool = False) -> list:
    """当前渲染的维度列表：默认 4 维文体维；include_intent_dim=True 追加「作者思维」第 5 维。

    parse_av_verdicts 永远只认 AV_TRAIT_DIMS 的 4 维（INTENT_DIM 仅出 advisory 文本不参与聚合判决），
    故此函数只服务 prompt 渲染 + 输出 schema 列举，不改下游聚合（零回归）。
    """
    return AV_TRAIT_DIMS + ([INTENT_DIM] if include_intent_dim else [])


def _n_samples() -> int:
    """读 env AV_JUDGE_N_SAMPLES：默认 3（N≥2 真聚合稳方差）· 1=关（退回单票）· 钳到 [1, MAX]。

    空 / 非法值 → 默认 3。< 1 钳到 1（=关闭聚合，退回单票）；> MAX 钳到 MAX。
    值 = distill_av_verify 为同一配对渲染的投票任务数（novel-av-judge 逐票独立判别）。
    """
    raw = (os.environ.get("AV_JUDGE_N_SAMPLES") or "").strip()
    if not raw:
        return AV_JUDGE_N_SAMPLES_DEFAULT
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return AV_JUDGE_N_SAMPLES_DEFAULT
    if n < 1:
        return 1
    return min(n, AV_JUDGE_N_SAMPLES_MAX)


def _position_swap_on() -> bool:
    """读 env AV_JUDGE_POSITION_SWAP：默认 **off**（G2-CYCLIC 去位置偏 · experiment）。

    position-swap 去偏的**有效性需离线对称性闸验证**（本机验证不了），故默认 off →
      全部投票任务原向呈现。只有显式 AV_JUDGE_POSITION_SWAP=on（experiment）才在
      N 个投票任务里给后一半分配 swap（_swap_assignment）。N=1 时即便 on 也退化为 0 个 swap。

    认 on / 1 / true / yes（大小写不敏感）为开；其余（含空 / 非法）为 off。
    """
    v = (os.environ.get("AV_JUDGE_POSITION_SWAP") or "").strip().lower()
    return v in ("on", "1", "true", "yes")


def _intent_dim_on() -> bool:
    """读 env AV_JUDGE_INTENT_DIM：默认 **off**（intent_recovery 扩展维 · experiment · 与现有 4 维正交）。

    on 时 build_av_judge_prompt 追加「作者思维」第 5 维（INTENT_DIM）——该维仅出 advisory 文本，
    **不进 parse_av_verdicts 聚合**（drift_dims 仍只算 4 维），真判决交 mstyle 余弦
    （replication_fidelity_check.intent_recovery_cosine）。默认 off → 现有 4 维行为零回归。

    认 on / 1 / true / yes（大小写不敏感）为开；其余（含空 / 非法）为 off。
    """
    v = (os.environ.get("AV_JUDGE_INTENT_DIM") or "").strip().lower()
    return v in ("on", "1", "true", "yes")


def _swap_assignment(n: int, swap_on: bool) -> list[bool]:
    """为 n 个投票任务分配 swap 方向（前一半 False · 后一半 True · 最大化位置对称采样）。

    · swap_on=False → 全 False（零回归 · 原向）。
    · swap_on=True 且 n≥2 → 前 ceil(n/2) 个 False、后 floor(n/2) 个 True
      （N=2→[F,T]·N=3→[F,F,T]·N=4→[F,F,T,T]）。
    · n=1 → 恒 [False]（单样本无从对称 · 零回归 · 即便 swap_on）。
    确定性纯函数 → 测试可断言分配序列。
    """
    n = max(1, n)
    if not swap_on or n == 1:
        return [False] * n
    half = (n + 1) // 2  # 前一半（含取整偏前）不 swap
    return [i >= half for i in range(n)]


# ════════════════════════════════════════════════════════════════
# Prompt 构造（确定性可测 · 配对判别 + 4 维解耦 + 读者视角）
# ════════════════════════════════════════════════════════════════

AV_JUDGE_SYSTEM_PROMPT = """你是一位资深网文读者兼文本鉴定师，专做「作者验证」（authorship verification）。

给你两段文本：一段是**某位作者的真迹**（锚 · 已确认出自该作者），另一段是一段**仿写**（待验证）。
每段的身份（作者真迹 / 仿写）会在正文里明确标注。你的任务**不是**给仿写段打一个总分，而是
**逐维度做配对判别**——以作者真迹为基准，判断仿写段在每个风格维度上**像不像同一位作者写的**，
并指出仿写段在哪一维「露馅 / 走味」（读者一眼觉得不是这位作者）。

# 判别纪律（authorship verification · 配对相对判别）

1. **以作者真迹为锚**：作者真迹那一段就是「这位作者长什么样」的唯一基准——不要用你脑中泛泛的
   「好文笔」标尺，只问「仿写段这一维像不像作者真迹」。
2. **读者视角**：你是读者，凭语感判「读起来是不是同一个人」，不是查统计指标。
3. **逐维度解耦**：每个维度**分开判**，不要混成一个印象分。某维像、某维不像，如实分列。
4. **配对判别输出**：每维给 verdict —— 「命中」（仿写段这一维读起来像作者真迹）或「走味」（仿写段露馅、像别人/像 AI）。
5. **走味必须指证**：判「走味」要点名仿写段哪一处露馅、它和作者真迹的差别在哪（一句话，落到具体文本）。
6. **不比内容**：两段写的人物/情节/场景不同是正常的——只比**写法风格**，不比写了什么。
7. **认准标注的身份判**：以正文标注的「作者真迹 / 仿写」身份为准，**始终判仿写段哪维走味**，不要因为
   两段呈现先后顺序而改变判别方向（呈现顺序不代表谁是作者）。
"""


def _trim(text: str, limit: int) -> str:
    """裁到 limit 字（配对判别只需要足量语感样本 · 防 prompt 过长占 token）。"""
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit] + f"\n……[截断于 {limit} 字]"
    return text


def build_av_judge_prompt(author_text: str, replica_text: str,
                          sample_limit: int = 3000, swap: bool = False,
                          include_intent_dim: bool = False) -> str:
    """构造 AV-judge 配对判别 prompt：作者真迹（锚）vs 仿写（待验）· 解耦维度 · 读者视角。

    确定性纯函数（零模型调用）——测试只验此处的配对结构 + rubric + 输出 JSON 契约。

    swap（G2-CYCLIC 去位置偏 · experiment · 默认 False=原向零回归）：
      · False：作者真迹先呈现、仿写后呈现（规范朝向）。
      · True：**仅在 prompt 文本层反转两段的呈现顺序**（仿写先呈现、作者真迹后呈现），但
        rubric / verdict / 指证要求**始终锚到「仿写段」**（不绑字母槽），让 judge 始终判仿写走味。
        4 维输出 JSON schema（维度键名）**零变化**——下游 parse_av_verdicts 完全复用。
      · 多票只压随机噪声、压不掉 LLM 对配对判别的系统性位置偏（倾向判后呈现段更差）；
        半数投票任务 swap 让走味维分布对「呈现顺序」不敏感（_swap_assignment 分配）。
      · ⚠️ swap 只去**位置偏**，**不去 familiarity 偏**（judge 对同源 gemini 稿的熟悉度偏好）——
        见报告 position_bias_note，绝不宣称消除自偏。

    include_intent_dim（intent_recovery · experiment · 默认 False）：True 时追加「作者思维」第 5 维
      （INTENT_DIM）渲染 + 进输出 JSON schema。该维只出 advisory 文本，真判决交 mstyle 余弦。

    结构保证（供测试 + 复盘核对，不黑箱）：
      · swap=False：作者真迹标签在仿写标签之前；swap=True：仿写标签在作者真迹标签之前。
      · 维度（_active_dims）全列 · 每维带读者视角说明 + 配对判别问法（始终问「仿写段」走味）。
      · 输出 JSON 每维要 verdict（命中/走味）+ reason（走味须指证仿写段露馅处）。
    """
    a = _trim(author_text, sample_limit)
    b = _trim(replica_text, sample_limit)
    dims = _active_dims(include_intent_dim)

    # 两段呈现顺序按 swap 反转；身份标签始终绑「作者真迹 / 仿写」（不绑字母槽）→ verdict 方向稳定
    author_block = ("━━━━━━━━━━ 作者真迹（锚 · 这位作者长这样）━━━━━━━━━━", a)
    replica_block = ("━━━━━━━━━━ 仿写（待验证 · 判它哪维露馅）━━━━━━━━━━", b)
    blocks = [replica_block, author_block] if swap else [author_block, replica_block]

    order_hint = ("（本次仿写段在前、作者真迹段在后呈现——呈现顺序不代表谁是作者，"
                  "以标签为准始终判仿写段走味）" if swap
                  else "（本次作者真迹段在前、仿写段在后呈现）")

    L = [
        "# 作者验证 · 配对判别（作者真迹 vs 仿写 · 逐维度判仿写段哪维走味）",
        "",
        "下面两段：一段是**作者真迹（锚）**，一段是**仿写（待验证）**，身份见各段标签。",
        order_hint,
        "请以作者真迹为基准，逐维度判别仿写段像不像同一位作者——这是**配对相对判别**，比给仿写段打绝对分可靠。",
        "",
    ]
    for label, body in blocks:
        L += [label, "", body, ""]

    L += [
        f"# {len(dims)} 个解耦特质维度（分开判 · 不要混成总分）",
        "",
    ]
    for i, (name, desc, ask) in enumerate(dims, 1):
        L += [
            f"## 维度 {i} · {name}",
            f"说明：{desc}",
            f"配对判别问法：{ask}",
            f"verdict 取值：「{MATCH_VERDICT}」（仿写段这一维读起来像作者真迹）或"
            f"「{DRIFT_VERDICT}」（仿写段露馅，像别人/像 AI）。",
            "",
        ]

    L += [
        "# 输出格式",
        f"请严格按以下 JSON 输出（{len(dims)} 维各一项 · verdict 必须是「命中」或「走味」二选一）：",
        "```json",
        "{",
        '  "dimensions": {',
    ]
    for i, (name, _desc, _ask) in enumerate(dims):
        comma = "," if i < len(dims) - 1 else ""
        L.append(
            f'    "{name}": {{"verdict": "{MATCH_VERDICT}|{DRIFT_VERDICT}", '
            f'"reason": "<一句话理由 · 判走味须点名仿写段哪处露馅及与作者真迹的差别>"}}{comma}'
        )
    L += [
        "  }",
        "}",
        "```",
        "",
        f"提醒：以作者真迹为锚做相对判别，只比写法不比内容；判「{DRIFT_VERDICT}」必须指证仿写段的具体露馅处。",
    ]
    return "\n".join(L)


# ════════════════════════════════════════════════════════════════
# 解析 verdict 回复 → 4 维 advisory
# ════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> dict:
    """从 LLM 回复里宽容抽 JSON（去 ```json 包裹 · 取首个花括号块）。失败返回 {}。"""
    if not text:
        return {}
    t = text.strip()
    # 去 markdown 围栏
    if "```" in t:
        # 取第一个 ``` 块内容（兼容 ```json / ``` 两种）
        parts = t.split("```")
        for seg in parts:
            seg = seg.strip()
            if seg.lower().startswith("json"):
                seg = seg[4:].strip()
            if seg.startswith("{"):
                t = seg
                break
    # 截取首个 { ... } 平衡块（宽容尾部多余文本）
    start = t.find("{")
    if start < 0:
        return {}
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return {}
    return {}


def parse_av_verdicts(reply: str) -> dict:
    """把 LLM 回复解析成 4 维 verdict advisory。

    返回 {dimensions: {维度: {verdict, reason, drift: bool}}, drift_dims: [...], parse_ok: bool}。
    宽容：缺维度 → verdict=None / drift=False（不臆造走味）；非「走味」一律当未走味（保守 · 不误伤）。
    """
    data = _extract_json(reply)
    raw = data.get("dimensions", data) if isinstance(data, dict) else {}
    dims: dict[str, dict] = {}
    drift_dims: list[str] = []
    for name, _desc, _ask in AV_TRAIT_DIMS:
        entry = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(entry, dict):
            verdict = str(entry.get("verdict") or "").strip()
            reason = str(entry.get("reason") or "").strip()
        elif isinstance(entry, str):
            verdict, reason = entry.strip(), ""
        else:
            verdict, reason = "", ""
        is_drift = (DRIFT_VERDICT in verdict) if verdict else False
        dims[name] = {
            "verdict": verdict or None,
            "reason": reason or None,
            "drift": is_drift,
        }
        if is_drift:
            drift_dims.append(name)
    return {
        "dimensions": dims,
        "drift_dims": drift_dims,
        "parse_ok": bool(data),
    }


# ════════════════════════════════════════════════════════════════
# 自一致性重采样聚合（Rating Roulette · N 次重采样 → 4 维多数票 robust 聚合 + 方差透明）
# ════════════════════════════════════════════════════════════════

def aggregate_verdicts(samples: list[dict]) -> dict:
    """把 N 次 parse_av_verdicts 结果按 4 维**各取多数票** robust 聚合 + 暴露方差（透明 · 不黑箱）。

    输入 samples：parse_av_verdicts(...) 的列表（每个含 dimensions / drift_dims / parse_ok）。
    聚合规则（per-dim majority vote · 稳单次 LLM-judge 噪声）：
      · 每维统计 N 次里判「走味(drift)」vs「未走味」的票数。
      · drift 票 **严格过半**（> n_valid/2）才聚合判走味 —— **平票偏保守判命中**（不误伤真作者 ·
        北极星⑤ advisory 顾问非法官）。
      · 某维 N 次全缺（verdict 都 None）→ 该维 verdict=None / drift=False（不臆造）。
    方差透明（advisory 不黑箱 · 复盘可核）：每维带 votes（drift/match/abstain 计数）+ agreement
      （多数派占比，1.0=N 次全一致，越低越不稳）+ flipped（是否出现过分歧）。
      顶层 mean_agreement / unstable_dims / max_disagreement 供「这次判别稳不稳」一眼可读。

    返回 {dimensions, drift_dims, parse_ok, n_samples, n_valid_samples, mean_agreement,
          unstable_dims, agreement_by_dim, sample_drift_dims}。
    """
    samples = samples or []
    n_samples = len(samples)
    valid = [s for s in samples if isinstance(s, dict)]
    n_valid = sum(1 for s in valid if s.get("parse_ok"))

    dims: dict[str, dict] = {}
    drift_dims: list[str] = []
    agreement_by_dim: dict[str, float] = {}
    unstable_dims: list[str] = []

    for name, _desc, _ask in AV_TRAIT_DIMS:
        drift_votes = 0
        match_votes = 0
        abstain = 0
        reasons: list[str] = []
        for s in valid:
            entry = (s.get("dimensions") or {}).get(name) if isinstance(s, dict) else None
            if not isinstance(entry, dict):
                abstain += 1
                continue
            verdict = entry.get("verdict")
            if verdict is None:
                abstain += 1
                continue
            if entry.get("drift"):
                drift_votes += 1
                if entry.get("reason"):
                    reasons.append(str(entry["reason"]))
            else:
                match_votes += 1
        decided = drift_votes + match_votes  # 有效（非弃权）票数
        # 多数票：drift 严格过半才判走味 → 平票偏命中（保守不误伤）
        is_drift = decided > 0 and drift_votes > (decided / 2)
        if decided > 0:
            majority = max(drift_votes, match_votes)
            agreement = round(majority / decided, 3)
            verdict_label = DRIFT_VERDICT if is_drift else MATCH_VERDICT
        else:
            agreement = 1.0  # 全弃权 = 无分歧（也无判别）
            verdict_label = None
        # flipped = N 次里 drift 与 match 都出现过（判别在该维不稳）
        flipped = drift_votes > 0 and match_votes > 0
        dims[name] = {
            "verdict": verdict_label,
            "reason": (reasons[0] if (is_drift and reasons) else None),
            "drift": is_drift,
            "votes": {"drift": drift_votes, "match": match_votes, "abstain": abstain},
            "agreement": agreement,
            "flipped": flipped,
        }
        agreement_by_dim[name] = agreement
        if flipped:
            unstable_dims.append(name)
        if is_drift:
            drift_dims.append(name)

    # 顶层方差透明指标
    decided_agreements = [
        d["agreement"] for d in dims.values()
        if (d["votes"]["drift"] + d["votes"]["match"]) > 0
    ]
    mean_agreement = (round(sum(decided_agreements) / len(decided_agreements), 3)
                      if decided_agreements else 1.0)

    # G2-CYCLIC-3 swap 透明（advisory 不黑箱 · position-swap 是否真消位置偏须可复盘）：
    #   · 走味语义已锚到「仿写段」（与呈现顺序无关 · 见 build_av_judge_prompt swap），故聚合逻辑
    #     不需按 _swapped 翻转——_swapped 仅供透明上报 + 位置偏诊断。
    #   · sample_drift_detail 是 sample_drift_dims 的并行结构（不改原字段 · 保既有测试绿），
    #     每条 {swapped, drift_dims} 让元验证脚本能比对「同一对在 swap-off / swap-on 下走味分布」。
    n_swapped = sum(1 for s in valid if isinstance(s, dict) and s.get("_swapped"))
    return {
        "dimensions": dims,
        "drift_dims": drift_dims,
        "parse_ok": n_valid > 0,
        "n_samples": n_samples,
        "n_valid_samples": n_valid,
        "mean_agreement": mean_agreement,
        "unstable_dims": unstable_dims,
        "agreement_by_dim": agreement_by_dim,
        "sample_drift_dims": [s.get("drift_dims", []) for s in valid],
        "sample_drift_detail": [
            {"swapped": bool(s.get("_swapped")), "drift_dims": s.get("drift_dims", [])}
            for s in valid
        ],
        "n_swapped_samples": n_swapped,
        "position_bias_note": ("半数样本已作者真迹/仿写呈现顺序反转（G2-CYCLIC）· 去 judge 位置偏 · "
                               "不去 familiarity 偏" if n_swapped > 0 else
                               "本次未启用 position-swap（AV_JUDGE_POSITION_SWAP=off 或 N=1）"),
    }


def build_report(mode: str, parsed: dict | None, author_path: str = "",
                 replica_path: str = "", profile_name: str | None = None,
                 elapsed: float | None = None, error: str | None = None) -> dict:
    """组装 advisory 报告（shadow → verdict=None 只记录；active → 走味维度上报为待裁决项）。

    永远 advisory · code AV_TRAIT_DRIFT 绝不进 HARD_GATE_CODES（北极星⑤ · 共同纪律 2）。
    """
    shadow = (mode == "shadow")
    report: dict = {
        "judge": "av_judge",
        "issue_code": ISSUE_CODE,
        "gate_level": "advisory",   # ⚠️ 永远 advisory · 永不 hard_gate
        "mode": mode,
        "shadow": shadow,
        "author_path": author_path,
        "replica_path": replica_path,
        "profile_used": profile_name,
        "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        "note": ("AV-judge 是顾问非法官 · 仅 advisory（可豁免）· 绝不 hard_gate"
                 "（LLM-judge 对网文隐性风格会失准 · 创意写作域约 1/4 难例翻转 · "
                 "建议人工复核走味维度）"),
    }
    if error:
        report["error"] = error
        report["verdict"] = None
        report["dimensions"] = {}
        report["drift_dims"] = []
        return report

    parsed = parsed or {"dimensions": {}, "drift_dims": [], "parse_ok": False}
    report["dimensions"] = parsed["dimensions"]
    report["drift_dims"] = parsed["drift_dims"]
    report["parse_ok"] = parsed.get("parse_ok", False)
    # 自一致性透明：N 票多数票聚合时，把方差指标平铺进报告（advisory 不黑箱 · 复盘可核）。
    # G2-CYCLIC swap 透明（n_swapped_samples / position_bias_note / sample_drift_detail）一并平铺。
    for k in ("n_samples", "n_valid_samples", "mean_agreement", "unstable_dims",
              "agreement_by_dim", "sample_drift_dims", "sample_failures",
              "n_swapped_samples", "position_bias_note", "sample_drift_detail"):
        if k in parsed:
            report[k] = parsed[k]
    if parsed.get("unstable_dims"):
        report["consistency_note"] = (
            f"自一致性聚合：{len(parsed['unstable_dims'])} 个维度在 "
            f"{parsed.get('n_valid_samples', '?')} 票判别中出现分歧（多数票裁定 · "
            "平票偏命中保守不误伤）· 建议人工复核走味维度")
    if shadow:
        # shadow：只记录 · 不出顶层 verdict（不上报 audit_hub）
        report["verdict"] = None
        report["issues"] = []
    else:
        # active：每个走味维度作为 advisory 待裁决项上报（仍 advisory · 仍可豁免）
        report["verdict"] = "drift" if parsed["drift_dims"] else "match"
        report["issues"] = [
            {
                "code": ISSUE_CODE,
                "gate_level": "advisory",
                "dimension": d,
                "reason": parsed["dimensions"][d].get("reason"),
                "message": f"AV-judge 配对判别：维度「{d}」走味（B 读起来不像作者 A）· advisory 可豁免",
            }
            for d in parsed["drift_dims"]
        ]
    return report


def _emit(report: dict, out: str | None) -> None:
    """advisory 报告落盘（out 为空则打印 stdout）。"""
    blob = json.dumps(report, ensure_ascii=False, indent=2)
    if out:
        op = Path(out)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(blob, encoding="utf-8")
        print(f"[av_judge] 报告写入: {op}", file=sys.stderr)
    else:
        print(blob)
