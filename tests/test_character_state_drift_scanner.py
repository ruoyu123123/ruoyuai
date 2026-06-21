# -*- coding: utf-8 -*-
"""character_state_drift_scanner R20 W9 Batch-Z·P0 NKW 时态分离回归测试

确定性·零依赖·零 LLM/零联网。覆盖：
  1. off 骨架
  2. 无人物卡 skip
  3. 缺 ledger → 用 DEFAULT_LEDGER 占位
  4. shadow + 稳定身份 drift → 不上报
  5. active + 稳定身份 drift → FAIL_MINOR
  6. dynamic_state 变化 → 不报（二筛吸收）
  7. ledger 已有 value 但草稿与 ledger 一致 → 不报
  8. ledger value 缺 → 不报（首次声明保守）
  9. 短稿 skip
 10. 草稿读取失败 → note
 11. _mode 非法回落
 12. _classify_attr 分类
 13. filter_dynamic_state_changes 二筛接口
 14. _load_ledger DEFAULT 占位
 15. CLI subprocess 退出码
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
import character_state_drift_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "character_state_drift_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("CHARACTER_STATE_DRIFT_MODE", None)
    else:
        os.environ["CHARACTER_STATE_DRIFT_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(characters=None, ledger=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")
    if ledger is not None:
        (db / "character_state_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return proj


# 草稿：张三 的 瞳色 是 蓝色（vs ledger=黑色）= stable drift
_STABLE_DRIFT_DRAFT = (
    "张三走进客栈。张三的瞳色是蓝色。他坐下点了一壶茶。"
    + "众人聚集议事。江湖纷争如烟云聚散。" * 80
)

# 草稿：张三 的 心情 是 凝重 = dynamic update（合法剧情）
_DYNAMIC_UPDATE_DRAFT = (
    "张三走进客栈。张三的心情是凝重。他坐下点了一壶茶。"
    + "众人聚集议事。江湖纷争如烟云聚散。" * 80
)

# 草稿：张三 的 瞳色 是 黑色（与 ledger 一致）= 不报
_CONSISTENT_DRAFT = (
    "张三走进客栈。张三的瞳色是黑色。他坐下点了一壶茶。"
    + "众人聚集议事。江湖纷争如烟云聚散。" * 80
)


def _make_ledger():
    return {
        "schema_version": 1,
        "characters": {
            "张三": {
                "stable_identity": {"瞳色": "黑色", "性别": "男"},
                "dynamic_state": {"心情": "平静", "位置": "山下"},
            }
        }
    }


# ───── 1 off 骨架 ──
def test_off_returns_skeleton():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
        out = mod.scan(_write(_STABLE_DRIFT_DRAFT), proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 2 无人物卡 ──
def test_no_character_card_skip():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_STABLE_DRIFT_DRAFT), _mk_project())
        assert out["character_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 3 缺 ledger → DEFAULT_LEDGER 占位 ──
def test_missing_ledger_uses_default():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_STABLE_DRIFT_DRAFT), proj)
        # ledger 缺·无角色 stable_identity ground → 无 drift（保守）
        assert out["ledger_placeholder"] is True
        assert out["stable_drift_count"] == 0
    finally:
        _set_mode(bak)


# ───── 4 shadow + stable drift 不上报 ──
def test_shadow_drift_no_report():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
        out = mod.scan(_write(_STABLE_DRIFT_DRAFT), proj)
        assert out["violations"] == []
        assert out["warning"] is None
        # 但仍记录 stable_drift_count
        assert out["stable_drift_count"] >= 1
    finally:
        _set_mode(bak)


# ───── 5 active + stable drift ──
def test_active_stable_drift_fail_minor():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
        out = mod.scan(_write(_STABLE_DRIFT_DRAFT), proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "CHARACTER_STATE_DRIFT_DETECTED"
        sample = out["stable_drift_samples"][0]
        assert sample["attr"] == "瞳色"
        assert sample["ledger_value"] == "黑色"
        assert sample["drafted_value"] == "蓝色"
    finally:
        _set_mode(bak)


# ───── 6 dynamic state 变化 → 不报 ──
def test_dynamic_state_change_not_reported():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
        out = mod.scan(_write(_DYNAMIC_UPDATE_DRAFT), proj)
        assert out["stable_drift_count"] == 0
        assert out["verdict"] == "PASS"
        # dynamic_state_updates 收集了变化
        upds = out["dynamic_state_updates"]
        assert any(u["attr"] == "心情" and u["new_value"] == "凝重" for u in upds)
    finally:
        _set_mode(bak)


# ───── 7 草稿与 ledger 一致 → 不报 ──
def test_consistent_with_ledger_no_drift():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
        out = mod.scan(_write(_CONSISTENT_DRAFT), proj)
        assert out["stable_drift_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 8 ledger 缺该 attr value → 不报（首次声明保守）──
def test_ledger_missing_attr_no_report():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("active")
        # ledger 没有 "瞳色"
        ledger = {"schema_version": 1, "characters":
                  {"张三": {"stable_identity": {"性别": "男"},
                          "dynamic_state": {}}}}
        proj = _mk_project(characters=[{"name": "张三"}], ledger=ledger)
        out = mod.scan(_write(_STABLE_DRIFT_DRAFT), proj)
        assert out["stable_drift_count"] == 0
    finally:
        _set_mode(bak)


# ───── 9 短稿 skip ──
def test_short_draft_skip():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write("短稿。" * 5), proj)
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


# ───── 10 读取失败 ──
def test_read_failure_returns_note():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ───── 11 _mode ──
def test_mode_invalid_falls_back():
    bak = os.environ.get("CHARACTER_STATE_DRIFT_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ───── 12 _classify_attr ──
def test_classify_attr():
    ledger = {"stable_identity_attrs": ["瞳色"], "dynamic_state_attrs": ["心情"]}
    assert mod._classify_attr("瞳色", ledger) == "stable"
    assert mod._classify_attr("心情", ledger) == "dynamic"
    assert mod._classify_attr("不存在", ledger) == "unknown"


# ───── 13 filter_dynamic_state_changes ──
def test_filter_dynamic_state_changes():
    proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
    candidates = [
        {"char": "张三", "attr": "心情", "note": "心情变化"},   # dynamic·剔除
        {"char": "张三", "attr": "瞳色", "note": "瞳色变化"},   # stable·保留
        {"char": "张三", "attr": "外号", "note": "未登记"},     # unknown·保留
    ]
    kept = mod.filter_dynamic_state_changes(candidates, proj)
    attrs = [c["attr"] for c in kept]
    assert "心情" not in attrs
    assert "瞳色" in attrs
    assert "外号" in attrs


def test_filter_dynamic_state_changes_empty():
    proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
    assert mod.filter_dynamic_state_changes([], proj) == []
    assert mod.filter_dynamic_state_changes(None, proj) == []


# ───── 14 _load_ledger DEFAULT 占位 ──
def test_load_ledger_default_placeholder():
    proj = _mk_project(characters=[{"name": "张三"}])
    ledger, char_ledger, ph = mod._load_ledger(proj)
    assert ph is True
    assert "瞳色" in ledger["stable_identity_attrs"]
    assert "心情" in ledger["dynamic_state_attrs"]
    assert char_ledger == {}


def test_load_ledger_real_merge():
    ledger = {"schema_version": 1, "characters":
              {"张三": {"stable_identity": {"自定义稳定": "X"},
                      "dynamic_state": {"自定义动态": "Y"}}}}
    proj = _mk_project(characters=[{"name": "张三"}], ledger=ledger)
    ldg, cldg, ph = mod._load_ledger(proj)
    assert ph is False
    assert "自定义稳定" in ldg["stable_identity_attrs"]
    assert "自定义动态" in ldg["dynamic_state_attrs"]
    assert "张三" in cldg


# ───── 15 _strip_changes / _cjk_count ──
def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\nlog") == "正文。"


def test_cjk_count_basic():
    assert mod._cjk_count("你好abc世界") == 4


# ───── 16 CLI exit ──
def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CHARACTER_STATE_DRIFT_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
    r = _run_cli(_write(_STABLE_DRIFT_DRAFT), proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[{"name": "张三"}], ledger=_make_ledger())
    r = _run_cli(_write(_CONSISTENT_DRAFT), proj)
    assert r.returncode == 0, r.stderr


# ───── 17 registry _new=true + 不在 hard_gate ──
def test_registry_entry():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg["scanners"].get("character_state_drift_scanner")
    assert s is not None
    assert s.get("_new") is True
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs
