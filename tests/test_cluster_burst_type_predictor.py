# -*- coding: utf-8 -*-
"""cluster_burst_type_predictor R24 W12 Batch-JJ · P0 STRONG"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_burst_type_predictor as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("BURST_TYPE_MODE", None)
    else:
        os.environ["BURST_TYPE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_brief(cluster_key="001", intended_burst_type="shock"):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    ec = {
        "clusters": [
            {
                "cluster_id": f"cluster_{cluster_key}",
                "intended_burst_type": intended_burst_type,
            }
        ]
    }
    (db / "事件簇.json").write_text(json.dumps(ec, ensure_ascii=False), encoding="utf-8")
    return proj


_PARA = "他静静地走在路上，望着前方未明的去路，思绪散落如雪。\n\n"
# tail 末尾含 shock 关键词
_TAIL_SHOCK = (
    "他僵在原地，瞳孔一缩。\n\n"
    "心头一震，竟然没想到事情会变成这样，整个人都怔住了。\n\n"
    "他猛地起身，浑身发冷。\n\n"
)
# tail 末尾含 laughter 关键词
_TAIL_LAUGHTER = (
    "她扑哧一声笑了出来。\n\n"
    "好笑啊好笑，太逗了，真是搞笑得不行。\n\n"
    "她忍俊不禁地捂嘴。\n\n"
)
_TAIL_FLAT = _PARA * 3

_DRAFT_SHOCK = _PARA * 60 + _TAIL_SHOCK
_DRAFT_LAUGHTER = _PARA * 60 + _TAIL_LAUGHTER
_DRAFT_FLAT = _PARA * 60 + _TAIL_FLAT


def test_off_returns_skeleton():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRAFT_SHOCK), cli_intended="shock")
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRAFT_FLAT), cli_intended="shock")
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_shock_tail_delivered():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_SHOCK), cli_intended="shock")
        # shock 类应有命中
        assert out["scores"]["shock"] > 0.0
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BURST_TYPE_NOT_DELIVERED" not in codes
    finally:
        _set_mode(bak)


def test_active_not_delivered():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        # 要 shock，实际 tail 是 laughter
        out = mod.scan(_write(_DRAFT_LAUGHTER), cli_intended="shock")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BURST_TYPE_NOT_DELIVERED" in codes
    finally:
        _set_mode(bak)


def test_active_mismatch_detected():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        # intended=anticipation·tail laughter 高·intended 完全无命中=NOT_DELIVERED 不是 MISMATCH
        # 测 MISMATCH 需要 intended 有微命中但 top1 是别的高 → 用 shock intended·tail mix
        mixed_tail = "她扑哧一声笑了出来，好笑啊好笑，太逗了，真是搞笑得不行。\n\n" * 5
        out = mod.scan(_write(_PARA * 60 + mixed_tail), cli_intended="shock")
        codes = {v["code"] for v in out.get("violations", [])}
        # shock 0 命中 → NOT_DELIVERED 命中（top1=laughter）
        assert "BURST_TYPE_NOT_DELIVERED" in codes
    finally:
        _set_mode(bak)


def test_active_no_intent_emits_info():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        # 无 cluster brief·无 cli_intended
        out = mod.scan(_write(_DRAFT_SHOCK))
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BURST_TYPE_NO_INTENT" in codes
    finally:
        _set_mode(bak)


def test_active_tail_flat_info():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_FLAT), cli_intended="shock")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BURST_TYPE_TAIL_FLAT" in codes
    finally:
        _set_mode(bak)


def test_active_reads_brief_from_project():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_brief("001", "laughter")
        out = mod.scan(_write(_DRAFT_LAUGHTER), project_root=proj, cluster_key="001")
        assert out["intended_burst_type"] == "laughter"
        # laughter 命中 → 无 NOT_DELIVERED
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BURST_TYPE_NOT_DELIVERED" not in codes
    finally:
        _set_mode(bak)


def test_take_tail_within_range():
    text = "一" * 50 + "\n\n" + "二" * 100 + "\n\n" + "三" * 50
    tail = mod._take_tail(text, cjk_min=80, cjk_max=200)
    assert 50 <= mod._cjk_count(tail) <= 200


def test_score_burst_types_nonzero_for_keyword():
    tail = "他怔住了，瞳孔一缩，倒吸一口冷气。"
    scores = mod._score_burst_types(tail)
    assert scores["shock"] > 0.0


def test_short_draft_skipped():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"), cli_intended="shock")
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("BURST_TYPE_NOT_DELIVERED", "BURST_TYPE_MISMATCH",
              "BURST_TYPE_NO_INTENT", "BURST_TYPE_TAIL_FLAT"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("cluster_burst_type_predictor")
    assert s is not None
    assert s.get("_new") is True


def test_placeholder_lexicon():
    """占位词典须标 _placeholder=true（北极星④）"""
    assert mod._BURST_LEXICONS.get("_placeholder") is True


def test_build_manifest_injects_intended_burst_type():
    """build_manifest 在 BURST_TYPE_MODE=active 时注入 intended_burst_type 字段"""
    src = (_SCRIPTS / "build_manifest.py").read_text(encoding="utf-8")
    assert '"intended_burst_type"' in src
    assert "BURST_TYPE_MODE" in src


# ── 🔴 2026-07-04 zero_shot_prototype 模型优先路径测试(W3·内容后端切轨) ──────
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
    经 _classify_burst_type_model 顺带触发一次真实分类。本 fixture 把它按文件级默认关闭
    （同 conftest._isolate_nn_gates 的思路，只是这个门控是函数不是环境变量，只能靠
    monkeypatch 不能靠 os.environ.pop），_run_with_mock_embedding 内会临时覆盖成 True。
    """
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        yield
    finally:
        embedding_store.content_backend_available = orig


