# 开源项目挖掘 Round 10（新循环第 2 轮 · 2026-07-11）

> 第三循环（用户 2026-07-10 六件套 /goal）第 2 轮。run_id=`round10_2026-07-11`，
> machine-readable artifact 见 `research/open_source_writing_systems_round10_candidates.json`。
> **本轮结论：11 个去重候选全部 FAIL，0 进入对抗复核 — NO_NEW_CANDIDATES 计数 2/3。**

## 一、角度设计与查重（先查重后开搜）

round9 报告第五节反推的 6 个初版角度经 round3-9 累计 42 角度 grep 查重，**4 个撞历史被淘汰**
（CJK 特化 NLP 撞 round6 / grammar-constrained decoding 撞 round6 结构化输出 / prompt 压缩撞
round8 上下文工程 / 文本 diff 撞 round7 diff-patch），重设计为：

| 角度 | 主题 | 动机 | 有效候选 |
|---|---|---|---|
| G | 中文拼写纠错 CSC | 开屏「找错别字」承诺功能 | 3 |
| H | JSON schema 演进/契约迁移 | 34 子系统契约债真实史 | 0（检索空） |
| I | 多智能体失败案例 postmortem | 反向学习 | 0（MAST 官方仓无法定位） |
| J | 计算叙事学理论实现 | 叙事深度检测 | 0（全<3⭐） |
| K | 命名实体消解/别名解析 | NER 裸道误报真实史 | 2 |
| L | 日韩/同类网文工具生态 | 跨生态机制借鉴 | 6 |

20 条 search query（12 首批其中 5 条撞 403 限流 + 8 条按断点纪律补跑合并）。去重排除 2 个已挖实体
（AI_NovelGenerator=round1 已移植 4 机制；AI-Novel-Writing-Assistant=round3 已 FAIL）。

## 二、五问闸结果（11 项全 FAIL）

### G 中文拼写纠错/文本校对（CSC）

