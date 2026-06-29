<!-- 🔴 2026-06-29 NN训练:情绪VAD回归 -->
# 情绪 VAD 回归模型（中文）· 训练管线

预测中文文本的 **Valence（效价）/ Arousal（唤起）/ Dominance（支配）** 三维情绪连续值（归一 [0,1]）。
为若渝两处服务：① Appraisal Beat 的 `vad_bin`（现为启发式手判）② 情绪曲线 / 角色 VAD-UED scanner（现为词典查表）。
本地 GPU 训练（RTX 4070Ti 12GB·fp16）。接入设计见 **[INTEGRATION.md](./INTEGRATION.md)**。

## 文件

| 文件 | 作用 | 依赖 | 能否现在跑 |
|---|---|---|---|
| `data_prep.py` | 下载+解析+归一+切分数据 | **纯 stdlib** | ✅ 已跑通 |
| `metrics.py` | MAE/RMSE/Pearson/CCC | **纯 stdlib** | ✅ |
| `model.py` | 中文 encoder + sigmoid 回归头 + CCC loss | torch+transformers | 定义 |
| `train.py` | fine-tune（**写好·主代理集中跑 GPU·别由子 agent 跑**） | torch+transformers+numpy | 🔴 待 GPU |
| `eval.py` | held-out 指标 + 对标词典基线 | 基线纯 stdlib / 模型需 torch | ✅(`--baseline-only`) |
| `vad_infer.py` | **集成桥**·确定性推理（模型/词典） | 模型需 torch·词典纯 stdlib | ✅(词典模式) |
| `data/` | 数据集+checkpoint（**.gitignore**·不入库） | — | — |

## 数据（来源 / 许可 / 规模 / 粒度 / 量纲）

> **诚实先行：中文几乎没有原生「句级三维 VAD」数据——可获取的中文维度情感资源基本只有二维 VA（无 dominance）。**
> 本管线默认训 **VA 二维**（数据扎实），D 维作为弱标注/迁移的可选增量（见 §数据局限）。

