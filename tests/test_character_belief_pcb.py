# -*- coding: utf-8 -*-
"""角色信息差(per-character belief) P0 回归锁 — 2026-06-29。

提案核心：重心在【生成层注入】非检测——让写手按各角色受限认知写，物理 masking 防角色用
不该知道的知识穿帮（扮猪吃老虎/信息差/悬念底层引擎）。Phase A 已建 schema，本批做注入+消费+检测。

覆盖三层：
  A. build_manifest._sanitize_character_belief  投射逻辑（knows 门控 / unaware+未到期负向 / can_speak /
     默认安全闸 None）。
  B. build_manifest._collect_scene_character_knowledge  接持久化 ledger + cluster scene_storyboard →
     manifest 字段（无 ledger → [] 向后兼容）。
  C. gen_writer._build_belief_section + build_prompt  消费（边界 prompt 渲染 / H7 在场 / 默认安全空）。
  D. character_belief_ledger_scanner  接真 ledger 检测（leak / can_speak=false / 合法 / 占位 fallback /
     advisory 永不 hard_gate）。

纯确定性（0 gen-model 调用·0 联网）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import gen_writer as gw  # noqa: E402
import character_belief_ledger_scanner as sc  # noqa: E402
import audit_hub  # noqa: E402


# ============================================================================
# A. _sanitize_character_belief — 投射逻辑（单一真理源·确定性）
# ============================================================================

def _ledger():
    return {
        "characters": {
            "A": {
                "known_facts": [
                    {"fact_id": "f1", "content": "宝藏在井底",
                     "learned_at_cluster": "cluster_001", "can_speak": True},
                    {"fact_id": "f2", "content": "B是叛徒",
                     "learned_at_cluster": "cluster_005", "can_speak": True},
                    {"fact_id": "f3", "content": "密道口令",
                     "learned_at_cluster": "cluster_001", "can_speak": False},
                ],
                "unaware_of": ["f4"],
            },
            "B": {
                "known_facts": [{"fact_id": "f4", "content": "A的真名",
                                 "learned_at_cluster": "cluster_001"}],
                "unaware_of": ["f1"],
            },
        },
        "facts": {"f4": {"content": "A的真名"}, "f1": {"content": "宝藏在井底"}},
    }


def _scene():
    return {"ch": 0, "participants": ["A", "B"], "focal_character": "A",
            "focalization_mode": "internal", "knowledge_gap_mode": "reader_adv"}


def test_knows_filtered_by_learned_cluster():
    """knows 只含 learned_at_cluster <= current 的 fact（复用 _cluster_due 门控）。"""
    proj = bm._sanitize_character_belief(_ledger(), _scene(), "cluster_002")
    a_knows = {k["fact_id"] for k in proj["characters"]["A"]["knows"]}
    assert "f1" in a_knows, "learned cluster_001 <= cluster_002 → 应在 knows"
    assert "f3" in a_knows, "learned cluster_001（can_speak=False）仍知道 → 在 knows"
    assert "f2" not in a_knows, "learned cluster_005 > cluster_002 → 未到期·不在 knows"


def test_future_known_fact_goes_to_must_not_reference():
    """未到期 known_fact（learned > current）→ must_not_reference（防 FUTURE_KNOWLEDGE_LEAK）。"""
    proj = bm._sanitize_character_belief(_ledger(), _scene(), "cluster_002")
    a_must = {m["fact_id"] for m in proj["characters"]["A"]["must_not_reference"]}
    assert "f2" in a_must, "本块尚未获知的 fact 应进 must_not_reference 做负向 masking"


def test_unaware_of_goes_to_must_not_reference():
    """unaware_of 的 fact → must_not_reference 负向指令。"""
    proj = bm._sanitize_character_belief(_ledger(), _scene(), "cluster_002")
    a_must = {m["fact_id"] for m in proj["characters"]["A"]["must_not_reference"]}
    b_must = {m["fact_id"] for m in proj["characters"]["B"]["must_not_reference"]}
    assert "f4" in a_must, "A unaware_of f4 → must_not_reference"
    assert "f1" in b_must, "B unaware_of f1 → must_not_reference"


def test_can_speak_false_retained_in_knows():
    """can_speak=False 的 fact 留在 knows 并带 can_speak 标记（知道但不能说出口）。"""
    proj = bm._sanitize_character_belief(_ledger(), _scene(), "cluster_002")
    f3 = [k for k in proj["characters"]["A"]["knows"] if k["fact_id"] == "f3"]
    assert f3 and f3[0]["can_speak"] is False


def test_scene_meta_passthrough():
    """focal_character / focalization_mode / knowledge_gap_mode / participants 透传。"""
    proj = bm._sanitize_character_belief(_ledger(), _scene(), "cluster_002")
    assert proj["focal_character"] == "A"
    assert proj["focalization_mode"] == "internal"
    assert proj["knowledge_gap_mode"] == "reader_adv"
    assert proj["participants"] == ["A", "B"]


def test_default_safe_no_participants_returns_none():
    """默认安全闸：scene 无 participants → None（不注入·向后兼容旧 storyboard）。"""
    assert bm._sanitize_character_belief(_ledger(), {"ch": 1}, "cluster_002") is None
    assert bm._sanitize_character_belief(_ledger(), {"ch": 1, "participants": []}, "cluster_002") is None


def test_default_safe_empty_ledger_returns_none():
    """默认安全闸：ledger 空 / 无 characters → None。"""
    assert bm._sanitize_character_belief({}, _scene(), "cluster_002") is None
    assert bm._sanitize_character_belief({"characters": {}}, _scene(), "cluster_002") is None


def test_known_fact_missing_learned_cluster_is_safe_known():
    """known_fact 无 learned_at_cluster 标记 → 默认安全闸·当已知（透传进 knows）。"""
    led = {"characters": {"A": {"known_facts": [{"fact_id": "x", "content": "某事"}]}},
           "facts": {}}
    proj = bm._sanitize_character_belief(led, {"participants": ["A"]}, "cluster_002")
    assert {k["fact_id"] for k in proj["characters"]["A"]["knows"]} == {"x"}


# ============================================================================
# B. _collect_scene_character_knowledge — 接持久化 ledger + cluster storyboard
# ============================================================================

def _mk_proj_with_ledger(tmp, ledger=None, storyboard=None):
    proj = tmp / "pcb_book"
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(json.dumps({
        "clusters": [{
            "cluster_id": "cluster_002", "status": "in_progress",
            "chapter_range": [1, 4], "scope_summary": "测试块",
            "scene_storyboard": storyboard if storyboard is not None else [
                {"ch": 0, "participants": ["A", "B"], "focal_character": "A",
                 "focalization_mode": "internal", "knowledge_gap_mode": "reader_adv"},
                {"ch": 1, "title": "无 participants 场景"},  # 无 participants → 跳过
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    if ledger is not None:
        (db / "character_belief_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return proj


def test_collect_scene_character_knowledge_e2e():
    """端到端：ledger + cluster storyboard → 每 scene 投射（无 participants 场景被跳过）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_proj_with_ledger(Path(td), ledger=_ledger())
        scanner = bm.DatabaseScanner(proj, 1)
        out = bm._collect_scene_character_knowledge(scanner, "cluster_002")
    assert len(out) == 1, "只有带 participants 的场景产投射·无 participants 场景跳过"
    assert out[0]["scene_index"] == 0
    assert set(out[0]["characters"].keys()) == {"A", "B"}


