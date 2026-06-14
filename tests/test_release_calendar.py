#!/usr/bin/env python3
"""release_calendar.py 测试（发布节奏·纯算术 + 扫章节·零依赖零联网）。"""
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import release_calendar as rc  # noqa: E402


def test_schedule_safe():
    """囤稿 ≥7 天 → safe。"""
    s = rc.compute_release_schedule(total_chars=60000, daily_char_target=6000)
    assert s["buffer_chars"] == 60000
    assert s["days_of_buffer"] == 10.0
    assert s["risk_level"] == "safe"


def test_schedule_warning():
    """3-7 天 → warning。"""
    s = rc.compute_release_schedule(total_chars=30000, daily_char_target=6000)
    assert s["days_of_buffer"] == 5.0
    assert s["risk_level"] == "warning"


def test_schedule_critical():
    """<3 天 → critical + 预警 advice。"""
    s = rc.compute_release_schedule(total_chars=6000, daily_char_target=6000)
    assert s["days_of_buffer"] == 1.0
    assert s["risk_level"] == "critical"
    assert "⚠️" in s["advice"]


def test_schedule_published_reduces_buffer():
    """已发布字数从囤稿扣除。"""
    s = rc.compute_release_schedule(total_chars=60000, daily_char_target=6000,
                                    published_chars=42000)
    assert s["buffer_chars"] == 18000          # 60000 - 42000
    assert s["days_of_buffer"] == 3.0
    assert s["risk_level"] == "warning"


def test_schedule_published_over_total_clamps_zero():
    s = rc.compute_release_schedule(total_chars=10000, daily_char_target=6000,
                                    published_chars=99999)
    assert s["buffer_chars"] == 0
    assert s["days_of_buffer"] == 0.0
    assert s["risk_level"] == "critical"


def test_schedule_zero_daily_target_no_crash():
    """日更目标 0/负 → clamp 1（不除零崩）。"""
    s = rc.compute_release_schedule(total_chars=6000, daily_char_target=0)
    assert s["daily_char_target"] == 1
    assert s["days_of_buffer"] == 6000.0       # 6000/1


def test_scan_project_chars_counts_cjk():
    """扫 章节/第N章/第N章.txt 算 CJK 字数。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i in (1, 2):
            d = root / "章节" / f"第{i:03d}章"
            d.mkdir(parents=True)
            (d / f"第{i:03d}章.txt").write_text("中文一千字" * 100, encoding="utf-8")
        count, total = rc.scan_project_chars(root)
        assert count == 2
        assert total == 1000                    # "中文一千字"=5 CJK ×100 ×2 章


def test_scan_project_chars_no_chapters():
    with tempfile.TemporaryDirectory() as td:
        assert rc.scan_project_chars(Path(td)) == (0, 0)


def test_scan_ignores_non_chapter_dirs():
    """非「第N章」目录不计入。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "章节" / "草稿").mkdir(parents=True)
        (root / "章节" / "草稿" / "x.txt").write_text("中文", encoding="utf-8")
        d = root / "章节" / "第001章"
        d.mkdir(parents=True)
        (d / "第001章.txt").write_text("正文内容", encoding="utf-8")   # 4 CJK
        count, total = rc.scan_project_chars(root)
        assert count == 1                        # 只第001章
        assert total == 4


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
