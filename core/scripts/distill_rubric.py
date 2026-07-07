#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""distill_rubric.py — LongBench-Write 六维质量 rubric（A12 · 2026-07-07）

出处：THUDM LongWriter `evaluation/judge.txt`（本仓 external_repos/LongWriter/evaluation/judge.txt）。
六维：Relevance / Accuracy / Coherence / Clarity / Breadth and Depth / Reading Experience，
各 1-5 整数分；聚合 (mean-1)*25 归一 0-100。

设计定位（A12 落地决策 · 与 research/open_source_writing_systems_round2.md 对齐）：
· 复刻评估现状是**纯统计无 LLM judge**（distill_replicate._score_draft_sfs → style_evaluator
  确定性 SFS；replication_fidelity_check 纯量化+mstyle 余弦）→ 按任务纪律落为
  **env 门控可选项**：DISTILL_RUBRIC_MODE 默认 off（真 API 纪律·不给主链加默认调用轮次）。
· 开启后由 distill_replicate.main 在终稿落盘前用**既有 call_gen_model 通道**发 1 次 judge
  调用（不新增独立 transport/链路步骤），结果写 meta.json["rubric_sixdim"] 与 SFS 并列。
· **长度剥离**（LongBench-Write 核心）：judge prompt 明示「不考虑长度是否达标」——质量与
  长度双轨分离（Breadth&Depth 评信息面的广度深度，不评字数达标）。
· **缺维度即整体作废重试**：judge JSON 缺任一维 / 分值非 1-5 整数 → 该次作废重试（≤3 次），
  全失败诚实记 status=rubric_unavailable，绝不伪造分。
· 🔴 北极星⑤：恒 gate_level=advisory 旁证观测——**不改任何闸门判据**，SFS 仍是唯一出货闸
  （distill-style 终止条件 / distill_finalize_verify 回灌闸均不读本模块产出）。
"""
from __future__ import annotations

import json
import os
import re

# 六维（LongBench-Write judge.txt 原始英文键 · 解析时按去符号小写归一容错别名写法）
RUBRIC_DIMENSIONS = [
    "Relevance",
    "Accuracy",
    "Coherence",
    "Clarity",
    "Breadth and Depth",
    "Reading Experience",
]

def _norm_key(k: str) -> str:
    """维度键归一（容忍 "Breadth & Depth" / "ReadingExperience" 等写法）：
    小写 → '&'→'and' → 去掉全部非字母。"""
    return re.sub(r"[^a-z]", "", str(k).lower().replace("&", "and"))


# 归一键 → 规范维度名
_DIM_CANON = {_norm_key(d): d for d in RUBRIC_DIMENSIONS}


def rubric_mode() -> str:
    """读 env DISTILL_RUBRIC_MODE 决定是否跑六维 rubric judge。

    值（大小写不敏感）：
      · off（默认 / 空 / 非法值）：不跑——复刻评估保持纯统计 SFS（真 API 纪律：
        rubric 需 1 次 gen-model judge 调用，默认不给主链加调用轮次）。
      · on/1/true/active → active：distill_replicate 终稿后发 1 次 judge 调用。
    """
    v = (os.environ.get("DISTILL_RUBRIC_MODE") or "").strip().lower()
    if v in ("on", "1", "true", "active"):
        return "active"
    return "off"


# judge system prompt（LongBench-Write judge.txt 中译适配 · 长度剥离明示）
RUBRIC_SYSTEM_PROMPT = (
    "你是文本质量评估专家，负责评估一段中文小说复刻文本的质量。评分务必严格。\n"
    "你只输出 JSON 评分，不输出其它内容。"
)


def build_rubric_prompt(replica_text: str, task_brief: str = "") -> str:
    """构造六维 rubric judge user prompt（LongBench-Write judge.txt 结构中译适配）。

    🔴 长度剥离（双轨分离核心）：明示「不考虑长度是否达标」——字数守恒/长度带另有
    确定性度量（cjk_count / splitter 契约），rubric 只评质量。
    """
    brief = task_brief or "按源作者风格 skill 复刻一段中文小说故事块（自创角色与场景·整块连续叙事）"
    return "\n\n".join([
        "请评估下面这段「复刻文本」对「写作任务」的完成质量。按以下六个维度各给 1-5 的整数分"
        "（5=最好，1=最差）：\n\n"
        "1. Relevance（相关性）：内容与写作任务高度相关完全适用（5）→ 完全不相关不适用（1）。\n"
        "2. Accuracy（准确性）：内容完全准确无事实错误无误导（5）→ 错误众多高度误导（1）。\n"
        "3. Coherence（连贯性）：结构清晰逻辑衔接流畅（5）→ 结构混乱毫无连贯（1）。\n"
        "4. Clarity（清晰度）：语言清楚细节丰富易于理解（5）→ 表达混乱细节匮乏（1）。\n"
        "5. Breadth and Depth（广度与深度）：内容兼具广度与深度信息量大（5）→ 严重缺乏广度深度信息量极少（1）。\n"
        "6. Reading Experience（阅读体验）：阅读体验极佳引人入胜（5）→ 体验极差乏味难读（1）。",
        "# 写作任务\n\n" + brief,
        "# 复刻文本\n\n" + (replica_text or ""),
        "# 输出要求\n\n"
        "先做一段简要质量分析，再给六维评分。输出必须严格遵循 JSON 格式：\n"
        '{"Analysis": ..., "Relevance": ..., "Accuracy": ..., "Coherence": ..., '
        '"Clarity": ..., "Breadth and Depth": ..., "Reading Experience": ...}\n\n'
        "⚠️ **评分时不考虑复刻文本的长度是否达标**（长度与质量双轨分离·长度另有确定性度量，"
        "字数多寡本身不加分也不扣分）。\n"
        "⚠️ 每个维度分数只能输出 1 到 5 之间的一个整数。六个维度一个都不能缺。",
    ])


def parse_rubric_json(text: str):
    """解析 judge 回复中的六维 JSON。返回 {规范维度名: int 1-5} 或 None（整体作废）。

    LongBench eval_quality.py 同款取块：第一个 '{' 到最后一个 '}'。
    整体作废条件（触发重试·绝不部分采信）：
      · 找不到 JSON / json.loads 失败 / 非 dict；
      · 六维缺任一维；
      · 任一维分值非整数（bool / 非整 float / 字符串数字除可安全转 int 外）或不在 [1,5]。
    """
    if not text:
        return None
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        obj = json.loads(text[s:e + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    scores: dict = {}
    for k, v in obj.items():
        canon = _DIM_CANON.get(_norm_key(k))
        if canon is None:
            continue  # Analysis 等非维度键跳过
        iv = _coerce_score(v)
        if iv is None:
            return None  # 分值非法 → 整体作废（缺维同义）
        scores[canon] = iv
    if set(scores.keys()) != set(RUBRIC_DIMENSIONS):
        return None  # 缺任一维 → 整体作废
    return scores


def _coerce_score(v):
    """把 judge 输出的分值安全转 1-5 整数；非法返回 None（bool/非整 float/越界均作废）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        iv = v
    elif isinstance(v, float):
        if not v.is_integer():
            return None
        iv = int(v)
    elif isinstance(v, str) and v.strip().isdigit():
        iv = int(v.strip())
    else:
        return None
    return iv if 1 <= iv <= 5 else None


