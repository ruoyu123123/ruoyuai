"""cross_cluster_persona_drift_aggregate.py 专属确定性回归测试（零 LLM / 零联网）。

钉死本脚本两块**尚未被现有测试覆盖**的核心确定性逻辑：

1. `_build_findings`（旧 PERSONA_DRIFT_DETECTED 逐章阈值判定 · 现有 test_l4_persona_d5
   完全没碰）—— drift>0.5 才产 finding · drift>0.75 → warning 否则 advisory ·
   None drift 跳过 · finding 字段（code/chapter/metric/message/suggestion）形态。

2. `main` CLI 端到端（现有测试完全没碰）—— 走 cluster 账本分支（CLUSTER_MODE=1 +
   故事块摘要.json 带 persona_drift 字段），不打 embedding/不联网，断言：
     · 退出码：warning → 2 / advisory → 1 / healthy → 0
     · 报告 JSON（per_chapter 剥 metric / summary 计数 / findings）
     · D5 影子并行 mode：shadow（默认）D5 不进顶层 findings 不改退出码；
       active 把 D5 advisory 升顶层 findings；off 完全不算 D5
     · SKIP 分支：账本无 persona_drift 记录 → exit 0 不生成报告

`main` 含 sys.exit，故走 subprocess 跑真 CLI（参照 tests/test_cross_cluster_fate_drift_aggregate.py·稳）。
现有 test_l4_persona_d5 已覆盖 D5 纯函数（_linreg_slope/build_d5_curves/build_d5_findings/
compute_d5_author_band/band 作者档），本文件不重复，只补 _build_findings + main CLI。

零 pytest · 标准库 · test_* 无参 · Windows · 只跑本模块：
    python tests/test_cross_cluster_persona_drift_aggregate.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cross_cluster_persona_drift_aggregate as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_persona_drift_aggregate.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具：造项目 + cluster 账本（带 persona_drift 字段，喂 main 的 cluster 分支）
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_ledger_with_drift(proj: Path, clusters: list) -> None:
    """clusters: [{"cluster_id","chapter_range","chapters":{"<ch>":{"persona_drift":{char:drift}}}}]"""
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters},
                   ensure_ascii=False),
        encoding="utf-8")


def _cluster(cid, lo, hi, ch_drifts):
    """ch_drifts: {ch(int): {char: drift}} → 落成 chapters 子结构带 persona_drift。"""
    chapters = {str(ch): {"persona_drift": dm} for ch, dm in ch_drifts.items()}
    return {"cluster_id": cid, "chapter_range": [lo, hi], "chapters": chapters}


def _run_cli(proj: Path, *args, mode=None):
    """跑真 CLI（cluster 分支需 CLUSTER_MODE=1）。PERSONA_D5_MODE 可注入。

    stdout 走 Windows 控制台编码，用 errors='replace' 容错；断言锚 returncode +
    报告文件（报告显式 utf-8 落盘，是确定性权威输出）。
    """
    env = dict(os.environ)
    env["CLUSTER_MODE"] = "1"
    if mode is not None:
        env["PERSONA_D5_MODE"] = mode
    else:
        # 默认清掉外部注入，确保走脚本内默认 shadow
        env.pop("PERSONA_D5_MODE", None)
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), *args],
        capture_output=True, cwd=str(_ROOT), env=env)
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def _latest_report(proj: Path) -> dict:
    out_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(out_dir.glob("persona_drift_*.json"))
    assert reports, "未生成 persona_drift 报告"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# _build_findings —— 旧逐章阈值判定（纯函数 · 现有测试零覆盖）
# ══════════════════════════════════════════════════════════════════════════
def test_build_findings_below_threshold_no_finding():
    """drift <= 0.5 → 不产 finding（阈值是 > 0.5，非 >=）。"""
    pc = {1: [{"character": "甲", "drift": 0.5}],
          2: [{"character": "甲", "drift": 0.3}]}
    assert mod._build_findings(pc) == []


def test_build_findings_advisory_band():
    """0.5 < drift <= 0.75 → advisory。"""
    pc = {1: [{"character": "甲", "drift": 0.6}]}
    finds = mod._build_findings(pc)
    assert len(finds) == 1
    f = finds[0]
    assert f["severity"] == "advisory"
    assert f["code"] == "PERSONA_DRIFT_DETECTED"
    assert f["chapter"] == 1
    assert "甲" in f["message"]
    assert "suggestion" in f and "甲" in f["suggestion"]


def test_build_findings_warning_band():
    """drift > 0.75 → warning。"""
    pc = {7: [{"character": "乙", "drift": 0.8}]}
    finds = mod._build_findings(pc)
    assert len(finds) == 1
    assert finds[0]["severity"] == "warning"
    assert finds[0]["chapter"] == 7


def test_build_findings_warning_boundary_exactly_075_is_advisory():
    """边界：drift 恰好 == 0.75 → advisory（warning 是 > 0.75 非 >=）。"""
    pc = {1: [{"character": "甲", "drift": 0.75}]}
    finds = mod._build_findings(pc)
    assert len(finds) == 1
    assert finds[0]["severity"] == "advisory"


def test_build_findings_skips_none_drift():
    """drift=None → 跳过（不崩、不产 finding）。"""
    pc = {1: [{"character": "甲", "drift": None},
              {"character": "乙", "drift": 0.9}]}
    finds = mod._build_findings(pc)
    # 只有「乙」产 finding（甲 None 跳过）
    assert len(finds) == 1
    assert finds[0]["metric"]["character"] == "乙" or "乙" in finds[0]["message"]


def test_build_findings_sorted_by_chapter_and_metric_passthrough():
    """多章 + 多角色 → 按章号升序产出 · metric 字段原样带出。"""
    pc = {3: [{"character": "甲", "drift": 0.9, "metric": {"drift": 0.9, "src": "x"}}],
          1: [{"character": "乙", "drift": 0.6}]}
    finds = mod._build_findings(pc)
    assert [f["chapter"] for f in finds] == [1, 3]
    # 第 3 章的 finding 带入了显式传的 metric
    f3 = [f for f in finds if f["chapter"] == 3][0]
    assert f3["metric"].get("src") == "x"


# ══════════════════════════════════════════════════════════════════════════
# main CLI —— cluster 账本分支（真 argparse + 真账本读取 + 真报告落盘）
# ══════════════════════════════════════════════════════════════════════════
def test_cli_warning_drift_exits_2():
    """drift > 0.75 → warning → exit 2 · 报告 summary.warning=1 · per_chapter 剥 metric。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger_with_drift(proj, [
            _cluster("cluster_001", 1, 2, {1: {"陆参": 0.82}, 2: {"陆参": 0.3}}),
        ])
        p = _run_cli(proj)
        assert p.returncode == 2, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["scan_type"] == "persona_drift"
        assert report["summary"]["warning"] == 1
        assert report["summary"]["advisory"] == 0
        assert report["summary"]["total"] == 1
        assert report["findings"][0]["severity"] == "warning"
        assert report["findings"][0]["code"] == "PERSONA_DRIFT_DETECTED"
        # per_chapter 输出只剩 character+drift（metric 被剥）
        pc_out = report["per_chapter"]
        ch1 = pc_out["1"]
        assert ch1 == [{"character": "陆参", "drift": 0.82}]
        assert "metric" not in json.dumps(ch1, ensure_ascii=False)


