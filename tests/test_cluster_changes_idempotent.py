"""cluster writer self-eval 收据的严格、幂等、零状态写入测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import save_state  # noqa: E402


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _changes(*, waivers=None, uncertainty=None) -> dict:
    self_eval = {"waivers": list(waivers or [])}
    if uncertainty is not None:
        self_eval["uncertainty_flags"] = list(uncertainty)
    return {"self_eval": self_eval}


def _write_changes(project: Path, cluster_id: str, value: dict) -> Path:
    path = (
        project / "章节" / f"{cluster_id}_draft"
        / f"{cluster_id}_changes.json"
    )
    _write_json(path, value)
    return path


def _receipt(project: Path, cluster_id: str) -> dict:
    path = project / "_数据库" / ".wal" / f"{cluster_id}_apply_cluster.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_valid_cluster_self_eval_writes_canonical_receipt(tmp_path: Path):
    _write_changes(
        tmp_path,
        "cluster_001",
        _changes(
            waivers=[{"code": "STYLE_DENSITY", "reason": "本块刻意放慢"}],
            uncertainty=["scene_02 的语气可能偏冷"],
        ),
    )

    assert save_state.cmd_apply_cluster_changes(tmp_path, "1") == 0
    receipt = _receipt(tmp_path, "cluster_001")
    assert receipt["cluster_id"] == "cluster_001"
    assert receipt["source"] == "章节/cluster_001_draft/cluster_001_changes.json"
    assert receipt["contract"] == "cluster_writer_self_eval"
    assert receipt["waiver_count"] == 1
    assert receipt["self_eval_fields"] == ["uncertainty_flags", "waivers"]
    assert receipt["objective_state_applied"] is False


def test_replay_is_semantically_idempotent(tmp_path: Path):
    _write_changes(tmp_path, "cluster_007", _changes())
    assert save_state.cmd_apply_cluster_changes(tmp_path, "007") == 0
    first = _receipt(tmp_path, "cluster_007")

    assert save_state.cmd_apply_cluster_changes(tmp_path, "cluster_007") == 0
    second = _receipt(tmp_path, "cluster_007")
    first.pop("validated_at")
    second.pop("validated_at")
    assert second == first


def test_validation_never_mutates_objective_databases(tmp_path: Path):
    db = tmp_path / "_数据库"
    objective = {
        "时间线.json": {"current_time": {"period": "黎明"}, "time_log": []},
        "地图.json": {"locations": [{"id": "gate", "status": "closed"}]},
        "世界状态.json": {"active_npc_threads": []},
        "人物卡.json": {"characters": [{"id": "lin", "name": "林潜"}]},
    }
    before = {}
    for name, value in objective.items():
        path = db / name
        _write_json(path, value)
        before[name] = path.read_bytes()
    _write_changes(tmp_path, "cluster_002", _changes())

    assert save_state.cmd_apply_cluster_changes(tmp_path, "cluster_002") == 0
    assert all((db / name).read_bytes() == raw for name, raw in before.items())
    assert _receipt(tmp_path, "cluster_002")["objective_state_applied"] is False


def test_legacy_objective_fields_are_rejected_without_receipt(tmp_path: Path):
    _write_changes(tmp_path, "cluster_003", {
        "self_eval": {"waivers": []},
        "factual": {"time_advance": {"elapsed": "一日"}},
    })

    assert save_state.cmd_apply_cluster_changes(tmp_path, "cluster_003") == 2
    receipt = tmp_path / "_数据库" / ".wal" / "cluster_003_apply_cluster.json"
    assert not receipt.exists()


def test_missing_required_waivers_is_rejected(tmp_path: Path):
    _write_changes(tmp_path, "cluster_004", {"self_eval": {}})

    assert save_state.cmd_apply_cluster_changes(tmp_path, "cluster_004") == 2
    receipt = tmp_path / "_数据库" / ".wal" / "cluster_004_apply_cluster.json"
    assert not receipt.exists()
