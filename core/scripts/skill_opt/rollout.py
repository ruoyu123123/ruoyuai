"""SkillOpt 写作 reward rollout。

每次复刻只消费与候选 skill digest、run_id、cluster_id 唯一绑定的 Claude 场景稿。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from . import reward as _reward
from . import scene_jobs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proc_utils import run_utf8  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
DISTILL_REPLICATE = REPO_ROOT / "core" / "scripts" / "distill_replicate.py"


@dataclass
class Trajectory:
    cluster_id: str
    reward: float
    components: dict
    replica_path: str
    skill_path: str
    duration_sec: float
    exit_code: int
    meta: dict = field(default_factory=dict)


def _run_replicate(
    style_skill: Path,
    cluster_id: str,
    project: Path,
    output: Path,
    claude_scenes_dir: Path,
    timeout: int = 1200,
) -> tuple[int, float]:
    """subprocess 调 distill_replicate.py。

    返回 (exit_code, duration_sec)。
    """
    cmd = [
        sys.executable,
        str(DISTILL_REPLICATE),
        "--style-skill", str(style_skill),
        "--mode", "cluster",
        "--cluster-ref", cluster_id,
        "--project", str(project),
        "--claude-scenes-dir", str(claude_scenes_dir),
        "--output", str(output),
    ]
    t0 = time.time()
    try:
        proc = run_utf8(cmd, timeout=timeout)
        return proc.returncode, time.time() - t0
    except subprocess.TimeoutExpired:
        return 124, time.time() - t0


def rollout_one(
    style_skill: Path,
    project: Path,
    cluster_id: str,
    out_root: Path,
    run_id: str,
    reward_mode: str = "soft",
) -> Trajectory:
    """单次 rollout: skill × cluster → trajectory。"""
    replica_dir = out_root / "trajectories" / run_id
    replica_dir.mkdir(parents=True, exist_ok=True)
    replica_path = replica_dir / f"{cluster_id}_replica.txt"
    claude_scenes_dir = scene_jobs.require_scene_job(
        skill_path=style_skill,
        cluster_id=cluster_id,
        out_root=out_root,
        run_id=run_id,
    )

    exit_code, dur = _run_replicate(
        style_skill=style_skill,
        cluster_id=cluster_id,
        project=project,
        output=replica_path,
        claude_scenes_dir=claude_scenes_dir,
    )

    # 复刻完毕,从写作链路产物拣 binary 信号
    # 注:rollout 阶段读的是 styles 库的产物,实际 audit/judge 反馈在 cluster 写作时产生
    # 这里是"复刻 → 同 cluster 历史 judge 产物"的代理评估
    r, components = _reward.reward_for_cluster(project, cluster_id, mode=reward_mode)

    return Trajectory(
        cluster_id=cluster_id,
        reward=r,
        components=components.to_dict(),
        replica_path=str(replica_path),
        skill_path=str(style_skill),
        duration_sec=dur,
        exit_code=exit_code,
        meta={
            "reward_mode": reward_mode,
            "claude_scenes_dir": str(claude_scenes_dir),
            "skill_digest": scene_jobs.skill_digest(style_skill),
        },
    )


def rollout_batch(
    style_skill: Path,
    project: Path,
    cluster_ids: Sequence[str],
    out_root: Path,
    run_id: str | None = None,
    reward_mode: str = "soft",
) -> tuple[list[Trajectory], Path]:
    """跑一个 batch 的 cluster_ids,落盘 trajectory 汇总。

    Returns:
        (trajectories, batch_log_path)
    """
    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S")

    scene_jobs.require_scene_jobs(
        skill_path=style_skill,
        cluster_ids=list(cluster_ids),
        out_root=out_root,
        run_id=run_id,
    )

    trajectories: list[Trajectory] = []
    for cid in cluster_ids:
        traj = rollout_one(
            style_skill=style_skill,
            project=project,
            cluster_id=cid,
            out_root=out_root,
            run_id=run_id,
            reward_mode=reward_mode,
        )
        trajectories.append(traj)

    # 落盘 batch 汇总
    log_dir = out_root / "trajectories" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "batch.json"
    log_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "skill_path": str(style_skill),
                "project": str(project),
                "reward_mode": reward_mode,
                "batch_size": len(cluster_ids),
                "trajectories": [asdict(t) for t in trajectories],
                "mean_reward": (
                    sum(t.reward for t in trajectories) / len(trajectories)
                    if trajectories
                    else 0.0
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return trajectories, log_path


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="SkillOpt rollout (单/批 复刻 + reward)")
    ap.add_argument("--style-skill", required=True)
    ap.add_argument("--project", required=True, help="workspace/styles/<书名>/")
    ap.add_argument("--cluster", action="append", required=True, help="可重复传多次")
    ap.add_argument("--out-root", default=None, help="默认 <project>/_skillopt/")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--reward-mode", choices=["soft", "strict"], default="soft")
    args = ap.parse_args()

    skill = Path(args.style_skill)
    proj = Path(args.project)
    out_root = Path(args.out_root) if args.out_root else proj / "_skillopt"

    trajs, log = rollout_batch(
        style_skill=skill,
        project=proj,
        cluster_ids=args.cluster,
        out_root=out_root,
        run_id=args.run_id,
        reward_mode=args.reward_mode,
    )
    mean = sum(t.reward for t in trajs) / len(trajs) if trajs else 0.0
    print(f"[OK] {len(trajs)} rollouts → mean_reward={mean:.4f} · {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
