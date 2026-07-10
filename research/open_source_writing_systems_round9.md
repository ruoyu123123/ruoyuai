# 开源项目挖掘 Round 9（新循环第 1 轮 · 2026-07-11）

> 用户 2026-07-10 以 /goal 重新拉起 system-upgrade mining loop（独立于已三振完结的 round6-8 循环，
> NO_NEW_CANDIDATES 计数器从 0 重计；候选去重覆盖全历史 round3-8 约 260 项）。
> 本轮 run_id=`round9_2026-07-11`，机器可读候选 artifact 见
> `research/open_source_writing_systems_round9_candidates.json`（candidate_id/精确 URL/角度/
> 去重键/stars/forks/license/pushed_at/archived/初筛怀疑点/gate_verdict 全字段）。

## 一、本轮 6 个搜索角度（与 round3-8 累计 30 角度不重叠）

| 角度 | 主题 | 候选数 |
|---|---|---|
| A | 架构约束/依赖规则/防双路径回潮 | 8 |
| B | 证据溯源/citation persistence | 5 |
| C | 实验跟踪/benchmark registry/real-API validation ledger | 5 |
| D | durable execution/checkpoint replay | 6 |
| E | 文档漂移/code-doc-prompt 一致性 | 1 |
| F | pytest 测试隔离/flaky detection | 10 |

**检索执行记录**：6 角度均经 `api.github.com/search/repositories` 正规限定词检索
（`archived:false pushed:>=2025-01-01` + stars/language 叠加）；A/B/D/F 组合命中 0（限定词过窄），
其候选来自并行调研 agent 定向提名后主会话逐一 API 核验；E 命中 38（top10 仅 mex 过 stars≥50 健康线），
C 命中 1（trackio 入列）。全部 35 项元数据（stars/forks/license/pushed_at/archived/template/mirror）
经 repos endpoint 实拉非记忆。

**故障恢复记录（round7 纪律执行）**：上会话进程退出致 3 个搜索 agent 无完成记录——F 角度候选名单
自其 transcript 磁盘副本回收（10 项 pytest 生态）；E 角度 agent 输出为空，主会话以 search API 补检索；
两批尝试（首批 Explore 因权限拒联网 + _r2 批）与主会话核验结果已按实体合并去重，无随机重搜替代。

**去重**：grep research/ 全目录交叉验证，35 项均为全历史新实体。唯一先例：round7 曾 REFUTE
mlflow 的 MemAlign 子功能（非 mlflow 本体），审 R9-C01 时已引用。

## 二、五问闸结果（35 项：34 FAIL / 1 见第三节实测）

gate 纪律：先核外部原始来源（API 元数据 + README 实拉），再 Read/Grep 仓内现状，才 verdict；
「应该没有」式判断为零——本轮含 3 个**实测探针 gate**（vulture 实跑 / 文档路径漂移探针 / xdist 时长对比）。

### A 架构约束/依赖规则/防双路径回潮

