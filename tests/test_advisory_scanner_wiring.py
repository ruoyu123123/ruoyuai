"""七个顾问脚本的生产调度契约。"""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_hub  # noqa: E402
import run_cross_cluster_aggregates as cross_wrapper  # noqa: E402


CLUSTER_ADVISORY_CODES = {
    "COTTON_NEEDLE_DETECTED",
    "COTTON_NEEDLE_BELOW_TARGET",
    "COTTON_NEEDLE_OVER",
    "DRAMATIC_IRONY_GAP_EMPTY",
    "DRAMATIC_IRONY_GAP_STALE",
    "LITRPG_STRUCTURE",
}

CROSS_CLUSTER_ADVISORY_CODES = {
    "ENTITY_STATE_GRAPH_CONFLICT",
    "OUSIOMETRIC_OSCILLATION_DEGRADED",
    "LOCATION_SIGNATURE_DRIFT",
    "MACGUFFIN_ORNAMENTAL",
}


def _task_map(tasks):
    return {task[0]: task for task in tasks}


def test_cluster_advisory_task_commands_and_litrpg_gate():
    project = Path("X:/novels/demo")
    draft = project / "章节" / "cluster_007_draft" / "cluster_007_draft.txt"
    tasks = audit_hub._build_cluster_advisory_tasks(
        project, draft, "cluster_007", ["--style", "style.json"], "litrpg")
    by_name = _task_map(tasks)

    assert set(by_name) == {
        "cotton_needle_subtext", "dramatic_irony_gap", "litrpg_structure"}
    cotton = by_name["cotton_needle_subtext"][1]
    assert Path(cotton[1]).name == "cotton_needle_subtext_advisor.py"
    assert cotton[2:] == [str(draft), "--project", str(project)]
    irony = by_name["dramatic_irony_gap"][1]
    assert Path(irony[1]).name == "dramatic_irony_gap_scanner.py"
    assert irony[2:] == [str(draft), "--project", str(project),
                         "--cluster", "cluster_007"]
    litrpg = by_name["litrpg_structure"][1]
    assert Path(litrpg[1]).name == "litrpg_structure_scanner.py"
    assert litrpg[2:] == [str(draft), "--project", str(project),
                          "--style", "style.json"]

    non_litrpg = audit_hub._build_cluster_advisory_tasks(
        project, draft, "cluster_007", [], "romance")
    assert "litrpg_structure" not in _task_map(non_litrpg)


def test_litrpg_dynamic_route_is_not_duplicated():
    project = Path("X:/novels/demo")
    draft = project / "章节" / "cluster_007_draft" / "cluster_007_draft.txt"
    tasks = audit_hub._build_cluster_advisory_tasks(
        project, draft, "cluster_007", [], "horror_game",
        dynamically_routed_script="litrpg_structure_scanner.py")
    assert "litrpg_structure" not in _task_map(tasks)


def test_cross_cluster_scanners_contains_four_advisors_once():
    expected = {
        "cross_cluster_entity_state_graph_aggregate",
        "cross_cluster_ousiometric_emd_scanner",
        "location_signature_consistency",
        "macguffin_entanglement_scanner",
    }
    full = cross_wrapper.SCANNERS
    assert expected.issubset(full)
    assert all(full.count(name) == 1 for name in expected)


def test_cross_cluster_commands_match_public_clis():
    project = Path("X:/novels/demo")

    def command(scanner):
        return cross_wrapper.build_scanner_command(
            scanner, SCRIPTS / f"{scanner}.py", project, "cluster_007", 8)

    entity = command("cross_cluster_entity_state_graph_aggregate")
    assert entity[2:] == [str(project)]

    ousiometric = command("cross_cluster_ousiometric_emd_scanner")
    assert ousiometric[2:] == ["--project", str(project)]

    location = command("location_signature_consistency")
    assert location[2:] == [
        "--project", str(project), "--scan-cluster", "cluster_007", "--draft",
        str(project / "章节" / "cluster_007_draft" / "cluster_007_draft.txt"),
    ]

    macguffin = command("macguffin_entanglement_scanner")
    assert macguffin[2:] == [str(project), "--last-n", "8"]


def test_location_command_updates_then_aggregates_in_one_invocation():
    source = (SCRIPTS / "location_signature_consistency.py").read_text(encoding="utf-8")
    update_at = source.index("update_registry(args.project, args.scan_cluster, draft)")
    aggregate_at = source.index("report = aggregate(args.project)")
    assert update_at < aggregate_at


def test_registry_describes_current_required_wiring():
    registry = json.loads(
        (SCRIPTS / "scanner_registry.json").read_text(encoding="utf-8"))["scanners"]
    expected = {
        "cotton_needle_subtext_advisor": "audit_hub cluster 审计",
        "dramatic_irony_gap": "audit_hub cluster 审计",
        "litrpg_structure": "audit_hub cluster 题材路由",
        "cross_cluster_entity_state_graph": "cluster-save-state step 11",
        "cross_cluster_ousiometric_emd": "cluster-save-state step 11",
        "location_signature_consistency": "cluster-save-state step 11",
        "macguffin_entanglement": "cluster-save-state step 11",
    }
    for key, trigger in expected.items():
        entry = registry[key]
        assert trigger in entry["trigger_by"]
        serialized = json.dumps(entry, ensure_ascii=False)
        assert "待接线" not in serialized
        assert "手动 CLI" not in serialized


def test_all_wired_codes_remain_advisory():
    for code in CLUSTER_ADVISORY_CODES | CROSS_CLUSTER_ADVISORY_CODES:
        assert code not in audit_hub.HARD_GATE_CODES
        assert audit_hub._gate_level_for(code, "error") == "advisory"
