---
description: 深度蒸馏角色，从已写章节中提取完整 Voice DNA
---

## 🛡️ Plan 强制规划

distill-character 必须走 **plan 强制规划层**。**没有 plan_id 不开工，没有 step 验证不宣称完成。**

- 模板：`core/claude-home/plans/distill-character.plan.json`（**6 步**）
- 开工前：`python core/scripts/plan_tracker.py create --command distill-character --project <书名> --key <角色id>`（`--key` = 角色 id，会替换模板里的 `{key}`）
- spawn 任何 sub-agent 的 prompt 必须含 `PLAN_ID:` 和 `STEP:`（缺失 = L3 hook 拦截 exit 2）
- 每步跑完由**主代理**调 `plan_tracker.py step <plan_id> --n N`
- 收尾 `plan_tracker.py end <plan_id>`

### 6 步流水线

| step | 名称 | 谁干 | 产物 |
|------|------|------|------|
| 1 | collect-material | Claude 分析 | `_数据库/.distill_character/{角色}_material.json`（历史对白/动作/内心 + 来源章号） |
| 2 | voice-dna-analysis | Claude 判断 | `_数据库/.distill_character/{角色}_voice_dna.json`（5 层 persona） |
| 3 | **voice-sample-gen** | **gen-model（同栈）** | `_数据库/.distill_character/{角色}_voice_samples.json`（style/anti_samples 候选） |
| 4 | merge-voice-pack | Claude 确定性合并 | 更新 `_数据库/人物卡.json` voice_pack |
| 5 | **process-integrity-verify** | `distill_character_verify.py` | `对比报告/voice_verify_{角色}.json`（PROCESS-INTEGRITY 硬 + fidelity advisory） |
| 6 | git-snapshot（required） | `git_snapshot.py --marker` | commit + `_数据库/.wal/distill_character_{key}_git_snapshot.json` marker |

## 语音样本生成

角色蒸馏分两段：
- **分析段**（提取角色实战对话 / 统计 rhythm / 识别 catchphrase 频率 / 分类 banned_phrases / 5 层 persona）→ **Claude 主代理 / sub-agent**（这是分析判断，不是生成）
- **生成段**（`voice_pack.style_samples` 候选 / `anti_samples` 候选）→ **gen-model**（含创意笔触的对话样本生成·守『蒸馏复刻必须同栈 gen-model』）

**工作流（step 3 同栈契约）**：
1. step1 主代理读已写章节 → 提取该角色全部对话 → 标注来源章号（每条 ≥2 章供 provenance）→ 写 `material.json`
2. step3 对 `style_samples` / `anti_samples` 这类含创意笔触的样本调用：
   ```bash
   python core/scripts/gen_creative.py --mode voice_sample \
     --project <项目> --character <角色> \
     --history <material.json> --voice-dna <voice_dna.json> \
     --count 4 --out <voice_samples.json>
   ```
   - gen-model 接收**已写对白 few-shot** + voice_dna → 产样本（输出 `_meta.generated_by_model` 作为同栈 provenance 证据）
   - 禁止用 `gen_fixer.py --mode polish` 代替本步骤
3. step4 主代理把 gen-model 输出合并回 `_数据库/人物卡.json` voice_pack，**必须**带入：
   - `voice_pack._gen_provenance`（= step3 `_meta` 的 generated_by_model/profile · 同栈证据）
   - 每条 `style_samples`/`anti_samples` 形如 `{"text": "...", "from_chapters": [ch1, ch2]}`（≥2 章 provenance）
   - `banned_phrases`（首次明确即入·不要求 ≥2 源）

**step 5 回灌验证闸**：`distill_character_verify.py --strict` 校验上述契约：
- **唯一 hard 项 = PROCESS-INTEGRITY**：同栈 gen-model 证据 + 每样本 ≥2 章 provenance + banned_phrases 结构合法。破损 + `--strict` → exit 2 拦在出货前。
- **voice-fidelity 永 advisory**：gen-model 新对白 / 现有样本词法自洽比对，**绝不 hard-lock 对白 match 分数**（voice 是采集非预设·随角色成长 fluid 演化·中途反复跑校准忌重量级 per-character SFS 循环）。fidelity 不达标也 exit 0。

