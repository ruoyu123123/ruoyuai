# -*- coding: utf-8 -*-
"""firstperson_retro_self_gap_scanner.py 专属回归测试 (R7 W2·2026-06-20)。

零依赖·确定性·零 LLM/零联网。覆盖:
  ① first_retro_consonant + 无 hindsight + active → FAIL_MINOR
  ② first_retro_dissonant + 充足 hindsight + active → PASS
  ③ first_present POV → skip (非回溯)
  ④ third_limited POV → skip
  ⑤ 无 POV mode 声明 → skip
  ⑥ shadow 模式只记不判
  ⑦ off → 骨架
  ⑧ 事件簇.json vs 作者风格.json 兜底优先级 / main CLI
  ⑨ build_manifest narrative_pov_mode 五分类字段集成
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
import firstperson_retro_self_gap_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "firstperson_retro_self_gap_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("FIRSTPERSON_RETRO_MODE", None)
    else:
        os.environ["FIRSTPERSON_RETRO_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(pov_mode=None, *, in_author_profile=False, status="in_progress"):
    """造项目: pov_mode 写入 事件簇.json active cluster (默认) 或 作者风格.json (兜底)。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if pov_mode is not None:
        if in_author_profile:
            (proj / "_数据库" / "作者风格.json").write_text(
                json.dumps({"narrative_pov_mode": pov_mode}, ensure_ascii=False),
                encoding="utf-8")
        else:
            (proj / "_数据库" / "事件簇.json").write_text(
                json.dumps({"clusters": [
                    {"cluster_id": "cluster_001", "status": status,
                     "narrative_pov_mode": pov_mode}
                ]}, ensure_ascii=False), encoding="utf-8")
    return proj


# 第一人称回溯 + 零 hindsight 签到(全程贴 experiencing-self·退化第一人称当下时)
_RETRO_NO_HINDSIGHT = (
    "我推开门走进房间，看见桌上放着一封信。我拿起信封拆开来读。"
    "信里写着一些奇怪的话，我皱起眉头。我把信折好放回信封，走到窗边。"
    "外面下着雨。我看着雨水打在玻璃上，心里没由来地烦躁。"
    "我转身走出房间，沿着走廊往下走。走廊很长，灯光昏暗。"
) * 30

# 第一人称回溯 + 充足 hindsight 签到(narrating-self 定期评点)
_RETRO_WITH_HINDSIGHT = (
    "回想起来，那是我第一次走进那个房间。我推开门，看见桌上放着一封信。"
    "那时候我并不知道这封信会改变一切。我拿起信封拆开来读。"
    "后来才明白信里那些话的真正含义。多年后回头看，我应该当场烧掉这封信。"
    "如今想来，外面那场雨像是某种预兆。事后回想，那是我最后一次平静的下午。"
    "直到很久以后，我才意识到自己当时已经踏入了无法回头的局面。"
    "我当时哪里知道，走廊尽头等着我的会是那样的命运。"
) * 12


# ── ⑦ off → 骨架 ────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(pov_mode="first_retro_consonant")
        out = mod.scan(_write(_RETRO_NO_HINDSIGHT), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "narrative_pov_mode" not in out
    finally:
        _set_mode(bak)


# ── ① first_retro_consonant + 无 hindsight + active → FAIL_MINOR ──────────
def test_active_retro_no_hindsight_fail_minor():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pov_mode="first_retro_consonant")
        out = mod.scan(_write(_RETRO_NO_HINDSIGHT), project_root=proj)
        assert out["narrative_pov_mode"] == "first_retro_consonant"
        assert out["hindsight_marker_count"] == 0
        assert out["hindsight_density_per_1k"] < 0.4
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
        # 去重说明应在 _doc 中体现
        assert "FUTURE_KNOWLEDGE_LEAK" in out["violations"][0]["_doc"]
    finally:
        _set_mode(bak)


# ── ② first_retro_dissonant + 充足 hindsight → PASS ────────────────────────
def test_active_retro_with_hindsight_pass():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pov_mode="first_retro_dissonant")
        out = mod.scan(_write(_RETRO_WITH_HINDSIGHT), project_root=proj)
        assert out["narrative_pov_mode"] == "first_retro_dissonant"
        assert out["hindsight_marker_count"] >= 5
        assert out["hindsight_density_per_1k"] >= 0.4
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── ③④ 非回溯 POV mode → skip ─────────────────────────────────────────────
def test_first_present_skips():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pov_mode="first_present")
        out = mod.scan(_write(_RETRO_NO_HINDSIGHT), project_root=proj)
        assert out["narrative_pov_mode"] == "first_present"
        assert "非第一人称回溯" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_third_limited_skips():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pov_mode="third_limited")
        out = mod.scan(_write(_RETRO_NO_HINDSIGHT), project_root=proj)
        assert out["narrative_pov_mode"] == "third_limited"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_third_omniscient_skips():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pov_mode="third_omniscient")
        out = mod.scan(_write(_RETRO_NO_HINDSIGHT), project_root=proj)
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑤ 无 POV mode 声明 → skip ───────────────────────────────────────────────
def test_no_pov_mode_skips():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pov_mode=None)
        out = mod.scan(_write(_RETRO_NO_HINDSIGHT), project_root=proj)
        assert out["narrative_pov_mode"] is None
        assert "无 narrative_pov_mode" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_project_skips():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_RETRO_NO_HINDSIGHT), project_root=None)
        assert out["narrative_pov_mode"] is None
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑥ shadow 模式即使触发也只记不判 ────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(pov_mode="first_retro_consonant")
        out = mod.scan(_write(_RETRO_NO_HINDSIGHT), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["hindsight_marker_count"] == 0
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 ───────────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pov_mode="first_retro_consonant")
        out = mod.scan(_write("我推开门。" * 5), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 事件簇.json 优先 vs 作者风格.json 兜底 ───────────────────────────────────
def test_event_cluster_takes_priority_over_author_profile():
    """事件簇.json active cluster 的 pov_mode 优先于作者风格.json。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [{"cluster_id": "cluster_001", "status": "in_progress",
                                    "narrative_pov_mode": "first_retro_dissonant"}]},
                   ensure_ascii=False), encoding="utf-8")
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"narrative_pov_mode": "third_limited"}, ensure_ascii=False),
        encoding="utf-8")
    assert mod._resolve_narrative_pov_mode(proj) == "first_retro_dissonant"


def test_falls_back_to_author_profile_when_no_active_cluster():
    """无 active cluster → 退作者风格.json。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [{"cluster_id": "cluster_001", "status": "done",
                                    "narrative_pov_mode": "first_retro_consonant"}]},
                   ensure_ascii=False), encoding="utf-8")
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"narrative_pov_mode": "first_retro_dissonant"}, ensure_ascii=False),
        encoding="utf-8")
    assert mod._resolve_narrative_pov_mode(proj) == "first_retro_dissonant"


