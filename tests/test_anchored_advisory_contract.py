# -*- coding: utf-8 -*-
"""anchored advisory schema 与 hard_gate 契约测试。

新 schema 升级：
  · anchor_window ≤ 40 CJK 字符
  · recursive_widen_level int(0..3)
基础 violation 可只填 char_start/char_end/surface_text。
"""
import json
import sys
from pathlib import Path

# 锁定 audit_hub anchor schema 与 hard_gate 清单。
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import audit_hub  # noqa: E402


def test_anchor_window_max_constant():
    assert audit_hub.ANCHOR_WINDOW_MAX_CJK == 40


def test_collect_anchor_spans_basic_back_compat():
    """老 schema 不带 anchor_window / recursive_widen_level 仍然工作"""
    vs = [{"anchor_span": {
        "char_start": 10, "char_end": 50,
        "surface_text": "示例命中片段",
    }}]
    out = audit_hub._collect_anchor_spans(vs)
    assert len(out) == 1
    assert "anchor_window" not in out[0]
    assert "recursive_widen_level" not in out[0]


def test_anchor_window_truncated_to_40_cjk():
    long_window = "一" * 100
    vs = [{"anchor_span": {
        "char_start": 0, "char_end": 100,
        "surface_text": "x",
        "anchor_window": long_window,
    }}]
    out = audit_hub._collect_anchor_spans(vs)
    # 截到 40 CJK
    cjk_count = sum(1 for ch in out[0]["anchor_window"] if "一" <= ch <= "鿿")
    assert cjk_count <= audit_hub.ANCHOR_WINDOW_MAX_CJK


def test_anchor_window_short_kept_intact():
    short_window = "这是一段短的引用"
    vs = [{"anchor_span": {
        "char_start": 0, "char_end": 10,
        "surface_text": "x",
        "anchor_window": short_window,
    }}]
    out = audit_hub._collect_anchor_spans(vs)
    assert out[0]["anchor_window"] == short_window


def test_recursive_widen_level_kept():
    vs = [{"anchor_span": {
        "char_start": 0, "char_end": 10,
        "surface_text": "x",
        "recursive_widen_level": 2,
    }}]
    out = audit_hub._collect_anchor_spans(vs)
    assert out[0]["recursive_widen_level"] == 2


def test_recursive_widen_level_out_of_range_dropped():
    vs = [{"anchor_span": {
        "char_start": 0, "char_end": 10,
        "surface_text": "x",
        "recursive_widen_level": 99,
    }}]
    out = audit_hub._collect_anchor_spans(vs)
    assert "recursive_widen_level" not in out[0]


def test_recursive_widen_level_zero_kept():
    vs = [{"anchor_span": {
        "char_start": 0, "char_end": 10,
        "surface_text": "x",
        "recursive_widen_level": 0,
    }}]
    out = audit_hub._collect_anchor_spans(vs)
    assert out[0]["recursive_widen_level"] == 0


def test_recursive_widen_level_non_int_dropped():
    vs = [{"anchor_span": {
        "char_start": 0, "char_end": 10,
        "surface_text": "x",
        "recursive_widen_level": "two",
    }}]
    out = audit_hub._collect_anchor_spans(vs)
    assert "recursive_widen_level" not in out[0]


def test_anchor_window_non_str_ignored():
    vs = [{"anchor_span": {
        "char_start": 0, "char_end": 10,
        "surface_text": "x",
        "anchor_window": 42,
    }}]
    out = audit_hub._collect_anchor_spans(vs)
    assert "anchor_window" not in out[0]


def test_registry_has_anchored_advisory_contract_block():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    block = reg.get("anchored_advisory_contract")
    assert block is not None
    assert block.get("anchor_window_max_cjk") == 40
    assert block.get("recursive_widen_level_range") == [0, 3]


def test_hard_gate_codes_unchanged_15():
    """R24 W12 Batch-KK schema 升级不增不减 hard_gate 码（本件不动 HARD_GATE_CODES）。
    🔴 2026-06-27 C03：基线 15→18（子系统载荷点火 3 码经独立 C03 特性入列·非本 schema 改动）。"""
    # 北极星⑤：本 schema 升级不黑箱新增 hard_gate；当前授权基线 = 19
    # 🔴 2026-06-27 C18：18→19（splitter 字数守恒 SPLIT_WORD_NOT_CONSERVED 经独立 C18 特性入列·非本 schema 改动）
    assert len(audit_hub.HARD_GATE_CODES) == 19


def test_existing_no_anchor_violation_still_works():
    """无 anchor_span 的 violation 完全不受影响"""
    vs = [{"severity": "minor", "message": "无锚 advisory"}]
    out = audit_hub._collect_anchor_spans(vs)
    assert out == []
