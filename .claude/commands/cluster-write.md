---
description: 按故事块（cluster）整块写作 · v24 倒置流水线 · 整块迭代修完才拆章
---

你是若渝AI的**故事块写作调度器**。你不写作、不校验、不审对话——你只按顺序调度 5 个专精 agent + 4 个脚本。

$ARGUMENTS

---

# 🔴 设计哲学（v24 倒置流水线 · 必读）

**问题**：旧流程在 writer 写整 cluster 草稿后**立刻**切成单章再逐章质检。后果：

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
- 🔴 audit_hub / reading-reflector / novel-voice-checker / foreshadower 全部走 `--mode cluster` / `MODE=ecas`
- 🔴 1 个 cluster = 1 次 cluster-write plan
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
0.5  pre_write_gate.py              （写前 Evolution Gate · 世界状态/人物卡 × brief 校验 · 死角色/销毁道具/锁定事实=blocking · gate_waivers 声明豁免 · cluster_001 skip）
     ↓
1.   build_manifest.py              （cluster 起首章 manifest · 注入 cluster brief + 全 25+ 子系统状态）
     ↓
2.   novel-writer MODE=ecas         （v29 两阶段：2a Claude 亲笔逐场景写 claude_scenes/ → 2b gen_writer.py 调 gemini 分段等体量润色出终稿 · self_eval/waivers 自评 · 不自报 factual · ★禁止 splitter · ★禁止 gen-model 从零生成）
     ↓
3.   cluster 级双轨质检（强制）
     ├─ 机械: audit_hub.py --mode cluster --cluster-id <key> --auto-fix --waivers
     │         hard_gate 不可豁免 · advisory 凭理由豁免
     └─ 阅读: novel-reading-reflector MODE=ecas ROUND=1→...
              连续 3 轮 0 issue 才进入下一 required step（SRE 风格健康检查）
     ↓
4.   novel-voice-checker MODE=cluster   （整 cluster 对话声纹 · 跨场景 voice 漂移）
     ↓
5.   novel-foreshadower + novel-reflector + novel-summarizer   （cluster 级三 agent 串行）
     ├─ foreshadower: 整 cluster 伏笔评估
     ├─ reflector: 整 cluster 经验沉淀
     └─ summarizer: cluster 级摘要
     ↓
6.   ★ 最后才切章
     ├─ novel-chapter-splitter MODE=ecas_freestyle（含 narrative_mode=in_medias_res）
     ├─ gen_chapter_titles.py --chapters <range_from_splitter_wal>   （normal/mid/high 三档）
     └─ split_cluster_changes.py --cluster <key>                     （只平铺纯格式 + self_eval/waivers 到 per-chapter · 不平铺 factual）
     ↓
7.   报告 + plan-end → 准备进 cluster-save-state
```

---

## 第 1 步前置子步骤：走向卡 → 世界涟漪（cluster_002+ 必跑 · cluster_001 无前置选择）

cluster_001 是首块，无上一 cluster 走向卡，本前置子步骤不执行。

cluster_002+ 之前主代理在 cluster-save-state step 13 让用户选定过下一 cluster brief；这是 plan step 1 的正式前置校验，必须先消费用户选择 artifact：

```bash
# 读取当前 cluster 的用户选择 artifact
python core/scripts/world_evolution_apply_card.py "<项目路径>" \
  --next-key <key> \
  --choice "_数据库/.wal/cluster_<key>_user_choice.json"
