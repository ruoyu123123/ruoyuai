# L9: 蒸馏 Agent 半 cluster 模式（A' 方案）

## 问题

单 cluster（6 章）agent 在 Claude Code sub-agent 环境下 **100% 失败**（6/6 全 502 EOF，24-28 min 统一超时）。

## 根因链

1. Claude Code sub-agent 有 **stream idle timeout**（默认 5 min `CLAUDE_STREAM_IDLE_TIMEOUT_MS`）
2. 长任务中 thinking 段或 tool gap 超过 idle 阈值 → 连接被切
3. 中转站层 retry 累积到 **wall-clock ~25 min 硬阈值** → 返回 502 EOF
4. 6 个并行 agent 同时消耗配额 → **session limit 打满** → 后续请求全 502
5. Windows 平台 Issue #49150 进一步加剧（Task() 无 timeout，hang 无法恢复）

## 解法：A' 半 cluster 模式

每个 cluster 拆为 3 个 sub-agent：
- Agent 1: 前半单章 JSON（3 章）
- Agent 2: 后半单章 JSON（3 章）
- Agent 3: 整 cluster 衔接 JSON（基于已落盘单章 JSON）

### 实测数据

| 任务类型 | 样本数 | 成功率 | 耗时范围 | token 消耗 |
|---|---|---|---|---|
| 3 章单章 JSON | 4 | 100% | 3-8 min | 65K-76K |
| 衔接 JSON（基于已有单章 JSON） | 2 | 100% | 3.2-3.5 min | 66K-70K |
| 衔接 JSON（需读原文） | 1 | 100% | 3.5 min | 66K |
| 6 章全 cluster（旧方案） | 6 | 0% | 24-28 min (timeout) | 10-30 (未执行) |

### 关键参数

- **并行数 ≤3**（防配额打满，之前 6 并行直接打爆 session limit）
- **单 agent 目标 ≤10 min**（远低于 5min idle / 25min wall-clock 双阈值）
- **每完成 1 章立即 Write**（防超时丢失已完成工作）
- **衔接 agent 后置**（依赖前两个 agent 的单章 JSON 落盘）

### JSON 合法性注意

agent 产出的 C_golden_paragraphs 段可能含未转义 ASCII 引号（原文中文引号被 LLM 转写为 ASCII）。
主代理收到后需跑 `json.loads()` 校验，失败则用 Python 自动修复（替换 value 内 stray `"` 为中文引号 `"`）。

## 适用范围

- `/distill-style` 阶段 1 表层蒸馏
- 任何需要 sub-agent 处理 >3 章原文的场景
- 不适用于主代理直接执行的场景（主代理无 sub-agent timeout 限制）

## 参考 Issues

- github.com/anthropics/claude-code/issues/25979（stream stall 无 read timeout）
- github.com/anthropics/claude-code/issues/49150（Task() 无 timeout，Windows）
- github.com/anthropics/claude-code/issues/4744（Agent Execution Timeout）
- github.com/anthropics/claude-code/issues/54472（API timeout during streaming）
