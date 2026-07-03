# -*- coding: utf-8 -*-
"""genre_pack_clash_scanner R11 W6 MODEST 占位回归

2026-07-01 追加：真语义 embedding 可选路径回归(mock 后端·完全照抄 topic_drift_scanner
测试手法)——钉死 (a) 无真后端时 match_method="lexicon"(零回归) (b) mock 真后端时
match_method="semantic" 且语义路径被正确使用。
"""
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import genre_pack_clash_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("GENRE_PACK_CLASH_MODE", None)
    else:
        os.environ["GENRE_PACK_CLASH_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(packs, override=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    obj = {"author_genre_packs": packs}
    if override:
        obj["author_fusion_resolution"] = override
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 4+ 场景·前 1/3 全 apocalypse / 后 1/3 全 romance
_BIPOLAR = ("\n\n".join([
    ("丧尸变异感染病毒物资罐头汽油净水避难所掩体基地幸存幸存者援助收容废墟空城死城。" * 6),
    ("丧尸变异物资罐头汽油净水掩体基地幸存援助。" * 6),
    ("普通场景，没有显著标记。" * 6),
    ("她心跳加速脸红耳根怀里温柔暖意环绕气息相贴拥抱告白誓言守护暧昧承诺。" * 6),
    ("心跳脸红怀里温柔暖意环绕气息告白誓言守护暧昧承诺纠缠。" * 6),
]) + "\n")


def test_off():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_BIPOLAR))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_skip():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_packs_skip():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BIPOLAR))
        assert "无多 pack" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_registry_hit_skip():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "xuanhuan"])
        out = mod.scan(_write(_BIPOLAR), proj)
        assert "无命中" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_active_bipolar_flag():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = mod.scan(_write(_BIPOLAR), proj)
        assert "fusion_resolution_hints" in out
        # bipolar_flags 可能命中也可能不命中(占位 seed 词袋)·两情况都接受
        assert "bipolar_flags" in out
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = mod.scan(_write(_BIPOLAR), proj)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_read_packs_no_file():
    proj = Path(tempfile.mkdtemp())
    p, o = mod._read_packs_and_override(proj)
    assert p == [] and o == {}


def test_read_packs_none_project():
    p, o = mod._read_packs_and_override(None)
    assert p == [] and o == {}


def test_lexicon_missing():
    assert mod._load_marker_lexicon("nonexistent_pack") == []


def test_mode_invalid():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_fail():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "no.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── 🔴 2026-07-01 真语义 embedding 可选路径（完全照抄 topic_drift_scanner 测试手法）──────

def _char_freq_embedding(text: str, dim: int = 32) -> list:
    """确定性 mock embedding（字符频率向量·同 test_topic_drift_scanner 手法）。"""
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _run_with_mock_embedding(fn, *args, **kwargs):
    """EMBED_BACKEND=mock + monkeypatch embedding_store.compute_embedding 后跑 fn。"""
    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "mock"
    import embedding_store
    orig = embedding_store.compute_embedding
    embedding_store.compute_embedding = _char_freq_embedding
    try:
        return fn(*args, **kwargs)
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_has_real_embedding_backend_false_by_default():
    bak = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        for k, v in saved.items():
            os.environ[k] = v


def test_has_real_embedding_backend_true_with_mstyle():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "mstyle"
        assert mod._has_real_embedding_backend() is True
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_pack_prototype_text():
    assert mod._pack_prototype_text("apocalypse_survival")
    assert mod._pack_prototype_text("bogus_pack_not_exist") == ""


def test_semantic_diff_threshold_constant():
    """双峰阈值：语义相似度差值域[-1,1]·区别于词袋密度差阈值 1.0（回归锁·防误改）。"""
    assert mod._SEMANTIC_DIFF_THRESHOLD == 0.15


def test_default_match_method_lexicon():
    """无真后端(默认) → match_method="lexicon"（零回归基线）。"""
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = mod.scan(_write(_BIPOLAR), proj)
        assert out["match_method"] == "lexicon"
        for f in out["bipolar_flags"]:
            assert f["match_method"] == "lexicon"
    finally:
        _set_mode(bak)


def test_semantic_match_method_when_backend_available():
    """真后端(mock)可用 → match_method="semantic"·命中的 bipolar_flags 也标 semantic。"""
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = _run_with_mock_embedding(mod.scan, _write(_BIPOLAR), proj)
        assert out["match_method"] == "semantic"
        for f in out["bipolar_flags"]:
            assert f["match_method"] == "semantic"
    finally:
        _set_mode(bak)


