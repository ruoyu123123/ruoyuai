# quality_clf — 质量 / AI 腔判别 NN

> 🔴 2026-06-29 NN训练:质量AI腔判别
> 本地 GPU(RTX 4070Ti·12GB) 训练一个「真人人感(human) vs AI腔(ai)」中文网文 prose 判别器，
> 强化若渝现有审核（scanner 规则 + gemini judge）+ 喂自学习 reward。
> **北极星⑤铁律：判别分只做 advisory，永不 hard_gate。作者风格档是第一权威。**

---

## 0. TL;DR — 怎么跑

```bash
PY=core/ml/.venv/Scripts/python.exe        # 本项目 venv（后台在装 torch cu124+transformers...）

# ① 造数据集（纯 stdlib·无需 torch·可立即跑）
$PY core/ml/quality_clf/data_prep.py                       # 全量(别抽样·实测 ~78:1 不均衡)
$PY core/ml/quality_clf/data_prep.py --human-cap-per-author 60   # ★训练推荐:均衡到 ~10:1

# ②（可选·推荐）多生成器同题材负样本（用带 openai 的系统 Python·py -3）
py -3 core/ml/quality_clf/gen_negatives.py --max-samples 60 --mode continue

# ③ 训练（★主代理在 GPU 上跑·写脚本时别跑）
$PY core/ml/quality_clf/train.py --base hfl/chinese-macbert-base \
    --out-dir runs/macbert_v1 --use-feats --fp16 --epochs 3

# ④ 评估
$PY core/ml/quality_clf/eval.py --model-dir runs/macbert_v1 --split test --fp16

# ⑤ 跨作者泛化测试（防作者身份捷径·强烈建议）
$PY core/ml/quality_clf/data_prep.py --split-mode by_author --holdout-authors 惊悚乐园 剑来 --out-dir data_byauthor
```

---

## 1. 研究结论（联网调研接地 · 2024-2026 SOTA）

两路独立调研（中文 AIGC 检测方法 + 数据/特征），关键结论：

**方法（基座/架构）**
- 监督 encoder fine-tune 是 12GB 本地最务实路线。首选 **`hfl/chinese-macbert-base`(102M·全量 FT 轻松进 12GB)**，
  或 `IDEA-CCNL/Erlangshen-DeBERTa-v2-320M-Chinese`（DeBERTa-v2 通常居中文分类榜首）。大模型(710M/bge-m3/Qwen2.5-7B)走 **QLoRA r=16**。
- **encoder 跨生成器/跨域会塌**：RoBERTa-large 域内 99.6% → 漂移后 **76%**（arXiv:2509.00731）。
  decoder-LLM+LoRA 泛化更稳（Qwen2.5-7B+LoRA 漂移后 95.9%），但重。
- **perplexity/zero-shot（Fast-DetectGPT/Binoculars）在中文单用弱**（≈59%），但当**辅助特征**很有用——
  用「外族」打分模型（gemini 生成 → 用 `Qwen2.5-0.5B`/`uer/gpt2-chinese-cluecorpussmall` 算 PPL）。
  ⚠️「perplexity 反转」：现代 LLM 输出 PPL 反而**更低**于人类——别硬编方向，让分类器学符号。
- 超参默认：lr 2e-5 · batch 16-32 · max_len 512 · epochs 3 · fp16/bf16 · focal γ=2 · AdamW wd 0.01 · warmup 8%。
  长文切 384-512 窗口、多 chunk logit 取均值聚合。中文别先 jieba 分词（encoder 是字/子词原生）。

**头号风险 = 捷径学习（shortcut learning）**
- 分类器易学到「题材/作者身份/单一生成器指纹」而非真「人感 vs AI 腔」。
  缓解：①内容-表达解耦 + 题材对齐的**配对**数据（同开头续写/改写·arXiv:2503.00258）；
  ②融合**题材无关的确定性风格特征**（features.py·burstiness/套话密度/标点分布）；
  ③留出**未见作者 + 未见生成器**当 test（`--split-mode by_author`）；④focal loss + 加权采样治不均衡，**不丢数据**。

**关键来源**：arXiv:2509.00731(中文 LoRA) · 2402.01158(LLM-Detector·中文特征分析) · **2604.11796(C-ReD·含 Gemini 生成器·已开源 github.com/HeraldofLight/C-ReD)** · 2601.04633(MAGA·humanized 硬负) · 2503.00258(内容-表达解耦) · 2310.05130(Fast-DetectGPT) · 2401.12070(Binoculars) · 2312.01672(STADEE 统计特征) · arXiv:2604.03136(StoryScope·虚构 AI tell)。

