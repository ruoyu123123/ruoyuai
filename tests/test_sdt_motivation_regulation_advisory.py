# -*- coding: utf-8 -*-
"""sdt_motivation_regulation_advisory R22 W10 Batch-EE·P1 SDT 调节回归测试

确定性·零依赖·零 LLM/零联网。
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
import sdt_motivation_regulation_advisory as mod  # noqa: E402

_TARGET = _SCRIPTS / "sdt_motivation_regulation_advisory.py"
_ENV = "SDT_REGULATION_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(characters=None, profile=None, prev_regulation=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    if profile is not None:
        (proj / "作者风格.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    if prev_regulation is not None:
        (db / "sdt_regulation_profile.json").write_text(
            json.dumps({"by_character": prev_regulation}, ensure_ascii=False), encoding="utf-8")
    return proj


# 张三长稿·intrinsic 主导
_INTRINSIC_DRAFT = "张三觉得有趣。他很喜欢。" * 60


# 张三长稿·external 主导（distance=4 跨度≥2）
_EXTERNAL_DRAFT = "张三被迫接受命令。他不得不去赚钱。" * 60


# external 但带 on-page 触发词
_EXTERNAL_W_TRIGGER = "张三被迫接受命令。他不得不去赚钱。觉醒了。" * 60


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_INTRINSIC_DRAFT), proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_no_character_card_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_INTRINSIC_DRAFT), _mk_project())
        assert out["verdict"] == "PASS"
        assert out["character_count"] == 0
    finally:
        _set_mode(bak)


def test_override_flag_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}],
                           profile={"_sdt_regulation_override": True},
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        out = mod.scan(_write(_EXTERNAL_DRAFT), proj)
        assert out["verdict"] == "PASS"
        assert "反类型豁免" in out["note"]
    finally:
        _set_mode(bak)


def test_shadow_drift_no_report():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": "张三"}],
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        out = mod.scan(_write(_EXTERNAL_DRAFT), proj)
        # shadow 不上报 violations
        assert out["violations"] == []
        assert out["warning"] is None
        # 但记录 drift
        assert out["drift_count"] >= 1
    finally:
        _set_mode(bak)


def test_active_drift_fail_minor():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}],
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        out = mod.scan(_write(_EXTERNAL_DRAFT), proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "SDT_REGULATION_DRIFT"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_drift_with_on_page_trigger_no_advisory():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}],
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        out = mod.scan(_write(_EXTERNAL_W_TRIGGER), proj)
        # 有 on-page trigger → 不报
        assert out["drift_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_distance_distance1_no_drift():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 相邻级（intrinsic↔integrated 距 1）·不触发
        proj = _mk_project(characters=[{"name": "张三"}],
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        text = "张三本来一直天性如此。我是这种人。" * 60
        out = mod.scan(_write(text), proj)
        # 跨度=1（intrinsic→integrated）不报
        assert out["drift_count"] == 0
    finally:
        _set_mode(bak)


def test_no_prev_profile_no_drift():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_EXTERNAL_DRAFT), proj)
        # 无 prev → 仅记录 current·无 drift
        assert out["drift_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write("张三好奇。"), proj)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_regulation_distance_basic():
    assert mod._regulation_distance("intrinsic", "amotivation") == 5
    assert mod._regulation_distance("intrinsic", "intrinsic") == 0
    assert mod._regulation_distance("unknown", "external") == 0


def test_strip_changes_basic():
    assert mod._strip_changes("正文。\n---CHANGES---\nlog") == "正文。"
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\nlog") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("你好abc世") == 3


def test_dominant_regulation_no_match():
    dom, dist = mod._dominant_regulation("无关文本", "张三")
    assert dom == ""
    assert dist == {}


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[{"name": "张三"}])
    r = _run_cli(_write(_INTRINSIC_DRAFT), proj)
    assert r.returncode == 0, r.stderr


def test_main_exit_1_on_drift():
    proj = _mk_project(characters=[{"name": "张三"}],
                       prev_regulation={"张三": {"dominant": "intrinsic"}})
    r = _run_cli(_write(_EXTERNAL_DRAFT), proj)
    assert r.returncode == 1, r.stderr


# ── 🔴 2026-07-03 zero_shot_prototype 模型优先路径测试(W3) ──────────────────
import math  # noqa: E402


def _char_freq_embedding(text, dim=32):
    """确定性 mock embedding（字符频率向量·同 test_macguffin_entanglement_scanner 手法）。"""
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _run_with_mock_embedding(fn):
    """EMBED_BACKEND=mock + monkeypatch embedding_store.compute_embedding 后跑 fn。"""
    bak_eb = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "mock"
    import embedding_store
    import zero_shot_prototype
    orig = embedding_store.compute_embedding
    embedding_store.compute_embedding = _char_freq_embedding
    zero_shot_prototype.clear_cache()
    try:
        return fn()
    finally:
        embedding_store.compute_embedding = orig
        zero_shot_prototype.clear_cache()
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_classify_sdt_window_gate_off_returns_none():
    assert mod._classify_sdt_window("他觉得很有趣") is None


def test_classify_sdt_window_model_hit():
    def _do():
        result = mod._classify_sdt_window("他觉得这件事很有趣，忍不住想多做一会儿")
        assert result == "intrinsic"
    _run_with_mock_embedding(_do)


def test_dominant_regulation_no_model_no_source_key():
    """默认无真后端 → distribution dict 不含 _source（零回归契约）。"""
    dom, dist = mod._dominant_regulation(_INTRINSIC_DRAFT, "张三")
    assert dom == "intrinsic"
    assert "_source" not in dist


def test_dominant_regulation_with_model_injects_source():
    def _do():
        text = "张三" + ("他觉得这件事很有趣，忍不住想多做一会儿" * 3)
        dom, dist = mod._dominant_regulation(text, "张三", window=40)
        assert dom == "intrinsic"
        assert dist.get("_source") == "zero_shot_embedding"
    _run_with_mock_embedding(_do)


def test_sdt_prototypes_cover_all_six_levels():
    assert (set(mod._SDT_REGULATION_PROTOTYPES.keys())
            == set(mod.SDT_LEXICON_PLACEHOLDER["regulation_order"]))
