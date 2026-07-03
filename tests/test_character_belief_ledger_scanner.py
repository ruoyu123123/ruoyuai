# -*- coding: utf-8 -*-
"""character_belief_ledger_scanner R18 W7 Batch-S·P0 OmniToM 回归测试

确定性·零依赖·零 LLM/零联网。覆盖：
  1. off 骨架
  2. 无人物卡 → skip
  3. shadow + 无 leak → PASS
  4. shadow + leak → 不上报 stderr
  5. active + leak → FAIL_MINOR + warning
  6. 同场景 reveal 后下场景使用 → 合法
  7. 跨场景越权使用 → leak
  8. 草稿太短 skip
  9. 草稿读取失败 → note
 10. _mode 非法回落 shadow
 11. _mode None 默认 shadow
 12. _strip_changes 两种分隔符
 13. _cjk_count
 14. locked_fact.json items[].key 读取
 15. CLI subprocess 退出码
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import character_belief_ledger_scanner as mod  # noqa: E402
import embedding_store  # noqa: E402

_TARGET = _SCRIPTS / "character_belief_ledger_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("CHARACTER_BELIEF_LEDGER_MODE", None)
    else:
        os.environ["CHARACTER_BELIEF_LEDGER_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(characters=None, locked_facts=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")
    if locked_facts is not None:
        # 🔴 2026-06-29 重接线：真 locked facts 在 事件簇.json.clusters[].locked_facts
        # (producer: apply_archive.apply_locked_facts)·非零 producer 的幻影 locked_fact.json
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": [{"cluster_id": "cluster_001",
                                      "locked_facts": locked_facts}]},
                       ensure_ascii=False),
            encoding="utf-8")
    return proj


_SCENE_SEP = "\n━━━━━━━━━━━━\n"

# 张三在场景 1 未知秘密；场景 2 突然 "张三知道秘密" → leak
_LEAK_DRAFT = (
    "张三走进酒馆。他点了一杯酒。" * 30
    + _SCENE_SEP
    + "张三知道秘密。他面色凝重。" * 30
)

# 场景 1 公开 reveal "秘密"，张三在场；场景 2 张三知道秘密 → 合法
_LEGAL_DRAFT = (
    "张三在屋里。说书人讲到秘密。秘密。秘密。" * 30
    + _SCENE_SEP
    + "张三知道秘密。他点头。" * 30
)


# ───── 1 off → 骨架 ──
def test_off_returns_skeleton():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_LEAK_DRAFT), proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


# ───── 2 无人物卡 → skip ──
def test_no_character_card_skip():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_LEAK_DRAFT), _mk_project())
        assert out["verdict"] == "PASS"
        assert out["character_count"] == 0
    finally:
        _set_mode(bak)


# ───── 3 shadow 无 leak → PASS 无 warning ──
def test_shadow_legal_no_warning():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_LEGAL_DRAFT), proj)
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ───── 4 shadow + leak → 不上报 ──
def test_shadow_leak_no_report():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_LEAK_DRAFT), proj)
        # shadow 不写 violations
        assert out["violations"] == []
        assert out["warning"] is None
        # 但仍记录 leak_count
        assert out["leak_count"] >= 1
    finally:
        _set_mode(bak)


# ───── 5 active + leak → FAIL_MINOR ──
def test_active_leak_fail_minor():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_LEAK_DRAFT), proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"][0]["code"] == "CHARACTER_KNOWLEDGE_LEAK"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ───── 6 当场 reveal → 下场景合法 ──
def test_legal_after_in_scene_reveal():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_LEGAL_DRAFT), proj)
        # leak_count 可能 0
        assert out["leak_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 7 跨场景越权 → leak 计数≥1 ──
def test_cross_scene_unauthorized_knowledge_counted():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_LEAK_DRAFT), proj)
        assert out["leak_count"] >= 1
        sample = out["leak_samples"][0]
        assert sample["character"] == "张三"
        assert sample["fact_ref"] == "秘密"
    finally:
        _set_mode(bak)


# ───── 8 短稿 skip ──
def test_short_draft_skip():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write("张三知道秘密。" * 10), proj)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


# ───── 9 读取失败 → note ──
def test_read_failure_returns_note():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ───── 10 _mode 非法回落 ──
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


# ───── 11 _mode None 默认 shadow ──
def test_mode_default_shadow():
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ───── 12 _strip_changes 分隔符 ──
def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\nlog") == "正文。"


def test_strip_changes_plain():
    assert mod._strip_changes("正文。\n---CHANGES---\nlog") == "正文。"


# ───── 13 _cjk_count ──
def test_cjk_count_basic():
    assert mod._cjk_count("你好abc世界") == 4


# ───── 14 事件簇.json.clusters[].locked_facts[].fact 读取（重接线后真数据源）──
def test_load_fact_refs_from_locked_fact():
    proj = _mk_project(
        characters=[{"name": "张三"}],
        locked_facts=[{"fact": "藏宝图", "subject": "李四"}])
    refs = mod._load_fact_refs(proj)
    assert "藏宝图" in refs


def test_load_fact_refs_fallback_placeholder():
    proj = _mk_project(characters=[{"name": "张三"}])
    refs = mod._load_fact_refs(proj)
    # 占位词典含 "秘密"
    assert "秘密" in refs


# ───── 15 CLI subprocess 退出码 ──
def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CHARACTER_BELIEF_LEDGER_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
    r = _run_cli(_write(_LEAK_DRAFT), proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
    r = _run_cli(_write(_LEGAL_DRAFT), proj)
    assert r.returncode == 0, r.stderr


# ============================================================================
# 🔴 2026-07-01 语义匹配路径升级测试（真 embedding 后端才跑 · mock embedding_store）
# 钉死两头：默认(无真后端)行为逐字节不变 / mock 真后端后语义路径独立生效
# （抓字面子串未命中的同义改写，字面命中依旧优先标 literal）。
# ============================================================================

def _write_ledger(proj, ledger):
    (proj / "_数据库" / "character_belief_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False), encoding="utf-8")


def _fake_semantic_embed(text: str):
    """确定性假 embedding：命中「父亲被杀」或其同义改写关键片段 → 方向1，否则方向2（测试专用）。"""
    markers = ("父亲被杀", "爹被人害死", "害死", "被杀")
    if any(m in (text or "") for m in markers):
        return [1.0, 0.0]
    return [0.0, 1.0]


_PAD = "张三走进了城南的老酒馆里独自坐下慢慢喝酒。" * 30
# ledger 记「父亲被杀」·正文写同义改写「爹被人害死」→ 字面子串不重叠·只有语义路径能抓
_PARAPHRASE_DRAFT = (
    _PAD + _SCENE_SEP
    + "张三知道爹被人害死了。他攥紧了拳头。" * 5
)


def test_default_no_real_backend_paraphrase_missed():
    """无真后端 → 字面子串不命中同义改写 → 漏检（且不带 match_method/semantic_matching_active，
    逐字节零回归）。"""
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    try:
        os.environ.pop("EMBED_BACKEND", None)
        assert mod._has_real_embedding_backend() is False
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        _write_ledger(proj, {
            "characters": {"张三": {"known_facts": [], "unaware_of": ["f1"]}},
            "facts": {"f1": {"content": "父亲被杀"}},
        })
        out = mod.scan(_write(_PARAPHRASE_DRAFT), proj)
        assert out["leak_count"] == 0
        assert out["leak_samples"] == []
        assert "semantic_matching_active" not in out
    finally:
        _set_mode(bak)
        if bak_eb is None:
            os.environ.pop("EMBED_BACKEND", None)
        else:
            os.environ["EMBED_BACKEND"] = bak_eb


def test_semantic_backend_catches_paraphrase_literal_misses(monkeypatch):
    """mock 真后端：字面「父亲被杀」未命中窗口·语义比对抓住同义改写「爹被人害死」→ leak。"""
    monkeypatch.setenv("EMBED_BACKEND", "test_semantic")
    monkeypatch.setattr(embedding_store, "compute_embedding", _fake_semantic_embed)
    monkeypatch.setenv("CHARACTER_BELIEF_LEDGER_MODE", "active")
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
    _write_ledger(proj, {
        "characters": {"张三": {"known_facts": [], "unaware_of": ["f1"]}},
        "facts": {"f1": {"content": "父亲被杀"}},
    })
    out = mod.scan(_write(_PARAPHRASE_DRAFT), proj)
    assert out["semantic_matching_active"] is True
    assert out["leak_count"] >= 1
    sample = out["leak_samples"][0]
    assert sample["fact_ref"] == "父亲被杀"
    assert sample["match_method"] == "semantic"
    assert sample["reason"] == "unaware_of"
    assert out["verdict"] == "FAIL_MINOR"


def test_semantic_backend_still_reports_literal_hits(monkeypatch):
    """mock 真后端：字面命中依旧优先标 literal（语义路径升级不丢失原有字面检测能力）。"""
    monkeypatch.setenv("EMBED_BACKEND", "test_semantic")
    monkeypatch.setattr(embedding_store, "compute_embedding", lambda t: [1.0, 0.0])
    monkeypatch.setenv("CHARACTER_BELIEF_LEDGER_MODE", "active")
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
    _write_ledger(proj, {
        "characters": {"张三": {"known_facts": [], "unaware_of": ["f_secret"]}},
        "facts": {"f_secret": {"content": "宝藏"}},
    })
    draft = _write(_PAD + "张三知道宝藏埋在哪里。" * 5)
    out = mod.scan(draft, proj)
    assert out["leak_count"] >= 1
    assert out["leak_samples"][0]["match_method"] == "literal"


def test_has_real_embedding_backend_env_gate(monkeypatch):
    """_has_real_embedding_backend 门控：未设/hash → False；非空非 hash / GEN_EMBED__* → True。"""
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    assert mod._has_real_embedding_backend() is False
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    assert mod._has_real_embedding_backend() is False
    monkeypatch.setenv("EMBED_BACKEND", "local")
    assert mod._has_real_embedding_backend() is True
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    monkeypatch.setenv("GEN_EMBED__default__API_KEY", "x")
    assert mod._has_real_embedding_backend() is True


def test_semantic_leak_floor_env_override(monkeypatch):
    """env CHARACTER_BELIEF_SEMANTIC_FLOOR 覆盖 > 默认值·非法值回退默认。"""
    monkeypatch.delenv("CHARACTER_BELIEF_SEMANTIC_FLOOR", raising=False)
    assert mod._semantic_leak_floor() == mod.DEFAULT_SEMANTIC_LEAK_FLOOR
    monkeypatch.setenv("CHARACTER_BELIEF_SEMANTIC_FLOOR", "0.7")
    assert mod._semantic_leak_floor() == 0.7
    monkeypatch.setenv("CHARACTER_BELIEF_SEMANTIC_FLOOR", "nope")
    assert mod._semantic_leak_floor() == mod.DEFAULT_SEMANTIC_LEAK_FLOOR


def test_placeholder_path_untouched_by_semantic_upgrade(monkeypatch):
    """占位词典路径（无 ledger）不受语义升级影响：即便真后端就绪也只走原字面逻辑
    （任务范围明确只升级持久化 ledger 路径）。"""
    monkeypatch.setenv("EMBED_BACKEND", "test_semantic")
    monkeypatch.setattr(embedding_store, "compute_embedding", _fake_semantic_embed)
    monkeypatch.setenv("CHARACTER_BELIEF_LEDGER_MODE", "active")
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])  # 无 ledger
    out = mod.scan(_write(_LEAK_DRAFT), proj)
    assert out["ledger_source"] == "placeholder"
    assert "semantic_matching_active" not in out
    for lk in out["leak_samples"]:
        assert "match_method" not in lk


# ═══════════════════════════════════════════════════════════════════════════
# 🔴 2026-07-03 Wave-4：语义路径批量 prefetch（一次 prefetch 取代逐条各自后端调用）
# ═══════════════════════════════════════════════════════════════════════════

def test_prefetch_called_once_before_detection_pass(monkeypatch):
    """语义路径下 _detect_leaks_from_ledger 应先一次性 prefetch 全部待 embed 文本
    （forbidden 短语 + 命中知识动词的窗口），而非逐条各自触发后端调用。"""
    calls = []

    def fake_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)),
                "cache_hits": 0, "computed": len(set(texts))}

    monkeypatch.setenv("EMBED_BACKEND", "test_semantic")
    monkeypatch.setattr(embedding_store, "compute_embedding", _fake_semantic_embed)
    monkeypatch.setattr(embedding_store, "prefetch_embeddings", fake_prefetch)
    monkeypatch.setenv("CHARACTER_BELIEF_LEDGER_MODE", "active")
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
    _write_ledger(proj, {
        "characters": {"张三": {"known_facts": [], "unaware_of": ["f1"]}},
        "facts": {"f1": {"content": "父亲被杀"}},
    })
    out = mod.scan(_write(_PARAPHRASE_DRAFT), proj)
    # prefetch 不论场景/命中窗口多少都恰好触发 1 次
    assert len(calls) == 1
    prefetched = calls[0]
    # forbidden 短语（ledger 原文）必在其中
    assert "父亲被杀" in prefetched
    # 至少捕到命中知识动词的正文窗口（同义改写片段）
    assert any("爹被人害死" in t for t in prefetched)
    # 语义检测结果不受批量化影响（与 test_semantic_backend_catches_paraphrase_literal_misses 同一断言）
    assert out["leak_count"] >= 1
    assert out["leak_samples"][0]["match_method"] == "semantic"


def test_prefetch_not_called_when_semantic_path_inactive(monkeypatch):
    """无真后端（默认）→ 走字面逻辑 → prefetch_embeddings 零调用（零回归）。"""
    calls = []

    def fake_prefetch(texts):
        calls.append(list(texts))
        return {}

    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    monkeypatch.setattr(embedding_store, "prefetch_embeddings", fake_prefetch)
    monkeypatch.setenv("CHARACTER_BELIEF_LEDGER_MODE", "active")
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
    _write_ledger(proj, {
        "characters": {"张三": {"known_facts": [], "unaware_of": ["f1"]}},
        "facts": {"f1": {"content": "父亲被杀"}},
    })
    mod.scan(_write(_PARAPHRASE_DRAFT), proj)
    assert calls == []
