# Agent 调用模板库

主代理（write-chapter / save-state / 未来新调度器）启动 agent 时，**必须严格使用下面的 prompt 模板**。

## 为什么要这样做

- 子代理的系统提示（`.claude/agents/novel-*.md`）已定义职责和输出契约
- 主代理**不应**在调用 prompt 里重复规则——那会和系统提示冲突，也会污染上下文
- 主代理**只传参数**，用模板最短形式
- 参数值从执行环境取（项目路径、章节号、manifest 路径），不得写死

## 调用原则（硬性）

1. **最小化 prompt**：只传契约字段，不传规则、不传数据
2. **契约字段必填**：写作类 agent 至少有 `PROJECT:` 和 `CHAPTER:` 或 `MODE:`
3. **路径用绝对路径**：避免子代理 cwd 歧义
4. **禁止把 skill/agent 文件内容塞进 prompt**：Hook 会拦截

---

## 模板 1: novel-writer

```
subagent_type: novel-writer
description: 写第<N>章
prompt:
PROJECT: <绝对项目路径>
CHAPTER: <N>
MANIFEST: <绝对项目路径>/_数据库/.manifest/ch_<NNN>.json
```

**示例**：

```
subagent_type: novel-writer
description: 写第2章
prompt:
PROJECT: <REPO_ROOT>/workspace/novels/<书名>
CHAPTER: 2
MANIFEST: <REPO_ROOT>/workspace/novels/<书名>/_数据库/.manifest/ch_002.json
```

---

## 模板 2: novel-validator-repair

```
subagent_type: novel-validator-repair
description: 修第<N>章 validate 报错
prompt:
PROJECT: <绝对项目路径>
CHAPTER: <N>
MODE: validate-repair
MAX_ROUNDS: 3
```

---

## 模板 2b: novel-validator-repair（style-repair 模式）

```
subagent_type: novel-validator-repair
description: 修第<N>章风格报错
prompt:
PROJECT: <绝对项目路径>
CHAPTER: <N>
MODE: style-repair
STYLE_REPORT: <validate_style.py 的完整 stdout 输出>
MAX_ROUNDS: 2
```

**说明**：`STYLE_REPORT` 字段必须包含 validate_style.py 输出的全部 `[PASS]/[WARN]/[FAIL]` 行，agent 根据 FAIL 项定向修复。

---

## 模板 3: novel-voice-keeper

```
subagent_type: novel-voice-keeper
description: 审第<N>章对话声纹
prompt:
PROJECT: <绝对项目路径>
CHAPTER: <N>
MODE: voice-audit
```

---

## 模板 4: novel-foreshadower

```
subagent_type: novel-foreshadower
description: 评第<N>章伏笔
prompt:
PROJECT: <绝对项目路径>
CHAPTER: <N>
MODE: foreshadow-review
```

---

## 模板 5: novel-summarizer

```
subagent_type: novel-summarizer
description: 写第<N>章摘要
prompt:
PROJECT: <绝对项目路径>
CHAPTER: <N>
MODE: summarize
```

---

## 模板 6: novel-reflector

```
subagent_type: novel-reflector
description: 提第<N>章经验
prompt:
PROJECT: <绝对项目路径>
CHAPTER: <N>
MODE: reflect
```

---

## 模板 7: novel-outline-planner

```
subagent_type: novel-outline-planner
description: 规划第<N+1>章走向
prompt:
PROJECT: <绝对项目路径>
CURRENT_CHAPTER: <N>
MODE: plan-next
```

---

## 参数字典（主代理从哪取值）

| 参数 | 取值方式 |
|---|---|
| `<绝对项目路径>` | 当前项目的绝对路径，如 `<REPO_ROOT>/workspace/novels/<书名>` |
| `<N>` | 当前章节号整数 |
| `<NNN>` | N 补零到三位，如 `002` / `015` / `120` |
| `<N+1>` | 下一章号整数 |

---

## 反模式（主代理绝不这样做）

### ❌ 反模式 1：在 prompt 里重复规则

```
prompt:
你是 novel-writer，职责是写第2章。
禁止使用禁用词：顿时、紧锁...
CHANGES 必须包含 9 类字段...
PROJECT: ...
```

**为什么错**：规则已经在 agent 系统提示里了，重复只会造成冲突 + Hook 拦截（规则词 > 8）。

### ❌ 反模式 2：把 manifest 内容内联到 prompt

```
prompt:
PROJECT: ...
CHAPTER: 2
manifest 内容如下：
{
  "must_read": [...],
  ...
}
```

**为什么错**：Agent 会自己 Read manifest。内联等于预加载，破坏 progressive disclosure。

### ❌ 反模式 3：用自然语言描述替代模板

```
prompt:
请你帮我写示例书名项目的第二章，基于 manifest 文件...
```

**为什么错**：自然语言有歧义，Hook 难校验契约字段。

### ❌ 反模式 4：省略必填字段

```
prompt:
CHAPTER: 2
```

（缺 PROJECT） — **Hook 会拦截**。

---

## 主代理自检清单（每次调用前）

- [ ] prompt 第一行是 `PROJECT:` 或 `CURRENT_CHAPTER:`
- [ ] 所有 `<...>` 占位符已替换为真实值
- [ ] 路径是绝对路径（含盘符）
- [ ] 没在 prompt 里复述 agent 职责或规则
- [ ] 没塞 manifest/正文/JSON 进 prompt
- [ ] description 包含 "Writer" / "Validator" / "Voice" / "章" 等关键词（触发 Hook 契约校验）
