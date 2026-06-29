# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN连贯性评分集成
"""model.py — 中文连贯性二分类模型定义（共享给 train.py / eval.py / coherence_infer.py）。

架构（SOTA 对标·见 README §模型）：
  中文 encoder（默认 hfl/chinese-roberta-wwm-ext · 102M）
    → mean-pool（attention-mask 加权）或 [CLS]
    → dropout
    → Linear(hidden, 2)          ← 二分类头（incoherent / coherent）
    → 原始 logits（forward 不过 softmax，交给 loss / predict_proba）

任务：判定相邻文本块（场景/段落对）是否连贯。class 1 = coherent（正样本，
人工/真作者真实接续），class 0 = incoherent（负样本，乱序/拼接/跨文打乱）。
正负样本天然不均衡（构造负例可远多于正例）→ 默认 Focal Loss（Lin 2017）治不均衡，
标准交叉熵（bce_loss）作兜底 fallback。

本文件零 I/O 副作用、纯定义，import torch 失败时给出清晰报错。
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import torch
    import torch.nn as nn
    from transformers import AutoModel, AutoConfig
except ImportError as e:  # pragma: no cover - 环境未装 torch 时的清晰报错
    raise ImportError(
        "model.py 需要 torch + transformers。请用 core/ml/.venv 并安装 "
        "requirements.txt（torch cu124 + transformers）。原始错误：%s" % e
    )


N_CLASSES = 2
LABEL_NAMES = ("incoherent", "coherent")  # index 0 / 1


# ----------------------------------------------------------------------------- loss
def focal_loss(logits, targets, alpha: float = 0.25, gamma: float = 2.0):
    """二分类 Focal Loss（Lin 2017·治正负样本不均衡）。

    logits: (B, 2) 原始 logits；targets: (B,) long ∈ {0,1}。
    alpha 为正类(1)的权重，负类(0)的权重 = 1-alpha；gamma 越大越聚焦难样本。
    与 quality_clf/train.py 的 focal_loss 同范式（log_softmax + (1-p_t)^gamma）。
    """
    logp = nn.functional.log_softmax(logits, dim=-1)
    p = logp.exp()
    logp_t = logp.gather(1, targets.unsqueeze(1)).squeeze(1)
    p_t = p.gather(1, targets.unsqueeze(1)).squeeze(1)
    # alpha_t：正类 alpha、负类 1-alpha（RetinaNet 二分类 alpha-balancing）
    alpha_t = torch.where(
        targets == 1,
        torch.full_like(p_t, alpha),
        torch.full_like(p_t, 1.0 - alpha),
    )
    loss = -alpha_t * ((1.0 - p_t) ** gamma) * logp_t
    return loss.mean()


def bce_loss(logits, targets):
    """标准交叉熵兜底（2-logit softmax 头的 BCE 等价形式）。

    logits: (B, 2)；targets: (B,) long ∈ {0,1}。作为 focal_loss 的 fallback。
    """
    return nn.functional.cross_entropy(logits, targets)


# ----------------------------------------------------------------------------- model
class CoherenceClassifier(nn.Module):
    """中文 encoder + 二分类头（连贯性 incoherent / coherent）。"""

    def __init__(self, base_model: str = "hfl/chinese-roberta-wwm-ext",
                 dropout: float = 0.1, pooling: str = "mean",
                 encoder_config=None):
        super().__init__()
        self.base_model = base_model
        self.pooling = pooling
        self.n_classes = N_CLASSES
        self.label_names = LABEL_NAMES
        # 🔴 2026-06-29 NN连贯性集成 — 离线自包含加载：
        #   给定 encoder_config（来自 checkpoint 本地 config.json）→ from_config（不下载预训练权重，
        #   随后 load_state_dict 覆盖为真权重）。训练首建 / 无本地 config 时退 from_pretrained（联网拉骨架）。
        if encoder_config is not None:
            self.encoder = AutoModel.from_config(encoder_config)
        else:
            self.encoder = AutoModel.from_pretrained(base_model)
        hidden = self.encoder.config.hidden_size
        self.dropout = nn.Dropout(dropout)
        self.head = nn.Linear(hidden, self.n_classes)

    def _pool(self, last_hidden, attention_mask):
        if self.pooling == "cls":
            return last_hidden[:, 0]
        # mean pool（mask 加权·稳定且对句对分类友好）
        mask = attention_mask.unsqueeze(-1).type_as(last_hidden)
        summed = (last_hidden * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1e-6)
        return summed / counts

    def forward(self, input_ids, attention_mask, token_type_ids=None):
        kwargs = {"input_ids": input_ids, "attention_mask": attention_mask}
        if token_type_ids is not None:
            kwargs["token_type_ids"] = token_type_ids
        out = self.encoder(**kwargs)
        pooled = self._pool(out.last_hidden_state, attention_mask)
        pooled = self.dropout(pooled)
        logits = self.head(pooled)
        return logits  # 原始 logits（不过 sigmoid/softmax，交给 loss）

    def predict_proba(self, input_ids, attention_mask, token_type_ids=None):
        """返回 softmax 概率 (B, 2)。列 1 = P(coherent)。"""
        logits = self.forward(input_ids, attention_mask, token_type_ids)
        return torch.softmax(logits, dim=-1)

    # ---- 持久化（checkpoint = 权重 + tokenizer + meta，供 eval/infer 确定性复原） ----
    def save(self, out_dir: str, tokenizer=None, extra_meta: dict | None = None):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), out / "pytorch_model.bin")
        meta = {
            "base_model": self.base_model,
            "task": "coherence_binary",
            "n_classes": self.n_classes,
            "label_names": list(self.label_names),
            "pooling": self.pooling,
            "output": "raw logits (B,2)；predict_proba → softmax，列 1 = P(coherent)",
        }
        if extra_meta:
            meta.update(extra_meta)
        (out / "coherence_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # 🔴 2026-06-29 NN连贯性集成 — 落盘 encoder config.json，使 checkpoint 离线自包含
        # （load 时本地 from_config 重建骨架，无需联网拉 base_model）。
        self.encoder.config.save_pretrained(out)
        if tokenizer is not None:
            tokenizer.save_pretrained(out)

    @classmethod
    def load(cls, ckpt_dir: str, map_location="cpu"):
        d = Path(ckpt_dir)
        meta = json.loads((d / "coherence_meta.json").read_text(encoding="utf-8"))
        # 🔴 2026-06-29 NN连贯性集成 — 优先本地 config.json 离线重建骨架（不联网）；
        # 缺则退 from_pretrained（首建 / 旧 checkpoint）。
        enc_cfg = None
        if (d / "config.json").exists():
            enc_cfg = AutoConfig.from_pretrained(str(d))
        model = cls(base_model=meta["base_model"],
                    pooling=meta.get("pooling", "mean"), encoder_config=enc_cfg)
        state = torch.load(d / "pytorch_model.bin", map_location=map_location)
        model.load_state_dict(state)
        model.eval()
        return model, meta
