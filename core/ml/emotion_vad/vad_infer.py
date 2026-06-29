# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:情绪VAD回归
"""vad_infer.py — VAD 推理桥（集成入口·确定性·advisory）。

这是若渝系统接「真 VAD 模型」的**单一入口**（INTEGRATION.md 三个落点都调它）：
  · Appraisal Beat 的 vad_bin（save_state·现为 summarizer 启发式判断）
  · character_vad_ued_scanner._score_vad（现为占位词典查表）
  · emotion_curve_rescan_scanner.segment_valence_curve（现为关键词 valence 映射）

设计纪律（北极星⑤·确定性）：
  · model.eval() + no_grad + fp32 + 无 dropout → 同输入恒同输出
  · 有 checkpoint → 用模型；无 → 退**已提交的占位词典**（与现状一致·非新增降级·explicit source 标记）
  · 输出 (valence, arousal, dominance, source)；VA 模型时 dominance=None（诚实·中文无 D 真标注）
  · 全程 advisory：本桥只产「传感器读数」，绝不做判决/hard_gate

用法（CLI 自测）：
  python vad_infer.py "他攥紧了拳头，指节发白"
  python vad_infer.py --ckpt checkpoints/va_base "她笑了笑，转身离开"
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE_DATA = HERE.parent.parent / "data"   # core/data（占位词典·已提交）

BIN_EDGES = (0.2, 0.4, 0.6, 0.8)          # 5 档等宽
BIN_LABELS = ("VL", "L", "M", "H", "VH")


def bin5(x: "float | None") -> "str | None":
    if x is None:
        return None
    for i, e in enumerate(BIN_EDGES):
        if x < e:
            return BIN_LABELS[i]
    return BIN_LABELS[-1]


def vad_bin(v, a, d=None) -> dict:
    """连续 (V,A,D)∈[0,1] → {valence/arousal/dominance: VL|L|M|H|VH}（dominance 缺则 None）。"""
    return {"valence": bin5(v), "arousal": bin5(a), "dominance": bin5(d)}


class VADPredictor:
    """单例式预测器。优先 checkpoint·否则占位词典。线程内缓存模型。"""

    def __init__(self, ckpt_dir: "str | None" = None):
        self.ckpt_dir = ckpt_dir or os.environ.get("RUOYU_VAD_CKPT")
        self._model = None
        self._tok = None
        self._meta = None
        self._lex = None
        self._mode = None  # 'model' | 'lexicon'

    # ---- lazy 模型加载 ----
    def _ensure(self):
        if self._mode is not None:
            return
        if self.ckpt_dir and Path(self.ckpt_dir).exists():
            try:
                import torch  # noqa: F401
                from transformers import AutoTokenizer
                sys.path.insert(0, str(HERE))
                from model import VADRegressor
                self._model, self._meta = VADRegressor.load(self.ckpt_dir)
                self._tok = AutoTokenizer.from_pretrained(self.ckpt_dir)
                self._mode = "model"
                return
            except Exception as e:  # noqa: BLE001 模型加载失败 → 退词典（显式标记·不静默假成功）
                print(f"[vad_infer] checkpoint 加载失败·退占位词典：{type(e).__name__}: {e}", file=sys.stderr)
        self._load_lexicon()
        self._mode = "lexicon"

    def _load_lexicon(self):
        self._lex = {"vad": {}, "va": {}}
        for fname, key in (("nrc_vad_v2_placeholder.json", "vad"), ("cvaw_cvap_placeholder.json", "va")):
            p = CORE_DATA / fname
            if p.exists():
                try:
                    self._lex[key] = json.loads(p.read_text(encoding="utf-8")).get("entries", {}) or {}
                except (OSError, json.JSONDecodeError):
                    pass

    @property
    def mode(self):
        self._ensure()
        return self._mode

    # ---- 预测 ----
    def predict(self, text: str) -> dict:
        """→ {valence, arousal, dominance|None, source}（V/A/D ∈ [0,1]）。"""
        self._ensure()
        if self._mode == "model":
            return self._predict_model(text)
        return self._predict_lexicon(text)

    def _predict_model(self, text: str) -> dict:
        import torch
        enc = self._tok(text, truncation=True, max_length=256, return_tensors="pt")
        kw = {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]}
        if "token_type_ids" in enc:
            kw["token_type_ids"] = enc["token_type_ids"]
        self._model.eval()
        with torch.no_grad():
            out = self._model(**kw).squeeze(0).tolist()
        names = self._meta["dim_names"]
        res = {n: round(float(out[i]), 6) for i, n in enumerate(names)}
        return {"valence": res.get("valence"), "arousal": res.get("arousal"),
                "dominance": res.get("dominance"), "source": "model"}

    def _predict_lexicon(self, text: str) -> dict:
        vad, va = self._lex["vad"], self._lex["va"]
        vs, as_, ds = [], [], []
        for ch in text:
            t = vad.get(ch)
            if isinstance(t, list) and len(t) >= 3:
                vs.append(t[0]); as_.append(t[1]); ds.append(t[2])
        for term, sc in va.items():
            if term in text and isinstance(sc, list) and len(sc) >= 2:
                vs.append(sc[0]); as_.append(sc[1])
        if not vs:
            return {"valence": None, "arousal": None, "dominance": None, "source": "lexicon_nohit"}
        mean = lambda xs: round(sum(xs) / len(xs), 6) if xs else None
        return {"valence": mean(vs), "arousal": mean(as_),
                "dominance": mean(ds) if ds else None, "source": "lexicon"}

    def predict_bin(self, text: str) -> dict:
        p = self.predict(text)
        return {**vad_bin(p["valence"], p["arousal"], p.get("dominance")), "source": p["source"]}


_DEFAULT = None


def get_predictor(ckpt_dir: "str | None" = None) -> VADPredictor:
    """进程级单例（确定性 + 避免重复加载模型）。"""
    global _DEFAULT
    if _DEFAULT is None or (ckpt_dir and ckpt_dir != _DEFAULT.ckpt_dir):
        _DEFAULT = VADPredictor(ckpt_dir)
    return _DEFAULT


def main():
    import argparse
    ap = argparse.ArgumentParser(description="VAD 推理桥 CLI 自测")
    ap.add_argument("text")
    ap.add_argument("--ckpt", default=None)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    p = get_predictor(args.ckpt)
    res = p.predict(args.text)
    print(f"mode={p.mode}")
    print("continuous:", json.dumps(res, ensure_ascii=False))
    print("vad_bin   :", json.dumps(p.predict_bin(args.text), ensure_ascii=False))


if __name__ == "__main__":
    main()
