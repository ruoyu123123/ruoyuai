# -*- coding: utf-8 -*-
"""choice_consequence_ledger · R25 W13 Batch-MM · P1"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import choice_consequence_ledger as mod  # noqa: E402

_TARGET = _SCRIPTS / "choice_consequence_ledger.py"
_ENV = "CHOICE_CONSEQUENCE_MODE"


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
# 🔴 2026-07-02 embedding 语义呼应补漏接线（真后端命中 + 门控关零回归）
# 参考范式：topic_drift_scanner._has_real_embedding_backend（本仓约定每文件自留一份）
# ════════════════════════════════════════════════════════════════════
def _clear_embed_env():
    bak_eb = os.environ.pop("EMBED_BACKEND", None)
    bak_gen = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("GEN_EMBED__")}
    return bak_eb, bak_gen


def _restore_embed_env(bak_eb, bak_gen):
    if bak_eb is not None:
        os.environ["EMBED_BACKEND"] = bak_eb
    for k, v in bak_gen.items():
        os.environ[k] = v


def test_has_real_embedding_backend_false_by_default():
    bak_eb, bak_gen = _clear_embed_env()
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        _restore_embed_env(bak_eb, bak_gen)


def test_has_real_embedding_backend_true_when_set():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "fake-real"
        assert mod._has_real_embedding_backend() is True
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_semantic_resonance_catches_paraphrase_when_literal_misses():
    """字面 keyword-in-text 未命中（正文意译呼应，没用原关键词），真后端语义余弦应补上
    命中——这正是本次升级要根治的漏检（'仇视'类关键词从没原样出现过）。"""
    bak_mode = os.environ.get(_ENV)
    bak_eb = os.environ.get("EMBED_BACKEND")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "fake-real"
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "主角决定揭露真相", "faction", 3, ["仇视", "恨意"])
        draft_text = "从今往后所有人都对他心怀不满真相被揭露后众人态度大变。" * 3
        assert not mod._check_resonance(draft_text, ["仇视", "恨意"]), \
            "前置条件：字面关键词必须不命中"

        import embedding_store
        orig = embedding_store.compute_embedding

        def _content_aware_embed(text):
            if ("揭露" in text) or ("心怀不满" in text):
                return [1.0, 0.0]
            return [0.0, 1.0]

        embedding_store.compute_embedding = _content_aware_embed
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
            embedding_store.compute_embedding = orig
    finally:
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_literal_hit_still_wins_over_semantic_when_both_available():
    """字面命中永远优先：即便真后端就绪，只要字面 keyword-in-text 命中就直接判中，
    match_method 必须是 literal_substring（不越级去用语义结果）。"""
    bak_mode = os.environ.get(_ENV)
    bak_eb = os.environ.get("EMBED_BACKEND")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "fake-real"
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "揭露 X", "faction", 3, ["仇视", "X 派"])
        draft_text = "于是众人对他生出仇视·X 派开始反扑。" * 30

        import embedding_store
        orig = embedding_store.compute_embedding
        # 无论传入什么文本都给同一向量·纯粹验证字面命中分支根本不查语义结果
        embedding_store.compute_embedding = lambda text: [1.0, 0.0]
        try:
            draft = _write_draft(draft_text)
            rep = mod.scan(proj, "cluster_006", str(draft))
            entries = mod.view_entries(proj)
            assert entries[0]["status"] == "visible"
            assert entries[0]["match_method"] == "literal_substring"
            assert "semantic_similarity" not in entries[0]
        finally:
            embedding_store.compute_embedding = orig
    finally:
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_semantic_below_threshold_falls_through_to_pending_or_starving():
    """真后端就绪但相似度 < 阈值 → 不误判 visible，按原 pending/starving 逻辑走。"""
    bak_mode = os.environ.get(_ENV)
    bak_eb = os.environ.get("EMBED_BACKEND")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "fake-real"
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_a",
                         "x", "preference", 5, ["不出现的关键词"])
        draft_text = "完全无关的正文内容在这里展开。" * 30

        import embedding_store
        orig = embedding_store.compute_embedding

        def _orthogonal_embed(text):
            # query（choice 摘要+关键词）与 draft 段落分别落到两个正交维度 → 相似度 0 < 阈值
            if "不出现的关键词" in text or text.strip() == "x":
                return [1.0, 0.0, 0.0]
            if "完全无关" in text:
                return [0.0, 1.0, 0.0]
            return [0.0, 0.0, 1.0]

        embedding_store.compute_embedding = _orthogonal_embed
        try:
            draft = _write_draft(draft_text)
            # cluster_006：窗口未到（expires_at_cluster_idx=10）
            rep = mod.scan(proj, "cluster_006", str(draft))
            codes = {v["code"] for v in rep["violations"]}
            assert mod.ISSUE_CODE_PENDING in codes
            assert mod.ISSUE_CODE_VISIBLE not in codes
        finally:
            embedding_store.compute_embedding = orig
    finally:
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_scan_gate_off_matches_original_literal_logic():
    """🔴 零回归锁：门控关（无 EMBED_BACKEND / 无 GEN_EMBED__*）→ scan() 判定结果与原字面
    keyword-in-text 逻辑逐字节一致，且 embedding_store.compute_embedding 即便被换成任意值
    也绝不会被调用（_build_semantic_context 在最前面短路返回 None）。"""
    bak_mode = os.environ.get(_ENV)
    bak_eb, bak_gen = _clear_embed_env()
    import embedding_store
    orig = embedding_store.compute_embedding

    def _boom(text):
        raise AssertionError("门控关时绝不应调用 compute_embedding")

    embedding_store.compute_embedding = _boom
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
        embedding_store.compute_embedding = orig
        _set_mode(bak_mode)
        _restore_embed_env(bak_eb, bak_gen)
