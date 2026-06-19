#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""av_judge.py — AV-judge 解耦特质·作者验证（读者视角配对判别 · advisory · 2026-05-31）

【是什么】Author-Verification judge：喂 1 段**作者原文** + 1 段**仿写**，要求 gen-model
  **逐维度配对判别**——把 A（作者）当锚、B（仿写）当待验，逐维点名「B 在哪个维度露馅
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

【自一致性重采样（Rating Roulette · 2026-05-31 · 稳方差）】
  即便配对相对判别，单次 LLM-judge 仍有方差（同一对 A/B 跑两次可能一次走味一次命中）。故
  同 judge model 跑 N 次重采样（temperature 微抖 · env AV_JUDGE_N_SAMPLES 默认 3 · 设 1 关），
  4 维**各取多数票**做 robust 聚合 + 暴露方差（agreement / unstable_dims · advisory 不黑箱）。
  无需 logprob（黑箱模型可用）· 平票偏命中（保守不误伤真作者）· 永远 advisory 永不 hard_gate。

【配对判别 > 绝对打分】(authorship verification 范式)
  不问「B 像不像作者（打 1-10）」，问「给定 A 是作者真迹，B 在哪几维露馅」——
  相对锚定把「这个作者基线长什么样」交给样本 A 决定，绕开 LLM 对网文隐性风格的绝对标尺漂移。

【必 shadow 上线 · 绝不 hard_gate】(北极星⑤ + 共同纪律 2)
  实证（Catch Me If You GAN / Are We There Yet On Detecting LLM Texts）：LLM-judge 对网文
  隐性风格会**失准**——创意写作域约 1/4 难例判别翻转。故：
    · 永远 advisory，code AV_TRAIT_DRIFT **绝不进 audit_hub.HARD_GATE_CODES**。
    · env AV_JUDGE_MODE 控制（默认 off）：
        off（默认）：完全跳过——不构 prompt、不调 gen-model、零回归（共同纪律 2：改判决行为默认 off）。
        shadow：构 prompt + 调 gen-model + 出 4 维 advisory，但**只记录**（report.shadow=True ·
                顶层 verdict=None · 不上报 audit_hub）→ 先与 SFS / 人评校准，再放量。
        active：超阈维度作为 advisory 待裁决项上报（仍 advisory · 仍可豁免 · 永不 hard_gate）。
  shadow 是默认上线姿态：先攒「AV-judge 判走味」vs「SFS 判过」vs「人评」三方对照样本，
  确认 AV-judge 在本作者上不误判，再考虑 active。

【复用 · 薄】(北极星⑥ 别臃肿)
  · 纯 prompt + 薄 Python，gen-model 调用复用 gen_model_loader 同款 fallback 管线
    （与 distill_replicate.call_gen_model 一致的 active→fallback 链 · 不另起调用栈）。
  · 零新依赖（stdlib + openai/​dotenv 已是 gen_model 栈既有）· 零 GPU。
  · 独立文件——**不挂 distill_replicate**（避与 L3b CoT-first / snippet_seed 等并行件冲突）。

用法：
  shadow:  AV_JUDGE_MODE=shadow python av_judge.py \\
             --author workspace/styles/蛊真人/原文/第010章.txt \\
             --replica workspace/styles/蛊真人/复刻测试/v7_round1/cluster_001_replica.txt \\
             [--out report.json]
  默认 off（无 env）→ 直接打印 skipped 报告退出 0。

测试只验确定性层（prompt 构造 + rubric + 配对结构 + off 默认）· 不实跑 gen-model（需 API）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 复用 gen_model_loader 管线（薄复用 · 不另起调用栈 · 北极星⑥）
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from gen_model_loader import (  # noqa: E402
    GenModelLoader,
    GenModelConfigError,
    GenModelExhaustedError,
    Profile,
    reasoning_extra_body,
)

# advisory 专用 issue code · ⚠️ 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤ · 共同纪律 2）
ISSUE_CODE = "AV_TRAIT_DRIFT"

