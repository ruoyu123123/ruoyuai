#!/usr/bin/env python3
"""对同栈复刻产物执行 required AV advisory 验证。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AV_JUDGE = ROOT / "core" / "scripts" / "av_judge.py"


def _cluster_meta(project: Path, cluster_id: str) -> dict:
    index_path = project / "cluster_index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"cluster_index.json 不存在: {index_path}")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    target = str(cluster_id).replace("cluster_", "").replace("auto_", "")
    for cluster in data.get("clusters") or []:
        current = str(cluster.get("cluster_id") or "")
        if current == cluster_id or current.replace("cluster_", "").replace("auto_", "") == target:
            return cluster
    raise KeyError(f"cluster_index.json 无 cluster: {cluster_id}")


def _author_anchor(project: Path, cluster: dict) -> Path:
    bounds = cluster.get("chapter_range") or []
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("cluster 缺合法 chapter_range，无法选择作者真迹锚点")
    chapter = int(bounds[0])
    candidates = [
        project / "原文" / f"第{chapter:03d}章.txt",
        project / "原文" / f"第{chapter}章.txt",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"cluster 首章作者原文不存在: {candidates}")


def main() -> int:
    parser = argparse.ArgumentParser(description="required 同栈复刻 AV advisory 验证")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument("--replica", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        cluster = _cluster_meta(args.project, args.cluster_id)
        author = _author_anchor(args.project, cluster)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"[ERROR] AV 验证输入解析失败: {exc}", file=sys.stderr)
        return 2
    if not args.replica.exists():
        print(f"[ERROR] 复刻终稿不存在: {args.replica}", file=sys.stderr)
        return 2

    command = [
        sys.executable, str(AV_JUDGE),
        "--author", str(author),
        "--replica", str(args.replica),
        "--out", str(args.output),
        "--required-run",
    ]
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