**Claude 处理的部分**：
- 角色 5 层 persona 分析（layer_0_hard_rules / layer_1_identity / ... / layer_4_behavior）
- knowledge.knows / doesnt_know 字段填充（POV 视角控制）
- 角色 arc / growth_arc 编纂

---

你是一位角色分析专家。请对以下角色进行深度蒸馏：

$ARGUMENTS

---

# 深度蒸馏流程

## 第一步：收集素材

1. Read `_数据库/人物卡.json`，获取目标角色的当前档案
2. Read `_数据库/故事块摘要.json`，获取该角色出场的所有章节
3. 逐章 Read 该角色出场的章节 txt 文件
4. 提取该角色的所有对话、动作描写、内心活动

---

## 第二步：Voice DNA 分析（5层 Persona 结构）

从收集的素材中，按5层结构提取角色的完整人格：

### Layer 0：硬规则（不可违反的边界）

**这是最重要的一层——定义角色"绝对不会做的事"。**

- 绝对不会说的话（哪些词/句式这个角色永远不会用）
- 绝对不会做的事（哪些行为违反角色本质）
- 绝对不会有的态度（哪些情绪反应不属于这个角色）
- 底线/禁区（触碰什么会让角色彻底改变态度）
- 知识边界（角色不可能知道的信息）

**为什么 Layer 0 最重要：**
"角色不会做什么"比"角色会做什么"更能防止AI跑偏。AI倾向于让所有角色都"善解人意""理性沟通"，Layer 0 就是防止这种同质化的护栏。

### Layer 1：身份（核心自我认知）

- 角色如何定义自己（自我认知 vs 他人认知的差异）
- 核心价值观（排序，冲突时哪个优先）
- 人生目标/驱动力
- 最深的恐惧/创伤
- 自我欺骗（角色对自己撒的谎）

### Layer 2：说话风格（语言指纹）

- 句子平均长度（字数）
- 最常用句式结构
- 标点使用偏好（句号多还是省略号多）
- 词汇复杂度（口语化程度）
- 独特用词/造词/口头禅
- 语气词偏好（"嗯""啊""哦""呢""吧"的使用频率）
- 表情/动作习惯（说话时的小动作）

### Layer 3：情感模式（情绪表达+冲突处理）

- 表达愤怒的方式（动作型/语言型/沉默型）
- 表达关心的方式（直接/间接/行动/嘴硬心软）
- 表达紧张的方式
- 情绪转换的速度（快速切换 vs 慢热）
- 冲突处理模式（对抗/回避/讨好/冷处理）
- 情绪激动时的句式变化
- 平静时的句式特征
- 依恋类型（安全/焦虑/回避/混乱）

### Layer 4：行为规则（具体场景下的反应模式）

- 面对冲突时的第一反应
- 面对选择时的决策倾向
- 与不同人的互动差异（对上/对下/对亲近的人/对陌生人）
- 独处时的行为特征
- 压力下的退行行为（回到更原始的应对方式）
- 特定触发场景的固定反应（如：被背叛时一定会...）

### 认知维度（补充）

- 世界观/信念体系（角色相信什么）
- 决策框架（角色如何做选择——直觉型/分析型/情感型）
- 心智模型（角色如何理解世界运作方式）
- 认知盲区（角色看不到什么）
- 偏见/刻板印象（角色对什么有预设判断）

---

## 第三步：生成 Voice DNA Profile

输出结构化的蒸馏结果：

