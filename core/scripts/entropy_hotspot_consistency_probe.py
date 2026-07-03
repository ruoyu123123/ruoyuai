#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""entropy_hotspot_consistency_probe.py — ConStory 中段 token-entropy hotspot 优先级探针 · cluster · 2026-06-21 R20 W9 Batch-AA · P1

【缺口 · R20 Q3-Q4 论文 id 18】ConStory 一致性 benchmark + 多篇调研：
长文本一致性问题在「中段 high-entropy 段」高度聚集。本探针不产新 issue
码，只**给现有 consistency 类 scanner(R8 consistency_error_triage /
R19 intra_cluster_style_break)做 priority bump** — 把 hotspot 区段标在
_临时/probe/hotspot.json，audit_hub 调度时优先扫这些区段。

【做法 · 确定性 · 零联网】
  · 草稿切 200 CJK token 块
  · 每块算 Shannon entropy(char 频率分布)
  · 计算块级 μ / σ
  · middle-50% 区间(0.25-0.75 position)内 entropy > μ + 1σ → hotspot
  · 写 _临时/probe/hotspot.json:
      {"hotspots": [{"start_cjk": N, "end_cjk": M, "entropy": E, "z": Z}, ...],
       "mean": μ, "std": σ, "block_size": 200, "policy": "consistency_priority_bump"}
  · 标记 _placeholder=true(distill 真小模型 entropy defer)

【与 audit_hub 集成】
  · audit_hub 调度 R8 consistency_error_triage / R19 intra_cluster_style_break /
    locked_fact_cross_scene 时检 probe 文件存在 → 优先扫 hotspot 范围(可选 hook)
  · **不引入新 issue 码** — 这是『scheduler priority signal』，非 detector
  · shadow 默认开 + 不报：探针文件写入永远成功，scanner 是否消费由其自决定

【2026-07-02 接入真模型】block 指标优先调用已训练部署的 surprisal_gpt2（经 nn_surprisal_bridge /
feature_cache 二选一）算 block 级 mean_surprisal 代替字符 Shannon entropy（`metric` 标注切换为
"model"·`_placeholder` 随之置 False）；z-score / middle-50% hotspot 判定逻辑完全复用不变。
RUOYU_NN_SURPRISAL 未开启/模型未完整命中该 cluster 全部 block 时整体回退字符 Shannon entropy
（`metric="heuristic"`·`_placeholder` 保持 True·不变）。

【与既有 scanner 严格正交】
  · cross_cluster_continuity 查跨章 timeline · 不分块算 entropy
  · revision_homogenization 查全文 entropy 同质化 · 不分块定位 hotspot
  本探针 = 中段 entropy hotspot 定位 唯一覆盖。

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · **不产 issue 码** · 绝不 hard_gate

env ENTROPY_HOTSPOT_PROBE_MODE: off / shadow(默认) / active
用法: python entropy_hotspot_consistency_probe.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
BLOCK_SIZE = 200
MIDDLE_LO = 0.25
MIDDLE_HI = 0.75
HOTSPOT_Z_THRESHOLD = 1.0


