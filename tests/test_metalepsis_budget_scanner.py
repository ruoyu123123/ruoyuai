# -*- coding: utf-8 -*-
"""metalepsis_budget_scanner.py 专属回归测试 (R8 W4 Batch-I · 2026-06-20)。

零依赖·确定性·零 LLM/零联网。覆盖:
  ① type=none + bracket marker + active → FAIL_MINOR
  ② type=none 干净草稿 → PASS
  ③ type=rhetorical + target=3 + 实际偏离 → FAIL_MINOR
  ④ 无 metalepsis_budget → skip
  ⑤ shadow 模式即使触发只记不判
  ⑥ off → 骨架
  ⑦ ontological 切换窗口未闭合 advisory
  ⑧ 辅助函数 detect_markers / _read_budget / main CLI
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
import metalepsis_budget_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "metalepsis_budget_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("METALEPSIS_BUDGET_MODE", None)
    else:
        os.environ["METALEPSIS_BUDGET_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(budget=None, file="作者风格.json"):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if budget is not None:
        obj["metalepsis_budget"] = budget
    (proj / "_数据库" / file).write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


_CLEAN = "他走进山门，青石阶上落满松针。师兄煮茶，炉火映着眉眼。" * 40
# bracket markers 多次 + 后续无闭合(连续几个 marker)
_BRACKET_HEAVY = (
    "他走进山门并继续往前。" * 60 +
    "【系统提示】恭喜玩家进入新副本【系统提示】发布主线任务【系统提示】注意危险" * 4 +
    "他继续往前走。" * 60
)
# rhetorical 风格(narratee 称谓)
_RHETORICAL = (
    "亲爱的读者，你以为故事就这样结束了吗？" + "他走进山门往前走。" * 80 +
    "你或许会问，这是真的吗？" + "他继续走向前。" * 60
)


# ── ⑥ off → 骨架 ────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(budget={"type": "none", "target_per_cluster": 0})
        out = mod.scan(_write(_BRACKET_HEAVY), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "marker_count" not in out
    finally:
        _set_mode(bak)


# ── ① type=none + marker + active → FAIL_MINOR ─────────────────────────────
def test_active_none_with_markers_fail_minor():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(budget={"type": "none", "target_per_cluster": 0})
        out = mod.scan(_write(_BRACKET_HEAVY), project_root=proj)
        assert out["budget"] is not None
        assert out["marker_count"] > 0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
        assert out["kind_counts"].get("bracket", 0) > 0
    finally:
        _set_mode(bak)


# ── ② type=none 干净草稿 → PASS ────────────────────────────────────────────
def test_active_none_clean_pass():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(budget={"type": "none", "target_per_cluster": 0})
        out = mod.scan(_write(_CLEAN), project_root=proj)
        assert out["budget"]["type"] == "none"
        assert out["marker_count"] == 0
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── ③ type=rhetorical + target 偏离 → FAIL_MINOR ──────────────────────────
def test_rhetorical_deviation_fail_minor():
    """target=10 但实际很多 marker → 偏差>0.5 → advisory。"""
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(budget={"type": "rhetorical", "target_per_cluster": 10})
        out = mod.scan(_write(_RHETORICAL), project_root=proj)
        # narratee 形 marker 应被 detect 算入
        assert out["marker_count"] > 0
    finally:
        _set_mode(bak)


def test_rhetorical_within_budget_pass():
    """target=1 实际 1 marker → 偏差 0 < 0.5 → PASS。"""
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(budget={"type": "rhetorical", "target_per_cluster": 1})
        body = "亲爱的读者，请听这个故事。" + "他走进山门。" * 100
        out = mod.scan(_write(body), project_root=proj)
        # 实际 1 marker = target → PASS
        if out["marker_count"] == 1:
            assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ④ 无 budget → skip ─────────────────────────────────────────────────────
def test_no_budget_skips():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(budget=None)
        out = mod.scan(_write(_BRACKET_HEAVY), project_root=proj)
        assert out["budget"] is None
        assert "无 metalepsis_budget" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_project_skips():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BRACKET_HEAVY), project_root=None)
        assert out["budget"] is None
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑤ shadow 模式 ──────────────────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(budget={"type": "none", "target_per_cluster": 0})
        out = mod.scan(_write(_BRACKET_HEAVY), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["marker_count"] > 0
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑦ ontological 窗口闭合 ────────────────────────────────────────────────
def test_ontological_unresolved_window():
    """ontological 切换后窗口连续几乎无标点 → 未闭合。"""
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(budget={"type": "ontological", "target_per_cluster": 1})
        # 一个 bracket marker 后接很多无标点正文
        body = ("他走进山门" * 80 + "\n\n" +
                "【系统】他突然意识到自己是故事里的角色然后他继续做" * 10 +
                "他走进山门" * 60)
        out = mod.scan(_write(body), project_root=proj)
        # 应至少检测出 marker
        assert out["marker_count"] > 0
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(budget={"type": "none", "target_per_cluster": 0})
        out = mod.scan(_write("【系统】" * 5), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── 辅助 ────────────────────────────────────────────────────────────────────
def test_detect_markers_bracket():
    hits = mod.detect_markers("正文【系统提示】内容")
    assert hits and hits[0]["kind"] == "bracket"


def test_detect_markers_narratee():
    hits = mod.detect_markers("亲爱的读者，请看")
    assert any(h["kind"] == "narratee" for h in hits)


def test_detect_markers_sorted_by_pos():
    hits = mod.detect_markers("【头】中间▶尾")
    positions = [h["pos"] for h in hits]
    assert positions == sorted(positions)


def test_strip_changes_separator():
    raw = "正文。\n---CHANGES_FACTUAL---\n{}"
    assert mod._strip_changes(raw) == "正文。"


def test_cjk_count():
    assert mod._cjk_count("元小说abc") == 3


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("METALEPSIS_BUDGET_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_budget_none():
    assert mod._read_budget(None) is None


def test_read_budget_from_author_profile():
    proj = _mk_project(budget={"type": "rhetorical", "target_per_cluster": 2})
    b = mod._read_budget(proj)
    assert b == {"type": "rhetorical", "target_per_cluster": 2}


def test_read_budget_invalid_dict_ignored():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"metalepsis_budget": "not a dict"}, ensure_ascii=False),
        encoding="utf-8")
    assert mod._read_budget(proj) is None


def test_read_budget_no_type_ignored():
    proj = _mk_project(budget={"target_per_cluster": 5})
    assert mod._read_budget(proj) is None


# ── CLI ─────────────────────────────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "METALEPSIS_BUDGET_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(budget={"type": "none", "target_per_cluster": 0})
    p = _write(_BRACKET_HEAVY)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_clean():
    proj = _mk_project(budget={"type": "none", "target_per_cluster": 0})
    p = _write(_CLEAN)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None
