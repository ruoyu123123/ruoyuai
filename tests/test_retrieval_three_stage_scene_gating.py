# -*- coding: utf-8 -*-
"""检索提示、场景门控、写前摘要与 manifest 压缩测试。

检索三段式：
  1. query 扩展：rag_retriever.expand_query_from_brief——brief 实体×属性组合词组
     （characters/props/location × scope_summary 关键词·3-5 组·确定性零 LLM）。
     无 brief / 无实体 / 无 scope 关键词 → []（query 零变化）。
  2. 时间距离防复读：命中按块距标 [NEAR_ECHO_RISK]（≤1 块·近块内容禁直接复用）/
     [PARAPHRASE]（2-3 块·需换写）/ [OK]（>3 块）；块距不可知 → 只打用途分类。
  3. 用途标注：启发式粗分类（对话风格参考/冲突节奏参考/世界观碎片/前情事实参考）。
  2+3 合成每条检索结果的 usage_hint（cluster 检索与 selective_history 共用）。

A11 DeepLore scene 维度门控（sillytavern-DeepLore 移植）：
  build_manifest._scene_gate_world_hits——世界观词条点名已知角色/地点却与本块出场
  角色/地点零交集 → 不注入（META 审计留痕）。保守闸：env MANIFEST_SCENE_GATING=off /
  无场景信息 / 词条不点名实体 → 零变化。

A2 遗留清偿：build_manifest._collect_pre_write_gate_digest——写前 gate 报告
  waived[]/warnings[] 摘要注入（有内容才注·T0 契约类）。

A3 遗留根治：manifest_compress.LONG_TEXT_KEY_WHITELIST——创作载荷长文本
  （tail_text/snippet/passages_by_type/scope_summary 子树）豁免 MAX_STR_LEN 盲切。
"""
import json
import sys
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402
import manifest_budget as mb  # noqa: E402
import manifest_compress as mc  # noqa: E402
import rag_retriever as rag  # noqa: E402


# ============ 夹具 ============

def _mk_brief_project(tmp_path, *, with_scope=True, with_entities=True):
    """最小项目：事件簇 cluster_001/002 + brief 实体/scope。"""
    root = tmp_path / "书A6"
    db = root / "_数据库"
    db.mkdir(parents=True)
    c2 = {"cluster_id": "cluster_002", "chapter_range": [4, 6]}
    if with_entities:
        c2["characters_focus"] = ["林昭"]
        c2["anchor_props"] = ["铜钥匙"]
        c2["hub_locations"] = ["档案馆"]
        c2["scene_storyboard"] = [
            {"ch": 4, "characters": ["林昭", "周队长"], "location": "档案馆地下室"}]
    else:
        c2["scene_storyboard"] = []
    if with_scope:
        c2["scope_summary"] = "暴雨夜潜入调查失踪案卷宗，牵出十年前旧案"
    clusters = [{"cluster_id": "cluster_001", "chapter_range": [1, 3],
                 "scene_storyboard": []}, c2]
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return root


def _mk_retrieval_project(tmp_path):
    """创建一个历史 cluster 与当前 cluster brief。"""
    root = _mk_brief_project(tmp_path)
    summary = "林昭走进档案馆翻找失踪案卷宗，深夜大火烧掉一半旧案。"
    write_cluster_summary(root, [cluster_record("cluster_001", summary=summary)])
    folder = root / "章节" / "cluster_001_draft"
    folder.mkdir(parents=True)
    (folder / "cluster_001_draft.txt").write_text(
        (summary + "管理员说十年前的旧案早已封存。") * 6,
        encoding="utf-8",
    )
    return root


