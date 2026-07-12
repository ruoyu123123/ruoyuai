"""clock_engine 的 cluster-native 契约与事件推进回归测试。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import clock_engine as ce  # noqa: E402


def clock(clock_id: str = "CK_001", **overrides) -> dict:
    item = {
        "clock_id": clock_id,
        "label": "反派耐心",
        "category": "antagonist_pressure",
        "ticks": 0,
        "max": 5,
        "tick_on": ["cluster_end"],
        "tick_per_event": 1,
        "trigger_on_max": "ME-V1-05",
        "visible_to_protagonist": False,
        "visible_to_writer": True,
        "is_surprise": False,
        "since_cluster": "cluster_001",
        "spawned_by": "outline",
        "status": "active",
        "_reason": "反派耐心持续消耗",
    }
    item.update(overrides)
    return item


def clock_file(*items: dict) -> dict:
    return {
        "_schema": "cluster_clocks",
        "schema_version": "1.0",
        "clocks": list(items),
    }


def make_project(tmp_path: Path, data: dict | None = None) -> Path:
    database = tmp_path / "_数据库"
    database.mkdir(parents=True)
    if data is not None:
        (database / "时钟表.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    return tmp_path


def read_file(project: Path) -> dict:
    return json.loads((project / "_数据库" / "时钟表.json").read_text(encoding="utf-8"))


def run_cli(*args: str, internal: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if internal:
        env["RUOYUAI_CLUSTER_STATE_INTERNAL"] = "1"
    else:
        env.pop("RUOYUAI_CLUSTER_STATE_INTERNAL", None)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "clock_engine.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def test_cluster_end_advances_only_matching_clocks(tmp_path):
    project = make_project(tmp_path, clock_file(
        clock("CK_END", tick_on=["cluster_end"]),
        clock("CK_EVENT", tick_on=["minor_event:fight*"]),
    ))
    result = ce.tick_cluster_end(project, "cluster_007")
    assert result["cluster_id"] == "cluster_007"
    assert result["event"] == {"type": "cluster_end", "value": ""}
    assert [item["clock_id"] for item in result["ticked"]] == ["CK_END"]
    by_id = {item["clock_id"]: item for item in read_file(project)["clocks"]}
    assert by_id["CK_END"]["ticks"] == 1
    assert by_id["CK_EVENT"]["ticks"] == 0


def test_explicit_event_glob_triggers_at_current_cluster(tmp_path):
    project = make_project(tmp_path, clock_file(clock(
        ticks=1,
        max=2,
        tick_on=["minor_event:fight*"],
    )))
    result = ce.tick_event(project, "cluster_012", "minor_event", "fight_boss")
    assert result["triggered"][0]["clock_id"] == "CK_001"
    stored = read_file(project)["clocks"][0]
    assert stored["ticks"] == 2
    assert stored["status"] == "triggered"
    assert stored["triggered_at_cluster"] == "cluster_012"


def test_nonmatching_event_does_not_advance(tmp_path):
    project = make_project(tmp_path, clock_file(clock(tick_on=["minor_event:fight*"])))
    result = ce.tick_event(project, "cluster_002", "minor_event", "conversation")
    assert result["ticked"] == []
    assert read_file(project)["clocks"][0]["ticks"] == 0


def test_list_active_uses_trigger_distance_and_cluster_id(tmp_path):
    project = make_project(tmp_path, clock_file(
        clock("CK_U", ticks=9, max=10),
        clock("CK_A", ticks=7, max=10),
        clock("CK_N", ticks=0, max=10),
        clock("CK_T", ticks=10, max=10, status="triggered",
              triggered_at_cluster="cluster_003"),
    ))
    result = ce.list_active(project, "cluster_004")
    assert result["cluster_id"] == "cluster_004"
    by_id = {item["clock_id"]: item for item in result["active_clocks"]}
    assert by_id["CK_U"]["urgency"] == "urgent"
    assert by_id["CK_A"]["urgency"] == "approaching"
    assert by_id["CK_N"]["urgency"] == "normal"
    assert "CK_T" not in by_id
    assert result["active_clocks"][0]["clock_id"] == "CK_U"


def test_hidden_clock_fields_are_explicit(tmp_path):
    project = make_project(tmp_path, clock_file(clock(
        visible_to_writer=False,
        is_surprise=True,
    )))
    item = ce.list_active(project, "cluster_001")["active_clocks"][0]
    assert item["visible_to_writer"] is False
    assert item["is_surprise"] is True


def test_spawn_requires_complete_definition_and_records_cluster(tmp_path):
    project = make_project(tmp_path, clock_file(clock("CK_008")))
    definition = {
        "label": "遗物交付",
        "category": "foreshadowing_due",
        "max": 4,
        "tick_on": ["cluster_end", "minor_event:父亲*"],
        "tick_per_event": 1,
        "trigger_on_max": "FS_BOX",
        "visible_to_protagonist": False,
        "visible_to_writer": True,
        "is_surprise": False,
        "spawned_by": "foreshadowing",
        "_reason": "关联事件累积后交付",
    }
    result = ce.spawn(project, "cluster_009", definition)
    assert result["created"] == "CK_009"
    stored = read_file(project)["clocks"][-1]
    assert stored["since_cluster"] == "cluster_009"
    assert stored["ticks"] == 0
    assert stored["status"] == "active"


def test_spawn_rejects_implicit_defaults(tmp_path):
    project = make_project(tmp_path, clock_file())
    with pytest.raises(ce.ClockContractError, match="missing"):
        ce.spawn(project, "cluster_001", {"label": "不完整"})


@pytest.mark.parametrize(
    "data, message",
    [
        ({"story_clocks": []}, "schema"),
        (clock_file(clock(tick_on=["chapter_end"])), "chapter_end"),
        (clock_file(clock(visible_to_writer=True, is_surprise=True)), "暗线"),
        (clock_file(clock(ticks=6, max=5)), "0..max"),
    ],
)
def test_legacy_or_malformed_schema_fails_hard(tmp_path, data, message):
    project = make_project(tmp_path, data)
    with pytest.raises(ce.ClockContractError, match=message):
        ce.load_clocks(project)


def test_missing_file_fails_hard(tmp_path):
    project = make_project(tmp_path)
    with pytest.raises(ce.ClockContractError, match="文件不存在"):
        ce.dashboard(project)


def test_cli_cluster_end_returns_one_when_clock_triggers(tmp_path):
    project = make_project(tmp_path, clock_file(clock(ticks=4, max=5)))
    proc = run_cli(
        str(project), "tick-cluster-end", "--cluster", "cluster_005"
    )
    assert proc.returncode == 1, proc.stderr
    result = json.loads(proc.stdout)
    assert result["triggered"][0]["clock_id"] == "CK_001"


def test_cli_requires_explicit_cluster_and_rejects_chapter_end(tmp_path):
    project = make_project(tmp_path, clock_file(clock()))
    missing = run_cli(str(project), "list")
    assert missing.returncode == 2
    legacy = run_cli(
        str(project), "tick-event", "--cluster", "cluster_001",
        "--event-type", "chapter_end",
    )
    assert legacy.returncode == 2
    assert "chapter_end" in legacy.stderr


def test_cli_rejects_direct_public_invocation(tmp_path):
    project = make_project(tmp_path, clock_file())
    proc = run_cli(str(project), "dashboard", internal=False)
    assert proc.returncode == 2
    assert "clock_engine.py" in proc.stderr
    assert "Traceback" not in proc.stderr
