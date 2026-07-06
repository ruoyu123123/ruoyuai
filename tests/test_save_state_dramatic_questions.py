# -*- coding: utf-8 -*-
"""save_state 戏剧问题账本回库测试 — 🔴 2026-06-29 PITQ/MDQ 读者粘性。

钉死 save_state.cmd_apply_dramatic_questions：novel-foreshadower 读整 cluster 正文登记本块戏剧问题
（伏笔⊂PITQ 特例·account 同构）→ JudgeReport.specific_findings.dramatic_questions={raised,answered}
→ 确定性 append 进 戏剧问题账本.json.clusters[<cid>]（读者粘性唯一宏观结构缺口·SOTA=Cambridge2026
PITQ + McKee MDQ + Loewenstein 信息缺口 + Zeigarnik）：
  · 只填 active cluster（JudgeReport 已是本 cluster 范围·account 归本 cid）
  · 幂等·去重：raised 按 qid·answered 按 qid（qid 全局唯一·标对应 qid 闭合）
  · raised_at_scene/answered_at_scene 归一为 int·scope 钳到 cluster|volume|series
  · 硬失败：JudgeReport 缺失 / 无 dramatic_questions 字段 → return 2
  · raised+answered 全空表示 producer 明确无戏剧问题变更，幂等 return 0
  · 账本缺/坏 → 从空骨架重建（辅助态文件·我方拥有·world_seed_init 已播种）
  · required STATE：producer 缺产物不允许静默吞掉
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state as ss  # noqa: E402
import cluster_lookup  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

def _mk_project(tmp: Path, *, dq=None, ledger=None, key="001",
                report_present=True) -> Path:
    db = tmp / "_数据库"
    (db / ".judge_reports").mkdir(parents=True, exist_ok=True)
    # 事件簇供 cluster 反查（cmd 内只用 normalize_cluster_id·非必需）
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}]},
        ensure_ascii=False), encoding="utf-8")
    if report_present:
        cid = cluster_lookup.normalize_cluster_id(key)
        sf = {"payoff_scores": [], "chekhov_candidates": [], "health_warnings": []}
        if dq is not None:
            sf["dramatic_questions"] = dq
        (db / ".judge_reports" / f"{cid}_foreshadower.json").write_text(
            json.dumps({"judge_id": "foreshadower", "cluster_id": cid,
                        "specific_findings": sf}, ensure_ascii=False), encoding="utf-8")
    if ledger is not None:
        (db / "戏剧问题账本.json").write_text(json.dumps(ledger, ensure_ascii=False),
                                          encoding="utf-8")
    return tmp


def _ledger(db_root):
    p = Path(db_root) / "_数据库" / "戏剧问题账本.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


_SKELETON_LEDGER = {"schema_version": 1, "clusters": {}}


def _raised(qid, scope="cluster", scene=1, q=None, win="1-2 cluster", gap_type=None):
    d = {"qid": qid, "question": q or f"主角能否查清{qid}的真相",
         "scope": scope, "raised_at_scene": scene, "expected_payoff_window": win}
    if gap_type is not None:
        d["gap_type"] = gap_type
    return d


def _answered(qid, scene=2):
    return {"qid": qid, "answered_at_scene": scene}


# ═══════════════════════ 回库 ═══════════════════════

def test_append_raised_and_answered():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_祭台真相", "volume", 1)],
                               "answered": [_answered("DQ_信件寄主", 4)]})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        led = _ledger(root)
        ent = led["clusters"]["cluster_001"]
        assert len(ent["raised"]) == 1 and len(ent["answered"]) == 1
        r = ent["raised"][0]
        assert r["qid"] == "DQ_祭台真相" and r["scope"] == "volume"
        assert r["raised_at_scene"] == 1 and r["expected_payoff_window"] == "1-2 cluster"
        assert ent["answered"][0]["qid"] == "DQ_信件寄主"
        assert ent["answered"][0]["answered_at_scene"] == 4


def test_idempotent_by_qid():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_A")], "answered": [_answered("DQ_B")]})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0  # re-apply
        ent = _ledger(root)["clusters"]["cluster_001"]
        assert len(ent["raised"]) == 1, "re-apply 同 qid 不重复 append"
        assert len(ent["answered"]) == 1


def test_dup_qid_within_one_report_deduped():
    """同一报告内重复 qid → 只留一条（qid 全局唯一）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_A"), _raised("DQ_A", "volume")],
                               "answered": []})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        ent = _ledger(root)["clusters"]["cluster_001"]
        assert len(ent["raised"]) == 1
        assert ent["raised"][0]["scope"] == "cluster"  # 首条胜出


