---
description: 当用户从零开始想写一部小说，且需要主代理引导走完从风格基线到逐故事块产出的端到端流程时使用
---

## 创作与 Gen-Model 分工层（v29）

正文创作笔触由 **Claude 亲笔**承载（v29 · novel-writer agent 逐场景写），gen-model（gemini）负责**分段等体量润色**与修复类步骤；Claude 主代理负责编排、检查、梳理和裁决。

| 步骤 | 唯一执行方式 |
|---|---|
| 灵感卡 | `/outline` step 3 spawn `novel-outline-planner MODE=brainstorm`：Claude 读调研缓存亲笔写卡 → `gen_creative.py --mode brainstorm --verify` 确定性验收（source_refs 必须真实存在于调研缓存） |
| 正文 | `/cluster-write` step 2 spawn `novel-writer`：Claude 亲笔逐场景写 `claude_scenes/` → `gen_writer.py --project <path> --cluster N` 调 gemini 分段等体量润色出终稿 |
| 违规修复 | checker 生成 brief，`gen_fixer.py` 按 brief 改 cluster 草稿 |
| 对话 voice 修复 | `novel-voice-checker` 生成 brief，`gen_fixer.py --mode voice-fix` 修 |
| 亲读后微调 | `gen_fixer.py --mode polish --instructions "<自由文本>"` |
| 字数补写 | 回到 cluster 草稿层把场景写透，再由 splitter 重新切章 |

**主代理仍负责**：调研（researcher）/ 摘要（summarizer）/ 反思（reflector）/ 阅读反思（reading-reflector）/ splitter 调度 / judge / save-state 状态整理 / 用户对话 / 决策。

**当前 active gen-model**：`python core/scripts/gen_model.py show`；切换：`gen_model.py switch <name>`。

---

你是若渝AI，执行完整的小说写作流程：

$ARGUMENTS

---

# /write 写小说完整流程

## 第一步：选择或蒸馏作者风格

**方式A：从风格库选择（用户输入编号）**
- 检查 `风格库/` 目录下的 *.json 文件
- 将用户选择的风格文件复制到 `_数据库/作者风格.json`
- 同时复制对应的 `_skill.md` 文件
- 跳过蒸馏，直接进入第二步

**方式B：蒸馏新风格（用户提供参考小说）**
- 执行 /distill-style 命令
- 完成后自动进入第二步

**方式C：自由模式（用户说"自由模式"）**
- 不使用参考风格
- 直接进入第二步

---

## 第二步：AI 生成灵感 + 用户选择

**灵感生成规则：**
- 根据蒸馏出的风格特征（类型、节奏、情绪模式）自动生成 3 个匹配该风格的故事灵感
- 每个灵感包含：类型标签 + 一句话梗概（30字以内）
- 灵感要多样化（不同题材/角度），但都适合该作者风格
- 用户选择后可以修改，AI 根据反馈调整
- 用户说「换一批」则重新生成 3 个完全不同的

**输出格式：**
```
根据这个风格，我为你生成了 3 个故事灵感：

[1] 📖 [类型] — [一句话梗概]
[2] 📖 [类型] — [一句话梗概]
[3] 📖 [类型] — [一句话梗概]

选一个（输入编号），或者：
  · 「换一批」→ 重新生成 3 个
  · 「1 但是把主角改成女的」→ 基于选择修改
  · 直接说你的想法 → 我来调整
```

---

## 第三步：确认 + 推荐模型

**用户确认后输出：**
```
好嘞！
📖 [类型] · cluster-only · 字数随 splitter 自然切分
风格参考：[参考小说名] 的写作风格
正文由 Claude 亲笔创作（继承当前会话模型），建议用最强的 Claude 模型写作，效果最好～
马上开写！
```

**模型切换提示：**
```
准备开写了！正文由 Claude 亲笔创作（novel-writer agent 继承当前会话模型），
用 /model 切到当前会话可用的最强 Claude 模型，创作质量最好：
  · /model → 选最新最强的 Claude 模型
  · 或直接用当前模型开写
```

---

## 第四步：生成大纲 + 初始化数据库

执行 /outline 命令：
0. 如有 `_数据库/作者风格.json`，所有后续写作必须遵循该风格档案
1. 生成卷级大纲（每卷的大势/核心悬念/主题），不细化到逐章
2. 初始化 34 个核心子系统 JSON
3. 终端展示卷级结构后，**直接开写第一个故事块**

**⚠️ 硬性规则：大纲完成后不要追加章级细化步骤——直接进入 `/cluster-write`！**

理由：
- 剧情是动态的，每个故事块走向由走向卡片决定
- 提前细化逐章剧情没有意义（会被用户选择改变）
- 大纲只定"大势"（卷级结局），"小势"（cluster 走向）逐块生成
- 章节数量可能与预期不同（这是正常的，由剧情自然决定）

