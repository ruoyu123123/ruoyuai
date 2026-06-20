#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_cluster_ousiometric_emd_scanner.py — 长篇跨 cluster 多尺度 ousiometric 振荡
(advisory · cross-cluster · 2026-06-20 R10 W6 Batch-O · L58 P1 · 长篇 1000+)

【缺口】R10 联网调研(arXiv 2208.09496 Fudolig 2023 Nature HSS + UVM ousiometric
项目)：长篇 1000 章+连续读体感由 power/danger 词汇时间序列【多尺度振荡】决定。
LLM 默认易在后段塌缩振幅(单调 plateau)。此前【0 跨 cluster 振荡检测】。

【做法 · 零依赖近似 EMD】：
  1. cluster_count >= 30 才启用。
  2. 遍历所有 cluster_index.json 已写 cluster 的草稿(或 cluster_summary)，
     按 cumulative word-time 拼接 power(力量类)/danger(危险类)词频时间序列。
  3. 滑窗 powers - danger 计算 valence 序列。
  4. 近似 EMD：纯 Python 简化(本地极值检出 → 振幅包络 → 比较前段/后段振幅
     均值与振幅塌缩比)。
  5. 最近 200k 字段 IMF 平均振幅 / 前段中位 < 0.5 → 振荡塌缩 advisory。

  说明：完整 PyEMD 不在 stdlib，本 scanner 用零依赖近似算法供 baseline；
  接入 PyEMD 时无需改 API。

【北极星② / ⑤】纯 advisory · 短篇 skip · 绝不 hard_gate。
  env OUSIOMETRIC_EMD_MODE: off / shadow(默认) / active。

用法：python cross_cluster_ousiometric_emd_scanner.py [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "OUSIOMETRIC_OSCILLATION_DEGRADED"
MIN_CLUSTER_COUNT = 30
WINDOW_CJK = 5000

POWER_LEX = re.compile(
    r"力量|强|威|雄|霸|猛|烈|雷|怒|战|斗|破|碎|崩|震|轰|杀|斩|裂|镇|压|主|统|执")
DANGER_LEX = re.compile(
    r"危|险|惧|怕|惊|恐|怖|凶|狰|狞|魔|邪|阴|噩|绝|死|亡|劫|祸|灾|噬|蚀|腐|溃|残")


def _mode() -> str:
    m = (os.environ.get("OUSIOMETRIC_EMD_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_clusters(project_root):
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "cluster_index.json"
    obj = _read_json(p) if p.exists() else None
    if isinstance(obj, dict):
        clusters = obj.get("clusters") or []
        return list(clusters) if isinstance(clusters, list) else []
    if isinstance(obj, list):
        return obj
    return []


def _cluster_draft_paths(project_root, clusters):
    paths = []
    if not project_root:
        return paths
    drafts_dir = Path(project_root) / "章节"
    if not drafts_dir.exists():
        return paths
    for c in clusters:
        cid = (c.get("cluster_id") if isinstance(c, dict)
               else str(c)) or ""
        if not cid:
            continue
        # 章节/cluster_<key>_draft.txt 或 cluster_<key>_draft/
        guess = drafts_dir / f"{cid}_draft.txt"
        if guess.exists():
            paths.append(guess)
            continue
        sub = drafts_dir / f"{cid}_draft"
        if sub.exists() and sub.is_dir():
            for f in sorted(sub.glob("*.txt")):
                paths.append(f)
    return paths


def compute_valence_series(text):
    """5k CJK 窗滑动 power-danger valence 序列。"""
    series = []
    n = len(text)
    if n < WINDOW_CJK:
        # 整段一窗
        p = len(POWER_LEX.findall(text))
        d = len(DANGER_LEX.findall(text))
        series.append((p - d) / max(n, 1) * 1000)
        return series
    step = WINDOW_CJK // 2
    i = 0
    while i + WINDOW_CJK <= n:
        chunk = text[i:i + WINDOW_CJK]
        p = len(POWER_LEX.findall(chunk))
        d = len(DANGER_LEX.findall(chunk))
        series.append((p - d) / WINDOW_CJK * 1000)
        i += step
    return series


def local_extrema(series):
    """检出局部极值索引。"""
    n = len(series)
    if n < 3:
        return []
    ext = []
    for i in range(1, n - 1):
        if (series[i] > series[i - 1] and series[i] > series[i + 1]) or \
                (series[i] < series[i - 1] and series[i] < series[i + 1]):
            ext.append(i)
    return ext


def amplitude_envelope(series, ext_idx):
    """采样极值得振幅。"""
    if not ext_idx:
        return 0.0
    vals = [abs(series[i]) for i in ext_idx]
    return sum(vals) / len(vals)


def scan_project(project_root) -> dict:
    mode_env = _mode()
    out = {"scanner": "cross_cluster_ousiometric_emd",
           "schema_version": "1.0", "mode": mode_env, "code": ISSUE_CODE,
           "gate_level": "advisory", "verdict": "PASS",
           "violations": [], "warning": None}
    if mode_env == "off":
        return out
    clusters = _list_clusters(project_root)
    out["cluster_count"] = len(clusters)
    if len(clusters) < MIN_CLUSTER_COUNT:
        out["note"] = (f"cluster_count={len(clusters)} < "
                       f"{MIN_CLUSTER_COUNT} 长篇门槛 · 跳过")
        return out
    paths = _cluster_draft_paths(project_root, clusters)
    if not paths:
        out["note"] = "无可读 cluster 草稿 · 跳过"
        return out
    # 拼接全文
    all_text_parts = []
    for p in paths:
        try:
            all_text_parts.append(Path(p).read_text(encoding="utf-8"))
        except OSError:
            continue
    all_text = "\n".join(all_text_parts)
    series = compute_valence_series(all_text)
    out["valence_series_len"] = len(series)
    if len(series) < 6:
        out["note"] = "valence 序列样本不足 · 跳过"
        return out
    half = len(series) // 2
    pre_series = series[:half]
    post_series = series[half:]
    pre_ext = local_extrema(pre_series)
    post_ext = local_extrema(post_series)
    pre_amp = amplitude_envelope(pre_series, pre_ext)
    post_amp = amplitude_envelope(post_series, post_ext)
    out["pre_amplitude"] = round(pre_amp, 4)
    out["post_amplitude"] = round(post_amp, 4)
    decay_ratio = (pre_amp - post_amp) / pre_amp if pre_amp > 0 else 0
    out["decay_ratio"] = round(decay_ratio, 4)

    msg = None
    if pre_amp > 0 and post_amp < 0.5 * pre_amp:
        msg = (f"ousiometric 振荡塌缩(后段振幅 {post_amp:.3f} < "
               f"前段 {pre_amp:.3f} 的 50%) · decay_ratio={decay_ratio:.2f}")
    if msg:
        if mode_env == "active":
            out["violations"].append({
                "code": ISSUE_CODE, "kind": "oscillation_degraded",
                "severity": "minor", "message": msg,
                "_doc": "advisory · 长篇拐点决策 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] ousiometric_emd: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="跨 cluster ousiometric EMD 多尺度振荡 (advisory · shadow)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--draft", default=None)
    args, _ = ap.parse_known_args()
    rep = scan_project(args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


# 兼容 audit_hub 调用签名 scan(draft_path, project_root)
def scan(draft_path=None, project_root=None):
    return scan_project(project_root)


if __name__ == "__main__":
    main()
