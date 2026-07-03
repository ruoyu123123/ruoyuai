#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""author_brand_perplexity_drift.py — 作者 LM perplexity z-band 漂移 advisory · R23 W11 Batch-HH · P1

【缺口 · author brand economics / LitBench 作者品牌】跨小说作者风格识别需
跨书 LM perplexity band：同作者书内 perplexity 分布稳定，单本写跑就漂移。
当前 cluster-write step 5 只跑 reading / voice judge，无作者 LM 锚。

【做法 · 确定性 · 零 LLM/零联网（占位 placeholder）】
  · 真版 author_lm_perplexity_band.json 由蒸馏阶段算（小 LM 逐章 perplexity ECDF + 均值±2σ）·
    本 batch 落占位 schema + char Shannon entropy 替身指标（_placeholder=true）
  · cluster-write step 5 在 reading/voice judge 后并行跑：
    - 算 cluster draft 窗口化 char Shannon entropy（500 CJK 窗·步长 250 CJK）
    - 落作者档 author_lm_perplexity_band.json 的 [p5, p95] 静默
    - 越界（< p5 或 > p95） → AUTHOR_BRAND_PERPLEXITY_DRIFT advisory
  · 单本档跳过（多书蒸馏才有 band）：band.book_count < 2 → skip

【2026-07-02 接入真模型】窗口指标优先调用已训练部署的 surprisal_gpt2（经 nn_surprisal_bridge /
feature_cache 二选一）算窗口 mean_surprisal 代替 char Shannon entropy（`metric_used` 标注切换为
"gpt2_mean_surprisal"）；RUOYU_NN_SURPRISAL 未开启/模型未完整命中该 cluster 全部窗口时整体回退
char Shannon entropy（`metric_used="char_shannon_entropy"`·不变）。两者同为 base-2 bits 量纲
（surprisal_infer.py 用 base_two=True），窗口切法/阈值判定逻辑不变。

【三 advisory】
  · AUTHOR_BRAND_PERPLEXITY_DRIFT_LOW   — 窗口熵 < p5（过度 boilerplate / 模板化）
  · AUTHOR_BRAND_PERPLEXITY_DRIFT_HIGH  — 窗口熵 > p95（过度生僻 / 风格脱锚）
  · AUTHOR_BRAND_PERPLEXITY_BAND_MISSING — 无 band 文件或单本档（info 旁注）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  AUTHOR_BRAND_PERPLEXITY_* 绝不进 audit_hub.HARD_GATE_CODES。

