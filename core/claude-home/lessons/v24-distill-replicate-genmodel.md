# L10: 蒸馏复刻强制 gen-model（v22.cluster.3 · 2026-05-25）

## 问题

`/distill-style` 的 phase-2 / phase-5 复刻测试用 Claude sub-agent（`general-purpose`）生成复刻段。
但正式写作（`/write-chapter` → `gen_writer.py`）走的是 **gen-model**（OpenAI 兼容协议外部模型，如 deepseek_v4_pro / pie_xian）。

两栈不一致 → skill v0→v1 升级是针对错的模型迭代。

## 根因链

1. Claude (Opus 4.7) 对自然语言风格规则的理解 ≠ deepseek / qwen / gemini 的理解
2. Claude 上 skill v0 复刻成功 → 报告"通过" → 进入 v1 升级
3. 但同 skill 给 gen-model 跑可能完全跑歪（指令遵循度 / 词汇分布 / 句长偏好都不同）
4. v1/v2/... 迭代积累的修正全是针对 Claude，对 gen-model 反而可能放大偏差
5. 最终 skill_FINAL 注入 gen_writer → 写出来的不像源作者

## 解法：三层防御

### L1 契约层
- `distill-style.md` phase-2 / phase-5 章节明确：复刻必须 `python core/scripts/distill_replicate.py`
- 禁止 spawn Agent 做复刻产出

### L2 工具层
- 新建 `core/scripts/distill_replicate.py`：唯一合法入口
- 内部用 `gen_model_loader.py` 加载 `.env` 中 `GEN_MODEL_ACTIVE` 指向的 profile
- 失败按 `GEN_MODEL_FALLBACK_CHAIN` 尝试
- 产出 `.txt` 正文 + `.meta.json` sidecar（含 `profile_used` / `model_used` / 字数 / 耗时）

### L3 校验层
- `pretooluse_agent_gate.py` **规则 11**：检测以下模式则 exit 2：
  - description 含「复刻测试 / v{N} 复刻 / phase-2 复刻 / phase-5 复刻」
  - description 含「复刻」+ prompt 含 `skill_v` 或 `复刻测试/v` 或 `test_*_replica`
- 提示用 `distill_replicate.py` 正确入口
- 紧急旁路：prompt 加 `DISTILL_REPLICATE_BYPASS=1`（救火用）

## 用法示例

```bash
python core/scripts/distill_replicate.py \
  --style-skill workspace/styles/惊悚乐园/skill_v0.md \
  --type opening \
  --output workspace/styles/惊悚乐园/复刻测试/v0_round1/test_opening_replica.txt \
  --ref-chapter workspace/styles/惊悚乐园/原文/第001章.txt \
  --target-words 1200
```

支持的 `--type`：`opening / battle / psychology / dialogue / description / transition`

## 实测数据（2026-05-25 惊悚乐园 v0_round1）

| Type | Profile | Model | CJK 字数 | 耗时 |
|---|---|---|---|---|
| opening | deepseek_v4_pro | deepseek-v4-pro | 1509 | 40.3s |
| battle | deepseek_v4_pro | deepseek-v4-pro | （进行中） | - |
| psychology | deepseek_v4_pro | deepseek-v4-pro | （已完成） | - |

每段 1200 ± 300 字，单次调用 40-60s 完成。比 Claude sub-agent（每段 7-15 min）快 10 倍。

## 翻车实例

2026-05-25 惊悚乐园 phase-2 第一轮用 Claude sub-agent 复刻 opening/battle/psychology 三段。
agent 自评全部 PASS（字数 / 禁词 / 段长全达标），但实际只代表 Claude Opus 4.7 能模仿 skill_v0。
切回 gen-model（deepseek_v4_pro）后，输出风格特征**有显著差异**（叙述者跳出频率 / 系统【】嵌入密度 / ACG 引用率 都低 30-50%）。
若没切回，v1 升级方向会完全错。

详见 memory `feedback_distill_replicate_must_match_writer_stack`。
