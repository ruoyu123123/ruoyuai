# -*- coding: utf-8 -*-
"""locked_fact_cross_scene_scanner 描述类 NLI 通路回归锁（2026-07-07 兑现 docstring 空头支票）

背景：docstring 曾承诺「描述类（外貌/出身）矛盾陈述」检测但代码只有恒定数值通路——
tests/test_constory_consistency_gold.py::test_contradiction_descriptive_mutex_blindspot
（盲区5/contradiction 类）实证「满门尽灭只剩沈昭一人」vs「兄长沈铖推门而入」0 检出。
本次落地：非数值描述类 fact × 人名共现句粗筛 → nn_nli_bridge（Erlangshen-110M）判
contradiction 高置信 → LOCKED_FACT_DESCRIPTIVE_CONTRADICTION（advisory·独立 `descriptive` 字段）。

钉死的纪律（每条都有测试）：
  1. NLI 后端不可用（默认环境即此态）→ 诚实 skip：note 写明「NLI 后端未启用」，
     绝不用关键词匹配假冒语义判定（executed=False · violations 恒空）
  2. mock NLI 判 contradiction（active 模式）→ 盲区5 同款 fixture 被检出
  3. mock 判 neutral / 低置信 contradiction → 不报
  4. LOCKED_FACT_DESCRIPTIVE_MODE 默认 shadow：只记不判（violations 恒空·命中进 shadow_observations）
  5. advisory 永不 hard：gate_level 恒 advisory · code 不在 audit_hub.HARD_GATE_CODES ·
     绝不复用 LOCKED_FACT_CROSS_SCENE_CONFLICT 这个 hard 码
  6. 恒定数值通路（hard 码）行为零变化：数值冲突照报 hard_gate，顶层字段不被描述类污染
  7. 粗筛：只有人名共现句才送 NLI（控制调用量）

mock 方式：monkeypatch nn_nli_bridge.enabled / nn_nli_bridge.predict_batch
（scanner 经 _nli_bridge() 惰性 import 同一 sys.modules 对象·patch 即生效）。

跑：py -m pytest tests/test_locked_fact_descriptive_nli.py -q
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import audit_hub  # noqa: E402
import locked_fact_cross_scene_scanner as lf  # noqa: E402
import nn_nli_bridge  # noqa: E402


# ───────────────────────── 脚手架 ─────────────────────────

def _scan(chars, draft_text):
    """临时项目落 人物卡.json + 草稿，跑 scan()。"""
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "_数据库"
    db.mkdir(parents=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": chars}, ensure_ascii=False), encoding="utf-8")
    draft = tmp / "draft.txt"
    draft.write_text(draft_text, encoding="utf-8")
    return lf.scan(tmp, draft)


# 盲区5 同款 fixture（test_constory_consistency_gold.py::test_contradiction_descriptive_mutex_blindspot）
_BLINDSPOT_CHARS = [{"name": "沈昭",
                     "locked_facts": [{"fact": "沈家满门尽灭，只剩沈昭一人"}]}]
_BLINDSPOT_DRAFT = (
    "沈昭正在灵前烧纸，火光映着一张没有表情的脸。\n\n\n"
    "沈昭的兄长沈铖推门而入，掸了掸肩上的雪：家里一切安好。\n"
)


def _mock_nli(monkeypatch, label_fn):
    """把 NLI 桥 mock 成可控判定器。label_fn(pair)->(label, prob)。返回捕获的调用列表。"""
    calls = []

    def fake_predict_batch(pairs, timeout=None):
        calls.append(list(pairs))
        out = []
        for p in pairs:
            label, prob = label_fn(p)
            probs = {"entailment": 0.0, "neutral": 0.0, "contradiction": 0.0}
            probs[label] = prob
            out.append({"label": label, "probs": probs, "source": "nli"})
        return out

    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", fake_predict_batch)
    return calls


def _contradict_brother_sentence(pair):
    """盲区5 判定：含「兄长」的正文句与锁定事实矛盾（高置信），其余 neutral。"""
    if "兄长" in pair["hypothesis"]:
        return ("contradiction", 0.95)
    return ("neutral", 0.90)


def _clean_env(monkeypatch):
    monkeypatch.delenv("RUOYU_NN_NLI", raising=False)
    monkeypatch.delenv("LOCKED_FACT_DESCRIPTIVE_MODE", raising=False)


# ═══════════ 1. NLI 不可用 → 诚实 skip（默认环境即此态·绝不假语义）═══════════

def test_nli_unavailable_honest_skip_default_env(monkeypatch):
    """默认环境（RUOYU_NN_NLI 未设 → bridge.enabled()=False）：描述类通路诚实 skip——
    note 明说「NLI 后端未启用」，executed=False，violations 空。绝不用关键词假冒语义。"""
    _clean_env(monkeypatch)
    r = _scan(_BLINDSPOT_CHARS, _BLINDSPOT_DRAFT)
    d = r["descriptive"]
    assert d["nli_available"] is False, d
    assert d["executed"] is False, d
    assert "NLI 后端未启用" in (d["note"] or ""), d
    assert d["violations"] == [], d
    assert d["shadow_observations"] == [], d
    assert d["facts_checked"] == 1, d          # 描述类 fact 被正确分流（不是没看到）
    # 顶层数值通路不受影响（盲区5 fixture 无数值矛盾）
    assert r["conflicts_count"] == 0, r
    assert r["code"] is None, r


def test_mode_off_disables_pathway(monkeypatch):
    """LOCKED_FACT_DESCRIPTIVE_MODE=off → 通路关闭（即便 NLI mock 可用也不跑）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "off")
    calls = _mock_nli(monkeypatch, _contradict_brother_sentence)
    r = _scan(_BLINDSPOT_CHARS, _BLINDSPOT_DRAFT)
    d = r["descriptive"]
    assert d["mode"] == "off", d
    assert d["executed"] is False, d
    assert d["violations"] == [], d
    assert calls == [], "off 模式不许送 NLI"


