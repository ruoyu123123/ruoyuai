# -*- coding: utf-8 -*-
"""thematic_argument_motif_balance 专属回归(2026-06-20·R8 W4 Batch-J·L34)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import thematic_argument_motif_balance as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("THEMATIC_ARGUMENT_BALANCE_MODE", None)
    else:
        os.environ["THEMATIC_ARGUMENT_BALANCE_MODE"] = m


def _mk_project(pairs=None, cluster_texts=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if pairs is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"thematic_argument_pairs": pairs},
                       ensure_ascii=False), encoding="utf-8")
    if cluster_texts is not None:
        clusters = [{"cluster_id": cid, "status": "done"}
                    for cid, _ in cluster_texts]
        (proj / "_数据库" / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False),
            encoding="utf-8")
        (proj / "章节").mkdir(parents=True, exist_ok=True)
        for cid, text in cluster_texts:
            (proj / "章节" / f"{cid}_draft.txt").write_text(text,
                                                              encoding="utf-8")
    return proj


def test_off_returns_skeleton():
    bak = os.environ.get("THEMATIC_ARGUMENT_BALANCE_MODE")
    try:
        _set_mode("off")
        out = mod.aggregate(None)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_pairs_skips():
    bak = os.environ.get("THEMATIC_ARGUMENT_BALANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.aggregate(proj)
        assert "无 thematic_argument_pairs" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_balanced_pairs_pass():
    bak = os.environ.get("THEMATIC_ARGUMENT_BALANCE_MODE")
    try:
        _set_mode("active")
        pairs = [{"position_A": "光", "symbols_A": ["光", "火"],
                  "position_B": "暗", "symbols_B": ["暗", "影"]}]
        texts = [
            ("c1", "光与暗交错。火焰下藏着影。"),
            ("c2", "光照大地，影从墙后伸出。"),
            ("c3", "火光熄灭，黑暗降临。"),
        ]
        proj = _mk_project(pairs=pairs, cluster_texts=texts)
        out = mod.aggregate(proj)
        assert out["verdict"] == "PASS"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_imbalanced_pairs_active_fail():
    bak = os.environ.get("THEMATIC_ARGUMENT_BALANCE_MODE")
    try:
        _set_mode("active")
        pairs = [{"position_A": "光", "symbols_A": ["光", "火"],
                  "position_B": "暗", "symbols_B": ["暗", "影"]}]
        texts = [
            ("c1", "光光光光光火火火火。"),
            ("c2", "光火光火光火光火。"),
            ("c3", "光光光火火火光光光。"),
        ]
        proj = _mk_project(pairs=pairs, cluster_texts=texts)
        out = mod.aggregate(proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("THEMATIC_ARGUMENT_BALANCE_MODE")
    try:
        _set_mode("shadow")
        pairs = [{"position_A": "光", "symbols_A": ["光"],
                  "position_B": "暗", "symbols_B": ["暗"]}]
        texts = [("c1", "光光光光。"), ("c2", "光光光。"), ("c3", "光光光。")]
        proj = _mk_project(pairs=pairs, cluster_texts=texts)
        out = mod.aggregate(proj)
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_too_few_clusters_skipped():
    bak = os.environ.get("THEMATIC_ARGUMENT_BALANCE_MODE")
    try:
        _set_mode("active")
        pairs = [{"position_A": "x", "symbols_A": ["x"],
                  "position_B": "y", "symbols_B": ["y"]}]
        proj = _mk_project(pairs=pairs, cluster_texts=[("c1", "x")])
        out = mod.aggregate(proj)
        assert "cluster 文本数过少" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_resolve_pairs():
    pairs = [{"position_A": "x", "symbols_A": ["x"],
              "position_B": "y", "symbols_B": ["y"]}]
    proj = _mk_project(pairs=pairs)
    assert mod._resolve_pairs(proj) == pairs


def test_resolve_pairs_skips_malformed():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir()
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"thematic_argument_pairs": [{"only_a": "x"}]},
                   ensure_ascii=False), encoding="utf-8")
    assert mod._resolve_pairs(proj) == []


def test_count_hits_basic():
    assert mod._count_hits("光光暗光", ["光", "暗"]) == 4


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("THEMATIC_ARGUMENT_BALANCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
