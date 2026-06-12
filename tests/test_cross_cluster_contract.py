#!/usr/bin/env python3
"""cross-cluster aggregator 契约测试（24 个 aggregator 此前零测试）

覆盖 core/scripts/run_cross_cluster_aggregates.py（save-state step 9 的统一 wrapper）：
  ① tier 注册表契约：core_5 / core_10_extra / full_18_extra 三级集合无幽灵引用
     （SCAN_TIERS 引用的 scanner .py 必须真实存在——wrapper 对缺文件静默 skipped，
      幽灵引用 = 该 scanner 永远不跑且无人发现，与 plan 模板幽灵引用同病）
  ② cluster 模式端到端：scaffold_subsystems 起最小项目 + 手造 2 cluster 的
     事件簇.json / 故事块摘要.json fixture → subprocess 跑 --cluster 002 --tier minimal_5
     → rc=0 + stderr 无 Traceback/[CRASH] + 跑满 5/5
  ③ 产出契约：scanner 报告 JSON 真落盘 _数据库/.cross_chapter_scan/ 且可解析
  ④ graceful degrade：无 故事块摘要.json / 无章节目录时不崩（恒 exit 0 铁律）
  ⑤ FATAL 边界：缺 --ch/--cluster、cluster 查不到 chapter_range → exit 2
  ⑥ 窗口换算 helper 单元：get_cluster_last_ch / chapters_in_last_n_clusters

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照 test_plan_script_contract）。
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
_WRAPPER = _SCRIPTS / "run_cross_cluster_aggregates.py"
_SCAFFOLD = _SCRIPTS / "scaffold_subsystems.py"

sys.path.insert(0, str(_SCRIPTS))
import run_cross_cluster_aggregates as rcca  # noqa: E402

# Windows 下子进程管道 stdout 默认 locale 编码（GBK）——强制子进程 UTF-8 输出
# （同 test_narrative_seq.py 范式）。
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def _run(args, timeout=240):
    return subprocess.run(
        [sys.executable, str(_WRAPPER)] + [str(a) for a in args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=timeout,
    )


def _write_shijianji(db: Path, clusters: list):
    (db / "事件簇.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "ecas_enabled": True,
                    "clusters": clusters}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def _two_cluster_fixture(db: Path):
    """手造 2 cluster 的 事件簇.json + 故事块摘要.json（字段契约见
    cluster_summary_reader.py docstring：cluster_id/title 必填 + chapters[ch] 富摘要）。"""
    _write_shijianji(db, [
        {"cluster_id": "cluster_001", "title": "旧仓库", "status": "已完成",
         "chapter_range": [1, 3]},
        {"cluster_id": "cluster_002", "title": "反杀局", "status": "in_progress",
         "chapter_range": [4, 6]},
    ])

    def _chs(lo, hi, summ, ending):
        return {str(c): {"cjk_count": 3200, "summary": summ * 4,
                         "ending_type": ending, "characters": ["主角", "对手"]}
                for c in range(lo, hi + 1)}

    summary = {"schema_version": "v2.cluster", "clusters": [
        {"cluster_id": "cluster_001", "title": "旧仓库", "chapter_range": [1, 3],
         "cluster_end_ch": 3, "word_count": 9000,
         "chapters": _chs(1, 3, "主角进入旧仓库发现线索并与对手周旋。", "cliffhanger")},
        {"cluster_id": "cluster_002", "title": "反杀局", "chapter_range": [4, 6],
         "cluster_end_ch": 6, "word_count": 9500,
         "chapters": _chs(4, 6, "对手反扑主角设局反杀埋下新伏笔。", "reveal")},
    ]}
    (db / "故事块摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


# ============ 共享 fixture：scaffold + 2 cluster + minimal_5 端到端跑一次 ============
# 端到端要起 5 个 scanner 子进程（数秒），test ②③ 共用同一次运行结果。
_CACHE = {}


def _full_run():
    if "result" in _CACHE:
        return _CACHE["result"]
    tmp = Path(tempfile.mkdtemp(prefix="xcc_contract_"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    proj = tmp / "proj"
    db = proj / "_数据库"
    r0 = subprocess.run(
        [sys.executable, str(_SCAFFOLD), "emit", "--db-dir", str(db)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=_ENV)
    assert r0.returncode == 0, f"scaffold emit 失败: {r0.stderr[-400:]}"
    _two_cluster_fixture(db)
    r = _run([proj, "--cluster", "002", "--tier", "minimal_5"])
    _CACHE["result"] = (proj, r)
    return _CACHE["result"]


# ============ ① tier 注册表契约 ============
def test_tier_registry_no_ghost_scanner():
    """SCAN_TIERS 三级集合：数量锁 + 无重复 + 引用的 scanner .py 全部真实存在。
    wrapper 对缺文件只 skipped+=1 静默跳过——幽灵引用唯一防线是本测试。"""
    core5 = rcca.SCAN_TIERS["core_5"]
    extra10 = rcca.SCAN_TIERS["core_10_extra"]
    extra18 = rcca.SCAN_TIERS["full_18_extra"]
    assert len(core5) == 5, core5
    assert len(extra10) == 5, extra10
    full = core5 + extra10 + extra18
    assert len(full) >= 18, f"full 集合 {len(full)} 个，名义 full_18 至少 18"
    assert len(full) == len(set(full)), "tier 集合内有重复 scanner"
    missing = [sc for sc in full if not (_SCRIPTS / f"{sc}.py").exists()]
    assert not missing, f"SCAN_TIERS 幽灵引用（文件不存在）: {missing}"
    # SCANNERS_WITH_CH 必须是注册表子集（孤儿条目 = --ch 分支永远不命中）
    orphan = rcca.SCANNERS_WITH_CH - set(full)
    assert not orphan, f"SCANNERS_WITH_CH 含注册表外条目: {orphan}"


# ============ ⑥ 窗口换算 helper 单元 ============
def test_cluster_window_helpers():
    """get_cluster_last_ch：cluster key 带/不带前缀都解析到末章；
    chapters_in_last_n_clusters：按已落章 cluster 累计章数、下限 4、缺文件 None。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        db = proj / "_数据库"
        db.mkdir(parents=True)
        # 缺 事件簇.json → 双双返回 None
        assert rcca.get_cluster_last_ch(proj, "002") is None
        assert rcca.chapters_in_last_n_clusters(proj, "002", 2) is None
        _write_shijianji(db, [
            {"cluster_id": "cluster_001", "title": "c1", "status": "已完成",
             "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "title": "c2", "status": "in_progress",
             "chapter_range": [4, 6]},
        ])
        assert rcca.get_cluster_last_ch(proj, "002") == 6
        assert rcca.get_cluster_last_ch(proj, "cluster_002") == 6
        assert rcca.get_cluster_last_ch(proj, "099") is None
        # 2 cluster 窗口 = 3+3 章；1 cluster = 3 章但下限钳到 4
        assert rcca.chapters_in_last_n_clusters(proj, "002", 2) == 6
        assert rcca.chapters_in_last_n_clusters(proj, "002", 1) == 4