# ═══════════ 2. mock contradiction → 盲区5 同款 fixture 被检出（active）═══════════

def test_mock_contradiction_detects_blindspot_fixture_active(monkeypatch):
    """ConStory 盲区5 兑现：mock NLI 判「兄长在世」句 contradiction 0.95（≥0.80 阈值）
    → active 模式产出 1 条 advisory violation（此前该 fixture 0 检出）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "active")
    _mock_nli(monkeypatch, _contradict_brother_sentence)
    r = _scan(_BLINDSPOT_CHARS, _BLINDSPOT_DRAFT)
    d = r["descriptive"]
    assert d["executed"] is True, d
    assert len(d["violations"]) == 1, d
    v = d["violations"][0]
    assert v["character"] == "沈昭", v
    assert v["fact"] == "沈家满门尽灭，只剩沈昭一人", v
    assert "兄长沈铖" in v["sentence"], v
    assert v["contradiction_prob"] == pytest.approx(0.95), v
    assert d["code"] == "LOCKED_FACT_DESCRIPTIVE_CONTRADICTION", d


# ═══════════ 3. neutral / 低置信 → 不报 ═══════════

def test_mock_neutral_no_violation(monkeypatch):
    """mock 全 neutral → 通路执行了但 0 violation（NLI 判定被尊重·不自作聪明）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "active")
    _mock_nli(monkeypatch, lambda p: ("neutral", 0.92))
    r = _scan(_BLINDSPOT_CHARS, _BLINDSPOT_DRAFT)
    d = r["descriptive"]
    assert d["executed"] is True, d
    assert d["violations"] == [], d
    assert d["pairs_sent"] >= 1, d


def test_low_confidence_contradiction_not_reported(monkeypatch):
    """contradiction 但置信 0.55 < 阈值 0.80 → 不报（高置信地板·宁漏勿误）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "active")
    _mock_nli(monkeypatch, lambda p: ("contradiction", 0.55))
    r = _scan(_BLINDSPOT_CHARS, _BLINDSPOT_DRAFT)
    d = r["descriptive"]
    assert d["executed"] is True, d
    assert d["violations"] == [], d


# ═══════════ 4. shadow（默认档位）：只记不判 ═══════════

def test_shadow_default_records_but_no_violations(monkeypatch):
    """默认档位（env 未设 → shadow）：即便 mock 判 contradiction 高置信，
    violations 恒空，命中只进 shadow_observations（只记不判·零回归上线姿势）。"""
    _clean_env(monkeypatch)   # LOCKED_FACT_DESCRIPTIVE_MODE 未设 → shadow
    _mock_nli(monkeypatch, _contradict_brother_sentence)
    r = _scan(_BLINDSPOT_CHARS, _BLINDSPOT_DRAFT)
    d = r["descriptive"]
    assert d["mode"] == "shadow", d
    assert d["executed"] is True, d
    assert d["violations"] == [], d
    assert len(d["shadow_observations"]) == 1, d
    assert "兄长沈铖" in d["shadow_observations"][0]["sentence"], d


# ═══════════ 5. advisory 永不 hard ═══════════

def test_advisory_never_hard_gate(monkeypatch):
    """🔴 制度锁：描述类通路 gate_level 恒 advisory；新 code 不在 HARD_GATE_CODES；
    绝不复用 LOCKED_FACT_CROSS_SCENE_CONFLICT 这个 hard 码。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "active")
    _mock_nli(monkeypatch, _contradict_brother_sentence)
    r = _scan(_BLINDSPOT_CHARS, _BLINDSPOT_DRAFT)
    d = r["descriptive"]
    assert len(d["violations"]) == 1, d                     # 有命中的最坏情况下——
    assert d["gate_level"] == "advisory", d                 # 仍是 advisory
    assert d["code"] != "LOCKED_FACT_CROSS_SCENE_CONFLICT", d
    assert d["code"] not in audit_hub.HARD_GATE_CODES, \
        f"{d['code']} 绝不许进 HARD_GATE_CODES（NLI 概率判定·北极星⑤）"
    # 描述类命中不污染顶层（顶层 code/gate_level 只归恒定数值通路管）
    assert r["code"] is None, r
    assert r["gate_level"] == "advisory", r
    assert r["conflicts_count"] == 0, r
    assert r["warning"] is None, r