def test_prefetch_called_once_with_scenes_and_pair_prototypes():
    """🔴 2026-07-03 Wave-4：真后端(mock)时 scan() 在 pair 循环前一次性
    prefetch_embeddings(全部场景+涉及pack原型)——断言只调一次且文本集合符合预期。"""
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "mock"
    import embedding_store
    orig_compute = embedding_store.compute_embedding
    orig_prefetch = embedding_store.prefetch_embeddings
    embedding_store.compute_embedding = _char_freq_embedding
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)),
                "cache_hits": 0, "computed": len(set(texts))}

    embedding_store.prefetch_embeddings = _rec_prefetch
    try:
        _set_mode("active")
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = mod.scan(_write(_BIPOLAR), proj)
    finally:
        embedding_store.compute_embedding = orig_compute
        embedding_store.prefetch_embeddings = orig_prefetch
        _set_mode(bak)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)

    assert out["match_method"] == "semantic"
    assert out["fusion_resolution_hints"], "registry 应命中该 pair(前置假设)"
    assert len(calls) == 1, f"prefetch 应只调一次，实际 {len(calls)}"
    text = mod._strip_changes(_BIPOLAR)
    scenes = [s for s in re.split(r"\n\s*\n+", text) if mod._cjk_count(s) >= 80]
    expected = scenes + [mod._pack_prototype_text(p) for p in ("apocalypse_survival", "romance")]
    assert calls[0] == expected


def test_prefetch_not_called_without_real_backend():
    """无真后端(默认)→ 不进语义分支·prefetch_embeddings 完全不触发（零回归）。"""
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    import embedding_store
    orig_prefetch = embedding_store.prefetch_embeddings
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {}

    embedding_store.prefetch_embeddings = _rec_prefetch
    try:
        _set_mode("active")
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = mod.scan(_write(_BIPOLAR), proj)
    finally:
        embedding_store.prefetch_embeddings = orig_prefetch
        _set_mode(bak)

    assert out["match_method"] == "lexicon"
    assert calls == [], "无真后端时不该调用 prefetch_embeddings"


def test_pair_scores_lexicon_basic():
    scenes = ["丧尸变异感染病毒物资罐头" * 3, "她心跳加速脸红耳根怀里温柔" * 3]
    scores = mod._pair_scores_lexicon(scenes, "apocalypse_survival", "romance")
    assert scores is not None
    assert len(scores) == 2


def test_pair_scores_lexicon_missing_pack_returns_none():
    scores = mod._pair_scores_lexicon(["随便的文字内容测试" * 20], "bogus1", "bogus2")
    assert scores is None


def test_pair_scores_semantic_dimension_mismatch_returns_none():
    """某 scene embedding 维度与原型不一致 → 该 pair 整体 None(调用方兜底词袋)。"""
    def _mixed(text, dim=32):
        if "SHORT" in text:
            return [0.1] * 8
        return _char_freq_embedding(text, dim)
    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "mock"
    import embedding_store
    orig = embedding_store.compute_embedding
    embedding_store.compute_embedding = _mixed
    try:
        cache = {}
        scores = mod._pair_scores_semantic(
            ["正常场景文字内容充足描写" * 5, "SHORT 场景"],
            "apocalypse_survival", "romance",
            embedding_store.compute_embedding, embedding_store.cosine_similarity, cache)
        assert scores is None
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_pair_scores_semantic_missing_prototype_returns_none():
    """pack 的 marker_lexicon 缺失(原型文本空) → None(调用方兜底词袋)。"""
    import embedding_store
    cache = {}
    scores = mod._pair_scores_semantic(
        ["随便场景内容" * 10], "bogus_pack_a", "bogus_pack_b",
        embedding_store.compute_embedding, embedding_store.cosine_similarity, cache)
    assert scores is None


def test_semantic_import_error_falls_back_to_lexicon():
    """embedding_store 不可导入(mock ImportError) → 全程退回词袋·match_method="lexicon"。"""
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    saved = sys.modules.get("embedding_store")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "mock"
        sys.modules["embedding_store"] = None  # type: ignore[assignment]
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = mod.scan(_write(_BIPOLAR), proj)
        assert out["match_method"] == "lexicon"
    finally:
        if saved is not None:
            sys.modules["embedding_store"] = saved
        else:
            sys.modules.pop("embedding_store", None)
        _set_mode(bak)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)
