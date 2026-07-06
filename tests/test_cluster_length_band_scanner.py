# -*- coding: utf-8 -*-
"""cluster_length_band_scanner · cluster 草稿长度带 advisory 体检 · 2026-07-06 P2 移植

借鉴 LongWriter「长输出长度评估」（research/open_source_writing_systems.md）。
兑现「纯 freestyle 后短稿风险由 step3 质检下游承接」架构承诺——expand 软下限
已整体清除，检测端此前无人查 cluster 长度。确定性零 LLM · advisory ·
绝不 hard_gate · 结果绝不回流 writer/manifest（北极星⑤）· env
CLUSTER_LENGTH_BAND_MODE 默认 shadow。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT / "core" / "ml" / "flywheel"))

import cluster_length_band_scanner as mod  # noqa: E402
import audit_hub  # noqa: E402
from code_to_model_table import (  # noqa: E402
    EXCLUDED_FROM_FLYWHEEL,
    resolve_model_for_code,
)

_MODE_ENV = "CLUSTER_LENGTH_BAND_MODE"
_OVERRIDE_ENV = "CLUSTER_LENGTH_BAND_OVERRIDE"


def _set_env(key, val):
    if val is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = val


def _write_draft(cjk_count: int) -> Path:
    """产一个含指定纯 CJK 字数的草稿（掺标点/空白验证 count_cjk 口径不数它们）。"""
    d = Path(tempfile.mkdtemp())
    p = d / "cluster_001_draft.txt"
    body = ("夜" * 50 + "，\n\n") * (cjk_count // 50)
    body += "夜" * (cjk_count % 50)
    p.write_text(body, encoding="utf-8")
    return p


def _run(mode, cjk_count, override=None):
    bak_mode = os.environ.get(_MODE_ENV)
    bak_override = os.environ.get(_OVERRIDE_ENV)
    try:
        _set_env(_MODE_ENV, mode)
        _set_env(_OVERRIDE_ENV, override)
        return mod.scan(_write_draft(cjk_count))
    finally:
        _set_env(_MODE_ENV, bak_mode)
        _set_env(_OVERRIDE_ENV, bak_override)


def test_default_mode_is_shadow():
    bak = os.environ.get(_MODE_ENV)
    try:
        _set_env(_MODE_ENV, None)
        assert mod._mode() == "shadow"
    finally:
        _set_env(_MODE_ENV, bak)


def test_off_returns_skeleton():
    out = _run("off", 100)
    assert out["mode"] == "off"
    assert out["violations"] == []
    assert out["verdict"] == "PASS"
    assert "cjk_count" not in out  # off 连数都不数


def test_in_band_pass_active():
    """带内（默认 [12000,25000]）→ PASS · 零 violation · cjk 口径只数 CJK。"""
    out = _run("active", 15000)
    assert out["cjk_count"] == 15000
    assert out["violations"] == []
    assert out["verdict"] == "PASS"
    assert out["warning"] is None
    assert out["gate_level"] == "advisory"


def test_under_band_active():
    """低于下限 → CLUSTER_LENGTH_UNDER_BAND · FAIL_MINOR · advisory。"""
    out = _run("active", 5000)
    assert out["verdict"] == "FAIL_MINOR"
    assert out["warning"]
    assert len(out["violations"]) == 1
    v = out["violations"][0]
    assert v["code"] == "CLUSTER_LENGTH_UNDER_BAND"
    assert v["severity"] == "minor"
    assert v["gate_level"] == "advisory"
    assert v["cjk_count"] == 5000
    assert v["band_min"] == 12000


def test_over_band_active():
    """高于上限 → CLUSTER_LENGTH_OVER_BAND。"""
    out = _run("active", 26000)
    assert out["verdict"] == "FAIL_MINOR"
    assert len(out["violations"]) == 1
    assert out["violations"][0]["code"] == "CLUSTER_LENGTH_OVER_BAND"
    assert out["violations"][0]["band_max"] == 25000


def test_band_boundaries_inclusive():
    """带边界含端点：恰好 min / max 都 PASS（带外才报）。"""
    assert _run("active", 12000)["violations"] == []
    assert _run("active", 25000)["violations"] == []


def test_override_env_parsed():
    """CLUSTER_LENGTH_BAND_OVERRIDE="min,max" 覆盖带宽。"""
    out = _run("active", 5000, override="3000,8000")
    assert out["band_min"] == 3000
    assert out["band_max"] == 8000
    assert out["violations"] == []  # 5000 在覆盖带内
    out2 = _run("active", 2000, override="3000,8000")
    assert out2["violations"][0]["code"] == "CLUSTER_LENGTH_UNDER_BAND"


def test_override_invalid_falls_back_to_default():
    """非法 override（非数字/min>=max/单值）→ 回默认带 + note 记录。"""
    for bad in ("abc", "8000,3000", "5000", "1,2,3", "-1,100"):
        out = _run("active", 15000, override=bad)
        assert out["band_min"] == 12000, f"override={bad!r}"
        assert out["band_max"] == 25000, f"override={bad!r}"
        assert "invalid" in out.get("note", ""), f"override={bad!r}"
    assert out["violations"] == []


def test_shadow_no_violations(capsys):
    """shadow（默认）：带外只 stderr 打印 · violations 留空 · verdict PASS。"""
    out = _run("shadow", 5000)
    assert out["cjk_count"] == 5000  # 照常计量
    assert out["violations"] == []
    assert out["warning"] is None
    assert out["verdict"] == "PASS"
    err = capsys.readouterr().err
    assert "[SHADOW] cluster_length_band" in err


def test_changes_section_stripped():
    """草稿尾部 ---CHANGES--- 段不计入长度（只量正文）。"""
    bak = os.environ.get(_MODE_ENV)
    try:
        _set_env(_MODE_ENV, "active")
        p = _write_draft(13000)
        p.write_text(
            p.read_text(encoding="utf-8") + "\n---CHANGES---\n" + "填" * 20000,
            encoding="utf-8")
        out = mod.scan(p)
        assert out["cjk_count"] == 13000
        assert out["violations"] == []
    finally:
        _set_env(_MODE_ENV, bak)


def test_missing_draft_graceful():
    bak = os.environ.get(_MODE_ENV)
    try:
        _set_env(_MODE_ENV, "active")
        out = mod.scan("nonexistent_draft.txt")
        assert out["violations"] == []
        assert "read failed" in out.get("note", "")
    finally:
        _set_env(_MODE_ENV, bak)


def test_audit_hub_integrates_scanner():
    """audit_hub cluster-mode 接线源码断言（同 test_scene_receipts 模式）：
    脚本名 + 默认 code + 多码解析器都在 tasks 里。"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "cluster_length_band_scanner" in src
    assert "CLUSTER_LENGTH_UNDER_BAND" in src
    idx = src.index("cluster_length_band_scanner.py")
    # 多码 scanner 必须走 _parse_multi_code_violations_scanner（UNDER/OVER 保真实 code）
    assert "_parse_multi_code_violations_scanner" in src[idx:idx + 800]


