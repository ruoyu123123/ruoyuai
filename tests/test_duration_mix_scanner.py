# -*- coding: utf-8 -*-
"""duration_mix_scanner.py 测试 (R8 W4 Batch-H · L22 · 2026-06-20)。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import duration_mix_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "duration_mix_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("DURATION_MIX_MODE", None)
    else:
        os.environ["DURATION_MIX_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if baseline:
        obj["duration_mix_baseline"] = baseline
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 草稿：纯 scene 单调 (LLM 默认风格·8k+ CJK 触发兜底)
_SCENE_ONLY = ("他走进屋子。她回头看他。他说话了。她点头答应。" * 500)
# 五型混搭草稿
_MIXED_DRAFT = (
    "他走进屋子，她回头看他。" * 60  # scene
    + "整整三天，他都没有睡觉。" * 6   # summary
    + "一晃半月，山中草木已黄。" * 6   # ellipsis
    + "月色洒在窗台上，远处屋脊连绵不绝。" * 6  # pause
    + "一瞬间，他脑海中闪过往事。" * 6  # stretch
)


# ── classify_sentence ────────────────────────────────────────────────────────
def test_classify_ellipsis():
    assert mod.classify_sentence("一晃半月，山中草木已黄。") == "ellipsis"
    assert mod.classify_sentence("数月后，他再次踏上征途。") == "ellipsis"


def test_classify_summary():
    assert mod.classify_sentence("整整三天，他没有合眼。") == "summary"
    assert mod.classify_sentence("连日来，村里都没有消息。") == "summary"


def test_classify_stretch():
    assert mod.classify_sentence("一瞬间，他脑海中闪过往事。") == "stretch"
    assert mod.classify_sentence("时间凝固了。") == "stretch"


def test_classify_scene_dialogue():
    assert mod.classify_sentence("他说：“你来了。”") == "scene"


def test_classify_scene_action():
    assert mod.classify_sentence("他走进屋子。") == "scene"


def test_classify_pause():
    assert mod.classify_sentence("月色洒在窗台上。") == "pause"


# ── compute_distribution ─────────────────────────────────────────────────────
def test_compute_distribution_keys():
    sents = ["他走进屋子。", "一晃半月。", "整整三天。", "月色洒在窗台。", "一瞬间他想起。"]
    dist = mod.compute_distribution(sents)
    assert dist["total_sentences"] == 5
    for k in ("scene_pct", "summary_pct", "ellipsis_pct", "pause_pct", "stretch_pct"):
        assert k in dist
    assert dist["ellipsis_pct"] > 0 and dist["summary_pct"] > 0


# ── scan: off 骨架 ───────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("DURATION_MIX_MODE")
    try:
        _set_mode("off")
        proj = _mk_project()
        out = mod.scan(_write(_MIXED_DRAFT), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
        assert "distribution" not in out
    finally:
        _set_mode(bak)


# ── 通用兜底：纯 scene cluster > 8k CJK → 触发 ───────────────────────────────
def test_active_scene_only_triggers_generic_floor():
    bak = os.environ.get("DURATION_MIX_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()  # 无 baseline
        out = mod.scan(_write(_SCENE_ONLY), project_root=proj)
        assert out["total_cjk"] >= 8000
        assert out["author_baseline"] is False
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert "节奏单调" in out["warning"]
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ── 混搭草稿 → 通用兜底不触发 ────────────────────────────────────────────────
def test_active_mixed_draft_passes_generic_floor():
    bak = os.environ.get("DURATION_MIX_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.scan(_write(_MIXED_DRAFT), project_root=proj)
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
        # 五型都有 (混搭草稿设计)
        d = out["distribution"]
        assert d["ellipsis_pct"] > 0.01
        assert d["stretch_pct"] > 0.005
    finally:
        _set_mode(bak)


# ── 作者档基线 z>1.5σ → drift ────────────────────────────────────────────────
def test_author_baseline_z_drift_triggers():
    bak = os.environ.get("DURATION_MIX_MODE")
    try:
        _set_mode("active")
        # 作者档：summary 主导·scene 低
        baseline = {
            "scene_pct": {"mean": 0.20, "std": 0.05},
            "summary_pct": {"mean": 0.60, "std": 0.05},
            "ellipsis_pct": {"mean": 0.05, "std": 0.02},
            "pause_pct": {"mean": 0.10, "std": 0.03},
            "stretch_pct": {"mean": 0.05, "std": 0.02},
        }
        proj = _mk_project(baseline=baseline)
        out = mod.scan(_write(_SCENE_ONLY), project_root=proj)
        assert out["author_baseline"] is True
        # scene 占比远高·summary 占比远低·应触发 drift
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert "偏离作者基线" in out["warning"]
        # drift_items 应包含 scene_pct 或 summary_pct
        kinds = {d["dimension"] for d in out["violations"][0]["drift_items"]}
        assert "scene_pct" in kinds or "summary_pct" in kinds
    finally:
        _set_mode(bak)


# ── shadow 命中只记不判 ──────────────────────────────────────────────────────
def test_shadow_records_no_violation():
    bak = os.environ.get("DURATION_MIX_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project()
        out = mod.scan(_write(_SCENE_ONLY), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["violations"] == []
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 短草稿 / 句数不足 跳过 ───────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("DURATION_MIX_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.scan(_write("他走进屋子。" * 5), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_too_few_sentences_skipped():
    bak = os.environ.get("DURATION_MIX_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        # 多 CJK 但句子数 < 10 (只用 5 个长句)
        long_sents = "他在山中走了一段非常漫长的路程·途中没遇见任何人·" * 30 + "。"
        out = mod.scan(_write(long_sents), project_root=proj)
        # 应 skip 或正常·此场景下句子很少
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── _mode 非法回落 ──────────────────────────────────────────────────────────
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("DURATION_MIX_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


# ── 读 baseline·空作者档 → None ─────────────────────────────────────────────
def test_read_baseline_none_when_missing():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert mod._read_author_baseline(proj) is None


def test_read_baseline_none_no_project():
    assert mod._read_author_baseline(None) is None


# ── _strip_changes 分支 ─────────────────────────────────────────────────────
def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\nfoo") == "正文。"
    assert mod._strip_changes("纯正文") == "纯正文"


# ── CLI subprocess ──────────────────────────────────────────────────────────
def _run_cli(draft_path, project, mode="shadow"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "DURATION_MIX_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_0_in_shadow_mode():
    proj = _mk_project()
    p = _write(_MIXED_DRAFT)
    r = _run_cli(p, proj, mode="shadow")
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["mode"] == "shadow"


def test_main_exit_1_active_triggers_floor():
    proj = _mk_project()
    p = _write(_SCENE_ONLY)
    r = _run_cli(p, proj, mode="active")
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None
