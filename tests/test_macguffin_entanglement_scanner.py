# -*- coding: utf-8 -*-
"""macguffin_entanglement_scanner 专属测试 (R9 W5 Batch-M·2026-06-20)

钉死：
  · is_macguffin=true 显式声明门控 (北极星②)
  · entanglement_ratio = |S_m^goal| / |S_m|
  · ratio < 0.4 + appearance >= 2 → MACGUFFIN_ORNAMENTAL (active)
  · 单 cluster 出现不判
  · shadow/off/active 三态
  · 永远 advisory · 不在 HARD_GATE_CODES
  · snapshot 写入 .cross_chapter_scan/
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import macguffin_entanglement_scanner as mac  # noqa: E402


def _mk_project(*, items=None, clusters=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if items is not None:
        (db / "道具.json").write_text(
            json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    if clusters is not None:
        (db / "故事块摘要.json").write_text(
            json.dumps({"schema_version": "v2.cluster", "clusters": clusters},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _set_mode(m):
    if m is None:
        os.environ.pop("MACGUFFIN_ENTANGLEMENT_MODE", None)
    else:
        os.environ["MACGUFFIN_ENTANGLEMENT_MODE"] = m


def _run(proj, mode=None, last_n=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if mode is not None:
        env["MACGUFFIN_ENTANGLEMENT_MODE"] = mode
    cmd = [sys.executable, str(_SCRIPTS / "macguffin_entanglement_scanner.py"),
           str(proj)]
    if last_n is not None:
        cmd += ["--last-n", str(last_n)]
    proc = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                          errors="replace", env=env)
    return proc


def _parse(stdout):
    start = stdout.find("{")
    end = stdout.rfind("}")
    return json.loads(stdout[start:end + 1])


def test_no_macguffin_skips():
    """所有 item.is_macguffin=false → skip 北极星②"""
    proj = _mk_project(items=[
        {"id": "i1", "name": "玉佩", "is_macguffin": False},
        {"id": "i2", "name": "长剑"},
    ], clusters=[{"cluster_id": "cluster_001", "scope_summary": "他拿出玉佩。"}])
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    assert "is_macguffin=true 声明" in rep["note"]
    assert rep["verdict"] == "PASS"


def test_off_mode_skips_calculation():
    proj = _mk_project(items=[{"id": "i1", "name": "玉佩", "is_macguffin": True}],
                       clusters=[{"cluster_id": "cluster_001",
                                  "scope_summary": "他拿玉佩为了夺回。"}])
    proc = _run(proj, mode="off")
    rep = _parse(proc.stdout)
    assert rep["mode"] == "off"


def test_too_few_clusters_skips():
    proj = _mk_project(items=[{"id": "i1", "name": "玉佩", "is_macguffin": True}],
                       clusters=[{"cluster_id": "cluster_001",
                                  "scope_summary": "他拿玉佩。"}])
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    assert "cluster 数太少" in rep.get("note", "")


def test_ornamental_macguffin_detected_active():
    """4 cluster·MacGuffin 出现 4 次但 goal pursuit 仅 1 → ratio=0.25 → 报"""
    items = [{"id": "i1", "name": "古剑", "is_macguffin": True}]
    clusters = [
        {"cluster_id": "cluster_001", "scope_summary": "他擦拭古剑。", "chapter_range": [1, 5]},
        {"cluster_id": "cluster_002", "scope_summary": "古剑挂在墙上。", "chapter_range": [6, 10]},
        {"cluster_id": "cluster_003", "scope_summary": "他展示古剑。", "chapter_range": [11, 15]},
        {"cluster_id": "cluster_004", "scope_summary": "他为了夺回古剑追查敌人。", "chapter_range": [16, 20]},
    ]
    proj = _mk_project(items=items, clusters=clusters)
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    codes = {f["code"] for f in rep["findings"]}
    assert "MACGUFFIN_ORNAMENTAL" in codes
    assert rep["verdict"] == "FAIL_MINOR"
    per = rep["per_macguffin"]["古剑"]
    assert per["entanglement_ratio"] < 0.4
    assert len(per["S_m"]) == 4
    assert len(per["S_m_goal"]) == 1


def test_engaged_macguffin_passes():
    """4 cluster·MacGuffin 出现且每次都伴 goal pursuit → ratio=1.0"""
    items = [{"id": "i1", "name": "圣杯", "is_macguffin": True}]
    clusters = [
        {"cluster_id": "cluster_001", "scope_summary": "他追查圣杯下落。", "chapter_range": [1, 5]},
        {"cluster_id": "cluster_002", "scope_summary": "为了找到圣杯他穿越雪山。", "chapter_range": [6, 10]},
        {"cluster_id": "cluster_003", "scope_summary": "他锁定圣杯持有者。", "chapter_range": [11, 15]},
        {"cluster_id": "cluster_004", "scope_summary": "他终于夺回圣杯。", "chapter_range": [16, 20]},
    ]
    proj = _mk_project(items=items, clusters=clusters)
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    codes = {f["code"] for f in rep["findings"]}
    assert "MACGUFFIN_ORNAMENTAL" not in codes
    per = rep["per_macguffin"]["圣杯"]
    assert per["entanglement_ratio"] == 1.0


def test_single_cluster_appearance_no_judge():
    """MacGuffin 只在 1 cluster 出现 → 不判"""
    items = [{"id": "i1", "name": "孤剑", "is_macguffin": True}]
    clusters = [
        {"cluster_id": "cluster_001", "scope_summary": "他擦拭孤剑。"},
        {"cluster_id": "cluster_002", "scope_summary": "他做别的事。"},
        {"cluster_id": "cluster_003", "scope_summary": "他做别的事。"},
    ]
    proj = _mk_project(items=items, clusters=clusters)
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    codes = {f["code"] for f in rep["findings"]}
    assert "MACGUFFIN_ORNAMENTAL" not in codes


def test_shadow_mode_no_findings():
    items = [{"id": "i1", "name": "古剑", "is_macguffin": True}]
    clusters = [
        {"cluster_id": "cluster_001", "scope_summary": "他擦拭古剑。", "chapter_range": [1, 5]},
        {"cluster_id": "cluster_002", "scope_summary": "古剑挂墙上。"},
        {"cluster_id": "cluster_003", "scope_summary": "古剑还在。"},
    ]
    proj = _mk_project(items=items, clusters=clusters)
    proc = _run(proj, mode="shadow")
    rep = _parse(proc.stdout)
    assert rep["mode"] == "shadow"
    assert rep["findings"] == []


def test_snapshot_written():
    items = [{"id": "i1", "name": "玉玺", "is_macguffin": True}]
    clusters = [
        {"cluster_id": "cluster_001", "scope_summary": "他追查玉玺。", "chapter_range": [1, 5]},
        {"cluster_id": "cluster_002", "scope_summary": "玉玺现身。", "chapter_range": [6, 10]},
    ]
    proj = _mk_project(items=items, clusters=clusters)
    _run(proj, mode="active")
    snap = proj / "_数据库" / ".cross_chapter_scan" / "macguffin_advisory_snapshot.json"
    assert snap.exists()
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert data["scan_type"] == "macguffin_entanglement"
    assert "per_macguffin" in data


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "MACGUFFIN_ORNAMENTAL" not in hgs


def test_code_not_in_audit_hub_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub
    assert "MACGUFFIN_ORNAMENTAL" not in audit_hub.HARD_GATE_CODES


def test_no_items_file_skips():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    assert "is_macguffin=true 声明" in rep["note"]


def test_invalid_items_format_skips():
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "道具.json").write_text("not json", encoding="utf-8")
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    assert rep["macguffin_count"] == 0


def test_macguffin_without_name_skipped():
    """name/title/id 全空·is_macguffin=True 也不入库"""
    items = [{"is_macguffin": True}]
    clusters = [{"cluster_id": "cluster_001", "scope_summary": "abc", "chapter_range": [1, 5]},
                {"cluster_id": "cluster_002", "scope_summary": "def", "chapter_range": [6, 10]}]
    proj = _mk_project(items=items, clusters=clusters)
    macs = mac._read_macguffins(proj)
    assert macs == []


def test_partial_engagement_above_floor():
    """ratio = 0.5 (≥0.4) → 不报"""
    items = [{"id": "i1", "name": "圣经", "is_macguffin": True}]
    clusters = [
        {"cluster_id": "cluster_001", "scope_summary": "他追查圣经。", "chapter_range": [1, 5]},
        {"cluster_id": "cluster_002", "scope_summary": "他为了找到圣经赶路。", "chapter_range": [6, 10]},
        {"cluster_id": "cluster_003", "scope_summary": "圣经被锁柜中。", "chapter_range": [11, 15]},
        {"cluster_id": "cluster_004", "scope_summary": "圣经又出现了。", "chapter_range": [16, 20]},
    ]
    proj = _mk_project(items=items, clusters=clusters)
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    per = rep["per_macguffin"]["圣经"]
    # ratio = 2/4 = 0.5 ≥ 0.4
    assert per["entanglement_ratio"] >= 0.4
    codes = {f["code"] for f in rep["findings"]}
    assert "MACGUFFIN_ORNAMENTAL" not in codes


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("MACGUFFIN_ENTANGLEMENT_MODE")
    try:
        os.environ["MACGUFFIN_ENTANGLEMENT_MODE"] = "garbage"
        assert mac._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_compute_entanglement_direct():
    """直接调函数·无 subprocess"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    macguffins = [{"name": "古剑", "id": "i1"}]
    clusters = [
        {"cluster_id": "cluster_001", "scope_summary": "他擦拭古剑。", "chapter_range": [1, 5]},
        {"cluster_id": "cluster_002", "scope_summary": "他追查古剑去向。"},
        {"cluster_id": "cluster_003", "scope_summary": "无关内容。"},
    ]
    per = mac.compute_entanglement(macguffins, clusters, proj)
    assert "古剑" in per
    assert per["古剑"]["appearance_clusters"] == 2
    assert per["古剑"]["entanglement_ratio"] == 0.5


