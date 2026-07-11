"""/continue 断点恢复·恢复点回归锁（🔴 2026-06-27 W5 · completeness critic 揪出的裸奔环节）。

/continue 续写靠 wal_recovery 算「从哪一步续跑」。恢复点算错会**重放已完成步**或
**漏跑未完成步**——此前无回归锁，恢复语义可静默漂移。本网钉死 wal_recovery 的恢复点计算：

  ① completed_steps=[1,2,3] → 恢复从 step4（done+1·连续完成正常路径·不漏 4）
  ② WAL/plan JSON 损坏 → wal_recovery 降级不崩（坏文件跳过·好 plan 照常报）
  ③ 非连续完成（中途步 pending、后续步 completed）→ 恢复点 = **第一个未完成步**，
     而非 completed 计数+1（修真 bug：count+1 会跳过中间 pending 步 = 漏步）

「不重放已完成步」由主代理据 wal_recovery 恢复点跳过已完成步保证（test_continue_resume_e2e 已锁）；本网锁
wal_recovery 给主代理的「续跑 --n N」指令正确性。零依赖范式（__main__ 自跑 + pytest 均可）·零 API。
"""
import io
import json
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import plan_tracker as pt  # noqa: E402
import wal_recovery as wr   # noqa: E402


# ============ 沙盒 + plan 构造（plan_tracker 真实形态）============

@contextmanager
def _wal_sandbox():
    """隔离沙盒项目 + 空 GLOBAL_PLANS_DIR（防真 .plans 别项目 plan 串味）。"""
    tmp = Path(tempfile.mkdtemp())
    proj = tmp / "续跑回归书"
    plans_dir = proj / "_数据库" / ".plans"
    plans_dir.mkdir(parents=True)
    empty_global = tmp / "_empty_global"
    empty_global.mkdir()
    saved = pt.GLOBAL_PLANS_DIR
    pt.GLOBAL_PLANS_DIR = empty_global  # wal_recovery 惰性 import 会拿到这个值
    try:
        yield proj, plans_dir
    finally:
        pt.GLOBAL_PLANS_DIR = saved


