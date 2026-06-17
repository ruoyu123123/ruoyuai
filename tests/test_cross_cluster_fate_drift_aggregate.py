"""cross_cluster_fate_drift_aggregate.py 确定性单元测试（零 LLM / 零联网）。

钉死两块纯逻辑：
- `_anchor_ch_from_ledger`：cluster 账本取末 cluster 末章当漂移锚点（cluster_end_ch
  优先 / chapter_range[1] 回退 / 取不到 None / candidate 过滤）。
- `main` CLI：委托 fate_engine.drift 算 overdue → 严重度分级（overdue_by>=5 warning
  否则 advisory）→ 写报告 JSON + summary 计数 + 退出码（有 warning → 1，否则 0）。

`main` 内含 sys.exit，故走 subprocess 跑真 CLI（真 argparse + 真 fate_engine.drift +
真报告落盘），断言退出码 / stdout / 报告文件内容 —— 全程不 mock 被测逻辑。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cross_cluster_fate_drift_aggregate as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_fate_drift_aggregate.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具：造项目目录 / 账本 / 大势卡
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_ledger(proj: Path, clusters: list) -> None:
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters},
                   ensure_ascii=False),
        encoding="utf-8")


def _write_fate(proj: Path, events: list) -> None:
    (proj / "_数据库" / "大势卡.json").write_text(
        json.dumps({"major_events_pool": events}, ensure_ascii=False),
        encoding="utf-8")


def _completed(eid, ch, **extra):
    return {"id": eid, "status": "completed", "completed_at_ch": ch, **extra}


def _scheduled(eid, title, prereq, max_ch, **extra):
    """一个 scheduled ME，window 锚在 prereq 上，max_chapters=max_ch。"""
    return {"id": eid, "title": title, "status": "scheduled",
            "expected_window_after": {"event": prereq, "max_chapters": max_ch},
            **extra}


def _run_cli(proj: Path, *args):
    """跑真 CLI（CLUSTER_MODE 由调用方注入 env 不需要 —— 默认逐章 glob 路径）。

    注：子进程 stdout 走 Windows 控制台编码（非 UTF-8），用 errors='replace' 容错解码，
    断言只锚 returncode + 报告文件（报告显式 utf-8 落盘，是确定性权威输出）。
    """
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), *args],
        capture_output=True, cwd=str(_ROOT))
    p_stdout = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    p_stderr = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, p_stdout, p_stderr)


def _latest_report(proj: Path) -> dict:
    out_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(out_dir.glob("fate_drift_*.json"))
    assert reports, "未生成 fate_drift 报告"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# _anchor_ch_from_ledger —— 纯函数
# ══════════════════════════════════════════════════════════════════════════
def test_anchor_prefers_cluster_end_ch():
    """末 cluster 有 cluster_end_ch → 直接取它当锚点 ch。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger(proj, [
            {"cluster_id": "cluster_001", "chapter_range": [1, 4], "cluster_end_ch": 4},
            {"cluster_id": "cluster_002", "chapter_range": [5, 9], "cluster_end_ch": 9},
        ])
        assert mod._anchor_ch_from_ledger(proj) == 9


def test_anchor_falls_back_to_chapter_range_hi():
    """末 cluster 无 cluster_end_ch → 回退 chapter_range[1]。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger(proj, [
            {"cluster_id": "cluster_001", "chapter_range": [1, 6]},
        ])
        assert mod._anchor_ch_from_ledger(proj) == 6


def test_anchor_empty_ledger_returns_none():
    """无账本 / 无 cluster → None（调用方回退逐章 glob）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))  # 不写账本
        assert mod._anchor_ch_from_ledger(proj) is None
        _write_ledger(proj, [])
        assert mod._anchor_ch_from_ledger(proj) is None


def test_anchor_no_anchorable_fields_returns_none():
    """末 cluster 既无 cluster_end_ch 也无合法 chapter_range → None。

    注：get_clusters 只返回含 chapters 或 chapter_range 的真实落账 cluster，故用
    chapters 让它落账，但 chapter_range 给非法形态（长度不符）→ 无可取锚点。
    """
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger(proj, [
            {"cluster_id": "cluster_001", "chapters": {"1": {}}, "chapter_range": [1]},
        ])
        assert mod._anchor_ch_from_ledger(proj) is None


