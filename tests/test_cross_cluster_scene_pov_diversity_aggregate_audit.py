#!/usr/bin/env python3
"""cross_cluster_scene_pov_diversity_aggregate.py 审计回归测试

钉死 triage_worth_fixing.json 中 L64 的修复（单位错配 bug）：
  cluster 模式下 `--last-n` 的语义是【章数】（runner 已把『最近 N 个 cluster』换算成
  章数窗口经 --last-n 传入），但旧代码把它当 `last_n_clusters=args.last_n`（cluster 个数）
  喂给 csr.get_chapter_records → 按 cluster 个数切片 → 近窗语义反向放大。
  修复：先 get_chapter_records(project_root) 取全账本（已按 ch 升序），再 recs[-last_n:]
  按【章】截最近窗口，对齐同文件磁盘路径 L99 `chapters_written[-args.last_n:]`。

判别性 fixture：4 cluster × 3 章 = 12 章（ch1-12），--last-n 3：
  ✅ 修复后（章单位窗口）：chapters_scanned == [10, 11, 12]（最近 3 章）
  ❌ 旧 bug（cluster 单位窗口）：last_n_clusters=3 → 最近 3 个 cluster = ch4..12（9 章）
  → 断言 chapters_scanned 恰为最近 3 章即区分修复 vs 回归。

附带结构断言：源码 cluster 分支不再出现 `last_n_clusters=args.last_n`（源级哨兵）。

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照 test_cross_cluster_contract）。
北极星⑤：本 scanner 全 advisory（exit 1 advisory / 2 warning）·只修近窗语义不动 hard_gate。
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
_SCANNER = _SCRIPTS / "cross_cluster_scene_pov_diversity_aggregate.py"

sys.path.insert(0, str(_SCRIPTS))
import cross_cluster_scene_pov_diversity_aggregate as scene_pov  # noqa: E402  (import 修复点)

# cluster 分支需 CLUSTER_MODE=1（is_cluster_mode 读 env）；Windows 子进程管道强制 UTF-8。
_ENV = dict(os.environ, CLUSTER_MODE="1", PYTHONIOENCODING="utf-8")


def _build_project(n_clusters: int, chs_per: int) -> Path:
    """造一个最小项目：故事块摘要.json 有 n_clusters 个落账 cluster，每个 chs_per 章，
    每章带不同 scene_type/pov（让 ledger_has_field('scene_type') 为真）。返回 project_root。"""
    tmp = Path(tempfile.mkdtemp(prefix="scene_pov_audit_"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    proj = tmp / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)

    clusters = []
    ch = 1
    scene_cycle = ["战斗", "对话", "探索", "权谋"]
    pov_cycle = ["甲", "乙", "丙", "丁"]
    for ci in range(n_clusters):
        lo = ch
        chapters = {}
        for _ in range(chs_per):
            chapters[str(ch)] = {
                "cjk_count": 3200,
                "summary": "测试章摘要。" * 4,
                # 每章独立 scene_type/pov，避免触发 streak 类 finding 干扰断言
                "scene_type": scene_cycle[ch % len(scene_cycle)],
                "pov": pov_cycle[ch % len(pov_cycle)],
                "characters": [pov_cycle[ch % len(pov_cycle)], "配角"],
            }
            ch += 1
        hi = ch - 1
        clusters.append({
            "cluster_id": f"cluster_{ci + 1:03d}",
            "title": f"块{ci + 1}",
            "status": "已完成" if ci < n_clusters - 1 else "in_progress",
            "chapter_range": [lo, hi],
            "cluster_end_ch": hi,
            "word_count": chs_per * 3200,
            "chapters": chapters,
        })
    (db / "故事块摘要.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def _run_scanner(proj: Path, last_n: int):
    """子进程跑 scanner（CLUSTER_MODE=1），返回 (CompletedProcess, 最新报告 dict)。"""
    r = subprocess.run(
        [sys.executable, str(_SCANNER), str(proj), "--last-n", str(last_n)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=120,
    )
    scan_dir = proj / "_数据库" / ".cross_chapter_scan"
    report = None
    if scan_dir.exists():
        reports = sorted(scan_dir.glob("scene_pov_diversity_*.json"))
        if reports:
            report = json.loads(reports[-1].read_text(encoding="utf-8"))
    return r, report


# ============ 主回归：cluster 模式 --last-n 是【章数】窗口，不是 cluster 个数 ============
def test_last_n_is_chapter_window_not_cluster_count():
    """4 cluster × 3 章（ch1-12）· --last-n 3 →
    chapters_scanned 必须恰为最近 3 【章】[10,11,12]（修复后）·
    而非最近 3 【cluster】的 ch4..12（旧 bug · 会是 9 章）。"""
    proj = _build_project(n_clusters=4, chs_per=3)
    r, report = _run_scanner(proj, last_n=3)
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-800:]}"
    assert report is not None, f"无报告落盘·stdout={r.stdout[-300:]} stderr={r.stderr[-300:]}"
    scanned = report.get("chapters_scanned")
    assert scanned == [10, 11, 12], (
        f"--last-n 3 应取最近 3 【章】[10,11,12]（章单位窗口）·"
        f"实得 {scanned}（若为 [4..12] 9 章 = 回归到 cluster 单位 bug）")


def test_last_n_window_matches_chapter_unit_for_various_n():
    """同一 12 章账本，--last-n N 必须恒为最近 N 章（章单位语义稳定）·
    覆盖 N 横跨 cluster 边界（N=2/5/7）证明切片按章不按 cluster。"""
    proj = _build_project(n_clusters=4, chs_per=3)  # ch1-12
    for n, expected in [(2, [11, 12]), (5, [8, 9, 10, 11, 12]),
                        (7, [6, 7, 8, 9, 10, 11, 12])]:
        r, report = _run_scanner(proj, last_n=n)
        assert "Traceback" not in r.stderr, f"n={n} 崩溃:\n{r.stderr[-600:]}"
        assert report is not None, f"n={n} 无报告"
        assert report.get("chapters_scanned") == expected, (
            f"--last-n {n} 应取最近 {n} 章 {expected}·实得 "
            f"{report.get('chapters_scanned')}")


def test_last_n_ge_total_returns_all_chapters():
    """--last-n 大于总章数时返回全部章（recs[-N:] 在 N>=len 时退化为全集·不越界）。"""
    proj = _build_project(n_clusters=3, chs_per=3)  # ch1-9
    r, report = _run_scanner(proj, last_n=99)
    assert "Traceback" not in r.stderr, f"崩溃:\n{r.stderr[-600:]}"
    assert report is not None, "无报告"
    assert report.get("chapters_scanned") == list(range(1, 10)), (
        f"--last-n 99 (>总章数) 应返回全部 9 章·实得 {report.get('chapters_scanned')}")


# ============ 源级哨兵：cluster 分支不再把 args.last_n 当 last_n_clusters ============
def test_source_no_longer_passes_last_n_clusters_args_last_n():
    """源码哨兵：cluster 分支必须用按章切片（get_chapter_records(project_root) + recs[-last_n:]）·
    绝不再出现 `get_chapter_records(project_root, last_n_clusters=args.last_n)`（旧 bug 形态）。"""
    src = _SCANNER.read_text(encoding="utf-8")
    assert "last_n_clusters=args.last_n" not in src, (
        "源码仍含 `last_n_clusters=args.last_n`（cluster 单位窗口 bug 回归）")
    assert "recs[-args.last_n:]" in src, (
        "源码缺 `recs[-args.last_n:]` 章单位窗口切片（修复点丢失）")


# ============ 退出码契约：advisory→1 / 无发现→0（北极星⑤·不升 hard_gate）============
def test_exit_code_contract_advisory_only():
    """构造 POV 高度集中（主 POV >85%）触发 1 条 advisory → exit 1（非 2/非阻断）·
    确认本 scanner 全 advisory 不产 warning（不违北极星⑤）。"""
    tmp = Path(tempfile.mkdtemp(prefix="scene_pov_exit_"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    proj = tmp / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    # 单 cluster 6 章·主 POV 6/6=100% 是「甲」→ 触发 POV_OVERCONCENTRATED（total>=5）
    chapters = {str(c): {"cjk_count": 3200, "summary": "x" * 12,
                         "scene_type": ["战斗", "对话"][c % 2],  # 2 种以上避免 LOW_DIVERSITY 干扰
                         "pov": "甲", "characters": ["甲", "乙"]}
                for c in range(1, 7)}
    (db / "故事块摘要.json").write_text(json.dumps(
        {"schema_version": "v2.cluster", "clusters": [
            {"cluster_id": "cluster_001", "title": "块1", "status": "in_progress",
             "chapter_range": [1, 6], "cluster_end_ch": 6, "chapters": chapters}]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    r, report = _run_scanner(proj, last_n=10)
    assert "Traceback" not in r.stderr, f"崩溃:\n{r.stderr[-600:]}"
    assert report is not None, "无报告"
    assert report["summary"]["warning"] == 0, "本 scanner 不应产 warning（全 advisory）"
    codes = [f.get("code") for f in report.get("findings", [])]
    assert "POV_OVERCONCENTRATED" in codes, f"主 POV 100% 应报 OVERCONCENTRATED·得 {codes}"
    assert r.returncode == 1, f"有 advisory 应 exit 1·实得 {r.returncode}"


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
    print(f"\n{'ALL OK' if not fails else f'{fails} FAIL'}")
    sys.exit(1 if fails else 0)
