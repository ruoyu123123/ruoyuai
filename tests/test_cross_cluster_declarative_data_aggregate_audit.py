#!/usr/bin/env python3
"""cross_cluster_declarative_data_aggregate.py 审计回归测试

锁定 2026-06 退出码契约修复 [L6/SC-2]（triage_worth_fixing.json L263 bug）：
  旧代码两档——`warning → exit 1` / 否则 `exit 0`，从不 exit 2；与本文件
  docstring『退出码：0 健康 / 1 advisory / 2 warning』自相矛盾，且与兄弟
  aggregator（cross_cluster_will_learn / cross_cluster_offscreen 已于 2026-05-29
  改三档）分叉。后果：真 warning 码（TRIGGERED_EVENTS_EMPTY / SECRET_OVERDUE）以
  exit 1 返回 → run_cross_cluster_aggregates.py L221 把 exit 1（无 Traceback）当
  advisory『正常，不记』静默丢弃 → 永不进 [FINDINGS]，真丢检测信号。

修复后三档：warning → exit 2 / advisory → exit 1 / 健康 → exit 0。

覆盖：
  ① warning 发现（SECRET_OVERDUE 到期未揭露）→ exit 2
  ② advisory-only 发现（RELATIONSHIPS_STAGNANT 数值停滞）→ exit 1
  ③ 无任何发现（章数未达阈值）→ exit 0
  ④ 源码层守卫：三档退出码逻辑齐全（防静默回归两档）
  ⑤ 模块可 import（不崩）

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照
test_cross_cluster_contract.py）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
_SCRIPT = _SCRIPTS / "cross_cluster_declarative_data_aggregate.py"

# Windows 下子进程管道 stdout 默认 locale 编码（GBK）——强制子进程 UTF-8 输出。
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def _run(proj: Path, timeout=120):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(proj)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=timeout,
    )


def _mk_chapter_dirs(proj: Path, lo: int, hi: int):
    """造物理章节目录，使 find_chapter_dirs 把 last_ch 锚到 hi。"""
    for ch in range(lo, hi + 1):
        (proj / "章节" / f"第{ch:03d}章").mkdir(parents=True, exist_ok=True)


def _new_proj() -> tuple[Path, Path, "tempfile.TemporaryDirectory"]:
    td = tempfile.TemporaryDirectory(prefix="decl_audit_")
    proj = Path(td.name) / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    return proj, db, td


# ============ ① warning → exit 2 ============
def test_warning_finding_exits_2():
    """SECRET_OVERDUE 是 warning 码：reveal_at_cluster 已到期（cluster_001 末章 ch3
    <= last_ch）且 status 仍 hidden → 修复后必须 exit 2（旧代码错为 exit 1，被调度器
    当 advisory 静默丢弃）。"""
    proj, db, td = _new_proj()
    try:
        # 事件簇.json 提供 cluster_001 的 chapter_range 供 cluster_id_to_range 解析末章
        (db / "事件簇.json").write_text(json.dumps({
            "schema_version": "v2.cluster",
            "clusters": [{"cluster_id": "cluster_001", "title": "c1",
                          "status": "已完成", "chapter_range": [1, 3]}],
        }, ensure_ascii=False), encoding="utf-8")
        (db / "伏笔表.json").write_text(json.dumps({
            "secrets": [{"id": "s1", "reveal_at_cluster": "cluster_001",
                         "status": "hidden", "secret": "藏着的真相"}],
        }, ensure_ascii=False), encoding="utf-8")
        _mk_chapter_dirs(proj, 1, 3)  # last_ch=3 >= reveal_hi(3)
        r = _run(proj)
        assert "[WARNING]" in r.stdout and "SECRET_OVERDUE" in r.stdout, \
            f"未触发 SECRET_OVERDUE warning：\n{r.stdout[-500:]}"
        assert r.returncode == 2, \
            f"warning 发现应 exit 2（修复点），实得 {r.returncode}：\n{r.stdout[-400:]}"
    finally:
        td.cleanup()


# ============ ② advisory-only → exit 1 ============
def test_advisory_only_finding_exits_1():
    """RELATIONSHIPS_STAGNANT 是 advisory 码：关系数值连续 ≥5 章无变化、无任何 warning
    → 修复后必须 exit 1（旧代码 advisory-only 错为 exit 0，与 docstring 矛盾）。"""
    proj, db, td = _new_proj()
    try:
        (db / "关系.json").write_text(json.dumps({
            "relationships": [{"from": "甲", "to": "乙", "_last_modified_at_ch": 1}],
        }, ensure_ascii=False), encoding="utf-8")
        _mk_chapter_dirs(proj, 1, 6)  # last_ch=6, 6-1=5 >= 5 触发停滞
        r = _run(proj)
        assert "[ADVISORY]" in r.stdout and "RELATIONSHIPS_STAGNANT" in r.stdout, \
            f"未触发 RELATIONSHIPS_STAGNANT advisory：\n{r.stdout[-500:]}"
        assert "[WARNING]" not in r.stdout, \
            f"该 fixture 不应有 warning（会污染 exit 码断言）：\n{r.stdout[-500:]}"
        assert r.returncode == 1, \
            f"advisory-only 应 exit 1（修复点），实得 {r.returncode}：\n{r.stdout[-400:]}"
    finally:
        td.cleanup()


# ============ ③ 无发现 → exit 0 ============
def test_no_finding_exits_0():
    """章数未达任何阈值（last_ch=2）→ 无 finding → exit 0（健康档不受修复影响）。"""
    proj, db, td = _new_proj()
    try:
        _mk_chapter_dirs(proj, 1, 2)
        r = _run(proj)
        assert "=== 发现 0 项" in r.stdout, f"应零发现：\n{r.stdout[-400:]}"
        assert r.returncode == 0, \
            f"健康应 exit 0，实得 {r.returncode}：\n{r.stdout[-400:]}"
    finally:
        td.cleanup()


# ============ ④ 源码层三档守卫（防静默回归两档） ============
def test_source_has_three_tier_exit_codes():
    """直接审源码：必须同时存在 `sys.exit(2)`（warning 档）与 advisory 档的
    `sys.exit(1)`，且 warning 分支不再写成 exit(1)。防止未来 diff 把三档悄悄改回
    两档（这正是本次修复的 bug 形态）。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "sys.exit(2)" in src, "缺 warning 档 sys.exit(2)（疑回归两档）"
    # warning 分支必须 exit 2，不能再是 exit 1
    assert 'severity"] == "warning" for f in findings):\n        sys.exit(2)' in src, \
        "warning 分支未指向 sys.exit(2)（回归到旧的 exit 1？）"
    # advisory 档 exit 1 必须存在
    assert 'severity"] == "advisory" for f in findings):\n        sys.exit(1)' in src, \
        "缺 advisory 档 sys.exit(1)"
    # docstring 退出码契约仍声明三档
    assert "0 健康 / 1 advisory / 2 warning" in src, "docstring 退出码契约文案被改"


# ============ ⑤ 模块可 import ============
def test_module_imports():
    """模块独立 import 不崩（依赖 cluster_summary_reader / cluster_lookup 链路完好）。"""
    sys.path.insert(0, str(_SCRIPTS))
    import importlib
    mod = importlib.import_module("cross_cluster_declarative_data_aggregate")
    assert hasattr(mod, "main"), "模块缺 main()"


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
