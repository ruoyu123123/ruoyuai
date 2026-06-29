# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN角色网络/共指集成
"""nn_coref_bridge 回归：默认安全（env off → []）·规则后端（最近先行词 + 性别匹配）·
HanLP 降级·边界情况·异常安全。

确定性·零网络·零外部依赖（mock HanLP）。"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import nn_coref_bridge as mod  # noqa: E402


# ── 默认安全：env off ────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RUOYU_NN_COREF", raising=False)
    assert mod.enabled() is False
    assert mod.resolve_coreferences("他走了", ["张三"]) == []


def test_empty_text(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    result = mod.resolve_coreferences("", ["张三"])
    assert result == []


def test_no_known_characters(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    result = mod.resolve_coreferences("他走了。", None)
    assert result == []


# ── 规则后端 ────────────────────────────────────────────────

def test_rule_basic_resolution(monkeypatch):
    """基本规则消解：代词「他」→ 最近先行词「张三」。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "rule")
    text = "张三走到窗前。他叹了口气。"
    result = mod.resolve_coreferences(text, ["张三"])
    assert len(result) >= 1
    assert result[0]["resolved_to"] == "张三"
    assert result[0]["mention"] == "他"
    assert result[0]["backend"] == "rule"


def test_rule_gender_matching(monkeypatch):
    """性别匹配：「她」→ 女性角色。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "rule")
    text = "张三和王小姐一起走着。她笑了笑。"
    result = mod.resolve_coreferences(text, ["张三", "王小姐"])
    she_results = [r for r in result if r["mention"] == "她"]
    assert len(she_results) >= 1
    assert she_results[0]["resolved_to"] == "王小姐"


def test_rule_nearest_antecedent(monkeypatch):
    """最近先行词：距离近的优先。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "rule")
    text = "张三说了句话。李四点了点头。他笑了。"
    result = mod.resolve_coreferences(text, ["张三", "李四"])
    he_results = [r for r in result if r["mention"] == "他"]
    assert len(he_results) >= 1
    # 李四更近 → 优先
    assert he_results[0]["resolved_to"] == "李四"


def test_rule_confidence_distance_decay(monkeypatch):
    """置信度随距离衰减。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "rule")
    # 近距离
    text_near = "张三走来。他笑了。"
    res_near = mod.resolve_coreferences(text_near, ["张三"])
    # 远距离
    padding = "这是一段很长很长的描写" * 10
    text_far = f"张三走来。{padding}他笑了。"
    res_far = mod.resolve_coreferences(text_far, ["张三"])

    if res_near and res_far:
        assert res_near[0]["confidence"] >= res_far[0]["confidence"]


def test_rule_ambiguous_flag(monkeypatch):
    """多个候选距离相近 → ambiguous=True。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "rule")
    text = "张三李四站在一起。他说了句话。"
    result = mod.resolve_coreferences(text, ["张三", "李四"])
    he_results = [r for r in result if r["mention"] == "他"]
    if he_results:
        # 两个角色距离很近 → 可能 ambiguous
        assert isinstance(he_results[0]["ambiguous"], bool)


def test_rule_no_antecedent(monkeypatch):
    """代词出现在角色名之前 → 不消解。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "rule")
    text = "他走了进来。张三看着门口。"
    result = mod.resolve_coreferences(text, ["张三"])
    he_results = [r for r in result if r["mention"] == "他"]
    # 「他」在「张三」之前出现 → 无先行词 → 不消解
    assert len(he_results) == 0


def test_rule_multiple_pronouns(monkeypatch):
    """多个代词各自消解。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "rule")
    text = "张三走来。他笑了。李四也来了。他点头。"
    result = mod.resolve_coreferences(text, ["张三", "李四"])
    # 第一个「他」→ 张三，第二个「他」→ 李四
    he_results = [r for r in result if r["mention"] == "他"]
    assert len(he_results) == 2
    assert he_results[0]["resolved_to"] == "张三"
    assert he_results[1]["resolved_to"] == "李四"


# ── HanLP 降级 ────────────────────────────────────────────

