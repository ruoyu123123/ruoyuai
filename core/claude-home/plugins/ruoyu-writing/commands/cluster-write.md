---
description: 按故事块（cluster）整块写作 · v24 倒置流水线 + v2 cluster 单层架构（2026-05-28）· 整块迭代修完才拆章
---

你是若渝AI的**故事块写作调度器**。你不写作、不校验、不审对话——你只按顺序调度 5 个专精 agent + 4 个脚本。

> **v2 cluster 化方案（2026-05-28）**：13 cluster 视野 scanner 并行跑（9 升维 + 4 新）· 切章是纯格式输出 0 质检 · CLUSTER_MODE=1 env 自动传递 · 详见 `workspace/_temp_research/system_redesign_detection_layer.md` + memory `feedback_full_system_cluster_centric`。

$ARGUMENTS

> **三段式纪律**：本命令所有 plan-step 必须遵守「研 → 干 → 反思」三段式。
> 详见 [core/claude-home/HOOKS_AND_REFLECTION.md](../../core/claude-home/HOOKS_AND_REFLECTION.md)。

---

# 🔴 设计哲学（v24 倒置流水线 · 必读）

**问题**：旧 `write-chapter` 调度器在 ECAS 模式下 writer 写整 cluster 草稿（5 章约 19000 字）后**立刻** splitter 切成单章，然后质检按单章 ch1/ch2/.../chN 各跑一遍。后果：

| 旧流程后果 | 根因 |
|---|---|
| 同一重复词 ch1 修了，ch2-5 仍存在 | splitter 是切位置，不是去重 |
| 跨章 voice 漂移检测不到 | 单章 reflector 看不见 ch2-5 上下文 |
| 跨章伏笔接力问题被漏 | 单章 audit 无 cluster 视野 |
| 5 章累计修 15-25 轮 | 每章独立 3-5 轮 |
| ch1 修完后切点错位 | splitter 不知道改了什么 |

**v24 修正**：所有改进**全程在 cluster 草稿层完成**——单一文本对象 `cluster_{key}_draft.txt`，跨场景一致性 + 跨章伏笔接力 + 跨角色 voice 全在一个上下文里跑完。**全 clean 后才** splitter 切章 + 起标题 + 平铺 per-chapter changes。**章节只是输出格式**，不是迭代单位。

**核心原则**：
- 🔴 writer 产出 `cluster_draft.txt` 后**禁止立即切章**（splitter 推迟到 step 6）
- 🔴 audit_hub / reading-reflector / voice-keeper / foreshadower 全部走 `--mode cluster` / `MODE=ecas`
- 🔴 1 个 cluster = 1 次 cluster-write plan（不是 1 章 1 次 write-chapter plan）
- 🔴 title 在 step 6 末尾 splitter 后再生成（chapter 内容已 clean）

---

# 🛡️ Plan 强制规划

**所有 7 步必须挂在 plan 上**——start 前必须 `plan-create` 拿 PLAN_ID，每步完成 `plan-step --n N`，末尾 `plan-end`。Hook 已强制本命令的 PLAN_ID。

```bash
# 写作前先创建 plan
PLAN_ID=$(python core/scripts/plan_tracker.py create \
  --command cluster-write \
  --project "<书名>" \
  --key "<cluster_key>")  # cluster_key 如 "001" / "002"
echo "PLAN_ID=$PLAN_ID"
```

所有 Agent 调用 prompt 顶部必须加：

```
PLAN_ID: $PLAN_ID
STEP: <当前步骤号>
```

---

# 流水线架构（7 步 · 倒置）