def test_anchor_skips_candidate_cluster():
    """candidate 雏形 cluster 被 reader 过滤 → 取前一个真实 cluster 的末章。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger(proj, [
            {"cluster_id": "cluster_001", "chapter_range": [1, 5], "cluster_end_ch": 5},
            {"cluster_id": "cluster_002", "chapter_range": [6, 8],
             "cluster_end_ch": 8, "status": "candidate"},
        ])
        # candidate 被滤掉 → 末个真实 cluster 是 001
        assert mod._anchor_ch_from_ledger(proj) == 5


# ══════════════════════════════════════════════════════════════════════════
# main CLI —— 真 argparse + 真 fate_engine.drift + 真报告落盘
# ══════════════════════════════════════════════════════════════════════════
def test_cli_warning_overdue_exits_1():
    """prereq 完成于 ch10，max_chapters=2，当前 ch20 → overdue_by=8(>=5) → warning → exit 1。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_fate(proj, [
            _completed("ME_A", 10),
            _scheduled("ME_B", "大转折", "ME_A", 2),
        ])
        p = _run_cli(proj, "--ch", "20")
        assert p.returncode == 1, f"stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["ch"] == 20
        assert report["summary"]["warning"] == 1
        assert report["summary"]["advisory"] == 0
        assert report["summary"]["total"] == 1
        f0 = report["findings"][0]
        assert f0["severity"] == "warning"
        assert f0["code"] == "FATE_EVENT_OVERDUE"
        assert f0["metric"]["overdue_by"] == 8
        assert "ME_B" in f0["message"] and "大转折" in f0["message"]


def test_cli_advisory_overdue_exits_0():
    """overdue_by=3(<5) → advisory（不致命）→ exit 0，但报告里 advisory 计数=1。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_fate(proj, [
            _completed("ME_A", 10),
            _scheduled("ME_B", "次转折", "ME_A", 5),  # ch18 - 10 = 8 > 5，overdue_by=3
        ])
        p = _run_cli(proj, "--ch", "18")
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["summary"]["advisory"] == 1
        assert report["summary"]["warning"] == 0
        assert report["findings"][0]["severity"] == "advisory"
        assert report["findings"][0]["metric"]["overdue_by"] == 3


def test_cli_healthy_no_overdue_exits_0():
    """未超窗（gap <= max_chapters）→ 0 项漂移 → exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_fate(proj, [
            _completed("ME_A", 10),
            _scheduled("ME_B", "按时事件", "ME_A", 8),  # ch15 - 10 = 5 <= 8 → 不漂移
        ])
        p = _run_cli(proj, "--ch", "15")
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["summary"]["total"] == 0
        assert report["findings"] == []
        assert report["summary"]["warning"] == 0
        assert report["summary"]["advisory"] == 0


def test_cli_severity_boundary_exactly_5_is_warning():
    """边界：overdue_by 恰好 == 5 → warning（>=5 判定，不是 >5）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_fate(proj, [
            _completed("ME_A", 10),
            _scheduled("ME_B", "边界事件", "ME_A", 3),  # ch18 - 10 = 8，overdue_by = 8-3 = 5
        ])
        p = _run_cli(proj, "--ch", "18")
        report = _latest_report(proj)
        assert report["findings"][0]["metric"]["overdue_by"] == 5
        assert report["findings"][0]["severity"] == "warning"
        assert p.returncode == 1


def test_cli_auto_resolves_ch_from_chapter_dirs():
    """无 --ch → 逐章 glob 取最大章号当锚点（无 cluster 模式时的默认路径）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # 造章目录：第001章 / 第012章 → 取 12
        for name in ("第001章", "第012章", "第007章"):
            (proj / "章节" / name).mkdir(parents=True)
        _write_fate(proj, [
            _completed("ME_A", 1),
            _scheduled("ME_B", "晚到事件", "ME_A", 2),  # gap=12-1=11，overdue_by=9 → warning
        ])
        p = _run_cli(proj)  # 不传 --ch
        report = _latest_report(proj)
        assert report["ch"] == 12  # 取最大章号
        assert report["summary"]["warning"] == 1
        assert p.returncode == 1


def test_cli_no_chapters_skips_gracefully():
    """无 cluster 账本 + 无章目录 → [SKIP] 无已写章节 → exit 0 不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_fate(proj, [_completed("ME_A", 1)])  # 大势卡在，但没章目录
        p = _run_cli(proj)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        # SKIP 分支（无章节）提前 exit 0，不进入 drift / 不生成报告目录
        assert not (proj / "_数据库" / ".cross_chapter_scan").exists()


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[cross_cluster_fate_drift_aggregate] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
