# -*- coding: utf-8 -*-
"""locked_fact 描述类 NLI 通路 · 配对窗选择策略回归锁（S4 高熵优先 + S5 三区间均衡轮询）

出处：research/open_source_writing_systems_round2.md S4 / S5。
  · S4（ConStory-Checker arXiv:2603.05890）：「一致性错误集中在 token 熵高的文本段」——
    surprisal 可用（nn_surprisal_bridge）时，候选配对按所在段 surprisal 降序重排后再截断
    _MAX_NLI_PAIRS=64（高熵段优先送 NLI·优先级最高·跨区间生效）。
  · S5（FlawedFictions arXiv:2504.11900 反向校准·2026-07-07 遗留根治）：旧「surprisal
    不可用→文档序前 64」实证只盖 22.5k 字草稿前 12% 的主角句——晚位注入恒漏
    （scratchpad s5_s6_calibration/S5_flawed_fictions_report.md §5.2）。现默认策略=
    三区间均衡轮询：草稿按段落三等分（前/中/后），64 对预算按 fact × 区间轮询均衡分配
    （单 fact 16 句上限内先各区间取样再补齐），保证晚位句子进得了配对窗。

钉死的纪律（每条都有测试）：
  1. surprisal 可用（mock 桥）→ 候选配对按所在段 surprisal 降序送 NLI ·
     pair_selection="surprisal_ranked"（S4 优先级仍最高·高熵优先跨区间生效）
  2. surprisal 不可用（默认环境）→ S5 三区间均衡轮询（fact × 区间轮询交错·确定性）·
     pair_selection="balanced_rotation" · 绝不调 surprisal 桥
  3. pair_selection 留痕在所有路径（off / NLI 不可用 / 执行）都存在
  4. 上限仍 64（_MAX_NLI_PAIRS 零变动）：ranked / balanced 两模式截断都照旧生效
  5. 任一所在段 surprisal 未命中 → 整体诚实回退 S5 均衡轮询（不产半吊子排序）
  6. S5 晚位句进窗正例：目标句在草稿末段（旧文档序前 16 进不了窗·新策略进得了）
  7. S5 单 fact 上限仍 16 · 16 上限内三区间均衡（先各区间取样再补齐）

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


def _mock_nli(monkeypatch, label_fn=None):
    """NLI 桥 mock（默认全 neutral）·捕获送入的 pairs 顺序。
    label_fn(pair)->(label, prob) 可自定义判定。"""
    calls = []

    def fake_predict_batch(pairs, timeout=None):
        calls.append(list(pairs))
        out = []
        for p in pairs:
            label, prob = (label_fn(p) if label_fn else ("neutral", 0.9))
            probs = {"entailment": 0.0, "neutral": 0.0, "contradiction": 0.0}
            probs[label] = prob
            out.append({"label": label, "probs": probs, "source": "nli"})
        return out

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

# 一行一段（网文惯例）·每行 1 个含「沈昭」的候选句（3 段=前/中/后各 1 区间）
_SENT_A = "沈昭正在灵前烧纸，火光映着一张没有表情的脸"
_SENT_B = "沈昭抬头看了看檐外的天色，转身把门带上"
_SENT_C = "沈昭的兄长沈铖推门而入，掸了掸肩上的积雪"
_DRAFT_3LINES = f"{_SENT_A}。\n{_SENT_B}。\n{_SENT_C}。\n"


def _numbered_draft(n):
    """n 个段落（一行一段）·每段 1 个含「沈昭」的候选句（≥_MIN_SENT_CJK 字）。"""
    return "".join(f"沈昭在第{j}号回廊里查看了一件与别处不同的旧物。\n" for j in range(n))


def _para_no(hypothesis):
    """从 _numbered_draft 句子里抽段号。"""
    return int(hypothesis.split("第")[1].split("号")[0])


# ═══════════ 1. surprisal 可用 → 高熵段优先（S4 优先级仍最高）═══════════

def test_surprisal_available_ranks_pairs_by_segment_surprisal_desc(monkeypatch):
    """mock 桥给 C 段最高熵、A 次之、B 最低 → 送 NLI 的配对顺序 = C, A, B
    （文档序是 A, B, C——证明重排真生效而非碰巧）。pair_selection 留痕 surprisal_ranked。
    S5 落地后 S4 优先级仍最高：surprisal 可用时均衡轮询让位于高熵优先（跨区间生效）。"""
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


# ═══════════ 2. surprisal 不可用 → S5 三区间均衡轮询（确定性）═══════════

def test_surprisal_unavailable_balanced_rotation_deterministic(monkeypatch):
    """🔴 S5 默认策略：surprisal 桥不可用（默认环境 enabled()=False）→ 候选按
    fact × 区间轮询交错（取代旧「文档序前 64」），且绝不调 surprisal 桥。
    3 段草稿=三区间各 1 句 → 2 个 fact 的期望序 = fact 轮询交错：
    f1×A, f2×A, f1×B, f2×B, f1×C, f2×C（旧文档序是 f1×(A,B,C) 再 f2×(A,B,C)）。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _forbid_surprisal(monkeypatch)
    _F1 = "沈家满门尽灭，只剩沈昭一人"
    _F2 = "沈昭自幼失明，从未见过雪"
    chars = [
        {"name": "沈昭", "locked_facts": [{"fact": _F1}]},
        {"name": "沈昭", "locked_facts": [{"fact": _F2}]},
    ]
    r = _scan(chars, _DRAFT_3LINES)
    d = r["descriptive"]
    assert d["pair_selection"] == "balanced_rotation", d
    expected = [{"premise": f, "hypothesis": s}
                for s in (_SENT_A, _SENT_B, _SENT_C) for f in (_F1, _F2)]
    sent = [p for batch in nli_calls for p in batch]
    assert sent == expected, sent
    assert d["pairs_sent"] == 6, d


