"""distill_track 元层追踪测试 — 守护 2026-05-31 防反复打转件。

只测**确定性纯函数层** + 一个 tmpdir IO 往返（ledger append/load）。零 LLM / 零网络 /
零 git 依赖（current_git_sha 不在测试里跑真 git，IO 往返用 --git-sha 注入固定值）。

覆盖任务书 5 点验收：
  1. ledger append（append_entry 不原地改 · load/save 往返）
  2. 基线比对（last_entry_for_ref 按 cluster_ref 分组取最近 · detect_regression）
  3. tolerance band（max(abs_floor, K×pooled_std) · 越噪越宽）
  4. 回归检测（掉幅超带=回归 · 带内=噪声 flat · 首条=baseline 不误报）
  5. advisory 不阻断（cmd_record / cmd_dashboard 永远 return 0）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import distill_track as dt


# ---- 统计纯函数 ----

def test_mean_basic():
    assert dt.mean([2, 4, 6]) == 4.0
    assert dt.mean([]) == 0.0


def test_sample_variance_single_run_is_zero():
    """单趟无法估方差 → 0（论文：runs<2 方差未知，下游退 abs_floor）。"""
    assert dt.sample_variance([70.0]) == 0.0
    assert dt.sample_variance([]) == 0.0


def test_sample_variance_unbiased_n_minus_1():
    # [70,72,74] 均值72 偏差平方和 4+0+4=8 / (3-1)=4.0
    assert abs(dt.sample_variance([70.0, 72.0, 74.0]) - 4.0) < 1e-9
    assert abs(dt.sample_std([70.0, 72.0, 74.0]) - 2.0) < 1e-9


def test_pooled_std_degrades_when_samples_thin():
    """两组都 <2 趟 → 自由度 0 → pooled_std=0（band 退 abs_floor）。"""
    assert dt.pooled_std(0.0, 1, 0.0, 1) == 0.0
    # 一组有方差一组单趟 → 用有方差那组
    assert abs(dt.pooled_std(4.0, 3, 0.0, 1) - 2.0) < 1e-9


def test_pooled_std_combines_two_groups():
    # 两组各 3 趟 var=4 和 var=4 → pooled var=4 → std=2
    assert abs(dt.pooled_std(4.0, 3, 4.0, 3) - 2.0) < 1e-9


# ---- build_entry ----

def test_build_entry_multi_run_aggregates():
    e = dt.build_entry("v8", "cluster_001", [70.0, 72.0, 74.0],
                       model="deepseek-v4-pro", git_sha="abc1234",
                       timestamp="2026-05-31T00:00:00+00:00")
    assert e["skill_version"] == "v8"
    assert e["cluster_ref"] == "cluster_001"
    assert e["sfs_mean"] == 72.0
    assert abs(e["variance"] - 4.0) < 1e-6
    assert e["runs"] == 3
    assert e["model"] == "deepseek-v4-pro"
    assert e["git_sha"] == "abc1234"
    assert e["sfs_scores"] == [70.0, 72.0, 74.0]


def test_build_entry_empty_raises():
    try:
        dt.build_entry("v1", "cluster_001", [])
        assert False, "空分应抛 ValueError"
    except ValueError:
        pass


def test_build_entry_single_run_variance_zero():
    e = dt.build_entry("v1", "cluster_001", [65.0])
    assert e["variance"] == 0.0
    assert e["runs"] == 1
    assert e["sfs_mean"] == 65.0


# ---- ledger append（不原地改 + schema 保留）----

def test_append_entry_does_not_mutate_input():
    base = {"schema_version": 1, "entries": [{"cluster_ref": "c1"}]}
    new = dt.append_entry(base, {"cluster_ref": "c2"})
    assert len(base["entries"]) == 1, "不得原地改输入 ledger"
    assert len(new["entries"]) == 2
    assert new["entries"][-1]["cluster_ref"] == "c2"
    assert new["schema_version"] == 1


def test_append_entry_on_empty_ledger():
    new = dt.append_entry({}, {"cluster_ref": "c1"})
    assert new["entries"] == [{"cluster_ref": "c1"}]
    assert new["schema_version"] == dt.SCHEMA_VERSION


# ---- last_entry_for_ref（按 cluster_ref 分组取最近）----

def test_last_entry_for_ref_picks_latest_same_ref():
    ledger = {"entries": [
        {"cluster_ref": "c1", "skill_version": "v1"},
        {"cluster_ref": "c2", "skill_version": "v1"},
        {"cluster_ref": "c1", "skill_version": "v2"},  # 最近的 c1
    ]}
    e = dt.last_entry_for_ref(ledger, "c1")
    assert e["skill_version"] == "v2"


def test_last_entry_for_ref_none_when_absent():
    ledger = {"entries": [{"cluster_ref": "c1"}]}
    assert dt.last_entry_for_ref(ledger, "c2") is None
    assert dt.last_entry_for_ref({}, "c1") is None


# ---- tolerance band ----

def test_tolerance_band_floor_when_no_prev():
    assert dt.tolerance_band(None, {"variance": 100, "runs": 3}) == dt.DEFAULT_ABS_FLOOR


def test_tolerance_band_uses_floor_when_low_noise():
    """低噪声（std 小）→ band = abs_floor（floor 兜底）。"""
    prev = {"variance": 0.04, "runs": 3}  # std=0.2
    cur = {"variance": 0.04, "runs": 3}
    band = dt.tolerance_band(prev, cur, abs_floor=2.0, std_k=1.5)
    assert band == 2.0  # 1.5×0.2=0.3 < 2.0 → 取 floor


def test_tolerance_band_widens_with_noise():
    """高噪声（std 大）→ band 跟着 K×pooled_std 放宽（高方差作者不误报）。"""
    prev = {"variance": 16.0, "runs": 3}  # std=4
    cur = {"variance": 16.0, "runs": 3}
    band = dt.tolerance_band(prev, cur, abs_floor=2.0, std_k=1.5)
    assert abs(band - 6.0) < 1e-6  # 1.5×4=6 > 2 → 取 6


# ---- detect_regression：核心防误报逻辑 ----

def test_detect_regression_baseline_first_entry():
    """首条无 prev → baseline · 绝不误报回归。"""
    cur = dt.build_entry("v0", "c1", [60.0, 62.0, 64.0])
    reg = dt.detect_regression(None, cur)
    assert reg["regressed"] is False
    assert reg["trend"] == "baseline"
    assert reg["prev_mean"] is None


def test_detect_regression_within_band_is_noise_flat():
    """掉分但落容差带内 → flat（视作噪声 · 不算回归 · 防单趟误报打转）。"""
    # 高方差：std=4 → band=6。掉 3 分 < 6 → flat
    prev = dt.build_entry("v1", "c1", [66.0, 70.0, 74.0])  # mean70 var16
    cur = dt.build_entry("v2", "c1", [63.0, 67.0, 71.0])   # mean67 var16 → Δ-3
    reg = dt.detect_regression(prev, cur)
    assert reg["regressed"] is False
    assert reg["trend"] == "flat"
    assert reg["delta"] == -3.0


def test_detect_regression_beyond_band_is_regression():
    """掉分超容差带 → 报回归（advisory）。"""
    # 低方差：std~1 → band=2。掉 8 分 >> 2 → 回归
    prev = dt.build_entry("v1", "c1", [71.0, 72.0, 73.0])  # mean72
    cur = dt.build_entry("v2", "c1", [63.0, 64.0, 65.0])   # mean64 → Δ-8
    reg = dt.detect_regression(prev, cur)
    assert reg["regressed"] is True
    assert reg["trend"] == "down"
    assert reg["delta"] == -8.0
    assert reg["band"] >= 2.0


def test_detect_regression_real_gain_up():
    """涨分超带 → up（真涨 · 不标回归）。"""
    prev = dt.build_entry("v1", "c1", [60.0, 61.0, 62.0])  # mean61
    cur = dt.build_entry("v2", "c1", [70.0, 71.0, 72.0])   # mean71 → Δ+10
    reg = dt.detect_regression(prev, cur)
    assert reg["regressed"] is False
    assert reg["trend"] == "up"
    assert reg["delta"] == 10.0


def test_detect_regression_high_variance_suppresses_false_alarm():
    """关键论文教训：高方差作者掉 5 分但 band=9 → 不误报（多趟方差兜底）。"""
    prev = dt.build_entry("v1", "c1", [60.0, 70.0, 80.0])  # std=10 var100
    cur = dt.build_entry("v2", "c1", [55.0, 65.0, 75.0])   # std=10 Δ-5
    reg = dt.detect_regression(prev, cur)  # band=1.5×10=15 > 5
    assert reg["regressed"] is False, "高方差下小掉分不应误报回归"
    assert reg["trend"] == "flat"


# ---- ledger_rows_for_dashboard（与 dashboard 共用纯逻辑）----

def test_dashboard_rows_per_ref_grouping_and_regression():
    ledger = {"entries": [
        dt.build_entry("v0", "c1", [70.0, 71.0, 72.0]),   # baseline c1
        dt.build_entry("v0", "c2", [50.0, 51.0, 52.0]),   # baseline c2（不同 ref 不污染 c1）
        dt.build_entry("v1", "c1", [62.0, 63.0, 64.0]),   # c1 掉 8 分 → 回归
    ]}
    rows = dt.ledger_rows_for_dashboard(ledger)
    assert len(rows) == 3
    # c1 第二条相对 c1 第一条（不是相对 c2）判定
    c1_second = rows[2]["regression"]
    assert c1_second["regressed"] is True
    assert abs(c1_second["prev_mean"] - 71.0) < 1e-6  # 比的是 c1 的 v0 不是 c2
    # 两条 baseline 都不回归
    assert rows[0]["regression"]["trend"] == "baseline"
    assert rows[1]["regression"]["trend"] == "baseline"


def test_dashboard_rows_filter_by_ref():
    ledger = {"entries": [
        dt.build_entry("v0", "c1", [70.0]),
        dt.build_entry("v0", "c2", [50.0]),
    ]}
    rows = dt.ledger_rows_for_dashboard(ledger, cluster_ref="c2")
    assert len(rows) == 1
    assert rows[0]["entry"]["cluster_ref"] == "c2"


# ---- IO 往返：record append → load → dashboard（永不阻断 return 0）----

def test_record_then_load_roundtrip_and_compare():
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "蛊真人"

        class A:  # 模拟 argparse Namespace
            project = str(proj)
            skill_version = "v0"
            cluster_ref = "cluster_001"
            model = "deepseek-v4-pro"
            sfs = [70.0, 71.0, 72.0]
            from_report = []
            git_sha = "sha0000"  # 注入固定 sha · 不跑真 git
            abs_floor = dt.DEFAULT_ABS_FLOOR
            std_k = dt.DEFAULT_STD_K

        rc = dt.cmd_record(A())
        assert rc == 0, "record 永远 return 0（advisory 不阻断）"

        p = dt.ledger_path(proj)
        assert p.exists(), "ledger 文件应被写出"
        led = json.loads(p.read_text(encoding="utf-8"))
        assert len(led["entries"]) == 1
        assert led["entries"][0]["sfs_mean"] == 71.0
        assert led["entries"][0]["git_sha"] == "sha0000"

        # 第二次：掉分 → 回归 advisory · 仍 return 0
        class B(A):
            skill_version = "v1"
            sfs = [60.0, 61.0, 62.0]
            git_sha = "sha1111"

        rc2 = dt.cmd_record(B())
        assert rc2 == 0
        led2 = json.loads(p.read_text(encoding="utf-8"))
        assert len(led2["entries"]) == 2  # append 不覆盖

        # dashboard 也永远 return 0
        class D:
            project = str(proj)
            cluster_ref = None
            abs_floor = dt.DEFAULT_ABS_FLOOR
            std_k = dt.DEFAULT_STD_K

        assert dt.cmd_dashboard(D()) == 0


def test_record_no_scores_returns_usage_error_not_crash():
    """无分 → return 1（参数缺失）· 不抛异常崩溃。"""
    with tempfile.TemporaryDirectory() as td:
        class A:
            project = td
            skill_version = "v0"
            cluster_ref = "c1"
            model = None
            sfs = []
            from_report = []
            git_sha = "x"
            abs_floor = dt.DEFAULT_ABS_FLOOR
            std_k = dt.DEFAULT_STD_K
        assert dt.cmd_record(A()) == 1


def test_dashboard_empty_ledger_returns_zero():
    with tempfile.TemporaryDirectory() as td:
        class D:
            project = td
            cluster_ref = None
            abs_floor = dt.DEFAULT_ABS_FLOOR
            std_k = dt.DEFAULT_STD_K
        assert dt.cmd_dashboard(D()) == 0


def test_load_ledger_corrupt_json_does_not_crash():
    """损坏 ledger → 按空账本处理 · 不抛（advisory 层鲁棒）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        p = dt.ledger_path(proj)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ not valid json", encoding="utf-8")
        led = dt.load_ledger(proj)
        assert led["entries"] == []


def test_scores_from_report_reads_sfs_quick():
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "r.json"
        rp.write_text(json.dumps({"sfs_quick": 73.5,
                                  "programmatic_score": {"total": 99}}),
                      encoding="utf-8")
        # 优先 sfs_quick
        assert dt._scores_from_report(rp) == 73.5


def test_scores_from_report_falls_back_to_programmatic_total():
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "r.json"
        rp.write_text(json.dumps({"programmatic_score": {"total": 68.2}}),
                      encoding="utf-8")
        assert dt._scores_from_report(rp) == 68.2


def test_scores_from_report_missing_returns_none():
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "r.json"
        rp.write_text(json.dumps({"nope": 1}), encoding="utf-8")
        assert dt._scores_from_report(rp) is None