def test_cli_advisory_drift_exits_1():
    """0.5 < drift <= 0.75 → advisory → exit 1。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger_with_drift(proj, [
            _cluster("cluster_001", 1, 1, {1: {"陆参": 0.6}}),
        ])
        p = _run_cli(proj)
        assert p.returncode == 1, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["summary"]["advisory"] == 1
        assert report["summary"]["warning"] == 0
        assert report["findings"][0]["severity"] == "advisory"


def test_cli_healthy_low_drift_exits_0():
    """所有 drift <= 0.5 → 0 项 finding → exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger_with_drift(proj, [
            _cluster("cluster_001", 1, 2, {1: {"陆参": 0.2}, 2: {"陆参": 0.4}}),
        ])
        p = _run_cli(proj)
        assert p.returncode == 0, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["findings"] == []
        assert report["summary"]["total"] == 0
        # chapters_scanned 仍记录扫到的章
        assert report["chapters_scanned"] == [1, 2]


def test_cli_skip_when_no_persona_drift_records():
    """cluster 模式但账本无 persona_drift 字段 → ledger_has_field=False → 落到磁盘分支；
    磁盘分支无 .embeddings → [SKIP] → exit 0 不生成报告目录。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # 账本有 cluster 但 chapters 不带 persona_drift
        (proj / "_数据库" / "故事块摘要.json").write_text(
            json.dumps({"schema_version": "v2.cluster", "clusters": [
                {"cluster_id": "cluster_001", "chapter_range": [1, 2],
                 "chapters": {"1": {}, "2": {}}}]}, ensure_ascii=False),
            encoding="utf-8")
        p = _run_cli(proj)
        assert p.returncode == 0, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        # SKIP（embeddings 不存在）提前退出，不落报告
        assert not (proj / "_数据库" / ".cross_chapter_scan").exists()


def test_cli_d5_shadow_default_not_in_top_findings():
    """默认 shadow：drift 全部低位(不触发旧 PERSONA_DRIFT_DETECTED)，但 D5 曲线高位上行；
    shadow 下 D5 advisory 只进 d5_shadow_findings，不进顶层 findings、不改退出码(=0)。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # 5 章 drift 都 <=0.5（旧逻辑不告警）但呈持续上行 slope=0.04 → D5 rising 触发
        _write_ledger_with_drift(proj, [
            _cluster("cluster_001", 1, 5, {
                1: {"陆参": 0.34}, 2: {"陆参": 0.38}, 3: {"陆参": 0.42},
                4: {"陆参": 0.46}, 5: {"陆参": 0.50}}),
        ])
        p = _run_cli(proj, mode="shadow")
        assert p.returncode == 0, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        # 顶层 findings 为空（旧逻辑无 >0.5）
        assert report["findings"] == []
        assert report["d5_persona_drift_curve"]["mode"] == "shadow"
        assert report["d5_persona_drift_curve"]["gate_level"] == "advisory"
        # D5 曲线挂在报告里（影子层有内容但不进顶层）
        assert "陆参" in report["d5_persona_drift_curve"]["curves"]
        # D5 trend finding 真的算出来了且只进影子段（证明 shadow 隔离生效）
        shadow_codes = [f["code"] for f in report["d5_shadow_findings"]]
        assert "PERSONA_DRIFT_CURVE_TREND" in shadow_codes


