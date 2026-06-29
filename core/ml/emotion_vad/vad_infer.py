# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:情绪VAD回归 / NN情绪VAD集成
"""vad_infer.py — VAD 推理桥（集成入口·确定性·advisory）。

这是若渝系统接「真 VAD 模型」的**单一入口**（INTEGRATION.md 三个落点都调它）：
  · Appraisal Beat 的 vad_bin（save_state·现为 summarizer 启发式判断）
  · character_vad_ued_scanner._score_vad（现为占位词典查表）
  · emotion_curve_rescan_scanner.segment_valence_curve（现为关键词 valence 映射）

🔴 进程隔离架构（2026-06-29 NN情绪VAD集成）：
  若渝主流水线跑系统 py3.14（无 torch）；模型跑 venv py3.10（torch）。两进程隔离。
  系统侧组件**不直接 import 本模块**，而是经 `core/scripts/nn_vad_bridge.py` 用 subprocess
  调 venv python 跑本模块的 **--batch jsonl in/out** 接口做批量推理。本模块自身永远在 venv 内运行。

设计纪律（北极星⑤·确定性）：
  · model.eval() + no_grad + fp32 + 无 dropout → 同输入恒同输出
  · 默认离线（checkpoint 自包含 config.json）→ 杜绝 transformers 联网 HEAD 探测挂起
  · 有 checkpoint → 用模型；无 → 退**已提交的占位词典**（与现状一致·explicit source 标记）
  · 输出 (valence, arousal, dominance, source)；VA 模型时 dominance=None（诚实·中文无 D 真标注）
  · 繁简：训练语料繁体（manifest simplified=false）→ 生产简体正文经 opencc s2t 简转繁对齐训练分布
    （opencc 缺失则原样输入·不崩·base encoder 兼容简体·精度略降）
  · 全程 advisory：本桥只产「传感器读数」，绝不做判决/hard_gate

用法（CLI 自测）：
  python vad_infer.py "他攥紧了拳头，指节发白"                          # 单条
  python vad_infer.py --ckpt checkpoints/va_base "她笑了笑，转身离开"    # 指定 ckpt
  python vad_infer.py --batch in.jsonl --out out.jsonl                  # 批量（桥调用接口）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 🔴 2026-06-29 NN情绪VAD集成 — 推理默认离线（checkpoint 自包含 config.json）：
# 杜绝 transformers 联网 HEAD 探测导致的挂起/超时（确定性 + 默认安全）。可被外部环境变量覆盖。
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

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
        self._s2t = None   # opencc 简转繁 converter / False(试过不可用) / None(未试)

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

    # ---- 繁简（模型训练繁体·生产简体）----
    def _get_s2t(self):
        if self._s2t is None:
            try:
                import opencc
                self._s2t = opencc.OpenCC("s2t")
            except Exception:  # noqa: BLE001 opencc 缺失 → 原样输入（诚实降级·不崩）
                self._s2t = False
                print("[vad_infer] opencc 不可用·模型输入按简体原样（精度略降·不崩）", file=sys.stderr)
        return self._s2t or None

    def _convert_for_model(self, text: str) -> str:
        conv = self._get_s2t()
        if conv is None:
            return text
        try:
            return conv.convert(text)
        except Exception:  # noqa: BLE001 转换异常 → 原样（不崩）
            return text

    # ---- 预测 ----
    def predict(self, text: str) -> dict:
        """→ {valence, arousal, dominance|None, source}（V/A/D ∈ [0,1]）。"""
        self._ensure()
        if self._mode == "model":
            return self._predict_model_batch([text])[0]
        return self._predict_lexicon(text)

    def predict_batch(self, texts: "list[str]") -> "list[dict]":
        """批量推理（桥接口）·保序·与输入一一对应。"""
        self._ensure()
        texts = [str(t) for t in texts]
        if self._mode == "model":
            return self._predict_model_batch(texts)
        return [self._predict_lexicon(t) for t in texts]

    def _predict_model_batch(self, texts: "list[str]", batch_size: int = 32) -> "list[dict]":
        import torch
        if not texts:
            return []
        names = self._meta["dim_names"]
        self._model.eval()
        results: "list[dict]" = []
        for i in range(0, len(texts), batch_size):
            chunk = [self._convert_for_model(t) for t in texts[i:i + batch_size]]
            enc = self._tok(chunk, truncation=True, max_length=256, padding=True, return_tensors="pt")
            kw = {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]}
            if "token_type_ids" in enc:
                kw["token_type_ids"] = enc["token_type_ids"]
            with torch.no_grad():
                out = self._model(**kw).tolist()
            for row in out:
                res = {n: round(float(row[j]), 6) for j, n in enumerate(names)}
                results.append({"valence": res.get("valence"), "arousal": res.get("arousal"),
                                "dominance": res.get("dominance"), "source": "model"})
        return results

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


def _run_batch(predictor: VADPredictor, in_path: Path, out_path: "Path | None") -> int:
    """读 jsonl（每行 {"text": ...}·保序·跳空行）→ predict_batch → 写 jsonl（每行结果·同序）。"""
    texts: "list[str]" = []
    for line in in_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            texts.append(str(obj.get("text", "")) if isinstance(obj, dict) else str(obj))
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
    ap = argparse.ArgumentParser(description="VAD 推理桥 CLI（单条自测 / --batch 批量接口）")
    ap.add_argument("text", nargs="?", default=None, help="单条文本（与 --batch 互斥）")
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--batch", default=None, help="输入 jsonl 路径（每行 {\"text\": ...}）")
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
    res = p.predict(args.text)
    print(f"mode={p.mode}")
    print("continuous:", json.dumps(res, ensure_ascii=False))
    print("vad_bin   :", json.dumps(p.predict_bin(args.text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
