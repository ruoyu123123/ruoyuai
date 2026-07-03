# -*- coding: utf-8 -*-
"""intent_ledger · R24 W12 Batch-LL · P2"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import intent_ledger as mod  # noqa: E402

_TARGET = _SCRIPTS / "intent_ledger.py"


def _mk_project(clusters=None, intent=None, changes_path=None, changes_data=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if clusters is not None:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False),
            encoding="utf-8")
    if intent is not None:
        # 用 writer_intent_anchor create
        sys.path.insert(0, str(_SCRIPTS))
        import writer_intent_anchor as wia
        ns = SimpleNamespace(
            project=str(proj), cluster_key=intent["cluster_key"],
            want=intent["want"], antagonist=intent["antagonist"],
            stake=intent["stake"], tone_word=intent["tone_word"],
            force=False)
        wia.cmd_create(ns)
    if changes_path is not None and changes_data is not None:
        full = proj / changes_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(json.dumps(changes_data, ensure_ascii=False),
                        encoding="utf-8")
    return proj


def _basic_clusters():
    return {
        "001": {
            "scope_summary": "主角调查院子里的怪声",
            "scene_storyboard": [],
            "emergence_brief": "主角进入旧屋",
        },
        "002": {
            "scope_summary": "主角解开旧屋谜题",
            "emergence_brief": "副反派现身",
        },
    }


def test_extract_keys_basic():
    out = mod._extract_keys("主角调查院子主角")
    assert isinstance(out, set)


def test_extract_keys_empty():
    assert mod._extract_keys("") == set()
    assert mod._extract_keys("a") == set()


def test_drift_score_identical():
    # 完全相同 = drift 0
    assert mod._drift_score("主角调查院子怪声", "主角调查院子怪声") == 0.0


def test_drift_score_disjoint():
    # 完全无重叠 = drift 1.0
    d = mod._drift_score("主角调查院子", "副反派现身")
    assert d > 0.5


def test_drift_score_one_empty_returns_zero():
    assert mod._drift_score("", "副反派现身") == 0.0
    assert mod._drift_score("主角调查院子", "") == 0.0


def test_load_cluster_brief_found():
    proj = _mk_project(_basic_clusters())
    b = mod._load_cluster_brief(proj, "001")
    assert "主角调查" in b["scope_summary"]


def test_load_cluster_brief_missing():
    proj = _mk_project({})
    b = mod._load_cluster_brief(proj, "999")
    # 缺失 cluster → 全空字符串/空 list 兜底
    assert b["scope_summary"] == ""
    assert b["emergence_brief"] == ""
    assert b["scene_storyboard"] == []


def test_next_cluster_emergence():
    proj = _mk_project(_basic_clusters())
    nxt = mod._next_cluster_emergence(proj, "001")
    assert "副反派" in nxt


def test_next_cluster_emergence_missing():
    proj = _mk_project(_basic_clusters())
    nxt = mod._next_cluster_emergence(proj, "002")
    assert nxt == ""


def test_load_writer_intent_when_present():
    proj = _mk_project(
        _basic_clusters(),
        intent={"cluster_key": "001", "want": "救妹妹",
                "antagonist": "无脸者", "stake": "灵魂被吞", "tone_word": "冷峻"})
    i = mod._load_writer_intent(proj, "001")
    assert i.get("want") == "救妹妹"


def test_load_writer_intent_missing():
    proj = _mk_project({})
    i = mod._load_writer_intent(proj, "001")
    assert i == {}


def test_append_writes_jsonl():
    proj = _mk_project(_basic_clusters())
    rc = mod.cmd_append(SimpleNamespace(
        project=str(proj), cluster_key="001", actual=None))
    assert rc == 0
    p = mod._ledger_path(proj)
    assert p.exists()
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["cluster_key"] == "001"
    assert "drift_score" in rec


def test_append_append_only():
    proj = _mk_project(_basic_clusters())
    mod.cmd_append(SimpleNamespace(project=str(proj),
                                   cluster_key="001", actual=None))
    mod.cmd_append(SimpleNamespace(project=str(proj),
                                   cluster_key="002", actual=None))
    p = mod._ledger_path(proj)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_append_with_actual_override():
    proj = _mk_project(_basic_clusters())
    actual_p = proj / "actual.json"
    actual_p.write_text(json.dumps({
        "summary": "主角实际进入旧屋发现尸体"}, ensure_ascii=False), encoding="utf-8")
    rc = mod.cmd_append(SimpleNamespace(
        project=str(proj), cluster_key="001", actual=str(actual_p)))
    assert rc == 0
    p = mod._ledger_path(proj)
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert "尸体" in rec["actual_summary"]


def test_view_missing_returns_1():
    proj = _mk_project({})
    rc = mod.cmd_view(SimpleNamespace(project=str(proj), last=5))
    assert rc == 1


def test_view_returns_rows():
    proj = _mk_project(_basic_clusters())
    mod.cmd_append(SimpleNamespace(project=str(proj),
                                   cluster_key="001", actual=None))
    rc = mod.cmd_view(SimpleNamespace(project=str(proj), last=5))
    assert rc == 0


def test_summary_no_ledger():
    proj = _mk_project({})
    rc = mod.cmd_summary(SimpleNamespace(project=str(proj)))
    assert rc == 0


def test_summary_aggregates_drifts():
    proj = _mk_project(_basic_clusters())
    mod.cmd_append(SimpleNamespace(project=str(proj),
                                   cluster_key="001", actual=None))
    mod.cmd_append(SimpleNamespace(project=str(proj),
                                   cluster_key="002", actual=None))
    # 不能直接 capture，通过函数读 path
    p = mod._ledger_path(proj)
    assert p.exists()
    # cmd_summary 输出 stdout·非 0 列即可
    rc = mod.cmd_summary(SimpleNamespace(project=str(proj)))
    assert rc == 0


def test_format_card_lines_three_rows():
    card = {
        "cluster_key": "001",
        "intent": {"want": "救妹妹"},
        "scope_summary": "进入旧屋",
        "actual_summary": "发现尸体",
        "next_cluster_framing": "反派现身",
        "drift_score": 0.5,
    }
    lines = mod._format_card_lines(card)
    assert len(lines) == 3
    assert any("你想干什么" in line for line in lines)
    assert any("实际发生" in line for line in lines)
    assert any("framing 漂移" in line for line in lines)


def test_ledger_path_default():
    proj = Path(tempfile.mkdtemp())
    p = mod._ledger_path(proj)
    assert p.name == ".intent_ledger.jsonl"
    assert p.parent.name == "_数据库"


def test_cli_append_smoke():
    proj = _mk_project(_basic_clusters())
    r = subprocess.run(
        [sys.executable, str(_TARGET), "append", str(proj), "001"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    assert "你想干什么" in r.stdout


def test_cli_view_smoke():
    proj = _mk_project(_basic_clusters())
    mod.cmd_append(SimpleNamespace(project=str(proj),
                                   cluster_key="001", actual=None))
    r = subprocess.run(
        [sys.executable, str(_TARGET), "view", str(proj), "--last", "3"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr


def test_cli_summary_smoke():
    proj = _mk_project(_basic_clusters())
    mod.cmd_append(SimpleNamespace(project=str(proj),
                                   cluster_key="001", actual=None))
    r = subprocess.run(
        [sys.executable, str(_TARGET), "summary", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.splitlines()[-1])
    assert out["ok"] is True
    assert out["rows"] == 1


def test_no_mutation_of_cluster_brief():
    """北极星⑤ 只读不修改 cluster_brief。"""
    proj = _mk_project(_basic_clusters())
    pre = (proj / "_数据库" / "事件簇.json").read_text(encoding="utf-8")
    mod.cmd_append(SimpleNamespace(project=str(proj),
                                   cluster_key="001", actual=None))
    post = (proj / "_数据库" / "事件簇.json").read_text(encoding="utf-8")
    assert pre == post


# ═══════════════════ embedding 接线（2026-07-02 · 真后端门控 + 字面 fallback）═══════════════════

def test_has_real_embedding_backend_false_by_default():
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v


def test_has_real_embedding_backend_false_when_hash():
    old = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "hash"
        assert mod._has_real_embedding_backend() is False
    finally:
        if old is not None:
            os.environ["EMBED_BACKEND"] = old
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_drift_score_semantic_catches_zero_overlap_synonym(monkeypatch):
    """真后端：字面零重叠的同义改写（「殊死搏杀」vs「决一死战」无共享 2-gram）应被余弦相似度
    识别为低漂移——字面 2-gram Jaccard 会误判成完全偏离（drift 必为 1.0）。"""
    # 前置断言：确认字面法确实判为完全偏离（两词各 4 字·3 个 2-gram 全不相交）
    assert mod._drift_score("殊死搏杀", "决一死战") == 1.0

    monkeypatch.setenv("EMBED_BACKEND", "mock")
    import embedding_store

    def _mock_embed(text):
        if "殊死搏杀" in text or "决一死战" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]
    monkeypatch.setattr(embedding_store, "compute_embedding", _mock_embed)

    drift = mod._drift_score("殊死搏杀", "决一死战")
    assert drift == 0.0, f"语义路径应识别为同义（drift=0）：{drift}"


def test_drift_score_semantic_falls_back_on_embedding_error(monkeypatch):
    """真后端配置但编码异常 → 回退字面 2-gram Jaccard（不崩·结果与门控关时一致）。"""
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    import embedding_store

    def _boom(text):
        raise RuntimeError("模拟真后端编码失败")
    monkeypatch.setattr(embedding_store, "compute_embedding", _boom)

    drift = mod._drift_score("主角调查院子", "副反派现身")
    assert drift > 0.5  # 与 test_drift_score_disjoint 门控关时的字面结果一致


def test_drift_score_default_matches_literal_jaccard_exactly():
    """🔴 零回归锁：门控关（默认）→ _drift_score 与直接手算字面 Jaccard 逐位一致。"""
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        want, framing = "主角调查院子里的怪声", "主角进入旧屋寻找线索"
        a = mod._extract_keys(want)
        b = mod._extract_keys(framing)
        expected = round(1.0 - len(a & b) / len(a | b), 4) if (a and b and (a | b)) else 0.0
        assert mod._drift_score(want, framing) == expected
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v