**中文 AI 腔的可量化特征**（已编码进 features.py）：套话连接词(与此同时/然而/值得一提的是/不仅如此/事实上)密度、
「不是X而是Y」否定排比、对偶/排比过度、句长均匀(低 burstiness)、陈词情绪(顿时/淡淡/嘴角勾起)、抽象大词、
对话标签同义词循环、标点分布。⚠️**破折号信号在中文是 null**（AI 用得反而更少 0.58% vs 人 0.85%）——
features.py 保留 `dash_per_k` 但不预设方向，交分类器学。

---

## 2. 数据方案（诚实标注 · 这是本任务最大短板）

### 2.1 正样本 human（人感真文）— 充足
`workspace/styles/<作者>/原文/第NNNN章.txt`，10 位网文作者共 **7861 章**：
主神大道1477 · 遮天1821 · 诡秘之主1394 · 惊悚乐园1000 · 小世界其乐无穷880 · 轮回乐园499 ·
佛本是道250 · 剑来250 · 将夜250 · 人生长恨水长东40。

### 2.2 负样本 ai（AI 腔）— **稀缺·这是瓶颈**
磁盘现有可用负样本仅两路：
- **gemini 复刻样本** `styles/<作者>/复刻测试/**/​*replica*.txt`（蒸馏闭环里 gemini 模仿作者的产物）——
  **39 个可用文件**（已剔除 2 个拒答残片、3 个过短、2 个 `_原文_concat`(其实是真人原文!)、若干 `_eval_prompt` 元文本）。
- **系统自产草稿** `workspace/novels/主神验尸官/.../cluster_001_draft.txt`（1 个）。

切成 480CJK chunk 后 = **637 个 AI chunk**。

### 2.3 数据集产物（meta.json 实测 · 全量默认）

```
全量默认（python data_prep.py·实测 2026-06-29）：
  human_chunks = 49,754      ai_chunks = 637      不均衡 human:ai = 78.1 : 1
  train: human 39,755 / ai 556    （train.jsonl ≈ 93MB）
  val  : human 30    / ai 30      （已均衡·指标可信）
  test : human 51    / ai 51      （已均衡）
  AI 文件跳过：拒答 2 / 过短 3 / 不可读 0
每 chunk ≈ 480 CJK（max 512 token 无截断）；每 record 带 24 维 feats + source/author/kind。
```

- val/test **默认均衡**（下采样多数类到与 AI 等量）→ AUC/F1/PR 可信。
- train **保留全部人样本**（respects 别抽样）→ 不均衡交 train.py 的 focal loss + 加权采样器处理，**不丢数据**。

### 2.4 🔴 数据局限（必须诚实说明）

1. **没有公开的「中文小说域 人 vs AI」数据集**——HC3/M4/C-ReD/LLM-Detector/STADEE 全是问答/新闻/摘要/作文，
   且 C-ReD 自己的结论是「域不匹配主导检测难度」。**负样本只能自造**。
2. **现有 39 个复刻样本太少 + 单生成器(gemini) + 都在模仿这 10 位作者** → 直接训练，检测器学到的是
   「gemini/本管线指纹」+「作者身份」，**换 DeepSeek/豆包/Qwen 会崩**。研究建议把这 39 个当**珍贵的
   held-out 对抗硬集/种子**，不是训练分布（用 `data_prep.py --replicas-heldout-test`）。
3. **极端不均衡（实测 ~78:1）**：人样本 49,754 chunk vs AI 637 chunk。focal+采样器能缓解但治标——
   训练时建议 `--human-cap-per-author 60`（→ ~10:1）或先用 gen_negatives.py 把负样本补到数千。
4. **对抗性天然衰减**：本管线的目标就是把 AI prose 写得像人 → 任何检测器都会被自家 humanize 流程逐步绕过
   （MAGA 实测 AUC 掉 ~8%·StoryScope 风格 fine-tune 后检测 97%→3%）。**须定期重训** + 把自家修过的稿当 AI 正例做对抗加固。

### 2.5 最可行的负样本策略（优先级·研究背书）

