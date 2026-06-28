# -*- coding: utf-8 -*-
"""F3 AnchoredAI · Finding schema 锚点透传测试 (R9 W5 Batch-M·2026-06-20)

钉死：
  · audit_hub._parse_violations_scanner 透传 violation.anchor_span → issue.anchor_spans[]
  · 向后兼容: scanner 未填 anchor_span → issue 不出现该字段
  · anchor_span 结构: {char_start:int, char_end:int, surface_text:str(≤200)}
  · 无效 anchor 静默丢弃 (不抛异常)
  · L41 narrator_commentary_scanner 是首个填充 anchor_span 的 pilot scanner
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import audit_hub  # noqa: E402


def _build_report(violations):
    return json.dumps({
        "scanner": "fake",
        "verdict": "FAIL_MINOR",
        "gate_level": "advisory",
        "violations": violations,
    }, ensure_ascii=False)


def test_collect_anchor_spans_empty():
    assert audit_hub._collect_anchor_spans([]) == []
    assert audit_hub._collect_anchor_spans([{"severity": "minor"}]) == []


def test_collect_anchor_spans_valid():
    vs = [{
        "severity": "minor",
        "anchor_span": {"char_start": 10, "char_end": 50,
                        "surface_text": "示例命中片段"},
    }]
    out = audit_hub._collect_anchor_spans(vs)
    assert len(out) == 1
    assert out[0]["char_start"] == 10
    assert out[0]["char_end"] == 50
    assert out[0]["surface_text"] == "示例命中片段"


def test_collect_anchor_spans_truncates_surface():
    long_text = "x" * 500
    vs = [{"anchor_span": {"char_start": 0, "char_end": 500, "surface_text": long_text}}]
    out = audit_hub._collect_anchor_spans(vs)
    assert len(out[0]["surface_text"]) <= 200


def test_collect_anchor_spans_invalid_silently_skipped():
    vs = [
        {"anchor_span": {"char_start": "bad"}},
        {"anchor_span": {}},
        {"anchor_span": "not a dict"},
        {"anchor_span": None},
    ]
    out = audit_hub._collect_anchor_spans(vs)
    assert out == []


def test_parse_violations_scanner_passes_through_anchor():
    """有 anchor_span → issue 含 anchor_spans[]"""
    violations = [{
        "severity": "minor",
        "anchor_span": {"char_start": 100, "char_end": 150,
                        "surface_text": "显然，他错了。"},
    }]
    stdout = _build_report(violations)
    issues = audit_hub._parse_violations_scanner(
        stdout, "narrator_commentary_scanner", "NARRATOR_COMMENTARY_OVERUSE", "风格")
    assert len(issues) == 1
    assert "anchor_spans" in issues[0]
    assert issues[0]["anchor_spans"][0]["char_start"] == 100
    assert issues[0]["anchor_spans"][0]["surface_text"] == "显然，他错了。"


def test_parse_violations_scanner_backwards_compat_no_anchor():
    """无 anchor_span → issue 不出现 anchor_spans 字段 (向后兼容)"""
    violations = [{"severity": "major", "message": "无锚 advisory"}]
    stdout = _build_report(violations)
    issues = audit_hub._parse_violations_scanner(
        stdout, "fake_scanner", "FAKE_CODE", "风格")
    assert len(issues) == 1
    # 严格向后兼容: 字段缺失
    assert "anchor_spans" not in issues[0]


def test_parse_violations_scanner_multi_anchor():
    violations = [
        {"severity": "minor", "anchor_span": {"char_start": 0, "char_end": 10,
                                               "surface_text": "片段一"}},
        {"severity": "minor", "anchor_span": {"char_start": 20, "char_end": 30,
                                               "surface_text": "片段二"}},
        {"severity": "minor"},  # 无 anchor 但不阻断
    ]
    stdout = _build_report(violations)
    issues = audit_hub._parse_violations_scanner(
        stdout, "fake", "NARRATOR_COMMENTARY_OVERUSE", "风格")
    assert "anchor_spans" in issues[0]
    assert len(issues[0]["anchor_spans"]) == 2


def test_l41_pilot_scanner_e2e_anchor_in_audit_hub():
    """L41 narrator_commentary_scanner pilot · 真跑 scanner → audit_hub 拿到 anchor"""
    import tempfile
    import os

    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import narrator_commentary_scanner as ncs

    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        os.environ["NARRATOR_COMMENTARY_MODE"] = "active"
        # 高密度 evaluative 草稿
        filler = "夜风扫过山脊，他独自向上踏步，影子被拉得很长。" * 25
        text = ("显然，他错了。" * 8 + "不得不说，这便是命运。" * 6
                + "毋庸置疑，他无法回头。" * 6 + filler)
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         encoding="utf-8", delete=False)
        f.write(text)
        f.close()
        rep = ncs.scan(f.name)
        # 模拟 scanner 的 JSON stdout
        stdout = json.dumps(rep, ensure_ascii=False)
        issues = audit_hub._parse_violations_scanner(
            stdout, "narrator_commentary_scanner",
            "NARRATOR_COMMENTARY_OVERUSE", "风格")
        assert len(issues) == 1
        assert "anchor_spans" in issues[0]
        assert len(issues[0]["anchor_spans"]) >= 1
        for span in issues[0]["anchor_spans"]:
            assert "char_start" in span
            assert "char_end" in span
            assert "surface_text" in span
            assert span["char_end"] > span["char_start"]
    finally:
        if bak is None:
            os.environ.pop("NARRATOR_COMMENTARY_MODE", None)
        else:
            os.environ["NARRATOR_COMMENTARY_MODE"] = bak


def test_anchor_span_does_not_change_severity_or_code():
    violations = [{
        "severity": "minor",
        "anchor_span": {"char_start": 5, "char_end": 20, "surface_text": "x"},
    }]
    stdout = _build_report(violations)
    issues = audit_hub._parse_violations_scanner(
        stdout, "fake", "NARRATOR_COMMENTARY_OVERUSE", "风格")
    assert issues[0]["code"] == "NARRATOR_COMMENTARY_OVERUSE"
    assert issues[0]["gate_level"] == "advisory"


def test_anchor_span_advisory_only_not_hard_gate():
    """schema 改造不影响 hard_gate 清单·NARRATOR_COMMENTARY_OVERUSE 不在 HARD_GATE_CODES"""
    assert "NARRATOR_COMMENTARY_OVERUSE" not in audit_hub.HARD_GATE_CODES
    assert "REVISION_REDUCED_AUTHOR_FIDELITY" not in audit_hub.HARD_GATE_CODES
    assert "CHRONOTOPE_MONOTONY" not in audit_hub.HARD_GATE_CODES
    assert "MACGUFFIN_ORNAMENTAL" not in audit_hub.HARD_GATE_CODES
    # 🔴 2026-06-27 C03+C18：授权基线 15→18→19（子系统载荷点火 3 码 + splitter 字数守恒 1 码·本 schema 改造不增 hard_gate）
    assert len(audit_hub.HARD_GATE_CODES) == 19
