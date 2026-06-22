# -*- coding: utf-8 -*-
"""[G2 P2 2026-06-22] 词典外部化 + 17 scanner 接 audit_hub SHADOW_SCANNERS

覆盖：
1. parrhesia_density_scanner 优先读 core/data/parrhesia_lexicon.json·缺失/损坏 fallback 内嵌
2. antagonist_valence_trajectory 优先读 core/data/antagonist_valence_lexicon.json·缺失/损坏 fallback 内嵌
3. scanner_registry.json 4 个 high-value scanner 含 lexicon_path 字段
4. audit_hub 17 scanner 接齐(R18 Batch-T 3 + R22-R25 14)·_parse_advisories_scanner 解析
5. test_code_not_in_hard_gate registry 守卫 - 17 个 code 均不在 HARD_GATE_CODES
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
_DATA = _ROOT / "core" / "data"
sys.path.insert(0, str(_SCRIPTS))


# ========== Phase A: 外部 lexicon 加载验证 ==========

def test_parrhesia_lexicon_file_exists_with_all_signals():
    p = _DATA / "parrhesia_lexicon.json"
    assert p.exists(), "core/data/parrhesia_lexicon.json 必须存在"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("_placeholder") is True
    for k in ("power_lower", "power_upper", "truth_claim", "risk_posture"):
        assert k in data, f"parrhesia_lexicon 缺信号键 {k}"
        assert isinstance(data[k], list) and data[k], f"{k} 必须非空 list"


def test_antagonist_valence_lexicon_file_exists():
    p = _DATA / "antagonist_valence_lexicon.json"
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("_placeholder") is True
    assert "positive_lex" in data and "negative_lex" in data
    assert len(data["positive_lex"]) >= 10
    assert len(data["negative_lex"]) >= 10


def test_parrhesia_scanner_loads_external_lexicon():
    """parrhesia_density_scanner._LEXICONS 必须来自外部·4 信号键齐全"""
    import parrhesia_density_scanner as mod
    importlib.reload(mod)
    assert mod._LEXICON_PATH.exists()
    for k in ("power_lower", "power_upper", "truth_claim", "risk_posture"):
        assert k in mod._LEXICONS, f"加载后缺 {k}"
        assert isinstance(mod._LEXICONS[k], list) and mod._LEXICONS[k]
    # 内嵌 fallback 仍存在(向后兼容)
    assert "_LEXICONS_FALLBACK" in dir(mod)


def test_parrhesia_scanner_fallback_to_inline_on_missing_file(tmp_path, monkeypatch):
    """文件缺失时 fallback 到内嵌·验证向后兼容"""
    import parrhesia_density_scanner as mod
    # 指向不存在的路径
    bogus_path = tmp_path / "nonexistent_parrhesia_lexicon.json"
    monkeypatch.setattr(mod, "_LEXICON_PATH", bogus_path)
    lex = mod._load_lexicons()
    assert lex.get("_placeholder") is True
    assert "power_lower" in lex
    # 必须 fallback (而非空)
    assert len(lex["power_lower"]) > 0


def test_parrhesia_scanner_fallback_to_inline_on_corrupt_file(tmp_path, monkeypatch):
    """文件损坏时 fallback 到内嵌"""
    import parrhesia_density_scanner as mod
    corrupt = tmp_path / "corrupt_lex.json"
    corrupt.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr(mod, "_LEXICON_PATH", corrupt)
    lex = mod._load_lexicons()
    assert lex.get("_placeholder") is True
    assert "power_lower" in lex


def test_parrhesia_scanner_fallback_on_missing_signal_key(tmp_path, monkeypatch):
    """文件存在但缺信号键 → fallback (防半残文件)"""
    import parrhesia_density_scanner as mod
    incomplete = tmp_path / "incomplete_lex.json"
    incomplete.write_text(json.dumps({"power_lower": ["a"]}), encoding="utf-8")
    monkeypatch.setattr(mod, "_LEXICON_PATH", incomplete)
    lex = mod._load_lexicons()
    # 缺其他 3 个信号 → 走 fallback
    assert lex is mod._LEXICONS_FALLBACK


def test_antagonist_valence_loads_external_lexicon():
    import antagonist_valence_trajectory as mod
    importlib.reload(mod)
    assert mod._LEXICON_PATH.exists()
    assert isinstance(mod.POSITIVE_LEX, tuple) and len(mod.POSITIVE_LEX) >= 10
    assert isinstance(mod.NEGATIVE_LEX, tuple) and len(mod.NEGATIVE_LEX) >= 10
    # fallback 仍可访问(向后兼容)
    assert hasattr(mod, "_POSITIVE_FALLBACK")
    assert hasattr(mod, "_NEGATIVE_FALLBACK")


def test_antagonist_valence_fallback_on_missing_file(tmp_path, monkeypatch):
    import antagonist_valence_trajectory as mod
    bogus = tmp_path / "missing.json"
    monkeypatch.setattr(mod, "_LEXICON_PATH", bogus)
    pos, neg = mod._load_valence_lexicon()
    assert pos == mod._POSITIVE_FALLBACK
    assert neg == mod._NEGATIVE_FALLBACK


# ========== Phase A: scanner_registry.json lexicon_path 字段 ==========

def test_scanner_registry_has_lexicon_path_for_externalized_scanners():
    reg_path = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    scanners = reg.get("scanners", {})
    # 4 个 high-value 词典外部化条目必带 lexicon_path
    for name, expected in [
        ("parrhesia_density_scanner", "core/data/parrhesia_lexicon.json"),
        ("antagonist_valence_trajectory", "core/data/antagonist_valence_lexicon.json"),
        ("imageability", "core/data/imageability_zh.json"),
        ("zeugma", "core/data/verb_object_collocation_freq.json"),
    ]:
        assert name in scanners, f"registry 缺 scanner 条目 {name}"
        assert "lexicon_path" in scanners[name], f"{name} 缺 lexicon_path 字段"
        assert scanners[name]["lexicon_path"] == expected, (
            f"{name}.lexicon_path 应为 {expected}·实际 {scanners[name]['lexicon_path']}")
        # 路径必须真存在
        lex_file = _ROOT / scanners[name]["lexicon_path"]
        assert lex_file.exists(), f"{name} 引用的 lexicon 文件不存在: {lex_file}"


# ========== Phase B: audit_hub 17 scanner 接齐 ==========

def test_audit_hub_has_parse_advisories_scanner_helper():
    """新增 _parse_advisories_scanner helper 必须存在"""
    import audit_hub
    assert hasattr(audit_hub, "_parse_advisories_scanner")


def test_parse_advisories_scanner_empty_input():
    import audit_hub
    issues = audit_hub._parse_advisories_scanner("", "src", "CODE", "风格")
    assert issues == []


def test_parse_advisories_scanner_with_advisories():
    """advisories[] 形态 → 每条出 1 issue"""
    import audit_hub
    payload = json.dumps({
        "scanner": "writer_growth_dashboard",
        "advisories": [
            {"code": "WRITER_GROWTH_VOCAB_DROP", "msg": "TTR 单调下降"},
            {"code": "WRITER_GROWTH_NO_RESPONSE_TO_FEEDBACK", "msg": "未响应反馈"},
        ],
    })
    issues = audit_hub._parse_advisories_scanner(
        payload, "writer_growth_dashboard", "WRITER_GROWTH_VOCAB_DROP", "风格")
    assert len(issues) == 2
    codes = sorted(i["code"] for i in issues)
    assert codes == ["WRITER_GROWTH_NO_RESPONSE_TO_FEEDBACK", "WRITER_GROWTH_VOCAB_DROP"]
    for i in issues:
        assert i["gate_level"] in ("advisory", "info")
        assert i["dimension"] == "风格"
        assert i["source"] == "writer_growth_dashboard"


def test_parse_advisories_scanner_violations_takes_priority():
    """若同时有 violations 和 advisories·走 violations 路径(_parse_violations_scanner 合成 1 条聚合)"""
    import audit_hub
    payload = json.dumps({
        "scanner": "x",
        "violations": [{"code": "X_CODE", "severity": "minor", "kind": "x"}],
        "advisories": [{"code": "X_CODE", "msg": "should be ignored"}],
    })
    issues = audit_hub._parse_advisories_scanner(payload, "x", "X_CODE", "风格")
    # violations 路径合成 1 条聚合 issue
    assert len(issues) == 1
    assert issues[0]["code"] == "X_CODE"


def test_parse_advisories_scanner_no_advisories_no_violations():
    """vital_relations_probe 这种 probe 输出·无 advisories/violations → 不产 issue"""
    import audit_hub
    payload = json.dumps({
        "schema_version": "1.0",
        "cjk": 100,
        "lexicon_placeholder": True,
        "vital_relations_density": 1.5,
    })
    issues = audit_hub._parse_advisories_scanner(
        payload, "vital_relations_probe", "VITAL_RELATIONS_DENSITY_TRACK", "风格")
    assert issues == []  # probe 零回归保障


# ========== 17 scanner 在线性(SHADOW_SCANNERS)守卫 ==========

# 17 个 scanner name + 期望的 default code
SHADOW_SCANNER_REGISTRY = [
    # R18 Batch-T 3
    ("paratactic_implicit_logic", "PARATAXIS_OFF_AUTHOR_BAND", "paratactic_implicit_logic_scanner.py"),
    ("indirect_characterization_ratio", "INDIRECT_CHARACTERIZATION_THIN", "indirect_characterization_ratio_scanner.py"),
    ("centering_theory_focus", "CENTERING_ROUGH_SHIFT_OVERLOAD", "centering_theory_focus_scanner.py"),
    # R22 (4)
    ("zeugma", "ZEUGMA_DETECTED", "zeugma_scanner.py"),
    ("anadiplosis", "ANADIPLOSIS_DETECTED", "anadiplosis_scanner.py"),
    ("premise_blend_card", "PREMISE_BLEND_CARD_MISSING", "premise_blend_card_scanner.py"),
    ("vital_relations_probe", "VITAL_RELATIONS_DENSITY_TRACK", "vital_relations_probe.py"),
    # R23 (7) - 不含 choice_consequence_ledger (CLI 工具非 scanner)
    ("parrhesia_density", "PARRHESIA_DENSITY_THIN", "parrhesia_density_scanner.py"),
    ("cluster_rasa_layer", "RASA_LAYER_DOMINANT_DRIFT", "cluster_rasa_layer_consistency_scanner.py"),
    ("rasa_causal_chain", "RASA_CAUSAL_BREAK_MISMATCH", "rasa_causal_chain_scanner.py"),
    ("direction_card_poetics", "DIRECTION_CARD_FRAMING_THIN", "direction_card_poetics_scanner.py"),
    ("audio_performance", "AUDIO_PERF_TRI_CLAUSE_HEAVY", "audio_performance_scanner.py"),
    ("writer_growth_dashboard", "WRITER_GROWTH_VOCAB_DROP", "writer_growth_dashboard.py"),
    ("antagonist_valence_trajectory", "ANTAGONIST_VALENCE_DRIFT_UNAUTHORIZED", "antagonist_valence_trajectory.py"),
    # R24 (3) - 不含 intent_ledger (CLI 工具非 scanner)
    ("microdrama_intraep_beat_lattice", "MICRODRAMA_HEAD_3S_NO_ACTION", "microdrama_intraep_beat_lattice.py"),
    ("prose_180_axis", "PROSE_AXIS_FLIP", "prose_180_axis_scanner.py"),
    ("imageability_round_trip_probe", "IMAGEABILITY_ROUND_TRIP_LOW", "imageability_round_trip_probe.py"),
]


def test_audit_hub_source_contains_all_17_shadow_scanners():
    """audit_hub.py 必须 import / 调度 17 个 scanner·验证「在线」"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    for name, code, filename in SHADOW_SCANNER_REGISTRY:
        assert filename in src, f"audit_hub.py 缺 scanner 文件名 {filename}"
        # 验证调度元组的 name 字段
        assert f'("{name}"' in src, f"audit_hub.py 缺 tasks 元组 ({name},...)"
        # 验证默认 code 出现
        assert f'"{code}"' in src, f"audit_hub.py 缺默认 code {code}"