def test_scope_normalized_to_cluster_when_invalid():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_A", scope="garbage")], "answered": []})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        assert _ledger(root)["clusters"]["cluster_001"]["raised"][0]["scope"] == "cluster"


def test_scene_idx_string_normalized_to_int():
    with tempfile.TemporaryDirectory() as d:
        r = _raised("DQ_A"); r["raised_at_scene"] = "3"
        a = _answered("DQ_B"); a["answered_at_scene"] = "5"
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [r], "answered": [a]})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        ent = _ledger(root)["clusters"]["cluster_001"]
        assert ent["raised"][0]["raised_at_scene"] == 3
        assert isinstance(ent["raised"][0]["raised_at_scene"], int)
        assert ent["answered"][0]["answered_at_scene"] == 5


def test_missing_qid_skipped():
    with tempfile.TemporaryDirectory() as d:
        bad = _raised("DQ_ok"); bad2 = {"question": "无 qid", "scope": "cluster"}
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [bad2, bad], "answered": [{"answered_at_scene": 2}]})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        ent = _ledger(root)["clusters"]["cluster_001"]
        assert len(ent["raised"]) == 1 and ent["raised"][0]["qid"] == "DQ_ok"
        assert len(ent["answered"]) == 0  # 无 qid 的 answered 跳过


def test_appends_to_existing_cluster_entry_no_overwrite():
    """已有该 cluster 条目（含历史问题）→ append 不覆盖。"""
    with tempfile.TemporaryDirectory() as d:
        ledger = {"schema_version": 1, "clusters": {
            "cluster_001": {"raised": [_raised("DQ_OLD")], "answered": []}}}
        root = _mk_project(Path(d), ledger=ledger,
                           dq={"raised": [_raised("DQ_NEW")], "answered": []})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        ent = _ledger(root)["clusters"]["cluster_001"]
        assert {r["qid"] for r in ent["raised"]} == {"DQ_OLD", "DQ_NEW"}


def test_answered_cross_cluster_qid_registered():
    """answered 指向先前 cluster 提出的 qid（跨 cluster 闭合）→ 照样登记到本块 answered。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [], "answered": [_answered("DQ_FROM_C001", 3)]})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        ent = _ledger(root)["clusters"]["cluster_001"]
        assert ent["answered"][0]["qid"] == "DQ_FROM_C001"


# ═══════════════════════ 账本缺/坏：自愈重建 ═══════════════════════

def test_missing_ledger_rebuilt_from_skeleton():
    """戏剧问题账本.json 缺（旧书未播种）但有 dramatic_questions → 从空骨架重建并写入。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=None,
                           dq={"raised": [_raised("DQ_A")], "answered": []})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        led = _ledger(root)
        assert led is not None and led["schema_version"] == 1
        assert led["clusters"]["cluster_001"]["raised"][0]["qid"] == "DQ_A"


def test_broken_ledger_rebuilt():
    """戏剧问题账本.json 损坏 → load_json 返 None → 从空骨架重建（不阻断）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_A")], "answered": []})
        (Path(root) / "_数据库" / "戏剧问题账本.json").write_text("{ broken", encoding="utf-8")
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        assert _ledger(root)["clusters"]["cluster_001"]["raised"][0]["qid"] == "DQ_A"


# ═══════════════════════ 默认安全 / 向后兼容 ═══════════════════════

def test_no_dramatic_questions_in_report_hard_fails():
    """JudgeReport 无 dramatic_questions → required 字段缺失，return 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER), dq=None)
        assert ss.cmd_apply_dramatic_questions(root, "001") == 2
        assert _ledger(root)["clusters"] == {}


def test_empty_raised_and_answered_writes_structured_empty_result():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [], "answered": []})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        assert _ledger(root)["clusters"] == {"cluster_001": {"raised": [], "answered": []}}