- **R10-G01** [`shibing624/pycorrector`](https://github.com/shibing624/pycorrector)（6485⭐ · Apache-2.0 · pushed 2026-06-04）— **FAIL**。Q1 否：全 memory/journal/lessons 零『错别字』事故记录（gen-model 生成正文非 CSC 目标场景——CSC 面向 OCR/ASR/人工输入）；grep 全 core/ 无 typo 检测层但也无需求观测；网文生造词（功法/境界/人名）对 kenlm/BERT 纠错是天然 OOV 误报面，接入 audit_hub 反而制造机械噪音违北极星⑤。开屏『帮用户找错别字』是主代理对话能力非脚本层。
- **R10-G02** [`igarashiakatuki/cnmbert`](https://github.com/IgarashiAkatuki/CNMBERT)（136⭐ · AGPL-3.0 · pushed 2026-01-10）— **FAIL**。场景不存在：面向拼音缩写黑话（yyds→永远的神）翻译，小说正文无此类输入；同 R10-G01 零观测。
- **R10-G03** [`jacob-zhou/simple-csc`](https://github.com/Jacob-Zhou/simple-csc)（89⭐ · Apache-2.0 · pushed 2025-07-09）— **FAIL**。同 R10-G01 零错别字事故观测；89⭐ 论文仓。

### K 命名实体消解/别名解析

- **R10-K01** [`moj-analytical-services/splink`](https://github.com/moj-analytical-services/splink)（2251⭐ · MIT · pushed 2026-07-10）— **FAIL**。域不匹配：Fellegi-Sunter 表格记录概率链接，输入是结构化记录对非叙事文本；人物别名归并机制已在 15 个脚本在位（character_identity_anchor_scanner/cluster_entity_stats/locked_fact_cross_scene 等 grep 实证）且无失效观测；历史『NER 裸道误报』是 NER 精度问题已修非归并缺陷。
- **R10-K02** [`benseverndev-oss/goldenmatch`](https://github.com/benseverndev-oss/goldenmatch)（121⭐ · MIT · pushed 2026-07-10）— **FAIL**。同 R10-K01 域不匹配+121⭐ 弱健康度。

### L 日韩/同类网文工具生态机制借鉴

- **R10-L01** [`kanasimi/work_crawler`](https://github.com/kanasimi/work_crawler)（4093⭐ · None · pushed 2026-04-06）— **FAIL**。合规红线：盗版内容批量下载工具（腾讯/大角虫/咪咕等站点爬取），违 feedback-reader-growth-compliance-redline 方向且与创作主链无关。
- **R10-L02** [`nigh/show-me-the-story`](https://github.com/Nigh/show-me-the-story)（328⭐ · MIT · pushed 2026-07-09）— **FAIL**。纯 GUI 工具向（Go 单二进制+web UI）优先排除；Go 栈宿主；机制未见超越。
- **R10-L03** [`yangqi1309134997-coder/ai-novel-generator`](https://github.com/yangqi1309134997-coder/ai-novel-generator)（238⭐ · NOASSERTION · pushed 2026-04-04）— **FAIL**。README 实核（API 拉取全文）：雪花法规划+章节蓝图+单章生成=章节级方案，与 round3 八连 FAIL 同构；产品形态是 Gradio GUI/商业多租户平台（会员/支付/卡密）；『连贯性分析』『润色去AI味』均有更精细 cluster 级原生实现（audit_hub 全家桶/反 AI 腔守卫+gen_fixer）。
- **R10-L04** [`writerslogic/scrivener-mcp`](https://github.com/writerslogic/scrivener-mcp)（31⭐ · AGPL-3.0 · pushed 2026-07-10）— **FAIL**。宿主不存在：Scrivener 是商业 GUI 写作软件，本系统无其项目文件；31⭐ 低于健康线。
- **R10-L05** [`jiqi136/ai-assistant`](https://github.com/jiqi136/Ai-Assistant)（28⭐ · Apache-2.0 · pushed 2026-06-08）— **FAIL**。28⭐ 低健康度+同类工具无新机制。
- **R10-L06** [`nonever2109/novel_writer_agent`](https://github.com/nonever2109/novel_writer_agent)（26⭐ · None · pushed 2026-05-13）— **FAIL**。26⭐ 低健康度+同类工具无新机制。

### 零候选角度的诚实记录

- **H schema 演进**：`json schema migration evolution` 与 `data contract validation` 两条正规限定词
  query 均 0 命中——Python 生态无独立强工具（Avro/Protobuf schema registry 是 Kafka 宿主）。仓内
  既有解法（scaffold_subsystems + subsystem_skeletons 34 骨架单一真理源，见 memory
  audit-hardening）在位，契约债root cause 已根治。
- **I postmortem**：MAST（arXiv:2503.13657）官方实现经 2 次定向搜索无法定位，只有 4 个 <2⭐ 引用仓
  ——provenance 不足从紧不猜 URL，0 有效候选。
- **J 计算叙事学**：6 命中全为 <3⭐ 玩具/课程仓，无一过工程健康线。

## 三、本轮结论

**11 去重候选 0 通过五问闸，无边界项，无对抗复核对象。NO_NEW_CANDIDATES 计数 2/3。**

拒因分布：无观测失效 5（G 组 3 + K 组 2）/ 同类无新机制 5（L 组）/ 合规红线 1 / 宿主不存在 1。
主导模式与 round3-8 一致：候选解决的问题在本系统中要么无真实观测（错别字/别名归并失效均零记录），
要么已有更贴合 cluster 粒度的原生实现。

## 四、下一轮角度反推（round11 · 决定轮）

本轮盲区：G-L 六角度中 4 个是「从系统承诺/历史事故反推」的防御向角度，命中率低的根因是
**这些事故都已被根治**（契约债→单一真理源、NER 误报→prompt 修复）——从「已修复历史」反推角度
天然撞 EXISTS。round11 转向「增量时间窗」策略（round5 曾用「近 30-60 天新发布」角度，时间窗已
滚动 2 天新内容有限，改用 2026 H1 学术会议窗）：
1. 2026 H1（ACL/NAACL/ICLR 2026）创意写作/长文本生成新论文的公开实现（时间窗增量）
2. LLM 长程记忆一致性的 2026 新基准与工具（区别于 round8 叙事 QA：面向 agent 记忆层）
3. 中文 TTS/有声书前处理工具链（断句/多音字标注——/export 下游生态，纯格式层）
4. 网文「爽点/情绪节拍」量化的中文学术实现（区别于 round6 情绪曲线：中文网文特化）
5. 确定性随机性控制/可复现采样（seed 管理、LLM 输出可复现性工程）
6. 写作过程遥测/键盘行为分析研究（keystroke dynamics 作者行为——蒸馏侧新数据源假设验证）
