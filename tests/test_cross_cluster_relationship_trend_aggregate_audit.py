#!/usr/bin/env python3
"""cross_cluster_relationship_trend_aggregate.py 回归测试

覆盖两类顾问信号（均 advisory/warning · 不干涉模型创作判断 · 北极星⑤）：
  RELATIONSHIP_OUT_OF_BOUND：关系.json 当前投影某维度超出 [-10, 10]
    （非数值 / bool 值被类型守卫跳过，不崩不误报）。
  RELATIONSHIP_FROZEN：近 N 个已完成 cluster 的 archive.relationships 里
    从未出现某关系对（cluster_state_sources 读 .wal/{cluster_id}_archive.json，
    以 cluster 为窗口单位，不是章节增量）。

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照 test_cross_cluster_contract）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
_SCANNER = _SCRIPTS / "cross_cluster_relationship_trend_aggregate.py"

sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT / "tests"))
import cross_cluster_relationship_trend_aggregate as mod  # noqa: E402  (import = 钉住不崩)
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402

# 子进程强制 UTF-8 输出（Windows 管道默认 GBK）。
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def _run(proj: Path, last_n=15, timeout=120):
    return subprocess.run(
        [sys.executable, str(_SCANNER), str(proj), "--last-n", str(last_n)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=timeout,
    )


def _mk_project(cluster_relationships: dict[str, list[dict]], relationships_snapshot: list[dict]) -> Path:
    """造一个最小项目：
      relationships_snapshot → _数据库/关系.json（RELATIONSHIP_OUT_OF_BOUND 的当前投影来源）
      cluster_relationships   → {cluster_id: [{"from","to"},...]}，写入
                                 _数据库/故事块摘要.json（已完成 cluster 登记，供窗口计数）+
                                 _数据库/.wal/{cluster_id}_archive.json（RELATIONSHIP_FROZEN
                                 判定 changed_pairs 的权威来源）
    返回 proj 路径（调用方负责 tempdir 生命周期）。
    """
    td = Path(tempfile.mkdtemp(prefix="rel_trend_audit_"))
    proj = td / "proj"
    db = proj / "_数据库"
    wal = db / ".wal"
    wal.mkdir(parents=True)
    (db / "关系.json").write_text(
        json.dumps({"relationships": relationships_snapshot}, ensure_ascii=False), encoding="utf-8")
    write_cluster_summary(proj, [cluster_record(cid) for cid in cluster_relationships])
    for cid, rels in cluster_relationships.items():
        archive = {"cluster_id": cid, "relationships": [dict(r) for r in rels]}
        (wal / f"{cid}_archive.json").write_text(
            json.dumps(archive, ensure_ascii=False), encoding="utf-8")
    return proj


def _load_report(proj: Path) -> dict:
    scan_dir = proj / "_数据库" / ".cross_cluster_scan"
    reports = sorted(scan_dir.glob("relationship_trend_*.json"))
    assert reports, f"无报告落盘: {list(scan_dir.iterdir()) if scan_dir.exists() else '目录缺失'}"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def _codes(report: dict) -> list[str]:
    return [f.get("code") for f in report.get("findings", [])]


# ============ RELATIONSHIP_FROZEN：近 N cluster 窗口零变更 ============
def test_frozen_relationship_now_detected():
    """8 个已完成 cluster 的 archive 里只有 X→Y 变化，A→B 全程不出现
    → A→B 在 关系.json 里有当前投影但从不在 changed_pairs 中 → 应报 RELATIONSHIP_FROZEN。"""
    cluster_relationships = {
        f"cluster_{index:03d}": [{"from": "X", "to": "Y"}] for index in range(1, 9)
    }
    proj = _mk_project(
        cluster_relationships,
        relationships_snapshot=[
            {"from": "A", "to": "B", "affinity": 3, "trust": 2},   # 冻结关系
            {"from": "X", "to": "Y", "trust": 2},                  # 活跃关系
        ],
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    frozen = [f for f in report["findings"] if f.get("code") == "RELATIONSHIP_FROZEN"]
    assert frozen, f"修复后应检出 A→B 冻结，实际 findings: {_codes(report)}"
    pairs = {(f.get("from"), f.get("to")) for f in frozen}
    assert ("A", "B") in pairs, f"冻结对应是 A→B，实得 {pairs}"
    # 活跃关系 X→Y 不应被误报冻结（它在每个 cluster 的 archive 里都出现）
    assert ("X", "Y") not in pairs, f"活跃关系 X→Y 被误判冻结: {pairs}"


def test_changing_relationship_not_frozen():
    """所有 current 关系在窗口内每个 cluster 的 archive 都有出现 → 一个 FROZEN 都不该报。"""
    cluster_relationships = {
        f"cluster_{index:03d}": [{"from": "A", "to": "B"}] for index in range(1, 10)
    }
    proj = _mk_project(
        cluster_relationships,
        relationships_snapshot=[{"from": "A", "to": "B", "trust": 2}],
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    assert "RELATIONSHIP_FROZEN" not in _codes(report), \
        f"活跃关系不应报冻结: {_codes(report)}"


def test_frozen_needs_min_8_clusters():
    """已完成 cluster 数 <8 时 FROZEN 阈值不触发（窗口单位=cluster，非章节）。"""
    cluster_relationships = {
        f"cluster_{index:03d}": [{"from": "X", "to": "Y"}] for index in range(1, 4)  # 仅 3 个
    }
    proj = _mk_project(
        cluster_relationships,
        relationships_snapshot=[{"from": "A", "to": "B", "trust": 2}],
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    assert "RELATIONSHIP_FROZEN" not in _codes(report), \
        f"仅 3 个已完成 cluster 不应触发 FROZEN（阈值=8）: {_codes(report)}"


# ============ RELATIONSHIP_OUT_OF_BOUND：非数值维度不崩 ============
def test_nonnumeric_dim_values_no_crash():
    """关系.json 某关系带 null / 字符串维度值（弱模型 freestyle 现实可发生）
    → 类型守卫跳过该维度，不崩、正常出报告。"""
    proj = _mk_project(
        {},
        relationships_snapshot=[
            {"from": "A", "to": "B", "trust": None, "affinity": "+3"},
            {"from": "C", "to": "D", "trust": 2, "affinity": 7},
        ],
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"非数值维度致崩:\n{r.stderr[-800:]}"
    assert r.returncode in (0, 1, 2), f"异常退出码 {r.returncode}: {r.stderr[-400:]}"
    report = _load_report(proj)  # 能落盘且可解析即证未中途崩
    assert isinstance(report.get("findings"), list)


def test_out_of_bound_string_value_no_crash():
    """关系.json 某维度是字符串 → 类型守卫挡在比较前；真数值越界仍照常 OUT_OF_BOUND 报。"""
    proj = _mk_project(
        {},
        relationships_snapshot=[
            {"from": "A", "to": "B", "trust": "high", "affinity": 12},  # 字符串 + 真越界
            {"from": "C", "to": "D", "fear": None, "respect": -15},     # null + 真越界
        ],
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"字符串维度致崩:\n{r.stderr[-800:]}"
    report = _load_report(proj)
    oob = [f for f in report["findings"] if f.get("code") == "RELATIONSHIP_OUT_OF_BOUND"]
    # 字符串/None 被守卫跳过，但 affinity=12 与 respect=-15 仍应各报一次越界
    oob_dims = {(f.get("from"), f.get("to"), f.get("dimension")) for f in oob}
    assert ("A", "B", "affinity") in oob_dims, f"affinity=12 越界应报: {oob_dims}"
    assert ("C", "D", "respect") in oob_dims, f"respect=-15 越界应报: {oob_dims}"
    # 字符串 trust='high' 不应进 OUT_OF_BOUND（被类型守卫挡掉，不崩也不误报）
    assert ("A", "B", "trust") not in oob_dims, f"字符串维度不应报越界: {oob_dims}"


def test_bool_value_excluded_from_series():
    """bool 是 int 子类——若只判 isinstance(int) 会让 True/False(=1/0)混入数值判定。
    守卫显式排除 bool：带 bool 维度的关系不该因 bool 进 OUT_OF_BOUND，也不致崩。"""
    proj = _mk_project(
        {}, relationships_snapshot=[{"from": "A", "to": "B", "trust": True, "affinity": 11}])
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"bool 维度致崩:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    oob_dims = {(f.get("from"), f.get("to"), f.get("dimension")) for f in report["findings"]
                if f.get("code") == "RELATIONSHIP_OUT_OF_BOUND"}
    assert ("A", "B", "trust") not in oob_dims, f"bool 维度不应进越界判定: {oob_dims}"
    assert ("A", "B", "affinity") in oob_dims, f"affinity=11 真越界仍应报: {oob_dims}"


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
