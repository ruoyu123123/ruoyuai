# 开源项目挖掘 Round 11（新循环第 3 轮 · 决定轮 · 2026-07-11）

> 第三循环（用户 2026-07-10 六件套 /goal）第 3 轮。run_id=`round11_2026-07-11`，
> artifact 见 `research/open_source_writing_systems_round11_candidates.json`。
> **本轮结论：11 个去重候选全部 FAIL，0 进入对抗复核 — NO_NEW_CANDIDATES 计数 3/3
> — 连续三轮达成，本循环 GitHub 挖掘终止。**

## 一、角度设计与查重

round10 反推的初版角度经查重再淘汰 3 个（2026 H1 论文实现撞 round3/5 时间窗角度、爽点量化撞
round6/7、键盘遥测=数据源不存在的必死角度），定稿 6 个全新角度（对 round3-10 累计 48 角度零重叠）：

| 角度 | 主题 | 真实观测锚点 | 有效候选 |
|---|---|---|---|
| M | 确定性/可复现采样工程 | SFS 单次方差 std≈5.56（memory 实证） | 0（域内无独立工具仓） |
| N | 中文 TTS 前处理/多音字 | 无（/export 下游假设） | 2 |
| O | agent 长程记忆专项 | 无（记忆体系 2026-06-14 已调研定论） | 6 |
| P | LLM 网关/中转可靠性 | elysia 返空+503 事故史 | 1 |
| Q | 中文分词/词性新模型 | 无（jieba 零失效记录） | 1 |
| R | 长文本嵌入/检索模型 | 无（bge 服役中无失效） | 1 |

12 条 search query（首批 8 + 放宽 4）；字面命中空的角度以域标杆定向核验补位（不猜 URL，
全部经 repos endpoint 元数据实拉）。11 项均为全历史新实体（grep research/ 零命中）。

## 二、五问闸结果（11 项全 FAIL）

### N 中文 TTS 前处理/多音字韵律