```
0.   world_evolution_apply_card.py   （用户选定走向卡 → 触发世界涟漪 → 世界先动一格）
     ↓
1.   build_manifest.py              （cluster 起首章 manifest · 注入 cluster brief + 全 25+ 子系统状态）
     ↓
2.   novel-writer MODE=ecas         （写整 cluster 草稿 · ★禁止 splitter）
     ↓
3.   cluster 级双轨质检（强制）
     ├─ 机械: audit_hub.py --mode cluster --cluster-id <key> --auto-fix --waivers
     │         hard_gate 不可豁免 · advisory 凭理由豁免
     └─ 阅读: novel-reading-reflector MODE=ecas ROUND=1→...
              连续 3 轮 0 issue 才放行（SRE 风格健康检查）
     ↓
4.   novel-voice-checker MODE=cluster   （整 cluster 对话声纹 · 跨场景 voice 漂移）
     ↓
5.   novel-foreshadower + novel-reflector + novel-summarizer   （cluster 级三 agent 串行）
     ├─ foreshadower: 整 cluster 伏笔评估
     ├─ reflector: 整 cluster 经验沉淀
     └─ summarizer: cluster 级摘要
     ↓
6.   ★ 最后才切章
     ├─ novel-chapter-splitter MODE=ecas_multi_chapter（含 narrative_mode=in_medias_res）
     ├─ gen_chapter_titles.py --chapters <range_from_splitter_wal>   （normal/mid/high 三档）
     └─ split_cluster_changes.py --cluster <key>                     （平铺 cluster_changes 到 per-chapter）
     ↓
7.   报告 + plan-end → 准备进 cluster-save-state
```

---

## 🌍 Step 0：走向卡 → 世界涟漪（cluster_002+ 必跑 · cluster_001 跳过）

cluster_001 是首块，无上一 cluster 走向卡，跳过 step 0。

cluster_002+ 之前主代理在 cluster-save-state 末尾让用户选过 A/B/C 走向卡，本步必须先跑：

```bash
# 取上一 cluster 末章号
LAST_CH=$(python core/scripts/cluster_emergence_engine.py last-ch "<项目路径>" --cluster <prev_key>)
python core/scripts/world_evolution_apply_card.py "<项目路径>" "$LAST_CH" <chosen_label>
```

效果：把 `cards[label].ripple_match` 落到 世界状态.json + 涟漪 log。

**例外**：
- cluster_001 → 跳过
- 用户「全自动」无走向卡 → 跳过
- 项目无 涟漪规则.json → 脚本自动 SKIP

---

# 第 1 步：build_manifest + style_directive

cluster 起首章 manifest 注入（含 cluster brief + 25+ 子系统状态）：

```bash
# 取本 cluster 起首章号（cluster_001 = 1 / cluster_002 起首 = 上 cluster 末章 + 1）
START_CH=$(python core/scripts/cluster_emergence_engine.py start-ch "<项目路径>" --cluster <key>)
python core/scripts/build_manifest.py "<项目路径>" "$START_CH"
```

- exit 0 → 继续
- exit 2（预检失败）→ 把 fatal 列表展示给用户，停止调度

**plan-step 1**（manifest + style_directive 都是必须落地的文件）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1
```

模板已配 expected_outputs，缺失自动 FAIL。

---

# 第 2 步：novel-writer ECAS 模式（★禁止 splitter）

用 Agent 工具启动 `novel-writer`，prompt 只含五行契约 + 一行 MODE 强制：

```
PLAN_ID: $PLAN_ID
STEP: 2
PROJECT: <项目路径>
CHAPTER: <START_CH>
MANIFEST: <项目路径>/_数据库/.manifest/ch_<START_CH 三位>.json
MODE: ecas
```

writer 行为：
- 产出 `章节/cluster_<key>_draft/cluster_<key>_draft.txt`（整 cluster 草稿 · 13000-22000 CJK）
- 产出 `章节/cluster_<key>_draft/cluster_<key>_changes.json`（cluster 级 factual/self_eval/伏笔变更）
- 🔴 **禁止自行调 splitter**（v24 流水线：splitter 推迟到 step 6）

writer 返回后，检查两文件落地。任一缺失 → 停止，向用户报告（writer 契约违规）。

**plan-step 2**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2
```