def test_collect_no_ledger_returns_empty():
    """默认安全闸：无 character_belief_ledger.json → []（今天所有旧书·零行为变化）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_proj_with_ledger(Path(td), ledger=None)
        scanner = bm.DatabaseScanner(proj, 1)
        out = bm._collect_scene_character_knowledge(scanner, "cluster_002")
    assert out == []


def test_collect_scene_index_backfilled_when_missing():
    """scene 无 ch 字段 → scene_index 回填为遍历序号。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_proj_with_ledger(Path(td), ledger=_ledger(), storyboard=[
            {"participants": ["A", "B"]},  # 无 ch
        ])
        scanner = bm.DatabaseScanner(proj, 1)
        out = bm._collect_scene_character_knowledge(scanner, "cluster_002")
    assert out and out[0]["scene_index"] == 0


def test_full_manifest_field_present_and_cache_registered():
    """字段接线进 _build_cache_layout（SEMI_STATIC_70·同 cluster 内不变）。"""
    layout = bm._build_cache_layout()
    assert "scene_character_knowledge" in layout["SEMI_STATIC_70_cacheable"]
    # 分层互斥不被破坏
    static = set(layout["STATIC_99_cacheable"])
    dyn = set(layout["DYNAMIC_30_cacheable"])
    assert "scene_character_knowledge" not in static
    assert "scene_character_knowledge" not in dyn