| 数据集 | 来源 | 许可 | 规模 | 粒度 | 维度 | 量纲 |
|---|---|---|---|---|---|---|
| **Chinese EmoBank**（CVAW/CVAP/CVAS/CVAT） | [github.com/NYCU-NLP/Chinese-EmoBank](https://github.com/NYCU-NLP/Chinese-EmoBank) | repo 无显式 LICENSE·学术引用 ACM-TALLIP-2022 / NAACL-2016·**商用需邮件 NYCU-NLP** | CVAW 5512 词 / CVAP 2250 短语 / **CVAS 2583 句** / **CVAT 2971 篇** | 词·短语·**句**·篇 | **VA**（无 D） | 1–9 |
| **DimABSA 2026**（zho restaurant/laptop） | [github.com/DimABSA/DimABSA2026](https://github.com/DimABSA/DimABSA2026) | **CC BY 4.0**（最干净·首选） | restaurant 6050 + laptop 3490（aspect→句级聚合） | 句（aspect 聚合） | **VA** | 1–9 |
| NRC-VAD（中文译版·D 维可选补） | [saifmohammad.com/.../nrc-vad](https://saifmohammad.com/WebPages/nrc-vad.html) | 研究免费·商用需联系·**需邮件申请下载** | 20k/55k 词 | 词 | **VAD**（含 D） | 0–1 |

- **归一**：valence/arousal `1–9 → (x-1)/8 → [0,1]`（与系统 `_score_vad` / `vad_bin` 量纲一致）。
- **句级主集**（`data_prep.py` 实跑产出）：CVAS + CVAT + DimABSA(zho rest/lap) 去重过滤后 **15025 句** → train 12020 / val 1502 / test 1503。
- **词级辅助**（`word_lexicon.jsonl`）：CVAW+CVAP **7762 词**（可直接升级系统占位词典·见 INTEGRATION §5 Phase 0）。
- **繁体**：数据是繁体中文；系统正文简体 → 生产用 `--simplify`（opencc）转简体，推理端同口径。
- DimABSA `finance_task1` 文件是 aspect-抽取专用（无 VA 标注）→ data_prep 自动丢弃（已核验·非 bug）。

## 模型选型（SOTA 对标·2023–2026）

- **基座**：默认 `hfl/chinese-roberta-wwm-ext`（base 102M）；上限用 `-large`（325M）或 `IDEA-CCNL/Erlangshen-Roberta-330M-Sentiment`（已在 22.7 万中文情感样本微调·warm-start 收敛快）。12GB fp16 训 large 可行（batch 8 + 梯度累积）。
- **回归头**：mean-pool → dropout → Linear(hidden, n_dims) → sigmoid（[0,1]）。
- **Loss**：CCC + MSE 混合（`--alpha`，默认 0.5）。CCC（一致性相关）在维度情感回归普遍优于纯 MSE（arXiv:2203.07378 / ESANN-2023）。
- **指标**：每维 MAE / RMSE / **Pearson r** / **CCC**。业界共识：**valence 好学、arousal 次之、dominance 最难**。

## 怎么跑

```bash
PY="D:/Desktop/ruoyuai/core/ml/.venv/Scripts/python.exe"   # 已含 torch cu124+transformers

# 1) 数据（已跑过·幂等·缓存）
$PY data_prep.py --with-dimabsa                 # 默认繁体
# $PY data_prep.py --with-dimabsa --simplify    # 生产：繁→简（先 pip install opencc-python-reimplemented）
# $PY data_prep.py --with-dimabsa --nrc-vad nrc_vad_zh.tsv   # 加 D 维弱标签 → 可训 --dims vad

# 2) 训练（🔴 主代理在 GPU 上跑·别由子 agent 跑）
$PY train.py --base hfl/chinese-roberta-wwm-ext --dims va --epochs 6 --batch 32 --lr 2e-5 --fp16 --out checkpoints/va_base
#  large（12GB·梯度累积）：
$PY train.py --base hfl/chinese-roberta-wwm-ext-large --dims va --epochs 6 --batch 8 --grad-accum 4 --lr 1e-5 --fp16 --max-len 256 --out checkpoints/va_large

# 3) 评估（对标词典启发式基线）
$PY eval.py --baseline-only                     # 只看启发式有多弱（无需 GPU）
$PY eval.py --ckpt checkpoints/va_base          # 训完：模型 vs 基线 ΔCCC

# 4) 推理桥自测
$PY vad_infer.py "他攥紧了拳头，怒火中烧"        # 无 checkpoint 走词典；设 RUOYU_VAD_CKPT 走模型
```

## 启发式基线（已实测·待模型超越）

`eval.py --baseline-only`（CVAW/CVAP 词典聚合·覆盖率 89.2%·test 1503 句）：

| 维 | MAE | RMSE | Pearson | CCC |
|---|---|---|---|---|
| valence | 0.114 | 0.144 | **0.550** | 0.548 |
| arousal | 0.129 | 0.159 | **0.207** | 0.172 |

→ valence 词典尚可，**arousal 词典几乎无效**（r=0.21）。真模型最该补的是 arousal（及全维 CCC）。这是训练成败的硬基准线。

## 12GB GPU 注意

- base 模型 batch 32 / seq 128 轻松；large 用 batch 8 + `--grad-accum 4` + `--max-len 256` + `--fp16`。
- AMP fp16 已内建（`torch.cuda.amp`）；显存仍紧可降 batch/seq 或上 LoRA。
- 确定性：固定 `--seed`，`train.py` 已 set torch/numpy/random seed。

## 集成

见 **[INTEGRATION.md](./INTEGRATION.md)**——三个落点（Appraisal `vad_bin` / `character_vad_ued_scanner._score_vad` / `emotion_curve_rescan_scanner`），全走 `vad_infer.py`，全 advisory，env 门控默认 off（零行为变化）。

## 数据局限（再次诚实）

1. **Dominance 中文无原生句级数据** → 默认 VA 模型，D 维走词典/summarizer 兜底或弱/迁移监督（精度低）。
2. **繁体训练 vs 简体推理** → 生产须 opencc 同口径，否则有字形 OOD。
3. **领域偏移**：训练数据是评论/新闻/影评，与网文叙事文体有分布差；当作 advisory 传感器（非判决）已规避风险，但 D/A 绝对值仅供相对趋势参考。
4. EmoBank 标签是众包均值（带 SD），本身有标注噪声（已保留 `Valence_SD`/`Arousal_SD` 于 raw·可用于加权/置信过滤的后续增强）。
