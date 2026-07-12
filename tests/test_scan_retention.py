"""scan_retention.py 回归测试 — 跨章扫描报告老化清理（纯文件系统逻辑·零 LLM·零联网）。

脚本只暴露 main()（argparse + sys.exit），核心确定性逻辑全在其中：
  · 按文件名前缀分组（正则 ^([a-z_]+?)_\\d{8}_\\d{6}\\.json$）
  · 每组按 st_mtime 降序排，保留最新 --keep 份，删除其余
  · 扫描目录不存在 → [SKIP] 退出 0
本测试驱动真实 main()（通过 sys.argv + 捕获 SystemExit），断言真实文件系统副作用。
绝不 mock 被测逻辑本身。
"""
import os
import sys
import tempfile
import time
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import scan_retention as mod  # noqa: E402


def _mk_scan_dir(tmp: Path) -> Path:
    """造 <project>/_数据库/.cross_cluster_scan/ 空目录，返回该 scan 目录。"""
    scan_dir = tmp / "_数据库" / ".cross_cluster_scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    return scan_dir


def _touch(scan_dir: Path, name: str, mtime: float | None = None) -> Path:
    """造一份扫描 json 文件，可选指定 mtime（用于确定性排序）。"""
    p = scan_dir / name
    p.write_text("{}", encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _run_main(project: Path, keep: int | None = None):
    """以真实 CLI 入口驱动 main()，捕获 sys.exit 码。返回 exit code（int）。"""
    argv = ["scan_retention.py", str(project)]
    if keep is not None:
        argv += ["--keep", str(keep)]
    old_argv = sys.argv
    sys.argv = argv
    try:
        mod.main()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
        return int(code)
    finally:
        sys.argv = old_argv
    return 0  # main 总会 sys.exit，但兜底


def test_keeps_newest_n_deletes_rest():
    """happy path：同一前缀 8 份 → keep=5 保留最新 5 删旧 3。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        scan_dir = _mk_scan_dir(tmp)
        base = 1_000_000_000.0
        files = []
        for i in range(8):
            # 文件名时间戳与 mtime 都递增，最新 = 最后造的
            name = f"narrative_scan_2026010{1}_0000{i:02d}.json"
            files.append(_touch(scan_dir, name, mtime=base + i))
        code = _run_main(tmp, keep=5)
        assert code == 0
        remaining = sorted(p.name for p in scan_dir.glob("*.json"))
        assert len(remaining) == 5, f"应保留 5 份，实际 {len(remaining)}: {remaining}"
        # 删掉的应是 mtime 最小的 3 份（i=0,1,2）
        for p in files[:3]:
            assert not p.exists(), f"最旧的 {p.name} 应被删除"
        for p in files[3:]:
            assert p.exists(), f"最新的 {p.name} 应保留"


def test_keep_more_than_present_deletes_nothing():
    """边界：keep 大于现有份数 → 一份不删。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        scan_dir = _mk_scan_dir(tmp)
        for i in range(3):
            _touch(scan_dir, f"plot_scan_20260101_0000{i:02d}.json", mtime=1_000.0 + i)
        code = _run_main(tmp, keep=5)
        assert code == 0
        assert len(list(scan_dir.glob("*.json"))) == 3  # 3 < 5，全保留


def test_groups_independent_per_prefix():
    """分组独立：两个不同前缀各自独立应用 keep，互不影响。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        scan_dir = _mk_scan_dir(tmp)
        a_files = [_touch(scan_dir, f"narrative_scan_20260101_0000{i:02d}.json", mtime=100.0 + i)
                   for i in range(4)]
        b_files = [_touch(scan_dir, f"plot_scan_20260101_0000{i:02d}.json", mtime=200.0 + i)
                   for i in range(2)]
        code = _run_main(tmp, keep=2)
        assert code == 0
        # narrative_scan 组 4 份 → 保留最新 2 删 2
        a_remaining = sorted(p.name for p in scan_dir.glob("narrative_scan_*.json"))
        assert len(a_remaining) == 2
        assert not a_files[0].exists() and not a_files[1].exists()
        assert a_files[2].exists() and a_files[3].exists()
        # plot_scan 组 2 份 ≤ keep=2 → 全保留（不被 narrative 组的删除影响）
        assert all(p.exists() for p in b_files)


def test_default_keep_is_5():
    """默认 --keep=5（不传参）：7 份 → 保留 5 删 2。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        scan_dir = _mk_scan_dir(tmp)
        files = [_touch(scan_dir, f"voice_scan_20260101_0000{i:02d}.json", mtime=500.0 + i)
                 for i in range(7)]
        code = _run_main(tmp, keep=None)  # 走 argparse 默认值 5
        assert code == 0
        assert len(list(scan_dir.glob("*.json"))) == 5
        assert not files[0].exists() and not files[1].exists()  # 最旧 2 删
        assert files[6].exists()  # 最新保留


def test_non_matching_names_ignored():
    """边界：不符合命名正则的文件（无时间戳/大写/非 json）一律不被分组也不被删。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        scan_dir = _mk_scan_dir(tmp)
        # 符合正则的同前缀 4 份（keep=1 应删 3）
        matched = [_touch(scan_dir, f"scan_20260101_0000{i:02d}.json", mtime=10.0 + i)
                   for i in range(4)]
        # 不符合正则的：缺秒段 / 数字前缀 / 非 json 扩展 / 多段
        # 注意：不用大写前缀做反例 —— Windows 文件系统大小写不敏感，
        #   "Scan_..." 与 "scan_..." 会指向同一文件，污染测试（平台依赖）。
        odd = [
            _touch(scan_dir, "scan_20260101.json", mtime=999.0),          # 缺 _HHMMSS
            _touch(scan_dir, "v2scan_20260101_000000.json", mtime=999.0), # 前缀含数字 → [a-z_]+ 不命中
            _touch(scan_dir, "report.txt", mtime=999.0),                  # 非 json（glob *.json 不命中）
            _touch(scan_dir, "scan_2026_0101_000000.json", mtime=999.0),  # 日期段非 8 位连续
        ]
        code = _run_main(tmp, keep=1)
        assert code == 0
        # matched 组 keep=1 → 只剩最新 1
        m_remaining = [p for p in matched if p.exists()]
        assert len(m_remaining) == 1 and m_remaining[0] == matched[3]
        # 所有不匹配的文件原封不动
        for p in odd:
            assert p.exists(), f"未匹配命名的 {p.name} 不应被删除"


def test_missing_scan_dir_skips_cleanly():
    """边界：扫描目录不存在 → [SKIP] 退出 0，不抛异常。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)  # 没建 _数据库/.cross_cluster_scan
        code = _run_main(tmp, keep=5)
        assert code == 0  # SKIP 分支也是 exit 0


def test_empty_scan_dir_no_crash():
    """边界：扫描目录存在但为空 → 不删不崩，退出 0。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_scan_dir(tmp)  # 空目录
        code = _run_main(tmp, keep=3)
        assert code == 0


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[scan_retention] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
