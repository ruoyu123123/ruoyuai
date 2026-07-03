# -*- coding: utf-8 -*-
# 🔴 2026-07-03 中文NLI蕴含推理桥
"""nli_infer.py — 中文自然语言推理（NLI）推理桥（集成入口·确定性·advisory）。

这是若渝系统接「真中文 NLI 蕴含模型」的**单一入口**，供以下 2 个消费方经
`core/scripts/nn_nli_bridge.py` 调用（subprocess 批量）：
  · cross_book_invariant_scanner.py —— 跨书硬规则 breach 检测·字面否定词窗口启发式的补充证据
  · writer_truth_check.py corroborate_factual —— 声明-vs-正文字面锚词匹配失败(uncertain)时的蕴含补判

🔴 进程隔离架构（与 vad_infer.py / coherence_infer.py / surprisal_infer.py 同款）：
  若渝主流水线跑系统 py3.14（无 torch）；模型跑 venv py3.10（torch）。两进程隔离。
  系统侧组件**不直接 import 本模块**，经 `core/scripts/nn_nli_bridge.py` 用 subprocess
  调 venv python 跑本模块的 **--batch jsonl in/out** 接口做批量推理。本模块自身永远在 venv 内运行。

模型：IDEA-CCNL/Erlangshen-Roberta-110M-NLI（110M 参数 · BertForSequenceClassification ·
  4 个中文 NLI 数据集微调 CMNLI+OCNLI+SNLI-zh 共 101 万样本·模型卡 https://huggingface.co/IDEA-CCNL/Erlangshen-Roberta-110M-NLI）。
  label 顺序从 checkpoint 自带 config.json 的 id2label 读（不硬编码假设），实测为
  {0:CONTRADICTION, 1:NEUTRAL, 2:ENTAILMENT}。

🔴 token_type_ids 经验校准（2026-07-03 实测 5 组人工构造句对）：本 checkpoint 的官方模型卡示例用
  `tokenizer.encode(a,b)` + `model(torch.tensor([ids]))`——不显式传 token_type_ids，BertModel 内部
  对未传入的 token_type_ids 用零缓冲区兜底（整段不分 premise/hypothesis）。若改用「教科书正确」的
  显式 0/1 分段 token_type_ids，"张三把钥匙给了李四"→"李四拿到了钥匙" 这组强 entailment 案例会被
  误判 CONTRADICTION（0.459 vs entailment 0.448·近乎摇摆）；不转发 token_type_ids（等效全零）则正确
  判 entailment（0.642）且其余 4 组测试句对方向不受影响。本桥固定复现模型卡口径。

设计纪律（北极星⑤·确定性）：
  · model.eval() + no_grad + fp32 + 无 dropout → 同输入恒同输出（CPU 推理·不上 GPU·与既有 4 桥一致）
  · 默认离线（checkpoint 自包含 config.json）→ 杜绝 transformers 联网 HEAD 探测挂起
  · 无 checkpoint → 诚实返回 source="unavailable"（NLI 无规则近似可退·不像 VAD 有占位词典）
  · 全程 advisory：本桥只产「传感器读数」，绝不做判决/hard_gate

用法（CLI 自测）：
  python nli_infer.py "张三把钥匙给了李四" "李四拿到了钥匙"          # 单条 premise hypothesis
  python nli_infer.py --ckpt <dir> "premise" "hypothesis"            # 指定 ckpt
  python nli_infer.py --batch in.jsonl --out out.jsonl               # 批量（桥调用接口）
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
_DEFAULT_CKPT = HERE.parent / "models" / "nli" / "erlangshen-roberta-110m-nli"
_MAX_LENGTH = 512  # 模型 max_position_embeddings=512·消费方可能传整段落当 premise


class NLIPredictor:
    """单例式预测器。checkpoint 缺失 → mode='unavailable'（无占位规则可退·诚实报告）。"""

    def __init__(self, ckpt_dir: "str | None" = None):
        self.ckpt_dir = ckpt_dir or os.environ.get("RUOYU_NLI_CKPT") or str(_DEFAULT_CKPT)
        self._model = None
        self._tok = None
        self._id2label: "dict[int, str] | None" = None
        self._mode = None  # 'model' | 'unavailable'

    def _ensure(self):
        if self._mode is not None:
            return
        if self.ckpt_dir and Path(self.ckpt_dir).exists():
            try:
                import torch  # noqa: F401
                from transformers import AutoTokenizer, AutoModelForSequenceClassification
                self._tok = AutoTokenizer.from_pretrained(self.ckpt_dir)
                self._model = AutoModelForSequenceClassification.from_pretrained(self.ckpt_dir)
                self._model.eval()
                self._id2label = {int(k): v for k, v in self._model.config.id2label.items()}
                self._mode = "model"
                return
            except Exception as e:  # noqa: BLE001 模型加载失败 → 诚实标记不可用（不静默假成功）
                print(f"[nli_infer] checkpoint 加载失败·标记不可用：{type(e).__name__}: {e}",
                      file=sys.stderr)
        self._mode = "unavailable"

    @property
    def mode(self):
        self._ensure()
        return self._mode

    def predict_batch(self, pairs: "list[dict]", batch_size: int = 16) -> "list[dict]":
        """pairs[i] = {"premise": str, "hypothesis": str}。保序·一一对应·分批摊薄模型加载开销。"""
        self._ensure()
        if self._mode != "model":
            return [{"label": None, "probs": {}, "source": "unavailable"} for _ in pairs]
        import torch
        results: "list[dict]" = []
        for i in range(0, len(pairs), batch_size):
            chunk = pairs[i:i + batch_size]
            premises = [str(p.get("premise", "")) for p in chunk]
            hyps = [str(p.get("hypothesis", "")) for p in chunk]
            try:
                enc = self._tok(premises, hyps, truncation=True, max_length=_MAX_LENGTH,
                                 padding=True, return_tensors="pt")
                # 🔴 刻意不转发 token_type_ids（见模块 docstring 经验校准段）——本 checkpoint
                # 在「不分段」口径下比显式 0/1 premise/hypothesis 分段更准（5 组实测句对验证）。
                kw = {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]}
                with torch.no_grad():
                    out = self._model(**kw)
                    probs_batch = torch.nn.functional.softmax(out.logits, dim=-1).tolist()
                for row in probs_batch:
                    probs = {self._id2label[j]: round(float(p), 6) for j, p in enumerate(row)}
                    label = max(probs, key=probs.get)
                    results.append({"label": label, "probs": probs, "source": "nli"})
            except Exception as e:  # noqa: BLE001 该批异常 → 本批各条标记 error（不拖垮其它批）
                print(f"[nli_infer] 批推理异常·该批标记 error：{type(e).__name__}: {str(e)[:160]}",
                      file=sys.stderr)
                results.extend({"label": None, "probs": {}, "source": "error"} for _ in chunk)
        return results

    def predict_one(self, premise: str, hypothesis: str) -> dict:
        return self.predict_batch([{"premise": premise, "hypothesis": hypothesis}])[0]


_DEFAULT = None


def get_predictor(ckpt_dir: "str | None" = None) -> NLIPredictor:
    """进程级单例（确定性 + 避免重复加载模型）。"""
    global _DEFAULT
    if _DEFAULT is None or (ckpt_dir and ckpt_dir != _DEFAULT.ckpt_dir):
        _DEFAULT = NLIPredictor(ckpt_dir)
    return _DEFAULT


def _run_batch(predictor: NLIPredictor, in_path: Path, out_path: "Path | None") -> int:
    """读 jsonl（每行 {"premise":..., "hypothesis":...}·保序·跳空行）→ predict_batch → 写 jsonl。"""
    pairs: "list[dict]" = []
    for line in in_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            pairs.append({"premise": "", "hypothesis": ""})
            continue
        try:
            obj = json.loads(line)
            pairs.append(obj if isinstance(obj, dict) else {"premise": "", "hypothesis": ""})
        except json.JSONDecodeError:
            pairs.append({"premise": "", "hypothesis": ""})
    preds = predictor.predict_batch(pairs)
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
    ap = argparse.ArgumentParser(description="中文 NLI 蕴含推理桥 CLI（单条自测 / --batch 批量接口）")
    ap.add_argument("premise", nargs="?", default=None, help="前提句（与 hypothesis 成对·与 --batch 互斥）")
    ap.add_argument("hypothesis", nargs="?", default=None, help="假设句")
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--batch", default=None, help="输入 jsonl 路径（每行 {\"premise\":..., \"hypothesis\":...}）")
    ap.add_argument("--out", default=None, help="输出 jsonl 路径（缺则打到 stdout）")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    p = get_predictor(args.ckpt)

    if args.batch:
        return _run_batch(p, Path(args.batch), Path(args.out) if args.out else None)

    if args.premise is None or args.hypothesis is None:
        ap.error("需要 premise + hypothesis 位置参数或 --batch 路径")
    res = p.predict_one(args.premise, args.hypothesis)
    print(f"mode={p.mode}")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
