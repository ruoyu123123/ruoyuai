# -*- coding: utf-8 -*-
"""A10 实体状态时间线图矛盾聚合 回归测试 — 🔴 2026-07-07（Magnet/Atlas arXiv:2607.00918）。

钉死 cross_cluster_entity_state_graph_aggregate.py：
  · dead_reappeared 正例（archive status 通路）/ 负例（后续无 status 不判）/
    gate_waivers 闪回声明豁免 / locked_facts 死亡声明辅助锚 / 同 cluster 内死亡不误报
  · item_dual_holder 同 cluster 双持有者正例 / 跨 cluster 转手负例
  · relationship_regressed 回跳无 note 正例 / 有 note 事件支撑负例
  · 字段缺失诚实 skip（checks 留痕）/ 无 archive 整体 skip
  · advisory 恒定（绝不 hard_gate·无 warning 档）
  · env ENTITY_STATE_GRAPH_MODE：shadow（默认）exit 0 / active exit 1 / off 不跑
  · registry + flywheel(coherence 桶) 对账
"""
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cross_cluster_entity_state_graph_aggregate as esg  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

def _mk_project(tmp: Path, archives: dict = None, ec_clusters: list = None) -> Path:
    """archives: {num: archive_dict}；ec_clusters: 事件簇.json clusters 列表。"""
    db = tmp / "_数据库"
    wal = db / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    for num, data in (archives or {}).items():
        (wal / f"cluster_{num:03d}_archive.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": ec_clusters or []}, ensure_ascii=False), encoding="utf-8")
    return tmp


def _chars(*entries):
    return {"characters": list(entries), "items": [], "relationships": []}


def _finding_types(report):
    return [f["conflict_type"] for f in report["findings"]]


# ═══════════════════ ① dead_reappeared ═══════════════════

def test_dead_reappeared_positive(tmp_path):
    p = _mk_project(tmp_path, archives={
        1: _chars({"id": "C_LI", "name": "李暴躁", "status": "dead"}),
        2: _chars({"id": "C_LI", "name": "李暴躁", "status": "alive"}),
    })
    report = esg.run_scan(p)
    assert _finding_types(report) == ["dead_reappeared"]
    f = report["findings"][0]
    assert f["code"] == "ENTITY_STATE_GRAPH_CONFLICT"
    assert f["severity"] == "advisory"
    assert f["death_cluster"] == 1 and f["reappear_cluster"] == 2
    assert "cluster_001_archive" in f["death_source"]


def test_dead_reappeared_no_status_honest_skip(tmp_path):
    """后续出场条目无 status（尸体/回忆被列出）→ 诚实不判。"""
    p = _mk_project(tmp_path, archives={
        1: _chars({"id": "C_LI", "name": "李暴躁", "status": "dead"}),
        2: _chars({"id": "C_LI", "name": "李暴躁"}),  # 无 status
    })
    report = esg.run_scan(p)
    assert report["findings"] == []


def test_dead_reappeared_flashback_waiver(tmp_path):
    """再出场块 brief 有 dead_character 类 gate_waivers（flashback 别名）→ 豁免留痕不报。"""
    p = _mk_project(
        tmp_path,
        archives={
            1: _chars({"id": "C_LI", "name": "李暴躁", "status": "dead"}),
            2: _chars({"id": "C_LI", "name": "李暴躁", "status": "alive"}),
        },
        ec_clusters=[{"cluster_id": "cluster_002", "gate_waivers": [
            {"type": "flashback", "target": "李暴躁", "reason": "整块为死前闪回"}]}],
    )
    report = esg.run_scan(p)
    assert report["findings"] == []
    assert len(report["waived"]) == 1
    assert report["waived"][0]["conflict_type"] == "dead_reappeared"
    assert report["waived"][0]["waived_by"]["target"] == "李暴躁"


def test_dead_anchor_from_locked_facts(tmp_path):
    """archive 无 dead status，事件簇 locked_facts 死亡声明当辅助锚。"""
    p = _mk_project(
        tmp_path,
        archives={
            1: _chars({"id": "C_ZHOU", "name": "老周", "status": "alive"}),
            2: _chars({"id": "C_ZHOU", "name": "老周", "status": "alive"}),
        },
        ec_clusters=[{"cluster_id": "cluster_001",
                      "locked_facts": [{"fact": "老周在钟楼坠亡，已死", "subject": "C_ZHOU"}]}],
    )
    report = esg.run_scan(p)
    assert _finding_types(report) == ["dead_reappeared"]
    f = report["findings"][0]
    assert "locked_facts" in f["death_source"]
    assert f["death_cluster"] == 1 and f["reappear_cluster"] == 2