---

## 首块开场规则（cluster_001 特殊处理）

cluster_001 是读者留存生死线。它仍由 `/cluster-write` 生成整块草稿，但开场和前几次切点必须满足：

- **第一句话**：冲突 / 悬念 / 反差 / 冲击，禁止日常流水账、天气、背景介绍或自我介绍开头。
- **前 100 字**：让读者明确感到“发生了什么？”。
- **主角出场**：必须在行动中出场，用选择和反应展示人设。
- **首个冲突**：在 cluster_001 前段建立，并在本块内至少升级一次。
- **伏笔**：本块至少有一个具体可回收细节，不能只是“主角有秘密”这种抽象描述。
- **切章后检查**：splitter 切出的前几个物理章节必须保留强钩子；若开场或切点不合格，回到 cluster 草稿层修复，正文修复只在 cluster 草稿层执行。

## 第五步：逐故事块写作循环（v26 · cluster-only）

**⚠️ 硬性规则：写完一个 cluster 后不要问"要继续吗"——直接执行 cluster-save-state，然后停在走向卡选择；用户选择落盘后才写下个 cluster。**

**链路可扩展规则**：允许把验证过、明显适合的功能、模型或成熟开源/论文机制加入本流程，但必须作为 `/outline`、`/cluster-write`、`/cluster-save-state` 或走向卡阶段的 required plan step / required 子步骤落地，并同步 plan、agent 合约、命令文档和测试。

每 cluster 的完整循环（自动推进到走向卡；走向卡是唯一停顿点）：

```
┌─ 走向卡前调研（cluster_002+ 必跑）─────────────┐
│ spawn novel-researcher TASK_TYPE=outline       │
│ 调研下一 cluster 的方向 / 同题材热点 / 雷区     │
└────────────────────────────────────────────────┘
         ↓ 不停顿，直接执行
┌─ 执行 /cluster-write CLUSTER_ID=<key>（7 步）─┐
│ ⚠️ 禁止在主会话中直接生成正文！               │
│ 必须由 cluster-write 调度器走完 7 步：         │
│   1. build_manifest                            │
│   2. novel-writer ECAS（产 cluster 整块草稿）  │
│   3. 双轨质检（audit + reflector 全 cluster）  │
│   4. novel-voice-checker（全 cluster）         │
│   5. foreshadower + reflector + summarizer     │
│   6. ★最后才 splitter + titles + 平铺 changes │
│   7. plan-end                                  │
│ 主会话只收到"✅ cluster <key> 写好了"          │
└────────────────────────────────────────────────┘
         ↓ 不停顿，直接执行
┌─ 执行 /cluster-save-state CLUSTER_ID=<key>───┐
│ cluster 级 save-state 流水线（step 以 plan 为准）│
│ → 应用 cluster_changes → cluster Git commit    │
│ → cluster 级 evaluator/updater → 涌现下个 brief│
│ ★ 必须执行，不能跳过！                         │
└────────────────────────────────────────────────┘
         ↓ 展示卡片，等待用户选择
┌─ 走向卡（唯一停顿点）─────────────────────────┐
│ 展示 2-3 张下个 cluster 候选 brief             │
│ 等待用户选择；必须记录显式选择 artifact         │
└────────────────────────────────────────────────┘
         ↓ 用户选择后，直接进入下个 cluster 循环
```

**绝对不要做的事：**
- ❌ 写完一个 cluster 后问"要继续写下一个吗？"
- ❌ 展示走向卡后问"确认这个方向吗？"
- ❌ cluster-save-state 流水线未完整完成就进入后续动作
- ❌ 未展示走向卡、替用户选择或缺少选择 artifact
- ❌ 数据库初始化未完成就开始写
- ❌ 把正文贴到终端
- ❌ 🔴 强禁：离开 cluster-only 创作与状态链路

**必须做的事：**
- ✅ 每 cluster 写完后立即执行完整的 cluster-save-state plan
- ✅ cluster-save-state 完成后展示走向卡
- ✅ 用户选择写入 artifact 后直接写下个 cluster（不再二次确认）
- ✅ 第一 cluster 之前必须先完成数据库初始化（/outline）
- ✅ cluster 内章数由 splitter 在 step 6 切定，不预锁

---

## Cluster 审计纪律

cluster-write / cluster-save-state 内部的质检只服务唯一 cluster 链路：