def test_cli_d5_active_promotes_to_top_findings_exit_1():
    """active：同样的高位上行曲线 → D5 advisory 升顶层 findings → exit 1（仍 advisory）。
    对照 shadow 用例（同数据 exit 0）证明 mode 真的改变了顶层行为。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger_with_drift(proj, [
            _cluster("cluster_001", 1, 5, {
                1: {"陆参": 0.34}, 2: {"陆参": 0.38}, 3: {"陆参": 0.42},
                4: {"陆参": 0.46}, 5: {"陆参": 0.50}}),
        ])
        p = _run_cli(proj, mode="active")
        assert p.returncode == 1, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        codes = [f["code"] for f in report["findings"]]
        assert "PERSONA_DRIFT_CURVE_TREND" in codes
        # active 升进顶层的 D5 finding 仍是 advisory（绝不 hard_gate / warning）
        d5 = [f for f in report["findings"] if f["code"] == "PERSONA_DRIFT_CURVE_TREND"][0]
        assert d5["severity"] == "advisory"
        assert report["d5_persona_drift_curve"]["mode"] == "active"


def test_cli_d5_off_no_curve_computed():
    """off：完全不算 D5 → curves 为空 · d5_shadow_findings 为空（纯旧 persona_drift 行为）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_ledger_with_drift(proj, [
            _cluster("cluster_001", 1, 5, {
                1: {"陆参": 0.34}, 2: {"陆参": 0.38}, 3: {"陆参": 0.42},
                4: {"陆参": 0.46}, 5: {"陆参": 0.50}}),
        ])
        p = _run_cli(proj, mode="off")
        assert p.returncode == 0, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["d5_persona_drift_curve"]["mode"] == "off"
        assert report["d5_persona_drift_curve"]["curves"] == {}
        assert report["d5_shadow_findings"] == []


# ──────────────────────────────────────────────────────────────────────────
# runner（只跑本模块，不依赖 pytest）
# ──────────────────────────────────────────────────────────────────────────
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
        except Exception as e:
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"[cross_cluster_persona_drift_aggregate] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
