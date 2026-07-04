"""deus_ex_solution_audit 专属测试 — Deus Ex Solution Audit（advisory · 2026-06-20）"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import deus_ex_solution_audit as des  # noqa: E402
import embedding_store  # noqa: E402


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


def _filler(n=25):
    return "\n".join(["夜色压在山脊石阶上他独自向上踏步影子拉得很长。"] * n)


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _write_manifest(is_finale: bool):
    p = Path(tempfile.mkdtemp()) / "manifest.json"
    p.write_text(json.dumps({"is_volume_finale": is_finale}, ensure_ascii=False),
                 encoding="utf-8")
    return p


def _mk_project(history=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if history:
        obj = history
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# ---------- 触发门控 ----------

def test_scan_skips_when_not_finale():
    """非 finale → 跳过。"""
    p = _write(_filler())
    m = _write_manifest(False)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["is_volume_finale"] is False
        assert "非 finale" in r.get("note", "")
        assert r["verdict"] == "PASS"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_no_manifest_skips():
    """无 manifest → 视为非 finale → skip。"""
    p = _write(_filler())
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p))
        assert r["is_volume_finale"] is False
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


# ---------- 模式 ----------

def test_off_skeleton():
    p = _write(_filler())
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "off"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["mode"] == "off"
        assert r["violations"] == []
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_default_shadow():
    p = _write(_filler())
    m = _write_manifest(True)
    try:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        r = des.scan(str(p), manifest_path=str(m))
        assert r["mode"] == "shadow"
    finally:
        p.unlink(missing_ok=True)


def test_invalid_mode_fallback():
    p = _write(_filler())
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "bogus"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["mode"] == "shadow"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_short_skip():
    p = _write("末段。")
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert "太短" in r.get("note", "")
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


# ---------- audit_deus_ex 核心 ----------

def test_audit_external_deus_ex_risk():
    """末段大量外力锚词 + 解决动作 → deus_ex_risk。"""
    body = _filler(30)
    tail = ("突然一道身影从天而降斩了恶龙。" + "没想到天降救兵恰好出现救了主角。" * 4)
    text = body + "\n" + tail
    r = des.audit_deus_ex(text)
    assert r["external_deus_hits_count"] >= 3
    assert r["deus_ex_risk"] is True


def test_audit_clean_anchored():
    """末段元素都在 body / history 中铺垫过 → 不报。"""
    # body 多次提到 林师兄 + 玄铁剑
    body = ("林师兄递给我玄铁剑。" * 3) + _filler(20)
    # tail 解决方案用同样元素
    tail = "我取出玄铁剑挡在身前林师兄出手击败了魔王。"
    text = body + "\n" + tail
    r = des.audit_deus_ex(text)
    assert r["underbacked_count"] == 0


def test_audit_history_helps_anchor():
    """body 没铺垫·history 提到过 → 仍有 anchors。"""
    body = _filler(30)
    tail = "我取出玄铁剑挡在身前击败了魔王。"
    history = "玄铁剑 玄铁剑 玄铁剑"
    r = des.audit_deus_ex(body + "\n" + tail, history_text=history)
    assert r["anchors_per_element"].get("玄铁剑", 0) >= 3


def test_audit_no_elements_no_risk():
    """末段无解决方案锚词 → 无元素 → 无风险。"""
    text = _filler(20) + "\n" + _filler(5)
    r = des.audit_deus_ex(text)
    # external 0 / elements 0 → risk False
    assert r["deus_ex_risk"] is False


# ---------- scan() active ----------

def test_scan_active_deus_ex_reports():
    """finale + 大量外力锚词 → FAIL_MINOR。"""
    body = _filler(30)
    tail = ("突然恶龙出现。" + "没想到天降救兵恰好出现。" * 3 +
            "忽然神兽降临斩了一切。" * 2)
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["verdict"] == "FAIL_MINOR"
        assert r["violations"][0]["kind"] == "deus_ex_solution"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_active_anchored_pass():
    """finale + 元素都铺垫过 → PASS。"""
    body = ("林师兄是我兄长林师兄交给我玄铁剑林师兄教我剑法。" * 3) + _filler(20)
    tail = "我取出玄铁剑林师兄出手击败了魔王。"
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["verdict"] == "PASS"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    body = _filler(30)
    tail = "突然恶龙出现没想到天降救兵恰好出现忽然神兽降临。" * 3
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "shadow"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["mode"] == "shadow"
        assert r["violations"] == []
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


# ---------- history 注入 ----------

def test_scan_history_anchors():
    """history 提到 resolution 元素 → 减少 underbacked。"""
    body = _filler(30)
    tail = "我取出玄铁剑林师兄出手击败了魔王。"
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    proj = _mk_project(history={
        "cluster_001": {"summary": "林师兄登场赠玄铁剑。",
                        "key_events": ["林师兄护我", "玄铁剑出鞘"]},
        "cluster_002": {"summary": "林师兄出手救我玄铁剑斩魔气。"},
    })
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), project_root=proj, manifest_path=str(m))
        # 历史有铺垫→不该是 underbacked
        anchors = r.get("anchors_per_element", {})
        assert sum(anchors.values()) > 0
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


# ---------- helpers ----------

def test_read_manifest_nested():
    """嵌套 cluster.is_volume_finale 读得到。"""
    p = Path(tempfile.mkdtemp()) / "m.json"
    p.write_text(json.dumps({"cluster": {"is_volume_finale": True}}), encoding="utf-8")
    assert des._read_is_volume_finale(str(p), None) is True


def test_read_manifest_bad_json():
    p = Path(tempfile.mkdtemp()) / "m.json"
    p.write_text("{ bad", encoding="utf-8")
    assert des._read_is_volume_finale(str(p), None) is False


def test_strip_changes():
    assert des._strip_changes("正文。\n---CHANGES---\nx") == "正文。"


def test_read_history_list_form():
    """故事块摘要.json 是 list 形式也能读。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps([{"summary": "片段一玄铁剑出场。"}, {"summary": "片段二林师兄登场。"}]),
        encoding="utf-8")
    h = des._read_historical_text(proj)
    assert "玄铁剑" in h
    assert "林师兄" in h


