# Gen-Model 抽象层 · 完整工作流

## 角色分工（v2 · 2026-05-19）

| 任务类别 | 由谁负责 | 工具 |
|---|---|---|
| **内容生成（含一切创意笔触）** | **gen-model**（当前 active profile） | `gen_writer.py` / `gen_fixer.py` / `gen_creative.py` |
| **收集 / 整理 / 判断 / 裁决** | **Claude**（主代理 + sub-agent） | spawn researcher / summarizer / reflector / reading-reflector / splitter / checker / meta-judge |
| **本地脚本** | Python | scanner / build_manifest / save_state / plan_tracker / git commit |

参见 memory `feedback_genmodel_claude_role_split.md` 的完整二分清单。

## 完整流程图（cluster 级 · 故事块 12000-25000 字）

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1. 调研先行                            spawn researcher（Claude）      │
│    → .research_cache/inspiration_*.md                                │
├──────────────────────────────────────────────────────────────────────┤
│ 2. plan_tracker create write-chapter   Bash + plan_tracker.py        │
│    本地脚本                                                           │
├──────────────────────────────────────────────────────────────────────┤
│ 3. build_manifest                       Bash + build_manifest.py     │
│    本地脚本                                                           │
├──────────────────────────────────────────────────────────────────────┤
│ 4. ★ 写正文（cluster 故事块）         **gen-model**                   │
│    python gen_writer.py ...                                          │
│    输出: cluster_N_draft.txt + changes.json                          │
│    自动跑 narrative + repeat_noun scanner                            │
├──────────────────────────────────────────────────────────────────────┤
│ 5. 切章                                 spawn chapter-splitter（Claude）│
│    输出: 第006章/第006章.txt ... 第010章/第010章.txt                  │
├──────────────────────────────────────────────────────────────────────┤
│ 5.5 ★ 章标题重生（splitter 后必跑）   **gen-model**                  │
│    python gen_chapter_titles.py --chapters X-Y --high-chapters K     │
│    三档策略 80% 2-4字 / 15% 5-8字 / 5% 8-14字（基于 70 章爆款调研）  │
│    自动按 cluster_position 分级 + 强制去重 + 写「第NNN章 标题」章首 │
├──────────────────────────────────────────────────────────────────────┤
│ 6. Scanner 全量校验                     Bash + python scanner         │
│    本地脚本                                                           │
├──────────────────────────────────────────────────────────────────────┤
│ 7. reader-first reflector 1 轮          spawn reading-reflector（Claude）│
│    输出: reading_reflector_R1.json（含 issue + emergent）             │
├──────────────────────────────────────────────────────────────────────┤
│ 8. ★ 修复 issue（综合修）              **gen-model**                  │
│    python gen_fixer.py --mode comprehensive --report-file R1.json    │
│    自动跑 scanner 校验                                                │
├──────────────────────────────────────────────────────────────────────┤
│ 9. 主代理亲读关键段                    主代理（Claude）               │
│    Read ch1-N + 给真实印象                                           │
├──────────────────────────────────────────────────────────────────────┤
│10. ★ 主代理亲读后微调（如需）         **gen-model**                   │
│    python gen_fixer.py --mode polish --instructions "..."            │
├──────────────────────────────────────────────────────────────────────┤
│11. ★ 字数扩写（如有章 < 2500）        **gen-model**                   │
│    python gen_fixer.py --mode word-count --target-min 2500           │
├──────────────────────────────────────────────────────────────────────┤
│12. validator-check（剧情/style 违规）  spawn validator-checker（Claude）│
│    输出: brief JSON → gen_fixer.py --mode validator-repair          │
│    ★ 修复段落                          **gen-model**                  │
├──────────────────────────────────────────────────────────────────────┤
│13. voice-check（对话声纹）              spawn voice-checker（Claude）  │
│    输出: brief JSON → gen_fixer.py --mode voice-fix                 │
│    ★ 修对话                            **gen-model**                  │
├──────────────────────────────────────────────────────────────────────┤
│14. save-state 流水线                    Bash + save_state.py          │
│    parse + apply + git commit + report                              │
└──────────────────────────────────────────────────────────────────────┘

★ = gen-model 接管步骤
```

## 灵感卡 / 走向卡流程（开书 + 每 save-state 后）

### 灵感卡（开书时）
```
1. 用户输入题材关键词
2. 主代理 spawn researcher → .research_cache/inspiration_<topic>_<时间>.md
3. ★ python gen_creative.py --mode brainstorm \
      --topic "<题材>" --count 3 \
      --research <cache> --style-ref <skill_FINAL.md>
4. gen-model 返回 JSON（3 张差异化灵感卡）
5. 主代理展示给用户选
```

### 走向卡（每章 save-state 后）
```
1. save-state 完成
2. 主代理 spawn outline-planner（Claude）→ 列卡片结构骨架 JSON
3. ★ python gen_creative.py --mode outline_card \
      --project <path> --next-chapter <N+1> --count 2 \
      --skeleton <骨架 JSON>
4. gen-model 返回完整卡片（骨架字段保留 + 创意字段填好）
5. 主代理展示给用户选
```

## 三件套对照

| 脚本 | mode 数 | 触发场景 |
|---|---|---|
| `gen_writer.py` | -（单功能：写正文） | cluster 故事块生成 |
| `gen_fixer.py` | 5（comprehensive / polish / word-count / validator-repair / voice-fix） | reflector 后 / 亲读后 / 字数不足 / checker brief 后 |
| `gen_creative.py` | 5（brainstorm / outline_card / voice_sample / volume_arc / world_entry；v1 实现前 2 个） | 开书灵感卡 / 每章走向卡 / distill-character / outline 等 |

## 配置管理

```bash
python core/scripts/gen_model.py list              # 列所有 profile
python core/scripts/gen_model.py show              # 当前 active 详情
python core/scripts/gen_model.py switch <name>     # 切换 active
python core/scripts/gen_model.py add <name>        # 新增 profile 模板
```

详细字段定义参见 `core/scripts/gen_writer.README.md`。

## 模型质量评估

gen-model 接管前**必须**做对照评估：
- 同一 cluster brief 跑当前 profile + Claude novel-writer agent（旧路径），对比
- 主代理亲读两个版本，给质量印象
- 不达标的 profile → 切换到下一个候选（DeepSeek/Kimi/GLM/Qwen/Claude 各家可对比）

## 反向自检（适用于所有 agent）

- 看到「应该写正文 / 改对话 / 写灵感卡 / 写走向卡正文 / 写角色样本 / 写卷描述」类任务 → 这是 **gen-model** 的活
- 看到「应该调研 / 切章 / 摘要 / 反思 / 评估 / 路由 / 与用户对话」类任务 → **Claude**
- 当前 active gen-model：`python core/scripts/gen_model.py show`
