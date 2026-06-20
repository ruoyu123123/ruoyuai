# -*- coding: utf-8 -*-
"""bremond_cadence_scanner 专属测试 — 三段式+blockage 节奏(advisory · 2026-06-20)

钉死：
  · 末窗同型 streak ≥3 → MONOTONE
  · over_success ≥80% → OVER_SUCCESS
  · over_failure ≥60% → OVER_FAILURE
  · allow_no_setback=True → 屏蔽 success 链
  · mode=off/shadow/active
  · 永远 advisory · 绝不 hard_gate
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import bremond_cadence_scanner as bc  # noqa: E402


def _mk_project(*, outcomes, author_signature=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    clusters = [{"cluster_id": f"cluster_{i+1:03d}",
                 "cluster_bremond_arc": {"outcome": o}}
                for i, o in enumerate(outcomes)]
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    if author_signature is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"outcome_signature": author_signature}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def _set_mode(m):
    if m is None:
        os.environ.pop("BREMOND_CADENCE_MODE", None)
    else:
        os.environ["BREMOND_CADENCE_MODE"] = m


def test_off_returns_skeleton():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("off")
        rep = bc.scan(None)
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_too_few_clusters_skips():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(outcomes=["success", "failure"])
        rep = bc.scan(str(proj))
        assert "样本不足" in rep.get("note", "")
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_monotone_success_streak_active():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(outcomes=["mixed", "failure",
                                       "success", "success", "success"])
        rep = bc.scan(str(proj))
        codes = [v["code"] for v in rep["violations"]]
        # success 占 3/5=60% 未达 80%·streak=3 触发 MONOTONE+ OVER 不触发
        assert "BREMOND_CADENCE_MONOTONE" in codes
        assert rep["verdict"] == "FAIL_MINOR"
        assert rep["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_allow_no_setback_suppresses_success_streak():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(outcomes=["mixed", "failure",
                                       "success", "success", "success"],
                           author_signature={"allow_no_setback": True})
        rep = bc.scan(str(proj))
        codes = [v["code"] for v in rep["violations"]]
        assert "BREMOND_CADENCE_MONOTONE" not in codes
        # over_success 也屏蔽
        assert "BREMOND_CADENCE_OVER_SUCCESS" not in codes
    finally:
        _set_mode(bak)


def test_over_success_ratio_triggered():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        # 5 个全 success → 100% ≥80% + streak=5
        proj = _mk_project(outcomes=["success"] * 5)
        rep = bc.scan(str(proj))
        codes = [v["code"] for v in rep["violations"]]
        assert "BREMOND_CADENCE_OVER_SUCCESS" in codes
        assert "BREMOND_CADENCE_MONOTONE" in codes
    finally:
        _set_mode(bak)


def test_over_failure_ratio_triggered():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        # 4 failure + 1 mixed → 80% failure ≥60%
        proj = _mk_project(outcomes=["mixed", "failure", "failure",
                                       "failure", "failure"])
        rep = bc.scan(str(proj))
        codes = [v["code"] for v in rep["violations"]]
        assert "BREMOND_CADENCE_OVER_FAILURE" in codes
        assert "BREMOND_CADENCE_MONOTONE" in codes
    finally:
        _set_mode(bak)


def test_balanced_distribution_passes():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(outcomes=["success", "mixed", "failure",
                                       "deferred", "mixed"])
        rep = bc.scan(str(proj))
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_shadow_mode_records_no_report():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(outcomes=["success"] * 5)
        rep = bc.scan(str(proj))
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_next_recommendation_present():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(outcomes=["success", "success", "success",
                                       "success", "success"])
        rep = bc.scan(str(proj))
        # 推荐 mixed/failure/deferred 等非 success 项
        assert rep["next_recommended_outcome"] != "success"
    finally:
        _set_mode(bak)


def test_corrupted_event_clusters_safe():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        (proj / "_数据库" / "事件簇.json").write_text("{ bad json",
                                                       encoding="utf-8")
        rep = bc.scan(str(proj))
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_invalid_outcome_label_skipped():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(outcomes=["nonsense", "success", "success",
                                       "success", "success"])
        rep = bc.scan(str(proj))
        # 'nonsense' 被丢弃 → 仅 4 个有效
        assert rep["total_resolved_clusters"] == 4
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("BREMOND_CADENCE_MONOTONE", "BREMOND_CADENCE_OVER_SUCCESS",
              "BREMOND_CADENCE_OVER_FAILURE"):
        assert c not in hgs, c


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        os.environ["BREMOND_CADENCE_MODE"] = "bogus"
        assert bc._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_no_project_root_no_crash():
    bak = os.environ.get("BREMOND_CADENCE_MODE")
    try:
        _set_mode("active")
        rep = bc.scan(None)
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)
