#!/usr/bin/env python3
"""把指定 cluster 的全部物理章节拼成表层蒸馏输入文件。

脚本按 `cluster_index.json` 的 `chapter_range` 读取完整正文，不截断；复刻参考文本的
采样逻辑不用于本入口。

设计纪律：纯确定性·零 LLM。

用法：
  python distill_prep_cluster_text.py <project_root> --cluster-ref cluster_001 --output <out.txt>
退出码：0 成功 / 1 cluster_index/原文 缺失 / 2 cluster_ref 未找到
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _find_cluster(index: dict, cluster_ref: str) -> dict | None:
    clusters = index.get("clusters") if isinstance(index, dict) else index
    if not isinstance(clusters, list):
        return None
    # 按 cluster_id 精确匹配
    for c in clusters:
        if isinstance(c, dict) and str(c.get("cluster_id")) == cluster_ref:
            return c
    # 兜底：cluster_NNN → 第 NNN 个（1-based）
    digits = "".join(ch for ch in cluster_ref if ch.isdigit())
    if digits:
        idx = int(digits) - 1
        if 0 <= idx < len(clusters) and isinstance(clusters[idx], dict):
            return clusters[idx]
    return None


def prep(project_root: Path, cluster_ref: str, output: Path) -> int:
    idx_path = project_root / "cluster_index.json"
    if not idx_path.exists():
        print(f"[prep_cluster] cluster_index.json 不存在: {idx_path}（先跑 cluster_segmenter）")
        return 1
    raw_dir = project_root / "原文"
    if not raw_dir.is_dir():
        print(f"[prep_cluster] 原文/ 不存在: {raw_dir}", file=sys.stderr)
        return 1
    try:
        index = json.loads(idx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[prep_cluster] cluster_index 解析失败: {e}", file=sys.stderr)
        return 1
    cluster = _find_cluster(index, cluster_ref)
    if cluster is None:
        print(f"[prep_cluster] cluster_ref 未找到: {cluster_ref}", file=sys.stderr)
        return 2
    rng = cluster.get("chapter_range") or []
    if len(rng) != 2:
        print(f"[prep_cluster] cluster {cluster_ref} 无 chapter_range", file=sys.stderr)
        return 2
    start, end = int(rng[0]), int(rng[1])
    parts, missing = [], []
    for ch in range(start, end + 1):
        f = raw_dir / f"第{ch}章.txt"
        if f.exists():
            try:
                parts.append(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                parts.append(f.read_text(encoding="gbk", errors="replace"))
        else:
            missing.append(ch)
    if not parts:
        print(f"[prep_cluster] cluster {cluster_ref} 章 {start}-{end} 原文全缺", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(parts), encoding="utf-8")
    note = f"（缺 {len(missing)} 章: {missing[:5]}）" if missing else ""
    print(f"[prep_cluster] {cluster_ref} 章 {start}-{end} 拼 {len(parts)} 章全文 → {output}{note}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--cluster-ref", required=True, help="cluster_id（如 cluster_001）")
    ap.add_argument("--output", required=True, help="拼好的全文落盘路径")
    args = ap.parse_args()
    pr = Path(args.project_root)
    out = Path(args.output)
    if not out.is_absolute():
        out = pr / args.output
    sys.exit(prep(pr, args.cluster_ref, out))


if __name__ == "__main__":
    main()