def test_codes_never_hard_gate():
    """北极星⑤：两个 code 绝不进 HARD_GATE_CODES · _gate_level_for 恒判 advisory。"""
    for code in ("CLUSTER_LENGTH_UNDER_BAND", "CLUSTER_LENGTH_OVER_BAND"):
        assert code not in audit_hub.HARD_GATE_CODES
        assert audit_hub._gate_level_for(code, "error") == "advisory"


def test_registry_entry_reconciled():
    """scanner_registry.json 对账：entry 存在 · layer=cluster · 脚本真实存在 · 两码登记。"""
    reg = json.loads((_SCRIPTS / "scanner_registry.json").read_text(encoding="utf-8"))
    entry = reg["scanners"]["cluster_length_band_scanner"]
    assert entry["layer"] == "cluster"
    assert entry["script"] == "cluster_length_band_scanner.py"
    assert (_SCRIPTS / "cluster_length_band_scanner.py").exists()
    assert "CLUSTER_LENGTH_UNDER_BAND" in entry["issues_emitted"]
    assert "CLUSTER_LENGTH_OVER_BAND" in entry["issues_emitted"]


def test_codes_excluded_from_flywheel():
    """数据飞轮：长度是遥测非可学习正文质量信号 → 两码归 EXCLUDED_FROM_FLYWHEEL
    （显式豁免·不进 13 训练桶）。"""
    for code in ("CLUSTER_LENGTH_UNDER_BAND", "CLUSTER_LENGTH_OVER_BAND"):
        assert code in EXCLUDED_FROM_FLYWHEEL
        assert len(EXCLUDED_FROM_FLYWHEEL[code]) >= 8
        assert resolve_model_for_code(code) is None
