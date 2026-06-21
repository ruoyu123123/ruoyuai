# -*- coding: utf-8 -*-
"""trope_tag_canonicalizer R23 W11 Batch-GG · P0"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import trope_tag_canonicalizer as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("TROPE_CANON_MODE", None)
    else:
        os.environ["TROPE_CANON_MODE"] = m


def _mk_project(clusters: list[dict] | None = None) -> Path:
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if clusters is not None:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return proj


def test_canon_loaded_with_30_pairs():
    canon = mod.load_canon()
    m = canon.get("canonical_map") or {}
    assert len(m) >= 30, f"canonical_map size = {len(m)}"
    assert canon.get("_placeholder") is True


def test_canonicalize_tag_basic():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("shadow")
        assert mod.canonicalize_tag("重生归来") == "重生"
        assert mod.canonicalize_tag("再活一次") == "重生"
        assert mod.canonicalize_tag("魂穿") == "穿越"
        # 未命中 → 保留 surface
        assert mod.canonicalize_tag("某新概念") == "某新概念"
    finally:
        _set_mode(bak)


def test_canonicalize_tag_off_identity():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("off")
        # off 模式 → identity（不归并）
        assert mod.canonicalize_tag("重生归来") == "重生归来"
    finally:
        _set_mode(bak)


def test_canonicalize_tags_dedup_preserve_order():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("shadow")
        out = mod.canonicalize_tags(["重生", "重生归来", "再活一次", "穿越", "杀手"])
        # 三 surface 都 canonical 化到「重生」· 去重
        assert out.count("重生") == 1
        assert "穿越" in out
        assert "杀手" in out
        assert out[0] == "重生"
    finally:
        _set_mode(bak)


def test_canonicalize_tags_empty_input():
    assert mod.canonicalize_tags(None) == []
    assert mod.canonicalize_tags([]) == []
    assert mod.canonicalize_tags(["", "  "]) == []


def test_scan_off_returns_skeleton():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("off")
        out = mod.scan_for_promotions(_mk_project([{"trope_tags": ["重生"]}]))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "canonical_distribution" not in out
    finally:
        _set_mode(bak)


def test_scan_no_tags_skipped():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("active")
        out = mod.scan_for_promotions(_mk_project([{"cluster_id": "cluster_001"}]))
        assert out.get("note") == "无 trope 标签可扫"
    finally:
        _set_mode(bak)


def test_scan_active_promotes_new_surface():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("active")
        clusters = [
            {"trope_tags": ["新型 trope X", "重生", "穿越"]},
            {"trope_tags": ["新型 trope X", "金手指"]},
            {"trope_tags": ["新型 trope X", "杀手"]},
        ]
        proj = _mk_project(clusters)
        out = mod.scan_for_promotions(proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "TROPE_NEW_SURFACE_PROMOTION_CANDIDATE" in codes
        # promotion_queue 应被写入
        q = proj / "_数据库" / ".trope_promotion_queue.json"
        assert q.exists()
        obj = json.loads(q.read_text(encoding="utf-8"))
        assert any(c["surface"] == "新型 trope X" for c in obj.get("candidates", []))
    finally:
        _set_mode(bak)


def test_scan_shadow_no_promotion_write():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("shadow")
        clusters = [
            {"trope_tags": ["新型 trope X", "重生"]},
            {"trope_tags": ["新型 trope X"]},
            {"trope_tags": ["新型 trope X"]},
        ]
        proj = _mk_project(clusters)
        out = mod.scan_for_promotions(proj)
        # shadow 不上 violation
        assert out["violations"] == []
        # 不写 queue
        q = proj / "_数据库" / ".trope_promotion_queue.json"
        assert not q.exists()
    finally:
        _set_mode(bak)


def test_canonical_distribution_aggregates_synonyms():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("shadow")
        clusters = [
            {"trope_tags": ["重生归来"]},
            {"trope_tags": ["再活一次"]},
            {"trope_tags": ["重生"]},
        ]
        out = mod.scan_for_promotions(_mk_project(clusters))
        dist = out.get("canonical_distribution", {})
        # 三个 surface 全 canonical 化到「重生」
        assert dist.get("重生") == 3
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("TROPE_NEW_SURFACE_PROMOTION_CANDIDATE", "TROPE_CANON_DICT_THIN"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("trope_tag_canonicalizer")
    assert s is not None
    assert s.get("_new") is True
