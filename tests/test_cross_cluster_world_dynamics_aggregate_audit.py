#!/usr/bin/env python3
"""cross_cluster_world_dynamics_aggregate.py 审计修复回归测试。

覆盖 triage_worth_fixing.json 中针对本文件的修复点：

  · L103 (severity low) — scan_faction_trends 的 FACTION_NEAR_ZERO / FACTION_NEAR_MAX
    极值检查只依赖 factions_state 当前快照，与 world_ticks_log 无关；但旧代码在极值
    检查前有一道 `log = world.get("world_ticks_log",[]); if len(log)<3: return findings`
    早退守卫（本是给上方已废弃的趋势重建逻辑准备的），误 gate 了无关的快照极值检查。
    新书早期默认态没有 world_ticks_log（或不足 3 条）→ 即便某势力 power=98 / stability=3
    也被静默吞掉两条 advisory。修复 = 删除该 log 赋值与错置守卫，让极值检查无条件执行。

回归断言（核心）：world_ticks_log **缺失 / 不足 3 条** 时，处于极值的势力**仍**被上报。
同时守住合法早退分支不回归（factions_state 空 / 世界状态.json 缺 / .world_evolution 缺）。

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照 test_cross_cluster_contract）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import cross_cluster_world_dynamics_aggregate as mod  # noqa: E402


def _mk_project(td, world_obj, *, with_evo_dir=True):
    """造一个最小项目：写 世界状态.json，按需建 .world_evolution 目录。

    scan_faction_trends 在读 factions 之后、极值检查之前有一道独立守卫：
    `.world_evolution` 目录不存在 → return []（L70-72，与本 bug 无关、保留）。
    要让执行流抵达极值检查，必须先满足这道门，故默认建该目录。
    """
    proj = Path(td) / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    (db / "世界状态.json").write_text(
        json.dumps(world_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    if with_evo_dir:
        (db / ".world_evolution").mkdir()
    return proj


def _codes(findings):
    return {f.get("code") for f in findings}


# ============ 核心回归：log 缺失 / 不足 3 条时极值检查仍执行 ============
def test_extreme_reported_when_world_ticks_log_absent():
    """🔴 主回归：世界状态.json 完全没有 world_ticks_log 键（新书早期默认态），
    某势力 power=98(NEAR_MAX) + stability=3(NEAR_ZERO) → 两条 advisory 必须上报。
    修复前：log=[] → len<3 → return findings(空) → 静默吞掉。修复后：正常上报。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, {
            "factions_state": {
                "巅峰霸主": {"power": 98, "stability": 50, "wealth": 60},
                "将崩王朝": {"power": 40, "stability": 3, "wealth": 30},
            },
            # 故意不写 world_ticks_log
        })
        findings = mod.scan_faction_trends(proj)
        codes = _codes(findings)
        assert "FACTION_NEAR_MAX" in codes, f"power=98 应报 NEAR_MAX，实得 {findings}"
        assert "FACTION_NEAR_ZERO" in codes, f"stability=3 应报 NEAR_ZERO，实得 {findings}"
        # 全 advisory（北极星：顾问非门禁）
        assert all(f["severity"] == "advisory" for f in findings), findings


def test_extreme_reported_when_log_shorter_than_three():
    """world_ticks_log 只有 1-2 条（新书早期事件稀疏）也不再早退：power=2 → NEAR_ZERO。
    旧守卫 len(log)<3 会在这里 return findings 空。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, {
            "factions_state": {"残部": {"power": 2, "stability": 50, "wealth": 50}},
            "world_ticks_log": [{"ch": 1}, {"ch": 2}],  # len==2 < 3
        })
        findings = mod.scan_faction_trends(proj)
        assert "FACTION_NEAR_ZERO" in _codes(findings), \
            f"power=2 + 短 log 应报 NEAR_ZERO，实得 {findings}"


def test_extreme_reported_with_long_log_still_works():
    """log 充足时（旧守卫本就放行）极值检查照常工作 —— 确认修复未破坏原可达路径。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, {
            "factions_state": {"霸主": {"power": 100, "stability": 99, "wealth": 50}},
            "world_ticks_log": [{"ch": i} for i in range(1, 6)],  # len==5 >= 3
        })
        findings = mod.scan_faction_trends(proj)
        # power=100 与 stability=99 各触发一条 NEAR_MAX（>=95）
        near_max = [f for f in findings if f["code"] == "FACTION_NEAR_MAX"]
        assert len(near_max) == 2, f"power=100+stability=99 应报 2 条 NEAR_MAX，实得 {findings}"


def test_no_finding_for_mid_range_values():
    """中间区间(5<v<95)不触发任何极值 finding —— 守住不误报。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, {
            "factions_state": {"普通门派": {"power": 50, "stability": 60, "wealth": 40}},
        })
        assert mod.scan_faction_trends(proj) == [], "中间值不该有 finding"


# ============ 守住合法早退分支不回归 ============
def test_empty_factions_state_returns_empty():
    """factions_state 为空 dict（skeleton 新书默认态）→ 合法早退返回 []（L66 守卫保留）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, {"factions_state": {}})
        assert mod.scan_faction_trends(proj) == []


def test_missing_world_state_returns_empty():
    """世界状态.json 不存在 → 返回 []（L62 守卫保留）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        (proj / "_数据库").mkdir(parents=True)
        assert mod.scan_faction_trends(proj) == []


def test_missing_world_evolution_dir_returns_empty():
    """.world_evolution 目录不存在 → 返回 []（L71 守卫与本 bug 无关、保留）。
    即便势力处于极值，没有该目录也按设计早退（确认修复未越界改动这道无关守卫）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, {
            "factions_state": {"霸主": {"power": 99, "stability": 50, "wealth": 50}},
        }, with_evo_dir=False)
        assert mod.scan_faction_trends(proj) == []


def test_non_numeric_dimension_skipped():
    """维度值非数字(None/字符串)被 L109 isinstance 守卫跳过、不崩。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(td, {
            "factions_state": {
                "怪数据": {"power": None, "stability": "高", "wealth": 3},
            },
        })
        findings = mod.scan_faction_trends(proj)
        # 只有 wealth=3 是合法数字且 <=5 → 一条 NEAR_ZERO；None/字符串被跳过不崩
        assert _codes(findings) == {"FACTION_NEAR_ZERO"}, findings


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
