# 运行时报错自学习 lessons（self_heal_engine 自动沉淀）

> 自动生成于 2026-06-19T13:36:32+08:00 · 数据源 runtime/self_heal_kb.json · 仅 known/regression 级
> 这是 advisory 经验（怎么避免重复运行时报错），不是 hard_gate。

## ::UnknownError::proactor_events.py:162（6x · known）
- **现象**：UnknownError @ proactor_events.py:162，样例 ``
- **为什么**：未知根因
- **怎么用**：人工诊断 raw 上下文

## ::UnicodeDecodeError::codecs.py:322（6x · known）
- **现象**：UnicodeDecodeError @ codecs.py:322，样例 `'X' codec can't decode byte 0xce in position N: invalid continuation byte`
- **为什么**：编码不符（中文 / 弯引号）
- **怎么用**：指定 encoding='utf-8'；弯引号 codepoint 校验（呼应 dialogue_quote）
