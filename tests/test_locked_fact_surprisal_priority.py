# -*- coding: utf-8 -*-
"""locked_fact 描述类 NLI 通路 · S4 高熵段优先粗筛回归锁（2026-07-07 二轮移植首批）

出处：research/open_source_writing_systems_round2.md S4 · ConStory-Checker
arXiv:2603.05890 实证「一致性错误集中在 token 熵高的文本段」。
接线（零新模型·纯接线）：surprisal 可用（nn_surprisal_bridge）时，描述类候选配对
按所在段 surprisal 降序重排后再截断 _MAX_NLI_PAIRS=64（高熵段优先送 NLI）；
不可用 → 保持既有文档序**逐字节不变**（诚实降级不伪装）。

钉死的纪律（每条都有测试）：
  1. surprisal 可用（mock 桥）→ 候选配对按所在段 surprisal 降序送 NLI ·
     pair_selection="surprisal_ranked"
  2. surprisal 不可用（默认环境）→ pairs/meta 与历史嵌套循环文档序逐字节相同 ·
     pair_selection="document_order" · 绝不调 surprisal 桥
  3. pair_selection 留痕在所有路径（off / NLI 不可用 / 执行）都存在
  4. 上限仍 64（_MAX_NLI_PAIRS 零变动）：ranked 模式下截断照旧生效
  5. 任一所在段 surprisal 未命中 → 整体诚实回退文档序（不产半吊子排序）

mock 方式：monkeypatch nn_surprisal_bridge / nn_nli_bridge 模块属性
（scanner 惰性 import 同一 sys.modules 对象·patch 即生效·同 test_locked_fact_descriptive_nli）。

跑：py -m pytest tests/test_locked_fact_surprisal_priority.py -q
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import locked_fact_cross_scene_scanner as lf  # noqa: E402
import nn_nli_bridge  # noqa: E402
import nn_surprisal_bridge  # noqa: E402


# ───────────────────────── 脚手架 ─────────────────────────

def _scan(chars, draft_text):
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "_数据库"
    db.mkdir(parents=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": chars}, ensure_ascii=False), encoding="utf-8")
    draft = tmp / "draft.txt"
    draft.write_text(draft_text, encoding="utf-8")
    return lf.scan(tmp, draft)


def _mock_nli(monkeypatch):
    """NLI 桥 mock 成全 neutral 判定器·捕获送入的 pairs 顺序。"""
    calls = []

    def fake_predict_batch(pairs, timeout=None):
        calls.append(list(pairs))
        return [{"label": "neutral",
                 "probs": {"entailment": 0.0, "neutral": 0.9, "contradiction": 0.0},
                 "source": "nli"} for _ in pairs]

    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", fake_predict_batch)
    return calls


def _mock_surprisal(monkeypatch, score_fn):
    """surprisal 桥 mock：score_fn(segment_text) -> float | None（None=该段未命中）。"""
    calls = []

    def fake_predict_batch(texts, ids=None, timeout=None):
        calls.append(list(texts))
        out = []
        for t in texts:
            s = score_fn(t)
            out.append(None if s is None else
                       {"mean_surprisal": s, "source": "model"})
        return out

    monkeypatch.setattr(nn_surprisal_bridge, "enabled", lambda: True)
    monkeypatch.setattr(nn_surprisal_bridge, "predict_batch", fake_predict_batch)
    return calls


def _forbid_surprisal(monkeypatch):
    """把 surprisal 桥钉成不可用·且被调用即炸（验证降级路径零调用）。"""
    def _boom(*a, **k):
        raise AssertionError("surprisal 不可用路径不许调 predict_batch")
    monkeypatch.setattr(nn_surprisal_bridge, "enabled", lambda: False)
    monkeypatch.setattr(nn_surprisal_bridge, "predict_batch", _boom)


def _clean_env(monkeypatch):
    monkeypatch.delenv("RUOYU_NN_NLI", raising=False)
    monkeypatch.delenv("RUOYU_NN_SURPRISAL", raising=False)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "active")


_CHARS = [{"name": "沈昭", "locked_facts": [{"fact": "沈家满门尽灭，只剩沈昭一人"}]}]

# 一行一段（网文惯例）·每行 1 个含「沈昭」的候选句
_SENT_A = "沈昭正在灵前烧纸，火光映着一张没有表情的脸"
_SENT_B = "沈昭抬头看了看檐外的天色，转身把门带上"
_SENT_C = "沈昭的兄长沈铖推门而入，掸了掸肩上的积雪"
_DRAFT_3LINES = f"{_SENT_A}。\n{_SENT_B}。\n{_SENT_C}。\n"


# ═══════════ 1. surprisal 可用 → 高熵段优先（降序重排生效）═══════════

def test_surprisal_available_ranks_pairs_by_segment_surprisal_desc(monkeypatch):
    """mock 桥给 C 段最高熵、A 次之、B 最低 → 送 NLI 的配对顺序 = C, A, B
    （文档序是 A, B, C——证明重排真生效而非碰巧）。pair_selection 留痕 surprisal_ranked。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _mock_surprisal(monkeypatch, lambda t: {_SENT_A: 5.0, _SENT_B: 2.0, _SENT_C: 9.0}[
        next(k for k in (_SENT_A, _SENT_B, _SENT_C) if k in t)])
    r = _scan(_CHARS, _DRAFT_3LINES)
    d = r["descriptive"]
    assert d["pair_selection"] == "surprisal_ranked", d
    assert d["pairs_sent"] == 3, d
    sent = [p["hypothesis"] for batch in nli_calls for p in batch]
    assert sent == [_SENT_C, _SENT_A, _SENT_B], sent


