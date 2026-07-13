"""SkillOpt patch 提案任务合同。

每个 optimizer step（当前 skill digest × ep/step 标签）有独立任务目录。训练只能
消费 manifest 中已完成且通过严格 JSON 验收的 patch 提案；缺提案时先落 trajectory
素材并登记任务，再由主代理 spawn novel-skill-author (MODE=patch) 亲笔补齐。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atomic_json

from skill_opt import optimizer
from skill_opt.scene_jobs import skill_digest


class OptimizerJobsRequiredError(RuntimeError):
    """训练需要主代理补齐 novel-skill-author patch 提案。"""


def manifest_path(out_root: Path) -> Path:
    return out_root / "optimizer_patch_jobs.json"


def job_dir_for(out_root: Path, step_tag: str, digest: str) -> Path:
    return out_root / "optimizer_patch_jobs" / step_tag / digest


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "jobs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError(f"patch 提案 manifest 结构无效: {path}")
    return data


def _load_accepted_patches(patches_path: Path, max_patches: int) -> list[dict] | None:
    """读 agent 产物并做严格验收；缺失或不合格返回 None（job 保持 pending）。"""
    if not patches_path.is_file():
        return None
    try:
        text = patches_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return optimizer.accept_patches(text, max_patches=max_patches)


def require_patch_job(
    *,
    skill_path: Path,
    out_root: Path,
    step_tag: str,
    trajectories: Sequence[dict],
    protected_sections: Sequence[str] = (),
    rejects: Sequence[dict] = (),
    max_patches: int = 4,
) -> tuple[list[dict], Path]:
    """返回 (验收后的 patches, job 目录)；缺提案时写 manifest 并抛 required 错误。

    素材文件 trajectory_batch.json 与 skill 快照按 digest 幂等落盘：重跑同一
    step_tag + 同一 skill 复用既有素材，digest 冲突直接报错防串稿。
    """
    digest = skill_digest(skill_path)
    job_dir = job_dir_for(out_root, step_tag, digest)
    job_dir.mkdir(parents=True, exist_ok=True)

    snapshot = job_dir / "skill_snapshot.md"
    if snapshot.exists():
        if skill_digest(snapshot) != digest:
            raise ValueError(f"patch 提案 skill 快照 digest 冲突: {snapshot}")
    else:
        snapshot.write_bytes(skill_path.read_bytes())

    batch_path = job_dir / "trajectory_batch.json"
    patches_path = job_dir / "patches.json"
    job_key = f"{digest}:{step_tag}"
    if batch_path.exists():
        existing = json.loads(batch_path.read_text(encoding="utf-8"))
        if existing.get("skill_digest") != digest:
            raise ValueError(f"trajectory_batch digest 冲突: {batch_path}")
    else:
        batch = {
            "version": 1,
            "job_key": job_key,
            "step_tag": step_tag,
            "skill_digest": digest,
            "skill_snapshot_path": str(snapshot.resolve()),
            "max_patches": max_patches,
            "protected_sections": list(protected_sections),
            "reject_buffer": list(rejects),
            "trajectories": list(trajectories),
            "context_text": optimizer.build_task_context(
                skill_text=skill_path.read_text(encoding="utf-8"),
                trajectories=trajectories,
                protected_sections=protected_sections,
                rejects=rejects,
                skill_version=step_tag,
                max_patches=max_patches,
            ),
            "output_path": str(patches_path.resolve()),
        }
        atomic_json.atomic_write_json(batch_path, batch)

    patches = _load_accepted_patches(patches_path, max_patches)
    ready = patches is not None

    path = manifest_path(out_root)
    data = _load_manifest(path)
    now = datetime.now(timezone.utc).isoformat()
    job = next((item for item in data["jobs"] if item.get("job_key") == job_key), None)
    payload = {
        "job_key": job_key,
        "step_tag": step_tag,
        "skill_digest": digest,
        "agent": "novel-skill-author",
        "mode": "patch",
        "skill_snapshot_path": str(snapshot.resolve()),
        "trajectory_batch_path": str(batch_path.resolve()),
        "patches_path": str(patches_path.resolve()),
        "max_patches": max_patches,
        "status": "ready" if ready else "pending",
        "updated_at": now,
    }
    if job is None:
        payload["created_at"] = now
        data["jobs"].append(payload)
    else:
        created_at = job.get("created_at", now)
        job.clear()
        job.update(payload)
        job["created_at"] = created_at
    data["jobs"].sort(key=lambda item: item["job_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json.atomic_write_json(path, data)

    if not ready:
        raise OptimizerJobsRequiredError(
            f"patch 提案任务未完成: {patches_path}；任务已写入 {path}。"
            "主代理须 spawn novel-skill-author (MODE=patch) 读 trajectory_batch.json "
            "写 patches.json，再以同一 run_id 重跑。"
        )
    return patches, job_dir


def write_verified_batch_receipt(*, out_root: Path, plan_id: str, step,
                                 output: Path) -> dict:
    """验证 manifest 中全部 patch 提案并写可供 plan_tracker 复核的批次回执。"""
    if not plan_id:
        raise ValueError("PLAN_ID 缺失")
    data = _load_manifest(manifest_path(out_root))
    jobs = data.get("jobs") or []
    if not jobs:
        raise ValueError("patch 提案 manifest 为空")
    verified = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("patch 提案任务必须是 object")
        patches_path = Path(str(job.get("patches_path") or ""))
        patches = _load_accepted_patches(
            patches_path, int(job.get("max_patches") or 0),
        )
        if patches is None:
            raise OptimizerJobsRequiredError(
                f"patch 提案未完成或验收不通过: {job.get('job_key')}"
            )
        verified.append({
            "job_key": job["job_key"],
            "step_tag": job["step_tag"],
            "skill_digest": job["skill_digest"],
            "patches_path": str(patches_path.resolve()),
            "patch_count": len(patches),
        })
    receipt = {
        "schema_version": "novel-skill-author.patches.v1",
        "agent": "novel-skill-author",
        "mode": "patch-proposal-batch",
        "plan_id": plan_id,
        "step": str(step),
        "completed": True,
        "job_count": len(verified),
        "jobs": verified,
        "manifest": str(manifest_path(out_root).resolve()),
    }
    atomic_json.atomic_write_json(output, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-all", action="store_true")
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--plan-id", default=os.environ.get("PLAN_ID"))
    parser.add_argument("--step", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.verify_all:
        parser.error("必须指定 --verify-all")
    try:
        receipt = write_verified_batch_receipt(
            out_root=Path(args.out_root),
            plan_id=str(args.plan_id or ""),
            step=args.step,
            output=Path(args.output),
        )
        print(f"[OK] verified skill-author patch proposals: {receipt['job_count']}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, OptimizerJobsRequiredError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
