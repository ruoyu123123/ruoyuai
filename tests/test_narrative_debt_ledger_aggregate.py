# -*- coding: utf-8 -*-
"""cross_cluster_narrative_debt_ledger_aggregate.py 单测（R7 Batch-D · 2026-06-20）。

确定性·零依赖·零 LLM/零联网。覆盖：
  ① compute_book_ledger 累计 stock+flow
  ② compute_volume_ledger 分卷独立
  ③ detect_findings：BOOK_MORTGAGE / VOLUME_RUNAWAY / VOLUME_OVERSHOOT 三条规则
  ④ off 模式骨架返回
  ⑤ shadow / active 模式 exit code
  ⑥ build_snapshot 字段稳定
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
_TESTS = _ROOT / "tests"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_TESTS))
import cross_cluster_narrative_debt_ledger_aggregate as mod  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_narrative_debt_ledger_aggregate.py"


def _utf8_env(**extra):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.update(extra)
    return env


def _mk_project(clusters):
    """造带 故事块摘要.json + 伏笔表.json 的项目目录。

    clusters = _cluster() 产出的测试记录（cluster_id/foreshadow_planted/foreshadow_paid）。
    故事块摘要.json 只写严格账本合同允许的字段；每条记录的 planted/paid 流水
    通过 伏笔表.json（setup_cluster + status）落盘，走生产代码真实的 SYS-5 ②
    回退通道——与 cross_cluster_narrative_debt_ledger_aggregate.py 的读取路径一致。
    """
    proj = Path(tempfile.mkdtemp(prefix="debt_ledger_"))
    database = proj / "_数据库"
    database.mkdir(parents=True, exist_ok=True)
    write_cluster_summary(proj, [cluster_record(c["cluster_id"]) for c in clusters])
    promises = []
    for c in clusters:
        cid = c["cluster_id"]
        paid = set(c.get("foreshadow_paid") or [])
        for fid in c.get("foreshadow_planted") or []:
            promises.append({
                "id": fid, "setup_cluster": cid,
                "status": "consumed" if fid in paid else "open",
            })
    (database / "伏笔表.json").write_text(
        json.dumps({"promises": promises, "deadlines": [], "pledges": [], "secrets": []},
                   ensure_ascii=False), encoding="utf-8")
    return proj


def _cluster(cid, planted=None, paid=None, vol=1, ch_range=(1, 3), status="done"):
    return {
        "cluster_id": cid,
        "title": cid,
        "chapter_range": list(ch_range),
        "cluster_end_ch": ch_range[1],
        "foreshadow_planted": list(planted or []),
        "foreshadow_paid": list(paid or []),
        "_me_volume": vol,
        "status": status,
        "chapters": {str(ch_range[0]): {}},  # 至少有一章避免被 filter
    }


# ── 单元：compute_book_ledger 累计 ─────────────────────────
def test_book_ledger_accumulates_stock_and_flow():
    clusters = [
        _cluster("cluster_001", planted=["f1", "f2"], paid=[]),
        _cluster("cluster_002", planted=["f3"], paid=["f1"]),
        _cluster("cluster_003", planted=["f4"], paid=["f2", "f3"]),
    ]
    book = mod.compute_book_ledger(clusters)
    assert book["total_planted"] == 4
    assert book["total_paid"] == 3
    assert book["open_debt"] == 1  # 仅 f4 未偿
    assert len(book["timeline"]) == 3
    # 末尾 cluster open_ratio = 1/4
    assert abs(book["timeline"][-1]["open_ratio"] - 0.25) < 1e-6


# ── 单元：compute_volume_ledger 分卷独立 ───────────────────
def test_volume_ledger_isolated_per_volume():
    clusters = [
        _cluster("cluster_001", planted=["f1"], vol=1),
        _cluster("cluster_002", planted=["f2"], paid=["f1"], vol=1),
        _cluster("cluster_003", planted=["g1", "g2"], vol=2),
    ]
    vols = mod.compute_volume_ledger(clusters)
    assert "1" in vols and "2" in vols
    assert vols["1"]["total_planted"] == 2 and vols["1"]["total_paid"] == 1
    assert vols["1"]["open_debt"] == 1
    assert vols["2"]["total_planted"] == 2 and vols["2"]["open_debt"] == 2


# ── 单元：BOOK_MORTGAGE_ABSENT 前 30% 零 planted ──────────
def test_finding_book_mortgage_absent():
    clusters = [
        _cluster("cluster_001", planted=[], paid=[]),
        _cluster("cluster_002", planted=[], paid=[]),
        _cluster("cluster_003", planted=[], paid=[]),
        _cluster("cluster_004", planted=["f1"], paid=[]),
        _cluster("cluster_005", planted=["f2"], paid=[]),
    ]
    book = mod.compute_book_ledger(clusters)
    vols = mod.compute_volume_ledger(clusters)
    findings = mod.detect_findings(book, vols, total_clusters=len(clusters))
    codes = [f["code"] for f in findings]
    assert mod.CODE_BOOK_MORTGAGE in codes


# ── 单元：VOLUME_TAIL_RUNAWAY open_ratio 连续上升 ─────────
def test_finding_volume_tail_runaway():
    # 卷 1 共 5 cluster · 60% 后 = cluster 4-5
    # 前面 still 还些·后段连续埋新且不还 → open_ratio 上升
    clusters = [
        _cluster("cluster_001", planted=["f1"], paid=[], vol=1),
        _cluster("cluster_002", planted=[], paid=["f1"], vol=1),
        _cluster("cluster_003", planted=["f2"], paid=[], vol=1),
        _cluster("cluster_004", planted=["f3"], paid=[], vol=1),
        _cluster("cluster_005", planted=["f4", "f5"], paid=[], vol=1),
        _cluster("cluster_006", planted=["f6", "f7"], paid=[], vol=1),
        _cluster("cluster_007", planted=["f8"], paid=[], vol=1),
    ]
    book = mod.compute_book_ledger(clusters)
    vols = mod.compute_volume_ledger(clusters)
    findings = mod.detect_findings(book, vols, total_clusters=len(clusters))
    codes = [f["code"] for f in findings]
    assert mod.CODE_VOL_RUNAWAY in codes or mod.CODE_VOL_OVERSHOOT in codes


# ── 单元：VOLUME_OVERSHOOT 卷末 open_ratio>60% ────────────
def test_finding_volume_overshoot():
    clusters = [
        _cluster("cluster_001", planted=["a", "b", "c", "d"], paid=["a"], vol=1),
    ]
    book = mod.compute_book_ledger(clusters)
    vols = mod.compute_volume_ledger(clusters)
    findings = mod.detect_findings(book, vols, total_clusters=len(clusters))
    codes = [f["code"] for f in findings]
    assert mod.CODE_VOL_OVERSHOOT in codes


# ── 单元：build_snapshot 字段稳定 ─────────────────────────
def test_build_snapshot_schema():
    clusters = [_cluster("cluster_001", planted=["a"], paid=[])]
    book = mod.compute_book_ledger(clusters)
    vols = mod.compute_volume_ledger(clusters)
    findings = mod.detect_findings(book, vols, total_clusters=1)
    snap = mod.build_snapshot(book, vols, findings)
    assert "book" in snap and "volumes" in snap and "advisory_codes" in snap
    assert snap["book"]["total_planted"] == 1
    assert isinstance(snap["volumes"], list)


# ── 单元：secrets_revealed 计入 paid ──────────────────────
def test_secrets_revealed_counted_as_paid():
    clusters = [
        {**_cluster("cluster_001", planted=[], paid=[]), "secrets_revealed": ["s1", "s2"]},
    ]
    book = mod.compute_book_ledger(clusters)
    # secrets 算 paid → total_paid 应 ≥ 2
    assert book["total_paid"] == 2


# ── CLI：off 模式骨架 ───────────────────────────────────
def test_cli_off_mode_zero_exit_no_report():
    proj = _mk_project([_cluster("cluster_001", planted=["a"])])
    env = _utf8_env(NARRATIVE_DEBT_MODE="off")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 0
    # off 模式不写 snapshot
    snap = proj / "_数据库" / ".cross_cluster_scan" / "narrative_debt_snapshot.json"
    assert not snap.exists()


# ── CLI：shadow 模式恒 exit 0（findings 不上报） ──────────
def test_cli_shadow_mode_zero_exit_even_with_findings():
    clusters = [
        _cluster("cluster_001", planted=[], paid=[]),
        _cluster("cluster_002", planted=[], paid=[]),
        _cluster("cluster_003", planted=[], paid=[]),
        _cluster("cluster_004", planted=["f1"], paid=[]),
    ]
    proj = _mk_project(clusters)
    env = _utf8_env(NARRATIVE_DEBT_MODE="shadow")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 0
    snap = proj / "_数据库" / ".cross_cluster_scan" / "narrative_debt_snapshot.json"
    assert snap.exists()
    snap_data = json.loads(snap.read_text(encoding="utf-8"))
    assert "book" in snap_data and "advisory_codes" in snap_data


# ── CLI：active 模式 advisory 退 1 ───────────────────────
def test_cli_active_mode_exits_1_on_advisory():
    clusters = [
        _cluster("cluster_001", planted=["a", "b", "c", "d"], paid=["a"], vol=1),
    ]
    proj = _mk_project(clusters)
    env = _utf8_env(NARRATIVE_DEBT_MODE="active")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 1


# ── 边界：空账本 ─────────────────────────────────────────
def test_empty_summary_no_crash():
    proj = _mk_project([])
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=_utf8_env(), encoding="utf-8")
    assert r.returncode == 0
    assert "SKIP" in r.stdout