- cluster 草稿阶段统一走 `audit_hub.py --mode cluster --cluster-id <key>` 和相关 cluster 级 scanner。
- 修复 brief 指向 `章节/cluster_<key>_draft/cluster_<key>_draft.txt`，由 `gen_fixer.py` 在 cluster 草稿层修；修完再由 splitter 切章。
- hard_gate 是硬错误，必须修复后才能继续；风格/工艺类 issue 可记录 waiver，但必须在 cluster brief / JudgeReport 中有具体理由。
- required step 产物缺失或不合格时必须停在当前 plan；不得把缺失能力写成空实现或标记为成功。

---

## Git 版本快照节点（自动执行）

每本小说都是一个独立的 Git 仓库（/outline 初始化时自动 git init）。
在以下关键节点自动提交快照，无需用户干预：

| 触发节点 | Commit 类型 | Message 示例 |
|----------|-------------|--------------|
| 项目初始化（/outline 末步） | chore | `chore: 初始化项目 + 34 个数据库文件` |
| 大纲生成完成（/outline 末尾） | feat | `feat: 生成大纲（3 卷）` |
| 故事块 Git 快照完成（cluster-save-state 的 git-commit-cluster required step） | feat(cluster-N) | `feat(cluster-001): 5 章 (ch1-ch5)` |
| 风格蒸馏完成（/distill-style） | feat | `feat: 蒸馏作者风格（v3 / 2033 章）` |
| 角色深度蒸馏（/distill-character） | feat | `feat: 蒸馏角色 李若渝 (v2 / ch 1-12)` |

**好处：**
- 用户可以回溯任意章节的历史版本（`git log` / `git checkout`）
- 设定修改后如果不满意，可以 `git revert` 回到调和前
- WAL 崩溃恢复后，通过 git 可以确认哪些章节已快照
- 不占用用户精力，全流程自动化

**硬性规则：**
- ❌ 不做 push/pull/force/reset --hard 等破坏性操作
- ❌ 不修改用户的全局 git config
- ❌ 无 git 环境时继续写作并假装已提交；git commit 是 cluster-save-state 的 required step，失败必须停在当前 plan
- ✅ 所有 commit 仅在项目目录内操作（`git -C "小说_书名"`）

---

## 写作 prompt 归属（v26 · cluster mode）

> 🔴 主代理**不持有也不发出任何章级写作 prompt**。正文一律由 `/cluster-write` 调度器在 step 2 spawn `novel-writer MODE=ecas` 时构造（写整 cluster 草稿）。

- writer prompt（cluster brief + scope_summary + scene_storyboard + 作者风格约束 + 锁定事实 + voice_pack + 风格参考段 + 用户走向选择等）由 cluster-write 调度器组装，详见 `cluster-write.md` step 1-2。
- 输出契约由 splitter 在 step 6 落地：writer 先产 `章节/cluster_<key>_draft/cluster_<key>_draft.txt` + `cluster_<key>_changes.json`，splitter 切章后平铺为 per-chapter `第NNN章/第NNN章.txt` + `第NNN章_changes.json`（正文/数据分离，纯正文无 `---CHANGES` 分隔符）。
- 🔴 v29 正文生成：Claude 亲笔逐场景写草稿（novel-writer agent）→ gemini 分段等体量润色（gen_writer.py）出终稿；writer 链不被告知目标章数，章数由 splitter 按字数硬范围（3000-4500 CJK/章）在 step 6 切定。

**主代理只需**：spawn cluster-write 调度器、收到「✅ cluster <key> 写好了」后立即 spawn cluster-save-state，绝不在主会话里直接生成正文。

---

## 第六步：完成

1. 确认 `/cluster-save-state` 所有 required steps + plan-end 已完成，走向卡选择 artifact 已落盘。
2. 若用户要求成品文件，执行 `/export`，由 `core/scripts/export_book.py` 生成 `exports/<书名>_全文_<章数>章.txt`。
   `/write` 不生成成品全文、不追加导出文件；成品文件只由 `/export` 的导出脚本生成。
3. 汇报：「已完成到 cluster <key>，状态已保存；导出文件见 <path>」（未执行 `/export` 时只汇报状态完成，不声称已有全文）。

---

## 断点恢复

执行 /session-start 加载所有状态，然后从第五步继续循环。

---

## 调用失败处理

连续失败时提示：
```
这个模型好像不太顺，用 /model 切到当前会话可用的最强 Claude 模型试试？
（若是 gemini 润色反复失败，改用 `gen_model.py switch <name>` 切换 gen-model）
```

---

**不要做的事：**
- ❌ 把正文贴到终端
- ❌ 每个 cluster 后问要不要继续（唯一停顿点是 cluster 走向卡选择）
- ❌ 展示大纲/设定/世界观长文
- ❌ 在主会话中直接生成正文
- ❌ 暴露或启用章级创作入口
- ✅ 按 cluster 自动推进，只显示进度、走向卡和最终导出位置
- ✅ 每个 cluster 写完立即释放子任务，主会话保持轻量
