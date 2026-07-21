"""Regression tests for cluster-only world_evolution_apply_card.

The public contract is:
    world_evolution_apply_card.py <project> --next-key <key> --choice <artifact>

The script must consume the cluster user-choice artifact and must not depend on
per-chapter fate card files or A/B/C labels.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import world_evolution_apply_card as mod  # noqa: E402

DB = "_\u6570\u636e\u5e93"
EVENT_CLUSTER = "\u4e8b\u4ef6\u7c07.json"
WORLD_STATE = "\u4e16\u754c\u72b6\u6001.json"
RIPPLE_RULES = "\u6d9f\u6f2a\u89c4\u5219.json"


def _mk_project(root: Path) -> Path:
    db = root / DB
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / EVENT_CLUSTER).write_text(
        json.dumps(
            {
                "clusters": [
                    {"cluster_id": "cluster_001", "chapter_range": [1, 5], "status": "done"},
                    {"cluster_id": "cluster_002", "status": "in_progress"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def _choice_path(root: Path, key: str = "002") -> Path:
    return root / DB / ".wal" / f"cluster_{key}_user_choice.json"


def _write_choice(root: Path, payload: dict, key: str = "002") -> Path:
    path = _choice_path(root, key)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_world_and_rules(root: Path) -> None:
    db = root / DB
    world = {
        "current_ch": 0,
        "factions_state": {"imperial_court": {"prestige": 50}},
    }
    rules = {
        "ripple_rules": [
            {
                "id": "RR_TEST_01",
                "trigger_type": "minor_event",
                "trigger_match": "imperial prestige damaged",
                "ripples": [
                    {
                        "target": "factions_state.imperial_court.prestige",
                        "delta": -10,
                        "reason": "test rule",
                    }
                ],
            }
        ]
    }
    (db / WORLD_STATE).write_text(json.dumps(world, ensure_ascii=False), encoding="utf-8")
    (db / RIPPLE_RULES).write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")


def _write_candidates(root: Path, key: str, candidates: list[dict]) -> Path:
    path = root / DB / ".wal" / f"cluster_{key}_brief_candidates.json"
    path.write_text(json.dumps({"candidates": candidates}, ensure_ascii=False), encoding="utf-8")
    return path


def _run(project: Path, choice: Path | None = None, next_key: str = "002") -> tuple[int, str, str]:
    choice_arg = str(choice if choice is not None else _choice_path(project))
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = mod.main([str(project), "--next-key", next_key, "--choice", choice_arg])
    return code, stdout.getvalue(), stderr.getvalue()


def _run_raw(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = mod.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def test_missing_choice_file_exits_1():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        code, out, err = _run(project)
        assert code == 1
        assert "choice file not found" in out
        assert err == ""


def test_cluster_001_has_no_previous_choice_and_passes():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        code, out, err = _run(project, next_key="001")
        assert code == 0
        assert "cluster_001 has no previous user-choice artifact" in out
        assert err == ""


def test_corrupt_choice_json_exits_2():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        path = _choice_path(project)
        path.write_text("{ not valid json", encoding="utf-8")
        code, out, _ = _run(project)
        assert code == 2
        assert "choice JSON parse failed" in out


def test_non_brief_choice_exits_2():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        _write_choice(project, {"mode": "x", "no_brief": True})
        code, out, _ = _run(project)
        assert code == 2
        assert "not a cluster brief" in out


def test_choice_cluster_mismatch_exits_2():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        _write_choice(
            project,
            {
                "answer": {
                    "cluster_id": "cluster_003",
                    "scope_summary": "wrong cluster",
                    "ripple_match": "imperial prestige damaged",
                }
            },
        )
        code, out, _ = _run(project, next_key="002")
        assert code == 2
        assert "choice cluster mismatch" in out


def test_missing_ripple_match_exits_2():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        _write_choice(project, {"answer": {"cluster_id": "cluster_002", "scope_summary": "no ripple"}})
        code, out, _ = _run(project)
        assert code == 2
        assert "missing required ripple_match" in out


def test_empty_ripple_match_is_legal_no_op_exits_0():
    """回归锁（2026-07-18 真机实战撞坑）：novel-outline-planner.md 第531行明确
    「ripple_match 必须是真实可匹配的标签或空字符串」——空字符串是合法声明"本 cluster
    不触发涟漪"（如故事节奏刻意把某条规则留到后续 ME 才触发），旧代码硬拒绝空字符串与
    该 agent 合约矛盾。空值路径不应依赖 世界状态/涟漪规则 文件存在（未调用 apply_minor_event）。"""
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        _write_choice(
            project,
            {
                "answer": {
                    "cluster_id": "cluster_002",
                    "title": "quiet bridge",
                    "ripple_match": "   ",
                }
            },
        )
        code, out, _ = _run(project)
        assert code == 0
        assert "quiet bridge" in out
        assert "不触发涟漪" in out


def test_world_files_missing_exits_2():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        _write_choice(
            project,
            {
                "answer": {
                    "cluster_id": "cluster_002",
                    "title": "ripple card",
                    "ripple_match": "imperial prestige damaged",
                }
            },
        )
        code, out, _ = _run(project)
        assert code == 2
        assert "world evolution required files missing" in out
        assert WORLD_STATE in out and RIPPLE_RULES in out


def test_happy_path_applies_minor_event_from_cluster_choice():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        _write_choice(
            project,
            {
                "answer": {
                    "cluster_id": "cluster_002_candidate_1",
                    "title": "imperial setback",
                    "scope_summary": "the court loses visible face after a failed public maneuver",
                    "ripple_match": "imperial prestige damaged",
                    "scene_storyboard": [{"summary": "court setback"}],
                }
            },
        )
        _write_candidates(
            project,
            "002",
            [
                {
                    "cluster_id": "cluster_002_candidate_1",
                    "scope_summary": "the court loses visible face after a failed public maneuver",
                    "ripple_match": "imperial prestige damaged",
                    "scene_storyboard": [{"summary": "court setback"}],
                },
                {
                    "cluster_id": "cluster_002_candidate_2",
                    "scope_summary": "a quiet detour",
                    "ripple_match": "quiet detour",
                    "scene_storyboard": [{"summary": "detour"}],
                },
            ],
        )
        _write_world_and_rules(project)

        code, out, _ = _run(project)
        assert code == 0
        assert "imperial setback" in out
        assert "cluster=cluster_002" in out
        assert "RR_TEST_01" in out
        assert "WARN" not in out

        world_after = json.loads((project / DB / WORLD_STATE).read_text(encoding="utf-8"))
        assert world_after["factions_state"]["imperial_court"]["prestige"] == 40
        assert any(
            e.get("trigger_type") == "minor_event"
            and e.get("trigger_value") == "imperial prestige damaged"
                and e.get("cluster_id") == "cluster_002"
            for e in world_after.get("world_ticks_log", [])
        )
        event_after = json.loads((project / DB / EVENT_CLUSTER).read_text(encoding="utf-8"))
        landed = next(c for c in event_after["clusters"] if c["cluster_id"] == "cluster_002")
        assert landed["user_choice"] == "candidate_1"
        assert landed["_user_decision"]["selected_candidate_index"] == 1
        assert landed["_user_decision"]["candidate_count"] == 2
        assert landed["choice_leads_to"] == "the court loses visible face after a failed public maneuver"
        assert landed["world_evolution_card"]["cluster_id"] == "cluster_002"
        assert landed["world_evolution_card"]["matched_rules"] == ["RR_TEST_01"]
        assert landed["scene_storyboard"] == [{"summary": "court setback"}]


def test_ripple_match_no_rule_match_exits_2_after_cluster_writeback():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        _write_choice(
            project,
            {
                "cluster_id": "cluster_002",
                "title": "uncovered ripple",
                "ripple_match": "not covered by rules",
            },
        )
        _write_world_and_rules(project)

        code, out, _ = _run(project)
        assert code == 2
        assert "matched no rules" in out

        world_after = json.loads((project / DB / WORLD_STATE).read_text(encoding="utf-8"))
        assert world_after["factions_state"]["imperial_court"]["prestige"] == 50
        event_after = json.loads((project / DB / EVENT_CLUSTER).read_text(encoding="utf-8"))
        landed = next(c for c in event_after["clusters"] if c["cluster_id"] == "cluster_002")
        assert landed["user_choice"] == "chosen"
        assert landed["world_evolution_card"]["matched_rules"] == []


def test_legacy_chapter_label_positionals_hard_rejected_exit_2():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        code, out, err = _run_raw([str(project), "5", "A"])
        assert code == 2
        assert out == ""
        assert "legacy single-chapter direction-card CLI is removed" in err


def test_public_cluster_flag_hard_rejected_exit_2():
    with tempfile.TemporaryDirectory() as d:
        project = _mk_project(Path(d))
        code, out, err = _run_raw([
            str(project),
            "--cluster",
            "002",
            "--choice",
            str(_choice_path(project)),
        ])
        assert code == 2
        assert out == ""
        assert "--cluster is not a public" in err