def test_read_macguffins_filters_false():
    proj = _mk_project(items=[
        {"id": "i1", "name": "玉佩", "is_macguffin": True},
        {"id": "i2", "name": "长剑", "is_macguffin": False},
        {"id": "i3", "name": "战斧"},  # no key
    ])
    macs = mac._read_macguffins(proj)
    names = {m["name"] for m in macs}
    assert names == {"玉佩"}


def test_no_clusters_field_skip():
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "道具.json").write_text(
        json.dumps({"items": [{"id": "i1", "name": "古剑", "is_macguffin": True}]}),
        encoding="utf-8")
    # 不写故事块摘要.json
    proc = _run(proj, mode="active")
    rep = _parse(proc.stdout)
    assert "cluster 数太少" in rep.get("note", "") or rep.get("findings", []) == []


def test_emit_findings_threshold():
    """直接测试 emit_findings 函数"""
    per = {
        "A": {"name": "A", "entanglement_ratio": 0.3,
              "appearance_clusters": 4, "S_m": ["c1", "c2", "c3", "c4"],
              "S_m_goal": ["c1"]},
        "B": {"name": "B", "entanglement_ratio": 0.6,
              "appearance_clusters": 4, "S_m": [], "S_m_goal": []},
        "C": {"name": "C", "entanglement_ratio": 0.2,
              "appearance_clusters": 1, "S_m": ["c1"], "S_m_goal": []},
    }
    findings = mac.emit_findings(per)
    codes = {f["code"] for f in findings}
    assert "MACGUFFIN_ORNAMENTAL" in codes
    # A 算·B 高于阈值不算·C 单 cluster 不算
    assert findings[0]["count"] == 1