def test_same_cluster_death_not_flagged(tmp_path):
    """同 cluster 内 alive→dead（本块内战死）绝不误报：alive 先于 dead 结算。"""
    p = _mk_project(
        tmp_path,
        archives={1: _chars({"id": "C_LI", "name": "李暴躁", "status": "alive"})},
        ec_clusters=[{"cluster_id": "cluster_001",
                      "locked_facts": [{"fact": "李暴躁砸神坛后当场身亡", "subject": "C_LI"}]}],
    )
    report = esg.run_scan(p)
    assert report["findings"] == []


# ═══════════════════ ② item_dual_holder ═══════════════════

def test_item_dual_holder_positive(tmp_path):
    p = _mk_project(tmp_path, archives={1: {
        "characters": [{"id": "C_A", "name": "甲", "status": "alive"}],
        "items": [
            {"id": "I_SWORD", "name": "断水剑", "holder": "C_A"},
            {"id": "I_SWORD", "name": "断水剑", "holder": "C_B"},
        ],
        "relationships": [],
    }})
    report = esg.run_scan(p)
    assert _finding_types(report) == ["item_dual_holder"]
    f = report["findings"][0]
    assert f["cluster"] == 1
    assert sorted(f["holders"]) == ["C_A", "C_B"]


def test_item_cross_cluster_transfer_ok(tmp_path):
    """跨 cluster 转手（c1 甲持有 → c2 乙持有）是合法叙事·不报。"""
    p = _mk_project(tmp_path, archives={
        1: {"characters": [{"id": "C_A", "name": "甲", "status": "alive"}],
            "items": [{"id": "I_SWORD", "name": "断水剑", "holder": "C_A"}],
            "relationships": []},
        2: {"characters": [{"id": "C_B", "name": "乙", "status": "alive"}],
            "items": [{"id": "I_SWORD", "name": "断水剑", "holder": "C_B"}],
            "relationships": []},
    })
    report = esg.run_scan(p)
    assert report["findings"] == []


# ═══════════════════ ③ relationship_regressed ═══════════════════

def test_relationship_regressed_positive(tmp_path):
    p = _mk_project(tmp_path, archives={
        1: {"characters": [{"id": "C_A", "name": "甲", "status": "alive"}], "items": [],
            "relationships": [{"id": "R1", "from": "C_A", "to": "C_B", "type": "盟友"}]},
        2: {"characters": [{"id": "C_A", "name": "甲", "status": "alive"}], "items": [],
            "relationships": [{"id": "R1", "from": "C_A", "to": "C_B", "type": "敌对"}]},
        3: {"characters": [{"id": "C_A", "name": "甲", "status": "alive"}], "items": [],
            "relationships": [{"id": "R1", "from": "C_A", "to": "C_B", "type": "盟友"}]},
    })
    report = esg.run_scan(p)
    assert _finding_types(report) == ["relationship_regressed"]
    f = report["findings"][0]
    assert f["cluster"] == 3 and f["regressed_to_type"] == "盟友"
    assert f["intermediate_type"] == "敌对"


def test_relationship_regressed_with_note_ok(tmp_path):
    """回跳块 archive 条目带 note（事件支撑：和解）→ 合法不报。"""
    p = _mk_project(tmp_path, archives={
        1: {"characters": [], "items": [],
            "relationships": [{"from": "C_A", "to": "C_B", "type": "盟友"}]},
        2: {"characters": [], "items": [],
            "relationships": [{"from": "C_A", "to": "C_B", "type": "敌对"}]},
        3: {"characters": [], "items": [],
            "relationships": [{"from": "C_A", "to": "C_B", "type": "盟友",
                               "note": "共敌立序殿压境，两人在钟楼歃血重盟"}]},
    })
    report = esg.run_scan(p)
    assert report["findings"] == []


# ═══════════════════ 诚实 skip / 恒 advisory / mode ═══════════════════

def test_missing_fields_honest_skip(tmp_path):
    """archive 三字段全缺 → 三项检查全 skipped 留痕·零 finding·不崩。"""
    p = _mk_project(tmp_path, archives={1: {"cluster_id": "cluster_001"}})
    report = esg.run_scan(p)
    assert report["findings"] == []
    assert {c["name"]: c["status"] for c in report["checks"]} == {
        "dead_reappeared": "skipped",
        "item_dual_holder": "skipped",
        "relationship_regressed": "skipped",
    }
    assert all(c.get("skip_reason") for c in report["checks"])