def test_balanced_rotation_deterministic_across_runs(monkeypatch):
    """S5 确定性：同输入重复跑两次 → 送 NLI 的配对序列逐字节相同（无随机·报告可复现）。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _forbid_surprisal(monkeypatch)
    chars = [{"name": "沈昭",
              "locked_facts": [{"fact": f"沈昭的锁定事实第{i}条，纯描述类无数值单位"}
                               for i in range(3)]}]
    draft = _numbered_draft(12)
    r1 = _scan(chars, draft)
    r2 = _scan(chars, draft)
    assert r1["descriptive"]["pair_selection"] == "balanced_rotation"
    assert r1["descriptive"]["pairs_sent"] == r2["descriptive"]["pairs_sent"]
    assert nli_calls[0] == nli_calls[1], "同输入两次跑必须产出相同配对序列"


# ═══════════ 3. pair_selection 留痕在所有路径 ═══════════

def test_pair_selection_field_present_when_mode_off(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LOCKED_FACT_DESCRIPTIVE_MODE", "off")
    r = _scan(_CHARS, _DRAFT_3LINES)
    assert r["descriptive"]["pair_selection"] == "balanced_rotation", r["descriptive"]


def test_pair_selection_field_present_when_nli_unavailable(monkeypatch):
    """NLI 后端未启用（诚实 skip 路径）→ 字段仍在且为 balanced_rotation（默认策略）。"""
    _clean_env(monkeypatch)
    monkeypatch.delenv("LOCKED_FACT_DESCRIPTIVE_MODE", raising=False)  # shadow 默认
    r = _scan(_CHARS, _DRAFT_3LINES)
    d = r["descriptive"]
    assert d["nli_available"] is False, d
    assert d["pair_selection"] == "balanced_rotation", d


# ═══════════ 4. 上限仍 64（两模式截断照旧）═══════════

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


def test_global_cap_still_64_under_balanced_rotation(monkeypatch):
    """S5 均衡轮询下预算零变动：5 fact × 20 句 → 单 fact 16 → 全局截断 64。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _forbid_surprisal(monkeypatch)
    chars = [{"name": "沈昭",
              "locked_facts": [{"fact": f"沈昭的锁定事实第{i}条，纯描述类无数值单位"}
                               for i in range(5)]}]
    r = _scan(chars, _numbered_draft(20))
    d = r["descriptive"]
    assert d["pair_selection"] == "balanced_rotation", d
    assert d["pairs_sent"] == 64, d
    sent = [p for batch in nli_calls for p in batch]
    assert len(sent) == 64, len(sent)
    per_fact = {}
    for p in sent:
        per_fact[p["premise"]] = per_fact.get(p["premise"], 0) + 1
    assert all(v <= lf._MAX_SENTS_PER_FACT for v in per_fact.values()), per_fact


# ═══════════ 5. 任一段 surprisal 未命中 → 整体诚实回退 S5 均衡轮询 ═══════════