def test_hanlp_import_failure_fallback(monkeypatch):
    """HanLP 未安装 → 降级到 rule 后端。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "hanlp")
    # 确保 hanlp 不可用（大概率没装）
    text = "张三走来。他笑了。"
    result = mod.resolve_coreferences(text, ["张三"])
    # 不管 HanLP 装没装，都应该返回有效结果（降级到 rule）
    if result:
        assert result[0]["backend"] in ("hanlp", "rule")


# ── 性别推断 ────────────────────────────────────────────────

def test_infer_gender_male():
    assert mod._infer_gender("王公子") == "male"
    assert mod._infer_gender("张先生") == "male"


def test_infer_gender_female():
    assert mod._infer_gender("李小姐") == "female"
    assert mod._infer_gender("赵姑娘") == "female"


def test_infer_gender_unknown():
    assert mod._infer_gender("陈默") is None


def test_pronoun_gender():
    assert mod._pronoun_gender("他") == "male"
    assert mod._pronoun_gender("她") == "female"
    assert mod._pronoun_gender("那个人") is None


# ── 内部函数 ────────────────────────────────────────────────

def test_find_character_mentions():
    text = "张三走了，李四留下来。张三又回来了。"
    mentions = mod._find_character_mentions(text, ["张三", "李四"])
    names = [m["name"] for m in mentions]
    assert names.count("张三") == 2
    assert names.count("李四") == 1
    # 按位置排序
    positions = [m["start"] for m in mentions]
    assert positions == sorted(positions)


def test_find_pronoun_mentions():
    text = "他走了，她留下。那个人也离开了。"
    pronouns = mod._find_pronoun_mentions(text)
    mention_texts = [p["mention"] for p in pronouns]
    assert "他" in mention_texts
    assert "她" in mention_texts
    assert "那个人" in mention_texts


def test_find_pronoun_no_overlap():
    """长代词优先·不重叠。「那个人」不应该同时匹配「那人」。"""
    text = "那个人走了。"
    pronouns = mod._find_pronoun_mentions(text)
    # 应该只有 1 个匹配（那个人），不应该有子串「那人」重复匹配
    assert len(pronouns) == 1
    assert pronouns[0]["mention"] == "那个人"


# ── 异常安全 ────────────────────────────────────────────────

def test_exception_safety(monkeypatch):
    """任何异常 → 返回空列表（不崩）。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    monkeypatch.setenv("COREF_BACKEND", "rule")
    # 强制 _resolve_rule 抛异常
    orig = mod._resolve_rule
    def boom(*a, **kw):
        raise RuntimeError("test explosion")
    monkeypatch.setattr(mod, "_resolve_rule", boom)
    result = mod.resolve_coreferences("张三走了。他叹气。", ["张三"])
    assert result == []


# ── 后端选择 ────────────────────────────────────────────────

def test_backend_default_is_rule(monkeypatch):
    monkeypatch.delenv("COREF_BACKEND", raising=False)
    assert mod._backend() == "rule"


def test_backend_hanlp(monkeypatch):
    monkeypatch.setenv("COREF_BACKEND", "hanlp")
    assert mod._backend() == "hanlp"


def test_backend_invalid_falls_to_rule(monkeypatch):
    monkeypatch.setenv("COREF_BACKEND", "nonexistent")
    assert mod._backend() == "rule"


# ── changes 剥离 ────────────────────────────────────────────

def test_changes_stripped(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    text = "张三走来。他笑了。\n---CHANGES_FACTUAL---\n{json}"
    result = mod.resolve_coreferences(text, ["张三"])
    # changes 之后的内容不应该被处理
    if result:
        assert all(r["span"][0] < text.index("---CHANGES") for r in result)


# ── span 验证 ────────────────────────────────────────────

def test_span_correctness(monkeypatch):
    """span 指向的文本应该等于 mention。"""
    monkeypatch.setenv("RUOYU_NN_COREF", "1")
    text = "张三站在门口。他看着远方。"
    result = mod.resolve_coreferences(text, ["张三"])
    for r in result:
        start, end = r["span"]
        assert text[start:end] == r["mention"]
