#!/usr/bin/env python3
"""cross_cluster_relationship_trend_aggregate.py 审计回归测试

钉死 triage_worth_fixing.json 对该 scanner 的两处修复（均为确定性数据投影健壮性·
全 advisory/warning 顾问信号·不干涉模型创作判断·北极星⑤）：

  Bug1（medium·triage L150）RELATIONSHIP_FROZEN 死代码：
    history 是 defaultdict，键随首次 append（受 `len(entry)>1` 守卫）诞生 →
    history 中每键列表长度恒 >=1 → 原内联 `if len(entries)==0` 分支恒为 False = 死代码，
    docstring 承诺的 RELATIONSHIP_FROZEN 从不产出。修复：把 FROZEN 检测移出 history 循环，
    独立遍历 current_rels 找『不在 history 中』（= 近 N 章窗口零变更）的关系。
    → test_frozen_relationship_now_detected / test_changing_relationship_not_frozen

  Bug2（low·triage L158）非数值维度崩溃：弱模型 freestyle 自由输出可产 {trust:null} /
    {affinity:"+6"}，原 _ingest_rels（L70-71）零类型校验原样存入 → 下游 abs(v2-v1) /
    dim_series 比较 / OUT_OF_BOUND 比较抛 TypeError → scanner 对该 cluster 全部 advisory
    静默丢失 + 向 incidents.jsonl 灌 [CRASH] 噪声。修复：(A) _ingest_rels 存值前
    isinstance(int/float) 且排除 bool；(B) OUT_OF_BOUND 同口径守卫。
    → test_nonnumeric_dim_values_no_crash / test_out_of_bound_string_value_no_crash /
      test_bool_value_excluded_from_series

非 cluster 模式（不设 CLUSTER_MODE 环境）下 scanner 走 Path B（逐章读
第NNN章_changes.json 的 factual.relationships[]）——正是上述两 bug 所在代码路径，
故 fixture 用磁盘逐章法即可精确命中。

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
import cross_cluster_relationship_trend_aggregate as mod  # noqa: E402  (import = 钉住不崩)

# 子进程强制 UTF-8 输出（Windows 管道默认 GBK）。显式去掉 CLUSTER_MODE → 走磁盘 Path B。
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
_ENV.pop("CLUSTER_MODE", None)


def _run(proj: Path, last_n=15, timeout=120):
    return subprocess.run(
        [sys.executable, str(_SCANNER), str(proj), "--last-n", str(last_n)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=timeout,
    )


def _mk_project(rels_json: dict, chapter_changes: dict[int, list]) -> Path:
    """造一个最小项目：
      rels_json        → _数据库/关系.json（current_rels 来源）
      chapter_changes  → {ch: [relationship dict,...]} 写入 第NNN章/第NNN章_changes.json
                         的 factual.relationships（Path B 的逐章变化来源）
    返回 proj 路径（调用方负责 tempdir 生命周期）。
    """
    td = Path(tempfile.mkdtemp(prefix="rel_trend_audit_"))
    proj = td / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    (db / "关系.json").write_text(json.dumps(rels_json, ensure_ascii=False), encoding="utf-8")
    for ch, rels in chapter_changes.items():
        chdir = proj / "章节" / f"第{ch:03d}章"
        chdir.mkdir(parents=True)
        (chdir / f"第{ch:03d}章_changes.json").write_text(
            json.dumps({"factual": {"relationships": rels}}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def _load_report(proj: Path) -> dict:
    scan_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(scan_dir.glob("relationship_trend_*.json"))
    assert reports, f"无报告落盘: {list(scan_dir.iterdir()) if scan_dir.exists() else '目录缺失'}"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def _codes(report: dict) -> list[str]:
    return [f.get("code") for f in report.get("findings", [])]


# ============ Bug1: RELATIONSHIP_FROZEN 起死回生 ============
def test_frozen_relationship_now_detected():
    """关系.json 有 A→B，但 >=8 章 changes 从不提 A→B（只动别的关系）→
    A→B 结构性地从不进 history → 修复前死代码恒不触发，修复后必报 RELATIONSHIP_FROZEN。"""
    # 8 章里只有 X→Y 在变（保证 recent>=8 且 X→Y 进 history 不被误判 frozen），A→B 全程零变更
    chapter_changes = {ch: [{"from": "X", "to": "Y", "trust": ch % 3}] for ch in range(1, 9)}
    proj = _mk_project(
        rels_json={"relationships": [
            {"from": "A", "to": "B", "affinity": 3, "trust": 2},   # 冻结关系
            {"from": "X", "to": "Y", "trust": 2},                  # 活跃关系
        ]},
        chapter_changes=chapter_changes,
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    frozen = [f for f in report["findings"] if f.get("code") == "RELATIONSHIP_FROZEN"]
    assert frozen, f"修复后应检出 A→B 冻结，实际 findings: {_codes(report)}"
    pairs = {(f.get("from"), f.get("to")) for f in frozen}
    assert ("A", "B") in pairs, f"冻结对应是 A→B，实得 {pairs}"
    # 活跃关系 X→Y 不应被误报冻结（它在 history 里）
    assert ("X", "Y") not in pairs, f"活跃关系 X→Y 被误判冻结: {pairs}"


def test_changing_relationship_not_frozen():
    """所有 current 关系近 N 章都有变更 → history 全覆盖 → 一个 FROZEN 都不该报。"""
    chapter_changes = {ch: [{"from": "A", "to": "B", "trust": ch % 4}] for ch in range(1, 10)}
    proj = _mk_project(
        rels_json={"relationships": [{"from": "A", "to": "B", "trust": 2}]},
        chapter_changes=chapter_changes,
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    assert "RELATIONSHIP_FROZEN" not in _codes(report), \
        f"活跃关系不应报冻结: {_codes(report)}"


def test_frozen_needs_min_8_chapters():
    """窗口 <8 章时 FROZEN 阈值不触发（沿用原 len(recent)>=8 阈值语义）。"""
    chapter_changes = {ch: [{"from": "X", "to": "Y", "trust": ch}] for ch in range(1, 4)}  # 仅 3 章
    proj = _mk_project(
        rels_json={"relationships": [{"from": "A", "to": "B", "trust": 2}]},
        chapter_changes=chapter_changes,
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    assert "RELATIONSHIP_FROZEN" not in _codes(report), \
        f"窗口仅 3 章不应触发 FROZEN（阈值=8）: {_codes(report)}"


# ============ Bug2: 非数值维度不再崩溃 ============
def test_nonnumeric_dim_values_no_crash():
    """changes 里某关系跨 2 章带 null / 字符串维度值（弱模型 freestyle 现实可发生）→
    修复前 _ingest_rels 原样存入 → 下游 abs(v2-v1) / dim_series 比较 TypeError 崩。
    修复后：非数值维度根本不入 entry，scanner 不崩、正常出报告。"""
    chapter_changes = {
        1: [{"from": "A", "to": "B", "trust": None, "affinity": "+3"}],   # null + 字符串
        2: [{"from": "A", "to": "B", "trust": "+6", "affinity": 5}],      # 字符串
        3: [{"from": "A", "to": "B", "trust": 2, "affinity": 7}],         # 正常
    }
    proj = _mk_project(
        rels_json={"relationships": [{"from": "A", "to": "B", "trust": 2}]},
        chapter_changes=chapter_changes,
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"非数值维度致崩(Bug2A 未修?):\n{r.stderr[-800:]}"
    assert r.returncode in (0, 1, 2), f"异常退出码 {r.returncode}: {r.stderr[-400:]}"
    report = _load_report(proj)  # 能落盘且可解析即证未中途崩
    # affinity 数值序列 5→7（ch2→ch3）Δ=2 <5 不报 LEAP；trust 仅 ch3 一个数值点 <2 不成序列。
    # 关键断言是「不崩 + 出报告」，code 具体内容不强约束（视有效数值序列而定）。
    assert isinstance(report.get("findings"), list)


def test_out_of_bound_string_value_no_crash():
    """关系.json 某维度是字符串 → 修复前 L113 `v>10` 对 str 抛 TypeError。
    修复后：非数值一并挡在比较前；真数值越界仍照常 OUT_OF_BOUND 报。"""
    proj = _mk_project(
        rels_json={"relationships": [
            {"from": "A", "to": "B", "trust": "high", "affinity": 12},  # 字符串 + 真越界
            {"from": "C", "to": "D", "fear": None, "respect": -15},     # null + 真越界
        ]},
        chapter_changes={ch: [{"from": "X", "to": "Y", "trust": ch % 2}] for ch in range(1, 4)},
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"字符串维度致崩(Bug2B 未修?):\n{r.stderr[-800:]}"
    report = _load_report(proj)
    oob = [f for f in report["findings"] if f.get("code") == "RELATIONSHIP_OUT_OF_BOUND"]
    # 字符串/None 被守卫跳过，但 affinity=12 与 respect=-15 仍应各报一次越界
    oob_dims = {(f.get("from"), f.get("to"), f.get("dimension")) for f in oob}
    assert ("A", "B", "affinity") in oob_dims, f"affinity=12 越界应报: {oob_dims}"
    assert ("C", "D", "respect") in oob_dims, f"respect=-15 越界应报: {oob_dims}"
    # 字符串 trust='high' 不应进 OUT_OF_BOUND（被类型守卫挡掉，不崩也不误报）
    assert ("A", "B", "trust") not in oob_dims, f"字符串维度不应报越界: {oob_dims}"


def test_bool_value_excluded_from_series():
    """bool 是 int 子类——若只判 isinstance(int) 会让 True/False(=1/0)混入数值序列。
    守卫显式排除 bool：带 bool 维度的关系不该因 bool 进 OUT_OF_BOUND，也不致崩。"""
    proj = _mk_project(
        rels_json={"relationships": [{"from": "A", "to": "B", "trust": True, "affinity": 11}]},
        chapter_changes={ch: [{"from": "X", "to": "Y", "trust": ch % 2}] for ch in range(1, 4)},
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"bool 维度致崩:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    oob_dims = {(f.get("from"), f.get("to"), f.get("dimension")) for f in report["findings"]
                if f.get("code") == "RELATIONSHIP_OUT_OF_BOUND"}
    assert ("A", "B", "trust") not in oob_dims, f"bool 维度不应进越界判定: {oob_dims}"
    assert ("A", "B", "affinity") in oob_dims, f"affinity=11 真越界仍应报: {oob_dims}"


# ============ 健全性：正常数据照常工作（LEAP 仍报 = 没把检测改死） ============
def test_legit_leap_still_detected():
    """正常数值急变（trust 在相邻章 +1→+8，Δ=7≥5）仍照常报 RELATIONSHIP_LEAP，
    证明 Bug2 类型守卫没把合法数值序列一起过滤掉。"""
    chapter_changes = {
        1: [{"from": "A", "to": "B", "trust": 1}],
        2: [{"from": "A", "to": "B", "trust": 8}],   # Δ=7 ≥5 → LEAP
    }
    proj = _mk_project(
        rels_json={"relationships": [{"from": "A", "to": "B", "trust": 2}]},
        chapter_changes=chapter_changes,
    )
    r = _run(proj)
    assert "Traceback" not in r.stderr, f"scanner 崩溃:\n{r.stderr[-600:]}"
    report = _load_report(proj)
    assert "RELATIONSHIP_LEAP" in _codes(report), \
        f"合法急变(Δ=7)应报 LEAP，类型守卫不该误杀: {_codes(report)}"


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
