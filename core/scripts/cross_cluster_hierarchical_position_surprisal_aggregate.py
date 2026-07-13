#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 cluster 内段落、场景与收尾边界的层级惊异度。"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ISSUE_CODE = "SCH_HIERARCHY_INVERTED"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr


def _mode() -> str:
    m = (os.environ.get("HIERARCHICAL_POSITION_SURPRISAL_MODE")
         or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _cluster_draft_path(project_root: Path, cluster_id: str) -> Path:
    key = cluster_id.removeprefix("cluster_")
    return project_root / "章节" / f"cluster_{key}_draft" / f"cluster_{key}_draft.txt"


def _read_cluster_body(project_root: Path, cluster_id: str) -> str:
    path = _cluster_draft_path(project_root, cluster_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"cluster 终稿不存在: {path}") from exc
    if not text.strip():
        raise ValueError(f"cluster 终稿为空: {path}")
    return text


def _tokenize(text):
    chars = [c for c in text if "一" <= c <= "鿿"]
    return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]


def _surprisal_seq(tokens, freqs, total):
    out = []
    for t in tokens:
        f = freqs.get(t, 1)
        out.append(-math.log(f / total))
    return out


def _avg_around(surps, idx, w=20):
    a = max(0, idx - w)
    b = min(len(surps), idx + w)
    if b - a < 4:
        return 0.0
    left = surps[a:idx]
    right = surps[idx:b]
    if not left or not right:
        return 0.0
    return (sum(right) / len(right)) - (sum(left) / len(left))


# 未启用模型时使用频次估算；启用后模型推理是必需路径。

_MODEL_WINDOW_CHARS = 30      # 边界前后各取多少字符窗口喂模型(GPT-2 需要一定上下文才稳定)
_MODEL_MIN_WINDOW_CHARS = 6   # 窗口过短(贴段首/段尾)跳过模型·交给频次代理


def _predict_surprisal_batch(texts: list[str]) -> list[dict | None]:
    """按配置调用 FeatureStore 或推理桥；启用后的故障直接失败。"""
    if not texts:
        return []
    if os.environ.get("RUOYU_NN_SURPRISAL") != "1":
        return [None] * len(texts)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled as feature_store_enabled
        if feature_store_enabled():
            results = FeatureStore.get().compute_surprisal_batch(texts)
            if len(results) != len(texts) or any(result is None for result in results):
                raise RuntimeError("FeatureStore surprisal 返回不完整")
            return results
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError("FeatureStore surprisal 推理失败") from exc
    try:
        import nn_surprisal_bridge as bridge
    except ImportError as exc:
        raise RuntimeError("nn_surprisal_bridge 不可用") from exc
    results = bridge.predict_batch(texts)
    if len(results) != len(texts) or any(result is None for result in results):
        raise RuntimeError("surprisal 模型推理返回不完整")
    return results


def _model_boundary_deltas(left_windows: list[str], right_windows: list[str]) -> list[float | None]:
    """对一批边界的『左窗口/右窗口』文本各自算 GPT-2 mean_surprisal·返回逐边界(右-左)差值。
    任一侧模型未命中 → 该边界位 None(不进入均值·跟 antagonist_valence_trajectory
    ._model_window_valence 一样只对命中项取平均)。"""
    n = len(left_windows)
    if n == 0 or n != len(right_windows):
        return []
    preds = _predict_surprisal_batch(left_windows + right_windows)
    if len(preds) != 2 * n:
        return [None] * n
    left_preds, right_preds = preds[:n], preds[n:]
    out: list[float | None] = []
    for lp, rp in zip(left_preds, right_preds):
        if (lp and rp and lp.get("mean_surprisal") is not None
                and rp.get("mean_surprisal") is not None):
            out.append(float(rp["mean_surprisal"]) - float(lp["mean_surprisal"]))
        else:
            out.append(None)
    return out


