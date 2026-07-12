"""SkillOpt 训练主循环。

训练对 FAST 段做有界编辑，保护 SLOW 段，并用 held-out selection reward 严格选择
候选。每次 rollout 必须消费与候选 skill digest 唯一绑定的 Claude 场景稿。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from skill_opt import (  # noqa: E402
    dataset,
    optimizer,
    patch_applier,
    reject_buffer,
    reward,
    reward_sfs,
    rollout,
    scene_jobs,
    skill_compactor,
    validation_gate,
)


# 论文默认超参 (arXiv 2605.23904)
DEFAULT_HPARAMS = {
    "epochs": 4,
    "rollout_batch": 40,
    "minibatch": 8,
    "l_t_max": 4,
    "l_t_min": 2,
    "max_reflection_per_minibatch": 3,
    "slow_update_samples_per_epoch": 20,
}


def _get_model_profile_name() -> str:
    """E5: 取当前 active gen-model profile 名 (model 版本追溯)。"""
    try:
        from gen_model_loader import get_default_loader
        return get_default_loader().get_active_profile().name
    except Exception:
        return ""


# Reward 路线 (北极星纪律: 解耦的两条路径不互相干涉)
# - "writing"  : 写作路线 = 读 _数据库/.audit/.judge_reports binary 信号 (剩余 cluster 写完才有)
# - "distill"  : 蒸馏路线 = 复刻→SFS 评分 (风格库原文即可,不依赖写作产物)
REWARD_ROUTES = ("writing", "distill")


def cosine_decay_lt(epoch: int, total_epochs: int, l_t_max: int, l_t_min: int) -> int:
    """L_t 按 cosine 从 max 衰减到 min。

    论文 §3.2: textual learning rate cosine decay from 4 to 2。
    epoch 是 1-indexed。
    """
    if total_epochs <= 1:
        return l_t_max
    progress = (epoch - 1) / (total_epochs - 1)
    cos = (1 + math.cos(math.pi * progress)) / 2  # 1 → 0
    return max(l_t_min, round(l_t_min + (l_t_max - l_t_min) * cos))


@dataclass
class EpochLog:
    epoch: int
    l_t: int
    steps_attempted: int = 0
    steps_accepted: int = 0
    steps_rejected: int = 0
    selection_reward_history: list[float] = field(default_factory=list)
    best_selection_reward: float = 0.0
    best_skill_path: str = ""


@dataclass
class TrainResult:
    project_root: str
    initial_skill: str
    final_skill: str
    epochs: list[EpochLog] = field(default_factory=list)
    final_selection_reward: float = 0.0
    final_test_reward: float = 0.0
    hparams: dict = field(default_factory=dict)


def _eval_selection_writing(
    skill_path: Path,
    project: Path,
    selection_ids: Sequence[str],
    out_root: Path,
    run_id: str,
    reward_mode: str,
) -> list[float]:
    """写作路线: rollout 复刻 + 读 audit/judge 信号算 reward。"""
    trajs, _ = rollout.rollout_batch(
        style_skill=skill_path,
        project=project,
        cluster_ids=list(selection_ids),
        out_root=out_root,
        run_id=run_id,
        reward_mode=reward_mode,
    )
    return [t.reward for t in trajs]


def _eval_selection_distill(
    skill_path: Path,
    project: Path,
    selection_ids: Sequence[str],
    out_root: Path,
    run_id: str,
    multi_ref_count: int = 5,
    multi_ref_seed: int = 42,
) -> list[float]:
    """蒸馏路线: 复刻→SFS 评分算 reward(返回 [0,1] 数组)。"""
    scene_jobs.require_scene_jobs(
        skill_path=skill_path,
        cluster_ids=list(selection_ids),
        out_root=out_root,
        run_id=run_id,
    )
    rewards = []
    for cid in selection_ids:
        sr = reward_sfs.reward_for_cluster_sfs(
            style_skill=skill_path,
            project_root=project,
            cluster_id=cid,
            out_root=out_root,
            run_id=run_id,
            multi_ref_count=multi_ref_count,
            multi_ref_seed=multi_ref_seed,
        )
        rewards.append(sr.reward)
    return rewards


def _eval_selection(
    skill_path: Path,
    project: Path,
    selection_ids: Sequence[str],
    out_root: Path,
    run_id: str,
    *,
    reward_route: str = "writing",
    reward_mode: str = "soft",
    multi_ref_count: int = 5,
    multi_ref_seed: int = 42,
) -> list[float]:
    """统一入口: 按 reward_route 分发。"""
    if reward_route == "distill":
        return _eval_selection_distill(
            skill_path, project, selection_ids, out_root, run_id,
            multi_ref_count=multi_ref_count, multi_ref_seed=multi_ref_seed,
        )
    return _eval_selection_writing(
        skill_path, project, selection_ids, out_root, run_id, reward_mode=reward_mode,
    )


def train(
    project_root: Path,
    initial_skill_path: Path,
    *,
    epochs: int = DEFAULT_HPARAMS["epochs"],
    rollout_batch_size: int = DEFAULT_HPARAMS["rollout_batch"],
    minibatch_size: int = DEFAULT_HPARAMS["minibatch"],
    l_t_max: int = DEFAULT_HPARAMS["l_t_max"],
    l_t_min: int = DEFAULT_HPARAMS["l_t_min"],
    reward_route: str = "writing",
    reward_mode: str = "soft",
    multi_ref_count: int = 5,
    multi_ref_seed: int = 42,
    seed: int = 42,
    run_id: str | None = None,
    prepare_scene_jobs: bool = False,
    dry_run: bool = False,
) -> TrainResult:
    """SkillOpt 主训练循环。

    Args:
        project_root: workspace/styles/<书名>/
        initial_skill_path: skill_FINAL.md (训练前需先经 skill_compactor 切 FAST/SLOW)
        epochs..l_t_min: 论文超参 (默认对齐 SearchQA)
        reward_mode: soft / strict
        seed: minibatch 采样确定性
        dry_run: 不真跑 rollout/optimizer,只打印计划

    Returns:
        TrainResult: 每 epoch log + best skill 路径

    需要前置:
    - cluster_index.json 已存在 (dataset.split_clusters 依赖)
    - skill_compactor 已切出 skill_FAST.md / skill_SLOW.md (可选,缺时 SLOW=空)
    """
    rng = random.Random(seed)

    # 1. 准备数据集
    try:
        split = dataset.load_split(project_root)
    except FileNotFoundError:
        split = dataset.split_clusters(project_root, seed=f"skillopt-{seed}")
        dataset.save_split(split, project_root)

    # 2. 准备 SLOW (PROTECTED 段名提取,送 optimizer)
    compact_dir = project_root / "skill_compact"
    protected_titles: list[str] = []
    if (compact_dir / "skill_SLOW.md").exists():
        slow_text = (compact_dir / "skill_SLOW.md").read_text(encoding="utf-8")
        slow_secs = skill_compactor._split_sections(slow_text)
        protected_titles = [s.title for s in slow_secs if s.title]

    # 3. 工作目录
    train_run_id = run_id or datetime.now().strftime("%Y%m%dT%H%M%S")
    train_dir = project_root / "_skillopt" / "train" / train_run_id
    train_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = train_dir / "training_checkpoint.json"
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists() else {}
    )

    # 4. 初始 skill (做工作副本不动原文件)
    current_skill_path = train_dir / "skill_current.md"
    if not current_skill_path.exists() or not checkpoint:
        current_skill_path.write_text(
            initial_skill_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

    schedule_path = train_dir / "training_schedule.json"
    if schedule_path.exists():
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    else:
        schedule = {
            str(epoch): rng.sample(split.train, min(rollout_batch_size, len(split.train)))
            for epoch in range(1, epochs + 1)
        }
        schedule_path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2), encoding="utf-8")
    resume_epoch = int(checkpoint.get("epoch", 1))
    resume_step = int(checkpoint.get("step", 0))

    def save_checkpoint(epoch: int, step: int, phase: str,
                        candidate_path: Path | None = None) -> None:
        payload = {
            "version": 1,
            "run_id": train_run_id,
            "epoch": epoch,
            "step": step,
            "phase": phase,
            "current_skill_path": str(current_skill_path.resolve()),
            "current_skill_digest": scene_jobs.skill_digest(current_skill_path),
            "candidate_skill_path": str(candidate_path.resolve()) if candidate_path else None,
            "candidate_skill_digest": (
                scene_jobs.skill_digest(candidate_path) if candidate_path else None
            ),
            "schedule_path": str(schedule_path.resolve()),
        }
        checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result = TrainResult(
        project_root=str(project_root),
        initial_skill=str(initial_skill_path),
        final_skill=str(current_skill_path),
        hparams={
            "epochs": epochs,
            "rollout_batch": rollout_batch_size,
            "minibatch": minibatch_size,
            "l_t_max": l_t_max,
            "l_t_min": l_t_min,
            "reward_route": reward_route,
            "reward_mode": reward_mode,
            "multi_ref_count": multi_ref_count,
            "multi_ref_seed": multi_ref_seed,
            "seed": seed,
            "run_id": train_run_id,
            # E5: model 版本追溯 (G1: 换模型后能查"这份 skill 对哪个模型优化")
            "gen_model_profile": _get_model_profile_name(),
        },
    )

    print(f"[SkillOpt train] project={project_root.name}")
    print(f"  reward_route={reward_route} (writing=binary 信号 · distill=SFS 评分)")
    print(f"  epochs={epochs} · rollout={rollout_batch_size} · minibatch={minibatch_size}")
    print(f"  L_t={l_t_max}→{l_t_min} (cosine decay)")
    print(f"  split: train={len(split.train)} sel={len(split.selection)} test={len(split.test)}")
    print(f"  PROTECTED 段数: {len(protected_titles)}")
    print(f"  workdir: {train_dir}")

    if prepare_scene_jobs:
        first_batch = schedule["1"][:minibatch_size]
        try:
            scene_jobs.require_scene_jobs(
                skill_path=current_skill_path,
                cluster_ids=first_batch,
                out_root=train_dir,
                run_id="ep1_step0_mb",
            )
        except scene_jobs.SceneJobsRequiredError:
            pass
        save_checkpoint(1, 0, "awaiting_initial_scenes")
        print(f"[prepare] 场景任务已写入 {scene_jobs.manifest_path(train_dir)}")
        return result

    if dry_run:
        print("[dry-run] 计划如上,不真跑。")
        return result

    # 5. 主循环
    best_reward = -1.0
    best_skill_text = current_skill_path.read_text(encoding="utf-8")

    for ep in range(1, epochs + 1):
        if ep < resume_epoch:
            continue
        l_t = cosine_decay_lt(ep, epochs, l_t_max, l_t_min)
        log = EpochLog(epoch=ep, l_t=l_t)
        print(f"\n[epoch {ep}/{epochs}] L_t={l_t}")

        # 切 minibatches (按 rollout_batch 上限挑训练样本)
        train_sample = schedule[str(ep)]
        steps = max(1, len(train_sample) // minibatch_size)

        for step in range(steps):
            if ep == resume_epoch and step < resume_step:
                continue
            log.steps_attempted += 1
            mb = train_sample[step * minibatch_size : (step + 1) * minibatch_size]
            save_checkpoint(ep, step, "minibatch_rollout")

            # rollout minibatch (拿 trajectory)
            evaluation_run_id = f"ep{ep}_step{step}_mb"
            if reward_route == "distill":
                # 蒸馏路线: 每个 cluster 单独跑 SFS reward
                scene_jobs.require_scene_jobs(
                    skill_path=current_skill_path,
                    cluster_ids=list(mb),
                    out_root=train_dir,
                    run_id=evaluation_run_id,
                )
                traj_dicts = []
                for cid in mb:
                    sr = reward_sfs.reward_for_cluster_sfs(
                        style_skill=current_skill_path,
                        project_root=project_root,
                        cluster_id=cid,
                        out_root=train_dir,
                        run_id=evaluation_run_id,
                        multi_ref_count=multi_ref_count,
                        multi_ref_seed=multi_ref_seed,
                    )
                    traj_dicts.append({
                        "cluster_id": cid,
                        "reward": sr.reward,
                        "sfs_score": sr.sfs_score,
                        "replica_path": sr.replica_path,
                        "components": {"sfs_score": sr.sfs_score},
                        "duration_sec": sr.duration_sec,
                    })
            else:
                # 写作路线: rollout 收 binary 信号
                trajs, _ = rollout.rollout_batch(
                    style_skill=current_skill_path,
                    project=project_root,
                    cluster_ids=mb,
                    out_root=train_dir,
                    run_id=evaluation_run_id,
                    reward_mode=reward_mode,
                )
                traj_dicts = [asdict(t) for t in trajs]

            # optimizer 提议 patches
            current_text = current_skill_path.read_text(encoding="utf-8")
            rejects = reject_buffer.load_epoch_rejects(project_root, ep)
            opt_log = train_dir / f"ep{ep}_step{step}_optimizer.json"
            if opt_log.exists():
                patches = json.loads(opt_log.read_text(encoding="utf-8")).get("patches", [])
            else:
                patches, raw_reply = optimizer.propose_patches(
                    skill_text=current_text,
                    trajectories=traj_dicts,
                    protected_sections=protected_titles,
                    rejects=rejects,
                    skill_version=f"ep{ep}_step{step}",
                    max_patches=l_t,
                )
                opt_log.write_text(
                    json.dumps({"patches": patches, "raw_reply": raw_reply[:5000]},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if not patches:
                print(f"  [ep{ep}_step{step}] optimizer 无 patch,跳过")
                continue

            # 应用 patches → 候选
            pr = patch_applier.apply_patches(current_text, patches, max_patches=l_t)
            if not pr.success:
                # 落盘失败详情 (调试)
                print(f"  [ep{ep}_step{step}] 全部 patch 应用失败: "
                      f"{[r[1] for r in pr.rejected]}")
                continue

            candidate_path = train_dir / f"ep{ep}_step{step}_candidate.md"
            if candidate_path.exists():
                if candidate_path.read_text(encoding="utf-8") != pr.new_text:
                    raise RuntimeError(f"恢复 candidate 内容冲突: {candidate_path}")
            else:
                candidate_path.write_text(pr.new_text, encoding="utf-8")
            save_checkpoint(ep, step, "selection", candidate_path)

            # 在 selection_set 上比对
            r_before = _eval_selection(
                current_skill_path, project_root, split.selection,
                train_dir, f"{evaluation_run_id}_sel_before",
                reward_route=reward_route, reward_mode=reward_mode,
                multi_ref_count=multi_ref_count, multi_ref_seed=multi_ref_seed,
            )
            r_after = _eval_selection(
                candidate_path, project_root, split.selection,
                train_dir, f"{evaluation_run_id}_sel_after",
                reward_route=reward_route, reward_mode=reward_mode,
                multi_ref_count=multi_ref_count, multi_ref_seed=multi_ref_seed,
            )
            gate = validation_gate.decide(r_before, r_after)
            log.selection_reward_history.append(gate.score_after)

            if gate.accepted:
                log.steps_accepted += 1
                # 升级
                current_skill_path.write_text(pr.new_text, encoding="utf-8")
                print(
                    f"  [ep{ep}_step{step}] ACCEPT {gate.score_before:.3f}→{gate.score_after:.3f} "
                    f"(applied {len(pr.applied)}/{len(patches)} patches)"
                )
                if gate.score_after > best_reward:
                    best_reward = gate.score_after
                    best_skill_text = pr.new_text
            else:
                log.steps_rejected += 1
                # 落 reject buffer (论文核心:防重蹈)
                for i, p in enumerate(pr.applied):
                    # L3: 记录 model profile 防跨模型污染
                    try:
                        from gen_model_loader import get_default_loader
                        _mp = get_default_loader().get_active_profile().name
                    except Exception:
                        _mp = ""
                    reject_buffer.record_reject(
                        project_root=project_root,
                        epoch=ep,
                        patch_id=f"ep{ep}_step{step}_patch{i}",
                        skill_version_before=f"ep{ep}_step{step}",
                        patch=p,
                        reward_before=gate.score_before,
                        reward_after=gate.score_after,
                        reason=gate.reason,
                        model_profile=_mp,
                    )
                print(f"  [ep{ep}_step{step}] REJECT {gate.reason}")

            next_epoch, next_step = ep, step + 1
            if next_step >= steps:
                next_epoch, next_step = ep + 1, 0
            save_checkpoint(next_epoch, next_step, "ready")

        log.best_selection_reward = max(log.selection_reward_history or [0.0])
        result.epochs.append(log)

        # epoch 末清 reject buffer (论文 epoch-local 原则)
        reject_buffer.clear_epoch(project_root, ep)
        print(f"[epoch {ep}] {log.steps_accepted}/{log.steps_attempted} accept · clear reject_buffer")

    # 6. 落 best_skill + final 评估
    best_path = train_dir / "best_skill.md"
    best_path.write_text(best_skill_text, encoding="utf-8")
    result.final_skill = str(best_path)
    result.final_selection_reward = best_reward

    # 在 test_set 上最终评估 (论文 held-out 测试)
    if split.test:
        test_rewards = _eval_selection(
            best_path, project_root, split.test,
            train_dir, "final_test",
            reward_route=reward_route, reward_mode=reward_mode,
            multi_ref_count=multi_ref_count, multi_ref_seed=multi_ref_seed,
        )
        result.final_test_reward = (
            sum(test_rewards) / len(test_rewards) if test_rewards else 0.0
        )

    # 7. 落训练 log
    log_path = train_dir / "train_log.json"
    log_path.write_text(
        json.dumps(
            {
                **{k: v for k, v in asdict(result).items() if k != "epochs"},
                "epochs": [asdict(e) for e in result.epochs],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    save_checkpoint(epochs + 1, 0, "completed", best_path)
    print(f"\n[done] best_skill: {best_path}")
    print(f"  selection_reward: {result.final_selection_reward:.4f}")
    print(f"  test_reward:      {result.final_test_reward:.4f}")
    print(f"  log: {log_path}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="SkillOpt 训练主循环")
    ap.add_argument("--project", required=True, help="workspace/styles/<书名>/")
    ap.add_argument("--skill", required=True, help="起始 skill_FINAL.md")
    ap.add_argument("--epochs", type=int, default=DEFAULT_HPARAMS["epochs"])
    ap.add_argument("--rollout-batch", type=int, default=DEFAULT_HPARAMS["rollout_batch"])
    ap.add_argument("--minibatch", type=int, default=DEFAULT_HPARAMS["minibatch"])
    ap.add_argument("--l-t-max", type=int, default=DEFAULT_HPARAMS["l_t_max"])
    ap.add_argument("--l-t-min", type=int, default=DEFAULT_HPARAMS["l_t_min"])
    ap.add_argument("--reward-route", choices=["writing", "distill"], default="writing",
                    help="writing=读 audit/judge binary 信号(需写作产物) · "
                         "distill=复刻→SFS 评分(需风格库原文)")
    ap.add_argument("--reward-mode", choices=["soft", "strict"], default="soft",
                    help="仅 writing 路线生效")
    ap.add_argument("--multi-ref-count", type=int, default=5,
                    help="仅 distill 路线生效 (SFS 多基线抽样)")
    ap.add_argument("--multi-ref-seed", type=int, default=42,
                    help="仅 distill 路线生效")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-id", required=True,
                    help="稳定训练标识；补齐 claude_scene_jobs.json 后以同值恢复")
    ap.add_argument("--prepare-scene-jobs", action="store_true",
                    help="只固化训练 schedule/checkpoint 并登记首批 Claude 场景任务")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        train(
            project_root=Path(args.project),
            initial_skill_path=Path(args.skill),
            epochs=args.epochs,
            rollout_batch_size=args.rollout_batch,
            minibatch_size=args.minibatch,
            l_t_max=args.l_t_max,
            l_t_min=args.l_t_min,
            reward_route=args.reward_route,
            reward_mode=args.reward_mode,
            multi_ref_count=args.multi_ref_count,
            multi_ref_seed=args.multi_ref_seed,
            seed=args.seed,
            run_id=args.run_id,
            prepare_scene_jobs=args.prepare_scene_jobs,
            dry_run=args.dry_run,
        )
    except scene_jobs.SceneJobsRequiredError as exc:
        print(f"[REQUIRED] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
