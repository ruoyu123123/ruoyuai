# Fine-Tune 指南 · 若渝AI v19.6（G9 文档化）

> 本文档不是必读——若你（用户）想把整套 AI 写作系统的天花板再推一档，本文给出业界已验证的 fine-tune / DPO / LoRA 路径。
>
> **当前 v19.6 系统纯 prompt engineering，理论上限受 base 模型约束**。
> 切换到 fine-tune 模型后，风格匹配 / voice 复刻能力可显著提升。

---

## 1. 为什么需要 Fine-Tune

业界证据（arxiv 2509.14543 / 2509.24930 / OpenAI Cookbook）：

- **few-shot 比 zero-shot 提升 23.5x**（v19.6 G4 已做）
- **completion 模式可达 99.9% 风格匹配**（fine-tune 必经路径）
- **base 模型不会写古风，再多 prompt 也写不出真古风**——天花板是模型本身

商业工具已应用：
- **Sudowrite Muse**：自训模型，「专门在已出版长短篇小说上微调」
- **NovelAI Erato/Xialong**：GLM-4.6 微调（355B MoE 全用户）
- **OpenAI 官方**：把"fictional brand voice and style"列为 DPO 典型用例

---

## 2. Fine-Tune 三种路径对比

| 方法 | 数据量需求 | 训练时长 | 成本 | 适合 |
|---|---|---|---|---|
| **SFT**（supervised fine-tune）| 1K-10K 样本 | 数小时 | 中 | 标准微调，新作家声纹复刻 |
| **DPO**（direct preference）| 500-5K 偏好对 | 数小时 | 中 | 用户选稿/拒稿数据已有 |
| **LoRA + 5-10 分钟数据** | 100-500 短样本 | 10-30 分钟 | 低 | 单个项目快速适配 |
| **KTO**（Kahneman-Tversky）| 1K 二元偏好 | 数小时 | 中 | 不需要成对偏好数据 |

---

## 3. 推荐路径：LoRA + DPO 双阶段

### 阶段 A · SFT 基础适配（项目内）

数据准备：
- **正样本**：用户在本系统已写章节 ch1-N 的正文（4 章 × 5000 字 = 20K token）
- **风格基线**：作者风格.json + skill_FINAL.md
- 用 build_manifest 的 must_read 字段作 system prompt
- 章节文本作 completion

训练：
```python
# 用 unsloth / axolotl / TRL 一类框架
from trl import SFTTrainer
trainer = SFTTrainer(
    model="claude-haiku-4-5",  # 或 Qwen2.5-7B / Llama-3.1-8B
    train_dataset=ds,
    peft_config=LoraConfig(r=16, lora_alpha=32),
    args=SFTConfig(num_train_epochs=2)
)
```

10 章 5000 字 + LoRA r=16 → 单卡 RTX 4090 ≈ 30 分钟

### 阶段 B · DPO 偏好对齐

数据准备：
- **chosen**（高质量）：本项目 audit_dashboard 给 A 级的章节
- **rejected**（低质量）：被 validator-repair 修过 ≥3 轮的早期草稿（如本项目 ch3 修复前后 7600→4998 的对比）
- 每对生成 `{prompt, chosen, rejected}` 三元组

训练：
```python
from trl import DPOTrainer
dpo_trainer = DPOTrainer(
    model=sft_model, ref_model=base,
    train_dataset=preference_ds,
    beta=0.1,
)
```

---

## 4. 我们已有的数据资产

可直接转 fine-tune 数据：

| 资产 | 路径 | 用途 |
|---|---|---|
| 章节正文 | `章节/第NNN章/第NNN章.txt` | SFT 完成示例 |
| validator-repair 修复痕迹 | `章节/第NNN章/第NNN章.repair.json` | DPO 偏好对 |
| 章纲摘要 judge_reports | `_数据库/章纲摘要.json` | grade 标签 |
| 风格蒸馏 | `_数据库/作者风格.json` | system prompt + golden_passages |
| 用户选稿历史 | git log + 用户走向卡选择 | RLHF / DPO 弱信号 |

---

## 5. 训练后如何接入本系统

只需替换 writer agent 的底层模型：
- 改 `.claude/agents/novel-writer.md` 顶部 model 字段
- 或用 Anthropic API key 切换到 fine-tuned model id
- 其他流水线（build_manifest / audit_hub / scanners）**全部不变**

---

## 6. ROI 决策树

```
你的项目章数 ≤ 30 → 不要 fine-tune（数据不够）
你的项目章数 31-100 → LoRA SFT 一阶段（数据刚够）
你的项目章数 > 100 → SFT + DPO 双阶段（数据充足，收益明显）
你想跨项目复用风格 → 蒸馏出"诡秘风格"通用 LoRA adapter
```

---

## 7. 不推荐 Fine-Tune 的场景

- 试水短篇（< 30 章）
- 风格未稳定（蒸馏 confidence < 0.7）
- 单次使用（一次性创作）

---

## 8. 与现有 v19.6 体系的关系

| 系统能力 | Fine-Tune 影响 |
|---|---|
| build_manifest / 跨章扫描 / save-state 流水线 | **不变**（接口层无关）|
| 风格蒸馏 / golden_passages | **强化**（作为 SFT 数据）|
| judge_reports / audit_hub | **不变**（检测层无关，但归一化后评分更稳）|
| writer prompt | **简化**（fine-tune 后可砍 70% 显式约束）|

---

## 9. 业界资源

- **OpenAI Cookbook DPO**：https://developers.openai.com/cookbook/examples/fine_tuning_direct_preference_optimization_guide
- **HuggingFace TRL**：https://huggingface.co/docs/trl
- **Unsloth 教程**：https://github.com/unslothai/unsloth
- **Axolotl 框架**：https://github.com/axolotl-ai-cloud/axolotl
- **NovelCritique 论文**：arxiv 2512.12839
- **WebNovelBench**：arxiv 2505.14818

---

## 10. v19.6 系统不做 Fine-Tune 的原因

当前章数 4 章，数据不足；目标是"工程纪律 + 蒸馏深度"双优势已超出大多数商业工具。Fine-Tune 是未来扩展路径，不是当前刚需。

> **如果未来项目跑到 100+ 章，本指南可作为升级路线图**。
