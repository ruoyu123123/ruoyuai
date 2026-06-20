# -*- coding: utf-8 -*-
"""narratee_address_scanner.py 专属回归测试 (R8 W4 Batch-I · 2026-06-20)。

零依赖·确定性·零 LLM/零联网。
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
import narratee_address_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "narratee_address_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("NARRATEE_ADDRESS_MODE", None)
    else:
        os.environ["NARRATEE_ADDRESS_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(registry=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if registry is not None:
        obj["narratee_registry"] = registry
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 一致 narratee:全用「亲爱的读者」
_CONSISTENT_BODY = (
    "亲爱的读者，请听我说。" * 8 +
    "他走进山门往前走。" * 60 +
    "亲爱的读者，故事还在继续。" * 6 +
    "他继续往前走。" * 40
)
# 漂浮 narratee:混用 亲爱的读者 / 诸位看官 / 各位
_DRIFT_BODY = (
    "亲爱的读者，请听我说。" * 2 +
    "诸位看官，请听我说。" * 5 +
    "各位请勿离席。" * 5 +
    "他走进山门往前走。" * 60 +
    "你以为故事就这样结束了吗？" * 3
)


# ── off → 骨架 ──────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(registry={"primary": "亲爱的读者", "min_consistency": 0.8})
        out = mod.scan(_write(_DRIFT_BODY), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert "primary_ratio" not in out
    finally:
        _set_mode(bak)


# ── 无 registry → skip ─────────────────────────────────────────────────────
def test_no_registry_skip():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(registry=None)
        out = mod.scan(_write(_DRIFT_BODY), project_root=proj)
        assert "无 narratee_registry" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 漂浮 narratee + active → FAIL_MINOR ────────────────────────────────────
def test_drift_fail_minor():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(registry={
            "primary": "亲爱的读者",
            "allowed_addresses": ["亲爱的读者"],
            "min_consistency": 0.8,
        })
        out = mod.scan(_write(_DRIFT_BODY), project_root=proj)
        assert out["total_narratee_hits"] > 0
        assert out["primary_ratio"] < 0.8
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
        assert len(out["out_of_registry"]) >= 1
    finally:
        _set_mode(bak)


# ── 一致 narratee + active → PASS ─────────────────────────────────────────
def test_consistent_pass():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(registry={
            "primary": "亲爱的读者",
            "allowed_addresses": ["亲爱的读者", "读者"],
            "min_consistency": 0.7,
        })
        out = mod.scan(_write(_CONSISTENT_BODY), project_root=proj)
        assert out["total_narratee_hits"] > 0
        assert out["primary_ratio"] >= 0.5
    finally:
        _set_mode(bak)


# ── shadow 模式 ──────────────────────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(registry={"primary": "亲爱的读者", "min_consistency": 0.8})
        out = mod.scan(_write(_DRIFT_BODY), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 正文无 narratee 命中 → skip ────────────────────────────────────────────
def test_no_narratee_hits_skip():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(registry={"primary": "亲爱的读者", "min_consistency": 0.8})
        body = "他走进山门，青石板上落满松针。" * 60
        out = mod.scan(_write(body), project_root=proj)
        assert out["total_narratee_hits"] == 0
        assert "正文无 narratee" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 ─────────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(registry={"primary": "亲爱的读者"})
        out = mod.scan(_write("亲爱的读者。" * 3), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── 辅助 ────────────────────────────────────────────────────────────────────
def test_count_forms_returns_all_keys():
    counts = mod.count_forms("亲爱的读者，诸位看官，你以为，各位。")
    assert "亲爱的读者" in counts
    assert "读者" in counts
    assert "看官" in counts
    assert "你_breakwall" in counts
    assert counts["亲爱的读者"] >= 1
    assert counts["看官"] >= 1
    assert counts["你_breakwall"] >= 1


def test_classify_form_qinai():
    assert mod._classify_form("亲爱的读者") == "亲爱的读者"


def test_classify_form_kanguan():
    assert mod._classify_form("看官") == "看官"
    assert mod._classify_form("诸位看官") == "看官"


def test_classify_form_du_zhe():
    assert mod._classify_form("读者") == "读者"


def test_classify_form_zhuwei():
    assert mod._classify_form("诸位") == "诸位"


def test_classify_form_ni():
    assert mod._classify_form("你") == "你_breakwall"


def test_read_registry_none():
    assert mod._read_registry(None) is None


def test_read_registry_from_author():
    proj = _mk_project(registry={"primary": "亲爱的读者", "min_consistency": 0.9})
    r = mod._read_registry(proj)
    assert r["primary"] == "亲爱的读者"


def test_read_registry_missing_primary_ignored():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"narratee_registry": {"min_consistency": 0.5}}, ensure_ascii=False),
        encoding="utf-8")
    assert mod._read_registry(proj) is None


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\nx") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("读者abc叙述") == 4


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("NARRATEE_ADDRESS_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "NARRATEE_ADDRESS_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(registry={
        "primary": "亲爱的读者",
        "allowed_addresses": ["亲爱的读者"],
        "min_consistency": 0.8,
    })
    p = _write(_DRIFT_BODY)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_no_registry():
    proj = _mk_project(registry=None)
    p = _write(_DRIFT_BODY)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