---

# 第 3 步：cluster 级双轨质检（强制）

## 3.1 机械轨：audit_hub --mode cluster

```bash
python core/scripts/audit_hub.py "<项目路径>" --mode cluster --cluster-id <key> --auto-fix \
  --waivers "<项目路径>/章节/cluster_<key>_draft/cluster_<key>_changes.json"
```

退出码语义（同 write-chapter）：
- 0 = pass / waived
- 1 = auto_fixed → 进 3.2
- 2 = needs_agent → 读 pending_agent · spawn 对应 agent · 修完重跑
- 3 = fatal → 停止

**关键差异**：`--mode cluster` 让 audit_hub 把整 `cluster_draft.txt` 当 1 个文本对象跑，**跨场景的伏笔/anchor/voice/repeat-phrase 完整性都能被检出**，不会再像单章模式那样错过。

派单时 agent prompt 加 `CLUSTER_ID: <key>` + `MODE: cluster`，让被派 agent 也跑 cluster 视野。

## 3.2 阅读轨：novel-reading-reflector MODE=ecas（强制）

```
Agent 启动 novel-reading-reflector:
PLAN_ID: $PLAN_ID
STEP: 3
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: ecas
ROUND: 1
```

reflector 8 维扫整 cluster：
- 段首单调含全主语词 / voice 漂移 / POV 一致 / 信息密度 / 节奏 / 对话工艺 / 互动质感 / 塑料感

verdict 处理：
| verdict | 处理 |
|---|---|
| pass + consecutive_clean ≥ 3 | 进 step 4 |
| pass + consecutive_clean < 3 | spawn round+1 复核 |
| fail | spawn validator-checker 改 cluster_draft.txt · 然后 spawn reflector round+1 |
| escalate_human | 报告用户决断 |

**SRE 风格预算**：MAX_ROUNDS=5。如果 5 轮后仍未 3 连 clean，且 reflector 自己 `final_recommendation_to_main_agent` 建议 final_pass（边际收益接近零），主代理可选择放行（写入 plan 日志理由）。

输出文件：`_数据库/.reading_reflection/cluster_<key>_round_<N>.json`

**plan-step 3**（双轨产 audit json + reflector json 都必须落地）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3
```

---

# 第 4 步：cluster 级 voice-keeper

```
Agent 启动 novel-voice-checker:
PLAN_ID: $PLAN_ID
STEP: 4
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

voice-checker 跨整 cluster 跑：
- 全角色 voice_pack 命中率
- 跨场景 voice 漂移检测（同一角色在 scene1 和 scene7 是否漂）
- catchphrase 过度使用 / banned_phrases 越界

返回有改写 brief → 调 `gen_fixer.py --mode voice-fix --brief <path>` 改 `cluster_draft.txt`。如有改 → 再跑一次 audit_hub --mode cluster 确认。

**plan-step 4**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4 --skip-output
```

---

# 第 5 步：cluster 级 foreshadower + reflector + summarizer（三 agent 串行）

## 5.1 foreshadower

```
Agent 启动 novel-foreshadower:
PLAN_ID: $PLAN_ID
STEP: 5
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

整 cluster 伏笔评估：
- 本 cluster 埋设的伏笔（foreshadowing_to_plant）是否真出现在正文
- 本 cluster 回收的伏笔（promises/secrets/pledges）是否兑现
- 给 step 6 splitter 切章时哪些段属于「不能切散的伏笔配对」

## 5.2 reflector

```
Agent 启动 novel-reflector:
PLAN_ID: $PLAN_ID
STEP: 5
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

提取 cluster 级写作经验（成功/失败模式）→ 写入 `_数据库/写作经验.json`。

## 5.3 summarizer

```
Agent 启动 novel-summarizer:
PLAN_ID: $PLAN_ID
STEP: 5
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

产出 cluster 级摘要 → `_数据库/.wal/cluster_<key>_summary.json`，供下游 `cluster-save-state` 用。