```

效果：把选中 cluster brief 的 `ripple_match` 落到 世界状态.json + 涟漪 log，并把 cluster 级 `user_choice` / `_user_decision` / `choice_leads_to` 写回 `事件簇.json`。

**硬停规则**：
- cluster_002+ 缺 `_数据库/.wal/cluster_<key>_user_choice.json` → 停止，不进入 build_manifest。
- `ripple_match` 为空、`世界状态.json` / `涟漪规则.json` 缺失、规则无匹配 → 停止。
- 不允许使用旧 `--cluster` 或 `<chapter> <label>` 入口。

## 第 1 步前置子步骤 2：写前 Evolution Gate（A2 · 2026-07-07）

world_evolution_apply_card 落库 brief 之后、auto_fate_draw / build_manifest 之前，必跑写前 gate——把穿帮从「写完 20k 字再修」提前到「写前拦」：

```bash
python core/scripts/pre_write_gate.py "<项目路径>" --next-key <key>
```

校验「当前世界状态/人物卡 × 选中 brief」四项：

| 检查 | 级别 | 说明 |
|---|---|---|
| 死亡角色上台 | blocking | brief 结构化出场名单（characters_focus / storyboard characters/participants/focal_character）含 人物卡 status=dead 或 character_arc_state 已死角色；文本**提及**死角色只 warning（回忆/动机合法） |
| 销毁道具 | blocking | anchor_props 引用 道具.json status ∈ destroyed/lost/已销毁/丢失 的道具 |
| 锁定事实冲突 | blocking | brief 文本与 locked_facts 恒定数值直接冲突（同 locked_fact_cross_scene 单位集·只抓恒定量不碰品级成长） |
| 重复事件嫌疑 | warning 只记 | scope_summary 与已 completed ME / 已完成 cluster 高词面重叠 |

- 产物：`_数据库/.wal/cluster_<key>_pre_write_gate.json`（含 skip 场景恒落盘）
- blocking 非空 → `[FATAL]` 走 stderr + exit 2 硬停，不进 build_manifest。这是**写前拒绝不是审计 issue**——不新增 hard_gate code。
- cluster_001 首块无 brief 选择场景 → 脚本内部检测优雅 skip exit 0。

**声明式豁免（北极星⑤ 创作声明权）**：brief 可选字段 `gate_waivers`（schema 见 `event_cluster_schema.json`），如：

```json
"gate_waivers": [{"type": "dead_character", "target": "沈铖", "reason": "闪回场景"}]
```

type 支持叙事手法别名（flashback/闪回/ambiguous_fate/模糊生死/time_skip/时间跳跃…）。gate 命中且有对应豁免 → 放行并留痕报告 `waived[]`。blocking 类豁免必须点名 target；gate 只拦「未声明的意外穿帮」，不对声明做二次裁决。命中拦截时的处置：修正 brief（换角色/换道具/改设定表述）或补 `gate_waivers` 声明后重跑本脚本。

报告的 `waived[]`/`warnings[]` 非空时，下一步 build_manifest 会自动注入 `pre_write_gate_digest` 段（T0 契约类·2026-07-08 A2 闭环）——writer 借此把已声明豁免当叙事手法有意识落笔（如亡者只以幻觉/回忆登场），warnings 提示避免复写已完成事件。

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
- 检索三段式（2026-07-08 A6·确定性零 LLM）：RAG/selective_history 的 query 自动带 cluster brief「实体×属性」扩展词组；每条检索命中带 `usage_hint`（块距防复读标签 [NEAR_ECHO_RISK]≤1块/[PARAPHRASE]2-3块/[OK]>3块 + 用途分类）——writer 按标签决定引用方式（advisory）
- scene 维度门控（2026-07-08 A11·env `MANIFEST_SCENE_GATING` 默认 on）：世界观词条点名了与本块出场角色/地点零交集的实体 → 不注入（被滤词条留痕 manifest `_scene_gating` META 段；匹配不到场景信息=不过滤零变化）

**plan-step 1**（manifest + style_directive 都是必须落地的文件）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1
```

模板已配 expected_outputs，缺失自动 FAIL。

---

# 第 2 步：novel-writer v29（Claude 亲笔创作 + gemini 分段润色 · ★禁止 splitter）

用 Agent 工具启动 `novel-writer`，prompt 只含 cluster 契约字段：

```
PLAN_ID: $PLAN_ID
STEP: 2
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: ecas
RESEARCH_REF: <项目路径>/_数据库/.research_cache/<本 cluster 调研文件或 synthesis>
```

