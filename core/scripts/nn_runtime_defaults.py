#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-30 NN 默认接入创作流程
"""nn_runtime_defaults.py — 创作流程 NN 模型默认开启（单一真理源）。

【为什么】NN 推理桥（surprisal/coherence/vad/coref/character_network）读 os.environ 的
RUOYU_NN_* 门控做开关。门控默认 off 是为了**单元测试确定性**（测试 import 函数走无-NN
确定性路径·只在显式 setenv 时测 NN 路径）。但真实创作流程要让模型「能用上」——
本模块由创作命令行入口（audit_hub.main / save_state.main）调用·setdefault 默认开启所有
NN 门控。

【为什么放 main() 调用而非模块级全局默认】
  · main() 只在 `python xxx.py` 命令行直接执行时跑——即真实创作流程入口
  · 单元测试 import 模块函数·不触发 main()·门控保持默认 off → 测试确定性零影响
  · setdefault 不覆盖已有值：显式 RUOYU_NN_*=0（调试/对照/性能基准）仍可关闭
  · 各桥 enabled() 仍独立做能力检测（venv/模型/ckpt 缺失 → 自动安全回退）——
    本模块只声明「意图开启」·能力由桥把关·非降级旁路·北极星⑤ advisory shadow

【接入点】
  · audit_hub.main()  → 5 个 NN scanner（surprisal/coherence/emotion-VAD/coref/char-net）
  · save_state.main() → appraisal beat 的 VAD 情绪重算

【北极星边界】NN 输出全 advisory（不进 hard_gate·不干涉模型创作判断）。
开 = 让顾问发声·非让顾问当法官。
"""
from __future__ import annotations

import os

# 创作流程默认开启的 NN 门控（仅含有真实创作调用点的桥）。
# 注：feature_store / data_flywheel / model_registry 当前无创作调用点（孤儿），
#     不列入——避免 setdefault 一个无人消费的门控造成「已接入」假象。
_CREATIVE_NN_GATES = (
    "RUOYU_NN_SURPRISAL",       # surprisal_scanner → 中文 GPT-2 信息密度（venv 推理）
    "RUOYU_NN_COHERENCE",       # coherence_scanner → 句段连贯评分（venv ckpt）
    "RUOYU_NN_VAD",             # emotion_arc/granularity/curve + save_state → 情绪 VAD（venv ckpt）
    "RUOYU_NN_COREF",           # character_consistency → 共指消解（rule 后端零依赖·系统 py）
    "RUOYU_CHARACTER_NETWORK",  # character_consistency → 角色关系网络（rule 后端·系统 py）
)


def creative_nn_gates() -> tuple[str, ...]:
    """返回创作流程默认开启的 NN 门控名（只读·供测试/审计引用单一真理源）。"""
    return _CREATIVE_NN_GATES


def enable_creative_nn_defaults() -> list[str]:
    """创作流程命令行入口调用：setdefault 默认开启所有 NN 门控。

    - setdefault 语义：未显式设置的门控 → 设 "1"；已设（含 "0" 显式关）→ 不动。
    - 返回本次实际新设的门控名列表（供日志/审计·便于排查「为何没开/开了哪些」）。
    """
    newly_set: list[str] = []
    for gate in _CREATIVE_NN_GATES:
        if gate not in os.environ:
            os.environ[gate] = "1"
            newly_set.append(gate)
    return newly_set