**plan-step 5**（三 agent 串完 + summary 落地）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 5
```

---

# 第 6 步：★最后才切章（splitter → titles → per-chapter changes）

## 6.1 spawn novel-chapter-splitter

```
Agent 启动 novel-chapter-splitter:
PLAN_ID: $PLAN_ID
STEP: 6
PROJECT: <项目路径>
DRAFT: <项目路径>/章节/cluster_<key>_draft/cluster_<key>_draft.txt
CLUSTER_RANGE: <START_CH>-<END_CH>
NARRATIVE_MODE: <linear|in_medias_res>   # cluster_001 默认 in_medias_res · 其余默认 linear
CLIMAX_HINT_SCENE_INDEX: <从 事件簇.json 取>
```

splitter 按 chapter_count_estimate + 切点评分算法切 N 章，产出：
- `章节/第<NNN>章/第<NNN>章.txt` × N（纯正文）
- `章节/第<NNN>章/第<NNN>章_changes.json` × N（占位 · 待 6.3 平铺）
- `_数据库/.wal/splitter_cluster_<key>_decisions.json`（切点 WAL · 记录每章范围）

## 6.2 gen_chapter_titles（normal/mid/high 三档）

```bash
# 从 splitter_wal 读 chapter_range
python core/scripts/gen_chapter_titles.py \
  --project "<项目路径>" \
  --chapters <START_CH>-<END_CH> \
  --high-chapters <绝对高潮章号>  # 由 fate_engine/foreshadower 标定
```

三档策略（70 章爆款调研支撑 · 见 memory `feedback_splitter_post_chapter_title_regen`）：
- 80% normal（2-4 字）
- 15% mid（5-8 字）
- 5% high（8-14 字 史诗钩子）

## 6.3 split_cluster_changes 平铺

```bash
python core/scripts/split_cluster_changes.py "<项目路径>" --cluster <key>
```

把 `cluster_changes.json` 按 splitter_wal 的切点拆成 N 个 `第<NNN>章_changes.json`：
- factual 段（人物状态/世界状态/伏笔变更）按发生章节落在对应 _changes.json
- self_eval / waivers 按段所在章号分配

**plan-step 6**（splitter WAL 必须落地）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 6
```

---

# 第 7 步：报告 + plan-end

```
📦 cluster <key> 写作完成

══ 流水线 ══
  1. build_manifest: ✅
  2. Writer (ECAS): <CJK 总数> / 切前未拆
  3. 双轨质检:
       - 机械: audit_hub --mode cluster <verdict> | 自动修 <a> / 派 agent <p> / 豁免 <w>
       - 阅读: reflector <N> 轮 → 最终 verdict=<pass|final_pass>
  4. Voice-keeper: <改写 m 段 · 0=无>
  5. Foreshadower: 埋 <i> 兑 <j>
     Reflector: 沉淀 <成功 X / 失败 Y> 经验
     Summarizer: cluster 摘要 <字数>
  6. 切章:
       - splitter: 切 <N> 章 (ch<S>-ch<E>) | narrative_mode=<...>
       - titles: <ch1 标题 / ch2 标题 / ... / chN 标题>
       - per-chapter changes: 平铺完成

══ 下一步 ══
  → /cluster-save-state 保存状态 + 涌现下个 cluster
```

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 7 --skip-output
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

`plan-end` 返回非 0 ⇒ 上方流水线有步骤漏跑，立即向用户报告**不要假装完成**。

---

# 📋 完成检查清单

向用户报告"cluster <key> 写作完成"前必须自验：