def test_partial_surprisal_miss_falls_back_to_balanced_rotation(monkeypatch):
    """桥 enabled 但某段返回 None（推理未命中）→ 不产半吊子排序，整体回退
    S5 均衡轮询（单 fact × 三区间各 1 句时轮询序恰为 A,B,C）。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _mock_surprisal(monkeypatch,
                    lambda t: None if _SENT_B in t else 9.0)   # B 段未命中
    r = _scan(_CHARS, _DRAFT_3LINES)
    d = r["descriptive"]
    assert d["pair_selection"] == "balanced_rotation", d
    sent = [p["hypothesis"] for batch in nli_calls for p in batch]
    assert sent == [_SENT_A, _SENT_B, _SENT_C], sent


# ═══════════ 6. S5 晚位句进窗正例（遗留根治核心）═══════════

def test_late_position_sentence_enters_pair_window(monkeypatch):
    """🔴 S5 反向校准根治正例：24 段草稿·矛盾目标句在第 16 段（末三分之一区间首段）。
    旧策略（文档序 + 单 fact 上限 16）只送前 16 段 → 目标句恒漏；
    新策略区间轮询首轮即取末段句 → 目标句进窗且被检出 violation。"""
    _clean_env(monkeypatch)
    target = "沈昭的兄长沈铖在第16号回廊尽头推门而入，掸了掸肩上的积雪"
    lines = [f"沈昭在第{j}号回廊里查看了一件与别处不同的旧物" for j in range(24)]
    lines[16] = target
    draft = "".join(s + "。\n" for s in lines)
    nli_calls = _mock_nli(monkeypatch, lambda p: (
        ("contradiction", 0.95) if "兄长" in p["hypothesis"] else ("neutral", 0.9)))
    _forbid_surprisal(monkeypatch)
    r = _scan(_CHARS, draft)
    d = r["descriptive"]
    assert d["pair_selection"] == "balanced_rotation", d
    assert d["pairs_sent"] == 16, d          # 单 fact 上限仍 16
    sent = [p["hypothesis"] for batch in nli_calls for p in batch]
    # 旧文档序前 16 = 第 0-15 段 → 目标句（第 16 段）必不在窗内（根治前的漏报形态）
    doc_order_first_16 = lines[:16]
    assert target not in doc_order_first_16
    # 新策略：目标句进窗 + 检出
    assert target in sent, sent
    assert len(d["violations"]) == 1, d
    assert d["violations"][0]["sentence"] == target, d


def test_per_fact_cap_16_with_region_balance(monkeypatch):
    """S5 单 fact 上限仍 16，且 16 上限内三区间均衡（先各区间取样再补齐）：
    30 段（每区间 10 句）→ 选中 16 句的区间分布 = 前 6 / 中 5 / 后 5。"""
    assert lf._MAX_SENTS_PER_FACT == 16
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _forbid_surprisal(monkeypatch)
    r = _scan(_CHARS, _numbered_draft(30))
    d = r["descriptive"]
    assert d["pair_selection"] == "balanced_rotation", d
    assert d["pairs_sent"] == 16, d
    paras = [_para_no(p["hypothesis"]) for batch in nli_calls for p in batch]
    counts = {0: 0, 1: 0, 2: 0}
    for j in paras:
        counts[j * 3 // 30] += 1
    assert counts == {0: 6, 1: 5, 2: 5}, counts
    # 每个区间的选中句在区间内保持文档序（前区间=第 0-5 段）
    assert sorted(j for j in paras if j < 10) == [0, 1, 2, 3, 4, 5], paras


def test_region_exhausted_tops_up_from_remaining(monkeypatch):
    """S5「补齐」语义：候选全部集中在前三分之一（中/后区间零候选）→ 区间耗尽自动跳过，
    照常送满全部候选（不因区间空而丢句）。"""
    _clean_env(monkeypatch)
    nli_calls = _mock_nli(monkeypatch)
    _forbid_surprisal(monkeypatch)
    # 18 段：前 6 段含「沈昭」，后 12 段是无人名噪声段（不成候选）
    lines = [f"沈昭在第{j}号回廊里查看了一件与别处不同的旧物" for j in range(6)]
    lines += [f"廊外的风卷着沙尘掠过第{j}根柱子，灯影摇成碎金" for j in range(6, 18)]
    draft = "".join(s + "。\n" for s in lines)
    r = _scan(_CHARS, draft)
    d = r["descriptive"]
    assert d["pair_selection"] == "balanced_rotation", d
    assert d["pairs_sent"] == 6, d
    sent = [p["hypothesis"] for batch in nli_calls for p in batch]
    assert sent == lines[:6], sent   # 单区间退化=文档序（确定性）