# ═══════════ 6. 恒定数值通路（hard 码）行为零变化 ═══════════

def test_numeric_path_unchanged_alongside_descriptive(monkeypatch):
    """数值矛盾 + 描述类 fact 同场：数值通路照报 hard_gate（顶层字段与历史口径一致），
    描述类命中只待在 descriptive 字段——两通路互不污染。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "active")
    _mock_nli(monkeypatch, _contradict_brother_sentence)
    chars = [
        {"name": "林惊羽", "locked_facts": [{"fact": "林惊羽三十八岁"}]},
        {"name": "沈昭", "locked_facts": [{"fact": "沈家满门尽灭，只剩沈昭一人"}]},
    ]
    draft = "林惊羽自称四十岁了，可没人信。\n" + _BLINDSPOT_DRAFT
    r = _scan(chars, draft)
    # 顶层 = 数值通路历史口径（hard）
    assert r["conflicts_count"] == 1, r
    assert r["code"] == "LOCKED_FACT_CROSS_SCENE_CONFLICT", r
    assert r["gate_level"] == "hard_gate", r
    assert r["conflicts"][0]["character"] == "林惊羽", r
    # 描述类命中在独立字段（advisory）
    d = r["descriptive"]
    assert len(d["violations"]) == 1, d
    assert d["violations"][0]["character"] == "沈昭", d
    assert d["gate_level"] == "advisory", d


def test_numeric_facts_not_leaked_into_descriptive_pathway(monkeypatch):
    """含可解析「N岁」的 fact 走数值通路，绝不分流进描述类（facts_checked 分账正确）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "active")
    calls = _mock_nli(monkeypatch, lambda p: ("neutral", 0.9))
    chars = [{"name": "林惊羽", "locked_facts": [{"fact": "林惊羽三十八岁"}]}]
    r = _scan(chars, "林惊羽今年三十八岁。")
    d = r["descriptive"]
    assert d["facts_checked"] == 0, d
    assert d["pairs_sent"] == 0, d
    assert calls == [], "数值 fact 不许送 NLI"


# ═══════════ 7. 粗筛：人名共现才送 NLI ═══════════

def test_prefilter_only_name_cooccurrence_sentences_sent(monkeypatch):
    """粗筛控制调用量：只有含「沈昭」的句子被配对送 NLI；无人名的噪声句不送。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "active")
    calls = _mock_nli(monkeypatch, lambda p: ("neutral", 0.9))
    draft = (
        "沈昭正在灵前烧纸，火光映着一张没有表情的脸。\n"
        "殿外的风卷着沙尘掠过廊柱，灯影在墙上摇成一片碎金。\n"   # 无人名·不送
        "案几上的茶早凉了，杯沿积着薄薄一圈灰。\n"               # 无人名·不送
        "沈昭的兄长沈铖推门而入，掸了掸肩上的雪。\n"
    )
    r = _scan(_BLINDSPOT_CHARS, draft)
    sent_pairs = [p for batch in calls for p in batch]
    assert r["descriptive"]["pairs_sent"] == 2, r["descriptive"]
    assert len(sent_pairs) == 2, sent_pairs
    for p in sent_pairs:
        assert "沈昭" in p["hypothesis"], p
        assert p["premise"] == "沈家满门尽灭，只剩沈昭一人", p
