# -*- coding: utf-8 -*-
"""agenda_drift_scanner R24 W12 Batch-KK · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import agenda_drift_scanner as mod  # noqa: E402
import writer_intent_anchor as wia  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("AGENDA_DRIFT_MODE", None)
    else:
        os.environ["AGENDA_DRIFT_MODE"] = m


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_anchor(want="救妹妹", antagonist="无脸者",
                            stake="灵魂被吞", tone_word="冷峻"):
    proj = Path(tempfile.mkdtemp())
    wia.cmd_create(SimpleNamespace(
        project=str(proj), cluster_key="001",
        want=want, antagonist=antagonist,
        stake=stake, tone_word=tone_word, force=False))
    return proj


def test_off_returns_skeleton():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write_draft("xxx"), None, "001")
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_anchor_emits_info_active():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        out = mod.scan(_write_draft("正文" * 100), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "WRITER_INTENT_NO_ANCHOR" in codes
    finally:
        _set_mode(bak)


def test_drift_emits_advisory():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_anchor(
            want="救妹妹", antagonist="无脸者",
            stake="灵魂被吞", tone_word="冷峻")
        # 草稿与 4 字段完全无关
        draft_text = "甜蜜蜜的婚礼在春天举行。" * 200
        out = mod.scan(_write_draft(draft_text), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "WRITER_INTENT_AGENDA_DRIFT" in codes
        assert len(out["drift_fields"]) > 0
    finally:
        _set_mode(bak)


def test_aligned_emits_ok():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_anchor(
            want="救妹妹", antagonist="无脸者",
            stake="灵魂被吞", tone_word="冷峻")
        # 草稿包含全部 4 字段字符
        draft_text = "救妹妹·无脸者·灵魂被吞·冷峻\n" * 200
        out = mod.scan(_write_draft(draft_text), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "WRITER_INTENT_OK" in codes
        assert "WRITER_INTENT_AGENDA_DRIFT" not in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violations():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project_with_anchor()
        out = mod.scan(_write_draft("xxx" * 100), proj, "001")
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_coverage_full_match():
    assert mod._coverage("ab", "..a..b..") == 1.0


def test_coverage_no_match():
    assert mod._coverage("救妹妹", "甜蜜婚礼") == 0.0


def test_coverage_partial():
    # 救妹妹 = {救,妹}（妹去重）·draft 中只有"妹" → 0.5
    score = mod._coverage("救妹妹", "妹的故事")
    assert 0.4 < score < 0.6


def test_coverage_empty_field():
    assert mod._coverage("", "anything") == 1.0


def test_codes_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("WRITER_INTENT_AGENDA_DRIFT", "WRITER_INTENT_NO_ANCHOR",
              "WRITER_INTENT_OK"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("agenda_drift_scanner")
    assert s is not None
    assert s.get("_new") is True


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_drift_threshold_constant():
    assert mod.DRIFT_THRESHOLD == 0.62


def test_audit_hub_integrates_scanner():
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "agenda_drift_scanner" in src
    assert "WRITER_INTENT_AGENDA_DRIFT" in src


# ═══════════════════════════════════════════════════════════════════════════
# 🔴 2026-07-01 语义覆盖率补齐（embedding cosine 替代字面 Jaccard · 同义改写零容错）
# ═══════════════════════════════════════════════════════════════════════════

def test_has_real_embedding_backend_false_by_default():
    """未配置 EMBED_BACKEND → False（默认 hash 袋·无真语义）。"""
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb


def test_has_real_embedding_backend_false_when_hash():
    """EMBED_BACKEND=hash → 仍 False（不拿 hash 假语义袋冒充）。"""
    old_eb = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "hash"
        assert mod._has_real_embedding_backend() is False
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_has_real_embedding_backend_true_when_embed_backend_set():
    old_eb = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "local"
        assert mod._has_real_embedding_backend() is True
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_default_match_method_is_char_jaccard_and_scores_unchanged():
    """🔴 零回归锁：无真 embedding 后端（默认）→ match_method=char_jaccard，field_scores
    与直接调用 _coverage() 逐字节一致（语义路径是"加"上去的，不是"换"掉字面路径）。"""
    bak_mode = os.environ.get("AGENDA_DRIFT_MODE")
    bak_eb = os.environ.pop("EMBED_BACKEND", None)
    try:
        _set_mode("active")
        fields = {"want": "救妹妹", "antagonist": "无脸者",
                  "stake": "灵魂被吞", "tone_word": "冷峻"}
        proj = _mk_project_with_anchor(**fields)
        draft_text = "甜蜜蜜的婚礼在春天举行。" * 200
        out = mod.scan(_write_draft(draft_text), proj, "001")
        assert out["match_method"] == "char_jaccard"
        stripped = mod._strip_changes(draft_text)
        expected = {f: round(mod._coverage(v, stripped), 4) for f, v in fields.items()}
        assert out["field_scores"] == expected, (out["field_scores"], expected)
    finally:
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb


def test_semantic_path_replaces_jaccard_for_synonym():
    """真 embedding 后端 mock：字面零重叠的同义改写（意图卡 want="决战"，草稿写"殊死搏杀"·
    两词共 0 个相同汉字）应被余弦相似度识别为一致（字面 Jaccard 会误判 drift）——验证语义
    路径被正确使用。"""
    bak_mode = os.environ.get("AGENDA_DRIFT_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "mock"   # 触发 _has_real_embedding_backend()=True
        proj = _mk_project_with_anchor(
            want="决战", antagonist="无脸者", stake="灵魂被吞", tone_word="冷峻")
        # "决战"={决,战} vs "殊死搏杀"={殊,死,搏,杀} 无共享汉字 → 字面 Jaccard 覆盖率必为 0.0
        # （同义改写零容错场景：字面法完全无法识别两者语义相同）
        draft_text = "殊死搏杀·无脸者·灵魂被吞·冷峻\n" * 50
        stripped = mod._strip_changes(draft_text)
        assert mod._coverage("决战", stripped) == 0.0   # 前置断言：确认字面法确实零重叠

        import embedding_store
        orig = embedding_store.compute_embedding

        def _mock_embed(text):
            if "决战" in text or "殊死搏杀" in text:
                return [1.0, 0.0]
            return [0.0, 1.0]

        embedding_store.compute_embedding = _mock_embed
        try:
            out = mod.scan(_write_draft(draft_text), proj, "001")
        finally:
            embedding_store.compute_embedding = orig

        assert out["match_method"] == "embedding_cosine", out
        assert out["field_scores"]["want"] == 1.0, out["field_scores"]
        assert "want" not in {d["field"] for d in out["drift_fields"]}, out["drift_fields"]
    finally:
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_prefetch_called_once_with_draft_and_fields(monkeypatch):
    """🔴 2026-07-03 Wave-4：真后端路径下 scan() 开头一次性 prefetch_embeddings(draft+4字段)，
    而不是逐条各自触发后端计算——断言只调一次且文本集合符合预期。"""
    import embedding_store
    monkeypatch.setenv("AGENDA_DRIFT_MODE", "active")
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    monkeypatch.setattr(embedding_store, "compute_embedding", lambda t: [1.0, 0.0])
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)),
                "cache_hits": 0, "computed": len(set(texts))}

    monkeypatch.setattr(embedding_store, "prefetch_embeddings", _rec_prefetch)
    proj = _mk_project_with_anchor(
        want="决战", antagonist="无脸者", stake="灵魂被吞", tone_word="冷峻")
    draft_text = "殊死搏杀·无脸者·灵魂被吞·冷峻\n" * 50
    mod.scan(_write_draft(draft_text), proj, "001")

    assert len(calls) == 1, f"prefetch 应只调一次，实际 {len(calls)}"
    stripped = mod._strip_changes(draft_text)
    assert calls[0] == [stripped, "决战", "无脸者", "灵魂被吞", "冷峻"]


def test_prefetch_skips_blank_fields(monkeypatch):
    """空字段不进 prefetch 文本集合（与 _semantic_coverage 对空字段短路一致）。

    真实 anchor 创建流程 (writer_intent_anchor._validate_fields) 拒绝空字段，
    正常路径永远拿不到空字段的已验证 anchor——这里直接 monkeypatch load_anchor
    模拟该边界数据形态，单独验证 scan() 内 _safe_prefetch 调用点的过滤逻辑。"""
    import embedding_store
    monkeypatch.setenv("AGENDA_DRIFT_MODE", "active")
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    monkeypatch.setattr(embedding_store, "compute_embedding", lambda t: [1.0, 0.0])
    monkeypatch.setattr(wia, "load_anchor", lambda project, key: {
        "want": "决战", "antagonist": "", "stake": "灵魂被吞", "tone_word": "冷峻",
        "_sha256": "fake",
    })
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {}

    monkeypatch.setattr(embedding_store, "prefetch_embeddings", _rec_prefetch)
    proj = Path(tempfile.mkdtemp())
    draft_text = "殊死搏杀·灵魂被吞·冷峻\n" * 50
    mod.scan(_write_draft(draft_text), proj, "001")

    assert len(calls) == 1
    stripped = mod._strip_changes(draft_text)
    assert calls[0] == [stripped, "决战", "灵魂被吞", "冷峻"]


def test_prefetch_not_called_without_real_backend(monkeypatch):
    """无真后端（默认）→ 不进语义分支·prefetch_embeddings 完全不触发（零回归）。"""
    import embedding_store
    monkeypatch.setenv("AGENDA_DRIFT_MODE", "active")
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {}

    monkeypatch.setattr(embedding_store, "prefetch_embeddings", _rec_prefetch)
    proj = _mk_project_with_anchor()
    out = mod.scan(_write_draft("甜蜜蜜的婚礼在春天举行。" * 200), proj, "001")

    assert out["match_method"] == "char_jaccard"
    assert calls == [], "无真后端时不该调用 prefetch_embeddings"


def test_semantic_path_falls_back_when_embedding_encode_fails():
    """真后端配置但草稿整体编码异常 → 回退字面 Jaccard（不崩·不误判为语义路径）。"""
    bak_mode = os.environ.get("AGENDA_DRIFT_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "mock"
        proj = _mk_project_with_anchor(
            want="救妹妹", antagonist="无脸者", stake="灵魂被吞", tone_word="冷峻")
        draft_text = "救妹妹·无脸者·灵魂被吞·冷峻\n" * 50

        import embedding_store
        orig = embedding_store.compute_embedding

        def _boom(text):
            raise RuntimeError("模拟真后端编码失败")

        embedding_store.compute_embedding = _boom
        try:
            out = mod.scan(_write_draft(draft_text), proj, "001")
        finally:
            embedding_store.compute_embedding = orig

        assert out["match_method"] == "char_jaccard", out
        stripped = mod._strip_changes(draft_text)
        assert out["field_scores"]["want"] == round(mod._coverage("救妹妹", stripped), 4)
    finally:
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)
