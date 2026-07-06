# -*- coding: utf-8 -*-
"""scene_receipts_scanner · 场景回执（storyboard → 草稿覆盖证据）· 2026-07-06 P1 移植

借鉴 LongWriter/AgentWrite「plan-then-write 回执」+ moyin-creator「场景校准」
（research/open_source_writing_systems.md）。确定性零 LLM · advisory · 绝不 hard_gate ·
env SCENE_RECEIPTS_MODE 默认 shadow。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT / "core" / "ml" / "flywheel"))

import scene_receipts_scanner as mod  # noqa: E402
import audit_hub  # noqa: E402
from code_to_model_table import resolve_model_for_code  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("SCENE_RECEIPTS_MODE", None)
    else:
        os.environ["SCENE_RECEIPTS_MODE"] = m


_SCENE_A = {
    "scene_index": 0,
    "scene_goal": "陈默要在钟楼档案室找到那本铜皮账册",
    "characters": ["陈默", "老周"],
    "location": "钟楼档案室",
}
_SCENE_B = {
    "scene_index": 1,
    "scene_goal": "苏晚萤在废弃医院焚烧祭坛召唤影子仆从",
    "characters": ["苏晚萤"],
    "location": "废弃医院",
}

_PARA_A = (
    "陈默推开钟楼档案室的木门，霉味扑面而来。\n\n"
    "他翻了半个时辰，指尖终于碰到那本铜皮账册的冷边。\n\n"
    "老周守在楼梯口，压着嗓子催他快点。"
)
_PARA_B = (
    "苏晚萤把最后一捆干柴堆上废弃医院的祭坛。\n\n"
    "火光起来的时候，影子仆从从墙缝里渗出来，朝她跪下。"
)
_PARA_FILLER = (
    "巷子里的雨下了整夜。\n\n"
    "更夫敲过三遍梆子，没人应声。"
)


def _mk_project(scenes, cluster_id="cluster_001"):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True)
    obj = {
        "schema_version": "v23.0",
        "clusters": [{"cluster_id": cluster_id, "scene_storyboard": scenes}],
    }
    (db / "事件簇.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def _write_draft(text, proj=None, key="001"):
    if proj is None:
        d = Path(tempfile.mkdtemp())
    else:
        d = proj / "章节" / f"cluster_{key}_draft"
        d.mkdir(parents=True, exist_ok=True)
    p = d / f"cluster_{key}_draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _run(mode, scenes, draft_text, cluster="cluster_001"):
    bak = os.environ.get("SCENE_RECEIPTS_MODE")
    try:
        _set_mode(mode)
        proj = _mk_project(scenes)
        draft = _write_draft(draft_text, proj)
        return mod.scan(draft, proj, cluster), proj
    finally:
        _set_mode(bak)


def test_off_returns_skeleton():
    bak = os.environ.get("SCENE_RECEIPTS_MODE")
    try:
        _set_mode("off")
        out = mod.scan("nonexistent.txt", None, None)
        assert out["mode"] == "off"
        assert out["violations"] == []
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_full_coverage_pass_active():
    """两场景全覆盖 → coverage=1.0 · PASS · 零 violation。"""
    out, _ = _run("active", [_SCENE_A, _SCENE_B],
                  _PARA_A + "\n\n" + _PARA_B)
    assert out["coverage_ratio"] == 1.0
    assert out["violations"] == []
    assert out["verdict"] == "PASS"
    assert out["warning"] is None
    assert all(r["matched"] for r in out["receipts"])
    assert all(r["match_pos"] is not None for r in out["receipts"])


def test_gap_detected_active():
    """检出：草稿刻意漏写场景 B → coverage 0.5 < 0.6 → advisory violation。"""
    out, _ = _run("active", [_SCENE_A, _SCENE_B],
                  _PARA_A + "\n\n" + _PARA_FILLER)
    assert out["coverage_ratio"] == 0.5
    assert out["verdict"] == "FAIL_MINOR"
    assert out["warning"]
    assert len(out["violations"]) == 1
    v = out["violations"][0]
    assert v["code"] == "SCENE_RECEIPT_COVERAGE_GAP"
    assert v["severity"] == "minor"
    assert 1 in v["missing_scene_indices"]
    # 顶层 gate_level 恒 advisory（北极星⑤）
    assert out["gate_level"] == "advisory"


def test_gap_shadow_no_violations(capsys):
    """shadow（默认）：同样的缺口只 stderr 打印 · violations 留空 · exit 语义 0。"""
    out, _ = _run("shadow", [_SCENE_A, _SCENE_B],
                  _PARA_A + "\n\n" + _PARA_FILLER)
    assert out["coverage_ratio"] == 0.5  # 回执照常计算
    assert out["violations"] == []
    assert out["warning"] is None
    assert out["verdict"] == "PASS"
    err = capsys.readouterr().err
    assert "[SHADOW] scene_receipts" in err


def test_default_mode_is_shadow():
    bak = os.environ.get("SCENE_RECEIPTS_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_empty_storyboard_graceful_skip():
    """cluster_002+ 骨架空 storyboard = 合法 fluid → 优雅 skip 不硬失败。"""
    out, _ = _run("active", [], _PARA_A)
    assert out["violations"] == []
    assert out["warning"] is None
    assert "skipped" in out.get("note", "")


def test_no_project_graceful_skip():
    bak = os.environ.get("SCENE_RECEIPTS_MODE")
    try:
        _set_mode("active")
        draft = _write_draft(_PARA_A)
        out = mod.scan(draft, None, "cluster_001")
        assert out["violations"] == []
        assert "skipped" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_single_scene_never_reports():
    """保守阈值：场景数 < 2 即使未覆盖也不报（freestyle 改编常态防误报）。"""
    out, _ = _run("active", [_SCENE_B], _PARA_A + "\n\n" + _PARA_FILLER)
    assert out["coverage_ratio"] == 0.0
    assert out["violations"] == []
    assert out["verdict"] == "PASS"


def test_cluster_inferred_from_draft_path():
    """--cluster 缺省时从 draft 路径 cluster_<key>_draft 推 key（cluster_lookup 归一）。"""
    out, _ = _run("active", [_SCENE_A, _SCENE_B],
                  _PARA_A + "\n\n" + _PARA_B, cluster=None)
    assert out["cluster_id"] == "cluster_001"
    assert out["coverage_ratio"] == 1.0


def test_artifact_written_to_audit_dir():
    out, proj = _run("shadow", [_SCENE_A, _SCENE_B],
                     _PARA_A + "\n\n" + _PARA_B)
    art = proj / "_数据库" / ".audit" / "cluster_001_scene_receipts.json"
    assert art.exists()
    obj = json.loads(art.read_text(encoding="utf-8"))
    assert obj["code"] == "SCENE_RECEIPT_COVERAGE_GAP"
    assert obj["coverage_ratio"] == out["coverage_ratio"]
    assert len(obj["receipts"]) == 2


def test_anchorless_scene_not_counted():
    """抽不出锚点的场景不进覆盖分母（matched=None·防误伤）。"""
    blank_scene = {"scene_index": 2}
    out, _ = _run("active", [_SCENE_A, _SCENE_B, blank_scene],
                  _PARA_A + "\n\n" + _PARA_B)
    assert out["evaluable_count"] == 2
    assert out["coverage_ratio"] == 1.0
    assert out["receipts"][2]["matched"] is None


def test_audit_hub_integrates_scanner():
    """audit_hub cluster-mode 接线源码断言（同 test_agenda_drift 模式）。"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "scene_receipts_scanner" in src
    assert "SCENE_RECEIPT_COVERAGE_GAP" in src


def test_code_never_hard_gate():
    """北极星⑤：SCENE_RECEIPT_COVERAGE_GAP 绝不进 HARD_GATE_CODES。"""
    assert "SCENE_RECEIPT_COVERAGE_GAP" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("SCENE_RECEIPT_COVERAGE_GAP", "error") == "advisory"


def test_registry_entry_reconciled():
    """scanner_registry.json 对账：entry 存在 · layer=cluster · 脚本真实存在 · code 登记。"""
    reg = json.loads((_SCRIPTS / "scanner_registry.json").read_text(encoding="utf-8"))
    entry = reg["scanners"]["scene_receipts_scanner"]
    assert entry["layer"] == "cluster"
    assert entry["script"] == "scene_receipts_scanner.py"
    assert (_SCRIPTS / "scene_receipts_scanner.py").exists()
    assert "SCENE_RECEIPT_COVERAGE_GAP" in entry["issues_emitted"]


def test_code_registered_in_flywheel_coherence_bucket():
    """数据飞轮 CODE_TO_MODEL：文本-storyboard 对齐归 coherence 桶。"""
    assert resolve_model_for_code("SCENE_RECEIPT_COVERAGE_GAP") == "coherence"