```json
{
  "voice_dna": {
    "layer_0_hard_rules": {
      "never_say": ["这个角色绝对不会说的话/词"],
      "never_do": ["这个角色绝对不会做的事"],
      "never_attitude": ["这个角色绝对不会有的态度"],
      "boundaries": ["触碰什么会让角色彻底改变"],
      "knowledge_limits": ["角色不可能知道的信息"]
    },
    "layer_1_identity": {
      "self_concept": "角色如何定义自己",
      "vs_others_view": "他人如何看待角色（差异点）",
      "core_values": ["价值观排序，冲突时的优先级"],
      "driving_force": "核心驱动力",
      "deepest_fear": "最深的恐惧",
      "self_deception": "角色对自己撒的谎"
    },
    "layer_2_speech_style": {
      "avg_sentence_length": "12字",
      "preferred_structures": ["短句连发", "问句反问"],
      "punctuation_habits": "句号为主，紧张时用省略号",
      "vocabulary_level": "口语化，偶尔蹦出文言词",
      "catchphrases": ["口头禅列表"],
      "tone_particles": {"嗯": "高频", "啊": "低频", "呢": "中频"},
      "speaking_gestures": ["说话时的小动作"]
    },
    "layer_3_emotional": {
      "anger": "沉默+动作（摔东西/转身走）",
      "care": "嘴上说反话，行动上帮忙",
      "nervous": "语速加快，重复用词",
      "emotion_switch_speed": "快速（一秒变脸）",
      "conflict_style": "正面对抗，不回避",
      "attachment_type": "回避型",
      "excited_sentence_pattern": "连续短句，不超过5字",
      "calm_sentence_pattern": "中等长度，节奏均匀"
    },
    "layer_4_behavior": {
      "first_reaction_to_conflict": "先动手后说话",
      "decision_pattern": "直觉型，想到就做",
      "interaction_differences": {
        "to_superiors": "表面恭敬，内心不服",
        "to_peers": "直来直去",
        "to_close_ones": "嘴硬心软",
        "to_strangers": "冷淡警惕"
      },
      "alone_behavior": "发呆/练功/自言自语",
      "regression_under_stress": "回到暴力解决的模式",
      "trigger_responses": {"被背叛": "彻底切断关系", "被小看": "用行动证明"}
    },
    "cognition": {
      "worldview": "弱肉强食，但保护弱者",
      "decision_framework": "直觉型（先做再想）",
      "mental_model": "世界是危险的，只有强者才能保护想保护的人",
      "blind_spots": ["看不到自己的脆弱", "低估他人的善意"],
      "biases": ["对权贵有偏见", "对弱者过度同情"]
    },
    "growth_summary": "从第1章的迷茫到第8章的坚定，核心转变在第5章"
  }
}
```

---

## 第四步：更新人物卡（增量合并，不覆盖）

**核心原则：增量更新，保留历史，版本追踪**

1. Read 当前 `_数据库/人物卡.json` 中该角色的完整数据
2. **增量合并 Voice DNA**（新数据补充旧数据，不覆盖已有字段）：
   - `voice_dna.layer_0_hard_rules` → 追加新发现的 never_say/never_do/never_attitude
   - `voice_dna.layer_1_identity` → 更新 self_concept（如有成长变化）
   - `voice_dna.layer_2_speech_style` → 更新统计数据（取最新章节的加权平均）
   - `voice_dna.layer_3_emotional` → 追加新发现的情绪模式
   - `voice_dna.layer_4_behavior` → 追加新的 trigger_responses 和 interaction_differences
   - `voice_dna.cognition` → 更新 blind_spots 和 biases（随剧情演变）
3. **更新 voice_pack**（⚠️ P2-7 反 over-generalize 守则）：
   - `style_samples`：替换为最新最具代表性的 3-5 条（标注来源章节）。
     **每条必须有 ≥2 个章节来源**才可入 samples；单次出现的「特色用法」当弱信号，
     不入 samples（避免 novel-voice-checker 后续按错误 pattern 改写其他对话）
   - `anti_samples`：追加新发现的反面例子，同样要求 ≥2 章节出现才硬列
   - `catchphrases`：**频次门槛 ≥3 次**才升级为 catchphrase；1-2 次只放在
     `tone_particles` 的「低频」桶。判断口诀：「频次 ≥3 才算 pattern」
   - `banned_phrases`：与角色明确人设冲突的禁说词；首次明确即可入列（这是底线，
     和正向 pattern 不同）
   - `rhythm`、`style` 等字段同步更新
