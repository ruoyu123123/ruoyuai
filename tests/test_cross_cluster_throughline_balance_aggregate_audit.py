#!/usr/bin/env python3
"""cross_cluster_throughline_balance_aggregate.py 审计回归测试

钉死 triage L185 修复点（severity=low · relax-only · 与姊妹检测 L163/L171 对齐）：
  PER_CHAPTER_COVERAGE_LOW finding 的小样本护栏 `total >= 5 and ...`。

修前缺陷：L185 `if len(chs_with_lt2) >= total * 0.4:` 无任何 total 下限——
  cluster 模式 / 新书首个 save-state 窗口常只 1-4 章，total<5 时该 advisory
  对统计无意义的小样本稳定误报（total=1 时 `1 >= 0.4` 恒 True）；且 total=0
  时 `total*0.4==0`、`len([])>=0` 恒 True 还会在后续 `.../total` 处 ZeroDivision。
修后：与 THROUGHLINE_MARGINALIZED(L163)/OS_DOMINANT(L171) 一致带 `and total >= 5`。

测试经磁盘路径（非 cluster 模式：子进程不设 CLUSTER_MODE → 走逐章磁盘逻辑）
端到端驱动 scanner，造『每章只推进 1 条 throughline』的 changes.json，使
chs_with_lt2==全部章；按章数切换 total<5 / total>=5 验证 finding 是否触发。

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照 test_cross_cluster_contract）。
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
_SCANNER = _SCRIPTS / "cross_cluster_throughline_balance_aggregate.py"

# import 修复点所在模块（钉住模块可导入 + 直接单测纯函数）
sys.path.insert(0, str(_SCRIPTS))
import cross_cluster_throughline_balance_aggregate as tb  # noqa: E402

# Windows 下子进程管道默认 GBK——强制 UTF-8（同 test_cross_cluster_contract 范式）。
# 显式不带 CLUSTER_MODE → scanner 走磁盘分支（is_cluster_mode() env 驱动）。
_ENV = {k: v for k, v in os.environ.items() if k not in ("CLUSTER_MODE", "CLUSTER_ID")}
_ENV["PYTHONIOENCODING"] = "utf-8"


def _make_project(n_chapters: int, active_throughlines: int = 1) -> Path:
    """造临时项目：n_chapters 章，每章 throughline_progress 只有 active_throughlines
    条真推进（其余默认缺省=no_progress→不计），使每章 active_count<2、
    chs_with_lt2 == 全部章。返回 project_root。"""
    tmp = Path(tempfile.mkdtemp(prefix="tb_audit_"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    proj = tmp / "proj"
    (proj / "_数据库").mkdir(parents=True)
    active = ["OS", "MC", "IC", "RS"][:active_throughlines]
    for ch in range(1, n_chapters + 1):
        ch_dir = proj / "章节" / f"第{ch:03d}章"
        ch_dir.mkdir(parents=True)
        tp = {t: f"{t} 本章有实质推进的剧情" for t in active}
        (ch_dir / f"第{ch:03d}章_changes.json").write_text(
            json.dumps({"factual": {"throughline_progress": tp}}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def _run_scanner(proj: Path, last_n: int = 10):
    """跑 scanner 子进程，返回 (returncode, findings_list)。报告在 sys.exit 前已落盘。"""
    r = subprocess.run(
        [sys.executable, str(_SCANNER), str(proj), "--last-n", str(last_n)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=120,
    )
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-800:]}"
    scan_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(scan_dir.glob("throughline_balance_*.json"))
    assert reports, f"无报告落盘（stdout={r.stdout[-300:]} stderr={r.stderr[-300:]})"
    obj = json.loads(reports[-1].read_text(encoding="utf-8"))
    return r.returncode, obj.get("findings", [])


def _codes(findings):
    return {f.get("code") for f in findings}


# ============ 修复点：total<5 小样本不再误报 PER_CHAPTER_COVERAGE_LOW ============
def test_small_window_no_per_chapter_coverage_low():
    """🔴 核心回归：3 章（total=3<5）且每章只推 1 条 throughline →
    修前 `3 >= 3*0.4(=1.2)` 恒触发 PER_CHAPTER_COVERAGE_LOW；
    修后 `total>=5 and ...` 短路 → 该码不出现。"""
    proj = _make_project(3, active_throughlines=1)
    rc, findings = _run_scanner(proj)
    assert "PER_CHAPTER_COVERAGE_LOW" not in _codes(findings), \
        f"total=3 小样本不应报 PER_CHAPTER_COVERAGE_LOW，实得 findings={findings}"


def test_window_of_one_no_per_chapter_coverage_low():
    """边界：total=1（新书首个 cluster 常态）→ 修前 `1 >= 0.4` 恒 True、还会在
    `.../total` 处除 0；修后 `total>=5` 短路彻底跳过该分支（既不误报也不除零崩溃）。"""
    proj = _make_project(1, active_throughlines=1)
    rc, findings = _run_scanner(proj)
    assert "PER_CHAPTER_COVERAGE_LOW" not in _codes(findings), \
        f"total=1 不应报 PER_CHAPTER_COVERAGE_LOW，实得 {findings}"


def test_window_of_four_no_per_chapter_coverage_low():
    """边界：total=4（仍 <5，cluster 早期实测可达）→ 修后不报。"""
    proj = _make_project(4, active_throughlines=1)
    rc, findings = _run_scanner(proj)
    assert "PER_CHAPTER_COVERAGE_LOW" not in _codes(findings), \
        f"total=4 不应报 PER_CHAPTER_COVERAGE_LOW，实得 {findings}"


# ============ 反向：total>=5 仍正常触发（护栏只 relax 小样本，不杀真信号）============
def test_large_window_still_fires_per_chapter_coverage_low():
    """🔴 不可回归：6 章（total=6>=5）且每章只推 1 条 → `6 >= 6*0.4(=2.4)` 仍触发，
    证明 `total>=5 and` 护栏没误杀合法样本下的真 advisory。"""
    proj = _make_project(6, active_throughlines=1)
    rc, findings = _run_scanner(proj)
    assert "PER_CHAPTER_COVERAGE_LOW" in _codes(findings), \
        f"total=6 充足样本应仍报 PER_CHAPTER_COVERAGE_LOW，实得 {findings}"
    # 有 finding → 退出码契约 exit 非 0（此 fixture 同时触发 THROUGHLINE_DORMANT
    # warning（MC/IC/RS 连续 6 章 no_progress）→ summary['warning']>0 → exit 2，
    # 故只断言 rc!=0；PER_CHAPTER_COVERAGE_LOW 本身是 advisory）。
    assert rc != 0, f"有 finding 应 exit 非 0，实得 {rc}"


def test_exactly_five_boundary_fires():
    """边界等号：total=5（护栏阈值下沿）→ `5 >= 5*0.4(=2.0)` 触发（>= 5 含等号）。"""
    proj = _make_project(5, active_throughlines=1)
    rc, findings = _run_scanner(proj)
    assert "PER_CHAPTER_COVERAGE_LOW" in _codes(findings), \
        f"total=5（阈值等号）应触发，实得 {findings}"


# ============ 充足样本+每章覆盖≥2：本检测不该误报（确认护栏没把正常的也压住）====
def test_large_window_healthy_coverage_no_finding():
    """6 章每章推满 4 条 throughline（active_count=4>=2）→ chs_with_lt2 为空 →
    即便 total>=5 也不报，确认护栏不改健康路径语义。"""
    proj = _make_project(6, active_throughlines=4)
    rc, findings = _run_scanner(proj)
    assert "PER_CHAPTER_COVERAGE_LOW" not in _codes(findings), \
        f"每章覆盖≥2 不应报 coverage_low，实得 {findings}"


# ============ 模块级：源码确实带护栏（防回滚的源面 sentinel）============
def test_source_has_total_guard():
    """读 scanner 源码确认 PER_CHAPTER_COVERAGE_LOW 触发行带 `total >= 5` 护栏
    （防有人改回无护栏版；与 L163/L171 姊妹检测保持一致）。"""
    src = _SCANNER.read_text(encoding="utf-8")
    assert "total >= 5 and len(chs_with_lt2) >= total * 0.4" in src, \
        "L185 触发行缺 `total >= 5 and` 护栏（与姊妹检测 L163/L171 不一致）"


# ============ 钉住 _has_progress 语义（fixture 依赖它把 1 条算 active）============
def test_has_progress_semantics():
    """fixture 靠 _has_progress：非哨兵字符串=推进、缺省 no_progress=不推进——
    这是『每章只 1 条 active』成立的前提，顺手钉住。"""
    assert tb._has_progress("OS 本章有实质推进的剧情") is True
    assert tb._has_progress("no_progress") is False
    assert tb._has_progress("") is False
    assert tb._has_progress(None) is False


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
    print(f"\n{'ALL OK' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