def aggregate_score(scores: dict) -> float:
    """聚合公式（LongBench-Write）：(六维均分 - 1) * 25 归一 0-100。全 1 → 0；全 5 → 100。"""
    vals = [scores[d] for d in RUBRIC_DIMENSIONS]
    mean = sum(vals) / len(vals)
    return round((mean - 1) * 25, 2)


def run_rubric_judge(call_fn, replica_text: str, task_brief: str = "",
                     max_attempts: int = 3) -> dict:
    """跑六维 rubric judge（缺维即整体作废重试 ≤max_attempts·全失败诚实记不伪造）。

    call_fn(system: str, user: str) -> str：judge LLM 调用（生产=distill_replicate 用
    call_gen_model 包装·同栈 gen-model；测试注入 mock）。call_fn 抛异常按一次失败计。

    返回（恒 gate_level=advisory · 永不进 HARD_GATE_CODES · 永不影响 verdict/exit）：
      · 成功：{status: "ok", scores: {六维: int}, aggregate_0_100: float, attempts, ...}
      · 全失败：{status: "rubric_unavailable", scores: None, aggregate_0_100: None, ...}
    """
    user = build_rubric_prompt(replica_text, task_brief)
    attempts_log: list = []
    for attempt in range(1, max_attempts + 1):
        try:
            reply = call_fn(RUBRIC_SYSTEM_PROMPT, user)
        except Exception as e:  # noqa: BLE001 · judge 调用失败按一次作废计·继续重试
            attempts_log.append({"attempt": attempt, "error": f"judge 调用异常: {str(e)[:160]}"})
            continue
        scores = parse_rubric_json(reply)
        if scores is not None:
            return {
                "status": "ok",
                "gate_level": "advisory",
                "scores": scores,
                "aggregate_0_100": aggregate_score(scores),
                "attempts": attempt,
                "attempts_log": attempts_log,
                "_doc": ("LongBench-Write 六维质量 rubric（长度剥离）· advisory 旁证观测·"
                         "SFS 仍是唯一出货闸·聚合=(mean-1)*25 归一 0-100"),
            }
        attempts_log.append({
            "attempt": attempt,
            "error": "judge JSON 缺维/分值非法 · 整体作废",
            "reply_head": (reply or "")[:200],
        })
    return {
        "status": "rubric_unavailable",
        "gate_level": "advisory",
        "scores": None,
        "aggregate_0_100": None,
        "attempts": max_attempts,
        "attempts_log": attempts_log,
        "_doc": (f"judge 连续 {max_attempts} 次缺维/非法 → 诚实记 rubric_unavailable "
                 "不伪造分（缺维即整体作废纪律·不部分采信）"),
    }


def run_rubric_judge_gated(call_fn, replica_text: str, task_brief: str = "") -> dict:
    """env 门控入口（distill_replicate.main 唯一调用面）：off 时零 LLM 调用直接返回。"""
    mode = rubric_mode()
    if mode != "active":
        return {
            "status": "off",
            "mode": mode,
            "gate_level": "advisory",
            "_doc": ("DISTILL_RUBRIC_MODE=off（默认·真 API 纪律）· 复刻评估保持纯统计 SFS·"
                     "设 DISTILL_RUBRIC_MODE=on 开启六维 rubric 旁证（1 次 gen-model judge 调用）"),
        }
    return run_rubric_judge(call_fn, replica_text, task_brief)