# ---------- advisory ----------

def test_always_advisory():
    body = _filler(30)
    tail = "突然恶龙出现没想到天降救兵恰好出现忽然神兽降临。" * 3
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["gate_level"] == "advisory"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_hard_gate():
    assert des.ISSUE_CODE == "DEUS_EX_SOLUTION"
    try:
        import audit_hub
        assert "DEUS_EX_SOLUTION" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass


def test_read_failure():
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"), manifest_path=str(m))
        assert "读取失败" in r.get("note", "")
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)


# ════════════════════════════════════════════════════════════════════
# 🔴 2026-07-04 内容语义 embedding 路径（W6-C 迁移：风格模型→bge 内容模型）
# mock 面：直接 monkeypatch embedding_store.content_backend_available /
# compute_content_embedding(_batch) / prefetch_content_embeddings（内容后端可用性
# 由 venv+infer 脚本+模型目录决定，不再是 EMBED_BACKEND 环境变量能摆弄的）
# ════════════════════════════════════════════════════════════════════
def test_content_backend_ready_false_by_default():
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        assert des._content_backend_ready() is False
    finally:
        embedding_store.content_backend_available = orig


def test_content_backend_ready_true_when_available():
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: True
    try:
        assert des._content_backend_ready() is True
    finally:
        embedding_store.content_backend_available = orig


