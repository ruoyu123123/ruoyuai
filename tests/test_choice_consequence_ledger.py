# -*- coding: utf-8 -*-
"""choice_consequence_ledger · R25 W13 Batch-MM · P1"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import choice_consequence_ledger as mod  # noqa: E402
import embedding_store  # noqa: E402

_TARGET = _SCRIPTS / "choice_consequence_ledger.py"
_ENV = "CHOICE_CONSEQUENCE_MODE"


# 🔴 2026-07-04 本地 autouse 隔离（不碰全局 conftest.py）：content_backend_available() 查真
# 文件系统（venv/infer 脚本/模型目录），本机若已备好 bge 模型会恒真——不像旧 EMBED_BACKEND
# 有 conftest._isolate_nn_gates 兜底清零，会让本文件里不测 embedding 的"素"用例跨机器非确定
# 污染（真机上真的算出高于阈值的相似度）。默认关闭·内容路径专项测试自行在测试体内覆盖。
@pytest.fixture(autouse=True)
def _content_backend_off_by_default():
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        yield
    finally:
        embedding_store.content_backend_available = orig


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_append_entry_writes_ledger():
    proj = _mk_project()
    e = mod.append_entry(proj, "cluster_005", "card_b",
                         "主角揭露 X", "faction", 3,
                         ["仇视", "X 派", "排挤"])
    assert e["cluster_id"] == "cluster_005"
    assert e["stakes_tier"] == "faction"
    assert e["expected_visibility_window"] == 3
    assert e["expires_at_cluster_idx"] == 8
    p = mod._ledger_path(proj)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["_namespace"] == "choice_consequence"
    assert len(data["entries"]) == 1


def test_append_invalid_tier_raises():
    proj = _mk_project()
    try:
        mod.append_entry(proj, "cluster_005", "card_b",
                         "x", "invalid_tier", 1, [])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "stakes_tier" in str(e)


def test_view_entries_filter():
    proj = _mk_project()
    mod.append_entry(proj, "cluster_005", "card_a", "s1",
                     "life", 2, ["kw1"])
    mod.append_entry(proj, "cluster_006", "card_b", "s2",
                     "moral", 2, ["kw2"])
    all_ = mod.view_entries(proj)
    assert len(all_) == 2
    f5 = mod.view_entries(proj, "cluster_005")
    assert len(f5) == 1
    assert f5[0]["choice_key"] == "card_a"


def test_scan_visible_when_resonance_hit():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "揭露 X", "faction", 3, ["仇视", "X 派"])
        draft = _write_draft("于是众人对他生出仇视·X 派开始反扑。" * 30)
        rep = mod.scan(proj, "cluster_006", str(draft))
        codes = {v["code"] for v in rep["violations"]}
        assert mod.ISSUE_CODE_VISIBLE in codes
        # ledger entry status 已更新
        entries = mod.view_entries(proj)
        assert entries[0]["status"] == "visible"
    finally:
        _set_mode(bak)


def test_scan_starving_after_window_expires():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        # cluster_005·窗口 2·过期 cluster_idx=7
        mod.append_entry(proj, "cluster_005", "card_a",
                         "选择", "faction", 2, ["不会出现的keyword"])
        draft = _write_draft("正文与 keyword 无关。" * 40)
        # cluster_008 = 已过期
        rep = mod.scan(proj, "cluster_008", str(draft))
        codes = {v["code"] for v in rep["violations"]}
        assert mod.ISSUE_CODE_STARVING in codes
        entries = mod.view_entries(proj)
        assert entries[0]["status"] == "starving"
    finally:
        _set_mode(bak)


def test_scan_pending_within_window():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_a",
                         "x", "preference", 5, ["不出现的"])
        draft = _write_draft("正文。" * 30)
        # cluster_006 · 窗口未到
        rep = mod.scan(proj, "cluster_006", str(draft))
        codes = {v["code"] for v in rep["violations"]}
        assert mod.ISSUE_CODE_PENDING in codes
        assert mod.ISSUE_CODE_STARVING not in codes
        entries = mod.view_entries(proj)
        assert entries[0]["status"] == "pending"
    finally:
        _set_mode(bak)


def test_scan_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_a",
                         "x", "life", 2, ["不出现"])
        draft = _write_draft("正文。" * 30)
        rep = mod.scan(proj, "cluster_010", str(draft))
        assert rep["mode"] == "off"
        assert "starving_entries" not in rep
    finally:
        _set_mode(bak)


def test_scan_shadow_no_violation_emitted():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_a",
                         "x", "life", 2, ["不出现"])
        draft = _write_draft("正文。" * 30)
        rep = mod.scan(proj, "cluster_010", str(draft))
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_namespace_separation():
    proj = _mk_project()
    mod.append_entry(proj, "cluster_005", "card_a", "x", "life", 2, [])
    p = mod._ledger_path(proj)
    data = json.loads(p.read_text(encoding="utf-8"))
    # 命名空间分立 = choice_consequence · 不是 author_planted
    assert data["_namespace"] == "choice_consequence"
    assert "author_planted" not in data


def test_cluster_idx_parse():
    assert mod._cluster_idx("cluster_005") == 5
    assert mod._cluster_idx("cluster_010") == 10
    assert mod._cluster_idx("") == 0
    assert mod._cluster_idx(None) == 0


def test_codes_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    for c in (mod.ISSUE_CODE_STARVING, mod.ISSUE_CODE_VISIBLE,
              mod.ISSUE_CODE_PENDING):
        assert c not in audit_hub.HARD_GATE_CODES


def test_cli_append_view_scan():
    proj = _mk_project()
    # append
    r = subprocess.run(
        [sys.executable, str(_TARGET), "append", str(proj), "cluster_005",
         "--choice-key", "card_b", "--summary", "s",
         "--tier", "faction", "--window", "2",
         "--keywords", "kw1,kw2"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    entry = json.loads(r.stdout)
    assert entry["choice_key"] == "card_b"

    # view
    r2 = subprocess.run(
        [sys.executable, str(_TARGET), "view", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r2.returncode == 0, r2.stderr
    entries = json.loads(r2.stdout)
    assert len(entries) == 1

    # scan
    draft = _write_draft("命中 kw1 命中 kw2。" * 20)
    r3 = subprocess.run(
        [sys.executable, str(_TARGET), "scan", str(proj),
         "cluster_006", str(draft)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r3.returncode in (0, 1), r3.stderr
    rep = json.loads(r3.stdout)
    assert rep["scanner"] == "choice_consequence_visibility_scanner"


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_load_ledger_missing_returns_skeleton():
    proj = _mk_project()
    data = mod._load_ledger(proj)
    assert data["entries"] == []
    assert data["_namespace"] == "choice_consequence"


# ════════════════════════════════════════════════════════════════════
# 🔴 2026-07-04 内容语义 embedding 路径（W6-C 迁移：风格模型→bge 内容模型）
# mock 面：直接 monkeypatch embedding_store.content_backend_available /
# compute_content_embedding(_batch) / prefetch_content_embeddings（内容后端可用性
# 由 venv+infer 脚本+模型目录决定，不再是 EMBED_BACKEND 环境变量能摆弄的）
# ════════════════════════════════════════════════════════════════════
def test_content_backend_ready_false_by_default():
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        assert mod._content_backend_ready() is False
    finally:
        embedding_store.content_backend_available = orig


def test_content_backend_ready_true_when_available():
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: True
    try:
        assert mod._content_backend_ready() is True
    finally:
        embedding_store.content_backend_available = orig


def test_semantic_resonance_catches_paraphrase_when_literal_misses():
    """字面 keyword-in-text 未命中（正文意译呼应，没用原关键词），内容后端语义余弦应补上
    命中——这正是 embedding 升级要根治的漏检（'仇视'类关键词从没原样出现过）。"""
    bak_mode = os.environ.get(_ENV)
    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_batch = embedding_store.compute_content_embeddings_batch
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _content_aware_embed(text):
        if ("揭露" in text) or ("心怀不满" in text):
            return [1.0, 0.0]
        return [0.0, 1.0]

    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "主角决定揭露真相", "faction", 3, ["仇视", "恨意"])
        draft_text = "从今往后所有人都对他心怀不满真相被揭露后众人态度大变。" * 3
        assert not mod._check_resonance(draft_text, ["仇视", "恨意"]), \
            "前置条件：字面关键词必须不命中"

        embedding_store.content_backend_available = lambda: True
        embedding_store.compute_content_embeddings_batch = (
            lambda texts: [_content_aware_embed(t) for t in texts])
        embedding_store.compute_content_embedding = _content_aware_embed
        embedding_store.prefetch_content_embeddings = lambda texts: {
            "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
        try:
            draft = _write_draft(draft_text)
            rep = mod.scan(proj, "cluster_006", str(draft))
            codes = {v["code"] for v in rep["violations"]}
            assert mod.ISSUE_CODE_VISIBLE in codes
            entries = mod.view_entries(proj)
            assert entries[0]["status"] == "visible"
            assert entries[0]["match_method"] == "embedding_cosine"
            assert entries[0]["semantic_similarity"] == 1.0
        finally:
            embedding_store.content_backend_available = orig_avail
            embedding_store.compute_content_embeddings_batch = orig_batch
            embedding_store.compute_content_embedding = orig_single
            embedding_store.prefetch_content_embeddings = orig_prefetch
    finally:
        _set_mode(bak_mode)


def test_literal_hit_still_wins_over_semantic_when_both_available():
    """字面命中永远优先：即便内容后端就绪，只要字面 keyword-in-text 命中就直接判中，
    match_method 必须是 literal_substring（不越级去用语义结果）。"""
    bak_mode = os.environ.get(_ENV)
    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings
    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "揭露 X", "faction", 3, ["仇视", "X 派"])
        draft_text = "于是众人对他生出仇视·X 派开始反扑。" * 30

        embedding_store.content_backend_available = lambda: True
        # 无论传入什么文本都给同一向量·纯粹验证字面命中分支根本不查语义结果
        embedding_store.compute_content_embedding = lambda text: [1.0, 0.0]
        embedding_store.prefetch_content_embeddings = lambda texts: {
            "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
        try:
            draft = _write_draft(draft_text)
            rep = mod.scan(proj, "cluster_006", str(draft))
            entries = mod.view_entries(proj)
            assert entries[0]["status"] == "visible"
            assert entries[0]["match_method"] == "literal_substring"
            assert "semantic_similarity" not in entries[0]
        finally:
            embedding_store.content_backend_available = orig_avail
            embedding_store.compute_content_embedding = orig_single
            embedding_store.prefetch_content_embeddings = orig_prefetch
    finally:
        _set_mode(bak_mode)


def test_semantic_below_threshold_falls_through_to_pending_or_starving():
    """内容后端就绪但相似度 < 阈值 → 不误判 visible，按原 pending/starving 逻辑走。"""
    bak_mode = os.environ.get(_ENV)
    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _orthogonal_embed(text):
        # query（choice 摘要+关键词）与 draft 段落分别落到两个正交维度 → 相似度 0 < 阈值
        if "不出现的关键词" in text or text.strip() == "x":
            return [1.0, 0.0, 0.0]
        if "完全无关" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_a",
                         "x", "preference", 5, ["不出现的关键词"])
        draft_text = "完全无关的正文内容在这里展开。" * 30

        embedding_store.content_backend_available = lambda: True
        embedding_store.compute_content_embedding = _orthogonal_embed
        embedding_store.prefetch_content_embeddings = lambda texts: {
            "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
        try:
            draft = _write_draft(draft_text)
            # cluster_006：窗口未到（expires_at_cluster_idx=10）
            rep = mod.scan(proj, "cluster_006", str(draft))
            codes = {v["code"] for v in rep["violations"]}
            assert mod.ISSUE_CODE_PENDING in codes
            assert mod.ISSUE_CODE_VISIBLE not in codes
        finally:
            embedding_store.content_backend_available = orig_avail
            embedding_store.compute_content_embedding = orig_single
            embedding_store.prefetch_content_embeddings = orig_prefetch
    finally:
        _set_mode(bak_mode)


def test_scan_content_backend_off_matches_original_literal_logic():
    """🔴 零回归锁：内容后端不可用（content_backend_available→False）→ scan() 判定结果与原
    字面 keyword-in-text 逻辑逐字节一致，且 embedding_store.compute_content_embedding 即便
    被换成任意值也绝不会被调用（_build_semantic_context 在最前面短路返回 None）。"""
    bak_mode = os.environ.get(_ENV)
    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding

    def _boom(text):
        raise AssertionError("内容后端不可用时绝不应调用 compute_content_embedding")

    embedding_store.content_backend_available = lambda: False
    embedding_store.compute_content_embedding = _boom
    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "揭露 X", "faction", 3, ["仇视", "X 派"])
        draft_text = "于是众人对他生出仇视·X 派开始反扑。" * 30
        draft = _write_draft(draft_text)
        rep = mod.scan(proj, "cluster_006", str(draft))
        codes = {v["code"] for v in rep["violations"]}
        assert mod.ISSUE_CODE_VISIBLE in codes
        entries = mod.view_entries(proj)
        assert entries[0]["status"] == "visible"
        assert entries[0]["match_method"] == "literal_substring"
        assert entries[0]["matched_keywords"] == mod._check_resonance(draft_text, ["仇视", "X 派"])
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        _set_mode(bak_mode)


# ════════════════════════════════════════════════════════════════════
# 🔴 2026-07-03 Wave-4 性能层：scan() 批量 prefetch（正文段落 + 全部待判定 entry
# query 一次性预热，其后 _build_semantic_context / _semantic_resonance 内的逐条
# compute_content_embedding 全部命中缓存·内容后端子进程按条调用极贵）
# ════════════════════════════════════════════════════════════════════
def test_scan_prefetches_paragraphs_and_queries_once():
    bak_mode = os.environ.get(_ENV)
    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_prefetch = embedding_store.prefetch_content_embeddings
    orig_single = embedding_store.compute_content_embedding
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0}

    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "主角决定揭露真相", "faction", 3, ["仇视", "恨意"])
        mod.append_entry(proj, "cluster_005", "card_c",
                         "另一个选择", "life", 3, ["kwX"])
        draft_text = "第一段正文内容足够长用于切段测试。\n第二段正文内容也足够长用于切段。"
        draft = _write_draft(draft_text)

        embedding_store.content_backend_available = lambda: True
        embedding_store.prefetch_content_embeddings = _rec_prefetch
        embedding_store.compute_content_embedding = lambda t: [0.0, 0.0]
        try:
            mod.scan(proj, "cluster_006", str(draft))
            assert len(calls) == 1, f"应恰好一次批量 prefetch·实际 {len(calls)} 次"
            texts = calls[0]
            assert "第一段正文内容足够长用于切段测试。" in texts
            assert "第二段正文内容也足够长用于切段。" in texts
            assert "主角决定揭露真相 仇视 恨意" in texts
            assert "另一个选择 kwX" in texts
        finally:
            embedding_store.content_backend_available = orig_avail
            embedding_store.prefetch_content_embeddings = orig_prefetch
            embedding_store.compute_content_embedding = orig_single
    finally:
        _set_mode(bak_mode)


def test_scan_content_backend_off_never_calls_prefetch():
    """🔴 零回归锁：内容后端不可用 → prefetch_content_embeddings 完全不被调用。"""
    bak_mode = os.environ.get(_ENV)
    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _boom(texts):
        raise AssertionError("内容后端不可用时绝不应调用 prefetch_content_embeddings")

    embedding_store.content_backend_available = lambda: False
    embedding_store.prefetch_content_embeddings = _boom
    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "揭露 X", "faction", 3, ["仇视", "X 派"])
        draft_text = "于是众人对他生出仇视·X 派开始反扑。" * 30
        draft = _write_draft(draft_text)
        rep = mod.scan(proj, "cluster_006", str(draft))
        assert rep["scanner"] == "choice_consequence_visibility_scanner"
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.prefetch_content_embeddings = orig_prefetch
        _set_mode(bak_mode)
