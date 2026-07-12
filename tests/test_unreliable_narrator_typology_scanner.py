# -*- coding: utf-8 -*-
"""unreliable_narrator_typology_scanner.py 专属回归测试。

零依赖·确定性·零 LLM/零联网。覆盖核心 6 分支：
  ① 有 unreliable_narrator_profile + 密 verbal_tic + active → PASS (达 floor)
  ② 有 profile 但 verbal_tic 稀薄 + active → FAIL_MINOR
  ③ reliable=1.0 (爽文默认) → skip
  ④ 无 profile → skip (北极星②)
  ⑤ shadow 模式即使触发也只记不判
  ⑥ off → 骨架
+ 辅助函数全分支 (_strip_changes / _cjk_count / _read_unreliable_profile /
  detect_verbal_tics 8 类) + main CLI subprocess。
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
import unreliable_narrator_typology_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "unreliable_narrator_typology_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("UNRELIABLE_NARRATOR_MODE", None)
    else:
        os.environ["UNRELIABLE_NARRATOR_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(profile: dict | None = None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if profile is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"unreliable_narrator_profile": profile},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# verbal_tic 密集草稿 (8 类各至少 1 个) · > 500 CJK
_TIC_DRAFT = (
    "也许我该承认，那时候我并不知道。我承认我错了，可能记岔了。"
    "你别误会，我没那意思。扯远了，说回正事。我刚才说的不对不对，"
    "我重新说。具体细节记不清，那时太久了。这事儿真假难辨，"
    "信不信由你。嘿嘿，不瞒你说，我可精得很。"
) * 8

# 干净草稿 (零 verbal_tic) · > 500 CJK
_CLEAN_DRAFT = "他推门进屋，桌上摆着一壶茶。窗外飘着雪，炉火噼啪作响。" * 25


# ── ⑥ off → 骨架 ─────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("off")
        proj = _mk_project({"axis": "knowledge", "archetype": "picaro",
                            "signal_intensity_target": 0.5})
        out = mod.scan(_write(_TIC_DRAFT), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
        assert "verbal_tic_per_1k" not in out
    finally:
        _set_mode(bak)


# ── ① 有 profile + 密 verbal_tic + active → PASS (达 floor) ─────────────────
def test_active_with_dense_tics_passes():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project({
            "axis": "knowledge", "archetype": "picaro",
            "signal_intensity_target": 0.5,
        })
        out = mod.scan(_write(_TIC_DRAFT), project_root=proj)
        assert out["verbal_tic_per_1k"] > 0.5  # 密度高
        assert out["verdict"] == "PASS"
        assert out["warning"] is None and out["violations"] == []
        assert out["declared_axis"] == "knowledge"
        assert out["declared_archetype"] == "picaro"
        # 类别命中检查
        cc = out["category_counts"]
        assert cc["hedge"] > 0
        assert cc["fault_admission"] > 0
        assert cc["defensive"] > 0
    finally:
        _set_mode(bak)


# ── ② profile + 稀薄 verbal_tic + active → FAIL_MINOR ───────────────────────
def test_active_with_thin_tics_fails_minor():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project({
            "axis": "narration_reliability", "archetype": "madman",
            "signal_intensity_target": 0.5,
        })
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert out["verbal_tic_per_1k"] < 0.5
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"] and out["violations"][0]["severity"] == "minor"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ── ③ reliable=1.0 → skip (爽文默认旁路) ─────────────────────────────────────
def test_reliable_default_skips():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project({"reliable": 1.0})
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert "reliable=1.0" in out.get("note", "")
        assert out["verdict"] == "PASS"
        assert "verbal_tic_per_1k" not in out
    finally:
        _set_mode(bak)


def test_reliable_high_float_skips():
    """reliable 不严格等于 1.0 但 ≥ 1.0 也 skip。"""
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project({"reliable": 1.2})
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert "reliable=1.0" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ④ 无 profile → skip ────────────────────────────────────────────────────
def test_no_profile_skips():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile=None)  # 不写 profile
        out = mod.scan(_write(_TIC_DRAFT), project_root=proj)
        assert out["unreliable_narrator_profile"] is None
        assert "无 unreliable_narrator_profile" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_project_root_skips():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_TIC_DRAFT), project_root=None)
        assert "无 unreliable_narrator_profile" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑤ shadow 即使触发也只记不判 ────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project({
            "axis": "knowledge", "archetype": "naif",
            "signal_intensity_target": 0.5,
        })
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["verbal_tic_per_1k"] < 0.5  # 触发条件
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 / _mode 非法 ─────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project({"axis": "knowledge", "archetype": "picaro",
                            "signal_intensity_target": 0.5})
        out = mod.scan(_write("也许吧。" * 5), project_root=proj)
        assert "草稿太短" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


# ── _strip_changes / _cjk_count ─────────────────────────────────────────────
def test_strip_changes_factual():
    raw = "正文。\n---CHANGES_FACTUAL---\n{}"
    assert mod._strip_changes(raw) == "正文。"


def test_strip_changes_no_sep():
    assert mod._strip_changes("纯正文") == "纯正文"


def test_cjk_count():
    assert mod._cjk_count("你好abc，") == 2


# ── _read_unreliable_profile 全分支 ──────────────────────────────────────────
def test_read_profile_none_project():
    assert mod._read_unreliable_profile(None) is None


def test_read_profile_file_missing():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert mod._read_unreliable_profile(proj) is None


def test_read_profile_bad_json():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{ bad", encoding="utf-8")
    assert mod._read_unreliable_profile(proj) is None


def test_read_profile_top_not_dict():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("[1,2,3]", encoding="utf-8")
    assert mod._read_unreliable_profile(proj) is None


def test_read_profile_no_field():
    proj = _mk_project(profile=None)
    assert mod._read_unreliable_profile(proj) is None


def test_read_profile_field_not_dict():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"unreliable_narrator_profile": "wrong_type"}), encoding="utf-8")
    assert mod._read_unreliable_profile(proj) is None


# ── _is_reliable_default ────────────────────────────────────────────────────
def test_is_reliable_default_true():
    assert mod._is_reliable_default({"reliable": 1.0})
    assert mod._is_reliable_default({"reliable": 1.5})


def test_is_reliable_default_false():
    assert not mod._is_reliable_default({"reliable": 0.3})
    assert not mod._is_reliable_default({})
    assert not mod._is_reliable_default({"reliable": "bad"})  # 非法值不视为可靠


# ── detect_verbal_tics 8 类全分支 ─────────────────────────────────────────────
def test_detect_8_categories():
    text = (
        "也许吧。我承认我错了。你别误会。说回正事。"
        "话又说回来。我记不太清了。信不信由你。嘿嘿。"
    )
    hits = mod.detect_verbal_tics(text)
    for cat in ("hedge", "fault_admission", "defensive", "digression",
                "inner_inconsistency", "selective_memory", "disbelief", "picaro_cunning"):
        assert hits[cat], f"{cat} 漏命中"


def test_detect_clean_no_hits():
    hits = mod.detect_verbal_tics("他推门进屋，茶香满室。")
    for items in hits.values():
        assert items == []


# ── archetype 异常归一提示 ──────────────────────────────────────────────────
def test_invalid_archetype_emits_note():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project({
            "axis": "knowledge", "archetype": "wizard_typo",
            "signal_intensity_target": 0.5,
        })
        out = mod.scan(_write(_TIC_DRAFT), project_root=proj)
        assert "wizard_typo" in out.get("archetype_note", "")
        # 非阻断·不影响 verdict
        assert out["verdict"] in ("PASS", "FAIL_MINOR")
    finally:
        _set_mode(bak)


# ── signal_intensity_target 默认回落 ────────────────────────────────────────
def test_signal_intensity_target_default():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project({"axis": "knowledge", "archetype": "picaro"})  # 无 target
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert out["signal_intensity_floor"] == 0.5  # 默认 0.5
    finally:
        _set_mode(bak)


def test_signal_intensity_target_bad_value():
    bak = os.environ.get("UNRELIABLE_NARRATOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project({"axis": "knowledge",
                            "signal_intensity_target": "bad_string"})
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert out["signal_intensity_floor"] == 0.5  # 非法 → 回落
    finally:
        _set_mode(bak)


# ── main() CLI subprocess ───────────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "UNRELIABLE_NARRATOR_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project({"axis": "narration_reliability", "archetype": "madman",
                        "signal_intensity_target": 0.5})
    p = _write(_CLEAN_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_when_skip():
    proj = _mk_project(profile=None)
    p = _write(_TIC_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None


# ── zero_shot 语义补召回（复用 zero_shot_prototype 基建）─────────────
import types  # noqa: E402


def test_semantic_augment_adds_zero_shot_hits(monkeypatch):
    """内容后端命中时·正则漏掉的同义表达被 zero_shot 补入·标 source=zero_shot。"""
    fake = types.SimpleNamespace(
        classify_batch=lambda texts, protos, floor=0.5: [
            {"label": "hedge", "score": 0.8, "margin": 0.2, "source": "zero_shot_embedding"}
            if "拿不准" in t else None for t in texts])
    monkeypatch.setitem(sys.modules, "zero_shot_prototype", fake)
    hits = {c: [] for c in mod.VERBAL_TIC_CATEGORIES}
    added = mod._augment_verbal_tics_semantic("这事儿我也拿不准啊。天气不错呢。", hits)
    assert added == 1
    assert len(hits["hedge"]) == 1 and hits["hedge"][0]["source"] == "zero_shot"


def test_semantic_augment_dedup_with_regex(monkeypatch):
    """同句已有正则命中该类 → zero_shot 不重复补入（并集去重）。"""
    fake = types.SimpleNamespace(
        classify_batch=lambda texts, protos, floor=0.5: [
            {"label": "hedge", "score": 0.9, "margin": 0.3, "source": "x"} for _ in texts])
    monkeypatch.setitem(sys.modules, "zero_shot_prototype", fake)
    text = "也许是这样吧。"  # 正则已命中 hedge(也许)
    hits = mod.detect_verbal_tics(text)
    before = len(hits["hedge"])
    added = mod._augment_verbal_tics_semantic(text, hits)
    assert added == 0 and len(hits["hedge"]) == before


def test_semantic_augment_backend_off_no_op(monkeypatch):
    """内容后端不可用（classify_batch 返 all-None）→ 补 0·零回归。"""
    fake = types.SimpleNamespace(
        classify_batch=lambda texts, protos, floor=0.5: [None] * len(texts))
    monkeypatch.setitem(sys.modules, "zero_shot_prototype", fake)
    hits = {c: [] for c in mod.VERBAL_TIC_CATEGORIES}
    added = mod._augment_verbal_tics_semantic("这事儿我也拿不准啊。", hits)
    assert added == 0