writer 行为（v29 两阶段 · 用户 2026-07-11 定调「所有创作路线转向 Claude 自身创作内容 + gemini 润色」）：
- **step 2a Claude 亲笔创作**：agent 读 manifest/风格 skill/brief/research 后逐场景亲笔写作，落盘
  `章节/cluster_<key>_draft/claude_scenes/scene_*.txt`（每场景写透·分场景落盘规避单响应上限）+
  拼接审计基线 `cluster_<key>_draft_claude.txt` + 自评草稿 `cluster_<key>_changes_claude.json`
- **step 2b gemini 分段润色**：agent 调 `gen_writer.py --project <root> --cluster <N>`——自动发现
  claude_scenes/，逐场景段调 gemini 按风格档**等体量重写润色**（守恒带 [0.85,1.30]·超界带字数指令
  重试 1 次·万字整体润色已实测三连败必须分段），拼接出终稿
- 产出 `章节/cluster_<key>_draft/cluster_<key>_draft.txt`（终稿 · 整 cluster ≥10000 CJK）
- 产出 `章节/cluster_<key>_draft/cluster_<key>_changes.json`（Claude self_eval/waivers + gen_writer
  确定性遥测合并 · `writer_mode: claude_draft_gemini_polish_v29` · **writer 链不自报 factual**）
- 🔴 **禁止自行调 splitter**（流水线纪律：splitter 推迟到 step 6）
- 🔴 **禁止 gen-model 从零生成**：gen_writer.py 已无该路径（缺 claude_scenes/ 即 [FATAL]·不兼容不降级）
- 🔴 勿复活 expand/字数兜底红线原样保留——Claude 每场景写透即止，字数不够=回头把场景写透而非尾部注水
- 实验依据：`workspace/_temp_research/四组生成对比_20260711`（cluster 级 Claude 草稿+gemini 润色
  双通道最优：嵌入 SFS 第一/零禁用词/事实链零漂移；gen-model 直写+多轮扩写=套话×10+设定漂移）

> 🔴 **factual 边界（沿用）**：writer 链只产正文 + 创作自评（self_eval/waivers），**不产任何 factual 状态自报**。cluster 级 factual（角色/道具/关系/locked_facts/伏笔）由 Claude agent 事后读正文梳理回库（archivist→apply_archive / foreshadower / outline brief），见 `/cluster-save-state`。

writer 返回后，检查四产物落地（claude_scenes/ + draft_claude.txt + draft.txt + changes.json）。任一缺失 → 停止，向用户报告（writer 契约违规）。

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

> 🔴 **NN 模型自动接入（2026-06-30）**：audit_hub `main()` 默认开启 5 个 NN 门控（surprisal 信息密度 / coherence 连贯 / VAD 情绪弧 / coref 共指 / character-network 角色网络），**无需手动 export**——经 `nn_runtime_defaults.enable_creative_nn_defaults()` 在命令行入口自动开（能力不足时各桥 `enabled()` 安全回退·显式 `RUOYU_NN_*=0` 可关闭做对照）。NN 输出**全 advisory**（不进 hard_gate·北极星⑤）。surprisal/coherence/VAD 经 `core/ml/.venv` subprocess 桥推理（首用加载模型~90s·已统一 300s timeout）。

退出码语义：
- 0 = pass / waived
- 1 = auto_fixed → 进 3.2
- 2 = needs_agent → 读 pending_agent · spawn 对应 agent · 修完重跑
- 3 = fatal → 停止

**关键差异**：`--mode cluster` 让 audit_hub 把整 `cluster_draft.txt` 当 1 个文本对象跑，**跨场景的伏笔/anchor/voice/repeat-phrase 完整性都能被检出**，不会再像单章模式那样错过。

派单时 agent prompt 加 `CLUSTER_ID: <key>` + `MODE: cluster`，让被派 agent 也跑 cluster 视野。

## 3.1b 作者金标准对比闸（required · 2026-06-04 补漏）