# ── 自一致性重采样（Rating Roulette · 稳 LLM-judge 方差）─────────────────
# 根因（本批任务说明 · arxiv 实证）：av_judge 原本**单次**配对判别——LLM-judge 对网文隐性
#   风格失准（创意写作域约 1/4 难例翻转），单次采样方差大，同一对 (A,B) 跑两次可能一次判
#   「走味」一次判「命中」。治法 = Rating Roulette / self-consistency：同 judge model 跑
#   N 次重采样（temperature 微抖），4 维**各取多数票**做 robust 聚合，把单次噪声平滑掉。
# env AV_JUDGE_N_SAMPLES：默认 3（质量优先 · N≥2 真聚合生效）· 设 1 = 关（退回单次单采样 ·
#   零回归逃生口）· 钳到 [1, AV_JUDGE_N_SAMPLES_MAX]（防 token / 时延失控）。
# ⚠️ 仍 advisory：聚合只稳方差、不强判——多数票 + 方差透明上报，仍可豁免、永不 hard_gate。
AV_JUDGE_N_SAMPLES_DEFAULT = 3
AV_JUDGE_N_SAMPLES_MAX = 7
# 每次重采样在 profile 基准 temperature 上的抖动量（Rating Roulette 微抖 · 制造采样多样性
#   又不让判别失稳）。第 0 次用基准温度，之后按 ±step 交替抖。
AV_JUDGE_TEMP_JITTER_STEP = 0.15

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

# ── intent_recovery 扩展维（P0 · experiment · 默认不进 AV_TRAIT_DIMS 主列表）──────────
# 「作者思维」维（B1-B3 决策骨）= 反推作者在岔路口的取舍倾向，**不是表层文体**。仅在
#   build_av_judge_prompt(include_intent_dim=True) 时追加渲染（默认 False · 零回归 · 不污染纯文体 4 维）。
# 🔴 grounding 切断复述捷径（R3 P0-IR-1）：ask 只给「不含作者档 rationale 原文的中性维度定义」，
#   绝不出现「母题/胜利代价藏悲凉」等已聚合的 author_decision_principles 文案——让 judge 自己反推，
#   防它照抄作者档原文造成虚假高余弦。判决权**不在此维 verdict**（它仅出 advisory 文本）——真判决
#   交确定性 mstyle 余弦（replication_fidelity_check.intent_recovery_cosine·embedding_store）。
# ⚠️ 弱模型对「决策倾向」抽象维执行力弱（惊悚乐园 v2 文字约束 rollback 教训）→ 默认关·仅 experiment 开。
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


def _av_judge_mode() -> str:
    """读 env AV_JUDGE_MODE：默认 active（2026-05-31 放量）/ shadow / off。

    active（默认）：构 prompt + 调 gen-model + 4 维配对判别 · 走味维度作 advisory 待裁决项上报
      （仍 advisory · 仍可豁免 · code AV_TRAIT_DRIFT 永不进 HARD_GATE_CODES）。LLM-judge 对网文
      隐性风格会失准（创意写作域约 1/4 难例翻转）→ 故必 advisory + 报告显式标注「建议人工复核」，
      绝不黑箱判决、绝不误伤真作者。
    shadow：构 prompt + 调 gen-model + 出 4 维 advisory，但只记录（不上报 audit_hub · 先校准）。
    off：完全跳过——不构 prompt、不调 gen-model（无 gen-model 配置/离线环境的逃生口）。

    空 / 非法值 → active（放量默认）。
    """
    m = (os.environ.get("AV_JUDGE_MODE") or "active").strip().lower()
    return m if m in ("shadow", "active", "off") else "active"


def _n_samples() -> int:
    """读 env AV_JUDGE_N_SAMPLES：默认 3（N≥2 真聚合稳方差）· 1=关（退回单次）· 钳到 [1, MAX]。

    空 / 非法值 → 默认 3。< 1 钳到 1（=关闭聚合，退回单采样，零回归）；> MAX 钳到 MAX。
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

    🔴 主代理施工决定（比蓝图「默认半 swap」更保守）：av_judge 已是 active 生产判别组件，
      position-swap 去偏的**有效性需 API 离线对称性闸验证**（本机验证不了），故默认 off →
      现有 active 行为**完全零回归**（swap 默认关 · self_consistency_judge 全 swap=False）。
      只有显式 AV_JUDGE_POSITION_SWAP=on（experiment）才在 N 采样里半数样本 swap。
      N=1 时即便 on 也退化为 0 个 swap（零回归）。

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
    """为 n 次重采样分配 swap 方向（前一半 False · 后一半 True · 最大化位置对称采样）。

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