def test_no_archives_whole_scan_skip(tmp_path):
    p = _mk_project(tmp_path)
    report = esg.run_scan(p)
    assert report.get("skipped") is True
    assert "archive" in report["skip_reason"]


def test_all_findings_advisory_never_hard(tmp_path):
    """advisory 恒定：三类矛盾齐发也全是 advisory·summary 无 warning 档。"""
    p = _mk_project(tmp_path, archives={
        1: {"characters": [{"id": "C_LI", "name": "李暴躁", "status": "dead"}],
            "items": [{"id": "I_X", "name": "剑", "holder": "A"},
                      {"id": "I_X", "name": "剑", "holder": "B"}],
            "relationships": [{"from": "A", "to": "B", "type": "盟友"}]},
        2: {"characters": [{"id": "C_LI", "name": "李暴躁", "status": "alive"}],
            "items": [],
            "relationships": [{"from": "A", "to": "B", "type": "敌对"}]},
        3: {"characters": [], "items": [],
            "relationships": [{"from": "A", "to": "B", "type": "盟友"}]},
    })
    report = esg.run_scan(p)
    assert len(report["findings"]) == 3
    assert all(f["severity"] == "advisory" for f in report["findings"])
    assert all(f["code"] == "ENTITY_STATE_GRAPH_CONFLICT" for f in report["findings"])
    assert "warning" not in report["summary"]


def _run_main(project: Path, monkeypatch, mode: str | None) -> int:
    if mode is None:
        monkeypatch.delenv("ENTITY_STATE_GRAPH_MODE", raising=False)
    else:
        monkeypatch.setenv("ENTITY_STATE_GRAPH_MODE", mode)
    monkeypatch.setattr(sys, "argv", ["cross_cluster_entity_state_graph_aggregate.py", str(project)])
    with pytest.raises(SystemExit) as e:
        esg.main()
    return e.value.code


def test_mode_shadow_default_exit0_and_report_written(tmp_path, monkeypatch):
    p = _mk_project(tmp_path, archives={
        1: _chars({"id": "C_LI", "name": "李暴躁", "status": "dead"}),
        2: _chars({"id": "C_LI", "name": "李暴躁", "status": "alive"}),
    })
    assert _run_main(p, monkeypatch, None) == 0  # 默认 shadow·有 finding 也 exit 0
    reports = list((p / "_数据库" / ".cross_cluster_scan").glob("entity_state_graph_*.json"))
    assert len(reports) == 1
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    assert data["scan_type"] == "entity_state_graph"
    assert data["summary"]["advisory"] == 1


def test_mode_active_exit1_off_exit0(tmp_path, monkeypatch):
    p = _mk_project(tmp_path, archives={
        1: _chars({"id": "C_LI", "name": "李暴躁", "status": "dead"}),
        2: _chars({"id": "C_LI", "name": "李暴躁", "status": "alive"}),
    })
    assert _run_main(p, monkeypatch, "active") == 1
    p2 = _mk_project(tmp_path / "clean", archives={
        1: _chars({"id": "C_A", "name": "甲", "status": "alive"})})
    assert _run_main(p2, monkeypatch, "active") == 0
    assert _run_main(p, monkeypatch, "off") == 0


# ═══════════════════ registry + flywheel 对账 ═══════════════════

def test_registry_and_flywheel_registered():
    reg = json.loads((_SCRIPTS / "scanner_registry.json").read_text(encoding="utf-8"))
    entry = reg["scanners"].get("cross_cluster_entity_state_graph")
    assert entry is not None, "scanner_registry.json 缺 cross_cluster_entity_state_graph"
    assert entry["layer"] == "cross-cluster"
    assert (_SCRIPTS / entry["script"]).exists()
    assert "ENTITY_STATE_GRAPH_CONFLICT" in entry["issues_emitted"]
    assert "advisory" in entry["doc"] and "hard_gate" in entry["doc"]

    sys.path.insert(0, str(_ROOT / "core" / "ml" / "flywheel"))
    from code_to_model_table import resolve_model_for_code
    assert resolve_model_for_code("ENTITY_STATE_GRAPH_CONFLICT") == "coherence"