def _mode() -> str:
    m = (os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_only(s: str) -> str:
    return "".join(ch for ch in s if "一" <= ch <= "鿿")


def _block_entropy(block: str) -> float:
    """Shannon entropy on char distribution (bits)."""
    if not block:
        return 0.0
    freq = Counter(block)
    total = sum(freq.values())
    h = 0.0
    for c in freq.values():
        p = c / total
        if p > 0:
            h -= p * math.log2(p)
    return h


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / len(values)
    return m, math.sqrt(var)


def _predict_surprisal_batch(texts: list[str]) -> list[float | None]:
    """批量取 GPT-2 mean_surprisal(经 FeatureStore 缓存优先→退 nn_surprisal_bridge 直连)。
    全不可用 → 全 None(调用方整体回退字符 Shannon entropy·不变)。"""
    if not texts:
        return []
    preds = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled as feature_store_enabled
        if feature_store_enabled():
            preds = FeatureStore.get().compute_surprisal_batch(texts)
    except Exception:  # noqa: BLE001 FeatureStore 故障 → 退 bridge，绝不影响探针
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


def _block_metric_values(chunks: list[str]) -> "tuple[list[float], str]":
    """优先真模型(GPT-2 surprisal)block 均值批量代替字符熵·任一 block 未命中 → 整体回退熵(不变)。
    返回 (block 值列表, metric 标注)。"""
    scores = _predict_surprisal_batch(chunks)
    if len(scores) == len(chunks) and all(v is not None for v in scores):
        return scores, "model"
    return [_block_entropy(c) for c in chunks], "heuristic"


def detect_hotspots(text: str, block_size: int = BLOCK_SIZE) -> dict:
    cjk = _cjk_only(text)
    if len(cjk) < block_size * 4:
        return {"hotspots": [], "mean": 0.0, "std": 0.0,
                "block_size": block_size, "blocks_total": 0,
                "skipped": "正文 CJK 过短"}
    positions = []
    chunks = []
    for i in range(0, len(cjk), block_size):
        chunk = cjk[i:i + block_size]
        if len(chunk) < block_size // 2:
            break
        positions.append((i, i + len(chunk)))
        chunks.append(chunk)
    if not chunks:
        return {"hotspots": [], "mean": 0.0, "std": 0.0,
                "block_size": block_size, "blocks_total": 0}
    values, metric = _block_metric_values(chunks)
    blocks = [(positions[i][0], positions[i][1], values[i]) for i in range(len(chunks))]
    entropies = [b[2] for b in blocks]
    mean, std = _mean_std(entropies)
    n = len(blocks)
    hotspots = []
    for i, (start, end, e) in enumerate(blocks):
        pos = (i + 0.5) / n
        if pos < MIDDLE_LO or pos > MIDDLE_HI:
            continue
        z = (e - mean) / std if std > 0 else 0.0
        if z > HOTSPOT_Z_THRESHOLD:
            hotspots.append({
                "start_cjk": start, "end_cjk": end,
                "entropy": round(e, 4), "z": round(z, 3),
                "position": round(pos, 4),
            })
    return {
        "hotspots": hotspots,
        "mean": round(mean, 4),
        "std": round(std, 4),
        "block_size": block_size,
        "blocks_total": n,
        "metric": metric,
    }


def _probe_path(project_root, cluster_id: str | None) -> Path:
    base = Path(project_root) if project_root else Path.cwd()
    out_dir = base / "_临时" / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"hotspot_{cluster_id}.json" if cluster_id else "hotspot.json"
    return out_dir / name


def run(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {"probe": "entropy_hotspot_consistency",
           "schema_version": "1.0",
           "mode": mode,
           "policy": "consistency_priority_bump",
           "_placeholder": True,
           "_doc": "真 distill 小模型 token-entropy defer · 当前用 char Shannon 近似"}
    if mode == "off":
        out["skipped"] = "mode=off"
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["error"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    info = detect_hotspots(text)
    out.update(info)
    if info.get("metric") == "model":
        out["_placeholder"] = False
        out["_doc"] = ("2026-07-02 已接入真模型(GPT-2 surprisal 经 nn_surprisal_bridge)·"
                        "block 均值 surprisal 代替字符 Shannon 近似")
    # 写探针文件(包括 0 hotspot 也写 · 让消费方区分『跑过零结果』vs『没跑』)
    out_path = _probe_path(project_root, cluster_id)
    try:
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        out["probe_path"] = str(out_path)
    except OSError as e:
        out["write_error"] = str(e)[:120]
    return out


def main():
    ap = argparse.ArgumentParser(description="ConStory 中段 entropy hotspot priority probe")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-id", default=None)
    args = ap.parse_args()
    rep = run(args.draft_path, args.project, args.cluster_id)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    # 探针不报错只标记
    sys.exit(0)


if __name__ == "__main__":
    main()