def test_semantic_anchor_hit_resolves_underbacked_via_history():
    """字面子串扫不出的意译铺垫（history 提到"神剑"但不是字面元素名"上古神"），内容后端
    语义扫描应能补上——这正是本次升级要根治的漏检（前置断言：字面法确实测不出）。"""
    body = _filler(30)
    tail = "我取出上古神剑一剑斩了敌首。"
    history = "很久以前一柄神剑现世无人可挡。" * 3
    text = body + "\n" + tail

    # 前置：默认门控关（autouse fixture）→ 字面法必须测不出这个意译铺垫
    pre = des.audit_deus_ex(text, history_text=history)
    assert "上古神" in pre["underbacked"], "前置条件：字面法必须测不出这个意译铺垫"
    assert pre["anchor_source_per_element"]["上古神"] == "literal_substring"

    orig_avail = embedding_store.content_backend_available
    orig_batch = embedding_store.compute_content_embeddings_batch
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _fake(t):
        return [1.0, 0.0] if "神剑" in t else [0.0, 1.0]

    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embeddings_batch = lambda texts: [_fake(t) for t in texts]
    embedding_store.compute_content_embedding = _fake
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = des.audit_deus_ex(text, history_text=history)
        assert "上古神" not in r["underbacked"]
        assert r["underbacked_count"] == 0
        assert r["anchor_source_per_element"]["上古神"] == "embedding_cosine"
        assert r["anchors_per_element"]["上古神"] == des.ANCHOR_FLOOR
        assert r["deus_ex_risk"] is False
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embeddings_batch = orig_batch
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch


def test_literal_sufficient_anchor_source_stays_literal():
    """字面 anchors_count 已达标（≥ ANCHOR_FLOOR）→ 即便内容后端就绪也不该被语义"升级"，
    anchor_source 仍是 literal_substring（字面子串永远是兜底优先判定）。"""
    body = ("林师兄递给我玄铁剑。" * 3) + _filler(20)
    tail = "我取出玄铁剑挡在身前林师兄出手击败了魔王。"
    text = body + "\n" + tail

    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings
    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embedding = lambda t: [1.0, 0.0]   # 随便返回什么都不该被采用
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = des.audit_deus_ex(text)
        assert r["underbacked_count"] == 0
        for el, src in r["anchor_source_per_element"].items():
            assert src == "literal_substring", f"{el} 不该被语义路径覆盖"
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch


def test_semantic_below_threshold_stays_underbacked():
    """内容后端就绪但相似度 < 阈值（history 与 resolution 元素完全无关）→ 不误判为已铺垫，
    仍报 underbacked（不是随便配了后端就无脑判定已铺垫）。"""
    body = _filler(30)
    tail = "我取出上古神剑一剑斩了敌首。"
    history = "完全不相关的历史段落内容在这里展开描写风景。" * 3
    text = body + "\n" + tail

    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings
    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embedding = lambda t: [1.0, 0.0] if "神剑" in t else [0.0, 1.0]
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = des.audit_deus_ex(text, history_text=history)
        assert "上古神" in r["underbacked"]
        assert r["anchor_source_per_element"]["上古神"] == "literal_substring"
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch


def test_scan_active_propagates_anchor_source_and_resolves_via_semantic():
    """scan() 全链路：内容后端命中语义铺垫 → underbacked 清空 → verdict 从 FAIL_MINOR 变 PASS，
    anchor_source_per_element 正确透传到顶层输出。"""
    body = _filler(30)
    tail = "我取出上古神剑一剑斩了敌首。"
    history = "很久以前一柄神剑现世无人可挡。" * 3
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    proj = _mk_project(history={
        "cluster_001": {"summary": "很久以前一柄神剑现世无人可挡。"},
    })

    bak_mode = os.environ.get("DEUS_EX_AUDIT_MODE")
    os.environ["DEUS_EX_AUDIT_MODE"] = "active"
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings
    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embedding = lambda t: [1.0, 0.0] if "神剑" in t else [0.0, 1.0]
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = des.scan(str(p), project_root=proj, manifest_path=str(m))
        assert r["verdict"] == "PASS"
        assert r["anchor_source_per_element"].get("上古神") == "embedding_cosine"
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch
        if bak_mode is not None:
            os.environ["DEUS_EX_AUDIT_MODE"] = bak_mode
        else:
            os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_audit_deus_ex_content_backend_off_matches_original_logic():
    """🔴 零回归锁：内容后端不可用 → audit_deus_ex 结果与原纯字面子串逻辑逐项一致
    （anchors_per_element / underbacked / deus_ex_risk），且 compute_content_embedding
    即便被换成任意值也绝不会被调用（_embed_history_once 在最前面短路返回 None）。"""
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding

    def _boom(t):
        raise AssertionError("内容后端不可用时绝不应调用 compute_content_embedding")

    embedding_store.content_backend_available = lambda: False
    embedding_store.compute_content_embedding = _boom
    try:
        body = _filler(30)
        tail = "我取出上古神剑一剑斩了敌首。"
        history = "很久以前一柄神剑现世无人可挡。" * 3
        text = body + "\n" + tail

        r = des.audit_deus_ex(text, history_text=history)
        assert r["anchors_per_element"] == {"上古神": 0}
        assert r["underbacked"] == ["上古神"]
        assert r["anchor_source_per_element"] == {"上古神": "literal_substring"}
        assert r["deus_ex_risk"] is True
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single