def _mk_gate_scanner(tmp_path, clusters, char_cards=None, map_data=None, registry=None):
    """A11 用 scanner：事件簇 + 人物卡 + 地图 + location registry。"""
    root = tmp_path / "书A11"
    db = root / "_数据库"
    db.mkdir(parents=True)
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    if char_cards is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": char_cards}, ensure_ascii=False), encoding="utf-8")
    if map_data is not None:
        (db / "地图.json").write_text(
            json.dumps(map_data, ensure_ascii=False), encoding="utf-8")
    if registry is not None:
        (db / "location_atmosphere_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    return bm.DatabaseScanner(root, 4)


_GATE_CLUSTERS = [{
    "cluster_id": "cluster_002", "chapter_range": [4, 6],
    "scene_storyboard": [{"ch": 4, "characters": ["林昭"], "location": "档案馆"}],
}]
_GATE_CHARS = [{"id": "char_linzhao", "name": "林昭"},
               {"id": "char_chenmo", "name": "陈默"}]


# ============ A6-1 query 扩展 ============

def test_expand_query_positive(tmp_path):
    """正例：实体×scope 关键词组合 3-5 组·每组含实体·确定性。"""
    root = _mk_brief_project(tmp_path)
    groups = rag.expand_query_from_brief(root, "cluster_002")
    assert 3 <= len(groups) <= 5
    joined = " ".join(groups)
    for ent in ("林昭", "铜钥匙", "档案馆"):
        assert ent in joined
    # 属性来自 scope_summary（实体名不算属性）
    assert any(kw in joined for kw in ("暴雨夜潜", "失踪", "卷宗", "牵出十年"))
    # 确定性：两次调用逐字节一致
    assert groups == rag.expand_query_from_brief(root, "cluster_002")


def test_expand_query_zero_change_paths(tmp_path):
    """零变化路径：无事件簇 / 无 scope / 无实体 → []。"""
    empty = tmp_path / "空书"
    (empty / "_数据库").mkdir(parents=True)
    assert rag.expand_query_from_brief(empty, "cluster_002") == []
    no_scope = _mk_brief_project(tmp_path, with_scope=False)
    assert rag.expand_query_from_brief(no_scope, "cluster_002") == []
    no_ent = _mk_brief_project(tmp_path / "b", with_entities=False)
    assert rag.expand_query_from_brief(no_ent, "cluster_002") == []


def test_corpus_query_doc_carries_expansion_and_brief(tmp_path):
    """query 同时携带扩展词组与当前 cluster brief。"""
    root = _mk_retrieval_project(tmp_path)
    corpus = rag._load_retrieval_corpus(root, "cluster_002")
    assert corpus is not None
    query_doc = corpus[1][-1]
    assert "林昭" in query_doc
    assert "暴雨夜潜入调查失踪案卷宗" in query_doc
    assert "档案馆地下室" in query_doc


# ============ A6-2/3 块距防复读 + 用途标注 ============

def test_echo_tag_bands():
    """块距分级：≤1 NEAR_ECHO_RISK / 2-3 PARAPHRASE / >3 OK / 未知无标签。"""
    assert rag.echo_tag(0) == "[NEAR_ECHO_RISK]"
    assert rag.echo_tag(1) == "[NEAR_ECHO_RISK]"
    assert rag.echo_tag(2) == "[PARAPHRASE]"
    assert rag.echo_tag(3) == "[PARAPHRASE]"
    assert rag.echo_tag(4) == "[OK]"
    assert rag.echo_tag(None) is None
    near = rag.build_usage_hint(1, "他说完就走。")
    assert near.startswith("[NEAR_ECHO_RISK]") and "禁直接复用" in near
    para = rag.build_usage_hint(3, "他说完就走。")
    assert para.startswith("[PARAPHRASE]") and "换写" in para
    ok = rag.build_usage_hint(9, "他说完就走。")
    assert ok.startswith("[OK]")
    unknown = rag.build_usage_hint(None, "他说完就走。")
    assert "[" not in unknown  # 块距不可知不臆造标签


def test_classify_usage_heuristics():
    """用途启发式四分类（对话>冲突>世界观>前情兜底）。"""
    assert rag.classify_usage("“你到底想要什么？”他压低声音。") == "对话风格参考"
    assert rag.classify_usage("刀光一闪，他挥拳砸下，血溅了半面墙。") == "冲突节奏参考"
    assert rag.classify_usage("南疆巫族的禁忌规则：结界之内不得动用血脉之力。") == "世界观碎片"
    assert rag.classify_usage("他昨天把钥匙留在了桌上。") == "前情事实参考"


def test_retrieve_tfidf_hits_carry_usage_hint(tmp_path):
    """相邻历史 cluster 命中带近块防复读提示。"""
    root = _mk_retrieval_project(tmp_path)
    results = rag.retrieve_tfidf(root, "cluster_002", top_k=3)
    assert results, "夹具应有 TF-IDF 命中"
    for r in results:
        assert r["usage_hint"].startswith("[NEAR_ECHO_RISK]")
        assert "禁直接复用" in r["usage_hint"]


def test_annotate_usage_hints_uses_cluster_distance():
    hits = [{"cluster_id": "cluster_001", "snippet": "他昨天把钥匙留在了桌上。"}]
    out = rag.annotate_usage_hints("cluster_005", hits)
    assert out[0]["usage_hint"] == "[OK]·前情事实参考"


# ============ A11 scene 维度门控 ============

def test_scene_gating_drops_unrelated_entry(tmp_path, monkeypatch):
    """点名已知角色（陈默）但与本块出场角色零交集 → 不注入 + META 审计留痕。"""
    monkeypatch.delenv("MANIFEST_SCENE_GATING", raising=False)
    s = _mk_gate_scanner(tmp_path, _GATE_CLUSTERS, char_cards=_GATE_CHARS,
                         registry={"钟楼": {"signature_sensory_motifs": ["雾"]}})
    hits = [
        {"id": "W1", "keywords": ["禁术"], "resolved": {"desc": "陈默独门禁术的来历"}},
        {"id": "W2", "keywords": ["大雾"], "resolved": {"desc": "全城常年不散的大雾"}},
        {"id": "W3", "keywords": ["血脉"], "resolved": {"desc": "林昭家族血脉的秘密"}},
        {"id": "W4", "keywords": ["钟声"], "resolved": {"desc": "钟楼午夜钟声的规矩"}},
    ]
    kept, report = bm._scene_gate_world_hits(s, hits, "cluster_002")
    kept_ids = [h["id"] for h in kept]
    assert "W1" not in kept_ids          # 点名无关角色 → 滤
    assert "W4" not in kept_ids          # 点名无关地点（钟楼∉本块地点）→ 滤
    assert "W2" in kept_ids              # 通用词条（不点名实体）→ 保守保留
    assert "W3" in kept_ids              # 点名本块出场角色 → 保留
    assert report is not None
    assert sorted(report["dropped_entry_ids"]) == ["W1", "W4"]
    assert report["gated_section"] == "world_keyword_hits"
    assert mb.SECTION_TIERS["_scene_gating"] == mb.TIER_META


def test_scene_gating_env_off_zero_change(tmp_path, monkeypatch):
    """env MANIFEST_SCENE_GATING=off → 逐条不动·无 report。"""
    monkeypatch.setenv("MANIFEST_SCENE_GATING", "off")
    s = _mk_gate_scanner(tmp_path, _GATE_CLUSTERS, char_cards=_GATE_CHARS)
    hits = [{"id": "W1", "resolved": {"desc": "陈默独门禁术的来历"}}]
    kept, report = bm._scene_gate_world_hits(s, hits, "cluster_002")
    assert kept == hits and report is None


def test_scene_gating_no_scene_info_zero_change(tmp_path, monkeypatch):
    """匹配不到场景信息（storyboard 无角色无地点）→ 不过滤零变化（保守闸）。"""
    monkeypatch.delenv("MANIFEST_SCENE_GATING", raising=False)
    bare = [{"cluster_id": "cluster_002", "chapter_range": [4, 6], "scene_storyboard": []}]
    s = _mk_gate_scanner(tmp_path, bare, char_cards=_GATE_CHARS)
    hits = [{"id": "W1", "resolved": {"desc": "陈默独门禁术的来历"}}]
    kept, report = bm._scene_gate_world_hits(s, hits, "cluster_002")
    assert kept == hits and report is None
    # 反查不到 cluster 同样零变化
    kept2, report2 = bm._scene_gate_world_hits(s, hits, None)
    assert kept2 == hits and report2 is None


def test_scene_gating_dimension_isolation(tmp_path, monkeypatch):
    """只判有上下文的维度：本块无地点信息时·纯地点词条不裁（不误删）。"""
    monkeypatch.delenv("MANIFEST_SCENE_GATING", raising=False)
    clusters = [{"cluster_id": "cluster_002", "chapter_range": [4, 6],
                 "scene_storyboard": [{"ch": 4, "characters": ["林昭"]}]}]  # 无 location
    s = _mk_gate_scanner(tmp_path, clusters, char_cards=_GATE_CHARS,
                         registry={"钟楼": {"signature_sensory_motifs": ["雾"]}})
    hits = [{"id": "W4", "keywords": ["钟声"], "resolved": {"desc": "钟楼午夜钟声的规矩"}}]
    kept, report = bm._scene_gate_world_hits(s, hits, "cluster_002")
    assert [h["id"] for h in kept] == ["W4"] and report is None


# ============ A2 遗留：pre_write_gate 报告摘要注入 ============

def _write_gate_report(root, payload):
    wal = root / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    (wal / "cluster_002_pre_write_gate.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_pre_write_gate_digest_injected(tmp_path):
    """正例：报告有 waived/warnings → 注入摘要（含豁免理由）·tier=T0。"""
    s = _mk_gate_scanner(tmp_path, _GATE_CLUSTERS, char_cards=_GATE_CHARS)
    _write_gate_report(s.root, {
        "cluster_id": "cluster_002", "verdict": "pass",
        "waived": [{"type": "dead_character", "target": "老蔡",
                    "detail": "已死角色出现在 brief",
                    "waived_by": {"type": "dead_character", "target": "老蔡",
                                  "reason": "以林昭的幻觉登场·非复活"}}],
        "warnings": [{"type": "duplicate_event", "target": "cluster:cluster_001",
                      "detail": "与已完成事件高词面重叠"}],
    })
    out = bm._collect_pre_write_gate_digest(s, "cluster_002")
    assert out is not None
    assert out["cluster_id"] == "cluster_002" and out["verdict"] == "pass"
    assert out["waived"][0]["waiver_reason"] == "以林昭的幻觉登场·非复活"
    assert out["warnings"][0]["type"] == "duplicate_event"
    assert mb.SECTION_TIERS["pre_write_gate_digest"] == mb.TIER_T0


def test_pre_write_gate_digest_zero_change_paths(tmp_path):
    """无报告 / waived+warnings 双空 / 坏 JSON → None（键不注入）。"""
    s = _mk_gate_scanner(tmp_path, _GATE_CLUSTERS, char_cards=_GATE_CHARS)
    assert bm._collect_pre_write_gate_digest(s, "cluster_002") is None  # 无报告
    assert bm._collect_pre_write_gate_digest(s, None) is None           # 无 cluster_id
    _write_gate_report(s.root, {"cluster_id": "cluster_002", "verdict": "pass",
                                "waived": [], "warnings": []})
    assert bm._collect_pre_write_gate_digest(s, "cluster_002") is None  # 双空
    (s.db / ".wal" / "cluster_002_pre_write_gate.json").write_text(
        "{broken", encoding="utf-8")
    assert bm._collect_pre_write_gate_digest(s, "cluster_002") is None  # 坏 JSON


# ============ A3 遗留：manifest_compress 长字符串白名单 ============

_LONG = "钟楼的雾又漫上来了，他沿着湿冷的石阶一步步往上走，风从缺口灌进来。" * 12  # >200 字


def test_compress_whitelist_preserves_creative_payload():
    """白名单键（含子树）字符串完整原文不切；非白名单键照旧盲切。"""
    obj = {
        "prev_cluster_tail": {"source_cluster": "cluster_001", "tail_text": _LONG},
        "rolling_style_anchor": {"anchors": [{"cluster_id": "cluster_001",
                                              "snippet": _LONG}]},
        "distill_golden_few_shot": {"passages_by_type": {"opening_passages": [_LONG]}},
        "event_cluster_context": {"scope_summary": _LONG},
        "some_other_section": {"long_note": _LONG},
    }
    out = mc.compress(obj)
    assert out["prev_cluster_tail"]["tail_text"] == _LONG
    assert out["rolling_style_anchor"]["anchors"][0]["snippet"] == _LONG
    assert out["distill_golden_few_shot"]["passages_by_type"]["opening_passages"][0] == _LONG
    assert out["event_cluster_context"]["scope_summary"] == _LONG
    trunc = out["some_other_section"]["long_note"]
    assert trunc != _LONG and "...(+" in trunc  # 非白名单仍走既有盲切（零回归）


def test_collect_long_strings_skips_whitelisted():
    """surprisal 候选收集与 compress 同构：白名单子树字符串不进收集（不浪费模型调用）。"""
    obj = {
        "prev_cluster_tail": {"tail_text": _LONG},
        "some_other_section": {"long_note": _LONG + "尾"},
    }
    collected = mc._collect_long_strings(obj)
    assert _LONG not in collected
    assert (_LONG + "尾") in collected
