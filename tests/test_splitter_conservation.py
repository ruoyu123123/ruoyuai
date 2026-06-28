"""C18 splitter 字数守恒 hard_gate 回归测试（2026-06-27）。

守护三条恒等（北极星④纯格式层 round-trip 完整性 · 治切章后 0 audit 丢字/重复 silently）：
  ① sum(per_chapter_cjk) + pending_tail_cjk == draft_cjk（CJK 精确整数）
  ② 无空 chunk 落盘
  ③ len(chunks) == chapters_split == len(per_chapter_cjk)
任一破 → SplitterIntegrityError(code=SPLIT_WORD_NOT_CONSERVED) → _main_freestyle exit 2（坏章节零落盘）。

🔴 北极星⑤边界锁：守恒只查 CJK 守恒 + 无空块 + 计数同步——绝不断言章数 N（v27/v28 fluid 禁锁）/
切点质量 / 叙事顺序 / 任何内容判断。本测试也只验守恒，不验切点对错。
"""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chapter_splitter as cs   # noqa: E402
import chapter_io as cio        # noqa: E402


def _make_draft(n_paras, cjk_per_para=100):
    """合成纯 CJK 草稿（每段同长·无伪标题·无对话·便于守恒精确核对）。"""
    para = "字" * cjk_per_para
    return "\n\n".join([para] * n_paras)


# ============ (a) 正常守恒过 ============

def test_normal_split_conserved():
    """常规多章切割 · 守恒恒等成立 · delta==0。"""
    text = _make_draft(150, 100)  # 15000 CJK
    with tempfile.TemporaryDirectory() as tmp:
        r = cs.run_freestyle(Path(tmp), "cluster_001", 1, text,
                             "标准", None, True, narrative_mode="linear")
    integ = r["integrity"]
    assert integ["conserved"] is True
    assert integ["accounted"] == integ["draft_cjk"]
    assert integ["delta"] == 0
    # 亲手复核恒等（不只信 report 自报）
    assert sum(r["per_chapter_cjk"]) + r["pending_tail"]["cjk"] == r["draft_cjk_total"]
    assert len(r["per_chapter_cjk"]) == r["chapters_split"]


# ============ (b) 人为篡改漏段 → 必 raise ============

def test_tamper_missing_segment_raises():
    """per_chapter_cjk 总和 < draft_cjk（漏段/丢字）→ raise + delta<0 + conserved=False。"""
    report = {}
    with pytest.raises(cs.SplitterIntegrityError) as ei:
        cs._assert_word_conservation(report, draft_cjk=10000,
                                     per_chapter_cjk=[3000, 3000], pending_tail_cjk=0,
                                     chunks_for_empty_check=["a" * 3000, "b" * 3000],
                                     chapters_split=2)
    assert ei.value.code == "SPLIT_WORD_NOT_CONSERVED"
    assert ei.value.delta == -4000  # accounted 6000 - draft 10000
    assert report["integrity"]["conserved"] is False
    assert report["integrity"]["delta"] == -4000


def test_tamper_duplicate_segment_raises():
    """per_chapter_cjk 总和 > draft_cjk（重复）→ raise + delta>0。"""
    report = {}
    with pytest.raises(cs.SplitterIntegrityError) as ei:
        cs._assert_word_conservation(report, draft_cjk=6000,
                                     per_chapter_cjk=[3000, 3000, 1000], pending_tail_cjk=0,
                                     chunks_for_empty_check=["a" * 3000, "b" * 3000, "c" * 1000],
                                     chapters_split=3)
    assert ei.value.delta == 1000


def test_empty_chunk_raises():
    """落盘空 chunk（whitespace-only）→ raise（即便字数恰好守恒）。"""
    report = {}
    with pytest.raises(cs.SplitterIntegrityError):
        cs._assert_word_conservation(report, draft_cjk=6000,
                                     per_chapter_cjk=[6000, 0], pending_tail_cjk=0,
                                     chunks_for_empty_check=["x" * 6000, "   "],
                                     chapters_split=2)
    assert report["integrity"]["conserved"] is False


def test_count_mismatch_raises():
    """chapters_split 与实际 chunk 数失配 → raise（即便字数守恒）。"""
    report = {}
    with pytest.raises(cs.SplitterIntegrityError):
        cs._assert_word_conservation(report, draft_cjk=6000,
                                     per_chapter_cjk=[3000, 3000], pending_tail_cjk=0,
                                     chunks_for_empty_check=["x" * 3000, "y" * 3000],
                                     chapters_split=3)  # 报 3 章但只 2 chunk


