# -*- coding: utf-8 -*-
# 🔴 2026-07-04 内容语义嵌入桥
"""content_infer.py — 中文内容语义嵌入推理桥（集成入口·确定性·advisory）。

【为什么】`core/ml/calibration/reports/ruoyu_style_separability_20260704.md` 真机测量证实：
ruoyu_style（作者判别模型）对内容关系在单段粒度下 AUC≈0.51-0.56（随机水平）——它认得「同一个
作者」认不出「内容是否相关」。全仓一批语义阈值（`SEMANTIC_RESONANCE_SIM_THRESHOLD` /
`SEMANTIC_ANCHOR_SIM_THRESHOLD` / `SEMANTIC_THREAD_MATCH_THRESHOLD` / `MILESTONE_SEMANTIC_TOUCH_FLOOR`
/ topic_drift_scanner 的 scope_summary 校验等）守的是内容语义关系，需要专门的内容嵌入后端。

模型：BAAI/bge-small-zh-v1.5（~24M 参数·中文检索/相似度专训·模型卡
  https://huggingface.co/BAAI/bge-small-zh-v1.5）。池化口径依据本仓下载副本自带的
  `1_Pooling/config.json`（`pooling_mode_cls_token=true`，其余 false）+ 模型卡 "Using Transformers"
  示例互相印证：`sentence_embeddings = model_output[0][:, 0]` 后
  `torch.nn.functional.normalize(sentence_embeddings, p=2, dim=1)`。
  BGE 系指令前缀（"为这个句子生成表示以用于检索相关文章："）只用于**非对称检索**的 query 侧
  （FlagEmbedding 用法：`encode_queries` 加前缀 / `encode_corpus` 不加，见
  https://github.com/FlagOpen/FlagEmbedding）；本桥场景是**对称段落-段落相似度**
  （两段都是 corpus 侧文本，非 query vs corpus），故两侧均不加前缀。

🔴 进程隔离架构（与 nli_infer.py / vad_infer.py / coherence_infer.py / surprisal_infer.py 同款）：
  若渝主流水线跑系统 py3.14（无 torch）；模型跑 venv py3.10（torch）。两进程隔离。
  系统侧组件**不直接 import 本模块**，经 `core/ml/daemon/model_daemon.py` 常驻 daemon（daemon-first）
  首次请求时调用一次 `load_model`/`encode_with_model` 并常驻缓存复用；CLI 路径（--batch）供独立
  批量场景（如未来的 nn_content_embed_bridge.py subprocess 调用）走 `ContentEmbedPredictor`。
  本模块自身永远在 venv 内运行。

设计纪律（北极星⑤·确定性）：
  · model.eval() + no_grad + fp32 + 无 dropout → 同输入恒同输出
  · CPU 推理·不上 GPU（与 nli_infer.py 同款：确定性优先·bge-small 24M 参数 CPU 已足够快）
  · 默认离线（checkpoint 自包含 config.json）→ 杜绝 transformers 联网 HEAD 探测挂起
  · 无 checkpoint → 诚实返回 source="unavailable"（CLI --batch 路径·不像 hash 兜底有占位向量）
  · 全程 advisory：本桥只产「传感器读数」，绝不做判决/hard_gate

用法（CLI 自测）：
  python content_infer.py "一段中文文本"                              # 单条自测
  python content_infer.py --ckpt <dir> "文本"                          # 指定 ckpt
  python content_infer.py --batch in.jsonl --out out.jsonl             # 批量（桥调用接口）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 推理默认离线（checkpoint 自包含 config.json）：杜绝 transformers 联网 HEAD 探测导致的挂起。
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

HERE = Path(__file__).resolve().parent
_DEFAULT_CKPT = HERE.parent / "models" / "content_embed" / "bge-small-zh-v1.5"
_MAX_LENGTH = 512   # 模型 max_position_embeddings=512（config.json 实测）
_DEFAULT_BATCH_SIZE = 32


def load_model(model_dir):
    """加载 tokenizer + model（venv 侧）。返回 (tokenizer, model) 二元组，供 encode_with_model 消费。

    🔴 供 `core/ml/daemon/model_daemon.py` 首次请求时调用一次并常驻缓存复用（二段式接口
    load_model/encode_with_model 同 style_infer.py 约定——daemon 侧 `_infer_style_embed` 的
    姊妹实现 `_infer_content_embed` 直接复用这两个函数名）。CLI 路径经 ContentEmbedPredictor
    独立调用一次，两条路径编码逻辑单一真理源、不会分叉出不同数值。
    """
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModel.from_pretrained(str(model_dir))
    model.eval()  # 确定性：关 dropout 等
    return (tok, model)


def encode_with_model(m, texts, batch_size=_DEFAULT_BATCH_SIZE, max_length=_MAX_LENGTH):
    """已加载 (tokenizer, model) → 批量编码 texts → CLS pooling + L2 归一 embedding（6 位小数）。

    池化：`out[0]` 即 `last_hidden_state`，取 `[:, 0]`（CLS token）——对齐模型自带
    `1_Pooling/config.json` 的 `pooling_mode_cls_token=true` 声明。归一：
    `torch.nn.functional.normalize(cls, p=2, dim=1)`（cosine 即点积，与本仓其余后端一致约定）。
    """
    import torch
    tok, model = m
    texts = [str(t) for t in texts]
    out_vecs: "list[list[float]]" = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            enc = tok(chunk, padding=True, truncation=True, max_length=max_length, return_tensors="pt")
            out = model(**enc)
            cls = out[0][:, 0]
            normed = torch.nn.functional.normalize(cls, p=2, dim=1)
            out_vecs.extend([[round(float(x), 6) for x in row] for row in normed.tolist()])
    return out_vecs


class ContentEmbedPredictor:
    """单例式预测器（CLI --batch 用·仿 nli_infer.NLIPredictor）。checkpoint 缺失/加载失败
    → mode='unavailable'（诚实报告·不崩·调用方可回退既有 backend）。"""

    def __init__(self, ckpt_dir: "str | None" = None):
        self.ckpt_dir = ckpt_dir or os.environ.get("RUOYU_CONTENT_EMBED_CKPT") or str(_DEFAULT_CKPT)
        self._m = None
        self._mode = None  # 'model' | 'unavailable'

    def _ensure(self):
        if self._mode is not None:
            return
        if self.ckpt_dir and Path(self.ckpt_dir).exists():
            try:
                self._m = load_model(self.ckpt_dir)
                self._mode = "model"
                return
            except Exception as e:  # noqa: BLE001 加载失败 → 诚实标记不可用（不静默假成功）
                print(f"[content_infer] checkpoint 加载失败·标记不可用：{type(e).__name__}: {e}",
                      file=sys.stderr)
        self._mode = "unavailable"

    @property
    def mode(self):
        self._ensure()
        return self._mode

    def predict_batch(self, texts: "list[str]", batch_size: int = _DEFAULT_BATCH_SIZE) -> "list[dict]":
        """texts → [{"embedding": [...], "source": "content_embed"}, ...]（保序）。
        不可用/推理异常 → [{"embedding": None, "source": "unavailable"|"error"}, ...]。"""
        self._ensure()
        if self._mode != "model":
            return [{"embedding": None, "source": "unavailable"} for _ in texts]
        try:
            vecs = encode_with_model(self._m, texts, batch_size=batch_size)
            return [{"embedding": v, "source": "content_embed"} for v in vecs]
        except Exception as e:  # noqa: BLE001 该批异常 → 该批标记 error（不拖垮已加载的模型状态）
            print(f"[content_infer] 批推理异常·标记 error：{type(e).__name__}: {str(e)[:160]}",
                  file=sys.stderr)
            return [{"embedding": None, "source": "error"} for _ in texts]

    def predict_one(self, text: str) -> dict:
        return self.predict_batch([text])[0]


_DEFAULT: "ContentEmbedPredictor | None" = None


def get_predictor(ckpt_dir: "str | None" = None) -> ContentEmbedPredictor:
    """进程级单例（确定性 + 避免重复加载模型）。"""
    global _DEFAULT
    if _DEFAULT is None or (ckpt_dir and ckpt_dir != _DEFAULT.ckpt_dir):
        _DEFAULT = ContentEmbedPredictor(ckpt_dir)
    return _DEFAULT


def _run_batch(predictor: ContentEmbedPredictor, in_path: Path, out_path: "Path | None") -> int:
    """读 jsonl（每行 {"text":...}·保序·跳空行）→ predict_batch → 写 jsonl。"""
    texts: "list[str]" = []
    for line in in_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            texts.append("")
            continue
        try:
            obj = json.loads(line)
            texts.append(str(obj.get("text", "")) if isinstance(obj, dict) else "")
        except json.JSONDecodeError:
            texts.append("")
    preds = predictor.predict_batch(texts)
    lines = [json.dumps(pr, ensure_ascii=False) for pr in preds]
    if out_path is not None:
        out_path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
        print(json.dumps({"mode": predictor.mode, "n": len(preds), "out": str(out_path)}, ensure_ascii=False))
    else:
        for ln in lines:
            print(ln)
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="中文内容语义嵌入推理桥 CLI（单条自测 / --batch 批量接口）")
    ap.add_argument("text", nargs="?", default=None, help="单条文本（与 --batch 互斥）")
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--batch", default=None, help="输入 jsonl 路径（每行 {\"text\":...}）")
    ap.add_argument("--out", default=None, help="输出 jsonl 路径（缺则打到 stdout）")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    p = get_predictor(args.ckpt)

    if args.batch:
        return _run_batch(p, Path(args.batch), Path(args.out) if args.out else None)

    if args.text is None:
        ap.error("需要 text 位置参数或 --batch 路径")
    res = p.predict_one(args.text)
    print(f"mode={p.mode}")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
