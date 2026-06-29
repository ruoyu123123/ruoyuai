# 神经网络训练结果（本地 GPU RTX 4070 Ti · 2026-06-29）

环境：`core/ml/.venv`（Py3.10 + torch 2.6.0+cu124）· HF 模型经 `HF_ENDPOINT=https://hf-mirror.com` 镜像下载（huggingface.co 国内直连超时）。

## 训练结果（诚实评估）

| 模型 | 目录 | 结果 | 基线 | 评价 |
|---|---|---|---|---|
| **情绪VAD回归** | `emotion_vad/checkpoints/va_base` | best ep4 · meanCCC **0.7987**（valence CCC 0.896/r 0.897 · arousal CCC 0.702/r 0.702） | 启发式词典 valence Pearson 0.55 / arousal **0.21** | ✅ **大胜·held-out 泛化·可部署** |
| 风格-作者级 | `style_embed/runs/style_embed_v1/final` | ep3 · author verif AP **0.887**/acc 0.822 | char-3gram AUC 0.884 | ≈基线（char-3gram 本就强·NN 略胜·边际） |
| **风格-角色声纹** | `style_embed/runs/style_embed_char_v1/final` | char voice AUC **0.653**/acc 0.621 | char-3gram AUC 0.575 | ✅ **超基线 8 点**·短对话声纹本难·embedding 偏塌缩(cos 0.93-0.97)但判别力真实 |
| 质量AI腔判别 | `quality_clf/runs/macbert_v1` | val AUC **1.0**（3 epoch 全 1.0） | — | ⚠️ **过拟合·不可部署** |

## ⚠️ 质量AI腔判别为何不可部署（诚实）
val AUC=1.0 是**捷径学习铁证**：负样本仅 637 个 gemini 复刻样本(单生成器·都模仿这 10 作者)→ 模型完美分开但学的是「gemini 指纹+特定分布」非「通用 AI 腔」。换 DeepSeek/豆包/Qwen 必崩(arXiv:2509.00731 跨生成器塌到 76%)。**真泛化路径**：`gen_negatives.py` 多生成器(需加 DeepSeek/Qwen profile)补负样本到数千 + 拉 C-ReD 辅助迁移。当前模型当 held-out 对抗硬集种子·不上线。

## 🔴 集成架构关键问题（待决）
若渝主流水线跑在**系统 Python 3.14（无 torch）**，模型在 **venv Python 3.10（torch CUDA）**——两进程隔离。集成需 **subprocess 推理桥**：若渝 scanner 批量调 `core/ml/.venv/Scripts/python.exe <infer>.py --in jsonl --out jsonl`（cluster 级批量·非实时·可接受）。env 门控 + 启发式兜底(无 venv/模型时若渝照常跑·默认安全)。

## 集成落点（设计就绪·待实施·全 advisory）
- **emotion_vad**（最高价值）→ `vad_infer.py` 桥 → Appraisal `vad_bin`(save_state) + `character_vad_ued_scanner` + `emotion_curve_rescan` + Phase-0 替换 `core/data/*_placeholder.json`(40词→真7762词)
- **style**（author+char）→ `embedding_store.EMBED_BACKEND=ruoyu_style` → SFS(`style_evaluator`/skill_opt reward) + 千人千面(`character_distinctiveness_scanner` CROSS_CHARACTER_VOICE_COLLISION)
- **quality_clf** → 不集成(待多生成器数据)

## 复现
```
PY=core/ml/.venv/Scripts/python.exe; $env:HF_ENDPOINT='https://hf-mirror.com'
# emotion_vad: cd emotion_vad; $PY train.py --base hfl/chinese-roberta-wwm-ext --dims va --epochs 6 --fp16 --out checkpoints/va_base
# style author: cd style_embed; $PY train.py --base-model StyleDistance/mstyledistance --task author --loss batch-all-triplet --epochs 3 --precision bf16
# style char:  $PY train.py --task character --epochs 5 --output runs/style_embed_char_v1
# 🔴 Windows 必须 dataloader_num_workers=0(spawn+gradient_checkpointing 闭包不可 pickle)
```
