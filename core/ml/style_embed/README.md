# style_embed — 风格/声纹嵌入 NN 训练管线

> 🔴 2026-06-29 NN训练:风格声纹嵌入
> 用对比学习 fine-tune 一个中文文本 encoder，产出**风格嵌入向量**，一模型两用：
> ① 作者级 **SFS** 风格保真度（北极星：写出和作者风格一致的文章）
> ② 角色级 **千人千面** 声纹互距（防角色同腔）。
> 本地单卡 **RTX 4070Ti 12GB**。

## 文件
| 文件 | 作用 | 能否本地跑 |
|---|---|---|
| `data_prep.py` | 10 作者原文 → chunk + 对比正负对 + 角色台词（纯 stdlib） | ✅ 已跑 |
| `train.py` | torch + sentence-transformers 对比学习 fine-tune | ⚠️ GPU，主代理集中跑 |
| `eval.py` | 留出作者 AUC/EER/检索 + 角色声纹 + 对标启发式 SFS | ✅（装完依赖即可） |
| `INTEGRATION.md` | 怎么接回 SFS / 千人千面 子系统 | 设计文档 |
| `data/` | 产出数据集（gitignore） | — |
| `runs/` | 训练产物 + eval 报告（gitignore） | — |

## 环境
```bash
PY=core/ml/.venv/Scripts/python.exe   # Py3.10 + torch 2.6 cu124（后台装着，别重装）
# 缺包时：$PY -m pip install 'sentence-transformers>=3.0' datasets peft accelerate scikit-learn
```

## 1) 数据准备（已跑，2 分钟，纯 stdlib）
```bash
$PY core/ml/style_embed/data_prep.py
# 可选：--chunk-size 768 --holdout-authors 佛本是道,将夜 --no-characters
```
**产出规模**（默认 chunk=512 CJK）：

| split | chunk 数 | 说明 |
|---|---|---|
| train | 30,503 | 8 位作者（held-out 2 位除外） |
| val | 4,020 | 训练作者留出章节 |
| test_seen | 4,072 | 训练作者留出章节（SFS 同分布验证） |
| test_unseen | 2,998 | **未见作者**（佛本是道+将夜）整本 → 泛化 = 新书场景 |
| MNRL 正对 | train 15,123 / val 2,009 | 同作者 (anchor,positive) |
| 角色台词 | 245 个角色 | phase2（封不觉 1936 句/32k 字等） |

共 **41,593** chunk，10 作者 7,861 章 ~25M+ CJK。`data/manifest.json` 有完整统计。

## 2) 训练（主代理单卡跑，别在子代理跑）
```bash
# 作者级（SFS 用）——少作者默认 SupCon 等价的 BatchAllTripletLoss
$PY core/ml/style_embed/train.py \
    --base-model StyleDistance/mstyledistance --task author \
    --loss batch-all-triplet --batch-size 48 --grad-accum 2 \
    --epochs 3 --precision bf16
# OOM → --batch-size 32 --grad-accum 4，或 --lora，或换 --base-model BAAI/bge-base-zh-v1.5
# 角色级（千人千面 phase2）
$PY core/ml/style_embed/train.py --task character --loss batch-all-triplet \
    --batch-size 32 --epochs 5
```
产物：`runs/style_embed_v1/final/`（SentenceTransformer 格式 + `ruoyu_meta.json` 记 dim）。

## 3) 评估（对标基线）
```bash
$PY core/ml/style_embed/eval.py --model runs/style_embed_v1/final     # NN 模型
$PY core/ml/style_embed/eval.py --baseline char3gram                  # 现状基线
$PY core/ml/style_embed/eval.py --baseline StyleDistance/mstyledistance  # 零样本基座
$PY core/ml/style_embed/eval.py --model runs/.../final --character    # 角色声纹
$PY core/ml/style_embed/eval.py --model runs/.../final --sfs-compare workspace/styles/诡秘之主/复刻测试/v_final
```

**现状基线（char-3gram · NN 必须超过）** — 已实测：

| 指标 | char-3gram 基线 AUC | NN 目标 |
|---|---|---|
| test_seen 作者验证 | **0.8845** | 显著 > 0.88 |
| test_unseen 未见作者 | **0.8265** | 显著 > 0.83（泛化是关键） |
| 角色声纹（phase2） | **0.7566** | → DramaCV 量级 ~0.82 |

## 4) 集成
见 `INTEGRATION.md`：新增 `EMBED_BACKEND=ruoyu_style` 后端 → `style_evaluator`/`reward_sfs`
挂 embedding-SFS（落点①），`character_distinctiveness_scanner` 挂 `CHARACTER_VOICE_EMBED=1`
NN 声纹互距（落点②）。全 advisory、确定性、零新 hard_gate（北极星⑤）。

## SOTA 依据
- **UAR/LUAR**（Rivera-Soto, EMNLP2021 · LLNL/LUAR）：同作者多文档监督对比、零样本跨域迁移。
- **DramaCV**（arXiv:2406.11368）：角色累积台词 512-dim 声纹，同段角色互为 in-batch 负例，AUC 协议。
- **SupCon**（arXiv:2004.11362）：多正例监督对比 → 少 class 场景无 false-negative（我们 8-10 作者的正解）。
- **SimCSE**（arXiv:2104.08821）：对比句向量，temperature≈0.05。
- **StyleDistance / mStyleDistance**（arXiv:2410.12757 / 2502.15168）：content-independent 风格嵌入，
  合成平行样本对比、40 风格特征、多语含中文 → **最对口的基座**（已是 embedding_store 的 mstyle 后端）。

## 设计要点
- **基座选型**：默认 `StyleDistance/mstyledistance`（风格而非语义、含中文、768-dim、sub-1B 全量可训）；
  备选 `BAAI/bge-base-zh-v1.5`（BERT/102M/768-dim，中文原生，12GB 轻松全量 fine-tune）。
- **损失选型**：8-10 作者 class 极少 → InfoNCE/MNRL 的 in-batch 负例必撞同作者（false negative）→
  默认 **BatchAllTripletLoss（SupCon 等价，同 label 全为正例）** + `GROUP_BY_LABEL` 采样器。
  MNRL 留给角色级（245 类）/将来作者扩容。
- **12GB**：bf16 混合精度 + gradient_checkpointing + grad_accum 放大有效 batch；LoRA 可选。
- **预算不限**：data_prep 全量不抽样（章节级 split），训练数据 30k+ chunk、15k 对，多 epoch。