def test_invalid_pov_mode_value_ignored():
    """非法值不被采纳。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [{"cluster_id": "cluster_001", "status": "in_progress",
                                    "narrative_pov_mode": "bogus"}]}, ensure_ascii=False),
        encoding="utf-8")
    assert mod._resolve_narrative_pov_mode(proj) is None


def test_resolve_pov_mode_bad_json_skips():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "事件簇.json").write_text("{ bad", encoding="utf-8")
    assert mod._resolve_narrative_pov_mode(proj) is None


# ── _strip_changes / _cjk_count / _mode 非法回落 ────────────────────────────
def test_strip_changes_separator():
    raw = "正文。\n---CHANGES---\n{}"
    assert mod._strip_changes(raw) == "正文。"


def test_cjk_count_only_cjk():
    assert mod._cjk_count("我回想abc123那时") == 5


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("FIRSTPERSON_RETRO_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── main() CLI subprocess 退出码 ────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "FIRSTPERSON_RETRO_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(pov_mode="first_retro_consonant")
    p = _write(_RETRO_NO_HINDSIGHT)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None and rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_on_good_hindsight():
    proj = _mk_project(pov_mode="first_retro_dissonant")
    p = _write(_RETRO_WITH_HINDSIGHT)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None and rep["verdict"] == "PASS"


# ── ⑨ build_manifest narrative_pov_mode 五分类字段集成 ──────────────────────
def test_build_manifest_exposes_narrative_pov_mode():
    """新增 narrative_pov_mode 字段在 inject_event_cluster_context 输出里·五分类合法值。"""
    import importlib
    sys.path.insert(0, str(_SCRIPTS))
    bm = importlib.import_module("build_manifest")

    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "cluster_001", "status": "in_progress",
             "chapter_range": [1, 5],
             "narrative_pov_mode": "first_retro_dissonant",
             "scope_summary": "首块"}
        ]}, ensure_ascii=False), encoding="utf-8")

    # 构造一个 scanner-stub (._collect_event_cluster_context 调下游 _build_volume_convergence_anchor·需 .load)
    class _S:
        def __init__(self, root):
            self.root = root
        def load(self, *_a, **_kw):
            return {}
    ctx = bm._collect_event_cluster_context(_S(proj), 1)
    assert ctx["mode"] == "on", ctx
    assert ctx["narrative_pov_mode"] == "first_retro_dissonant"
    assert "_narrative_pov_mode_doc" in ctx


def test_build_manifest_pov_mode_default_third_limited():
    """cluster 未声明 pov_mode + 作者档未声明 → 默认 third_limited。"""
    import importlib
    sys.path.insert(0, str(_SCRIPTS))
    bm = importlib.import_module("build_manifest")

    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "cluster_001", "status": "in_progress",
             "chapter_range": [1, 5], "scope_summary": "首块"}
        ]}, ensure_ascii=False), encoding="utf-8")

    class _S2:
        def __init__(self, root):
            self.root = root
        def load(self, *_a, **_kw):
            return {}
    ctx = bm._collect_event_cluster_context(_S2(proj), 1)
    assert ctx["mode"] == "on", ctx
    assert ctx["narrative_pov_mode"] == "third_limited"


def test_build_manifest_pov_mode_falls_back_to_author_profile():
    """cluster 未声明 → 退作者档。"""
    import importlib
    sys.path.insert(0, str(_SCRIPTS))
    bm = importlib.import_module("build_manifest")

    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "cluster_001", "status": "in_progress",
             "chapter_range": [1, 5], "scope_summary": "首块"}
        ]}, ensure_ascii=False), encoding="utf-8")
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"narrative_pov_mode": "first_present"}, ensure_ascii=False),
        encoding="utf-8")

    class _S3:
        def __init__(self, root):
            self.root = root
        def load(self, *_a, **_kw):
            return {}
    ctx = bm._collect_event_cluster_context(_S3(proj), 1)
    assert ctx["mode"] == "on", ctx
    assert ctx["narrative_pov_mode"] == "first_present"
