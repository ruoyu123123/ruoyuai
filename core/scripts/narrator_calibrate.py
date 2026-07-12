#!/usr/bin/env python3
"""按完成的 cluster 校准叙事压力阶段并给出下一 cluster 的 advisory 建议。

输入来自当前 cluster 的终稿与 writer self-eval；持久状态只使用
``叙事节拍器.json`` 的 cluster-native 结构。

退出码：0 无紧迫建议；1 产生紧迫 advisory；2 输入或状态契约损坏。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json
import state_cli_guard


SCHEMA_NAME = "cluster_storyteller"
SCHEMA_VERSION = "1.0"
CLUSTER_RE = re.compile(r"^cluster_[0-9]{3,}$")
OUTCOMES = {"setback", "win", "neutral"}
PHASES = {"rising", "climax", "cooldown", "steady"}
TOP_REQUIRED = {
    "_schema", "schema_version", "rhythm_profile", "beat_targets",
    "default_beat_policy", "appraisal_beats", "storyteller_profile",
    "current_pressure_phase", "since_phase_change_cluster",
    "cluster_outcome_log", "adaptation_factor", "narrator_recommendation",
}
PUBLIC_TOP_KEYS = TOP_REQUIRED - {"_schema"} | {"consumption"}


class NarratorContractError(ValueError):
    """叙事节拍器或 cluster 创作产物不符合唯一契约。"""


def _require_cluster_id(value: str) -> str:
    if not isinstance(value, str) or not CLUSTER_RE.fullmatch(value):
        raise NarratorContractError(f"非法 cluster_id: {value!r}")
    return value


def _cluster_num(cluster_id: str) -> int:
    return int(_require_cluster_id(cluster_id).rsplit("_", 1)[1])


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise NarratorContractError(f"文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NarratorContractError(f"JSON 读取失败: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise NarratorContractError(f"JSON 顶层必须是 object: {path}")
    return data


def _require_string(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NarratorContractError(f"{field} 必须是非空字符串")
    return value


def validate_pacer(pacer: dict) -> dict:
    missing = TOP_REQUIRED - set(pacer)
    public = {key for key in pacer if not str(key).startswith("_")}
    unknown = public - PUBLIC_TOP_KEYS
    if missing or unknown:
        raise NarratorContractError(
            f"叙事节拍器字段错误: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if pacer["_schema"] != SCHEMA_NAME or pacer["schema_version"] != SCHEMA_VERSION:
        raise NarratorContractError(
            f"叙事节拍器 schema 必须是 {SCHEMA_NAME!r}/{SCHEMA_VERSION!r}"
        )
    _require_string(pacer["rhythm_profile"], "rhythm_profile")
    _require_string(pacer["storyteller_profile"], "storyteller_profile")
    if pacer["current_pressure_phase"] not in PHASES:
        raise NarratorContractError("current_pressure_phase 非法")
    _require_cluster_id(pacer["since_phase_change_cluster"])
    for field in ("beat_targets", "appraisal_beats", "cluster_outcome_log"):
        if not isinstance(pacer[field], list):
            raise NarratorContractError(f"{field} 必须是数组")
    if not isinstance(pacer["default_beat_policy"], dict):
        raise NarratorContractError("default_beat_policy 必须是 object")
    for index, beat in enumerate(pacer["beat_targets"]):
        if not isinstance(beat, dict):
            raise NarratorContractError(f"beat_targets[{index}] 必须是 object")
        _require_cluster_id(beat.get("cluster_id"))
    seen: set[str] = set()
    for index, entry in enumerate(pacer["cluster_outcome_log"]):
        if not isinstance(entry, dict):
            raise NarratorContractError(f"cluster_outcome_log[{index}] 必须是 object")
        cid = _require_cluster_id(entry.get("cluster_id"))
        if cid in seen:
            raise NarratorContractError(f"cluster_outcome_log 重复 cluster_id: {cid}")
        seen.add(cid)
        if entry.get("outcome") not in OUTCOMES:
            raise NarratorContractError(f"cluster_outcome_log[{index}].outcome 非法")
        if not isinstance(entry.get("intensity"), int) or not 0 <= entry["intensity"] <= 10:
            raise NarratorContractError(f"cluster_outcome_log[{index}].intensity 必须位于 0..10")
        if entry.get("outcome_source") not in {"writer_declared", "prose_inference"}:
            raise NarratorContractError(f"cluster_outcome_log[{index}].outcome_source 非法")
    adaptation = pacer["adaptation_factor"]
    required_adaptation = {
        "recent_n_clusters", "expected_setback_per_n_clusters", "tolerance_window",
        "current_setback_count_in_window", "current_win_streak", "current_loss_streak",
    }
    if not isinstance(adaptation, dict) or not required_adaptation <= set(adaptation):
        raise NarratorContractError("adaptation_factor 缺少 cluster 窗口字段")
    for field in required_adaptation:
        value = adaptation[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise NarratorContractError(f"adaptation_factor.{field} 必须是非负整数")
    if adaptation["recent_n_clusters"] <= 0:
        raise NarratorContractError("adaptation_factor.recent_n_clusters 必须大于 0")
    recommendation = pacer["narrator_recommendation"]
    if not isinstance(recommendation, dict):
        raise NarratorContractError("narrator_recommendation 必须是 object")
    for field in ("next_cluster_target_outcome", "next_cluster_intensity_target"):
        if field not in recommendation:
            raise NarratorContractError(f"narrator_recommendation 缺少 {field}")
    if recommendation["next_cluster_target_outcome"] not in {"setback", "win", "auto"}:
        raise NarratorContractError("next_cluster_target_outcome 非法")
    return pacer


def load_pacer(project_root: Path) -> dict:
    path = Path(project_root) / "_数据库" / "叙事节拍器.json"
    return validate_pacer(_read_json(path))


def save_pacer(project_root: Path, pacer: dict) -> None:
    validate_pacer(pacer)
    atomic_json.atomic_write_json(Path(project_root) / "_数据库" / "叙事节拍器.json", pacer)


def _cluster_artifacts(project_root: Path, cluster_id: str) -> tuple[dict, str]:
    draft_dir = Path(project_root) / "章节" / f"{cluster_id}_draft"
    changes = _read_json(draft_dir / f"{cluster_id}_changes.json")
    self_eval = changes.get("self_eval")
    if not isinstance(self_eval, dict):
        raise NarratorContractError("cluster changes.self_eval 必须是 object")
    draft_path = draft_dir / f"{cluster_id}_draft.txt"
    if not draft_path.is_file():
        raise NarratorContractError(f"文件不存在: {draft_path}")
    try:
        draft = draft_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise NarratorContractError(f"正文读取失败: {draft_path}: {exc}") from exc
    if not draft.strip():
        raise NarratorContractError(f"cluster 正文为空: {draft_path}")
    return changes, draft


def infer_outcome(changes: dict, draft: str) -> tuple[str, int, str]:
    """优先采用 writer outcome，自评缺席时从整块正文推断 advisory outcome。"""
    self_eval = changes.get("self_eval")
    if not isinstance(self_eval, dict):
        raise NarratorContractError("changes.self_eval 必须是 object")
    alignment = self_eval.get("storyteller_alignment")
    if alignment is not None and not isinstance(alignment, dict):
        raise NarratorContractError("storyteller_alignment 必须是 object")
    declared = (alignment or {}).get("actual_outcome")
    if declared is not None and declared not in OUTCOMES:
        raise NarratorContractError("storyteller_alignment.actual_outcome 非法")

    negative = ["失败", "受伤", "死亡", "暴露", "败退", "崩溃", "失控", "受重创"]
    positive = ["成功", "赢", "突破", "救下", "夺回", "解决", "击败", "达成", "逃脱"]
    negative_hits = sum(draft.count(word) for word in negative)
    positive_hits = sum(draft.count(word) for word in positive)
    intensity = min(8, 2 + max(negative_hits, positive_hits))
    if declared is not None:
        return declared, intensity, "writer_declared"
    if negative_hits >= max(2, positive_hits + 1):
        inferred = "setback"
    elif positive_hits >= max(2, negative_hits + 1):
        inferred = "win"
    else:
        inferred = "neutral"
    return inferred, intensity, "prose_inference"


def narrator_view(pacer: dict, cluster_id: str) -> dict:
    """返回当前 cluster 可直接注入 writer 的节拍器视图。"""
    validate_pacer(pacer)
    cluster_id = _require_cluster_id(cluster_id)
    targets = [
        dict(beat) for beat in pacer["beat_targets"]
        if beat["cluster_id"] == cluster_id
    ]
    return {
        "profile": pacer["storyteller_profile"],
        "current_phase": pacer["current_pressure_phase"],
        "since_phase_change_cluster": pacer["since_phase_change_cluster"],
        "rhythm_profile": pacer["rhythm_profile"],
        "current_cluster_beats": targets,
        "default_beat_policy": pacer["default_beat_policy"],
        "adaptation_factor": pacer["adaptation_factor"],
        "narrator_recommendation": pacer["narrator_recommendation"],
    }


def evaluate_phase(
    log: list[dict], current_phase: str, since_cluster: str, cluster_id: str
) -> tuple[str, str]:
    """按 cluster outcome 序列推进 advisory 压力阶段。"""
    recent = log[-5:]
    win_run = 0
    win_intensity = 0
    setback_run = 0
    for entry in reversed(recent):
        if entry["outcome"] != "win":
            break
        win_run += 1
        win_intensity += entry["intensity"]
    for entry in reversed(recent):
        if entry["outcome"] != "setback":
            break
        setback_run += 1
    elapsed = _cluster_num(cluster_id) - _cluster_num(since_cluster) + 1
    if elapsed <= 0:
        raise NarratorContractError("since_phase_change_cluster 晚于当前 cluster")
    if current_phase == "rising" and win_run >= 3 and win_intensity >= 12:
        return "climax", cluster_id
    if current_phase == "climax" and elapsed >= 2:
        return "cooldown", cluster_id
    if current_phase == "cooldown" and elapsed >= 3:
        return "steady", cluster_id
    if current_phase == "steady" and (setback_run >= 2 or elapsed >= 5):
        return "rising", cluster_id
    return current_phase, since_cluster


def calibrate(project_root: Path, cluster_id: str) -> dict:
    """消费一个完成的 cluster，幂等更新节拍器并返回下一块建议。"""
    cluster_id = _require_cluster_id(cluster_id)
    pacer = load_pacer(project_root)
    changes, draft = _cluster_artifacts(project_root, cluster_id)
    outcome, intensity, outcome_source = infer_outcome(changes, draft)

    log = [entry for entry in pacer["cluster_outcome_log"] if entry["cluster_id"] != cluster_id]
    log.append({
        "cluster_id": cluster_id,
        "outcome": outcome,
        "intensity": intensity,
        "outcome_source": outcome_source,
    })
    log.sort(key=lambda entry: _cluster_num(entry["cluster_id"]))
    pacer["cluster_outcome_log"] = log

    old_phase = pacer["current_pressure_phase"]
    new_phase, since_cluster = evaluate_phase(
        log, old_phase, pacer["since_phase_change_cluster"], cluster_id
    )
    pacer["current_pressure_phase"] = new_phase
    pacer["since_phase_change_cluster"] = since_cluster

    adaptation = pacer["adaptation_factor"]
    window = log[-adaptation["recent_n_clusters"]:]
    setbacks = sum(entry["outcome"] == "setback" for entry in window)
    win_streak = 0
    loss_streak = 0
    for entry in reversed(window):
        if entry["outcome"] != "win":
            break
        win_streak += 1
    for entry in reversed(window):
        if entry["outcome"] != "setback":
            break
        loss_streak += 1
    adaptation["current_setback_count_in_window"] = setbacks
    adaptation["current_win_streak"] = win_streak
    adaptation["current_loss_streak"] = loss_streak

    expected = adaptation["expected_setback_per_n_clusters"]
    tolerance = adaptation["tolerance_window"]
    recommendation = pacer["narrator_recommendation"]
    if setbacks + tolerance < expected:
        recommendation.update({
            "next_cluster_target_outcome": "setback",
            "next_cluster_intensity_target": "high",
            "_reason": f"近 {len(window)} 个 cluster 的 setback={setbacks}，低于节拍窗口",
        })
        urgent = True
    elif setbacks - tolerance > expected:
        recommendation.update({
            "next_cluster_target_outcome": "win",
            "next_cluster_intensity_target": "high",
            "_reason": f"近 {len(window)} 个 cluster 的 setback={setbacks}，高于节拍窗口",
        })
        urgent = True
    else:
        recommendation.update({
            "next_cluster_target_outcome": "auto",
            "next_cluster_intensity_target": "auto",
            "_reason": "近期 outcome 分布位于节拍窗口内，由 writer 按当前因果自由选择",
        })
        urgent = False

    save_pacer(project_root, pacer)
    view = narrator_view(pacer, cluster_id)
    return {
        "cluster_id": cluster_id,
        "outcome": outcome,
        "outcome_source": outcome_source,
        "intensity": intensity,
        "phase_change": old_phase != new_phase,
        "phase": new_phase,
        "rhythm_profile": view["rhythm_profile"],
        "current_cluster_beats": view["current_cluster_beats"],
        "adaptation_factor": adaptation,
        "next_recommendation": recommendation,
        "_urgent": urgent,
    }


def main() -> int:
    state_cli_guard.require_internal("narrator_calibrate.py")
    parser = argparse.ArgumentParser(description="cluster 叙事节拍校准")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    try:
        result = calibrate(Path(args.project), args.cluster)
    except (NarratorContractError, OSError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["_urgent"] else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