> **为什么补**：audit/reflector/voice 全查机械维（句长/段长/禁用词/voice 一致），**从不拿真作者原文比"调性/喜剧到位度"**——cluster_001 实测全过却跑偏成赛博惊悚（作者是市井喜剧）。本闸拿生成指纹比 `作者风格.json` 基线，**情绪标点（感叹/问号/省略）偏低 = 喜剧引擎没落地的可量化代理信号**。

```bash
python core/scripts/replication_fidelity_check.py --project "<项目路径>" --cluster <key> --strict
# 等价: --project <项目路径> --cluster <key>
```

- exit 0 = 作者量化指纹通过，report 写入 `_数据库/.audit/replication_fidelity_cluster_<key>.json`。
- exit 1 = 存在偏离，必须派修复 agent 改 `cluster_draft.txt` 后重跑本闸；不得写理由豁免后继续。
- exit 2 = 无正文 / 无作者风格基线 / 输入缺失，当前 plan 硬停。
- 质性调性（市井喜剧 vs 惊悚）量化闸抓不全 → 重大风格书必须把 golden_passages 调性对比作为本 step 的正式补充检查。

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

reflector 10 维扫整 cluster：
- 段首单调含全主语词 / voice 漂移 / POV 一致 / 信息密度 / 节奏 / 对话工艺 / 互动质感 / 塑料感（含 StoryScope 结构层 AI tell 子清单：场景末主题宣讲/关键人物全员道德单义/收束过净/零时间复杂度·金标准基线校准·体裁常态与作者档优先可让位·2026-07-07）/ 锁定事实语义冲突（多跳推理·补机械层与 110M NLI 都够不着的间接矛盾·2026-07-07）/ 悬置线推进性（subplot_threads 活跃线 + 当前卷未消费 ME 连续多块零触碰·advisory 提示可豁免·2026-07-07）

verdict 处理：
| verdict | 处理 |
|---|---|
| pass + consecutive_clean ≥ 3 | 进 step 4 |
| pass + consecutive_clean < 3 | spawn round+1 复核 |
| fail | spawn validator-checker 改 cluster_draft.txt · 然后 spawn reflector round+1 |
| hard_stop | 停止当前 plan，修复输入/正文/反思链路后续跑 |

**SRE 风格预算**：MAX_ROUNDS=6。达到上限仍未 3 连 clean 时 `verdict=hard_stop`，当前 plan 停止；不得写 `final_pass` 或人工进入 cluster-save-state。

输出文件：`_数据库/.reading_reflection/cluster_<key>_round_<N>.json`

