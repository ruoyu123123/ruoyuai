# -*- coding: utf-8 -*-
"""object_biography_scanner R13 W6 Batch-R · P1 STRONG 回归测试（确定性·零依赖·零 LLM/零联网）。

覆盖 13 case：
  1. off → 骨架
  2. 无候选物件 → skip
  3. 草稿候选 + 多相位 → PASS（阶段链完整）
  4. 物件 in_use 单档 streak → phase_skip_rate 触发
  5. active 模式 in_use 重度 → FAIL_MINOR + warning + violations
  6. shadow 高密度命中 → 仅 stderr 不上报
  7. 登记表读取 plot_critical=true 入选
  8. 登记表 mentions>=2 入选
  9. 登记表损坏 → fallback 草稿候选
 10. 短稿 → skip
 11. 草稿读取失败 → note
 12. snapshot 写盘成功
 13. _mode 非法回落
 14. main() CLI subprocess 退出码
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
import object_biography_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "object_biography_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("OBJECT_BIOGRAPHY_MODE", None)
    else:
        os.environ["OBJECT_BIOGRAPHY_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(items=None, *, file="物件登记表.json"):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if items is not None:
        (proj / "_数据库" / file).write_text(
            json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    return proj


# in_use 单档物件（玉佩 反复 拿着 使用 握住）
_INUSE_DRAFT = ("他拿着玉佩走进屋。玉佩握住时温润。他用玉佩使用了几分。"
                "玉佩还在他手里持着。再后来玉佩使用了一回又一回。") * 12

# 完整相位链（剑 取出 → 使用 → 破损 → 找回 → 重逢）
_FULL_PHASE_DRAFT = (
    "他取出剑，剑光寒冽锋利。\n"
    "剑使用之时，杀气盈野无人能挡。\n"
    "数日后，剑破损碎裂落地有声。\n"
    "他遍寻江湖访名匠，终于找回剑。\n"
    "再后来，剑重逢故人，复归手中如旧。\n"
) * 20


# 无候选（短小通名）
_NO_CANDIDATE = "他走进山门，看见师兄。师兄煮茶。" * 30


# ───── 1. off → 骨架 ─────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_INUSE_DRAFT), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert out["violations"] == [] and out["warning"] is None
        assert "phase_skip_rate" not in out
    finally:
        _set_mode(bak)


# ───── 2. 无候选物件 → skip ─────────────────────────────────────────────────
def test_no_candidate_objects_skip():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_NO_CANDIDATE), _mk_project())
        assert out["verdict"] == "PASS"
        # 可能 named_objects_count=0 或 note 包含跳过
        assert out.get("named_objects_count", 0) == 0
    finally:
        _set_mode(bak)


# ───── 3. 完整相位链 → 不触发 phase_skip（在 active 下也 PASS 或 minor）─────
def test_full_phase_chain_no_phase_skip_flag():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("active")
        items = [{"name": "剑", "plot_critical": True}]
        proj = _mk_project(items=items)
        out = mod.scan(_write(_FULL_PHASE_DRAFT), proj)
        assert out["named_objects_count"] == 1
        per = out["per_object"][0]
        # 至少含 acquired 与 in_use（取出/使用）
        assert per["mentions"] >= 3
        assert not per["phase_skip"]   # 完整链 → phase_skip=False
    finally:
        _set_mode(bak)


# ───── 4. in_use 单档 streak → phase_skip_rate 触发 ─────────────────────────
def test_in_use_only_triggers_phase_skip_rate():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("active")
        items = [{"name": "玉佩", "plot_critical": True}]
        proj = _mk_project(items=items)
        out = mod.scan(_write(_INUSE_DRAFT), proj)
        assert out["named_objects_count"] == 1
        per = out["per_object"][0]
        assert per["phase_skip"]    # 仅 in_use 单档 → 触发
        assert out["phase_skip_rate"] >= 0.40
    finally:
        _set_mode(bak)


# ───── 5. active 模式 → FAIL_MINOR + warning ───────────────────────────────
def test_active_in_use_overuse_fail_minor():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("active")
        items = [{"name": "玉佩", "plot_critical": True}]
        proj = _mk_project(items=items)
        out = mod.scan(_write(_INUSE_DRAFT), proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"] and out["violations"][0]["code"] == "OBJECT_BIOGRAPHY_THIN"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ───── 6. shadow 模式 → 仅 stderr 不上报 ────────────────────────────────────
def test_shadow_no_violation():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("shadow")
        items = [{"name": "玉佩", "plot_critical": True}]
        proj = _mk_project(items=items)
        out = mod.scan(_write(_INUSE_DRAFT), proj)
        assert out["mode"] == "shadow"
        # 仍计算 phase_skip_rate · 但不上报
        assert out["verdict"] == "PASS"
        assert out["violations"] == []
        assert out["warning"] is None
        assert out["phase_skip_rate"] >= 0.40
    finally:
        _set_mode(bak)


# ───── 7. 登记表 plot_critical=true 入选 ─────────────────────────────────────
def test_load_named_object_plot_critical():
    items = [{"name": "玉佩", "plot_critical": True}]
    proj = _mk_project(items=items)
    loaded = mod._load_named_objects_from_db(proj)
    assert loaded and loaded[0]["name"] == "玉佩"
    assert loaded[0]["plot_critical"] is True


# ───── 8. 登记表 mentions>=2 入选 ────────────────────────────────────────────
def test_load_named_object_mentions_threshold():
    items = [{"name": "剑", "mentions": 5},
             {"name": "酒杯", "mentions": 1}]   # 低于门槛
    proj = _mk_project(items=items)
    loaded = mod._load_named_objects_from_db(proj)
    names = [o["name"] for o in loaded]
    assert "剑" in names and "酒杯" not in names


# ───── 9. 登记表损坏 → fallback 草稿候选 ────────────────────────────────────
def test_load_named_object_bad_json_falls_back():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "物件登记表.json").write_text("{ bad json", encoding="utf-8")
    loaded = mod._load_named_objects_from_db(proj)
    assert loaded == []   # 登记表损坏 → 返回 [] → scan 走 _harvest_candidates_from_text


# ───── 10. 短稿 → skip ──────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("玉佩。" * 10), _mk_project())   # < 500 CJK
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 11. 草稿读取失败 → note ───────────────────────────────────────────────
def test_read_failure_returns_note():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 12. snapshot 写盘 ─────────────────────────────────────────────────────
def test_snapshot_written():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("active")
        items = [{"name": "玉佩", "plot_critical": True}]
        proj = _mk_project(items=items)
        out = mod.scan(_write(_INUSE_DRAFT), proj, cluster_id="cluster_test")
        snap = proj / "_数据库" / ".object_biography" / "cluster_test.json"
        assert snap.exists()
        payload = json.loads(snap.read_text(encoding="utf-8"))
        assert payload["cluster_id"] == "cluster_test"
        assert payload["named_objects_count"] >= 1
    finally:
        _set_mode(bak)


# ───── 13. _mode 非法回落 ───────────────────────────────────────────────────
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_default_shadow_when_unset():
    bak = os.environ.get("OBJECT_BIOGRAPHY_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ───── 14. main() CLI subprocess 退出码 ─────────────────────────────────────
def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "OBJECT_BIOGRAPHY_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    items = [{"name": "玉佩", "plot_critical": True}]
    proj = _mk_project(items=items)
    p = _write(_INUSE_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None
    assert rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_clean_full_phase():
    items = [{"name": "剑", "plot_critical": True}]
    proj = _mk_project(items=items)
    p = _write(_FULL_PHASE_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "PASS"


# ───── 辅助 _strip_changes / _cjk_count ─────────────────────────────────────
def test_strip_changes_factual_separator():
    raw = "正文。\n\n---CHANGES_FACTUAL---\n{\"foo\":1}"
    assert mod._strip_changes(raw) == "正文。"


def test_strip_changes_plain_separator():
    raw = "另一段。\n---CHANGES---\nlog"
    assert mod._strip_changes(raw) == "另一段。"


def test_cjk_count_basic():
    assert mod._cjk_count("你好世界abc") == 4


# ───── 候选采集 _harvest_candidates_from_text ───────────────────────────────
def test_harvest_excludes_generic_names():
    text = "他取出东西用着。东西使用。" * 10
    out = mod._harvest_candidates_from_text(text)
    names = [o["name"] for o in out]
    assert "东西" not in names


def test_harvest_requires_cue_cooccurrence():
    # 高频但无 phase cue 的词不入选
    text = "山门青石。山门青石。山门青石。" * 20
    out = mod._harvest_candidates_from_text(text)
    names = [o["name"] for o in out]
    assert "山门" not in names    # 无 phase cue 共现


# ───── per_object signals 准确性 ────────────────────────────────────────────
def test_per_object_phase_seq_order():
    text = "他取出剑。剑使用之时。剑破损碎裂。剑找回。"
    sig = mod._per_object_signals(text, {"name": "剑"})
    assert sig["mentions"] == 4
    # 至少 acquired 与 damaged 在序列里
    assert "acquired" in sig["phase_seq"]
    assert sig["missing_acquired"] is False
