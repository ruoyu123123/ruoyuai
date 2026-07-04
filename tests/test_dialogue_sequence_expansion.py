# -*- coding: utf-8 -*-
"""dialogue_sequence_expansion 专属回归(2026-06-20·R8 W4 Batch-J·L30)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import dialogue_sequence_expansion as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("DIALOGUE_SEQ_EXPANSION_MODE", None)
    else:
        os.environ["DIALOGUE_SEQ_EXPANSION_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(profile=None, brief_expected=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if brief_expected is not None:
        ec = {"clusters": [{"cluster_id": "cluster_001", "status": "in_progress",
                            "expected_dialogue_expansion_min": brief_expected}]}
        (proj / "_数据库" / "事件簇.json").write_text(
            json.dumps(ec, ensure_ascii=False), encoding="utf-8")
    if profile is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"dialogue_expansion_profile": profile},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# >500 CJK draft helpers
_FLAT_DIALOGUE = (
    "他低头说：“好。”\n她抬头答：“嗯。”\n他又说：“走吧。”\n她回：“行。”\n"
) * 30  # 4 turn × 30 = 120 turn 全无扩展标志

_EXPANSION_DIALOGUE = (
    "他放下杯子，“先说一件事，等等再讨论那个。”\n"
    "她点头，“你是说我们要先确认线索？”\n"
    "他笑了下，“再问一句，你确定没问题？”\n"
    "她耸肩，“就这样？没了？”\n"
) * 30  # 4 turn × 30 全是扩展


def test_off_returns_skeleton():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_FLAT_DIALOGUE))
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "expansion_ratio" not in out
    finally:
        _set_mode(bak)


def test_active_flat_dialogue_fail():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_FLAT_DIALOGUE))
        assert out["dialogue_turn_count"] >= 100
        assert out["expansion_ratio"] == 0.0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_active_expansion_dialogue_pass():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_EXPANSION_DIALOGUE))
        assert out["expansion_ratio"] > 0.5
        assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_FLAT_DIALOGUE))
        assert out["mode"] == "shadow"
        assert out["expansion_ratio"] == 0.0
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("“好。”" * 5))
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_too_few_turns_skipped():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("active")
        # 长文本但无 quote
        out = mod.scan(_write("普通叙事段落，没有对话。" * 80))
        assert "对话 turn 太少" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_classify_turn_categories():
    assert mod.classify_turn("先说一件事，听我说") == {"pre"}
    assert mod.classify_turn("你是说他知道了？") == {"insert"}
    assert mod.classify_turn("就这样？") == {"post"}
    assert mod.classify_turn("好。") == set()


def test_extract_turns_returns_strings():
    turns = mod.extract_turns("他说：“好。”她说：“嗯。”")
    assert turns == ["好。", "嗯。"]


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("BOGUS")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_resolve_expected_min_cluster_brief():
    proj = _mk_project(brief_expected=0.5)
    assert mod._resolve_expected_min(proj) == 0.5


def test_resolve_expected_min_author_profile():
    proj = _mk_project(profile={"expansion_ratio_baseline": 0.4})
    assert mod._resolve_expected_min(proj) == 0.4


def test_resolve_expected_min_none():
    assert mod._resolve_expected_min(None) is None
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir()
    assert mod._resolve_expected_min(proj) is None


def test_read_failure_returns_note():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


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
    经 _classify_turn_kind/_classify_turns_batch 顺带触发一次真实分类。本 fixture 把它
    按文件级默认关闭（同 conftest._isolate_nn_gates 的思路，只是这个门控是函数不是环境
    变量，只能靠 monkeypatch 不能靠 os.environ.pop），_run_with_mock_embedding 内会临时
    覆盖成 True。
    """
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        yield
    finally:
        embedding_store.content_backend_available = orig


def test_classify_turn_kind_gate_off_returns_none():
    assert mod._classify_turn_kind("你好。") is None


def test_classify_turn_kind_model_hit():
    def _do():
        result = mod._classify_turn_kind("就这样？没有别的了吗？")
        assert result == "post"
    _run_with_mock_embedding(_do)


def test_classify_turn_model_augments_lexicon_union():
    """turn 本身不含任何正则触发词·但语义上属于 post → 模型补入(并集不取代)。"""
    def _do():
        turn = "没有别的了吗？完了？"  # 不含 PRE/INSERT/POST_MARKERS 任何锚词
        assert mod._lexicon_turn_kinds(turn) == set()
        kinds = mod.classify_turn(turn)
        assert kinds == {"post"}
    _run_with_mock_embedding(_do)


def test_classify_turn_lexicon_preserved_when_gate_off():
    """真后端关闭时·classify_turn 与 _lexicon_turn_kinds 逐字节一致（零回归契约）。"""
    turn = "先说一件事，听我说"
    assert mod.classify_turn(turn) == mod._lexicon_turn_kinds(turn) == {"pre"}


def test_scan_turn_classify_source_default_lexicon():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_FLAT_DIALOGUE))
        assert out["turn_classify_source"] == "lexicon"
        assert out["model_boosted_turns"] == 0
    finally:
        _set_mode(bak)


def test_scan_turn_classify_source_model_boosted():
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
    try:
        _set_mode("active")

        def _do():
            # turn 语义像 post 但不含任何正则触发词 → 只能靠模型补召回
            boosted_dialogue = "他问：“事情就到这里，没有别的了吗？完了？”\n" * 40
            out = mod.scan(_write(boosted_dialogue))
            assert out["model_boosted_turns"] > 0
            assert out["turn_classify_source"] == "zero_shot_embedding+lexicon"

        _run_with_mock_embedding(_do)
    finally:
        _set_mode(bak)


def test_seq_kind_prototypes_cover_pre_insert_post():
    assert set(mod._SEQ_KIND_PROTOTYPES.keys()) == {"pre", "insert", "post"}


# ── 🔴 2026-07-03 Wave-4 性能层：scan() 批量分类回归 ─────────────────────────
def test_classify_turns_batch_empty_list():
    assert mod._classify_turns_batch([]) == []


def test_classify_turns_batch_gate_off_returns_all_none():
    labels = mod._classify_turns_batch(["先说一件事", "就这样？", "好。"])
    assert labels == [None, None, None]


def test_classify_turns_batch_matches_lexicon_free_semantics():
    """无正则锚词的 turn·真后端下批量分类应识别语义类别（同单条 _classify_turn_kind）。"""
    def _do():
        turns = ["没有别的了吗？完了？", "普通陈述句没有任何标志"]
        labels = mod._classify_turns_batch(turns)
        assert labels[0] == "post"
    _run_with_mock_embedding(_do)


def test_scan_calls_classify_batch_exactly_once():
    """🔴 Wave-4 核心契约：scan() 对全部 turn 只触发一次 zero_shot_prototype.classify_batch。"""
    bak = os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE")
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
            out = mod.scan(_write(_EXPANSION_DIALOGUE))
        finally:
            zero_shot_prototype.classify_batch = orig
        assert len(calls) == 1
        expected_turns = mod.extract_turns(
            mod._strip_changes(_EXPANSION_DIALOGUE))
        assert calls[0] == expected_turns
        assert out["dialogue_turn_count"] == len(expected_turns)
    finally:
        _set_mode(bak)