**plan-step 3**（双轨产 audit json + reflector json 都必须落地）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3
```

---

# 第 4 步：cluster 级 novel-voice-checker

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
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4 --output "_数据库/.checker_briefs/cluster_<key>_voice.json"
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

## 按字数切章

splitter 按每章 3000-4500 CJK 的范围自动计算 N；末章不足 3000 CJK 时写入 `pending_tail.txt`，留给下个 cluster 拼接。

**读取 cluster brief 的 `_writer_mode`** 只用于契约校验；当前唯一合法值是 `freestyle`：

```bash
WRITER_MODE=$(python -c "
import json
ec = json.load(open('<项目路径>/_数据库/事件簇.json', encoding='utf-8'))
for c in ec.get('clusters', []):
    cid = str(c.get('cluster_id', ''))
    if '<key>' in cid:
        print(c.get('_writer_mode', 'freestyle'))
        break
" 2>/dev/null)
WRITER_MODE=${WRITER_MODE:-freestyle}
if [ "$WRITER_MODE" != "freestyle" ]; then
  echo "[FATAL] cluster-write 只支持 freestyle；禁止恢复 multi_chapter/locked 分支：$WRITER_MODE" >&2
  exit 2
fi

# 检测上 cluster pending_tail
PREV_KEY=<上 cluster key · 如 "005" 当本 cluster=006>
PREV_PENDING_TAIL="<项目路径>/章节/cluster_${PREV_KEY}_draft/cluster_${PREV_KEY}_pending_tail.txt"
if [ -f "$PREV_PENDING_TAIL" ]; then
  echo "[pending_tail] 检测到上 cluster 尾段: $PREV_PENDING_TAIL"
  PENDING_ARG="PREVIOUS_PENDING_TAIL_PATH: $PREV_PENDING_TAIL"
else
  PENDING_ARG=""
fi
```

## 6.1 spawn novel-chapter-splitter

### 输入契约

```
Agent 启动 novel-chapter-splitter:
PLAN_ID: $PLAN_ID
STEP: 6
PROJECT: <项目路径>
DRAFT_PATH: <项目路径>/章节/cluster_<key>_draft/cluster_<key>_draft.txt
MODE: ecas_freestyle
CLUSTER_ID: cluster_<key>
CLUSTER_START_CH: <START_CH>
ECAS_BRIEF_PATH: <项目路径>/_数据库/事件簇.json
NARRATIVE_MODE: <linear|in_medias_res>
CLIMAX_HINT_SCENE_INDEX: <从 事件簇.json 取>
$PENDING_ARG   # 上 cluster pending_tail 路径（如有）
```

splitter 行为：
- 按字数自动计算 N（每章 3000-4500 CJK）
- 末章 < 3000 → 写 `cluster_<key>_pending_tail.txt`（不切章 · 下个 cluster 时 prepend）
- 末章 ≥ 3000 → 正常 N 章切完
- 输出 splitter_wal 含 `pending_tail.exists` + `pending_tail.cjk` + `chapter_range` 字段

产出：
- `章节/第<NNN>章/第<NNN>章.txt` × N（纯正文 · N 由 splitter 按字数算）
- `章节/第<NNN>章/第<NNN>章_changes.json` × N（占位 · 待 6.3 平铺）
- `_数据库/.wal/splitter_cluster_<key>_decisions.json`（切点 WAL · 记录每章范围 + pending_tail meta）
- `章节/cluster_<key>_draft/cluster_<key>_pending_tail.txt`（末章不足时）

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

把 `cluster_changes.json` 按 splitter_wal 的切点拆成 N 个 `第<NNN>章_changes.json`（**只平铺纯格式元数据 + 创作自评**）：
- 纯格式段（每章章号 / 字数范围 / 切点 meta）按切点落到对应 _changes.json
- self_eval / waivers 按段所在章号分配
- 🔴 **不平铺 factual**：writer 已不自报 factual（changes.factual 为空），cluster 级 factual 状态由 `/cluster-save-state` 的 archivist→apply_archive 在 cluster 级确定性回库，**不下放到 per-chapter**

**plan-step 6**（splitter WAL 必须落地）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 6
```

## 6.4 🆕 章末 cliffhanger anchor 强制 scan（L4 防御）

**为什么必跑**：cluster_001 ch4 翻车 3 次 sediment 出来的强制点。章末 cliffhanger 必须锚到**已存在**的 cluster_blueprint / 伏笔表 / 事件簇 brief，禁止：
- 任何剧本体过渡（hook L1 已拦，这步是兜底）
- 任何文学过渡分隔符 / 听觉视觉淡出 / 收束句
- 装神弄鬼无锚 cliffhanger（如「影子里有不是他的」「凉风从背后吹来」纯氛围镜头）

```bash
python core/scripts/chapter_end_anchor_scan.py "<项目路径>" \
  --chapters $START_CH-$END_CH \
  --strict 2>&1 | tail -20
```

退出码：
- 0 = 全章末锚定通过
- 1 = 部分章末 advisory（writer 可豁免 · 但必须写理由到 changes.json）
- 2 = 命中 banned_patterns（hard_gate · 必修）

**hard_gate 处理**：spawn `novel-validator-checker` 出 brief，回到 `章节/cluster_<key>_draft/cluster_<key>_draft.txt` 做 cluster 草稿层修复，然后重新执行 step 6 splitter + titles + per-chapter changes。正文修复只在 cluster 草稿层执行。

权威 lesson：`memory/feedback_no_screenplay_stage_directions_in_novels.md`

---

# 第 7 步：报告 + plan-end

```
📦 cluster <key> 写作完成

══ 流水线 ══
  1. build_manifest: ✅
  2. Writer (ECAS): <CJK 总数> / 切前未拆
  3. 双轨质检:
       - 机械: audit_hub --mode cluster <verdict> | 自动修 <a> / 派 agent <p> / 豁免 <w>
       - 阅读: reflector <N> 轮 → 最终 verdict=pass 且 consecutive_clean_rounds≥3
  4. Voice-keeper: <改写 m 段 · 0=无>
  5. Foreshadower: 埋 <i> 兑 <j>
     Reflector: 沉淀 <成功 X / 失败 Y> 经验
     Summarizer: cluster 摘要 <字数>
  6. 切章 (freestyle):
       - splitter: 切 <N> 章 (ch<S>-ch<E>) | narrative_mode=<...>
       - 上 cluster pending_tail prepend: <Y/N · 字数 X>
       - 本 cluster pending_tail held: <Y/N · 字数 X · 等下 cluster 拼>
       - titles: <ch1 标题 / ch2 标题 / ... / chN 标题>
       - per-chapter changes: 平铺完成

══ 下一步 ══
  → /cluster-save-state 保存状态 + 涌现下个 cluster
  → 如有 pending_tail held：写完下个 cluster 后会自动拼接补料
```

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 7 --output "_数据库/.wal"
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
- [ ] `_数据库/.audit/replication_fidelity_cluster_<key>.json` 落地，严格模式 verdict=pass
- [ ] `_数据库/.reading_reflection/cluster_<key>_round_<N>.json` 落地，最终 verdict=pass 且 consecutive_clean_rounds≥3
- [ ] `_数据库/.wal/cluster_<key>_summary.json` 落地
- [ ] `_数据库/.wal/splitter_cluster_<key>_decisions.json` 落地
- [ ] N 个 `章节/第<NNN>章/第<NNN>章.txt` 全落地
- [ ] N 个 `章节/第<NNN>章/第<NNN>章_changes.json` 全落地（含 self_eval/waivers 真实数据，非 placeholder · factual 由 cluster-save-state 的 archivist 回库不在此）
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
1. Bash 调用 build_manifest / world_evolution_apply_card / pre_write_gate / audit_hub --mode cluster / gen_chapter_titles / split_cluster_changes / plan_tracker 等脚本
2. Agent 工具启动专精 agent（含 audit_hub exit 2 时按 pending_agent 清单派单）
3. 根据返回决定下一步
4. 向用户汇报

---

# Agent / 脚本不可用时的硬停策略

`.claude/agents/` 下对应 agent 定义缺失、调用失败或产物缺失：

- Writer 失败 → 停止当前 plan，修复后从 plan_tracker 下一步续跑。
- audit_hub --mode cluster 致命错误（exit 3）→ 停止。
- reading-reflector 失败 → 停止；阅读轨是 cluster 进入切章前的 required 条件之一。
- novel-voice-checker 失败 → 停止；声纹审查是 cluster 质量链的 required 步骤。
- foreshadower/reflector/summarizer 失败 → 停止；不得切章产出一个缺反馈账本的 cluster。
- splitter 失败 → 停止（章节产物缺失 = 整 cluster 无法消费）。

---

# 失败恢复

如果 cluster-write 流水线崩溃：

1. 先跑 `wal_recovery.py` / `plan_tracker status <plan_id>`，以 plan_tracker 的 first incomplete step 为恢复点。
2. 续跑 `/cluster-write CLUSTER_ID=<key>`，已完成且产物通过 expected_outputs 的 step 不重做。
3. 若产物损坏导致续跑无法验证，先修正该产物或明确 abort 当前 plan；不得通过改名草稿绕过状态。
4. 若仍崩溃 → 报告用户人工介入。

---
本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