def test_all_17_scanner_script_files_exist():
    for name, code, filename in SHADOW_SCANNER_REGISTRY:
        assert (_SCRIPTS / filename).exists(), f"scanner 脚本不存在: {filename}"


def test_17_scanner_codes_not_in_hard_gate():
    """北极星⑤·17 个 scanner 默认 code 必须 advisory·绝不进 HARD_GATE_CODES"""
    import audit_hub
    hard_gates = set(audit_hub.HARD_GATE_CODES)
    for name, code, filename in SHADOW_SCANNER_REGISTRY:
        assert code not in hard_gates, (
            f"{name} 默认 code {code} 进入 HARD_GATE_CODES·违反北极星⑤顾问制(全 advisory)")


def test_audit_hub_shadow_scanner_count_at_least_17_new():
    """[G2 P2] tasks.extend 中新增 17 个 scanner·使用标记注释守卫"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    # 起止标记
    assert "[G2 P2 2026-06-22] 17 SHADOW_SCANNERS 接齐" in src, "G2 P2 起始锚未植入"
    assert "17 SHADOW_SCANNERS 接齐 END" in src, "G2 P2 终止锚未植入"


def test_audit_hub_uses_parse_advisories_for_cross_cluster_scanners():
    """writer_growth_dashboard / antagonist_valence_trajectory / vital_relations_probe
    必须用 _parse_advisories_scanner 而非 _parse_violations_scanner(它们顶层不出 violations[])"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    # 提取 [G2 P2 ... END] 段
    start = src.index("[G2 P2 2026-06-22] 17 SHADOW_SCANNERS 接齐")
    end = src.index("17 SHADOW_SCANNERS 接齐 END")
    segment = src[start:end]
    for scanner_name in ("writer_growth_dashboard", "antagonist_valence_trajectory", "vital_relations_probe"):
        # 找该 scanner 所在的 tuple 段
        idx = segment.find(f'("{scanner_name}"')
        assert idx >= 0, f"段中缺 {scanner_name}"
        # 取该 scanner 接下来 ~600 字符段（足够覆盖 tuple body）
        tuple_body = segment[idx:idx + 600]
        assert "_parse_advisories_scanner" in tuple_body, (
            f"{scanner_name} 必须用 _parse_advisories_scanner(其输出无 violations[])")