4. **增量维护 recognition_anchors / negative_facts**（A7 辨识锚点分层 · schema 见 `subsystem_skeletons.json` 人物卡 `_recognition_schema`）：
   - `recognition_anchors`：从素材正文提炼身体标记/习惯动作/标志性道具，每条 `{"anchor": "...", "position_or_scene": "..."}`，去重追加；**正文有据才写**。主角/核心角色建议 ≥2 条（advisory 软建议·不硬锁）
   - `negative_facts`：纯字符串列表·「绝不应展现的能力/特征」反向锚（「不会武功」「不识字」）；Layer 0 `never_do` 中属**客观能力边界**的条目可同步沉淀进来（态度/价值观类不算）
   - 消费方：`character_identity_anchor_scanner`（漂移+违背检测·advisory）+ `novel-voice-checker`（对话口径）
5. **更新 decision_patterns**（追加新模式，保留旧模式）
6. **整理 growth_arc**：
   - 追加本次蒸馏发现的新转折点
   - 合并相似条目，保留关键节点
   - 更新 `voice_dna.growth_summary`
7. **版本标记**：
   ```json
   {
     "distill_history": [
       {"version": 1, "chapters_analyzed": "1-5", "date": "2026-01-15"},
       {"version": 2, "chapters_analyzed": "1-12", "date": "2026-01-20"}
     ],
     "last_distill_version": 2,
     "last_distill_chapters": "1-12"
   }
   ```
8. Write 更新后的人物卡

**合并规则：**
- 数组字段（never_say, trigger_responses 等）：去重追加
- 数值字段（avg_sentence_length 等）：取最新分析结果
- 描述字段（self_concept 等）：如有实质变化则更新，否则保留
- 冲突处理：新数据优先，但保留旧数据为 `_previous` 备份

---

## 第五步：输出蒸馏报告

```
🧬 角色蒸馏完成 — [角色名]

  分析章节：第1-N章（共X章出场）
  对话样本：Y 条
  风格样本：更新为 Z 条（替换旧样本）
  性格变化：A 个关键转折点
  决策模式：B 条
  
  Voice DNA 摘要：
  「[一句话描述这个角色的说话风格]」
  
  成长轨迹：
  第1章[状态] → 第X章[状态] → 第N章[状态]
```

---

## 第六步：Git 自动提交（required）

角色蒸馏完成后（人物卡.json 已更新），经唯一入口 `git_snapshot.py` 提交快照并落 marker（与 plan step 6 一致）：

```bash
python core/scripts/git_snapshot.py {project_root} --message 蒸馏角色 --marker _数据库/.wal/distill_character_{key}_git_snapshot.json
```

commit 失败或 marker 缺失 = 本 required step 失败（CLAUDE.md Git 纪律，不静默放行）。

commit 信息示例：
- 初次蒸馏：`feat: 蒸馏角色 李若渝 v1（ch 1-5）`
- 增量蒸馏：`feat: 蒸馏角色 李若渝 v2（ch 1-12）`
- 全书蒸馏：`feat: 全书蒸馏角色 李若渝 v5（ch 1-50）`

---

## 使用场景

- 写到中期（第5章左右）时，对主角做一次深度蒸馏，校准后续写作
- 发现角色"不像自己"时，做深度蒸馏找出漂移点
- 写完全书后，为续集/番外准备角色档案
- 多部作品共享角色时，导出 Voice DNA

---

**目标：让每个角色都有独一无二的"灵魂"，而不只是一张设定卡**

---
本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
