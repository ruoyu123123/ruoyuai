"""skill_opt.reward — 多信号聚合成 binary reward

E2 调研发现现成可用的 4 个 binary 信号:
1. audit_hub.verdict        : pass / fail
2. reading-reflector.verdict: pass / fail
3. voice-checker drift_count : == 0
4. writer-truth-check lie    : == 0

聚合策略:
- strict (论文风格): 4 个全 pass → 1.0,任一 fail → 0.0
- soft (推荐起步) : 通过比例 → [0, 1] 连续

北极星纪律 ⑤: reward 只读现有 judge/scanner 产物的 binary 字段,不引入新评判。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RewardComponents:
    audit_pass: bool | None = None
    reading_pass: bool | None = None
    voice_clean: bool | None = None
    truth_clean: bool | None = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "audit_pass": self.audit_pass,
            "reading_pass": self.reading_pass,
            "voice_clean": self.voice_clean,
            "truth_clean": self.truth_clean,
            "raw": self.raw,
        }


def _read_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def collect_for_cluster(
    project_root: Path,
    cluster_id: str,
) -> RewardComponents:
    """从写作链路产物里拣 4 个 binary 信号。

    路径约定 (E2 调研实测):
    - audit_hub:        _数据库/.audit/<cluster_id>.json     字段 .verdict ∈ {pass, fail}
    - reading-reflect:  _数据库/.judge_reports/<cluster_id>_reading.json  .verdict
    - voice-checker:    _数据库/.judge_reports/<cluster_id>_voice.json    .voice_drift_count
    - truth-check:      _数据库/.judge_reports/<cluster_id>_truth.json    .lie_count

    缺失 → 字段 None (聚合时按"未通过"处理)。
    """
    db = project_root / "_数据库"
    audit_dir = db / ".audit"
    judge_dir = db / ".judge_reports"

    audit = _read_json(audit_dir / f"{cluster_id}.json")
    reading = _read_json(judge_dir / f"{cluster_id}_reading.json")
    voice = _read_json(judge_dir / f"{cluster_id}_voice.json")
    truth = _read_json(judge_dir / f"{cluster_id}_truth.json")

    def _verdict_pass(d: dict) -> bool | None:
        v = d.get("verdict")
        if v is None:
            return None
        return str(v).lower() == "pass"

    def _count_clean(d: dict, key: str) -> bool | None:
        n = d.get(key)
        if n is None:
            return None
        try:
            return int(n) == 0
        except (TypeError, ValueError):
            return None

    return RewardComponents(
        audit_pass=_verdict_pass(audit),
        reading_pass=_verdict_pass(reading),
        voice_clean=_count_clean(voice, "voice_drift_count"),
        truth_clean=_count_clean(truth, "lie_count"),
        raw={
            "audit": bool(audit),
            "reading": bool(reading),
            "voice": bool(voice),
            "truth": bool(truth),
        },
    )


def aggregate(rc: RewardComponents, mode: str = "soft") -> float:
    """聚合 4 个信号成 [0, 1] reward。

    Args:
        rc: 4 个 binary 信号 (None=未跑/缺失)
        mode:
          - "strict": 4 个非 None 信号全 True → 1.0,否则 0.0
          - "soft"  : 通过数 / 总有效数 (None 不计入分母)

    None 字段在 strict 下当 False (保守:数据缺失不奖励)。
    None 字段在 soft 下不计入分母 (数据缺失不惩罚也不奖励)。
    """
    signals = [rc.audit_pass, rc.reading_pass, rc.voice_clean, rc.truth_clean]

    if mode == "strict":
        # 任一 None 或 False → 不奖励
        if any(s is not True for s in signals):
            return 0.0
        return 1.0

    if mode == "soft":
        valid = [s for s in signals if s is not None]
        if not valid:
            return 0.0  # 全无数据 → 0
        return sum(1 for s in valid if s) / len(valid)

    raise ValueError(f"未知 mode: {mode}")


def reward_for_cluster(
    project_root: Path,
    cluster_id: str,
    mode: str = "soft",
) -> tuple[float, RewardComponents]:
    """单 cluster 的 reward 计算 (rollout 阶段调用)。

    Returns:
        (reward, components):
          reward      ∈ [0, 1] 聚合分
          components  : 原始 binary 字段 (用于落 trajectory log)
    """
    rc = collect_for_cluster(project_root, cluster_id)
    return aggregate(rc, mode=mode), rc
