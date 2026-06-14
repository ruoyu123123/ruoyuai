#!/usr/bin/env python3
"""monetization_advisor.py 测试（变现参谋·纯算术·区间+confidence+disclaimer·零依赖零联网）。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import monetization_advisor as ma  # noqa: E402


def test_patreon_interval():
    r = ma.estimate_patreon(1000)
    assert r["low"] == 150.0          # 1000 × 0.03 × 5
    assert r["high"] == 650.0         # 1000 × 0.13 × 5
    assert r["confidence"] == "high"
    assert r["unit"] == "USD/月"
    assert "disclaimer" in r


def test_patreon_zero():
    r = ma.estimate_patreon(0)
    assert r["low"] == 0.0 and r["high"] == 0.0


def test_ku_kenp():
    r = ma.estimate_ku(100000)
    assert r["low"] == 430.0          # 100000 × 0.0043
    assert r["confidence"] == "high"
    assert "独家" in r["note"]         # KU 独家合规框提示


def test_qidian_low_confidence():
    r = ma.estimate_qidian(1000)
    assert r["confidence"] == "low"   # 千订高方差
    assert r["high"] > r["low"]       # 区间
    assert "粗估" in r["unit"]


def test_breakeven_positive():
    r = ma.breakeven(monthly_token_cost=80, monthly_floor_income=300)
    assert r["net"] == 220.0
    assert r["breakeven"] is True
    assert "已覆盖" in r["note"]


def test_breakeven_negative():
    r = ma.breakeven(monthly_token_cost=300, monthly_floor_income=80)
    assert r["net"] == -220.0
    assert r["breakeven"] is False
    assert "未覆盖" in r["note"]


def test_breakeven_clamps_negative_input():
    r = ma.breakeven(monthly_token_cost=-50, monthly_floor_income=-10)
    assert r["monthly_token_cost"] == 0.0
    assert r["monthly_floor_income"] == 0.0


def test_licensing_advisor_has_legal_disclaimer():
    r = ma.licensing_advisor()
    assert len(r["comparison"]) == 2          # 分成 vs 买断
    assert "法律" in r["disclaimer"]           # 法律免责硬前置


def test_all_estimates_have_disclaimer():
    """北极星：所有平台估算必带 disclaimer「以官方合同为准」（防误当承诺·守禁止我以为）。"""
    for r in (ma.estimate_patreon(100), ma.estimate_ku(1000), ma.estimate_qidian(100)):
        assert r["disclaimer"]
        assert "官方" in r["disclaimer"]


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
