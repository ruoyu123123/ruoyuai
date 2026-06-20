# -*- coding: utf-8 -*-
"""cast_economy_scanner 专属测试 — Truby 配角经济(advisory · 2026-06-20)

钉死：
  · 新引入 > budget → CAST_INTRODUCE_BURST
  · 同 actant 位 ≥2 → CAST_COMPOSITE_HINT
  · 历史 helper→当前 opponent 且无 pivot → CAST_ROLE_SPLIT_IMPLICIT
  · 群像题材 budget=4 自动放宽
  · 永远 advisory · 绝不 hard_gate
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cast_economy_scanner as ce  # noqa: E402


def _write_draft():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write("占位")
    f.close()
    return Path(f.name)


def _mk_manifest(**kwargs):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False)
    f.write(json.dumps(kwargs, ensure_ascii=False))
    f.close()
    return Path(f.name)


def _mk_project(*, ledger=None, characters=None, genre=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        (proj / "_数据库" / "cluster_actant_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    if characters is not None:
        (proj / "_数据库" / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")
    if genre is not None:
        (proj / "_数据库" / "用户偏好.json").write_text(
            json.dumps({"genre": genre}, ensure_ascii=False), encoding="utf-8")
    return proj


def _set_mode(m):
    if m is None:
        os.environ.pop("CAST_ECONOMY_MODE", None)
    else:
        os.environ["CAST_ECONOMY_MODE"] = m


def test_off_returns_skeleton():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("off")
        rep = ce.scan(str(_write_draft()))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_empty_cast_skips():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(cluster_id="cluster_001")
        rep = ce.scan(str(_write_draft()), manifest_path=str(mf))
        assert "无 active_cast" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_introduce_burst_default_budget():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        mf = _mk_manifest(cluster_id="cluster_002",
                          active_cast=["A", "B", "C", "D"])
        rep = ce.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "CAST_INTRODUCE_BURST" in codes
        assert rep["intro_budget"] == 2
        assert rep["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_ensemble_genre_widens_budget():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="scheming_politics")
        mf = _mk_manifest(cluster_id="cluster_001",
                          active_cast=["A", "B", "C", "D"])
        rep = ce.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "CAST_INTRODUCE_BURST" not in codes
        assert rep["intro_budget"] == 4
    finally:
        _set_mode(bak)


def test_known_cast_not_counted_as_new():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "A"}, {"name": "B"},
                                         {"name": "C"}])
        mf = _mk_manifest(cluster_id="cluster_002",
                          active_cast=["A", "B", "C", "D"])
        rep = ce.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        # 只新引入 D → 1 不超 budget
        assert "CAST_INTRODUCE_BURST" not in [v["code"] for v in rep["violations"]]
        assert rep["newly_introduced"] == ["D"]
    finally:
        _set_mode(bak)


def test_composite_hint_helper_list():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(cluster_id="cluster_001",
                          active_cast=["甲", "乙"],
                          cluster_actant_state={"helper": ["甲", "乙"]})
        rep = ce.scan(str(_write_draft()), manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "CAST_COMPOSITE_HINT" in codes
    finally:
        _set_mode(bak)


def test_role_split_implicit_advisory():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(ledger={"clusters": [{
            "cluster_id": "cluster_001",
            "assignments": {"helper": "张三"}
        }]})
        mf = _mk_manifest(cluster_id="cluster_002",
                          active_cast=["张三"],
                          cluster_actant_state={"opponent": "张三"})
        rep = ce.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "CAST_ROLE_SPLIT_IMPLICIT" in codes
    finally:
        _set_mode(bak)


def test_role_split_with_pivot_exempt():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(ledger={"clusters": [{
            "cluster_id": "cluster_001",
            "assignments": {"helper": "张三"}
        }]})
        mf = _mk_manifest(cluster_id="cluster_002",
                          active_cast=["张三"],
                          cluster_actant_state={"opponent": "张三"},
                          pivot_events=["张三"])
        rep = ce.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "CAST_ROLE_SPLIT_IMPLICIT" not in codes
    finally:
        _set_mode(bak)


def test_shadow_records_no_report():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project()
        mf = _mk_manifest(cluster_id="cluster_001",
                          active_cast=["A", "B", "C", "D", "E"])
        rep = ce.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_known_cast_dict_characters():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters={"a": {"name": "甲"},
                                         "b": {"name": "乙"}})
        mf = _mk_manifest(cluster_id="cluster_002",
                          active_cast=["甲", "乙"])
        rep = ce.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        assert rep["newly_introduced"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("CAST_INTRODUCE_BURST", "CAST_COMPOSITE_HINT",
              "CAST_ROLE_SPLIT_IMPLICIT"):
        assert c not in hgs, c


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        os.environ["CAST_ECONOMY_MODE"] = "bogus"
        assert ce._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_no_project_no_crash():
    bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(cluster_id="cluster_001",
                          active_cast=["A", "B", "C"])
        rep = ce.scan(str(_write_draft()), manifest_path=str(mf))
        # 无 project · 全是新引入·3>2 触发 burst
        assert "CAST_INTRODUCE_BURST" in [v["code"] for v in rep["violations"]]
    finally:
        _set_mode(bak)
