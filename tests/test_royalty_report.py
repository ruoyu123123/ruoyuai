#!/usr/bin/env python3
"""royalty_report.py 测试（财务对账·纯确定性聚合·真临时 CSV·零依赖零联网）。"""
import io
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import royalty_report as rr  # noqa: E402


def _write_csv(td, content):
    p = Path(td) / "royalty.csv"
    p.write_text(content, encoding="utf-8")
    return p


def test_aggregate_by_currency_separates():
    """多币种不混加（USD/CNY 分组·避免汇率混算）。"""
    rows = [
        {"date": "2026-06-01", "platform": "起点", "book": "A", "amount": 100.0, "currency": "CNY"},
        {"date": "2026-06-01", "platform": "patreon", "book": "A", "amount": 50.0, "currency": "USD"},
    ]
    r = rr.aggregate_royalties(rows)
    assert r["by_currency"]["CNY"]["total"] == 100.0
    assert r["by_currency"]["USD"]["total"] == 50.0
    assert r["row_count"] == 2


def test_aggregate_by_month_platform_book():
    """按月/平台/书聚合。"""
    rows = [
        {"date": "2026-06-01", "platform": "起点", "book": "A", "amount": 100.0, "currency": "CNY"},
        {"date": "2026-06-15", "platform": "起点", "book": "A", "amount": 200.0, "currency": "CNY"},
        {"date": "2026-07-01", "platform": "番茄", "book": "B", "amount": 50.0, "currency": "CNY"},
    ]
    c = rr.aggregate_royalties(rows)["by_currency"]["CNY"]
    assert c["total"] == 350.0
    assert c["by_month"]["2026-06"] == 300.0
    assert c["by_month"]["2026-07"] == 50.0
    assert c["by_platform"]["起点"] == 300.0
    assert c["by_book"]["A"] == 300.0 and c["by_book"]["B"] == 50.0


def test_parse_csv_basic():
    """解析通用 CSV（date,platform,book,amount,currency）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write_csv(td, "date,platform,book,amount,currency\n"
                           "2026-06-01,起点,某书,1234.56,CNY\n"
                           "2026-06-01,patreon,某书,89.00,USD\n")
        rows = rr.parse_csv(p)
        assert len(rows) == 2
        assert rows[0]["amount"] == 1234.56
        assert rows[1]["currency"] == "USD"


def test_parse_csv_skips_bad_rows():
    """金额非数行跳过（容错·部分坏行不毁整对账）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write_csv(td, "date,platform,book,amount,currency\n"
                           "2026-06-01,起点,A,100,CNY\n"
                           "2026-06-02,起点,B,坏数据,CNY\n"
                           "2026-06-03,起点,C,200,CNY\n")
        rows = rr.parse_csv(p)
        assert len(rows) == 2          # 坏行跳过
        assert rows[0]["book"] == "A" and rows[1]["book"] == "C"


def test_parse_csv_amount_with_comma():
    """千分位逗号金额（"1,234.56"）解析。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write_csv(td, "date,platform,book,amount,currency\n"
                           '2026-06-01,起点,A,"1,234.56",CNY\n')
        rows = rr.parse_csv(p)
        assert rows[0]["amount"] == 1234.56


def test_parse_csv_missing_file():
    assert rr.parse_csv(Path(tempfile.gettempdir()) / "__no_such_royalty__.csv") == []


def test_main_exit_2_no_rows():
    """无有效行（仅表头）→ main 返回 2（降级码·sys.exit 在 __main__）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write_csv(td, "date,platform,book,amount,currency\n")
        with redirect_stderr(io.StringIO()):
            assert rr.main([str(p)]) == 2


def test_main_exit_0_success():
    """有效行 → main 返回 0 + 输出 JSON 报表。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write_csv(td, "date,platform,book,amount,currency\n"
                           "2026-06-01,起点,A,100,CNY\n")
        import io as _io
        from contextlib import redirect_stdout
        buf = _io.StringIO()
        with redirect_stdout(buf):
            assert rr.main([str(p)]) == 0
        assert "by_currency" in buf.getvalue()


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
