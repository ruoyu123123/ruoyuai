# -*- coding: utf-8 -*-
"""cross_character_kth_order_belief_scanner R20 W9 Batch-Z·P0 K=2 嵌套信念回归测试

确定性·零依赖·零 LLM/零联网。覆盖：
  1. off 骨架
  2. <2 角色 skip
  3. shadow + 无 drift → PASS
  4. shadow + drift 不上报 stderr
  5. active + drift overestimate → FAIL_MINOR
  6. active + drift underestimate（dramatic_irony 未声明）→ FAIL_MINOR
  7. manifest dramatic_irony_anchor 白名单 → 不报
  8. 短稿 skip
  9. 草稿读取失败 → note
 10. _mode 非法回落
 11. _strip_changes 两种分隔符
 12. _cjk_count
 13. locked_fact.json 读取
 14. 占位 fact lexicon fallback
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
import cross_character_kth_order_belief_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_character_kth_order_belief_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE", None)
    else:
        os.environ["CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(characters=None, locked_facts=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")
    if locked_facts is not None:
        # 🔴 2026-06-29 重接线：真 locked facts 在 事件簇.json.clusters[].locked_facts
        # (producer: apply_archive.apply_locked_facts)·非零 producer 的幻影 locked_fact.json
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": [{"cluster_id": "cluster_001",
                                      "locked_facts": locked_facts}]},
                       ensure_ascii=False),
            encoding="utf-8")
    return proj


def _mk_manifest(anchors):
    p = Path(tempfile.mkdtemp()) / "manifest.json"
    p.write_text(json.dumps({"dramatic_irony_anchor": anchors},
                            ensure_ascii=False), encoding="utf-8")
    return p


_SEP = "\n━━━━━━━━━━━━\n"

# 张三 以为 李四 知道秘密（A 高估 B）·B 实际全程未在场景中接触秘密 → DRIFT
_OVERESTIMATE_DRAFT = (
    ("张三独自走过空旷的街道。" * 30)  # 场景 1：仅 A 在场，无 fact
    + _SEP
    + ("张三以为李四知道秘密。他暗自盘算着。" * 25)  # 场景 2：A 错以为 B knows
)

# 场景 1 李四在场获得秘密知识；场景 2 张三 以为 李四 不知道秘密 → underestimate DRIFT
_UNDERESTIMATE_DRAFT = (
    ("李四走进议事厅，长老揭开秘密。秘密秘密秘密。" * 25)  # 场景 1：B 学到 fact
    + _SEP
    + ("张三以为李四不知道秘密。他暗自盘算。" * 25)  # 场景 2：A 错以为 B 不知
)

# 张三 认为 李四 知道秘密·B 实际场景 1 已经在场接触 fact → 合法不报
_LEGAL_DRAFT = (
    ("李四走进议事厅，长老揭开秘密。秘密。" * 25)
    + _SEP
    + ("张三认为李四知道秘密。两人对视。" * 25)
)


# ───── 1 off ──
def test_off_returns_skeleton():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
        out = mod.scan(_write(_OVERESTIMATE_DRAFT), proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 2 <2 角色 skip ──
def test_too_few_characters_skip():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_OVERESTIMATE_DRAFT), proj)
        assert "K=2 不适用" in out.get("note", "")
    finally:
        _set_mode(bak)


# ───── 3 shadow 无 drift ──
def test_shadow_legal_pass():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
        out = mod.scan(_write(_LEGAL_DRAFT), proj)
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ───── 4 shadow + drift 不上报 ──
def test_shadow_drift_no_report():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
        out = mod.scan(_write(_OVERESTIMATE_DRAFT), proj)
        assert out["violations"] == []
        assert out["warning"] is None
        # 但仍记录 drift_count
        assert out["drift_count"] >= 1
    finally:
        _set_mode(bak)


# ───── 5 active + overestimate ──
def test_active_overestimate_fail_minor():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
        out = mod.scan(_write(_OVERESTIMATE_DRAFT), proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "CHARACTER_KTH_ORDER_BELIEF_DRIFT"
        # 至少一条 a_overestimates_b
        kinds = [d["kind"] for d in out["drift_samples"]]
        assert "a_overestimates_b" in kinds
    finally:
        _set_mode(bak)


# ───── 6 active + underestimate ──
def test_active_underestimate_fail_minor():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
        out = mod.scan(_write(_UNDERESTIMATE_DRAFT), proj)
        assert out["verdict"] == "FAIL_MINOR"
        kinds = [d["kind"] for d in out["drift_samples"]]
        assert "a_underestimates_b" in kinds
    finally:
        _set_mode(bak)


# ───── 7 manifest anchor 白名单 → 不报 ──
def test_dramatic_irony_anchor_whitelist():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
        manifest = _mk_manifest([
            {"a": "张三", "b": "李四", "topic": "秘密"},
        ])
        out = mod.scan(_write(_UNDERESTIMATE_DRAFT), proj, manifest)
        # underestimate drift 被 anchor 抵消
        assert out["drift_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 8 短稿 skip ──
def test_short_draft_skip():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
        out = mod.scan(_write("短稿。" * 5), proj)
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


# ───── 9 读取失败 ──
def test_read_failure_returns_note():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ───── 10 _mode 非法回落 ──
def test_mode_invalid_falls_back():
    bak = os.environ.get("CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ───── 11 _strip_changes ──
def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\nlog") == "正文。"


def test_strip_changes_plain():
    assert mod._strip_changes("正文。\n---CHANGES---\nlog") == "正文。"


# ───── 12 _cjk_count ──
def test_cjk_count_basic():
    assert mod._cjk_count("你好abc世界") == 4


# ───── 13 事件簇.json.clusters[].locked_facts 读（重接线后真数据源）──
def test_load_fact_refs_from_locked_fact():
    proj = _mk_project(
        characters=[{"name": "张三"}, {"name": "李四"}],
        locked_facts=[{"fact": "藏宝图"}])
    refs = mod._load_fact_refs(proj)
    assert "藏宝图" in refs


# ───── 14 占位 fallback ──
def test_load_fact_refs_fallback():
    proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
    refs = mod._load_fact_refs(proj)
    assert "秘密" in refs


# ───── 15 anchor 加载缺 manifest ──
def test_load_anchors_missing():
    assert mod._load_anchors(None) == set()
    assert mod._load_anchors(Path(tempfile.mkdtemp()) / "no.json") == set()


# ───── 16 CLI exit ──
def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CROSS_CHARACTER_KTH_ORDER_BELIEF_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
    r = _run_cli(_write(_OVERESTIMATE_DRAFT), proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[{"name": "张三"}, {"name": "李四"}])
    r = _run_cli(_write(_LEGAL_DRAFT), proj)
    assert r.returncode == 0, r.stderr


# ───── 17 registry _new=true + 不在 hard_gate ──
def test_registry_entry():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg["scanners"].get("cross_character_kth_order_belief_scanner")
    assert s is not None
    assert s.get("_new") is True
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs
