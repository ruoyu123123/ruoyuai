# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:情绪VAD回归
"""model.py — 中文 VAD 回归模型定义（共享给 train.py / eval.py / vad_infer.py）。

架构（SOTA 对标·见 README §模型）：
  中文 encoder（默认 hfl/chinese-roberta-wwm-ext）
    → mean-pool（attention-mask 加权）或 [CLS]
    → dropout
    → Linear(hidden, n_dims)
    → sigmoid → 输出归一到 [0,1]（与系统 _score_vad / vad_bin 量纲对齐）

维度：
  --dims va  → 2 维 (valence, arousal)   ← 默认，中文有充足真标注
  --dims vad → 3 维 (valence, arousal, dominance) ← D 维为弱标注/迁移（见 README §数据局限）

Loss：CCC（Concordance Correlation Coefficient）+ MSE 混合。
  CCC 同时奖励高相关 + 低均值/方差偏差，维度情感回归普遍优于纯 MSE
  （arXiv:2203.07378 / ESANN-2023）。每维独立 CCC，再对维度求平均。

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


DIM_SETS = {
    "va": ("valence", "arousal"),
    "vad": ("valence", "arousal", "dominance"),
}


# ----------------------------------------------------------------------------- loss
def ccc_loss(pred: "torch.Tensor", target: "torch.Tensor", eps: float = 1e-8) -> "torch.Tensor":
    """1 - CCC，按维度求 CCC 再平均。pred/target: (B, n_dims) ∈ [0,1]。

    CCC = 2·cov(x,y) / (var_x + var_y + (mean_x - mean_y)^2)
    """
    pred_mean = pred.mean(dim=0)
    target_mean = target.mean(dim=0)
    pred_var = pred.var(dim=0, unbiased=False)
    target_var = target.var(dim=0, unbiased=False)
    cov = ((pred - pred_mean) * (target - target_mean)).mean(dim=0)
    ccc = (2 * cov) / (pred_var + target_var + (pred_mean - target_mean) ** 2 + eps)
    return (1.0 - ccc).mean()


def combined_loss(pred, target, alpha: float = 0.5):
    """alpha·CCC_loss + (1-alpha)·MSE。alpha 默认 0.5。"""
    mse = nn.functional.mse_loss(pred, target)
    ccc = ccc_loss(pred, target)
    return alpha * ccc + (1.0 - alpha) * mse, {"mse": float(mse.detach()), "ccc_loss": float(ccc.detach())}


# ----------------------------------------------------------------------------- model
class VADRegressor(nn.Module):
    """中文 encoder + n_dims sigmoid 回归头。"""

    def __init__(self, base_model: str = "hfl/chinese-roberta-wwm-ext",
                 dims: str = "va", dropout: float = 0.1, pooling: str = "mean"):
        super().__init__()
        if dims not in DIM_SETS:
            raise ValueError(f"dims 必须 ∈ {list(DIM_SETS)}，收到 {dims!r}")
        self.dims = dims
        self.dim_names = DIM_SETS[dims]
        self.n_dims = len(self.dim_names)
        self.base_model = base_model
        self.pooling = pooling
        self.encoder = AutoModel.from_pretrained(base_model)
        hidden = self.encoder.config.hidden_size
        self.dropout = nn.Dropout(dropout)
        self.head = nn.Linear(hidden, self.n_dims)

    def _pool(self, last_hidden, attention_mask):
        if self.pooling == "cls":
            return last_hidden[:, 0]
        # mean pool（mask 加权·稳定且对回归友好）
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
        return torch.sigmoid(logits)  # → [0,1]

    # ---- 持久化（checkpoint = 权重 + tokenizer + meta，供 eval/infer 确定性复原） ----
    def save(self, out_dir: str, tokenizer=None, extra_meta: dict | None = None):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), out / "pytorch_model.bin")
        meta = {
            "base_model": self.base_model,
            "dims": self.dims,
            "dim_names": list(self.dim_names),
            "pooling": self.pooling,
            "output_scale": "[0,1] sigmoid（与 _score_vad / vad_bin 对齐）",
        }
        if extra_meta:
            meta.update(extra_meta)
        (out / "vad_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if tokenizer is not None:
            tokenizer.save_pretrained(out)

    @classmethod
    def load(cls, ckpt_dir: str, map_location="cpu"):
        d = Path(ckpt_dir)
        meta = json.loads((d / "vad_meta.json").read_text(encoding="utf-8"))
        model = cls(base_model=meta["base_model"], dims=meta["dims"], pooling=meta.get("pooling", "mean"))
        state = torch.load(d / "pytorch_model.bin", map_location=map_location)
        model.load_state_dict(state)
        model.eval()
        return model, meta