| 级别 | 做法 | 现状 |
|---|---|---|
| **P0 立即可跑** | 39 复刻 + 草稿当 **bootstrap/对抗 held-out** 训一个 baseline·跑通全管线 | ✅ 已就绪（data_prep 默认） |
| **P1 推荐主力** | `gen_negatives.py` 从人样本开头**同题材配对**生成 AI 续写/改写·**多生成器** | ⚠️ 脚本就绪·但 .env 当前多半只有 gemini·要真泛化须加 DeepSeek/Qwen/豆包 profile |
| **P2 辅助迁移** | 拉 **C-ReD**(github.com/HeraldofLight/C-ReD·含 Gemini 等现代生成器) 做预训练/校准·HC3-Chinese 挖套话词表 | 📋 未拉（跨域·仅辅助·别当小说域真值） |

---

## 3. 文件清单

| 文件 | 作用 | 跑得了吗 |
|---|---|---|
| `features.py` | 24 维确定性风格特征（stdlib·抗捷径·与 semantic_slop 同源词表） | ✅ 纯 stdlib |
| `data_prep.py` | 造 human/ai 数据集 + source-level/by-author 切分 + eval 均衡 | ✅ 纯 stdlib·**已实测跑通** |
| `gen_negatives.py` | （可选）多生成器同题材配对负样本 | ⚠️ 需 openai（系统 py -3） |
| `train.py` | macbert-base fine-tune + 特征融合 + focal loss + 可选 LoRA/质量回归头 | 🟡 需 torch·**留 GPU 跑** |
| `eval.py` | held-out 评估（chunk/doc 级·按 kind 拆对抗鲁棒性·混淆矩阵） | 🟡 需 torch |
| `ai_tone_scanner.py` | 训好后包成 advisory scanner（对齐 audit_hub 契约） | 🟡 需 torch+模型 |
| `INTEGRATION.md` | 接入审核体系设计（方案 A scanner / 方案 B reward） | 📄 文档 |
| `requirements.txt` / `.gitignore` | 依赖 / 产物不入库（data/ runs/） | — |
| `data/` | 数据集产物（gitignore） | 自动生成 |

---

## 4. 推荐训练配方（GPU·主代理跑）

```bash
# 基线（macbert-base 全量 FT·融合风格特征·均衡 bootstrap）
$PY data_prep.py --human-cap-per-author 60                       # ~10:1
$PY train.py --base hfl/chinese-macbert-base --use-feats --fp16 \
    --epochs 3 --batch 16 --lr 2e-5 --out-dir runs/macbert_v1
$PY eval.py --model-dir runs/macbert_v1 --split test --fp16

# 跨作者泛化验证（必做·看是否学到作者身份捷径）
$PY data_prep.py --split-mode by_author --holdout-authors 惊悚乐园 剑来 --out-dir data_byauthor
$PY train.py --data-dir data_byauthor --use-feats --fp16 --out-dir runs/macbert_byauthor
$PY eval.py --model-dir runs/macbert_byauthor --data-dir data_byauthor --split test --fp16
# → 若 by_author 的 AUC 远低于 random_file → 在学作者身份·须补多生成器配对数据

# 大模型 LoRA（更稳泛化·更慢）
$PY train.py --base IDEA-CCNL/Erlangshen-DeBERTa-v2-710M-Chinese --lora --use-feats --fp16
```

**评估看什么**：不均衡下 accuracy 会骗人——看 **PR-AUC + AUC + TPR@1%FPR + 混淆矩阵**；
看 `by_kind` 里 `ai_replica`（humanize 硬负）掉多少；看 doc 级（线上按整章判）。

### 4.1 可选：PPL 辅助特征（提升泛化）
研究强推。可加 `compute_ppl.py`（未含·按需）：用 `Qwen/Qwen2.5-0.5B`(base) 算每 chunk 平均 token log-prob +
GLTR rank 桶占比 + burstiness，并进 features 向量。注意用**非 gemini 族**打分模型（自检测悖论）。

---

## 5. 集成

见 `INTEGRATION.md`。要点：训好 → `ai_tone_scanner.py` 复制进 `core/scripts/` → audit_hub 注册（仿 semantic_slop 那段）→
code `AI_TONE_NN` 天然 advisory（不进 HARD_GATE_CODES）→ **先 shadow 跑一整本校准阈值再转 active**。
reward 侧：把 NN 分**二值化**进 SkillOpt/writer reward 合取，只读 binary、永不硬卡。