# ============ ⑤ FATAL 边界 ============
def test_cli_fatal_paths_exit_2():
    """缺 --ch/--cluster → exit 2；--cluster 查不到 chapter_range → exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        db = proj / "_数据库"
        db.mkdir(parents=True)
        _write_shijianji(db, [{"cluster_id": "cluster_001", "title": "c1",
                               "status": "已完成", "chapter_range": [1, 3]}])
        r = _run([proj])
        assert r.returncode == 2, f"无锚点应 exit 2，实得 {r.returncode}"
        assert "[FATAL]" in r.stderr, r.stderr[-300:]
        r2 = _run([proj, "--cluster", "099"])
        assert r2.returncode == 2, f"cluster 不存在应 exit 2，实得 {r2.returncode}"
        assert "[FATAL]" in r2.stderr, r2.stderr[-300:]


def test_tier_off_skips_all():
    """--tier off → 一个 scanner 不跑、exit 0（紧急快速出稿路径）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        (proj / "_数据库").mkdir(parents=True)
        r = _run([proj, "--ch", "5", "--tier", "off"])
        assert r.returncode == 0, f"off 应 exit 0，实得 {r.returncode}: {r.stderr[-300:]}"
        assert "[SKIP]" in r.stdout, r.stdout


# ============ ② cluster 模式端到端 ============
def test_minimal5_cluster_run_green():
    """scaffold 最小项目 + 2 cluster fixture → --cluster 002 --tier minimal_5：
    rc=0 · stderr 无 Traceback/[CRASH] · 跑满 5/5 · minimal_5 归一成 core_5 ·
    末章锚点解析到 ch6（cluster_002 的 chapter_range[1]）。"""
    proj, r = _full_run()
    assert r.returncode == 0, f"wrapper 应恒 exit 0，实得 {r.returncode}: {r.stderr[-500:]}"
    assert "Traceback" not in r.stderr, f"stderr 出现 Traceback:\n{r.stderr[-800:]}"
    assert "[CRASH]" not in r.stderr, f"有 scanner 真崩溃:\n{r.stderr[-800:]}"
    assert "intensity=core_5" in r.stdout, f"minimal_5 未归一成 core_5: {r.stdout}"
    assert "5/5" in r.stdout, f"未跑满 5/5（有 scanner 被 skipped？）: {r.stdout}"
    assert "ch6" in r.stdout, f"cluster 002 末章锚点应为 ch6: {r.stdout}"