def _windowed_model_delta(spans: list[str]) -> float | None:
    """对一组已切好的文本片段(paras/scenes)相邻边界取左右窗口·跑真模型算平均 ΔS。
    片段不足 2 个或窗口太短时返回 None。"""
    if len(spans) < 2:
        return None
    left_windows, right_windows = [], []
    for i in range(len(spans) - 1):
        left = spans[i][-_MODEL_WINDOW_CHARS:]
        right = spans[i + 1][:_MODEL_WINDOW_CHARS]
        if len(left) < _MODEL_MIN_WINDOW_CHARS or len(right) < _MODEL_MIN_WINDOW_CHARS:
            continue
        left_windows.append(left)
        right_windows.append(right)
    if not left_windows:
        return None
    deltas = _model_boundary_deltas(left_windows, right_windows)
    valid = [d for d in deltas if d is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _compute_para_deltas_model(text: str) -> float | None:
    """段间边界 ΔS·真模型版(GPT-2 surprisal)。无有效边界/模型不可用 → None。"""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) < 3:
        return None
    return _windowed_model_delta(paras)


def _compute_scene_deltas_model(text: str) -> float | None:
    """场景间边界 ΔS·真模型版(GPT-2 surprisal)。无有效边界/模型不可用 → None。"""
    scenes = re.split(r"\n{3,}|^#+\s*scene[^\n]*\n", text, flags=re.M | re.I)
    scenes = [s for s in scenes if s and _cjk_count(s) > 100]
    if len(scenes) < 2:
        return None
    return _windowed_model_delta(scenes)


def _compute_cluster_end_delta_model(text: str) -> float | None:
    """末段(后 15%) vs 前段边界 ΔS·真模型版(GPT-2 surprisal)。太短/模型不可用 → None。"""
    if len(text) < 100:
        return None
    tail_start = int(len(text) * 0.85)
    head_window = text[max(0, tail_start - _MODEL_WINDOW_CHARS):tail_start]
    tail_window = text[tail_start:tail_start + _MODEL_WINDOW_CHARS]
    if len(head_window) < _MODEL_MIN_WINDOW_CHARS or len(tail_window) < _MODEL_MIN_WINDOW_CHARS:
        return None
    deltas = _model_boundary_deltas([head_window], [tail_window])
    return deltas[0] if deltas else None


def _compute_para_deltas(text, freqs, total):
    """段间边界 ΔS。"""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) < 3:
        return 0.0
    deltas = []
    cumulative_len = 0
    para_tokens_lens = []
    for p in paras:
        tk = _tokenize(p)
        para_tokens_lens.append(len(tk))
    full = _tokenize(text)
    surps = _surprisal_seq(full, freqs, total)
    cumulative = 0
    for i, ln in enumerate(para_tokens_lens[:-1]):
        cumulative += ln
        d = _avg_around(surps, cumulative, w=15)
        deltas.append(d)
    return sum(deltas) / len(deltas) if deltas else 0.0


def _compute_scene_deltas(text, freqs, total):
    """场景间(\n\n\n+ 或 # scene)边界 ΔS。"""
    scenes = re.split(r"\n{3,}|^#+\s*scene[^\n]*\n", text, flags=re.M | re.I)
    scenes = [s for s in scenes if s and _cjk_count(s) > 100]
    if len(scenes) < 2:
        return 0.0
    full = _tokenize(text)
    surps = _surprisal_seq(full, freqs, total)
    deltas = []
    cumulative = 0
    for sc in scenes[:-1]:
        cumulative += len(_tokenize(sc))
        deltas.append(_avg_around(surps, cumulative, w=30))
    return sum(deltas) / len(deltas) if deltas else 0.0


def _compute_cluster_end_delta(text, freqs, total):
    """末段 (后 15%) 与前段对比 ΔS。"""
    full = _tokenize(text)
    if len(full) < 100:
        return 0.0
    tail_start = int(len(full) * 0.85)
    surps = _surprisal_seq(full, freqs, total)
    head = surps[:tail_start]
    tail = surps[tail_start:]
    if not head or not tail:
        return 0.0
    return (sum(tail) / len(tail)) - (sum(head) / len(head))