- [ ] `plan_tracker.py status $PLAN_ID` 显示 7 个 required 步骤全部 `[x] completed`
- [ ] `plan_tracker.py end $PLAN_ID` 返回 exit 0
- [ ] `_数据库/.manifest/ch_<START_CH>.json` 落地
- [ ] `章节/cluster_<key>_draft/cluster_<key>_draft.txt` 落地（writer 产出 cluster 草稿，非空）
- [ ] `章节/cluster_<key>_draft/cluster_<key>_changes.json` 落地
- [ ] `_数据库/.audit/cluster_<key>_audit.json` 落地，最终 verdict ∈ {pass, waived, auto_fixed}
- [ ] `_数据库/.reading_reflection/cluster_<key>_round_<N>.json` 落地，最终 verdict ∈ {pass, final_pass}
- [ ] `_数据库/.wal/cluster_<key>_summary.json` 落地
- [ ] `_数据库/.wal/splitter_cluster_<key>_decisions.json` 落地
- [ ] N 个 `章节/第<NNN>章/第<NNN>章.txt` 全落地
- [ ] N 个 `章节/第<NNN>章/第<NNN>章_changes.json` 全落地（含 factual/self_eval 真实数据，非 placeholder）
- [ ] N 个章标题已重生（normal/mid/high 三档分布）

任何一项不达 → 不允许声称"cluster 写作完成"。

---

# 硬性纪律（调度器的边界）

- 🔴 **你不 Write 任何章节内容** — Writer 的事
- 🔴 **你不 Edit cluster_draft.txt** — validator-checker → gen_fixer.py 的事
- 🔴 **你不在 step 6 之前调任何 splitter** — 这是 v24 倒置流水线的核心纪律
- 🔴 **你不跳过任何 agent** — required 步骤一个不漏
- 🔴 **质检全程 `--mode cluster` / `MODE=ecas`** — 不允许在单章视野下做评估

你唯一能做的是：
1. Bash 调用 build_manifest / world_evolution_apply_card / audit_hub --mode cluster / gen_chapter_titles / split_cluster_changes / plan_tracker 等脚本
2. Agent 工具启动专精 agent（含 audit_hub exit 2 时按 pending_agent 清单派单）
3. 根据返回决定下一步
4. 向用户汇报

---

# Agent 不可用时的降级

`.claude/agents/` 下对应 agent 定义缺失或调用失败：

- Writer 失败 → 停止，报告用户
- audit_hub --mode cluster 致命错误（exit 3）→ 停止
- reading-reflector 失败 → 跳过阅读轨，记入报告（**且仅当机械轨已 pass**才能跳）
- voice-keeper 失败 → 跳过审查（非关键路径，记入报告）
- foreshadower/reflector/summarizer 失败 → 跳过该 agent，记入报告（不阻塞切章）
- splitter 失败 → 停止，报告用户（章节产物缺失 = 整 cluster 无法消费）

---

# vs write-chapter（旧调度器）差异速查

| 维度 | write-chapter（旧 · 不推荐） | cluster-write（新 · 推荐） |
|---|---|---|
| 调用粒度 | 1 章 1 次 | 1 cluster 1 次 |
| writer 后 splitter 时机 | 立刻 step 2 末切 | 推迟到 step 6 |
| 质检视野 | 单章 ch{N} | 整 cluster |
| 跨章 voice/伏笔 | 看不见 | 全程可见 |
| 总轮数（5 章 cluster） | ≈ 15-25 轮 | ≈ 3-7 轮 |
| 重复词去重 | 单章局部 | 整块去重 |
| title 生成 | step 2 内 | step 6 末（内容已 clean）|
| changes 写法 | per-chapter 写好 | cluster 级写 · step 6 平铺 |

---

# 失败逃生舱（v26 · cluster-only）

如果 cluster-write 流水线崩溃：

1. 把 `cluster_draft.txt` 改名重置：`mv cluster_<key>_draft.txt cluster_<key>_draft.bak.txt`
2. 重跑 `/cluster-write CLUSTER_ID=<key>` 从 step 1 开始
3. 若仍崩溃 → 报告用户人工介入。**🔴 v26 不再降级到 chapter mode**——chapter mode (write-chapter / save-state) 命令/plan/CLI 全部已废弃移除，无后门。