# ════════════════════════════════════════════════════════════════════
# 🔴 2026-07-03 Wave-4 性能层：audit_deus_ex 批量 prefetch（历史段落 + 每个
# resolution 元素语境窗口 query 两侧文本一次性预热，其后 _embed_history_once /
# _semantic_anchor_hit 内的逐条 compute_content_embedding 全部命中缓存·内容后端
# 子进程按条调用极贵）
# ════════════════════════════════════════════════════════════════════
def test_audit_deus_ex_prefetches_history_and_element_queries_once():
    body = _filler(30)
    tail_text = "我取出玄铁剑挡在身前林师兄出手击败了魔王。"
    history = "玄铁剑传说流传已久。\n林师兄曾经历此劫。"
    text = body + "\n" + tail_text

    orig_avail = embedding_store.content_backend_available
    orig_prefetch = embedding_store.prefetch_content_embeddings
    orig_single = embedding_store.compute_content_embedding
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0}

    embedding_store.content_backend_available = lambda: True
    embedding_store.prefetch_content_embeddings = _rec_prefetch
    embedding_store.compute_content_embedding = lambda t: [0.0, 0.0]
    try:
        des.audit_deus_ex(text, history_text=history)
        assert len(calls) == 1, f"应恰好一次批量 prefetch·实际 {len(calls)} 次"
        prefetched = calls[0]

        # 独立重算 tail/elements（与 audit_deus_ex 内部同口径）核对文本集合完整性
        n = len(text)
        tail_start = int(n * (1.0 - des.TAIL_RATIO))
        tail = text[tail_start:]
        elements = des._extract_resolution_elements(tail)
        all_elements = (elements["char_names"] + elements["item_names"] + elements["power_hits"])
        assert all_elements, "前置条件：本用例必须真的抽出 resolution 元素才有意义"

        for p in des._split_history_paragraphs(history):
            assert p in prefetched
        for el in all_elements:
            assert des._anchor_query_text(el, tail) in prefetched
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.prefetch_content_embeddings = orig_prefetch
        embedding_store.compute_content_embedding = orig_single


def test_audit_deus_ex_content_backend_off_never_calls_prefetch():
    """🔴 零回归锁：内容后端不可用 → prefetch_content_embeddings 完全不被调用。"""
    orig_avail = embedding_store.content_backend_available
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _boom(texts):
        raise AssertionError("内容后端不可用时绝不应调用 prefetch_content_embeddings")

    embedding_store.content_backend_available = lambda: False
    embedding_store.prefetch_content_embeddings = _boom
    try:
        body = _filler(30)
        tail = "我取出上古神剑一剑斩了敌首。"
        history = "很久以前一柄神剑现世无人可挡。" * 3
        text = body + "\n" + tail
        r = des.audit_deus_ex(text, history_text=history)
        assert "underbacked" in r
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.prefetch_content_embeddings = orig_prefetch
