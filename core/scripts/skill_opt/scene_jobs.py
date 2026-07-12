"""SkillOpt Claude 场景稿任务合同。

每个候选 skill、训练 run 和 cluster 都有独立场景目录。rollout 只能消费
manifest 中已完成且通过文件校验的任务；缺稿时先登记任务，再由主代理生成场景稿。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atomic_json


class SceneJobsRequiredError(RuntimeError):
    """训练需要主代理补齐 Claude 场景稿。"""


def skill_digest(skill_path: Path) -> str:
    return hashlib.sha256(skill_path.read_bytes()).hexdigest()


def scenes_dir_for(out_root: Path, run_id: str, digest: str, cluster_id: str) -> Path:
    return out_root / "claude_scene_jobs" / run_id / digest / cluster_id / "claude_scenes"


def manifest_path(out_root: Path) -> Path:
    return out_root / "claude_scene_jobs.json"


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "jobs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError(f"场景任务 manifest 结构无效: {path}")
    return data


def _scene_files(scenes_dir: Path) -> list[Path]:
    return sorted(scenes_dir.glob("scene_*.txt")) if scenes_dir.is_dir() else []


def _scene_job_ready(
    scenes_dir: Path, *, job_key: str, digest: str, cluster_id: str,
) -> bool:
    """场景文件与专用 writer receipt 同时匹配才视为 ready。"""
    files = _scene_files(scenes_dir)
    if not files:
        return False
    for path in files:
        text = path.read_text(encoding="utf-8").strip()
        if len(re.findall(r"[\u4e00-\u9fff]", text)) < 200:
            return False
    report_path = scenes_dir / "agent_report.json"
    if not report_path.exists():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(report, dict)
        and report.get("schema_version") == "novel-replica-writer.receipt.v1"
        and report.get("agent") == "novel-replica-writer"
        and report.get("mode") == "style-replica-draft"
        and report.get("completed") is True
        and isinstance(report.get("plan_id"), str) and bool(report["plan_id"])
        and report.get("step") is not None
        and report.get("job_key") == job_key
        and report.get("candidate_skill_digest") == digest
        and report.get("cluster_id") == cluster_id
        and sorted(report.get("scene_files") or []) == [path.name for path in files]
    )


def require_scene_job(
    *,
    skill_path: Path,
    cluster_id: str,
    out_root: Path,
    run_id: str,
) -> Path:
    """返回唯一 Claude 场景目录；缺稿时写 manifest 并抛 required 错误。"""
    digest = skill_digest(skill_path)
    scenes_dir = scenes_dir_for(out_root, run_id, digest, cluster_id)
    candidate_snapshot = scenes_dir.parent / "candidate_skill.md"
    candidate_snapshot.parent.mkdir(parents=True, exist_ok=True)
    if candidate_snapshot.exists():
        if skill_digest(candidate_snapshot) != digest:
            raise ValueError(f"候选 skill 快照 digest 冲突: {candidate_snapshot}")
    else:
        candidate_snapshot.write_bytes(skill_path.read_bytes())
    path = manifest_path(out_root)
    data = _load_manifest(path)
    job_key = f"{digest}:{run_id}:{cluster_id}"
    now = datetime.now(timezone.utc).isoformat()
    ready = _scene_job_ready(
        scenes_dir, job_key=job_key, digest=digest, cluster_id=cluster_id,
    )
    job = next((item for item in data["jobs"] if item.get("job_key") == job_key), None)
    payload = {
        "job_key": job_key,
        "candidate_skill_path": str(candidate_snapshot.resolve()),
        "candidate_skill_digest": digest,
        "cluster_id": cluster_id,
        "run_id": run_id,
        "scenes_dir": str(scenes_dir.resolve()),
        "agent_report": str((scenes_dir / "agent_report.json").resolve()),
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
        raise SceneJobsRequiredError(
            f"Claude 场景任务未完成: {scenes_dir}；任务已写入 {path}。"
            "主代理须用 novel-replica-writer 生成 scene_*.txt 与 agent_report.json，"
            "再以同一 run_id 重跑。"
        )
    return scenes_dir


def require_scene_jobs(
    *, skill_path: Path, cluster_ids: list[str], out_root: Path, run_id: str,
) -> dict[str, Path]:
    """一次登记同一 rollout 集合的全部任务，缺稿时统一抛错。"""
    ready: dict[str, Path] = {}
    missing: list[str] = []
    for cluster_id in cluster_ids:
        try:
            ready[cluster_id] = require_scene_job(
                skill_path=skill_path,
                cluster_id=cluster_id,
                out_root=out_root,
                run_id=run_id,
            )
        except SceneJobsRequiredError:
            missing.append(cluster_id)
    if missing:
        raise SceneJobsRequiredError(
            f"{len(missing)} 个 Claude 场景任务待完成: {', '.join(missing)}；"
            f"详见 {manifest_path(out_root)}。补齐后重跑同一 run_id。"
        )
    return ready


def write_verified_batch_receipt(*, out_root: Path, plan_id: str, step,
                                 output: Path) -> dict:
    """验证 manifest 中全部 Agent 回执并写可供 plan_tracker 复核的批次回执。"""
    if not plan_id:
        raise ValueError("PLAN_ID 缺失")
    data = _load_manifest(manifest_path(out_root))
    jobs = data.get("jobs") or []
    if not jobs:
        raise ValueError("场景任务 manifest 为空")
    verified = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("场景任务必须是 object")
        scenes_dir = Path(str(job.get("scenes_dir") or ""))
        if not _scene_job_ready(
            scenes_dir,
            job_key=str(job.get("job_key") or ""),
            digest=str(job.get("candidate_skill_digest") or ""),
            cluster_id=str(job.get("cluster_id") or ""),
        ):
            raise SceneJobsRequiredError(f"场景任务未完成或回执不匹配: {job.get('job_key')}")
        report_path = scenes_dir / "agent_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("plan_id") != plan_id:
            raise ValueError(f"Agent 回执 PLAN_ID 不匹配: {job.get('job_key')}")
        verified.append({
            "job_key": job["job_key"],
            "cluster_id": job["cluster_id"],
            "candidate_skill_digest": job["candidate_skill_digest"],
            "agent_report": str(report_path.resolve()),
        })
    receipt = {
        "schema_version": "novel-replica-writer.receipt.v1",
        "agent": "novel-replica-writer",
        "mode": "style-replica-batch",
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
        print(f"[OK] verified replica writer receipts: {receipt['job_count']}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, SceneJobsRequiredError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
