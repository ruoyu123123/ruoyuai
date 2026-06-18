"""skill_opt.dataset — train/select/test 集划分

业界源 (arXiv 2605.23904 SearchQA 实验):
- train      60% : 用于 rollout 收 trajectory
- selection  20% : 用于 validation gate 严格优于判定
- test       20% : 留作最终 holdout, SkillOpt 训练全程不可触碰

读取 cluster_index.json,按 cluster_id 分层抽样。
确定性切分:同书 + 同 seed → 同切分(便于复现)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


@dataclass
class Split:
    train: List[str] = field(default_factory=list)
    selection: List[str] = field(default_factory=list)
    test: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "train": self.train,
            "selection": self.selection,
            "test": self.test,
            "counts": {
                "train": len(self.train),
                "selection": len(self.selection),
                "test": len(self.test),
            },
        }


def _deterministic_shuffle(items: List[str], seed: str) -> List[str]:
    """同 seed → 同顺序。SHA256(seed + cluster_id) 排序。"""
    def key(x: str) -> str:
        return hashlib.sha256(f"{seed}::{x}".encode()).hexdigest()
    return sorted(items, key=key)


def split_clusters(
    project_root: Path,
    seed: str = "skillopt-v1",
    ratios: Tuple[float, float, float] = (0.6, 0.2, 0.2),
) -> Split:
    """读 cluster_index.json,把 clusters 切成 train/selection/test。

    Args:
        project_root: workspace/styles/<书名>/
        seed: 确定性切分种子
        ratios: (train, selection, test) 比例,默认 60/20/20

    Returns:
        Split 三段 cluster_id 列表

    Raises:
        FileNotFoundError: cluster_index.json 不存在
        ValueError: ratios 不合 1
    """
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(f"ratios 必须合 1, 收到 {ratios}")

    idx_path = project_root / "cluster_index.json"
    if not idx_path.exists():
        raise FileNotFoundError(
            f"cluster_index.json 缺失: {idx_path}。"
            f"先跑 python core/scripts/cluster_segmenter.py --project {project_root}"
        )

    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    all_ids = [c["cluster_id"] for c in idx.get("clusters", [])]
    if not all_ids:
        raise ValueError(f"cluster_index.json 无 clusters: {idx_path}")

    shuffled = _deterministic_shuffle(all_ids, seed)
    n = len(shuffled)
    n_train = int(n * ratios[0])
    n_sel = int(n * ratios[1])
    # 剩余全归 test (避免向下取整丢样本)
    return Split(
        train=shuffled[:n_train],
        selection=shuffled[n_train : n_train + n_sel],
        test=shuffled[n_train + n_sel :],
    )


def save_split(split: Split, project_root: Path) -> Path:
    """落盘 _skillopt/split.json。同 seed 重跑保持稳定。"""
    out_dir = project_root / "_skillopt"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "split.json"
    out_path.write_text(
        json.dumps(split.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


def load_split(project_root: Path) -> Split:
    """从 _skillopt/split.json 恢复切分。"""
    path = project_root / "_skillopt" / "split.json"
    if not path.exists():
        raise FileNotFoundError(f"split.json 缺失: {path}。先跑 dataset.split_clusters")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Split(
        train=data.get("train", []),
        selection=data.get("selection", []),
        test=data.get("test", []),
    )


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="SkillOpt 数据集切分 (train/select/test)")
    ap.add_argument("--project", required=True, help="workspace/styles/<书名>/")
    ap.add_argument("--seed", default="skillopt-v1")
    ap.add_argument("--show", action="store_true", help="读现有 split 不重切")
    args = ap.parse_args()

    root = Path(args.project)
    if args.show:
        split = load_split(root)
        print(json.dumps(split.to_dict(), ensure_ascii=False, indent=2))
        return 0

    split = split_clusters(root, seed=args.seed)
    out = save_split(split, root)
    print(f"[OK] split → {out}")
    print(f"  train={len(split.train)} selection={len(split.selection)} test={len(split.test)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