- **R9-A01** [`ast-grep/ast-grep`](https://github.com/ast-grep/ast-grep)（15017⭐ · MIT · pushed 2026-07-10）— **FAIL**。Q1 否：北极星回归锁（tests/test_north_star_invariants.py 7 类）无已观测 grep 误报失效；引入 Rust 二进制依赖属预防性升级非缺口修补。
- **R9-A02** [`uber/piranha`](https://github.com/uber/piranha)（2456⭐ · Apache-2.0 · pushed 2026-04-02）— **FAIL**。Q1 否：本仓库无 feature-flag 系统宿主；两轮死码清理（a3061e5 净删 5600 行）为文档/模块级非 flag 死码；Piranha 深度支持偏 Java/Swift。
- **R9-A03** [`zyskarch/pytestarch`](https://github.com/zyskarch/pytestarch)（166⭐ · Apache-2.0 · pushed 2026-06-29）— **FAIL**。EXISTS：『splitter 不依赖质检』等关键 import 边界已由北极星不变量锁覆盖；全 memory/journal 无 import 方向违规事故，无漏网实例支撑新增架构断言层。
- **R9-A04** [`semgrep/semgrep`](https://github.com/semgrep/semgrep)（15829⭐ · LGPL-2.1 · pushed 2026-07-10）— **FAIL**。同 R9-A01：语义规则引擎解决的『grep 锁误报/漏报』问题未被观测；LGPL 引擎重。
- **R9-A05** [`seddonym/import-linter`](https://github.com/seddonym/import-linter)（1088⭐ · BSD-2-Clause · pushed 2026-07-03）— **FAIL**。同 R9-A03 同位竞争（import 契约三件套之一），共享『无漏网事故』否定证据。
- **R9-A06** [`gauge-sh/tach`](https://github.com/tach-org/tach)（2764⭐ · MIT · pushed 2026-06-11）— **FAIL**。同 R9-A03 同位竞争；模块边界现由北极星锁+plan 契约维护，无失效观测。
- **R9-A07** [`fpgmaas/deptry`](https://github.com/osprey-oss/deptry)（1436⭐ · MIT · pushed 2026-07-10）— **FAIL**。宿主弱+无观测：仓根仅 requirements.txt（无 pyproject/setup.py 包工程），依赖健康从无事故记录。
- **R9-A08** [`jendrikseipp/vulture`](https://github.com/jendrikseipp/vulture)（4684⭐ · MIT · pushed 2026-04-30）— **FAIL**。实测 gate：vulture 2.16 实跑 core/scripts（min-confidence 80）仅 13 条碎屑（未用局部变量/import，含 gen_writer.py:974 无害 if-False 残渣），0 整函数/模块死码——两轮人工清理纪律有效，无当前缺口。

### B 证据溯源/citation persistence

- **R9-B01** [`openlineage/openlineage`](https://github.com/OpenLineage/OpenLineage)（2533⭐ · Apache-2.0 · pushed 2026-07-10）— **FAIL**。宿主不存在：数据血缘标准面向 Airflow/Spark 类管道 job/dataset；research 证据链是文本 artifact。
- **R9-B02** [`trungdong/prov`](https://github.com/trungdong/prov)（134⭐ · MIT · pushed 2026-07-10）— **FAIL**。从紧：research provenance 断链无行为失效观测（round6 纪律：schema 缺口≠行为缺陷）；缺的抓取时间/HTTP 状态/快照哈希可直接扩 researcher artifact 字段，引 W3C PROV 全图模型过重且孤立于现有 research_ref 链。
- **R9-B03** [`researchobject/ro-crate-py`](https://github.com/ResearchObject/ro-crate-py)（86⭐ · Apache-2.0 · pushed 2026-07-10）— **FAIL**。设计冲突：STRUCTURE §九-bis 明确 research_cache 24h 复用/30 天清理/不入 git（URL 必失效、价值已融合正文），与科研持久打包哲学正面矛盾。
- **R9-B04** [`webrecorder/warcio`](https://github.com/webrecorder/warcio)（459⭐ · Apache-2.0 · pushed 2026-06-10）— **FAIL**。宿主不存在：WebFetch 由 harness 执行，脚本层拿不到原始 HTTP 响应流无法写 WARC；且与 research_cache 短生命周期矛盾。
- **R9-B05** [`manubot/manubot`](https://github.com/manubot/manubot)（473⭐ · NOASSERTION · pushed 2026-07-02）— **FAIL**。标识符体系不匹配：cite-by-ID 面向 DOI/PMID 学术源，网文调研来源无此标识；license NOASSERTION。

### C 实验跟踪/benchmark registry/real-API validation ledger

- **R9-C01** [`mlflow/mlflow`](https://github.com/mlflow/mlflow)（26968⭐ · Apache-2.0 · pushed 2026-07-10）— **FAIL**。平台宿主过重（server/UI/DB）+EXISTS（core/ml/registry ModelRegistry 已有 register/promote/compare+metrics）+round7 先例（其 MemAlign judge 功能已被独立怀疑者 REFUTE）。
- **R9-C02** [`iterative/dvc`](https://github.com/treeverse/dvc)（15737⭐ · Apache-2.0 · pushed 2026-07-10）— **FAIL**。EXISTS：MAPE-K 边界已定『Git 快照当锚点』；训练数据为小体量 jsonl，remote 版本化无需求观测。
- **R9-C03** [`datahub-project/datahub`](https://github.com/datahub-project/datahub)（12249⭐ · Apache-2.0 · pushed 2026-07-10）— **FAIL**。宿主不存在：需常驻元数据服务栈。
- **R9-C04** [`marquezproject/marquez`](https://github.com/MarquezProject/marquez)（2236⭐ · Apache-2.0 · pushed 2026-07-06）— **FAIL**。宿主不存在：OpenLineage 参考实现 server。
- **R9-C05** [`gradio-app/trackio`](https://github.com/gradio-app/trackio)（1569⭐ · MIT · pushed 2026-07-10）— **FAIL**。EXISTS+无观测：README 实核为 wandb 兼容 metrics 时序日志（SQLite+dashboard）；ModelRegistry.metrics 已覆盖版本级记录，NN 训练为一次性离线 fit 无曲线跟踪失效史；接入点（learning-loop 旁挂）非主链 required 位置。

### D durable execution/checkpoint replay

- **R9-D01** [`temporalio/sdk-python`](https://github.com/temporalio/sdk-python)（1127⭐ · MIT · pushed 2026-07-10）— **FAIL**。宿主不存在（Temporal server）+第二编排层违禁（SELF_LEARNING_ARCHITECTURE 明文禁）+EXISTS（wal_recovery.py 读 plan_tracker 持久态、continue.md 唯一断点权威）。
- **R9-D02** [`dbos-inc/dbos-transact-py`](https://github.com/dbos-inc/dbos-transact-py)（1469⭐ · MIT · pushed 2026-07-08）— **FAIL**。D 角度共同否定证据；库形态最轻但 durable 状态需 Postgres，单机现实无此宿主。
- **R9-D03** [`restatedev/sdk-python`](https://github.com/restatedev/sdk-python)（75⭐ · MIT · pushed 2026-07-01）— **FAIL**。宿主不存在（Restate server）+D 角度共同否定证据。
- **R9-D04** [`prefecthq/prefect`](https://github.com/PrefectHQ/prefect)（22894⭐ · Apache-2.0 · pushed 2026-07-10）— **FAIL**。完整编排平台=第二编排层，直接违禁。
- **R9-D05** [`dagster-io/dagster`](https://github.com/dagster-io/dagster)（15812⭐ · Apache-2.0 · pushed 2026-07-09）— **FAIL**。同 R9-D04。
- **R9-D06** [`hatchet-dev/hatchet`](https://github.com/hatchet-dev/hatchet)（7487⭐ · MIT · pushed 2026-07-10）— **FAIL**。任务队列平台需 server 宿主+第二编排层违禁。

### E 文档漂移/code-doc-prompt 一致性

- **R9-E01** [`mex-memory/mex`](https://github.com/mex-memory/mex)（1149⭐ · MIT · pushed 2026-07-08）— **FAIL**。实测 gate：①本体为 Node≥20 第二套记忆脚手架（AGENTS.md/ROUTER.md/patterns），与既有 memory 体系双轨违禁；②其 path/command checker 思想经探针实测无缺口（29 文档 62 路径引用 0 真缺失）；③已观测文档漂移（5cb214e/6138b74 两轮补漏）为语义级双口径，确定性 checker 覆盖不了。

### F pytest 测试隔离/flaky detection

- **R9-F01** [`pytest-dev/pytest-randomly`](https://github.com/pytest-dev/pytest-randomly)（713⭐ · MIT · pushed 2026-07-06）— **FAIL**。从紧：顺序依赖污染史（NN 门控 env 残留）已被 conftest autouse 隔离根治且此后零回归；randomly 属二道预防锁非当前失效修补，且全局 random.seed 重置对 8729 存量测试有未评估翻车面。
- **R9-F02** [`pytest-dev/pytest-forked`](https://github.com/pytest-dev/pytest-forked)（81⭐ · MIT · pushed 2026-04-14）— **FAIL**。宿主不存在：基于 os.fork，本机 win32 不支持。
- **R9-F03** [`dropbox/pytest-flakefinder`](https://github.com/dropbox/pytest-flakefinder)（157⭐ · NOASSERTION · pushed 2022-10-26）— **FAIL**。筛选规则硬排除：pushed_at=2022-10-26 违反 pushed>=2025-01-01，长期停更。
- **R9-F04** [`esss/pytest-replay`](https://github.com/ESSS/pytest-replay)（62⭐ · MIT · pushed 2026-06-23）— **FAIL**。无顺序型 flaky 观测（同 R9-F01 根治史）；录制回放解决的问题不存在。
- **R9-F05** [`kiwicom/pytest-recording`](https://github.com/kiwicom/pytest-recording)（612⭐ · MIT · pushed 2026-06-18）— **FAIL**。纪律冲突：『需 API 的测试真调 API 不节省』（feedback_real_api_tests_no_economize）+fake-LLM 三-seam 夹具已覆盖离线路径，VCR 录制回放两头不占。
- **R9-F06** [`miketheman/pytest-socket`](https://github.com/miketheman/pytest-socket)（342⭐ · MIT · pushed 2026-07-07）— **FAIL**。无事故+半覆盖：现状为 profile 级兜底（测试注入假 base_url='http://x'），全历史无『测试意外真调 API』事故；socket 级全局禁网属预防性。
- **R9-F07** [`tox-dev/tox`](https://github.com/tox-dev/tox)（3912⭐ · MIT · pushed 2026-07-09）— **FAIL**。单机单 Python 环境，多环境矩阵无需求。
- **R9-F08** [`aklajnert/pytest-subprocess`](https://github.com/aklajnert/pytest-subprocess)（117⭐ · MIT · pushed 2026-05-26）— **FAIL**。EXISTS：13 个测试文件已用标准 monkeypatch 模式 mock subprocess，无痛点观测。
- **R9-F09** [`pytest-dev/pytest-xdist`](https://github.com/pytest-dev/pytest-xdist)（1873⭐ · MIT · pushed 2026-06-29）— **FAIL**（唯一边界项 · 双实测定案，见第三节）。
- **R9-F10** [`pytest-dev/pytest-rerunfailures`](https://github.com/pytest-dev/pytest-rerunfailures)（468⭐ · NOASSERTION · pushed 2026-07-01）— **FAIL**。纪律方向相反：失败自动重跑掩盖 flaky 而非根治（违『问题根治』与『验证须查 stderr 不信 exit code』）；license NOASSERTION。

## 三、边界项实测：R9-F09 pytest-xdist

唯一进入实测的边界项。假设「全量回归（required 纪律）在 8729 测试规模下串行时长是循环真实瓶颈」，
本机双实测（2026-07-11，pip 临时安装、测毕即卸载不留环境存量）：

| 模式 | 结果 | 耗时 |
|---|---|---|
| 串行 `pytest tests/ -q` | 8716 passed / 13 skipped / **0 failed** | **230.02s（3分50秒）** |
| 并行 `pytest tests/ -q -n auto` | 8716 passed / 13 skipped / **0 failed** | **98.32s（1分38秒）** |

- 加速比 2.34x，失败集完全一致；conftest 唯一 autouse fixture 为 function-scoped env 隔离，
  xdist 多进程下隔离性反而更强，无 session 共享资源——**并行安全性一次实测通过**。
- **但 Q1 定案 FAIL**：①串行基线仅 230 秒，「测试慢」从未被观测为痛点（memory/journal 零记录，
  实测证实基线本来就快，循环瓶颈在 LLM 调用与人工决策不在测试）；②2.2 分钟/轮的节省属舒适性优化，
  违「默认优先补真实已观测失效模式」；③引入代价是「8716 测试永久保持并行安全」的新维护不变量 +
  新 dev 依赖，一次实测通过≠长期安全承诺；④无主链/既有轨道 required step 落点。
- **未来重开条件（数据留档）**：若测试规模增长致串行全量 >10 分钟且被真实观测为循环拖累，
  本节实测数据（2.34x/0 失败一致）即当时的 PASS 依据，无需重测可直接进对抗复核。

## 四、本轮结论

**35 个去重候选，0 个通过五问闸；无候选进入对抗复核（round6-8 惯例：仅五问闸 PASS 的边界项进
独立怀疑者，本轮唯一边界项已被双实测定案 FAIL）。**

- 本轮为用户 2026-07-10 新 /goal 循环的第 1 轮：**NO_NEW_CANDIDATES 计数 1/3**。
- 本轮方法论增量：①api.github.com 元数据批拉（29 项）+ search API 正规限定词检索首次全角度落地；
  ②3 个实测探针 gate（vulture 实跑=13 条碎屑 0 死码 / 文档路径探针=62 引用 0 真缺失 /
  xdist 双计时对比）取代「应该没有」式推断——全部 verdict 均有可复跑证据。
- 顺带产出：全量套件基线数据（8716 passed / 230s 串行）+ 5 处 `datetime.utcnow()` DeprecationWarning
  线索（arc_aggregator/character_arc_aggregator，Python 3.14 下仍容忍，未来 Python 升级需处理，
  advisory 非本轮集成对象）。

## 五、下一轮角度反推（自本轮拒因盲区）

本轮拒因分布：宿主不存在 11 / EXISTS 8 / 无观测失效 9 / 纪律冲突 4 / 停更 1 / 双轨 2。
盲区分析：本轮 6 角度全部是**工程基础设施**方向（架构/溯源/跟踪/执行/文档/测试），零创作能力角度——
round3-8 已密集扫过创作侧（叙事引擎/角色建模/judge/记忆），但 goal 允许「带公开实现的论文仓库」，
且近月（2026 上半年）新发论文实现是滚动增量源。下轮候选角度（须再查重）：
1. 2026 新发长文本创作评估论文的公开实现（LongForm eval 类，注意 round7 EQ-bench 先例的章节化陷阱）
2. 中文网文/CJK 特化 NLP 工具新库（分词/指代/文体计量的 2025-2026 新实现）
3. LLM 输出结构化约束/grammar-constrained decoding（若 gen-model 中转支持）
4. 多智能体写作系统的**失败案例研究**仓库（反向学习：别人踩过的坑）
5. prompt 压缩/上下文管理新实现（manifest 注入密度优化方向）
6. 确定性文本 diff/一致性校验新工具（cluster 修订链质量方向）
