# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN角色网络/共指集成
"""character_network_extractor 回归：默认安全（env off → 空结果）·
角色识别（known > jieba > 空）·共现统计·对话归属·对话交互·中心性·Renard 降级。

确定性·零网络·零外部依赖（mock jieba/renard）。"""
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import character_network_extractor as mod  # noqa: E402


# ── 测试样本 ────────────────────────────────────────────────

_SAMPLE_DRAFT = """张三走进了酒楼，李四已经在角落坐着。

张三说："你来得真早。"
李四道："等你半天了。"
张三笑道："那我请你喝酒。"
李四说："好啊，正好饿了。"


王五从门外走了进来。
张三道："王五，过来一起。"
王五说："正好路过。"
李四笑道："三个人更热闹。"
"""

_SAMPLE_CHARACTERS = ["张三", "李四", "王五"]


# ── 默认安全 ────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RUOYU_CHARACTER_NETWORK", raising=False)
    assert mod.enabled() is False
    result = mod.extract_character_network("任意文本", ["张三"])
    assert result["source"] == "disabled"
    assert result["characters"] == []
    assert result["edges"] == []


def test_empty_input(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    result = mod.extract_character_network("", ["张三"])
    assert result["source"] == "empty_input"


def test_no_characters_found(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    result = mod.extract_character_network("一段没有任何角色名的纯描写文本。")
    assert result["source"] == "no_characters_found"


# ── 角色识别 ────────────────────────────────────────────────

def test_identify_known_characters(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    result = mod.extract_character_network(_SAMPLE_DRAFT, _SAMPLE_CHARACTERS)
    assert set(result["characters"]) == {"张三", "李四", "王五"}


def test_identify_from_project_db(monkeypatch, tmp_path):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    db = tmp_path / "_数据库"
    db.mkdir()
    (db / "人物.json").write_text(
        json.dumps({"characters": [{"name": "张三"}, {"name": "李四"}]},
                   ensure_ascii=False),
        encoding="utf-8")
    result = mod.extract_character_network(
        _SAMPLE_DRAFT, project_dir=str(tmp_path))
    assert "张三" in result["characters"]
    assert "李四" in result["characters"]


def test_identify_jieba_fallback(monkeypatch):
    """jieba 可用时走 NER 兜底。jieba 不可用 → 空角色列表。"""
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    # 不传 known_characters、不传 project_dir → 走 jieba
    # jieba 可能没装·此时返回 no_characters_found
    result = mod.extract_character_network(_SAMPLE_DRAFT)
    assert result["source"] in ("rule_based", "no_characters_found")


# ── 共现统计 ────────────────────────────────────────────────

def test_cooccurrence_edges(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    result = mod.extract_character_network(_SAMPLE_DRAFT, _SAMPLE_CHARACTERS)
    cooc_edges = [e for e in result["edges"] if e["type"] == "co_occurrence"]
    # 张三和李四应该共现（同一场景）
    pairs = {(e["source"], e["target"]) for e in cooc_edges}
    assert ("张三", "李四") in pairs or ("李四", "张三") in pairs


def test_scene_participation(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    result = mod.extract_character_network(_SAMPLE_DRAFT, _SAMPLE_CHARACTERS)
    sp = result["scene_participation"]
    assert len(sp) >= 1
    # 至少一个场景包含张三
    assert any("张三" in chars for chars in sp.values())


# ── 对话归属 ────────────────────────────────────────────────

def test_dialogue_attribution(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    result = mod.extract_character_network(_SAMPLE_DRAFT, _SAMPLE_CHARACTERS)
    dialogue_edges = [e for e in result["edges"] if e["type"] == "dialogue"]
    # 应该检测到张三和李四之间的对话交互
    assert len(dialogue_edges) >= 1


def test_dialogue_count(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    result = mod.extract_character_network(_SAMPLE_DRAFT, _SAMPLE_CHARACTERS)
    # 应该检测到多段对话
    assert result.get("dialogue_count", 0) >= 4


def test_orphan_dialogue_count(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    text = '张三走了进来。\n\n"今天天气不错。"\n\n"是啊。"'
    result = mod.extract_character_network(text, ["张三"])
    # 引号对话但无明确归属 → orphan
    assert result.get("orphan_dialogues", 0) >= 0  # 不崩即可


# ── 代词发言人 coref 兜底（2026-07-02）────────────────────────
# "张三" 距引号开头超过 30 字·30 字回扫必然找不到角色名·逼出 coref 兜底路径
_PRONOUN_SPEAKER_TEXT = (
    "张三独自坐在酒楼靠窗的位置，窗外的雨已经下了整整一个下午都没有停过，"
    "街道上几乎看不见什么行人，气氛安静得让人心里发闷。"
    '他说："你来得真早。"'
)


def _fake_resolve_pronoun_to_zhangsan(text, known_characters=None):
    """content-aware 假共指消解（测试专用）：把文本里所有"他"消解到张三。"""
    results = []
    for m in re.finditer("他", text):
        results.append({
            "mention": "他", "span": [m.start(), m.end()],
            "resolved_to": "张三", "confidence": 0.9,
            "backend": "fake", "ambiguous": False,
        })
    return results


def test_pronoun_dialogue_resolved_via_coref_bridge(monkeypatch):
    """门控开 + mock 桥 → 代词发言人（"他说"）被正确归属为具体角色名，
    attribution_method 标 "coref"。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    fake_bridge = types.SimpleNamespace(
        resolve_coreferences=_fake_resolve_pronoun_to_zhangsan)
    monkeypatch.setitem(sys.modules, "nn_coref_bridge", fake_bridge)

    dialogues = mod._attribute_dialogue(_PRONOUN_SPEAKER_TEXT, ["张三", "李四"])
    target = next(d for d in dialogues if d["content"] == "你来得真早。")
    assert target["speaker"] == "张三"
    assert target["attribution_method"] == "coref"


def test_pronoun_dialogue_coref_empty_bridge_zero_regression(monkeypatch):
    """门控开但桥返回 [] → 输出与门控关（默认）完全一致（零回归证明）。"""
    monkeypatch.delenv("RUOYU_NN_COREF", raising=False)
    baseline = mod._attribute_dialogue(_PRONOUN_SPEAKER_TEXT, ["张三", "李四"])

    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    empty_bridge = types.SimpleNamespace(
        resolve_coreferences=lambda text, known_characters=None: [])
    monkeypatch.setitem(sys.modules, "nn_coref_bridge", empty_bridge)
    with_empty_bridge = mod._attribute_dialogue(_PRONOUN_SPEAKER_TEXT, ["张三", "李四"])

    assert with_empty_bridge == baseline
    target = next(d for d in baseline if d["content"] == "你来得真早。")
    assert target["speaker"] is None  # 正则+回扫都抓不到、桥无结果 → 仍是 None


def test_regex_attribution_not_overridden_by_coref(monkeypatch):
    """正则能直接归属的对话（发言人是已知角色名）→ 不进入 coref 候选路径，
    即使门控开 + 桥对该角色名给出"消解"结果，speaker 仍是正则归属结果、
    attribution_method 仍是 "regex"（优先级验证：正则显式归属最优先不动）。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")

    def _wrong_resolver(text, known_characters=None):
        # 故意对已知角色名"张三"也给出消解结果·验证正则路径根本不会查询它
        results = []
        for m in re.finditer("张三", text):
            results.append({
                "mention": "张三", "span": [m.start(), m.end()],
                "resolved_to": "李四", "confidence": 0.9,
                "backend": "fake", "ambiguous": False,
            })
        return results

    monkeypatch.setitem(
        sys.modules, "nn_coref_bridge",
        types.SimpleNamespace(resolve_coreferences=_wrong_resolver))

    text = '张三说："你来得真早。"'
    dialogues = mod._attribute_dialogue(text, ["张三", "李四"])
    target = next(d for d in dialogues if d["content"] == "你来得真早。")
    assert target["speaker"] == "张三"
    assert target["attribution_method"] == "regex"


# ── 中心性 ────────────────────────────────────────────────

def test_centrality(monkeypatch):
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    result = mod.extract_character_network(_SAMPLE_DRAFT, _SAMPLE_CHARACTERS)
    centrality = result["centrality"]
    assert "张三" in centrality
    # 张三是核心角色（出现在两个场景 + 多段对话）
    assert centrality["张三"] >= centrality.get("王五", 0)


# ── 对话交互边 ────────────────────────────────────────────

def test_dialogue_interaction_consecutive(monkeypatch):
    """连续对话归属 → 对话交互边。"""
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    text = '张三说："你好。"\n李四道："你好啊。"\n张三笑道："走吧。"'
    result = mod.extract_character_network(text, ["张三", "李四"])
    dialogue_edges = [e for e in result["edges"] if e["type"] == "dialogue"]
    if dialogue_edges:
        # 张三和李四之间的对话交互
        pairs = {(e["source"], e["target"]) for e in dialogue_edges}
        assert ("张三", "李四") in pairs or ("李四", "张三") in pairs


# ── 场景分割 ────────────────────────────────────────────────

def test_scene_split():
    text = "第一场景内容\n\n\n第二场景内容\n\n\n第三场景内容"
    scenes = mod._split_scenes(text)
    assert len(scenes) == 3


def test_scene_split_single():
    text = "只有一个场景的文本"
    scenes = mod._split_scenes(text)
    assert len(scenes) == 1


def test_scene_split_separator():
    text = "场景一\n---\n场景二\n***\n场景三"
    scenes = mod._split_scenes(text)
    assert len(scenes) >= 2


# ── 边界情况 ────────────────────────────────────────────────

def test_changes_stripped(monkeypatch):
    """changes 元数据被正确剥离。"""
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    text = _SAMPLE_DRAFT + "\n---CHANGES_FACTUAL---\n{some json}"
    result = mod.extract_character_network(text, _SAMPLE_CHARACTERS)
    assert result["source"] == "rule_based"


def test_exception_safety(monkeypatch):
    """内部异常 → 返回空结果（不崩）。"""
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    # 强制 _extract_impl 抛异常
    orig = mod._extract_impl
    def boom(*a, **kw):
        raise RuntimeError("test explosion")
    monkeypatch.setattr(mod, "_extract_impl", boom)
    result = mod.extract_character_network("文本", ["张三"])
    assert result["source"] == "error"
    assert result["characters"] == []


# ── Renard 降级 ────────────────────────────────────────────

def test_renard_not_installed_fallback(monkeypatch):
    """Renard 未安装 → 降级到纯规则。"""
    monkeypatch.setenv("RUOYU_CHARACTER_NETWORK", "1")
    # _try_renard 在 import 失败时返回 None → 走规则分支
    result = mod.extract_character_network(_SAMPLE_DRAFT, _SAMPLE_CHARACTERS)
    # 不管 Renard 装没装，都应该返回有效结果
    assert result["source"] in ("rule_based", "renard")
    assert len(result["characters"]) >= 2


# ── 内部函数单测 ────────────────────────────────────────────

def test_find_characters_regex():
    text = "张三和李四在一起，王五不在。"
    found = mod._find_characters_regex(text, ["张三", "李四", "王五", "赵六"])
    assert found == ["张三", "李四", "王五"]
    assert "赵六" not in found


def test_attribute_dialogue():
    text = '张三说："你好。"\n李四道："你也好。"'
    dialogues = mod._attribute_dialogue(text, ["张三", "李四"])
    speakers = [d["speaker"] for d in dialogues]
    assert "张三" in speakers
    assert "李四" in speakers


def test_compute_cooccurrence():
    scenes = ["张三和李四在喝茶", "王五独自一人", "张三和王五见面"]
    result = mod._compute_cooccurrence(scenes, ["张三", "李四", "王五"])
    edges = result["edges"]
    assert ("张三", "李四") in edges or ("李四", "张三") in edges


def test_compute_dialogue_interaction():
    dialogues = [
        {"speaker": "张三", "start": 0},
        {"speaker": "李四", "start": 10},
        {"speaker": "张三", "start": 20},
        {"speaker": "张三", "start": 30},  # 同人连续 → 不计
    ]
    edges = mod._compute_dialogue_interaction(dialogues)
    pair = ("张三", "李四")
    assert pair in edges
    assert edges[pair]["weight"] == 2  # 张三→李四 + 李四→张三


def test_compute_centrality():
    chars = ["A", "B", "C"]
    edges = [
        {"source": "A", "target": "B", "weight": 5},
        {"source": "A", "target": "C", "weight": 3},
    ]
    c = mod._compute_centrality(chars, edges)
    assert c["A"] == 1.0  # 最大度
    assert c["B"] < c["A"]


def test_empty_result_fields():
    r = mod._empty_result("test")
    assert "characters" in r and "edges" in r
    assert "scene_participation" in r and "centrality" in r
    assert r["source"] == "test"