def _write_plan(plans_dir: Path, plan: dict):
    (plans_dir / f"{plan['id']}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


def _plan_with_steps(pid, command, key, statuses, *, completed=False):
    """构造 plan_tracker 真实形态 plan：statuses = [step1状态, step2状态, ...]
    （'completed' / 'pending' …）·顶层用 completed_at 时间戳表整 plan 完成（非 status 字段）。"""
    steps = [{"n": i + 1, "name": f"s{i+1}", "status": st} for i, st in enumerate(statuses)]
    return {
        "id": pid, "command": command, "project": "续跑回归书", "key": key,
        "chapter": None, "total_steps": len(statuses),
        "completed_at": "2026-06-27T00:00:00" if completed else None,
        "aborted_at": None, "steps": steps,
    }


def _run_wal_main(*argv) -> tuple:
    buf = io.StringIO()
    saved_argv = sys.argv
    sys.argv = ["wal_recovery.py", *map(str, argv)]
    try:
        with redirect_stdout(buf):
            wr.main()
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = saved_argv
    return code, buf.getvalue()


# ============ ① 连续完成 [1,2,3] → 续跑 step4（不漏 4·正常路径）============

def test_completed_prefix_resumes_from_next_step():
    """completed_steps=[1,2,3]（7 步 plan）→ exit 1 + 「续跑 ... --n 4」。"""
    with _wal_sandbox() as (proj, plans_dir):
        statuses = ["completed", "completed", "completed", "pending", "pending", "pending", "pending"]
        _write_plan(plans_dir, _plan_with_steps("续跑回归书_cw_001", "cluster-write",
                                                "cluster_001", statuses))
        code, out = _run_wal_main(proj)
        assert code == 1, f"有中断应 exit 1·实际 {code}\n{out}"
        assert "--n 4" in out, f"3 步连续完成应续跑 step4·实际:\n{out}"
        assert "🔴 中断" in out


def test_resume_step_matches_done_count_plus_one_for_all_prefixes():
    """连续完成 k 步 → 续跑 step k+1（前缀完成 == done_count+1 不回归）。"""
    for k in range(0, 12):
        with _wal_sandbox() as (proj, plans_dir):
            statuses = ["completed"] * k + ["pending"] * (12 - k)
            _write_plan(plans_dir, _plan_with_steps(
                "续跑回归书_csv_k", "cluster-save-state", "cluster_002", statuses))
            code, out = _run_wal_main(proj)
            assert code == 1, out
            assert f"--n {k + 1}" in out, f"完成 {k} 步应续跑 step{k+1}·实际:\n{out}"


# ============ ② WAL/plan JSON 损坏 → 降级不崩 ============

def test_corrupt_plan_json_degrades_without_crash():
    """plans 目录混入损坏 JSON → wal_recovery 跳过坏文件·照常报好 plan·不抛异常。"""
    with _wal_sandbox() as (proj, plans_dir):
        # 一份损坏 JSON（截断）
        (plans_dir / "续跑回归书_broken.json").write_text(
            '{"id": "broken", "project": "续跑回归书", "steps": [', encoding="utf-8")
        # 一份正常未完成 plan
        statuses = ["completed", "pending"]
        _write_plan(plans_dir, _plan_with_steps("续跑回归书_cw_ok", "cluster-write",
                                                "cluster_003", statuses))
        code, out = _run_wal_main(proj)  # 不得抛异常
        assert code == 1, out
        assert "续跑回归书_cw_ok" in out
        assert "broken" not in out, "损坏 plan 不应进入报告"
        assert "--n 2" in out


def test_no_plans_dir_exit0_no_crash():
    """项目无任何 plan → exit 0·不崩（continue 进入「写下一个 cluster」分支）。"""
    with _wal_sandbox() as (proj, _plans_dir):
        code, out = _run_wal_main(proj)
        assert code == 0, out
        assert "无 plan 数据" in out or "未完成 0" in out


# ============ ③ 非连续完成 → 恢复点 = 第一个未完成步（修真 bug）============

def test_non_contiguous_completion_resumes_first_pending():
    """🔴 修真 bug：steps 1,2 completed·3 pending·4 completed（非连续完成·5-7 pending）→
    恢复点必须是 step3（第一个未完成）·绝非 done_count(3)+1=4（会漏跑 step3）。"""
    with _wal_sandbox() as (proj, plans_dir):
        statuses = ["completed", "completed", "pending", "completed", "pending", "pending", "pending"]
        _write_plan(plans_dir, _plan_with_steps("续跑回归书_cw_gap", "cluster-write",
                                                "cluster_004", statuses))
        code, out = _run_wal_main(proj)
        assert code == 1, out
        assert "--n 3" in out, f"非连续完成应从第一个未完成步 step3 续跑·实际:\n{out}"
        assert "--n 4" not in out, "绝不能告诉主代理跳过未完成的 step3（漏步 bug）"


def test_first_resume_step_helper_unit():
    """_first_resume_step 单元：取第一个未完成步·连续场景 == done_count+1·空/缺 n 退化。"""
    # 连续完成前缀
    pre = [{"n": 1, "status": "completed"}, {"n": 2, "status": "completed"},
           {"n": 3, "status": "pending"}]
    assert wr._first_resume_step(pre, 2) == 3
    # 非连续：中途 pending·后续 completed → 取最小未完成
    gap = [{"n": 1, "status": "completed"}, {"n": 2, "status": "pending"},
           {"n": 3, "status": "completed"}]
    assert wr._first_resume_step(gap, 2) == 2, "非连续应取 step2 而非 done_count(2)+1=3"
    # verified 视同完成（与 main done_count 口径一致）
    ver = [{"n": 1, "verified": True}, {"n": 2, "status": "pending"}]
    assert wr._first_resume_step(ver, 1) == 2
    # 空 steps → 退回 done_count+1（旧语义·不崩）
    assert wr._first_resume_step([], 0) == 1
    # 缺 n 字段 → 退回 done_count+1
    assert wr._first_resume_step([{"status": "pending"}], 0) == 1


# ============ ③' completed_steps 与 plan_tracker step 状态一致性 ============

def test_done_count_consistent_with_plan_tracker_step_status():
    """wal_recovery 的「X/Y 步」done_count 与 plan_tracker step 状态口径一致：
    plan_tracker.STATUS_COMPLETED 标记的步计入完成·pending 不计。"""
    with _wal_sandbox() as (proj, plans_dir):
        # 用 plan_tracker 的真实状态常量构造（口径对齐校验）
        statuses = [pt.STATUS_COMPLETED, pt.STATUS_COMPLETED, pt.STATUS_PENDING,
                    pt.STATUS_PENDING, pt.STATUS_PENDING]
        _write_plan(plans_dir, _plan_with_steps("续跑回归书_csv_cons", "cluster-save-state",
                                                "cluster_005", statuses))
        code, out = _run_wal_main(proj)
        assert code == 1, out
        # 2 步完成 / 5 步总 → 显示 2/5·续跑 step3
        assert "2/5 步" in out, f"done_count 应与 plan_tracker 状态一致·实际:\n{out}"
        assert "--n 3" in out


# ============ ④ 已完成 plan 不报中断（恢复语义：不把已完成 plan 当中断）============

def test_completed_plan_not_flagged_interrupted():
    """整 plan 完成（completed_at 时间戳·全步 completed）→ exit 0·不报中断（不重跑全书）。"""
    with _wal_sandbox() as (proj, plans_dir):
        statuses = ["completed"] * 12
        _write_plan(plans_dir, _plan_with_steps("续跑回归书_csv_done", "cluster-save-state",
                                                "cluster_006", statuses, completed=True))
        code, out = _run_wal_main(proj)
        assert code == 0, f"已完成 plan 应 exit 0·实际 {code}\n{out}"
        assert "未完成 0" in out
        assert "🔴 中断" not in out


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
