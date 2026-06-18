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


def _audit_verdict_pass(d: dict) -> bool | None:
    """audit verdict ∈ {pass, waived, fail, fatal}。pass 和 waived 都算通过(waived=issue全豁免)。"""
    v = d.get("verdict")
    if v is None:
        return None
    return str(v).lower() in ("pass", "waived")


def _last_round_reading(reading_dir: Path, cluster_id: str) -> dict:
    """reading-reflector 每 cluster 多轮 round,取最后一轮(SRE 健康检查最终态)。"""
    rounds = sorted(reading_dir.glob(f"{cluster_id}_round_*.json"))
    if not rounds:
        return {}
    return _read_json(rounds[-1])


def _grade_to_binary(d: dict, key: str = "overall_grade") -> bool | None:
    """A/B 算通过, C/D/F 算失败。无 grade 字段 → None。"""
    g = d.get(key)
    if g is None:
        return None
    return str(g).upper() in ("A", "B", "S")


def _cluster_chapter_range(project_root: Path, cluster_id: str) -> list[int]:
    """从 事件簇.json 取本 cluster 含哪些章号。"""
    ec_path = project_root / "_数据库" / "事件簇.json"
    if not ec_path.exists():
        return []
    try:
        d = json.loads(ec_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    for c in d.get("clusters", []):
        if c.get("cluster_id") == cluster_id:
            # 真实字段是 chapter_range: [start, end] (闭区间)
            cr = c.get("chapter_range")
            if cr and len(cr) == 2:
                return list(range(int(cr[0]), int(cr[1]) + 1))
            # 兜底:scene_storyboard 含 ch 字段
            sb = c.get("scene_storyboard") or []
            ch_set = set()
            for s in sb:
                ch = s.get("ch") or s.get("chapter")
                if ch:
                    ch_set.add(int(ch))
            return sorted(ch_set)
    return []


def _truth_clean_for_cluster(judge_dir: Path, project_root: Path, cluster_id: str) -> bool | None:
    """truth-check 是章级,聚合 cluster 内所有章:全 A/B → 通过,任一 C/D/F → 失败,无数据 → None。"""
    chs = _cluster_chapter_range(project_root, cluster_id)
    if not chs:
        return None
    grades = []
    for ch in chs:
        d = _read_json(judge_dir / f"ch_{ch:03d}_writer-truth-check.json")
        g = d.get("overall_grade")
        if g:
            grades.append(g)
    if not grades:
        return None
    return all(str(g).upper() in ("A", "B", "S") for g in grades)


def collect_for_cluster(
    project_root: Path,
    cluster_id: str,
) -> RewardComponents:
    """从写作链路真实产物拣 4 个 binary 信号(已按 凿窍纪 实测路径核对)。

    真实路径 (2026-06-18 凿窍纪 grep 实测):
    - audit:        _数据库/.audit/<cluster_id>_audit.json  字段 .verdict ∈ {pass, waived, fail, fatal}
    - reading:      _数据库/.reading_reflection/<cluster_id>_round_<N>.json (取最后一轮) .verdict ∈ {pass, fail}
    - voice:        _数据库/.judge_reports/<cluster_id>_voice-checker.json .overall_grade ∈ {A,B,C,D,F}
    - truth:        _数据库/.judge_reports/ch_<NNN>_writer-truth-check.json (章级·聚合 cluster 内所有章)

    pass/waived 都算 audit 通过 (waived=issue 全豁免=实质 pass)
    overall_grade A/B/S 算通过, C/D/F 失败
    缺失 → 字段 None
    """
    db = project_root / "_数据库"
    audit_dir = db / ".audit"
    judge_dir = db / ".judge_reports"
    reading_dir = db / ".reading_reflection"

    audit = _read_json(audit_dir / f"{cluster_id}_audit.json")
    reading = _last_round_reading(reading_dir, cluster_id)
    voice = _read_json(judge_dir / f"{cluster_id}_voice-checker.json")

    def _verdict_pass(d: dict) -> bool | None:
        v = d.get("verdict")
        if v is None:
            return None
        return str(v).lower() in ("pass", "waived")

    return RewardComponents(
        audit_pass=_audit_verdict_pass(audit),
        reading_pass=_verdict_pass(reading),
        voice_clean=_grade_to_binary(voice),
        truth_clean=_truth_clean_for_cluster(judge_dir, project_root, cluster_id),
        raw={
            "audit": bool(audit),
            "reading": bool(reading),
            "voice": bool(voice),
            "truth_chapters": _cluster_chapter_range(project_root, cluster_id),
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
