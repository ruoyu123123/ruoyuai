"""skill_opt.train_dashboard — 训练收敛曲线 + 健康度哨兵

F4 调研发现: train_log.json 已落每 epoch 的 accept/reject + reward_history,
却没有聚合脚本。本模块把 train 产物聚合成人可读报表。

输出:
1. 收敛表 (run × epoch × accept_rate × mean_reward × best_reward)
2. 健康度哨兵:
   - 连续 2 epoch accept=0 → STALLED
   - reward 单调降 3 epoch → DRIFTING
   - 最终 test_reward < 0.5 → LOW_QUALITY
3. 跨 run 对比 (同书多次训练)

用法:
  python core/scripts/skill_opt/train_dashboard.py --project workspace/styles/<书名>/
  python core/scripts/skill_opt/train_dashboard.py --project workspace/styles/<书名>/ --json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass
class EpochSummary:
    epoch: int
    l_t: int
    steps_attempted: int
    steps_accepted: int
    steps_rejected: int
    accept_rate: float
    mean_sel_reward: float
    best_sel_reward: float


@dataclass
class RunSummary:
    run_id: str
    project: str
    reward_route: str
    epochs: list[EpochSummary] = field(default_factory=list)
    final_selection_reward: float = 0.0
    final_test_reward: float = 0.0
    hparams: dict = field(default_factory=dict)
    health_warnings: list[str] = field(default_factory=list)


@dataclass
class DashboardReport:
    project: str
    runs: list[RunSummary] = field(default_factory=list)
    global_warnings: list[str] = field(default_factory=list)


def _parse_run(run_dir: Path) -> RunSummary | None:
    """解析单个 run 目录的 train_log.json。"""
    log_path = run_dir / "train_log.json"
    if not log_path.exists():
        return None
    try:
        d = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    hparams = d.get("hparams", {})
    run = RunSummary(
        run_id=run_dir.name,
        project=d.get("project_root", ""),
        reward_route=hparams.get("reward_route", "unknown"),
        final_selection_reward=d.get("final_selection_reward", 0.0),
        final_test_reward=d.get("final_test_reward", 0.0),
        hparams=hparams,
    )

    for ep_data in d.get("epochs", []):
        attempted = ep_data.get("steps_attempted", 0)
        accepted = ep_data.get("steps_accepted", 0)
        rejected = ep_data.get("steps_rejected", 0)
        sel_history = ep_data.get("selection_reward_history", [])
        run.epochs.append(EpochSummary(
            epoch=ep_data.get("epoch", 0),
            l_t=ep_data.get("l_t", 0),
            steps_attempted=attempted,
            steps_accepted=accepted,
            steps_rejected=rejected,
            accept_rate=accepted / attempted if attempted > 0 else 0.0,
            mean_sel_reward=(
                sum(sel_history) / len(sel_history) if sel_history else 0.0
            ),
            best_sel_reward=max(sel_history) if sel_history else 0.0,
        ))

    # 健康度哨兵
    _check_health(run)
    return run


def _check_health(run: RunSummary) -> None:
    """健康度哨兵检查。"""
    epochs = run.epochs
    if not epochs:
        return

    # 连续 2 epoch accept=0 → STALLED
    consecutive_zero = 0
    for ep in epochs:
        if ep.steps_accepted == 0:
            consecutive_zero += 1
            if consecutive_zero >= 2:
                run.health_warnings.append(
                    f"STALLED: epoch {ep.epoch - 1}-{ep.epoch} 连续 0 accept"
                )
                break
        else:
            consecutive_zero = 0

    # reward 单调降 3 epoch → DRIFTING
    sel_rewards = [ep.mean_sel_reward for ep in epochs if ep.mean_sel_reward > 0]
    if len(sel_rewards) >= 3:
        decreasing = all(
            sel_rewards[i] > sel_rewards[i + 1]
            for i in range(len(sel_rewards) - 1)
        )
        if decreasing:
            run.health_warnings.append(
                f"DRIFTING: selection reward 单调下降 {sel_rewards}"
            )

    # test_reward < 0.5 → LOW_QUALITY
    if run.final_test_reward < 0.5 and run.final_test_reward > 0:
        run.health_warnings.append(
            f"LOW_QUALITY: test_reward={run.final_test_reward:.3f} < 0.5"
        )

    # 全 epoch 0 accept → TOTAL_FAILURE
    if all(ep.steps_accepted == 0 for ep in epochs):
        run.health_warnings.append("TOTAL_FAILURE: 全 epoch 0 accept (patch_applier 或 optimizer 可能有 bug)")


def build_dashboard(project_root: Path) -> DashboardReport:
    """聚合同书所有 train run。"""
    report = DashboardReport(project=str(project_root))
    train_root = project_root / "_skillopt" / "train"
    if not train_root.exists():
        report.global_warnings.append("无训练记录")
        return report

    for run_dir in sorted(train_root.iterdir()):
        if not run_dir.is_dir():
            continue
        run = _parse_run(run_dir)
        if run:
            report.runs.append(run)

    if not report.runs:
        report.global_warnings.append("无有效 train_log.json")

    return report


def format_text(report: DashboardReport) -> str:
    """格式化为人可读文本表格。"""
    lines = [f"=== SkillOpt Dashboard: {report.project} ===", ""]

    if report.global_warnings:
        for w in report.global_warnings:
            lines.append(f"  [WARN] {w}")
        return "\n".join(lines)

    for run in report.runs:
        lines.append(f"--- Run: {run.run_id} (route={run.reward_route}) ---")
        lines.append(f"  hparams: epochs={run.hparams.get('epochs')} "
                     f"rollout={run.hparams.get('rollout_batch')} "
                     f"minibatch={run.hparams.get('minibatch')}")
        lines.append("")
        lines.append("  | Epoch | L_t | Attempted | Accepted | Rate  | Mean Reward | Best Reward |")
        lines.append("  |-------|-----|-----------|----------|-------|-------------|-------------|")
        for ep in run.epochs:
            lines.append(
                f"  | {ep.epoch:5d} | {ep.l_t:3d} | {ep.steps_attempted:9d} | "
                f"{ep.steps_accepted:8d} | {ep.accept_rate:5.1%} | "
                f"{ep.mean_sel_reward:11.4f} | {ep.best_sel_reward:11.4f} |"
            )
        lines.append("")
        lines.append(f"  Final: selection={run.final_selection_reward:.4f} "
                     f"test={run.final_test_reward:.4f}")
        if run.health_warnings:
            lines.append("  Health:")
            for w in run.health_warnings:
                lines.append(f"    [{w.split(':')[0]}] {w}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="SkillOpt 训练收敛 dashboard")
    ap.add_argument("--project", required=True, help="workspace/styles/<书名>/")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非文本表格")
    args = ap.parse_args()

    report = build_dashboard(Path(args.project))

    if args.json:
        import dataclasses
        print(json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
