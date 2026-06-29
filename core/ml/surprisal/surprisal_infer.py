# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN信息密度集成
"""surprisal_infer.py — 预训练中文 GPT-2 token-level surprisal 推理（零训练·纯推理）。

这是若渝「信息密度扫描器」的 **venv 侧推理入口**（在 core/ml/.venv 内运行·需 torch + transformers + minicons）。
系统侧经 `core/scripts/nn_surprisal_bridge.py` 用 subprocess 批量调本脚本做推理。

【为什么用 surprisal / 信息密度】
论文 (Meister et al. 2021, Giulianelli et al. 2023) 实证：
  · 人类文本的 surprisal 分布有自然波动（信息密度张弛有度）
  · AI 生成文本 surprisal 方差显著偏低（"太顺滑"·信息密度平坦）
  · 高阶统计量 (skewness, kurtosis) 比单纯均值鉴别力更强

【做法】
  · 用 minicons 的 IncrementalLMScorer 封装 GPT-2 计算 token-level surprisal (base-2 bits)
  · 按段落粒度输入·输出 6 个统计量 (mean, std, max, min, skewness, kurtosis) + token_count
  · 模型加载单例缓存（避免重复加载）

【进程隔离架构】
  本模块自身永远在 venv 内运行 (py3.10 + torch)。系统侧组件不直接 import 本模块。

【默认模型】uer/gpt2-chinese-cluecorpussmall（102M 参数·~400MB fp16·中文 CLUECorpusSmall 预训练）
  首次运行需联网下载（后续离线缓存）。可通过 --model 或 RUOYU_SURPRISAL_MODEL 环境变量切换。

用法：
  python surprisal_infer.py "一段中文文本"                              # 单条自测
  python surprisal_infer.py --batch in.jsonl --out out.jsonl            # 批量（桥调用接口）
  python surprisal_infer.py --model uer/gpt2-chinese-cluecorpussmall   # 指定模型
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# HuggingFace 缓存路径：优先项目内缓存（离线复用）。
_ML_DIR = Path(__file__).resolve().parent.parent  # core/ml
_HF_CACHE = _ML_DIR / "hf_cache"
if _HF_CACHE.exists():
    os.environ.setdefault("HF_HOME", str(_HF_CACHE))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(_HF_CACHE))

DEFAULT_MODEL = "uer/gpt2-chinese-cluecorpussmall"


# ============ 统计量计算（纯函数·torch-free） ============

def _compute_stats(values: list[float]) -> dict:
    """从 token-level surprisal 列表算 6 个统计量。空列表 → 全 None。"""
    n = len(values)
    if n == 0:
        return {"mean_surprisal": None, "std_surprisal": None,
                "max_surprisal": None, "min_surprisal": None,
                "skewness": None, "kurtosis": None, "token_count": 0}

    mean = sum(values) / n
    if n < 2:
        return {"mean_surprisal": round(mean, 4), "std_surprisal": 0.0,
                "max_surprisal": round(mean, 4), "min_surprisal": round(mean, 4),
                "skewness": 0.0, "kurtosis": 0.0, "token_count": n}

    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0

    # Skewness (Fisher, sample)
    if std > 1e-9 and n >= 3:
        m3 = sum((x - mean) ** 3 for x in values) / n
        skewness = m3 / (std ** 3)
    else:
        skewness = 0.0

    # Kurtosis (excess, Fisher)
    if std > 1e-9 and n >= 4:
        m4 = sum((x - mean) ** 4 for x in values) / n
        kurtosis = m4 / (std ** 4) - 3.0
    else:
        kurtosis = 0.0

    return {
        "mean_surprisal": round(mean, 4),
        "std_surprisal": round(std, 4),
        "max_surprisal": round(max(values), 4),
        "min_surprisal": round(min(values), 4),
        "skewness": round(skewness, 4),
        "kurtosis": round(kurtosis, 4),
        "token_count": n,
    }


# ============ 单例模型加载 ============

_SCORER = None
_SCORER_MODEL_NAME = None


def _ensure_scorer(model_name: str):
    """单例加载 IncrementalLMScorer。首次调用加载模型·后续复用。"""
    global _SCORER, _SCORER_MODEL_NAME
    if _SCORER is not None and _SCORER_MODEL_NAME == model_name:
        return _SCORER

    from minicons import scorer as mc_scorer
    _SCORER = mc_scorer.IncrementalLMScorer(model_name, device="cpu")
    _SCORER_MODEL_NAME = model_name

    # 如果有 GPU·迁移
    try:
        import torch
        if torch.cuda.is_available():
            _SCORER.model.to("cuda")
            _SCORER.device = "cuda"
    except Exception:  # noqa: BLE001
        pass
    return _SCORER


def _set_device(scorer, device: str):
    """显式指定设备。"""
    if device == "auto":
        return  # 已在 _ensure_scorer 中自动选择
    try:
        import torch
        target = device if device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
        scorer.model.to(target)
        scorer.device = target
    except Exception:  # noqa: BLE001
        pass


# ============ 推理 ============

def predict_one(text: str, model_name: str = DEFAULT_MODEL, device: str = "auto") -> dict:
    """单条推理 → {mean_surprisal, std_surprisal, max_surprisal, min_surprisal,
                    skewness, kurtosis, token_count, source}。"""
    if not text or not text.strip():
        return {**_compute_stats([]), "source": "empty"}

    try:
        sc = _ensure_scorer(model_name)
        if device != "auto":
            _set_device(sc, device)

        # minicons 0.3.38: token_score(surprisal=True, base_two=True)
        # → list[list[(token, surprisal_bits)]]·surprisal 已为正值（-log2 prob·信息论标准单位 bits）
        token_scores = sc.token_score([text], surprisal=True, base_two=True)
        if not token_scores or not token_scores[0]:
            return {**_compute_stats([]), "source": "no_tokens"}

        # 提取 surprisal 值（surprisal=True 直接返回正 surprisal·无需取负）
        surprisals = [float(score) for _token_str, score in token_scores[0]]

        stats = _compute_stats(surprisals)
        stats["source"] = "model"
        return stats
    except Exception as e:  # noqa: BLE001
        return {**_compute_stats([]), "source": "error",
                "error": f"{type(e).__name__}: {str(e)[:200]}"}


def predict_batch(texts: list[str], model_name: str = DEFAULT_MODEL,
                  device: str = "auto") -> list[dict]:
    """批量推理·保序·与 texts 一一对应。每条独立 try/except（一条异常不影响其他）。"""
    results = []
    for text in texts:
        results.append(predict_one(text, model_name=model_name, device=device))
    return results


# ============ CLI 批量接口（桥调用入口） ============

def _run_batch(in_path: Path, out_path: Path, model_name: str, device: str) -> int:
    """读 JSONL（每行 {"id": "...", "text": "..."}）→ predict → 写 JSONL。"""
    items: list[tuple[str, str]] = []  # (id, text)
    for line in in_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            item_id = obj.get("id", f"item_{len(items)}")
            text = str(obj.get("text", ""))
            items.append((item_id, text))
        except json.JSONDecodeError:
            items.append((f"item_{len(items)}", ""))

    texts = [t for _, t in items]
    preds = predict_batch(texts, model_name=model_name, device=device)

    out_lines = []
    for (item_id, _text), pred in zip(items, preds):
        pred["id"] = item_id
        out_lines.append(json.dumps(pred, ensure_ascii=False))

    out_path.write_text(("\n".join(out_lines) + "\n") if out_lines else "", encoding="utf-8")
    print(json.dumps({"n": len(preds), "model": model_name, "out": str(out_path)},
                      ensure_ascii=False))
    return 0


# ============ main ============

def main():
    ap = argparse.ArgumentParser(
        description="中文 GPT-2 surprisal 推理（单条自测 / --batch 批量接口）")
    ap.add_argument("text", nargs="?", default=None, help="单条文本（与 --batch 互斥）")
    ap.add_argument("--batch", default=None, help="输入 JSONL 路径")
    ap.add_argument("--out", default=None, help="输出 JSONL 路径")
    ap.add_argument("--model", default=None,
                    help=f"HuggingFace 模型名（默认 {DEFAULT_MODEL}）")
    ap.add_argument("--device", default="auto", help="cpu / cuda / auto（默认）")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    model_name = args.model or os.environ.get("RUOYU_SURPRISAL_MODEL", DEFAULT_MODEL)

    if args.batch:
        if not args.out:
            ap.error("--batch 需要搭配 --out")
        return _run_batch(Path(args.batch), Path(args.out),
                          model_name=model_name, device=args.device)

    if args.text is None:
        ap.error("需要 text 位置参数或 --batch 路径")
    result = predict_one(args.text, model_name=model_name, device=args.device)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