def test_surprisal_ranked_stable_ties_keep_document_order(monkeypatch):
    """同分（同段/等熵）稳定保持文档序——排序确定性，报告可复现。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _mock_surprisal(monkeypatch, lambda t: 3.3)   # 全段等熵
    r = _scan(_CHARS, _DRAFT_3LINES)
    d = r["descriptive"]
    assert d["pair_selection"] == "surprisal_ranked", d
    sent = [p["hypothesis"] for batch in nli_calls for p in batch]
    assert sent == [_SENT_A, _SENT_B, _SENT_C], sent


# ═══════════ 2. surprisal 不可用 → 文档序逐字节不变 ═══════════

def test_surprisal_unavailable_document_order_byte_identical(monkeypatch):
    """🔴 诚实降级：surprisal 桥不可用（默认环境 enabled()=False）→ 送 NLI 的
    pairs 与历史嵌套循环（fact 主序 × 文档序）逐字节相同，且绝不调 surprisal 桥。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _forbid_surprisal(monkeypatch)
    chars = [
        {"name": "沈昭", "locked_facts": [{"fact": "沈家满门尽灭，只剩沈昭一人"}]},
        {"name": "沈昭", "locked_facts": [{"fact": "沈昭自幼失明，从未见过雪"}]},
    ]
    r = _scan(chars, _DRAFT_3LINES)
    d = r["descriptive"]
    assert d["pair_selection"] == "document_order", d
    # 历史算法期望序：fact1×(A,B,C) 然后 fact2×(A,B,C)
    expected = ([{"premise": "沈家满门尽灭，只剩沈昭一人", "hypothesis": s}
                 for s in (_SENT_A, _SENT_B, _SENT_C)]
                + [{"premise": "沈昭自幼失明，从未见过雪", "hypothesis": s}
                   for s in (_SENT_A, _SENT_B, _SENT_C)])
    sent = [p for batch in nli_calls for p in batch]
    assert sent == expected, sent
    # violations/meta 的 position 口径也必须保持（取第一个 meta 抽查）
    assert d["pairs_sent"] == 6, d


# ═══════════ 3. pair_selection 留痕在所有路径 ═══════════

def test_pair_selection_field_present_when_mode_off(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "off")
    r = _scan(_CHARS, _DRAFT_3LINES)
    assert r["descriptive"]["pair_selection"] == "document_order", r["descriptive"]


def test_pair_selection_field_present_when_nli_unavailable(monkeypatch):
    """NLI 后端未启用（诚实 skip 路径）→ 字段仍在且为 document_order。"""
    _clean_env(monkeypatch)
    monkeypatch.delenv("LOCKED_FACT_DESCRIPTIVE_MODE", raising=False)  # shadow 默认
    r = _scan(_CHARS, _DRAFT_3LINES)
    d = r["descriptive"]
    assert d["nli_available"] is False, d
    assert d["pair_selection"] == "document_order", d


# ═══════════ 4. 上限仍 64（ranked 模式截断照旧）═══════════

def test_global_cap_still_64_under_surprisal_ranking(monkeypatch):
    """5 个 fact × 20 共现句 = 100 候选 → 单 fact 上限 16 → 80 → 全局截断 64。
    _MAX_NLI_PAIRS 常量本身也锁死 64。"""
    assert lf._MAX_NLI_PAIRS == 64
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _mock_surprisal(monkeypatch, lambda t: float(len(t)))   # 任意确定性分数
    chars = [{"name": "沈昭",
              "locked_facts": [{"fact": f"沈昭的锁定事实第{i}条，纯描述类无数值单位"}
                               for i in range(5)]}]
    draft = "".join(f"沈昭在第{j}个场景里做了一件与别处不同的事。\n" for j in range(20))
    r = _scan(chars, draft)
    d = r["descriptive"]
    assert d["pair_selection"] == "surprisal_ranked", d
    assert d["pairs_sent"] == 64, d
    sent = [p for batch in nli_calls for p in batch]
    assert len(sent) == 64, len(sent)
    # 单 fact 上限 16 同时生效
    per_fact = {}
    for p in sent:
        per_fact[p["premise"]] = per_fact.get(p["premise"], 0) + 1
    assert all(v <= lf._MAX_SENTS_PER_FACT for v in per_fact.values()), per_fact


# ═══════════ 5. 任一段未命中 → 整体诚实回退文档序 ═══════════

def test_partial_surprisal_miss_falls_back_to_document_order(monkeypatch):
    """桥 enabled 但某段返回 None（推理未命中）→ 不产半吊子排序，整体回退文档序。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _mock_surprisal(monkeypatch,
                    lambda t: None if _SENT_B in t else 9.0)   # B 段未命中
    r = _scan(_CHARS, _DRAFT_3LINES)
    d = r["descriptive"]
    assert d["pair_selection"] == "document_order", d
    sent = [p["hypothesis"] for batch in nli_calls for p in batch]
    assert sent == [_SENT_A, _SENT_B, _SENT_C], sent
