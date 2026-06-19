"""skill_opt.validation_gate — held-out 严格优于判定

业界源 (arXiv 2605.23904 Algorithm 1 Lines 22-26):
- 在 selection_set 上算 V(s_candidate) 和 V(s_current)
- 严格 `>` 才接受,平局拒绝(防 reward hacking 漂移)
- V(s) = mean(reward(rollout)) over selection_set

北极星纪律 ⑤: gate 判定基于 binary reward,不引入新硬约束。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass
class GateResult:
    accepted: bool
    score_before: float
    score_after: float
    delta: float
    reason: str


def aggregate(rewards: Sequence[float]) -> float:
    """V(s) = 算术平均。rewards 是 selection_set 每个样本的 binary 0/1 或连续 [0,1]。"""
    if not rewards:
        return 0.0
    return sum(rewards) / len(rewards)


def decide(
    rewards_before: Sequence[float],
    rewards_after: Sequence[float],
    epsilon: float = 0.0,
    floor: float | None = None,
) -> GateResult:
    """严格优于判定 + 可选绝对地板。

    Args:
        rewards_before: 旧 skill 在 selection_set 上的 reward 数组
        rewards_after : 候选 skill 在同 selection_set 上的 reward 数组
        epsilon: 改进阈值 (论文默认严格 `>`, ε=0)
        floor: L6 SFS 绝对地板 — 候选 reward 低于此值无条件拒绝,
            防长期漂移 (G5: gate 只测相对优于无绝对锚)。
            典型值: v0_sfs × 0.97。None = 不检查 (向后兼容)。

    Returns:
        GateResult(accepted, score_before, score_after, delta, reason)

    判定逻辑 (论文 Lines 22-26 + L6 地板扩展):
        1. floor 检查: score_after < floor → 拒绝
        2. 严格优于: (score_after - score_before) > epsilon
        3. 平局 (delta=0) → 拒绝
    """
    if len(rewards_before) != len(rewards_after):
        raise ValueError(
            f"rewards_before/after 长度不等: {len(rewards_before)} vs {len(rewards_after)}"
        )
    sb = aggregate(rewards_before)
    sa = aggregate(rewards_after)
    delta = sa - sb

    # L6: 绝对地板检查 (先于相对优于判定)
    if floor is not None and sa < floor:
        return GateResult(
            accepted=False,
            score_before=sb,
            score_after=sa,
            delta=delta,
            reason=f"FLOOR_REJECT: score_after={sa:.4f} < floor={floor:.4f} (绝对地板拒绝·防漂移)",
        )

    accepted = delta > epsilon
    if accepted:
        reason = f"selection_set 严格优于: {sb:.4f} → {sa:.4f} (delta={delta:+.4f})"
    elif abs(delta) < 1e-9:
        reason = f"selection_set 平局拒绝: {sb:.4f} (delta=0)"
    else:
        reason = f"selection_set 未严格优于: {sb:.4f} → {sa:.4f} (delta={delta:+.4f})"
    return GateResult(
        accepted=accepted,
        score_before=sb,
        score_after=sa,
        delta=delta,
        reason=reason,
    )