# ============ ③ 产出契约：报告 JSON 落盘且可解析 ============
def test_scan_reports_land_and_parse():
    """端到端跑完后 _数据库/.cross_chapter_scan/ 必须有 ≥1 份报告 JSON 且全部可解析
    （scanner 数据不足时可不出报告，但 fixture 喂了 fate 锚点 → fate_drift 必落盘）。"""
    proj, r = _full_run()
    assert r.returncode == 0, r.stderr[-300:]
    scan_dir = proj / "_数据库" / ".cross_chapter_scan"
    assert scan_dir.exists(), f"报告目录未创建: {scan_dir}"
    reports = sorted(scan_dir.glob("*.json"))
    assert reports, f"无任何报告 JSON 落盘: {list(scan_dir.iterdir())}"
    for rp in reports:
        obj = json.loads(rp.read_text(encoding="utf-8"))  # 损坏即抛 → FAIL
        assert isinstance(obj, (dict, list)), f"{rp.name} 解析出非容器类型: {type(obj)}"


# ============ ④ graceful degrade：缺输入不崩 ============
def test_graceful_degrade_missing_summary():
    """裸项目（只有 事件簇.json·无 故事块摘要.json·无章节目录·未 scaffold）：
    scanner 全部回退/空跑，wrapper 仍 exit 0、无 Traceback、无 [CRASH]。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        db = proj / "_数据库"
        db.mkdir(parents=True)
        _write_shijianji(db, [{"cluster_id": "cluster_002", "title": "c2",
                               "status": "in_progress", "chapter_range": [4, 6]}])
        r = _run([proj, "--cluster", "002", "--tier", "minimal_5"])
        assert r.returncode == 0, f"缺输入应 graceful exit 0，实得 {r.returncode}: {r.stderr[-500:]}"
        assert "Traceback" not in r.stderr, f"stderr 出现 Traceback:\n{r.stderr[-800:]}"
        assert "[CRASH]" not in r.stderr, f"有 scanner 崩溃:\n{r.stderr[-800:]}"
        assert "5/5" in r.stdout, r.stdout


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