# ============================================================================
# C. gen_writer._build_belief_section + build_prompt — 消费
# ============================================================================

def _write_manifest(db, scene_knowledge):
    mdir = db / ".manifest"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "ch_001.json").write_text(json.dumps({
        "chapter": 1, "scene_character_knowledge": scene_knowledge,
    }, ensure_ascii=False), encoding="utf-8")


def _belief_scene_knowledge():
    return [{
        "scene_index": 0, "focal_character": "林晚",
        "focalization_mode": "internal", "knowledge_gap_mode": "reader_adv",
        "participants": ["林晚", "陈默"],
        "characters": {
            "林晚": {
                "knows": [{"fact_id": "k1", "content": "KNOWN_林晚知道密道_KS", "can_speak": True}],
                "must_not_reference": [{"fact_id": "m1", "content": "MUSTNOT_陈默是叛徒_SECRET",
                                        "reason": "林晚 不知情（unaware_of）"}],
            },
            "陈默": {
                "knows": [{"fact_id": "k2", "content": "SPEAKNO_陈默私藏口令_CS", "can_speak": False}],
                "must_not_reference": [],
            },
        },
        "_directive": "信息差物理 masking",
    }]


def test_build_belief_section_renders_knows_and_mustnot():
    """_build_belief_section 渲染各角色 knows / must_not / can_speak=false 标记。"""
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "ch_001.json"
        mp.write_text(json.dumps({"scene_character_knowledge": _belief_scene_knowledge()},
                                 ensure_ascii=False), encoding="utf-8")
        sec = gw._build_belief_section(mp)
    assert "角色认知边界" in sec
    assert "KNOWN_林晚知道密道_KS" in sec, "knows 应渲染"
    assert "MUSTNOT_陈默是叛徒_SECRET" in sec, "must_not_reference 应渲染"
    assert "不知道" in sec, "must_not 应标负向"
    assert "不能说出口" in sec, "can_speak=False 应标知道但不能说出口"


def test_build_belief_section_default_safe_empty():
    """默认安全：无 scene_character_knowledge / 空 → ""（零回归）。"""
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "ch_001.json"
        mp.write_text(json.dumps({"chapter": 1}, ensure_ascii=False), encoding="utf-8")
        assert gw._build_belief_section(mp) == ""
        mp.write_text(json.dumps({"scene_character_knowledge": []}, ensure_ascii=False), encoding="utf-8")
        assert gw._build_belief_section(mp) == ""


