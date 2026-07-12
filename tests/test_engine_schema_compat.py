"""build_manifest 消费三引擎 cluster-native schema 的收集函数集成回归测试。

覆盖 DatabaseScanner._collect_active_clocks / _collect_protagonist_stress /
_collect_storyteller_directive：验证给定合法的单一 cluster-native 数据库文件时，
三个 manifest 收集函数返回 mode="on" 且字段值正确，而不是因为跨模块 schema 不匹配
静默退化成 mode="error"。clock_engine / stress_evaluator / narrator_calibrate 各自
的 schema 校验、事件推进与 CLI 契约分别由 test_clock_engine.py / test_stress_evaluator.py /
test_narrator_calibrate.py 覆盖，此处不重复。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import build_manifest as bm  # noqa: E402


def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write_event_cluster_range(db: Path, cluster_id: str, chapter_range: list[int]) -> None:
    (db / "事件簇.json").write_text(json.dumps({
        "clusters": [{"cluster_id": cluster_id, "chapter_range": chapter_range}],
    }, ensure_ascii=False), encoding="utf-8")


_CLOCKS_CANONICAL = {
    "_schema": "cluster_clocks",
    "schema_version": "1.0",
    "clocks": [
        {
            "clock_id": "CK_001", "label": "老郑诅咒累计", "category": "antagonist_pressure",
            "ticks": 12, "max": 13, "tick_on": ["minor_event:老郑在场*"],
            "tick_per_event": 1, "trigger_on_max": "ME-V2-01",
            "visible_to_protagonist": False, "visible_to_writer": True,
            "is_surprise": False, "since_cluster": "cluster_001",
            "spawned_by": "outline", "status": "active",
        },
        {
            "clock_id": "CK_002", "label": "第 18 张脸", "category": "world_pressure",
            "ticks": 0, "max": 18, "tick_on": ["cluster_end"],
            "tick_per_event": 1, "trigger_on_max": "ME-V1-01",
            "visible_to_protagonist": False, "visible_to_writer": True,
            "is_surprise": False, "since_cluster": "cluster_001",
            "spawned_by": "outline", "status": "active",
        },
    ],
}

_STRESS_CANONICAL = {
    "_schema": "cluster_protagonist_stress",
    "schema_version": "1.0",
    "protagonist": "陆建国",
    "stress_level": 9,
    "stress_max": 10,
    "stress_threshold_break": 10,
    "stress_log": [],
    "persona_violations_tracked": {"core_traits": [
        {"trait": "谨慎", "violation_keywords": ["暴露"], "align_keywords": ["隐藏"],
         "stress_per_violation": 2},
    ]},
    "mental_break_pool": [],
    "coping_mechanisms": {"high_stress_behaviors": ["独自查案不通知同事"]},
}

_PACER_CANONICAL = {
    "_schema": "cluster_storyteller",
    "schema_version": "1.0",
    "rhythm_profile": "混合",
    "beat_targets": [
        {"cluster_id": "cluster_001", "target": "开篇强冲突倒叙"},
        {"cluster_id": "cluster_002", "target": "第一次转折"},
    ],
    "default_beat_policy": {"mode": "fluid"},
    "appraisal_beats": [],
    "storyteller_profile": "cassandra",
    "current_pressure_phase": "rising",
    "since_phase_change_cluster": "cluster_001",
    "cluster_outcome_log": [],
    "adaptation_factor": {
        "recent_n_clusters": 5, "expected_setback_per_n_clusters": 1, "tolerance_window": 0,
        "current_setback_count_in_window": 0, "current_win_streak": 0, "current_loss_streak": 0,
    },
    "narrator_recommendation": {
        "next_cluster_target_outcome": "auto", "next_cluster_intensity_target": "auto",
    },
}


def test_manifest_active_clocks_surfaces_canonical_schema():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "时钟表.json").write_text(
            json.dumps(_CLOCKS_CANONICAL, ensure_ascii=False), encoding="utf-8"
        )
        _write_event_cluster_range(db, "cluster_001", [1, 5])
        s = bm.DatabaseScanner(root, 1)
        r = bm._collect_active_clocks(s, 1)
        assert r["mode"] == "on"
        assert r["cluster_id"] == "cluster_001"
        assert r["total_active"] == 2
        assert r["urgent_count"] >= 1
        by_id = {c["clock_id"]: c for c in r["active_clocks"]}
        assert by_id["CK_001"]["urgency"] == "urgent"
        assert by_id["CK_001"]["remaining"] == 1


def test_manifest_protagonist_stress_surfaces_high_stress_and_coping():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(
            json.dumps(_STRESS_CANONICAL, ensure_ascii=False), encoding="utf-8"
        )
        s = bm.DatabaseScanner(root, 3)
        r = bm._collect_protagonist_stress(s, 3)
        assert r["mode"] == "on"
        assert r["schema_mode"] == "cluster"
        assert r["protagonist"] == "陆建国"
        assert r["stress_level"] == 9
        assert r["is_high_stress"] is True
        assert r["coping_behaviors"] == ["独自查案不通知同事"]
        assert r["persona_violations_to_avoid"] == [{"trait": "谨慎", "violation_kw": ["暴露"]}]


def test_manifest_storyteller_directive_surfaces_canonical_schema():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "叙事节拍器.json").write_text(
            json.dumps(_PACER_CANONICAL, ensure_ascii=False), encoding="utf-8"
        )
        _write_event_cluster_range(db, "cluster_001", [1, 5])
        s = bm.DatabaseScanner(root, 2)
        r = bm._collect_storyteller_directive(s, 2)
        assert r["mode"] == "on"
        assert r["profile"] == "cassandra"
        assert r["current_phase"] == "rising"
        assert r["rhythm_profile"] == "混合"
        ids = [b["cluster_id"] for b in (r["current_cluster_beats"] or [])]
        assert ids == ["cluster_001"]
