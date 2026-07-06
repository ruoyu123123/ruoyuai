"""🔴 2026-07-05 休眠 scanner 接线回归锁（W1 audit_hub + scanner registry）。

病史：7 个已实现且有独立单测的 scanner（frisson_lead_window / butler_yearning_4layer /
failure_segment_prose_density / soundscape_trinity / premature_reader_reveal /
trajectory_moral_slope / check_acr_frustration_consistency）写完后从未被 audit_hub
cluster-mode tasks 调度（frisson 甚至 registry 已注册但不调）——「纸面承诺 ≠ 运行事实」
的孤儿契约债。本文件钉死：

  ① audit_hub 源码真调每个 scanner（脚本名 + 聚合 code 都在 tasks 里）。
  ② scanner_registry.json 有对应 entry 且 script 路径真实存在（元数据对账源）。
  ③ 全部 advisory：所有 emit code 不在 audit_hub.HARD_GATE_CODES（北极星⑤顾问非法官）。
  ④ _parse_multi_code_violations_scanner 按 violation 自带 code 分组（一 scanner 多 code
     的 frisson/butler/soundscape 保持真实 code·数据飞轮 CODE_TO_MODEL 按真实 code 注册）。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import audit_hub  # noqa: E402

_AUDIT_SRC = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
_REGISTRY = json.loads((_SCRIPTS / "scanner_registry.json").read_text(encoding="utf-8"))

# (registry key, 脚本文件名, audit_hub 里的聚合/默认 code, 全部 emit code)
_WIRED = [
    ("character_identity_anchor_scanner", "character_identity_anchor_scanner.py",
     "CHARACTER_IDENTITY_ANCHOR_DRIFT", ["CHARACTER_IDENTITY_ANCHOR_DRIFT"]),
    ("frisson_lead_window", "frisson_lead_window_scanner.py",
     "FRISSON_LEAD_FLAT",
     ["FRISSON_LEAD_FLAT", "FRISSON_CLIMAX_OVERLOAD", "FRISSON_NO_CLIMAX_FOUND"]),
    ("butler_yearning_4layer_scanner", "butler_yearning_4layer_scanner.py",
     "SCENE_YEARNING_ABSENT", ["SCENE_YEARNING_ABSENT", "YEARNING_LAYER_MONOTONE"]),
    ("failure_segment_prose_density_scanner", "failure_segment_prose_density_scanner.py",
     "FAILURE_SEGMENT_DENSITY_GAP", ["FAILURE_SEGMENT_DENSITY_GAP"]),
    ("soundscape_trinity_scanner", "soundscape_trinity_scanner.py",
     "SOUNDSCAPE_THIN", ["SOUNDSCAPE_THIN", "SOUNDSCAPE_MONOTONE", "SOUNDMARK_ABSENT"]),
    ("premature_reader_reveal_scanner", "premature_reader_reveal_scanner.py",
     "PREMATURE_READER_REVEAL", ["PREMATURE_READER_REVEAL"]),
    ("trajectory_moral_slope_scanner", "trajectory_moral_slope_scanner.py",
     "AMBIGUOUS_FLAT_TRAJECTORY", ["AMBIGUOUS_FLAT_TRAJECTORY"]),
    ("check_acr_frustration_consistency", "check_acr_frustration_consistency.py",
     "ACR_FRUSTRATION_MISMATCH", ["ACR_FRUSTRATION_MISMATCH"]),
]


def test_audit_hub_wires_every_scanner():
    """① 源码断言：脚本名 + 聚合 code 都出现在 audit_hub（同 test_agenda_drift 模式）。"""
    missing = []
    for _key, script, agg_code, _codes in _WIRED:
        if script not in _AUDIT_SRC:
            missing.append(script)
        if agg_code not in _AUDIT_SRC:
            missing.append(agg_code)
    assert not missing, f"audit_hub 未接线（孤儿回归）: {missing}"


def test_registry_entry_exists_and_script_path_real():
    """② registry 元数据对账：entry 存在 · layer=cluster · script 路径真实存在。"""
    scanners = _REGISTRY.get("scanners", {})
    for key, script, _agg, codes in _WIRED:
        entry = scanners.get(key)
        assert entry is not None, f"scanner_registry.json 缺 entry: {key}"
        assert entry.get("layer") == "cluster", f"{key} layer 应为 cluster"
        # script 字段可能带说明后缀（如 " + build_manifest..."），取首个 token 校验
        script_field = str(entry.get("script", "")).split(" ")[0]
        assert script_field == script, f"{key} script 字段={script_field} 应为 {script}"
        assert (_SCRIPTS / script).exists(), f"{key} 脚本不存在: {script}"
        emitted = entry.get("issues_emitted", [])
        for c in codes:
            assert c in emitted, f"{key} registry issues_emitted 缺 {c}"


def test_all_codes_advisory_never_hard_gate():
    """③ 北极星⑤：全部 emit code 不在 HARD_GATE_CODES·_gate_level_for 判 advisory。"""
    for _key, _script, _agg, codes in _WIRED:
        for c in codes:
            assert c not in audit_hub.HARD_GATE_CODES, f"{c} 不得进 hard_gate（北极星⑤）"
            assert audit_hub._gate_level_for(c, "error") == "advisory", c


def test_multi_code_parser_groups_by_violation_code():
    """④ _parse_multi_code_violations_scanner：per-violation code 分组·各组一条聚合 issue·
    默认 code 兜底·清单外顶层 hard_gate 不升格。"""
    report = {
        "scanner": "soundscape_trinity_scanner",
        "gate_level": "advisory",
        "verdict": "FAIL_MINOR",
        "violations": [
            {"code": "SOUNDSCAPE_THIN", "severity": "minor", "message": "声学维度极弱"},
            {"code": "SOUNDSCAPE_MONOTONE", "severity": "minor", "message": "单声道"},
            {"severity": "minor", "message": "无 code 的兜底项"},
        ],
    }
    issues = audit_hub._parse_multi_code_violations_scanner(
        json.dumps(report, ensure_ascii=False),
        "soundscape_trinity_scanner", "SOUNDSCAPE_THIN", "风格")
    codes = sorted(i["code"] for i in issues)
    # 无 code 项落默认 SOUNDSCAPE_THIN → 与显式 THIN 同组
    assert codes == ["SOUNDSCAPE_MONOTONE", "SOUNDSCAPE_THIN"], codes
    assert all(i["gate_level"] == "advisory" for i in issues)
    assert all(i["severity"] == "warning" for i in issues)  # 无 major → warning


def test_multi_code_parser_top_level_hard_gate_not_upgraded_outside_list():
    """④ 双闸：scanner 顶层自报 hard_gate 但 code 不在 HARD_GATE_CODES → 仍 advisory。"""
    report = {"scanner": "x", "gate_level": "hard_gate", "verdict": "FAIL",
              "violations": [{"code": "FRISSON_LEAD_FLAT", "severity": "major"}]}
    issues = audit_hub._parse_multi_code_violations_scanner(
        json.dumps(report), "x", "FRISSON_LEAD_FLAT", "节奏")
    assert issues[0]["gate_level"] == "advisory"
    assert issues[0]["severity"] == "error"  # major → error


def test_multi_code_parser_empty_pass_no_issue():
    """④ PASS（violations 空）→ 不产 issue。"""
    report = {"scanner": "x", "gate_level": "advisory", "verdict": "PASS", "violations": []}
    assert audit_hub._parse_multi_code_violations_scanner(
        json.dumps(report), "x", "Y", "风格") == []
