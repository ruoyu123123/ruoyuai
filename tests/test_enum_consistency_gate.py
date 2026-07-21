"""G3-ENUMKAPPA 标注一致性前置闸测试（2026-07-18·确定性·zero-LLM）。

守护：
  1. fleiss_kappa：全一致→1.0，完全随机/均匀分布→趋近 0；
  2. align_by_pct_bucket：跨采样按 pct 分桶对齐，单份采样覆盖的桶不计入；
  3. run_enum_kappa_gate：采样数<3 / 有效点<3 → low_confidence 保守拒绝；
     众数占比或 kappa 达标 → PASS；均不达标 → FAIL；
  4. CLI 恒 exit 0（advisory·不抛错）+ 报告落盘 _数据库/.enum_consistency/。
零依赖（stdlib）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import enum_consistency_gate as gate  # noqa: E402


def test_fleiss_kappa_full_agreement():
    rows = [{"suspense": 3}, {"curiosity": 3}, {"surprise": 3}]
    assert gate.fleiss_kappa(rows) == 1.0


def test_fleiss_kappa_uniform_random_near_zero():
    # 3 raters/item，类别在 item 间均匀打散（模拟真随机标注）。
    rows = [
        {"suspense": 1, "curiosity": 1, "surprise": 1},
        {"suspense": 1, "curiosity": 1, "surprise": 1},
        {"suspense": 1, "curiosity": 1, "surprise": 1},
        {"suspense": 1, "curiosity": 1, "surprise": 1},
    ]
    k = gate.fleiss_kappa(rows)
    assert k < 0.2


def test_fleiss_kappa_too_few_items_returns_zero():
    assert gate.fleiss_kappa([{"suspense": 3}]) == 0.0
    assert gate.fleiss_kappa([]) == 0.0


def test_tension_type_bucket_keyword_fallback():
    assert gate._tension_type_bucket("suspense") == "suspense"
    assert gate._tension_type_bucket("读者已知有炸弹还没爆") == "suspense"
    assert gate._tension_type_bucket("读者想知道到底发生了什么") == "curiosity"
    assert gate._tension_type_bucket("结局大反转打脸预期") == "surprise"
    assert gate._tension_type_bucket("纯过场无信息差") == "未分类"


def test_gap_code_bucket_rejects_unknown():
    assert gate._gap_code_bucket("reader_adv") == "reader_adv"
    assert gate._gap_code_bucket("garbage") == "未分类"


def test_align_by_pct_bucket_requires_multi_sample_coverage():
    samples = [
        [(5.0, "suspense"), (55.0, "curiosity")],
        [(8.0, "suspense")],  # 只有第一份和这份都覆盖 0-10% 桶
    ]
    rows = gate.align_by_pct_bucket(samples, bucket_size=10)
    # 0-10% 桶两份采样都有点 → 计入；50-60% 桶只第一份有 → 不计入
    assert rows == [{"suspense": 2}]


def _write_surface(d: Path, name: str, dim51_points=None, dim32_points=None):
    payload = {"qualitative_dims": {}}
    if dim51_points is not None:
        payload["qualitative_dims"]["dim51_张力曲线"] = dim51_points
    if dim32_points is not None:
        payload["qualitative_dims"]["dim32_信息差管理"] = {"per_point": dim32_points}
    (d / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_run_enum_kappa_gate_low_confidence_under_3_samples():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write_surface(d, "s1.json", dim51_points=[{"pct": 10, "tension_type": "suspense"}])
        result = gate.run_enum_kappa_gate([d / "s1.json"], dim="tension_type")
        assert result["low_confidence"] is True
        assert result["should_inject"] is False


def test_run_enum_kappa_gate_pass_on_high_agreement():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for i in range(5):
            _write_surface(d, f"s{i}.json", dim51_points=[
                {"pct": 10, "tension_type": "suspense"},
                {"pct": 40, "tension_type": "curiosity"},
                {"pct": 70, "tension_type": "surprise"},
            ])
        paths = [d / f"s{i}.json" for i in range(5)]
        result = gate.run_enum_kappa_gate(paths, dim="tension_type")
        assert result["should_inject"] is True
        assert result["fleiss_kappa"] == 1.0


def test_run_enum_kappa_gate_fail_on_disagreement():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        cats = ["suspense", "curiosity", "surprise"]
        for i in range(6):
            _write_surface(d, f"s{i}.json", dim51_points=[
                {"pct": 10, "tension_type": cats[i % 3]},
                {"pct": 40, "tension_type": cats[(i + 1) % 3]},
                {"pct": 70, "tension_type": cats[(i + 2) % 3]},
            ])
        paths = [d / f"s{i}.json" for i in range(6)]
        result = gate.run_enum_kappa_gate(paths, dim="tension_type")
        assert result["should_inject"] is False
        assert result["low_confidence"] is False


def test_run_enum_kappa_gate_gap_code_dim():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for i in range(4):
            _write_surface(d, f"s{i}.json", dim32_points=[
                {"gap_code": "reader_adv", "confidence": "high"},
                {"gap_code": "double_blind", "confidence": "high"},
                {"gap_code": "reader_disadv", "confidence": "high"},
            ])
        paths = [d / f"s{i}.json" for i in range(4)]
        result = gate.run_enum_kappa_gate(paths, dim="gap_code")
        assert result["should_inject"] is True


def test_cli_exit_zero_and_report_written(capsys):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for i in range(3):
            _write_surface(d, f"s{i}.json", dim51_points=[
                {"pct": 20, "tension_type": "suspense"},
                {"pct": 60, "tension_type": "curiosity"},
            ])
        paths = [str(d / f"s{i}.json") for i in range(3)]
        rc = gate.main([str(d), "--cluster", "cluster_001", "--dim", "tension_type",
                         "--surface", *paths])
        assert rc == 0
        report = d / "_数据库" / ".enum_consistency" / "cluster_001_g3_tension_type.json"
        assert report.exists()
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["dim"] == "tension_type"
        assert "should_inject" in data


def test_cli_missing_surface_files_still_exits_zero():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        rc = gate.main([str(d), "--cluster", "cluster_002", "--dim", "gap_code",
                         "--surface", str(d / "nope1.json"), str(d / "nope2.json"),
                         str(d / "nope3.json")])
        assert rc == 0