def test_build_prompt_injects_belief_boundary_and_h7():
    """端到端：build_prompt 注入角色认知边界 + H7·在场 A 的 must_not（B 私有/林晚不知的事）标 不知道。"""
    _bak = os.environ.get("SNIPPET_SEED_MODE")
    os.environ["SNIPPET_SEED_MODE"] = "off"
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "_数据库"
            db.mkdir(parents=True)
            (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {}}, ensure_ascii=False),
                                          encoding="utf-8")
            (db / "事件簇.json").write_text(json.dumps({"clusters": [
                {"cluster_id": "cluster_001", "status": "in_progress", "chapter_range": [1, 4],
                 "scope_summary": "测试"}]}, ensure_ascii=False), encoding="utf-8")
            (db / "人物卡.json").write_text(json.dumps({"characters": [
                {"name": "林晚", "role": "主角"}, {"name": "陈默", "role": "ally"}]},
                ensure_ascii=False), encoding="utf-8")
            _write_manifest(db, _belief_scene_knowledge())
            system, user, _seed = gw.build_prompt(root, 1, 1, polish_view={'idx': 0, 'total': 1, 'scene_text': '井边的场景稿正文。' * 10})
            full = system + "\n" + user
        # H7 规则在 system
        assert "角色信息差(per-character belief)" in system, "system 应含 H7 角色信息差规则"
        # 认知边界段在 user
        assert "角色认知边界" in user, "user 应注入角色认知边界段"
        assert "KNOWN_林晚知道密道_KS" in full, "林晚 knows 应注入"
        assert "MUSTNOT_陈默是叛徒_SECRET" in full, "林晚 must_not（不知道）应注入供 masking"
        assert "SPEAKNO_陈默私藏口令_CS" in full, "陈默 can_speak=False fact 应注入（标不能说出口）"
    finally:
        if _bak is None:
            os.environ.pop("SNIPPET_SEED_MODE", None)
        else:
            os.environ["SNIPPET_SEED_MODE"] = _bak


def test_build_prompt_no_belief_default_safe():
    """默认安全：manifest 无 scene_character_knowledge → prompt 不含认知边界段（零回归）。"""
    _bak = os.environ.get("SNIPPET_SEED_MODE")
    os.environ["SNIPPET_SEED_MODE"] = "off"
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "_数据库"
            db.mkdir(parents=True)
            (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {}}, ensure_ascii=False),
                                          encoding="utf-8")
            (db / "事件簇.json").write_text(json.dumps({"clusters": [
                {"cluster_id": "cluster_001", "status": "in_progress", "chapter_range": [1, 4],
                 "scope_summary": "测试"}]}, ensure_ascii=False), encoding="utf-8")
            _write_manifest(db, [])  # 空
            _system, user, _seed = gw.build_prompt(root, 1, 1, polish_view={'idx': 0, 'total': 1, 'scene_text': '井边的场景稿正文。' * 10})
        assert "## 🧠 角色认知边界" not in user
    finally:
        if _bak is None:
            os.environ.pop("SNIPPET_SEED_MODE", None)
        else:
            os.environ["SNIPPET_SEED_MODE"] = _bak


# ============================================================================
# D. character_belief_ledger_scanner — 接真 ledger 检测
# ============================================================================

_SCENE_PAD = "张三走进了城南的老酒馆里坐下慢慢喝酒。" * 40  # >500 CJK


