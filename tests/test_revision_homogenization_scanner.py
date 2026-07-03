# -*- coding: utf-8 -*-
"""revision_homogenization_scanner 专属测试 (R9 W5 Batch-M·F2·2026-06-20)

钉死：
  · pre_fix/post_fix 三维指纹 (function_word/sent_len_mean/pstdev) 计算
  · delta_sfs >= 2.0 → REVISION_REDUCED_AUTHOR_FIDELITY (active)
  · 作者档基线 baseline_source=author_profile 优先
  · 无作者档 → pair_fallback (两草稿互相对比)
  · --blind-subset 实验旗
  · shadow / off / active 三态
  · 永远 advisory · 不在 HARD_GATE_CODES
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import revision_homogenization_scanner as rh  # noqa: E402
import embedding_store  # noqa: E402


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(*, profile=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = profile or {}
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


def _set_mode(m):
    if m is None:
        os.environ.pop("REVISION_HOMOGENIZATION_MODE", None)
    else:
        os.environ["REVISION_HOMOGENIZATION_MODE"] = m


# 作者风格 (长句·function word 密集): 多 verbose 子句
_AUTHOR_LIKE = (
    "他在山脊的青石阶上独自向上攀登的时候，便看见了那座被雾气笼罩的古寺，"
    "却没想到山门里竟传来低低的钟声。" * 20)

# 中性 LLM 化 (短句·少 function word): 主语+动作流水
_LLM_LIKE = (
    "他爬山。看见古寺。雾气厚。他停下。" * 60)


def test_off_returns_skeleton():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("off")
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_drafts_skip():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        rep = rh.scan(str(_write("短。")), str(_write("更短。")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_homogenization_detected_active():
    """post_fix 风格离作者基线更远 → FAIL"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        # 作者基线: 长句 (mean ~35)·function_word 密集 (~150/千字)
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
            "sentence_length_mean": {"mean": 35.0, "std": 3.0},
            "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "REVISION_REDUCED_AUTHOR_FIDELITY" in codes
        assert rep["verdict"] == "FAIL_MINOR"
        assert rep["delta_sfs"] >= 2.0
    finally:
        _set_mode(bak)


def test_clean_revision_passes():
    """post_fix 风格仍贴作者基线 → PASS"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
            "sentence_length_mean": {"mean": 35.0, "std": 3.0},
            "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
        })
        # 两份都贴近作者风格
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_AUTHOR_LIKE)),
                      project_root=proj)
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_shadow_no_report():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
            "sentence_length_mean": {"mean": 35.0, "std": 3.0},
            "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_pair_fallback_when_no_profile():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)))
        assert rep["baseline_source"] == "pair_fallback"
        # 退路依然能算 pre/post SFS
        assert "pre_fix_sfs" in rep
        assert "post_fix_sfs" in rep
        assert "delta_sfs" in rep
    finally:
        _set_mode(bak)


def test_baseline_author_profile_priority():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
            "sentence_length_mean": {"mean": 35.0, "std": 3.0},
            "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        assert rep["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)


def test_blind_subset_flag():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        # 多段
        text_a = "\n".join([_AUTHOR_LIKE[:200]] * 20)
        text_b = "\n".join([_LLM_LIKE[:200]] * 20)
        rep = rh.scan(str(_write(text_a)), str(_write(text_b)), blind_subset=4)
        assert rep.get("blind_subset_n") == 4
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate_registry():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "REVISION_REDUCED_AUTHOR_FIDELITY" not in hgs


def test_code_not_in_audit_hub_hard_gate():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
    import audit_hub
    assert "REVISION_REDUCED_AUTHOR_FIDELITY" not in audit_hub.HARD_GATE_CODES


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        os.environ["REVISION_HOMOGENIZATION_MODE"] = "junk"
        assert rh._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        rep = rh.scan(str(Path(tempfile.mkdtemp()) / "miss1.txt"),
                      str(Path(tempfile.mkdtemp()) / "miss2.txt"))
        assert "读取失败" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_fingerprint_three_dims():
    fp = rh._fingerprint(_AUTHOR_LIKE)
    assert fp is not None
    assert "function_word_per_1k" in fp
    assert "sent_len_mean" in fp
    assert "sent_len_pstdev" in fp
    assert fp["function_word_per_1k"] >= 0


def test_baseline_partial_dims_works():
    """作者档只声明部分维度·依然可比"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        assert rep["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)