- **R11-N01** [`mozillazg/python-pinyin`](https://github.com/mozillazg/python-pinyin)（5338⭐ · MIT · pushed 2026-06-22）— **FAIL**。Q1 否：全系统无拼音消费点（grep core/ 零命中）、/export 仅出 txt、用户从未提出有声书需求；requirements.txt 无此依赖。TTS 前处理是『假设性下游生态』非已观测需求。
- **R11-N02** [`wenet-e2e/wetextprocessing`](https://github.com/wenet-e2e/WeTextProcessing)（795⭐ · Apache-2.0 · pushed 2026-06-26）— **FAIL**。同 R11-N01：TTS 文本正则化（数字/单位读法展开）服务于语音合成场景，本系统无该链路。

### O agent 长程记忆系统专项

- **R11-O01** [`topoteretes/cognee`](https://github.com/topoteretes/cognee)（27541⭐ · Apache-2.0 · pushed 2026-07-10）— **FAIL**。双轨+宿主：GraphRAG 式记忆平台需服务栈+图存储；若渝记忆=Claude memory 目录+34 子系统 JSON+manifest 注入+摘要金字塔，2026-06-14 专项调研已下结论『局部超 SOTA·不让弱模型自管内存』（memory: project_memory_novel_storage_research）；引入=第二记忆层违双轨禁令。
- **R11-O02** [`morettt/my-neuro`](https://github.com/morettt/my-neuro)（1303⭐ · MIT · pushed 2026-07-05）— **FAIL**。域无关：桌宠/陪伴产品，与写作系统无交集。
- **R11-O03** [`dataojitori/nocturne_memory`](https://github.com/Dataojitori/nocturne_memory)（1262⭐ · MIT · pushed 2026-06-26）— **FAIL**。同 R11-O01：记忆 server 宿主+第二记忆层双轨。
- **R11-O04** [`lycheemem/lycheemem`](https://github.com/LycheeMem/LycheeMem)（1127⭐ · Apache-2.0 · pushed 2026-07-07）— **FAIL**。同 R11-O01 双轨记忆层。
- **R11-O05** [`iaar-shanghai/awesome-ai-memory`](https://github.com/IAAR-Shanghai/Awesome-AI-Memory)（1067⭐ · Apache-2.0 · pushed 2026-07-08）— **FAIL**。策展性质非工具实体（round5 元策展角度已确立：awesome 列表不是集成对象）。
- **R11-O06** [`teleai-uagi/awesome-agent-memory`](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory)（525⭐ · Apache-2.0 · pushed 2026-07-10）— **FAIL**。同 R11-O05 策展性质。

### P LLM API 网关/中转可靠性

- **R11-P01** [`berriai/litellm`](https://github.com/BerriAI/litellm)（53213⭐ · NOASSERTION · pushed 2026-07-10）— **FAIL**。EXISTS+纪律冲突：llm_transport.py 已收敛双协议（gemini 原生 SSE thinkingLevel/OpenAI）+异常归一+Retry-After+TransportEmpty 空响应守卫+refusal 检测+CJK 续写循环——多为本系统特化逻辑 litellm 不具备；系统全局锁定单一 gen-model 无多 provider 路由需求；litellm 核心卖点 fallback 链正面撞『禁 fallback 降级』铁律。真实事故史（elysia 中转返空/503）根因在上游服务端，客户端换网关库不解决，且 TransportEmpty 已堵『空文本报成功』。license NOASSERTION（MIT+enterprise 混合）。

### Q 中文分词/词性新模型

- **R11-Q01** [`ckiplab/ckiptagger`](https://github.com/ckiplab/ckiptagger)（1682⭐ · GPL-3.0 · pushed 2025-07-09）— **FAIL**。域偏移+无观测：CKIP 训练语料为繁体中文（台湾中研院），简体网文场景准确率打折；TensorFlow 1.x 旧栈；jieba 集成点在 5+ scanner 在位（grep 实证）且全历史零分词失效记录。

### R 长文本嵌入/检索模型

- **R11-R01** [`flagopen/flagembedding`](https://github.com/FlagOpen/FlagEmbedding)（11920⭐ · MIT · pushed 2026-04-22）— **FAIL**。EXISTS 极端形态：其产物 bge 模型已在系统内服役（Wave-6 内容双轨后端·core/ml/calibration/content_embed_separability_20260704.json 实证），阈值刚按 bge 分布重标完且无失效观测；『升级模型版本』非新能力集成对象。

## 三、循环终止声明

**`NO_NEW_CANDIDATES`** — 第三循环连续三轮（round9: 35 候选 / round10: 11 候选 / round11: 11 候选，
共 **57 个去重候选、18 个全新搜索角度**）无一通过北极星五问闸；仅 round9 pytest-xdist 一项进入
实测边界审（双计时 2.34x 加速但串行基线 230s 无痛点，FAIL），全程无候选进入对抗复核幸存/集成/
真实 API 验证阶段。达成 /goal 终止条件。

**三个循环累计**（2026-07-07 起）：round3-5（96）+ round6-8（164）+ round9-11（57）= **317 个
去重候选、54 个搜索角度、0 通过**。round1-2 早期机制移植阶段有成功落地项，与三振循环分开统计。

## 四、本循环方法论沉淀（与前两循环的增量）

1. **实测探针 gate**：vulture 实跑/文档路径探针/xdist 双计时——可复跑证据取代推断，是 round6
   「先 grep 后判断」纪律的升级形态。
2. **api.github.com 通道**：github.com 443 间歇断连时 API 域独立可达；search 正规限定词 + repos
   元数据实拉一次拿全 artifact 合同字段；10/min search 限流须 >6s 间隔。
3. **角度先查重再开搜**：round10/11 初版角度各淘汰 4/3 个撞历史——48 个累计角度下，新角度设计
   的主要失败模式是「换个说法的旧角度」。
4. **verdict 随判随写回 JSON**：断点安全，中断后从 artifact 续跑无需重评。
5. **「从已修复历史反推角度」天然撞 EXISTS**：契约债/NER 误报等真实事故已被根治，以它们为锚的
   角度命中率为零——增量只会来自新发布成果、新观测失效或用户新不满（与 round8 结论一致，
   本循环再次验证）。

## 五、重开条件（非永恒结论）

- pytest-xdist：测试规模增长致串行全量 >10 分钟且被真实观测为拖累时，round9 实测数据即 PASS 依据。
- 挖掘循环整体：出现新观测失效模式、用户新需求或重要新发布（如 gen-model 生态变化）时按需重启，
  不建议再做同类全域扫描（54 角度 317 候选的覆盖面已充分证明当前成熟度）。