def _mk_scanner_proj(tmp, ledger=None, characters=None):
    proj = tmp / "scan_book"
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    chars = characters if characters is not None else [{"name": "张三", "role": "主角"}]
    (db / "人物卡.json").write_text(json.dumps({"characters": chars}, ensure_ascii=False),
                                    encoding="utf-8")
    if ledger is not None:
        (db / "character_belief_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return proj


def _draft(tmp, text):
    p = tmp / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _set_mode(m):
    if m is None:
        os.environ.pop("CHARACTER_BELIEF_LEDGER_MODE", None)
    else:
        os.environ["CHARACTER_BELIEF_LEDGER_MODE"] = m


def test_scanner_ledger_detects_unaware_leak():
    """接真 ledger·active：张三提及 unaware_of 的 fact「宝藏」→ CHARACTER_KNOWLEDGE_LEAK。"""
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ledger = {"characters": {"张三": {"known_facts": [], "unaware_of": ["f_secret"]}},
                      "facts": {"f_secret": {"content": "宝藏"}}}
            proj = _mk_scanner_proj(tmp, ledger=ledger)
            draft = _draft(tmp, _SCENE_PAD + "张三知道宝藏埋在哪里。" * 5)
            out = sc.scan(draft, proj)
        assert out["ledger_source"] == "persistent"
        assert out["leak_count"] >= 1
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "CHARACTER_KNOWLEDGE_LEAK"
        assert out["gate_level"] == "advisory"
        assert out["leak_samples"][0]["reason"] == "unaware_of"
    finally:
        _set_mode(bak)


def test_scanner_ledger_detects_cannot_speak_leak():
    """接真 ledger·active：张三知道但 can_speak=false 的 fact 出现在知识动词窗口 → leak。"""
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ledger = {"characters": {"张三": {"known_facts": [
                {"fact_id": "f3", "content": "口令", "learned_at_cluster": "cluster_001",
                 "can_speak": False}], "unaware_of": []}}, "facts": {}}
            proj = _mk_scanner_proj(tmp, ledger=ledger)
            draft = _draft(tmp, _SCENE_PAD + "张三知道口令是什么。" * 5)
            out = sc.scan(draft, proj)
        assert out["leak_count"] >= 1
        assert out["leak_samples"][0]["reason"] == "known_but_cannot_speak"
    finally:
        _set_mode(bak)


def test_scanner_ledger_legal_known_speakable_no_leak():
    """接真 ledger：张三确知且可说的 fact「宝藏」被引用 → 不算 leak（先扣除 speakable）。"""
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ledger = {"characters": {"张三": {"known_facts": [
                {"fact_id": "f1", "content": "宝藏", "learned_at_cluster": "cluster_001",
                 "can_speak": True}], "unaware_of": []}}, "facts": {}}
            proj = _mk_scanner_proj(tmp, ledger=ledger)
            draft = _draft(tmp, _SCENE_PAD + "张三知道宝藏在井底。" * 5)
            out = sc.scan(draft, proj)
        assert out["ledger_source"] == "persistent"
        assert out["leak_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_scanner_no_ledger_falls_back_to_placeholder():
    """无 character_belief_ledger.json → ledger_source=placeholder（向后兼容·占位逻辑）。"""
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("active")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            proj = _mk_scanner_proj(tmp, ledger=None)
            draft = _draft(tmp, _SCENE_PAD + "张三知道宝藏在井底。" * 5)
            out = sc.scan(draft, proj)
        assert out["ledger_source"] == "placeholder"
        assert "fact_ref_count" in out
    finally:
        _set_mode(bak)


def test_scanner_ledger_shadow_no_report():
    """接真 ledger·shadow：检测到 leak 但不上报 violations（默认观察期·advisory）。"""
    bak = os.environ.get("CHARACTER_BELIEF_LEDGER_MODE")
    try:
        _set_mode("shadow")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ledger = {"characters": {"张三": {"known_facts": [], "unaware_of": ["f_secret"]}},
                      "facts": {"f_secret": {"content": "宝藏"}}}
            proj = _mk_scanner_proj(tmp, ledger=ledger)
            draft = _draft(tmp, _SCENE_PAD + "张三知道宝藏埋在哪里。" * 5)
            out = sc.scan(draft, proj)
        assert out["leak_count"] >= 1, "shadow 仍记录 leak_count"
        assert out["violations"] == [], "shadow 不写 violations"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


def test_character_knowledge_leak_never_hard_gate():
    """北极星⑤：CHARACTER_KNOWLEDGE_LEAK 绝不进 audit_hub.HARD_GATE_CODES（先 advisory 观察期）。"""
    assert "CHARACTER_KNOWLEDGE_LEAK" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("CHARACTER_KNOWLEDGE_LEAK") == "advisory"
