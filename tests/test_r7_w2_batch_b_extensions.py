"""R7 W2 Batch-B 落地回归（5 条 advisory · 新字段 + genre pack + writer 注入）：

  · Proust olfactory_anchors（cluster brief 新字段·与 foreshadowing 槽并列·advisory）
  · Nummenmaa emotion_body_topography_hint（manifest 顶层·env shadow/active/off·作者档让位）
  · cosmic_horror genre pack（5 维 judge_dims + writer_directives + _canonical_genres 注册）
  · Focalization Type×Facet 二轴矩阵（manifest 顶层·env shadow/active/off）
  · 章末钩子 11 型 taxonomy 已在 test_hook_taxonomy_r7_extension.py 单独覆盖

全部 advisory · 零 hard_gate · 零依赖纯标准库 · 不污染既有测试。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import scaffold_genre_packs as gp  # noqa: E402
import build_manifest as bm  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────
# Action 3：cosmic_horror genre pack 注册 + 结构 + advisory 边界
# ──────────────────────────────────────────────────────────────────────────

def test_cosmic_horror_registered_in_canonical():
    """cosmic_horror 加入 _canonical_genres 列表（验证脚本入口·防 _verify 漏检）。"""
    cg = gp.canonical_genres()
    assert "cosmic_horror" in cg, cg


def test_cosmic_horror_pack_has_required_fields():
    """cosmic_horror 包结构完整：judge_dims + writer_directives + _label。"""
    pk = gp.get_pack("cosmic_horror")
    assert isinstance(pk, dict) and pk, "cosmic_horror 应有包"
    assert pk.get("_label")
    jd = pk.get("judge_dims")
    assert isinstance(jd, dict) and jd
    # 5 维 judge_dims：CH1 理智衰退/CH2 不可名状/CH3 知识即代价/CH4 宇宙尺度/CH5 不可靠叙述
    for prefix in ("CH1", "CH2", "CH3", "CH4", "CH5"):
        assert any(k.startswith(prefix) for k in jd), f"缺 {prefix}_* 维度: {list(jd)}"
    wd = pk.get("writer_directives")
    assert isinstance(wd, list) and len(wd) >= 5, "应至少 5 条 writer 工艺指令"


def test_cosmic_horror_pack_verify_passes():
    """_verify 通过：cosmic_horror 在 canonical 内·judge_dims 是 dict（结构合法）。"""
    assert gp._verify() == 0


def test_cosmic_horror_writer_directives_contain_unnamable_discipline():
    """writer_directives 包含『不可名状纪律』核心工艺（防写时丢失）。"""
    wd = gp.get_writer_directives("cosmic_horror")
    assert any("不可名状" in d or "禁直白命名" in d for d in wd), wd


# ──────────────────────────────────────────────────────────────────────────
# Action 2：Nummenmaa emotion_body_topography_hint 三态 + 作者档让位
# ──────────────────────────────────────────────────────────────────────────

def _mk_proj(tmp: Path, style_overrides=None, book="测试书") -> Path:
    proj = tmp / book
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    style = {"author": "x", "quantitative": {"sentence_length": {"mean": 28}}}
    if style_overrides:
        # merge nested dicts shallowly
        for k, v in style_overrides.items():
            if isinstance(v, dict) and isinstance(style.get(k), dict):
                style[k].update(v)
            else:
                style[k] = v
    (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return proj


def _set_env(key, value):
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def test_emotion_topography_default_shadow_none():
    """默认 env=shadow → None（不注入·零回归）。"""
    _set_env("EMOTION_TOPOGRAPHY_INJECT_MODE", None)
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        s = bm.DatabaseScanner(proj, 1)
        assert bm._collect_emotion_body_topography_hint(s) is None


def test_emotion_topography_active_returns_payload():
    """active → 注入完整 payload（含 8 基本 + 6 复杂情绪 topography）。"""
    _set_env("EMOTION_TOPOGRAPHY_INJECT_MODE", "active")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = _mk_proj(Path(d))
            s = bm.DatabaseScanner(proj, 1)
            r = bm._collect_emotion_body_topography_hint(s)
            assert r is not None
            assert r["gate_level"] == "advisory"
            tp = r["topography_lookup"]
            # 8 基本情绪
            for emo in ("anger", "fear", "happiness", "sadness", "surprise",
                        "disgust", "neutral", "anxiety"):
                assert emo in tp, f"缺基本情绪 {emo}"
            # 6 复杂情绪
            for emo in ("love", "depression", "contempt", "pride", "shame", "guilt"):
                assert emo in tp, f"缺复杂情绪 {emo}"
            # 每情绪 ≥ 1 身体部位
            assert all(isinstance(v, list) and v for v in tp.values())
            assert r["_authority"] == "GENERAL"
    finally:
        _set_env("EMOTION_TOPOGRAPHY_INJECT_MODE", None)


def test_emotion_topography_off_returns_none():
    _set_env("EMOTION_TOPOGRAPHY_INJECT_MODE", "off")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = _mk_proj(Path(d))
            s = bm.DatabaseScanner(proj, 1)
            assert bm._collect_emotion_body_topography_hint(s) is None
    finally:
        _set_env("EMOTION_TOPOGRAPHY_INJECT_MODE", None)


def test_emotion_topography_authority_fallback_when_author_has_physio_distribution():
    """作者档若规定 quantitative.physio_cue_distribution → _authority=FALLBACK 让位作者档。"""
    _set_env("EMOTION_TOPOGRAPHY_INJECT_MODE", "active")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = _mk_proj(Path(d), style_overrides={
                "quantitative": {"physio_cue_distribution": {"facial": 0.3, "hand": 0.4}}
            })
            s = bm.DatabaseScanner(proj, 1)
            r = bm._collect_emotion_body_topography_hint(s)
            assert r is not None
            assert r["_authority"] == "FALLBACK", r
            assert "_note" in r
    finally:
        _set_env("EMOTION_TOPOGRAPHY_INJECT_MODE", None)


# ──────────────────────────────────────────────────────────────────────────
# Action 5：Focalization Type×Facet 二轴矩阵
# ──────────────────────────────────────────────────────────────────────────

def test_focalization_matrix_default_shadow_none():
    _set_env("FOCALIZATION_INJECT_MODE", None)
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        s = bm.DatabaseScanner(proj, 1)
        assert bm._collect_focalization_matrix(s, 1) is None


def test_focalization_matrix_active_returns_payload():
    _set_env("FOCALIZATION_INJECT_MODE", "active")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = _mk_proj(Path(d))
            s = bm.DatabaseScanner(proj, 1)
            r = bm._collect_focalization_matrix(s, 1)
            assert r is not None
            assert r["gate_level"] == "advisory"
            mtx = r["matrix"]
            # 4 types
            for t in ("zero", "internal", "external", "variable"):
                assert t in mtx["types"], t
            # 3 facets
            for f in ("perceptual", "psychological", "ideological"):
                assert f in mtx["facets"], f
            assert mtx.get("advisory")
    finally:
        _set_env("FOCALIZATION_INJECT_MODE", None)


def test_focalization_matrix_off_returns_none():
    _set_env("FOCALIZATION_INJECT_MODE", "off")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = _mk_proj(Path(d))
            s = bm.DatabaseScanner(proj, 1)
            assert bm._collect_focalization_matrix(s, 1) is None
    finally:
        _set_env("FOCALIZATION_INJECT_MODE", None)


# ──────────────────────────────────────────────────────────────────────────
# Action 1：Proust olfactory_anchors cluster brief 字段
# ──────────────────────────────────────────────────────────────────────────

def test_cluster_brief_returns_olfactory_anchors_field():
    """cluster brief 返回的 dict 在 mode=on 时含 olfactory_anchors 字段（默认空列表）。

    走源码静态检查：mode=on 分支的 return dict 必含此字段（防字段被未来重构掉）。
    """
    src = (_ROOT / "core" / "scripts" / "build_manifest.py").read_text(encoding="utf-8")
    # 关键标识：cluster brief return dict 内有 olfactory_anchors（与 anchor_props/foreshadowing_to_plant 并列）
    assert '"olfactory_anchors"' in src, "build_manifest cluster brief 缺 olfactory_anchors 字段"


# ──────────────────────────────────────────────────────────────────────────
# 北极星⑤：5 新机制全 advisory · 零 hard_gate
# ──────────────────────────────────────────────────────────────────────────

def test_no_new_hard_gate_codes_introduced():
    """R7 W2 Batch-B 5 条全 advisory · scanner_registry 的 hard_gate_codes 严格无新增。"""
    reg = json.loads((_ROOT / "core" / "scripts" / "scanner_registry.json").read_text(encoding="utf-8"))
    hg = set(reg.get("hard_gate_codes", []))
    # R7 W2 Batch-B 不引入任何新 hard_gate code
    forbidden_new = {"OLFACTORY_ANCHOR_MISSING", "EMOTION_TOPOGRAPHY_MISMATCH",
                     "FOCALIZATION_FACET_WHIPLASH", "COSMIC_HORROR_NAMED",
                     "HOOK_TYPE_INVALID"}
    intersection = hg & forbidden_new
    assert not intersection, f"R7 W2 Batch-B 禁引入新 hard_gate code，命中: {intersection}"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"[r7_w2_batch_b] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
