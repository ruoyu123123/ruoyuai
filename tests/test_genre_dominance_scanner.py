# -*- coding: utf-8 -*-
"""genre_dominance_scanner R11 W6 MODEST 回归(确定性·零依赖)

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
import genre_dominance_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("GENRE_DOMINANCE_MODE", None)
    else:
        os.environ["GENRE_DOMINANCE_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(packs, primary=None, dominance=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"author_genre_packs": packs}, ensure_ascii=False),
        encoding="utf-8")
    if primary:
        (proj / "_数据库" / "fusion_declaration.json").write_text(
            json.dumps({"primary": primary, "dominance_ratio": dominance or {}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 多场景草稿(段落间空行切场景) — 重 romance 标记 + 弱 xianxia 标记
_INVERSION_DRAFT = ("\n\n".join([
    "她心跳加速，脸红耳根，怀里温柔暖意环绕，气息相贴。" * 16,
    "他低语告白，誓言深深守护，唇角微扬眼神湿润纠缠。" * 16,
    "拥抱在怀，胸膛贴近，亲吻暧昧承诺再次重逢。" * 16,
    "灵气一闪，飞剑掠空。仙气漫漫。" * 4,
]) + "\n")

_STARVED_DRAFT = ("\n\n".join([
    "她心跳加速，脸红耳根，怀里温柔暖意环绕。" * 16,
    "他低语告白，誓言深深守护，唇角微扬。" * 16,
    "拥抱在怀，胸膛贴近，亲吻暧昧承诺。" * 16,
    "灵气一闪。日落山头。" * 2,
    "他笑着望她，心上人，承诺，定情信物。" * 16,
]) + "\n")

# 用于无 packs 场景的"长草稿"
_LONG_PLAIN = "她心跳脸红怀里温柔暖意环绕气息相贴。\n\n他低语告白誓言深深守护。\n\n" * 30


def test_off():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_INVERSION_DRAFT))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_skip():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_packs_skip():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_LONG_PLAIN))
        assert "无多 pack" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_single_pack_skip():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["romance"])
        out = mod.scan(_write(_LONG_PLAIN), proj)
        assert "无多 pack" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_active_inversion_flag():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_INVERSION_DRAFT), proj)
        assert out["per_pack_total"]["romance"] > out["per_pack_total"]["xianxia"]
        codes = [f["code"] for f in out["flags"]]
        assert "GENRE_DOMINANCE_INVERSION" in codes or "GENRE_PRIMARY_STARVED" in codes
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_active_primary_starved():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_STARVED_DRAFT), proj)
        codes = [f["code"] for f in out["flags"]]
        assert "GENRE_PRIMARY_STARVED" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_INVERSION_DRAFT), proj)
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_packs_no_file():
    proj = Path(tempfile.mkdtemp())
    assert mod._read_genre_packs(proj) == []
    assert mod._read_genre_packs(None) == []


def test_load_lexicon_missing_pack():
    assert mod._load_marker_lexicon("bogus_pack") == []


def test_gini_zero_all_zero():
    assert mod._gini([0, 0, 0]) == 0.0


def test_gini_uniform():
    g = mod._gini([1, 1, 1, 1])
    assert g < 0.05


def test_few_scenes_skip():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        # 单段·无空行
        out = mod.scan(_write("她心跳加速脸红耳根怀里温柔。" * 60), proj)
        assert "场景数 <2" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
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


def test_pack_prototype_text_from_lexicon():
    text = mod._pack_prototype_text("xianxia")
    assert text  # xianxia lexicon 非空 → 原型文本非空
    assert "、" in text


def test_pack_prototype_text_missing_pack_empty():
    assert mod._pack_prototype_text("bogus_pack_not_exist") == ""


def test_default_match_method_is_lexicon():
    """无真后端(默认) → match_method="lexicon"（零回归基线）。"""
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_INVERSION_DRAFT), proj)
        assert out["match_method"] == "lexicon"
        for f in out["violations"]:
            assert f["match_method"] == "lexicon"
    finally:
        _set_mode(bak)


def test_semantic_match_method_when_backend_available():
    """真后端(mock)可用 → match_method="semantic"·per_scene_density 用余弦相似度(clamp [0,1])。"""
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = _run_with_mock_embedding(mod.scan, _write(_INVERSION_DRAFT), proj)
        assert out["match_method"] == "semantic"
        for ds in out["per_scene_density"]:
            for v in ds.values():
                assert 0.0 <= v <= 1.0
    finally:
        _set_mode(bak)


def test_semantic_violations_carry_match_method():
    """active 模式下 violations 携带 match_method 字段(语义/字面来源可追溯)。"""
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = _run_with_mock_embedding(mod.scan, _write(_STARVED_DRAFT), proj)
        for v in out["violations"]:
            assert v["match_method"] == "semantic"
    finally:
        _set_mode(bak)


def test_semantic_import_error_falls_back_to_lexicon():
    """embedding_store 不可导入(mock ImportError) → 全程退回词袋·match_method="lexicon"。"""
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    saved = sys.modules.get("embedding_store")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "mock"
        sys.modules["embedding_store"] = None  # type: ignore[assignment]
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_INVERSION_DRAFT), proj)
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


def test_prefetch_called_once_with_scenes_and_prototypes():
    """🔴 2026-07-03 Wave-4：真后端(mock)时 scan() 在 scene 循环前一次性
    prefetch_embeddings(全部场景+各pack原型)——断言只调一次且文本集合符合预期。"""
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
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
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_INVERSION_DRAFT), proj)
    finally:
        embedding_store.compute_embedding = orig_compute
        embedding_store.prefetch_embeddings = orig_prefetch
        _set_mode(bak)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)

    assert out["match_method"] == "semantic"
    assert len(calls) == 1, f"prefetch 应只调一次，实际 {len(calls)}"
    text = mod._strip_changes(_INVERSION_DRAFT)
    scenes = [s for s in re.split(r"\n\s*\n+", text) if mod._cjk_count(s) >= 80]
    expected = scenes + [mod._pack_prototype_text(p) for p in ("xianxia", "romance")]
    assert calls[0] == expected


def test_prefetch_not_called_without_real_backend():
    """无真后端(默认)→ 不进语义分支·prefetch_embeddings 完全不触发（零回归）。"""
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    import embedding_store
    orig_prefetch = embedding_store.prefetch_embeddings
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {}

    embedding_store.prefetch_embeddings = _rec_prefetch
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_INVERSION_DRAFT), proj)
    finally:
        embedding_store.prefetch_embeddings = orig_prefetch
        _set_mode(bak)

    assert out["match_method"] == "lexicon"
    assert calls == [], "无真后端时不该调用 prefetch_embeddings"


def test_semantic_dimension_mismatch_falls_back_per_scene():
    """场景 embedding 维度与原型不一致 → 该 scene/pack 单独退回词袋(不影响其他)·不崩。"""
    def _mixed(text, dim=32):
        if "MISMATCH" in text:
            return [0.1] * 8
        return _char_freq_embedding(text, dim)
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "mock"
    import embedding_store
    orig = embedding_store.compute_embedding
    embedding_store.compute_embedding = _mixed
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        draft = ("\n\n".join([
            "MISMATCH 这段会触发维度不一致" * 40,
            "她心跳加速脸红耳根怀里温柔暖意环绕气息相贴" * 40,
        ]) + "\n")
        out = mod.scan(_write(draft), proj)
        # 不崩·仍产出合法结构
        assert "per_scene_density" in out
        assert len(out["per_scene_density"]) == 2
    finally:
        embedding_store.compute_embedding = orig
        _set_mode(bak)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)
