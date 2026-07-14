# -*- coding: utf-8 -*-
"""wal_recovery 续跑点检测回归网（续跑点唯一真相源 = plan JSON）。

锁完成/中断分类判据：plan_tracker 持久化用 completed_at/aborted_at 时间戳（顶层无 status
字段），分类必须查时间戳——只查 p.get("status")（恒 None）会把所有 plan 误判中断
exit1 + 给出错误续跑指令。

覆盖：
  · _parse_plan_list_output（纯正则·真实）
  · _filter_plans_by_cluster（cluster 过滤判据 1·真实）
  · 完成/中断分类（复现 main 内 _done/_aborted 闭包·status-None 回归核心·闭包难直接 import）

零依赖范式（__main__ 自跑）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import wal_recovery as wr  # noqa: E402


# ============ _parse_plan_list_output（纯正则·真实）============
def test_parse_plan_list_basic():
    # 真实 plan_tracker list 输出格式（`[{flag:6}]` 左对齐·双空格分隔）
    text = ("  [DONE  ] pid1  cmd=cluster-write  project=书A  chapter=5\n"
            "  [ACTIVE] pid2  cmd=outline  project=书A  chapter=None")
    out = wr._parse_plan_list_output(text, "书A")
    assert len(out) == 2
    assert out[0]["id"] == "pid1" and out[0]["chapter"] == 5 and out[0]["status"] == "DONE"
    assert out[1]["chapter"] is None and out[1]["status"] == "ACTIVE"


def test_parse_plan_list_project_filter():
    text = ("  [DONE  ] pid1  cmd=x  project=书A  chapter=1\n"
            "  [DONE  ] pid2  cmd=y  project=书B  chapter=2")
    out = wr._parse_plan_list_output(text, "书A")
    assert len(out) == 1 and out[0]["id"] == "pid1"


def test_parse_plan_list_empty():
    assert wr._parse_plan_list_output("", "书A") == []


# ============ _filter_plans_by_cluster（判据 1 plan.key 归一·真实）============
def test_filter_by_cluster_key_match():
    """plan.key 归一化 == 目标 cluster（"001"→"cluster_001"·不机械拼接）。"""
    plans = [{"id": "p1", "key": "cluster_001"}, {"id": "p2", "key": "cluster_002"}]
    out = wr._filter_plans_by_cluster(plans, "/tmp/nonexistent_proj", "001")
    assert len(out) == 1 and out[0]["id"] == "p1"


def test_filter_by_cluster_no_match():
    plans = [{"id": "p1", "key": "cluster_005"}]
    out = wr._filter_plans_by_cluster(plans, "/tmp/nonexistent_proj", "001")
    assert out == []


# ============ 完成/中断分类（复现 main 内闭包·status-None 回归核心）============
# 闭包在 main() 内不可直接 import·此处复现其逻辑测「分类原则」。
def _done(p):
    return bool(p.get("completed_at")) or p.get("status") in ("DONE", "done", "completed")


def _aborted(p):
    return bool(p.get("aborted_at")) or p.get("status") in ("ABORT", "abort")


def test_classify_done_by_completed_at():
    """🔴 completed_at 时间戳判 done（顶层无 status·只查 p.get(status) 恒 None 会误判全中断）。"""
    assert _done({"completed_at": "2026-01-01T00:00:00"}) is True


def test_classify_aborted_by_aborted_at():
    assert _aborted({"aborted_at": "2026-01-01T00:00:00"}) is True


def test_classify_incomplete_status_none():
    """🔴 status-None + 无时间戳 → 真 incomplete（活跃未完成）·不被误判 done/aborted。"""
    p = {"id": "p1"}
    assert not (_done(p) or _aborted(p))


def test_classify_text_status_compat():
    """文本兜底 status=DONE/ABORT 兼容（_parse_plan_list_output 路径）。"""
    assert _done({"status": "DONE"}) is True
    assert _aborted({"status": "ABORT"}) is True
    assert _done({"status": "active"}) is False


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