def _jittered_temperatures(base: float, n: int) -> list[float]:
    """为 n 次重采样生成 temperature 序列（Rating Roulette 微抖 · 制造采样多样性）。

    第 0 次用基准温度（保留单次行为的可复现性）；之后按 +step / -step 交替抖，钳到 [0, 1.5]。
    确定性纯函数（不随机）→ 测试可断言序列；既造多样性又不让判别失稳。
    """
    base = float(base if base is not None else 0.8)
    temps: list[float] = []
    for i in range(max(1, n)):
        if i == 0:
            t = base
        else:
            # i=1 → +step, i=2 → -step, i=3 → +2step, i=4 → -2step ...
            mag = ((i + 1) // 2) * AV_JUDGE_TEMP_JITTER_STEP
            t = base + mag if (i % 2 == 1) else base - mag
        temps.append(round(max(0.0, min(1.5, t)), 3))
    return temps


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

    确定性纯函数（不调 gen-model）——测试只验此处的配对结构 + rubric + 输出 JSON 契约。

    swap（G2-CYCLIC 去位置偏 · experiment · 默认 False=原向零回归）：
      · False：作者真迹先呈现、仿写后呈现（历史原向）。
      · True：**仅在 prompt 文本层反转两段的呈现顺序**（仿写先呈现、作者真迹后呈现），但
        rubric / verdict / 指证要求**始终锚到「仿写段」**（不绑字母槽），让 judge 始终判仿写走味。
        4 维输出 JSON schema（维度键名）**零变化**——下游 parse_av_verdicts 完全复用。
      · 多 seed 只压随机噪声、压不掉 LLM 对配对判别的系统性位置偏（倾向判后呈现段更差）；
        半数样本 swap 让走味维分布对「呈现顺序」不敏感（self_consistency_judge 分配）。
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
# gen-model 调用（薄复用 gen_model_loader · 与 distill_replicate 同款 fallback 管线）
# ════════════════════════════════════════════════════════════════

def call_gen_model(loader: GenModelLoader, system: str, user: str,
                   default_max_tokens: int = 1500,
                   tag: str = "av_judge") -> tuple[str, Profile, float]:
    """调当前 active profile，失败按 fallback 链尝试。返回 (text, profile, elapsed)。

    与 distill_replicate.call_gen_model 同款 active→fallback 链——不另起调用栈（北极星⑥）。
    判别只需短输出（4 维 JSON），default_max_tokens 比复刻小。
    """
    from openai import OpenAI

    candidates = loader.get_callable_profiles()
    failures: list[tuple[str, str]] = []
    prefix = f"[{tag}] " if tag else ""

    for i, profile in enumerate(candidates):
        max_tokens = profile.max_tokens or default_max_tokens
        if i == 0:
            print(f"{prefix}[av_judge] 调用 active: {profile.name} ({profile.model})", file=sys.stderr)
        else:
            print(f"\n{prefix}[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url)
        full_text = ""
        t0 = time.time()
        _xb = reasoning_extra_body(profile)  # reasoning 控制(elysiver reasoning_effort/pie-xian thinking_level)·防 thinking 暴走
        try:
            stream = client.chat.completions.create(
                model=profile.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=profile.temperature,
                stream=True,
                **({"extra_body": _xb} if _xb else {}),
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    full_text += piece
                    sys.stderr.write(piece)
                    sys.stderr.flush()
        except Exception as e:  # noqa: BLE001 — fallback 链需吞任意 provider 异常
            reason = str(e)[:200]
            print(f"\n{prefix}[FALLBACK] {profile.name} 失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue

        elapsed = time.time() - t0
        print(f"\n{prefix}[av_judge] 接收完毕 ({len(full_text)} chars, {elapsed:.1f}s) via {profile.name}")
        return full_text, profile, elapsed

    raise GenModelExhaustedError(failures)


# ════════════════════════════════════════════════════════════════
# 解析 LLM 回复 → 4 维 advisory
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


def self_consistency_judge(loader: "GenModelLoader", author_text: str, replica_text: str,
                           sample_limit: int = 3000, n_samples: int | None = None,
                           tag: str = "av_judge", swap_on: bool | None = None,
                           include_intent_dim: bool = False) -> dict:
    """同 judge model 跑 N 次重采样（temperature 微抖 + 半数 position-swap）→ 多数票聚合（Rating Roulette）。

    这是 av_judge 的**自一致性核心**：稳住单次 LLM-judge 的方差。N=1 时退化为单次单采样
    （= 改造前行为 · 零回归逃生口）。

    G2-CYCLIC 半数 swap（experiment · AV_JUDGE_POSITION_SWAP=on 才开 · 默认 off=全 swap=False 零回归）：
      · swap_on=None → 读 env _position_swap_on()（默认 off）。
      · on 时 N 采样里前一半原向 / 后一半 swap（_swap_assignment）——在**不增调用次数**的现有 N 采样里
        分配 swap（520 友好 · 复用基建），最大化位置对称采样去 judge 位置偏。N=1 恒不 swap（零回归）。
      · 走味语义已锚到「仿写段」（不绑字母槽 · 见 build_av_judge_prompt swap）→ swap 样本 drift_dims
        方向天然一致，聚合无需翻转；_swapped 标志仅供透明上报（aggregate_verdicts 平铺 n_swapped_samples）。

    include_intent_dim（intent_recovery · experiment · 默认 False）：透传给 build_av_judge_prompt
      追加「作者思维」第 5 维（仅 advisory 文本 · 不进 parse 聚合 · 真判决交 mstyle 余弦）。

    实现（薄复用 · 北极星⑥）：
      · build_av_judge_prompt 按 swap 方向构（swap-off / swap-on 各构一次 · 缓存复用 · 省 token）。
      · 复用 call_gen_model（签名不变 → 既有 mock 兼容）；temperature 抖动通过临时改写候选
        profile.temperature 实现（call_gen_model 内部读 profile.temperature），跑完恢复。
      · 每次回复 parse_av_verdicts → 打 _swapped 标志 → aggregate_verdicts 多数票聚合。

    返回 aggregate_verdicts(...) 的结果，外加 error（任一/全部采样失败时聚合仍尽力 · 全失败才
    error 非空 + drift_dims 空）。advisory 永不抛错中断流水线（北极星⑤）。
    """
    n = n_samples if n_samples is not None else _n_samples()
    n = max(1, n)
    swap_on = _position_swap_on() if swap_on is None else swap_on
    swaps = _swap_assignment(n, swap_on)

    # prompt 按 swap 方向构（最多两种 · 缓存复用省 token）
    _prompt_cache: dict[bool, str] = {}

    def _prompt_for(sw: bool) -> str:
        if sw not in _prompt_cache:
            _prompt_cache[sw] = build_av_judge_prompt(
                author_text, replica_text, sample_limit, swap=sw,
                include_intent_dim=include_intent_dim)
        return _prompt_cache[sw]

    # 取基准 temperature（候选 profile 的第一档 · 缺则 0.8）做抖动序列
    candidates = []
    try:
        candidates = list(loader.get_callable_profiles())
    except Exception:  # noqa: BLE001 — 取不到候选不致命，后续 call_gen_model 自会报错
        candidates = []
    base_temp = candidates[0].temperature if candidates else 0.8
    temps = _jittered_temperatures(base_temp, n)

    samples: list[dict] = []
    failures: list[str] = []
    for i, (temp, sw) in enumerate(zip(temps, swaps)):
        user_prompt = _prompt_for(sw)
        # temperature 微抖：临时改写候选 profile 温度（call_gen_model 内部读 profile.temperature）
        saved = [(p, getattr(p, "temperature", None)) for p in candidates]
        for p in candidates:
            try:
                p.temperature = temp
            except Exception:  # noqa: BLE001 — profile 不可写则跳过抖动（不致命）
                pass
        try:
            reply, _profile, _elapsed = call_gen_model(
                loader, AV_JUDGE_SYSTEM_PROMPT, user_prompt, tag=f"{tag}_sc{i + 1}")
            parsed = parse_av_verdicts(reply)
            parsed["_swapped"] = sw   # G2-CYCLIC 透明：标记该样本呈现方向（聚合不翻转·仅上报）
            samples.append(parsed)
        except GenModelExhaustedError as e:
            failures.append(f"sample{i + 1}: gen-model 全部失败 {str(e)[:120]}")
        except Exception as e:  # noqa: BLE001 — advisory 永不中断
            failures.append(f"sample{i + 1}: {str(e)[:120]}")
        finally:
            for p, t in saved:  # 恢复原温度（不污染 loader 给后续调用）
                try:
                    p.temperature = t
                except Exception:  # noqa: BLE001
                    pass

    agg = aggregate_verdicts(samples)
    # 全部采样失败 → error 非空（调用方据此降级）；部分失败聚合仍尽力，只记 partial 警告
    if not samples:
        agg["error"] = "; ".join(failures) or "无有效采样"
    else:
        agg["error"] = None
        if failures:
            agg["sample_failures"] = failures
    return agg


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
        "note": ("AV-judge 是顾问非法官 · 仅 advisory（可豁免）· 绝不 hard_gate · "
                 "默认 off · shadow 先与 SFS/人评校准（LLM-judge 对网文隐性风格会失准 · "
                 "创意写作域约 1/4 难例翻转）"),
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
    # 自一致性透明：N 次重采样多数票聚合时，把方差指标平铺进报告（advisory 不黑箱 · 复盘可核）。
    # G2-CYCLIC swap 透明（n_swapped_samples / position_bias_note / sample_drift_detail）一并平铺。
    for k in ("n_samples", "n_valid_samples", "mean_agreement", "unstable_dims",
              "agreement_by_dim", "sample_drift_dims", "sample_failures",
              "n_swapped_samples", "position_bias_note", "sample_drift_detail"):
        if k in parsed:
            report[k] = parsed[k]
    if parsed.get("unstable_dims"):
        report["consistency_note"] = (
            f"自一致性聚合：{len(parsed['unstable_dims'])} 个维度在 "
            f"{parsed.get('n_valid_samples', '?')} 次重采样中出现分歧（多数票裁定 · "
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


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# best-of-N 复用接口（薄 · gen_writer 配对重排择优用 · 不另起调用栈 · 北极星⑥）
# ════════════════════════════════════════════════════════════════

def pairwise_drift_count(loader: GenModelLoader, author_text: str, replica_text: str,
                         sample_limit: int = 3000) -> dict:
    """配对判别一段仿写 vs 作者真迹，返回走味维度计数（best-of-N 择优用 · advisory）。

    这是 av_judge 给写作端 best-of-N 重排的**薄复用接口**——不走 CLI / 不读写文件，
    直接拿 loader + 两段文本做一次 4 维配对判别，返回结构化结果给调用方做候选排序。

    返回 {drift_count: int, drift_dims: [...], dimensions: {...}, parse_ok: bool,
          error: str|None}。
      · drift_count = 走味维度数（0=四维全命中，最像作者；越大越不像 → best-of-N 越靠后）。
      · 任何 gen-model 失败 → error 非空 + drift_count=None（调用方据此降级到纯 SFS 排序，
        不阻断 · advisory 永不抛错中断写作流水线 · 北极星⑤）。

    ⚠️ 永远 advisory：本函数只为「在 N 个候选里相对排序」服务，不产 hard_gate、不否决任何稿。
    LLM-judge 对网文隐性风格会失准（创意写作域约 1/4 难例翻转），故只做 select 不做强判。

    自一致性（2026-05-31）：内部走 self_consistency_judge（AV_JUDGE_N_SAMPLES 默认 3 次重采样 ·
      4 维多数票聚合），稳住单次方差再交 best-of-N 排序。N=1（env 设）退化为单次（零回归）。
    """
    agg = self_consistency_judge(loader, author_text, replica_text, sample_limit,
                                 tag="av_judge_bestofn")
    if agg.get("error"):
        return {"drift_count": None, "drift_dims": [], "dimensions": {},
                "parse_ok": False, "error": agg["error"][:200]}
    return {
        "drift_count": len(agg["drift_dims"]),
        "drift_dims": agg["drift_dims"],
        "dimensions": agg["dimensions"],
        "parse_ok": agg["parse_ok"],
        "error": None,
        # 方差透明（调用方可据 mean_agreement 判这次排序信号稳不稳）
        "n_valid_samples": agg.get("n_valid_samples"),
        "mean_agreement": agg.get("mean_agreement"),
        "unstable_dims": agg.get("unstable_dims", []),
    }


# ════════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="AV-judge 解耦特质·作者验证（配对判别 · 读者视角 · advisory · 默认 off）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--author", required=True, help="作者真迹文本路径（锚）")
    parser.add_argument("--replica", required=True, help="仿写文本路径（待验证）")
    parser.add_argument("--out", help="报告 JSON 输出路径（缺省打印到 stdout）")
    parser.add_argument("--sample-limit", type=int, default=3000,
                        help="每段送审字数上限（默认 3000 · 配对判别只需足量语感样本）")
    parser.add_argument("--position-swap", action="store_true",
                        help="G2-CYCLIC 去位置偏（experiment · 半数样本反转呈现顺序 · 等价 AV_JUDGE_POSITION_SWAP=on）")
    parser.add_argument("--intent-dim", action="store_true",
                        help="intent_recovery 加「作者思维」第 5 维（experiment · 仅 advisory 文本 · 等价 AV_JUDGE_INTENT_DIM=on）")
    args = parser.parse_args()

    mode = _av_judge_mode()
    n_samples = _n_samples()
    swap_on = _position_swap_on() or args.position_swap
    intent_on = _intent_dim_on() or args.intent_dim
    print(f"[av_judge] AV_JUDGE_MODE = {mode}"
          f"（{'完全跳过 · 零回归' if mode == 'off' else 'shadow 只记录 · 不上报' if mode == 'shadow' else 'active · 走味维度作 advisory 上报'}）"
          f" · AV_JUDGE_N_SAMPLES = {n_samples}"
          f"（{'单次 · 关聚合' if n_samples == 1 else f'{n_samples} 次重采样多数票聚合稳方差'}）"
          f" · position_swap = {'on（G2-CYCLIC 半 swap · experiment）' if swap_on else 'off（零回归）'}"
          f" · intent_dim = {'on（第5维 · experiment）' if intent_on else 'off（默认4维）'}")

    # off（默认）：完全跳过——不构 prompt、不调 gen-model（共同纪律 2 · 零回归）
    if mode == "off":
        report = build_report("off", None, args.author, args.replica)
        report["skipped"] = True
        report["verdict"] = None  # 跳过 = 未判别 · 不报 match（off 报告不暗示真判决）
        report["issues"] = []
        _emit(report, args.out)
        return 0

    author_path, replica_path = Path(args.author), Path(args.replica)
    for p, label in ((author_path, "作者真迹"), (replica_path, "仿写")):
        if not p.exists():
            print(f"[ERROR] {label}文件不存在: {p}", file=sys.stderr)
            return 2

    author_text = _read_text(author_path)
    replica_text = _read_text(replica_path)

    loader = GenModelLoader()
    try:
        active = loader.get_active_profile()
        print(f"[av_judge] active profile = {active.name} ({active.model})", file=sys.stderr)
    except GenModelConfigError as e:
        # 配置缺失：advisory 层不阻断流水线——出 error 报告退 0（共同纪律 · 失败不中断）
        report = build_report(mode, None, str(author_path), str(replica_path),
                              error=f"gen-model 配置错误: {e}")
        _emit(report, args.out)
        return 0

    # 自一致性：N 次重采样 + 多数票聚合（AV_JUDGE_N_SAMPLES 默认 3 · 1=退回单次）
    #   + G2-CYCLIC 半数 swap（swap_on · experiment · 默认 off 零回归）+ intent_dim（intent_on · 仅 advisory 文本）
    t0 = time.time()
    agg = self_consistency_judge(loader, author_text, replica_text,
                                 args.sample_limit, n_samples=n_samples, tag="av_judge",
                                 swap_on=swap_on, include_intent_dim=intent_on)
    elapsed = time.time() - t0
    if agg.get("error"):
        report = build_report(mode, None, str(author_path), str(replica_path),
                              error=f"gen-model 全部失败: {str(agg['error'])[:300]}")
        _emit(report, args.out)
        return 0  # advisory 不阻断

    report = build_report(mode, agg, str(author_path), str(replica_path),
                          profile_name=active.name, elapsed=elapsed)
    _emit(report, args.out)
    return 0


def _emit(report: dict, out: str | None) -> None:
    blob = json.dumps(report, ensure_ascii=False, indent=2)
    if out:
        op = Path(out)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(blob, encoding="utf-8")
        print(f"[av_judge] 报告写入: {op}", file=sys.stderr)
    else:
        print(blob)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