def test_classify_burst_type_model_gate_off_returns_none():
    assert mod._classify_burst_type_model("他瞳孔一缩，僵在原地。") is None


def test_classify_burst_type_model_direct():
    """直接测 _classify_burst_type_model：mock 真后端 → shock 尾部命中 shock 原型。"""
    def _do():
        tail = ("他瞳孔一缩，整个人僵在原地不敢动弹，心头猛地一震，怎么会是这样简直难以置信，"
                "众人倒吸一口冷气都不敢置信眼前的景象脸色瞬间惨白，他浑身发冷僵住了半晌说不出话")
        result = mod._classify_burst_type_model(tail)
        assert result is not None
        assert result["label"] == "shock"
        assert result["source"] == "zero_shot_embedding"
    _run_with_mock_embedding(_do)


def test_active_top1_source_lexicon_by_default():
    """默认无真后端 → top1.source == 'lexicon'（零回归契约）。"""
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_SHOCK), cli_intended="shock")
        assert out["top1"]["source"] == "lexicon"
    finally:
        _set_mode(bak)


def test_active_top1_source_model_when_backend_available():
    """真后端可用且分类置信达标 → top1.source == 'zero_shot_embedding'。

    用自建 draft（尾段单段独立 ≥80 CJK）避免 _DRAFT_SHOCK 的 _TAIL_SHOCK(仅约45 CJK)
    被 _take_tail 拉入前面中性 _PARA 填充稀释语义信号（_take_tail 逐段往前累积到
    cjk_min 才停，前置中性段会混进 tail 干扰 nearest-centroid 分类）。
    """
    bak = os.environ.get("BURST_TYPE_MODE")
    try:
        _set_mode("active")
        draft = _PARA * 60 + (
            "他瞳孔一缩，整个人僵在原地不敢动弹，心头猛地一震，怎么会是这样简直难以置信，"
            "众人倒吸一口冷气都不敢置信眼前的景象脸色瞬间惨白，他浑身发冷僵住了半晌说不出话，"
            "脑子里一片空白，久久无法回神，只觉得天旋地转，浑身僵硬得像块石头\n\n")

        def _do():
            out = mod.scan(_write(draft), cli_intended="shock")
            assert out["top1"]["source"] == "zero_shot_embedding"
            assert out["top1"]["type"] == "shock"

        _run_with_mock_embedding(_do)
    finally:
        _set_mode(bak)


def test_burst_type_prototypes_cover_all_nine_types():
    lexicon_types = {k for k in mod._BURST_LEXICONS if not k.startswith("_")}
    assert set(mod._BURST_TYPE_PROTOTYPES.keys()) == lexicon_types