env AUTHOR_BRAND_PERPLEXITY_MODE: off / shadow（默认） / active
用法: python author_brand_perplexity_drift.py <draft> [--project <root>] [--band <path>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

ISSUE_CODE_LOW = "AUTHOR_BRAND_PERPLEXITY_DRIFT_LOW"
ISSUE_CODE_HIGH = "AUTHOR_BRAND_PERPLEXITY_DRIFT_HIGH"
ISSUE_CODE_BAND_MISSING = "AUTHOR_BRAND_PERPLEXITY_BAND_MISSING"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

WINDOW_CJK = 500
WINDOW_STEP_CJK = 250
MIN_BOOK_COUNT = 2  # 单本档不算 band 主题


def _mode() -> str:
    m = (os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_only(text: str) -> str:
    return "".join(ch for ch in text if "一" <= ch <= "鿿")


def _shannon_entropy(s: str) -> float:
    """char Shannon entropy（bits）"""
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values() if c)


def _windowed_entropies(cjk_text: str,
                        window: int = WINDOW_CJK,
                        step: int = WINDOW_STEP_CJK) -> list[float]:
    out = []
    n = len(cjk_text)
    if n < window:
        if n >= window // 4:
            out.append(_shannon_entropy(cjk_text))
        return out
    i = 0
    while i + window <= n:
        out.append(_shannon_entropy(cjk_text[i:i + window]))
        i += step
    return out


def _predict_surprisal_batch(texts: list[str]) -> list[float | None]:
    """批量取 GPT-2 mean_surprisal(经 FeatureStore 缓存优先→退 nn_surprisal_bridge 直连)。
    全不可用 → 全 None(调用方整体回退 char Shannon entropy·不变)。"""
    if not texts:
        return []
    preds = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled as feature_store_enabled
        if feature_store_enabled():
            preds = FeatureStore.get().compute_surprisal_batch(texts)
    except Exception:  # noqa: BLE001 FeatureStore 故障 → 退 bridge，绝不影响 scanner
        preds = None
    if preds is None:
        try:
            import nn_surprisal_bridge as bridge
        except ImportError:
            return [None] * len(texts)
        preds = bridge.predict_batch(texts)
    if len(preds) != len(texts):
        return [None] * len(texts)
    return [(p.get("mean_surprisal") if p else None) for p in preds]


def _window_slices(cjk_text: str, window: int = WINDOW_CJK,
                   step: int = WINDOW_STEP_CJK) -> list[str]:
    """与 _windowed_entropies 同构的窗口切片(供模型路径复用同一组窗口边界)。"""
    out = []
    n = len(cjk_text)
    if n < window:
        if n >= window // 4:
            out.append(cjk_text)
        return out
    i = 0
    while i + window <= n:
        out.append(cjk_text[i:i + window])
        i += step
    return out


def _windowed_surprisals_model(cjk_text: str, window: int = WINDOW_CJK,
                               step: int = WINDOW_STEP_CJK) -> list[float] | None:
    """真模型版：同窗口切片批量算 GPT-2 mean_surprisal 代替字符熵。
    任一窗口未命中 → None(整体回退 _windowed_entropies·避免部分 None 破坏 ecdf 比较语义)。"""
    slices = _window_slices(cjk_text, window, step)
    if not slices:
        return None
    scores = _predict_surprisal_batch(slices)
    if len(scores) != len(slices) or any(v is None for v in scores):
        return None
    return scores


def _windowed_metric(cjk_text: str) -> "tuple[list[float], str]":
    """优先真模型(GPT-2 surprisal)窗口值·不可用/未完整命中 → 回退 char Shannon entropy(不变)。
    返回 (窗口值列表, metric 标注)。"""
    model_vals = _windowed_surprisals_model(cjk_text, WINDOW_CJK, WINDOW_STEP_CJK)
    if model_vals is not None:
        return model_vals, "gpt2_mean_surprisal"
    return _windowed_entropies(cjk_text, WINDOW_CJK, WINDOW_STEP_CJK), "char_shannon_entropy"


def _load_band(project_root, band_path) -> dict | None:
    if band_path:
        p = Path(band_path)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
    if project_root:
        candidates = [
            Path(project_root) / "_数据库" / "author_lm_perplexity_band.json",
            Path(project_root) / "author_lm_perplexity_band.json",
        ]
        for c in candidates:
            if c.exists():
                try:
                    return json.loads(c.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
    return None


def _ensure_placeholder_band(project_root) -> Path | None:
    """如无 band 文件占位写一份骨架（不参与判定·只供蒸馏阶段后续填值）"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    p = db / "author_lm_perplexity_band.json"
    if p.exists():
        return p
    try:
        db.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    placeholder = {
        "_schema_version": "1.0",
        "_placeholder": True,
        "_doc": ("R23 W11 Batch-HH·占位骨架·蒸馏阶段产逐章 perplexity ECDF 后填值·"
                  "当前 char Shannon entropy 替身·真版用小 LM perplexity"),
        "metric": "char_shannon_entropy",
        "window_cjk": WINDOW_CJK,
        "window_step_cjk": WINDOW_STEP_CJK,
        "book_count": 0,
        "ecdf": {"p5": None, "p25": None, "p50": None, "p75": None, "p95": None},
        "mean": None,
        "std": None,
        "books": []
    }
    try:
        p.write_text(json.dumps(placeholder, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p
    except OSError:
        return None


def scan(draft_path, project_root=None, band_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "author_brand_perplexity_drift", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk_text = _cjk_only(text)
    if len(cjk_text) < 1500:
        out["note"] = "草稿太短·跳过"
        return out

    band = _load_band(project_root, band_path)
    if band is None:
        # 占位写入
        wrote = _ensure_placeholder_band(project_root) if mode == "active" else None
        out["note"] = "无 author_lm_perplexity_band.json·已占位" if wrote else "无 band 文件"
        out["band_missing"] = True
        if mode == "active":
            out["violations"].append({
                "kind": "author_brand_perplexity",
                "severity": "info",
                "code": ISSUE_CODE_BAND_MISSING,
                "message": "无 band 文件 / 占位 placeholder",
                "_doc": "R23 W11 Batch-HH·advisory·绝不 hard_gate",
            })
            out["warning"] = "无 author_lm_perplexity_band.json·跳过判定"
        return out

    # 单本档跳过
    book_count = band.get("book_count")
    is_placeholder = bool(band.get("_placeholder"))
    if not isinstance(book_count, int) or book_count < MIN_BOOK_COUNT or is_placeholder:
        out["note"] = "band 单本档 / 占位·跳过判定"
        out["band_book_count"] = book_count
        out["band_placeholder"] = is_placeholder
        if mode == "active":
            out["violations"].append({
                "kind": "author_brand_perplexity",
                "severity": "info",
                "code": ISSUE_CODE_BAND_MISSING,
                "message": "band 单本档 / 占位·不参与判定",
                "_doc": "R23 W11 Batch-HH·advisory·绝不 hard_gate",
            })
            out["warning"] = "band 单本档 / 占位·跳过"
        return out

    ecdf = band.get("ecdf") or {}
    p5 = ecdf.get("p5")
    p95 = ecdf.get("p95")
    if not isinstance(p5, (int, float)) or not isinstance(p95, (int, float)):
        out["note"] = "band 缺 p5/p95·跳过"
        return out

    entropies, metric_used = _windowed_metric(cjk_text)
    if not entropies:
        out["note"] = "窗口不足·跳过"
        return out

    below = sum(1 for e in entropies if e < p5)
    above = sum(1 for e in entropies if e > p95)
    total = len(entropies)
    below_ratio = below / total
    above_ratio = above / total

    out.update({
        "cjk": len(cjk_text),
        "windows": total,
        "window_cjk": WINDOW_CJK,
        "metric_used": metric_used,
        "p5": p5,
        "p95": p95,
        "below_p5_ratio": round(below_ratio, 3),
        "above_p95_ratio": round(above_ratio, 3),
        "windows_entropy_min": round(min(entropies), 3),
        "windows_entropy_max": round(max(entropies), 3),
        "windows_entropy_mean": round(sum(entropies) / total, 3),
    })

    flags = []
    if below_ratio > 0.30:
        flags.append({
            "code": ISSUE_CODE_LOW,
            "msg": (f"{below_ratio:.0%} 窗口熵 < p5({p5:.3f})"
                    f"·过度 boilerplate / 模板化"),
            "severity": "minor",
        })
    if above_ratio > 0.30:
        flags.append({
            "code": ISSUE_CODE_HIGH,
            "msg": (f"{above_ratio:.0%} 窗口熵 > p95({p95:.3f})"
                    f"·过度生僻 / 风格脱锚"),
            "severity": "minor",
        })

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "author_brand_perplexity",
                    "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "author brand economics·R23 W11 Batch-HH·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] author_brand_perplexity: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="作者 LM perplexity z-band advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--band", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.band)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