def _scan_cluster(text):
    """对单个 cluster 终稿计算三类 ΔS。"""
    tokens = _tokenize(text)
    if len(tokens) < 100:
        return None
    freqs = Counter(tokens)
    total = sum(freqs.values())

    para_model = _compute_para_deltas_model(text)
    scene_model = _compute_scene_deltas_model(text)
    cend_model = _compute_cluster_end_delta_model(text)

    delta_para = para_model if para_model is not None else _compute_para_deltas(text, freqs, total)
    delta_scene = scene_model if scene_model is not None else _compute_scene_deltas(text, freqs, total)
    delta_cend = cend_model if cend_model is not None else _compute_cluster_end_delta(text, freqs, total)

    return {
        "delta_para": round(delta_para, 4),
        "delta_scene": round(delta_scene, 4),
        "delta_cluster_end": round(delta_cend, 4),
        "tokens": len(tokens),
        "source": {
            "delta_para": "model" if para_model is not None else "heuristic",
            "delta_scene": "model" if scene_model is not None else "heuristic",
            "delta_cluster_end": "model" if cend_model is not None else "heuristic",
        },
    }


def aggregate(project_root: Path, last_n: int = 10):
    clusters = csr.get_clusters(project_root, last_n=last_n)
    if not clusters:
        return None, []
    per_cluster = []
    for record in clusters:
        cluster_id = str(record["cluster_id"])
        body = _read_cluster_body(project_root, cluster_id)
        r = _scan_cluster(body)
        if r:
            r["cluster_id"] = cluster_id
            per_cluster.append(r)
    if not per_cluster:
        return None, []
    n = len(per_cluster)
    avg_para = sum(r["delta_para"] for r in per_cluster) / n
    avg_scene = sum(r["delta_scene"] for r in per_cluster) / n
    avg_cend = sum(r["delta_cluster_end"] for r in per_cluster) / n
    summary = {"clusters": [r["cluster_id"] for r in per_cluster],
               "avg_delta_para": round(avg_para, 4),
               "avg_delta_scene": round(avg_scene, 4),
               "avg_delta_cluster_end": round(avg_cend, 4)}
    findings = []
    # 期望 ΔS_CLUSTER_END ≥ ΔS_SCENE ≥ ΔS_PARA
    if not (avg_cend >= avg_scene * 0.8) or not (avg_scene >= avg_para * 0.8):
        findings.append({
            "severity": "advisory",
            "code": ISSUE_CODE,
            "suggestion": (f"SCH 阶层颠倒·ΔS_PARA={summary['avg_delta_para']} "
                           f"ΔS_SCENE={summary['avg_delta_scene']} "
                           f"ΔS_CLUSTER_END={summary['avg_delta_cluster_end']}·"
                           f"期望末段惊异度峰最高(收尾应有信息密度跃升)"),
            "metrics": summary,
        })
    return summary, findings


def main():
    ap = argparse.ArgumentParser(
        description="SCH 层级位置惊异度 · cross-cluster shadow")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    mode = _mode()
    project_root = Path(args.project).resolve()
    if mode == "off":
        print("[SKIP] HIERARCHICAL_POSITION_SURPRISAL_MODE=off")
        return 0

    try:
        summary, findings = aggregate(project_root, args.last_n)
    except (OSError, RuntimeError, ValueError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "hierarchical_position_surprisal",
        "scan_ts": ts, "mode": mode,
        "gate_level": "advisory",
        "summary": summary,
        "findings": findings,
    }
    out_path = out_dir / f"hierarchical_position_surprisal_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[hierarchical_position_surprisal] findings={len(findings)} → {out_path}")
    if mode == "shadow":
        return 0
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