def test_end_to_end_drop_paragraph_raises(monkeypatch):
    """端到端：monkeypatch _slice_by_indices 丢字 → run_freestyle 必 raise（守恒兜底真拦得住）。"""
    text = _make_draft(150, 100)
    orig = cs._slice_by_indices

    def _lossy(paras, split_indices):
        chunks = orig(paras, split_indices)
        if chunks:
            chunks[0] = chunks[0][:-50]  # 砍首 chunk 末 50 CJK（模拟 splitter 丢字 bug）
        return chunks

    monkeypatch.setattr(cs, "_slice_by_indices", _lossy)
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(cs.SplitterIntegrityError):
            cs.run_freestyle(Path(tmp), "cluster_001", 1, text,
                             "标准", None, True, narrative_mode="linear")


# ============ (c) prepend pending_tail 守恒 ============

def test_prepend_pending_tail_conserved():
    """跨 cluster 补料：draft_cjk 含 prepend·守恒含 pending_tail。"""
    text = _make_draft(120, 100)       # 12000 CJK
    prev_tail = _make_draft(20, 100)   # 2000 CJK 上 cluster pending_tail
    with tempfile.TemporaryDirectory() as tmp:
        r = cs.run_freestyle(Path(tmp), "cluster_002", 5, text,
                             "标准", prev_tail, True, narrative_mode="linear")
    integ = r["integrity"]
    assert integ["conserved"] is True
    # draft_cjk 基线 = prepend + 本 cluster 草稿（伪标题剥离后·此处无伪标题）
    assert integ["draft_cjk"] == cio.count_cjk(prev_tail) + cio.count_cjk(text)
    assert sum(r["per_chapter_cjk"]) + r["pending_tail"]["cjk"] == integ["draft_cjk"]
    assert r["previous_pending_tail_consumed_cjk"] == cio.count_cjk(prev_tail)


# ============ (d) N==0 全退 pending_tail 守恒 ============

def test_n_zero_all_pending_tail_conserved():
    """草稿 < 单章下限 → N==0 · 整段退 pending_tail · accounted == pending_tail == draft_cjk。"""
    text = _make_draft(10, 100)  # 1000 CJK < lo 3000
    with tempfile.TemporaryDirectory() as tmp:
        r = cs.run_freestyle(Path(tmp), "cluster_003", 9, text,
                             "标准", None, True, narrative_mode="linear")
    assert r["chapters_split"] == 0
    integ = r["integrity"]
    assert integ["conserved"] is True
    assert integ["pending_tail_cjk"] == integ["draft_cjk"]
    assert integ["accounted"] == integ["draft_cjk"]
    assert r["pending_tail"]["exists"] is True


# ============ (e) 末章上溢再平衡后守恒 ============

def test_tail_overflow_rebalance_conserved():
    """轮次2/6 实测的末章上溢再平衡路径·再平衡后仍守恒（搬段不丢字）。"""
    para = "这是一个测试段落，" * 10   # ~80 CJK/段
    text = "\n\n".join([para] * 195)
    with tempfile.TemporaryDirectory() as tmp:
        r = cs.run_freestyle(Path(tmp), "cluster_001", 1, text,
                             "标准", None, True, narrative_mode="linear")
    integ = r["integrity"]
    assert integ["conserved"] is True
    assert integ["accounted"] == integ["draft_cjk"]
    hi = r["_freestyle_decision_log"]["per_chapter_range"][1]
    assert all(c <= hi for c in r["per_chapter_cjk"]), \
        f"末章上溢未再平衡: {r['per_chapter_cjk']} hi={hi}"
    assert sum(r["per_chapter_cjk"]) + r["pending_tail"]["cjk"] == integ["draft_cjk"]


# ============ SplitterIntegrityError 形态契约 ============

def test_integrity_error_code_constant():
    """code 常量与三方 hard_gate 清单一致（SPLIT_WORD_NOT_CONSERVED）。"""
    assert cs.SplitterIntegrityError.code == "SPLIT_WORD_NOT_CONSERVED"
    e = cs.SplitterIntegrityError("boom", delta=-7)
    assert e.delta == -7
    assert e.code == "SPLIT_WORD_NOT_CONSERVED"
