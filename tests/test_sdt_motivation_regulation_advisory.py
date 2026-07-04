# -*- coding: utf-8 -*-
"""sdt_motivation_regulation_advisory R22 W10 Batch-EE·P1 SDT 调节回归测试

确定性·零依赖·零 LLM/零联网。
"""
import json
import os
import re
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


def _char_freq_embedding_batch(texts, dim=32):
    """_char_freq_embedding 的批量版（供 mock embedding_store.compute_content_embeddings_batch）。"""
    return [_char_freq_embedding(t, dim) for t in texts]


def _run_with_mock_embedding(fn):
    """monkeypatch embedding_store.content_backend_available()→True + compute_content_embeddings_batch
    后跑 fn（2026-07-04：zero_shot_prototype 从风格 EMBED_BACKEND 切到内容后端，mock 面同步换轨）。
    """
    import embedding_store
    import zero_shot_prototype
    orig_avail = embedding_store.content_backend_available
    orig_batch = embedding_store.compute_content_embeddings_batch
    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embeddings_batch = _char_freq_embedding_batch
    zero_shot_prototype.clear_cache()
    try:
        return fn()
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embeddings_batch = orig_batch
        zero_shot_prototype.clear_cache()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _default_content_backend_off():
    """🔴 2026-07-04：content_backend_available() 是文件存在性判定（venv+推理脚本+模型
    目录），不是环境变量——本机若真装了 core/ml/models/content_embed/bge-small-zh-v1.5，
    "不设 EMBED_BACKEND" 不再等于"门控关闭"，本文件几乎每条 active-mode scan() 测试都会
    经 _classify_sdt_window/_dominant_regulations_batch 顺带触发一次真实分类。本 fixture
    把它按文件级默认关闭（同 conftest._isolate_nn_gates 的思路，只是这个门控是函数不是
    环境变量，只能靠 monkeypatch 不能靠 os.environ.pop），_run_with_mock_embedding 内会
    临时覆盖成 True。
    """
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        yield
    finally:
        embedding_store.content_backend_available = orig


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


# ── 🔴 2026-07-03 Wave-4 性能层：scan() 批量分类回归 ─────────────────────────
def test_classify_windows_batch_empty_list():
    assert mod._classify_windows_batch([]) == []


def test_classify_windows_batch_gate_off_returns_all_none():
    labels = mod._classify_windows_batch(["他觉得很有趣", "他被迫做这件事"])
    assert labels == [None, None]


def test_dominant_regulations_batch_matches_single_char_no_backend():
    """gate off（默认词典累加）：批量版与逐角色 _dominant_regulation 结果一致。"""
    text = _INTRINSIC_DRAFT + _EXTERNAL_DRAFT
    batch_result = mod._dominant_regulations_batch(text, ["张三"])
    single = mod._dominant_regulation(text, "张三")
    assert batch_result["张三"] == single


def test_dominant_regulations_batch_unknown_character_empty():
    result = mod._dominant_regulations_batch("无关文本", ["张三", "李四"])
    assert result == {"张三": ("", {}), "李四": ("", {})}


def test_dominant_regulations_batch_multi_character_with_model():
    """真后端下·多角色批量结果应与逐角色调用 _dominant_regulation 完全一致（数学不变）。"""
    def _do():
        text = ("张三" + "他觉得这件事很有趣，忍不住想多做一会儿" * 3
                + "李四" + "他是被逼的，不得不照命令去做" * 3)
        batch_result = mod._dominant_regulations_batch(text, ["张三", "李四"], window=40)
        for ch in ("张三", "李四"):
            single = mod._dominant_regulation(text, ch, window=40)
            assert batch_result[ch] == single
        assert batch_result["张三"][0] == "intrinsic"
        assert batch_result["李四"][0] == "external"
    _run_with_mock_embedding(_do)


def test_scan_calls_classify_batch_exactly_once_across_characters():
    """🔴 Wave-4 核心契约：scan() 对全部角色 × 全部窗口只触发一次 classify_batch。"""
    bak = os.environ.get(_ENV)
    calls = []

    def _recording_classify_batch(texts, label_prototypes, floor=0.5):
        calls.append(list(texts))
        return [None] * len(texts)

    try:
        _set_mode("active")
        import zero_shot_prototype
        orig = zero_shot_prototype.classify_batch
        zero_shot_prototype.classify_batch = _recording_classify_batch
        try:
            proj = _mk_project(characters=[{"name": "张三"}])
            text = _INTRINSIC_DRAFT + _EXTERNAL_DRAFT
            out = mod.scan(_write(text), proj)
        finally:
            zero_shot_prototype.classify_batch = orig
        assert len(calls) == 1
        expected_windows = len(list(re.finditer("张三", text)))
        assert len(calls[0]) == expected_windows
        assert out["character_count"] == 1
    finally:
        _set_mode(bak)