def test_missing_report_hard_fails():
    """foreshadower JudgeReport 不存在（未跑）→ return 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER), report_present=False)
        assert ss.cmd_apply_dramatic_questions(root, "001") == 2
        assert _ledger(root)["clusters"] == {}


def test_cluster_prefixed_key_accepted():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_A")], "answered": []}, key="001")
        assert ss.cmd_apply_dramatic_questions(root, "cluster_001") == 0
        assert len(_ledger(root)["clusters"]["cluster_001"]["raised"]) == 1


# ═══════════════════════ 🔴 Sternberg 读者知识缺口三态 gap_type 回库 ═══════════════════════

def test_gap_type_persisted_when_valid():
    """合法 gap_type（suspense/curiosity/surprise）随 raised 回库（白名单字段·须显式带）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_A", gap_type="curiosity")], "answered": []})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        r = _ledger(root)["clusters"]["cluster_001"]["raised"][0]
        assert r["gap_type"] == "curiosity"


def test_gap_type_invalid_normalized_to_none():
    """非法 gap_type → None（默认安全·不报错）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_A", gap_type="garbage")], "answered": []})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        r = _ledger(root)["clusters"]["cluster_001"]["raised"][0]
        assert r["gap_type"] is None


def test_gap_type_missing_defaults_to_none():
    """旧账本/慢热单一缺口 raised 缺 gap_type → None（向后兼容·字段存在便于下游统一读）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), ledger=dict(_SKELETON_LEDGER),
                           dq={"raised": [_raised("DQ_A")], "answered": []})
        assert ss.cmd_apply_dramatic_questions(root, "001") == 0
        r = _ledger(root)["clusters"]["cluster_001"]["raised"][0]
        assert r["gap_type"] is None


def test_skeleton_schema_documents_gap_type():
    """subsystem_skeletons.json 的 ledger schema 含 gap_type 三态语义（单一真理源）。"""
    sk = json.loads((_ROOT / "core" / "claude-home" / "templates"
                     / "subsystem_skeletons.json").read_text(encoding="utf-8"))
    fs = sk["_dramatic_question_ledger_schema"]["_field_semantics"]
    key = "clusters.<cluster_id>.raised[].gap_type"
    assert key in fs
    for t in ("suspense", "curiosity", "surprise"):
        assert t in fs[key]


# ═══════════════════════ 单一真理源 + 播种 + 白名单 ═══════════════════════

def test_skeleton_schema_present():
    """subsystem_skeletons.json 必含 _dramatic_question_ledger_schema._skeleton（单一真理源）。"""
    sk = json.loads((_ROOT / "core" / "claude-home" / "templates"
                     / "subsystem_skeletons.json").read_text(encoding="utf-8"))
    assert "_dramatic_question_ledger_schema" in sk
    assert sk["_dramatic_question_ledger_schema"]["_skeleton"] == {
        "schema_version": 1, "clusters": {}}
    # 非 34 核心·不进 canonical
    assert "戏剧问题账本" not in sk["_canonical_34"]


def test_world_seed_init_seeds_empty_ledger():
    """world_seed_init 幂等播种空账本（辅助态文件·非 34 核心）。"""
    import world_seed_init as wsi
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        db.mkdir(parents=True)
        # 最小输入：空 ME/arc/ensemble（播种只关心 ledger 这步）
        for fn, payload in (("大势卡.json", {"major_events": []}),
                            ("character_arc_state.json", {"characters": {}}),
                            ("群像档.json", {"characters": {}})):
            (db / fn).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        r = wsi.seed(Path(d), explicit_factions=[], force=False,
                     reset_ticks=False, dry_run=False)
        assert r["dramatic_question_ledger_seeded"] is True
        led = json.loads((db / "戏剧问题账本.json").read_text(encoding="utf-8"))
        assert led == {"schema_version": 1, "clusters": {}}
        # 幂等：二次播种 seeded=False·不覆盖
        r2 = wsi.seed(Path(d), explicit_factions=[], force=False,
                      reset_ticks=False, dry_run=False)
        assert r2["dramatic_question_ledger_seeded"] is False


def test_scaffold_known_extras_whitelists_ledger():
    """scaffold KNOWN_EXTRAS 白名单含 戏剧问题账本.json（--strict-extras 不误报）。"""
    import scaffold_subsystems as scaf
    assert "戏剧问题账本.json" in scaf.KNOWN_EXTRAS


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
