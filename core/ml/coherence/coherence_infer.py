# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN连贯性评分集成
"""coherence_infer.py — 连贯性二分类推理桥（集成入口·确定性·advisory）。

这是若渝系统接「真连贯性模型」的**单一入口**：判定相邻文本块（场景/段落对）或
单个滑窗文本是否连贯。class 1 = coherent（真实接续），class 0 = incoherent（乱序/拼接）。

🔴 进程隔离架构（2026-06-29 NN连贯性集成）：
  若渝主流水线跑系统 py3.14（无 torch）；模型跑 venv py3.10（torch）。两进程隔离。
  系统侧组件**不直接 import 本模块**，而是经 `core/scripts/nn_coherence_bridge.py` 用 subprocess
  调 venv python 跑本模块的 **--batch jsonl in/out** 接口做批量推理。本模块自身永远在 venv 内运行。

设计纪律（北极星⑤·确定性）：
  · model.eval() + no_grad + fp32 + 无 dropout（eval 下 dropout 为恒等）→ 同输入恒同输出
  · 默认离线（checkpoint 自包含 config.json）→ 杜绝 transformers 联网 HEAD 探测挂起
  · 有 checkpoint → 用模型；无 → mode="unavailable"（显式标记·让调用方回退启发式·绝不假成功）
  · coherence_score = softmax 列 1 概率 = P(coherent)∈[0,1]；is_coherent = score >= 0.5
  · 全程 advisory：本桥只产「传感器读数」，绝不做判决/hard_gate

两种输入格式（JSONL 每行其一）：
  · {"id": "...", "text": "..."}                  ← 单个滑窗文本（窗内连贯度）
  · {"id": "...", "text_a": "...", "text_b": "..."} ← 文本对（B 是否连贯接续 A）
  文本对走 tokenizer 原生 pair 编码（[CLS] a [SEP] b [SEP] + token_type_ids），
  这正是「用 [SEP] 拼接」在 BERT 句对分类里的标准做法，且 CoherenceClassifier 支持 token_type_ids。

用法（CLI 自测）：
  python coherence_infer.py "连续几段文本"                                # 单条
  python coherence_infer.py --model runs/coherence_v1 "一段文本"          # 指定 checkpoint
  python coherence_infer.py --batch in.jsonl --out out.jsonl             # 批量（桥调用接口）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 🔴 2026-06-29 NN连贯性集成 — 推理默认离线（checkpoint 自包含 config.json）：
# 杜绝 transformers 联网 HEAD 探测导致的挂起/超时（确定性 + 默认安全）。可被外部环境变量覆盖。
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

HERE = Path(__file__).resolve().parent
DEFAULT_CKPT = HERE / "runs" / "coherence_v1"   # 训练默认产出目录

DEFAULT_MAX_LEN = 512   # chinese-roberta-wwm-ext 位置上限；meta 若记录 max_len 则以其为准


def _unavailable() -> dict:
    """无 checkpoint / 加载失败时的统一读数（让桥识别 source!="model" → 回退启发式）。"""
    return {"coherence_score": None, "is_coherent": None, "source": "unavailable"}


class CoherencePredictor:
    """单例式连贯性预测器。优先 checkpoint·否则 unavailable。进程内缓存模型。"""

    def __init__(self, ckpt_dir: str | None = None):
        # 解析顺序：显式 arg → RUOYU_COHERENCE_CKPT → 默认 runs/coherence_v1
        self.ckpt_dir = ckpt_dir or os.environ.get("RUOYU_COHERENCE_CKPT") or str(DEFAULT_CKPT)
        self._model = None
        self._tok = None
        self._meta = None
        self._max_len = DEFAULT_MAX_LEN
        self._mode = None  # 'model' | 'unavailable'

    # ---- lazy 模型加载 ----
    def _ensure(self):
        if self._mode is not None:
            return
        if self.ckpt_dir and Path(self.ckpt_dir).exists():
            try:
                import torch  # noqa: F401
                from transformers import AutoTokenizer
                sys.path.insert(0, str(HERE))
                from model import CoherenceClassifier
                self._model, self._meta = CoherenceClassifier.load(self.ckpt_dir)
                self._tok = AutoTokenizer.from_pretrained(self.ckpt_dir)
                self._max_len = int(self._meta.get("max_len", DEFAULT_MAX_LEN))
                self._mode = "model"
                return
            except Exception as e:  # noqa: BLE001 加载失败 → unavailable（显式标记·不静默假成功）
                print(f"[coherence_infer] checkpoint 加载失败·置 unavailable："
                      f"{type(e).__name__}: {e}", file=sys.stderr)
        self._mode = "unavailable"

    @property
    def mode(self):
        self._ensure()
        return self._mode

    # ---- 预测 ----
    def predict(self, text: str) -> dict:
        """单个滑窗文本 → {coherence_score, is_coherent, source}。"""
        self._ensure()
        if self._mode == "model":
            return self._predict_model_batch([str(text)])[0]
        return _unavailable()

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """批量单文本推理（桥接口）·保序·与输入一一对应。"""
        self._ensure()
        texts = [str(t) for t in texts]
        if self._mode == "model":
            return self._predict_model_batch(texts)
        return [_unavailable() for _ in texts]

    def predict_pairs(self, pairs: list[tuple[str, str]]) -> list[dict]:
        """批量文本对推理（B 是否连贯接续 A）·保序。
        tokenizer 原生 pair 编码插入真 [SEP] 特殊 token 并置 token_type_ids（段 A=0/段 B=1）。"""
        self._ensure()
        pairs = [(str(a), str(b)) for a, b in pairs]
        if self._mode == "model":
            return self._predict_model_pairs(pairs)
        return [_unavailable() for _ in pairs]

    # ---- 模型前向（确定性：eval + no_grad + fp32） ----
    def _encode(self, *encode_args):
        enc = self._tok(*encode_args, truncation=True, max_length=self._max_len,
                        padding=True, return_tensors="pt")
        kw = {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]}
        if "token_type_ids" in enc:
            kw["token_type_ids"] = enc["token_type_ids"]
        return kw

    def _rows_to_results(self, probs_rows) -> list[dict]:
        results: list[dict] = []
        for row in probs_rows:
            score = round(float(row[1]), 6)   # 列 1 = P(coherent)
            results.append({"coherence_score": score,
                            "is_coherent": bool(score >= 0.5),
                            "source": "model"})
        return results

    def _predict_model_batch(self, texts: list[str], batch_size: int = 32) -> list[dict]:
        import torch
        if not texts:
            return []
        self._model.eval()
        results: list[dict] = []
        for i in range(0, len(texts), batch_size):
            kw = self._encode(texts[i:i + batch_size])
            with torch.no_grad():
                probs = self._model.predict_proba(**kw).tolist()
            results.extend(self._rows_to_results(probs))
        return results

    def _predict_model_pairs(self, pairs: list[tuple[str, str]], batch_size: int = 32) -> list[dict]:
        import torch
        if not pairs:
            return []
        self._model.eval()
        results: list[dict] = []
        for i in range(0, len(pairs), batch_size):
            chunk = pairs[i:i + batch_size]
            kw = self._encode([a for a, _ in chunk], [b for _, b in chunk])
            with torch.no_grad():
                probs = self._model.predict_proba(**kw).tolist()
            results.extend(self._rows_to_results(probs))
        return results


_DEFAULT = None


def get_predictor(ckpt_dir: str | None = None) -> CoherencePredictor:
    """进程级单例（确定性 + 避免重复加载模型）。"""
    global _DEFAULT
    if _DEFAULT is None or (ckpt_dir and ckpt_dir != _DEFAULT.ckpt_dir):
        _DEFAULT = CoherencePredictor(ckpt_dir)
    return _DEFAULT


def _parse_items(in_path: Path) -> list[tuple[str, str, object]]:
    """读 jsonl → [(id, kind, payload)]·保序·跳空行。kind ∈ {'single','pair'}。"""
    items: list[tuple[str, str, object]] = []
    for line in in_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = {}
        if not isinstance(obj, dict):
            obj = {}
        item_id = obj.get("id", f"item_{len(items)}")
        if "text_a" in obj or "text_b" in obj:
            items.append((item_id, "pair", (str(obj.get("text_a", "")), str(obj.get("text_b", "")))))
        else:
            items.append((item_id, "single", str(obj.get("text", ""))))
    return items


def _run_batch(predictor: CoherencePredictor, in_path: Path, out_path: Path | None) -> int:
    """读 jsonl（单文本或文本对·保序）→ 分流批量推理 → 写 jsonl（同序·每行带 id）。"""
    items = _parse_items(in_path)
    results: list[dict | None] = [None] * len(items)

    single_idx = [i for i, it in enumerate(items) if it[1] == "single"]
    pair_idx = [i for i, it in enumerate(items) if it[1] == "pair"]
    if single_idx:
        for i, pr in zip(single_idx, predictor.predict_batch([items[i][2] for i in single_idx])):
            results[i] = pr
    if pair_idx:
        for i, pr in zip(pair_idx, predictor.predict_pairs([items[i][2] for i in pair_idx])):
            results[i] = pr

    lines = [json.dumps({"id": item_id, **(pr or _unavailable())}, ensure_ascii=False)
             for (item_id, _kind, _payload), pr in zip(items, results)]
    if out_path is not None:
        out_path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
        print(json.dumps({"mode": predictor.mode, "n": len(results), "out": str(out_path)},
                         ensure_ascii=False))
    else:
        for ln in lines:
            print(ln)
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="连贯性二分类推理桥 CLI（单条自测 / --batch 批量接口）")
    ap.add_argument("text", nargs="?", default=None, help="单条文本（与 --batch 互斥）")
    ap.add_argument("--model", default=None, help="checkpoint 目录（默认 RUOYU_COHERENCE_CKPT 或 runs/coherence_v1）")
    ap.add_argument("--batch", default=None, help="输入 jsonl 路径（每行 {\"text\":...} 或 {\"text_a\":...,\"text_b\":...}）")
    ap.add_argument("--out", default=None, help="输出 jsonl 路径（缺则打到 stdout）")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    p = get_predictor(args.model)

    if args.batch:
        return _run_batch(p, Path(args.batch), Path(args.out) if args.out else None)

    if args.text is None:
        ap.error("需要 text 位置参数或 --batch 路径")
    res = p.predict(args.text)
    print(f"mode={p.mode}")
    print("coherence:", json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