def test_no_change_zero_delta():
    """相同 pre/post → delta_sfs ≈ 0"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_AUTHOR_LIKE)))
        assert abs(rep["delta_sfs"]) < 1e-3
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_delta_sfs_threshold_2():
    """delta_sfs < 2.0 → 不报"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        # 两份非常相似 → delta 小
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 50.0},
            "sentence_length_mean": {"mean": 35.0, "std": 20.0},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_AUTHOR_LIKE[:-100] + "他说。")),
                      project_root=proj)
        # 相似草稿 + 宽 std → 不报
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_blind_subset_zero_no_op():
    """blind_subset=0 → 不裁切"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        text_a = "\n".join([_AUTHOR_LIKE[:200]] * 10)
        rep = rh.scan(str(_write(text_a)), str(_write(text_a)), blind_subset=0)
        # blind_subset 0 不写字段
        assert "blind_subset_n" not in rep
    finally:
        _set_mode(bak)


def test_invalid_profile_falls_back():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": "bad", "std": -1}
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        # 无有效 baseline → 退路
        assert rep["baseline_source"] == "pair_fallback"
    finally:
        _set_mode(bak)


def test_sfs_distance_pair_zero_when_identical():
    fp = rh._fingerprint(_AUTHOR_LIKE)
    assert rh._sfs_distance_pair(fp, fp) == 0.0


# ============================================================================
# 🔴 2026-07-01 语义路径升级测试（真 embedding 后端才跑 · mock embedding_store）
# 钉死两头：默认(无真后端)行为逐字节不变 / mock 真后端后语义路径独立生效。
# ============================================================================

_SEM_BASE_SENTENCE = "他在山脊的青石阶上独自向上攀登。"
_SEM_PRE = _SEM_BASE_SENTENCE * 40                     # 纯净：全部编码方向1（无 MARKER_B）
_SEM_POST = ("MARKER_B" + _SEM_BASE_SENTENCE) * 40     # 每句都带 MARKER_B → 全部编码方向2
# pre/post 的 CJK 内容完全一致（MARKER_B 是 ASCII，不计入 _cjk_count / FUNCTION_WORDS_PATTERN）
# → 数值三维指纹恒等·delta_sfs≈0·确保语义信号独立可观测（不被数值路径掩盖）。


def _fake_embed_factory():
    """确定性假 embedding：文本含 MARKER_B → 方向2，否则方向1（模拟语义差异·测试专用）。"""
    def _fake(text):
        if "MARKER_B" in (text or ""):
            return [0.0, 1.0, 0.0, 0.0]
        return [1.0, 0.0, 0.0, 0.0]
    return _fake


def test_default_no_real_backend_output_unchanged(monkeypatch):
    """无真后端（EMBED_BACKEND 未设/为 hash）→ 逐字节零回归：output 无任何新增语义字段
    （复用 test_homogenization_detected_active 的已验证触发场景）。"""
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    for k in list(os.environ):
        if k.startswith("GEN_EMBED__"):
            monkeypatch.delenv(k, raising=False)
    assert rh._has_real_embedding_backend() is False
    monkeypatch.setenv("REVISION_HOMOGENIZATION_MODE", "active")
    proj = _mk_project(profile={
        "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
        "sentence_length_mean": {"mean": 35.0, "std": 3.0},
        "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
    })
    rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)), project_root=proj)
    expected_keys = {
        "scanner", "schema_version", "mode", "code", "gate_level", "verdict",
        "violations", "warning", "pre_fix_fingerprint", "post_fix_fingerprint",
        "baseline_source", "pre_fix_sfs", "post_fix_sfs", "delta_sfs", "violations_count",
    }
    assert set(rep.keys()) == expected_keys, f"不该有新字段泄漏到默认路径：{set(rep.keys()) - expected_keys}"
    assert rep["verdict"] == "FAIL_MINOR"  # 与 test_homogenization_detected_active 同场景·已验证触发
    assert set(rep["violations"][0].keys()) == {
        "code", "kind", "severity", "message", "delta_sfs",
        "pre_fix_sfs", "post_fix_sfs", "_doc",
    }


def test_has_real_embedding_backend_env_gate(monkeypatch):
    """_has_real_embedding_backend 门控：未设/hash → False；非空非 hash / GEN_EMBED__* → True。"""
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    assert rh._has_real_embedding_backend() is False
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    assert rh._has_real_embedding_backend() is False
    monkeypatch.setenv("EMBED_BACKEND", "mstyle")
    assert rh._has_real_embedding_backend() is True
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    monkeypatch.setenv("GEN_EMBED__default__API_KEY", "x")
    assert rh._has_real_embedding_backend() is True


def test_semantic_helper_none_without_real_backend(monkeypatch):
    """_semantic_pre_post_distance 无真后端 → None（调用方回退数值路径）。"""
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    for k in list(os.environ):
        if k.startswith("GEN_EMBED__"):
            monkeypatch.delenv(k, raising=False)
    assert rh._semantic_pre_post_distance(_SEM_PRE, _SEM_PRE) is None


def test_semantic_path_triggers_independent_of_numeric(monkeypatch):
    """mock 真后端：pre/post 数值指纹恒等(数值路径不触发)·语义方向正交 → 语义路径独立触发。"""
    monkeypatch.setenv("EMBED_BACKEND", "test_semantic")
    monkeypatch.setattr(embedding_store, "compute_embedding", _fake_embed_factory())
    monkeypatch.setattr(embedding_store, "embedding_method", lambda: "test_semantic_backend")
    monkeypatch.setenv("REVISION_HOMOGENIZATION_MODE", "active")
    rep = rh.scan(str(_write(_SEM_PRE)), str(_write(_SEM_POST)))
    assert rep["embedding_backend_active"] is True
    assert rep["embedding_method"] == "test_semantic_backend"
    assert rep["delta_sfs"] < rh.DELTA_SFS_FLOOR, "数值路径本不该触发(pre/post CJK 内容恒等)"
    assert rep["delta_sfs_semantic"] > 0.9, "两个正交单位向量·余弦距离应≈1"
    assert rep["verdict"] == "FAIL_MINOR"
    codes = [v["code"] for v in rep["violations"]]
    assert "REVISION_REDUCED_AUTHOR_FIDELITY" in codes
    assert rep["match_method"] == "embedding_semantic"
    assert rep["violations"][0]["match_method"] == "embedding_semantic"


def test_semantic_path_no_trigger_when_embeddings_identical(monkeypatch):
    """mock 真后端：pre/post 完全相同 → 语义距离=0·不触发·verdict PASS。"""
    monkeypatch.setenv("EMBED_BACKEND", "test_semantic")
    monkeypatch.setattr(embedding_store, "compute_embedding", _fake_embed_factory())
    monkeypatch.setattr(embedding_store, "embedding_method", lambda: "test_semantic_backend")
    monkeypatch.setenv("REVISION_HOMOGENIZATION_MODE", "active")
    rep = rh.scan(str(_write(_SEM_PRE)), str(_write(_SEM_PRE)))
    assert rep["embedding_backend_active"] is True
    assert rep["delta_sfs_semantic"] < 1e-6
    assert rep["verdict"] == "PASS"
    assert "match_method" not in rep


def test_semantic_path_combines_with_numeric_trigger(monkeypatch):
    """mock 真后端 + 数值指纹也触发(沿用 test_homogenization_detected_active 场景)
    → match_method 数值+语义两者兼有。"""
    monkeypatch.setenv("EMBED_BACKEND", "test_semantic")
    monkeypatch.setattr(embedding_store, "compute_embedding", _fake_embed_factory())
    monkeypatch.setattr(embedding_store, "embedding_method", lambda: "test_semantic_backend")
    monkeypatch.setenv("REVISION_HOMOGENIZATION_MODE", "active")
    proj = _mk_project(profile={
        "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
        "sentence_length_mean": {"mean": 35.0, "std": 3.0},
        "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
    })
    post_text = _LLM_LIKE.replace("他", "MARKER_B他")  # 不改变 CJK 数值指纹·只改变 mock 语义方向
    rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(post_text)), project_root=proj)
    assert rep["verdict"] == "FAIL_MINOR"
    assert rep["delta_sfs"] >= rh.DELTA_SFS_FLOOR
    assert rep["delta_sfs_semantic"] > 0.9
    assert rep["match_method"] == "numeric_fingerprint+embedding_semantic"
    assert rep["violations"][0]["match_method"] == "numeric_fingerprint+embedding_semantic"


def test_semantic_floor_env_override(monkeypatch):
    """env REVISION_HOMOGENIZATION_EMBED_FLOOR 覆盖 > 默认值·非法值回退默认。"""
    monkeypatch.delenv("REVISION_HOMOGENIZATION_EMBED_FLOOR", raising=False)
    assert rh._semantic_floor() == rh.DEFAULT_DELTA_SFS_SEMANTIC_FLOOR
    monkeypatch.setenv("REVISION_HOMOGENIZATION_EMBED_FLOOR", "0.3")
    assert rh._semantic_floor() == 0.3
    monkeypatch.setenv("REVISION_HOMOGENIZATION_EMBED_FLOOR", "not_a_number")
    assert rh._semantic_floor() == rh.DEFAULT_DELTA_SFS_SEMANTIC_FLOOR


def test_embedding_centroid_none_on_empty_text():
    assert rh._embedding_centroid("") is None
    assert rh._embedding_centroid("   ") is None


def test_semantic_code_not_in_hard_gate_registry():
    """语义路径升级后仍是同一个 ISSUE_CODE·不新增 code·hard_gate 名单校验依旧成立。"""
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "REVISION_REDUCED_AUTHOR_FIDELITY" not in hgs
