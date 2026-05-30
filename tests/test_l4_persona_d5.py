"""L4 · cross_cluster_persona_drift_aggregate D5 人设漂移曲线回归测试 —— 北极星①②⑤⑥。

钉死：在原 persona_drift 逐章 drift 数据上叠一层「跨 cluster 连续偏离度曲线」（非 0/1），
且**全 advisory · env 默认 shadow 只记不判 · 默认行为零回归 · 作者档基线第一权威**。

守护点：
  · 纯函数：_linreg_slope / build_d5_curves / build_d5_findings / compute_d5_author_band 确定性可测
  · 曲线是连续偏离度（mean/last/slope/volatility 多维），不是 0/1 阈值告警
  · D5 finding severity 恒 advisory · code=PERSONA_DRIFT_CURVE_TREND · 绝不进 hard_gate
  · 该 code 不在 audit_hub.HARD_GATE_CODES（北极星⑤ 新 code 绝不进 hard_gate）
  · 影子并行 env PERSONA_D5_MODE：
      - shadow（默认）→ D5 不进顶层 findings、不改 exit code（默认行为零回归）
      - active → D5 advisory 升顶层 findings（仍 advisory）
      - off → 完全不算 D5
  · 作者档第一权威：narrative_fingerprint 有 character_behavior_loops/depth → band._source=作者档；
    narrative_fingerprint 缺/空 → 通用兜底（不臆造）
  · 真原文矫枉过正金标准：蛊真人（空 narrative_fingerprint → 通用带）/ 惊悚乐园（有 → 作者档带）
    真作者档喂入 band 不崩、不产 hard_gate

只测确定性纯函数（无 LLM / agent）。守纪律：真作者原文档喂校验确认不误判（北极星纪律金标准）。
只跑本模块：python tests/test_l4_persona_d5.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_persona_drift_aggregate as m  # noqa: E402


# ---------- 纯函数：线性回归斜率 ----------

def test_linreg_slope_flat_zero():
    assert m._linreg_slope([1, 2, 3, 4], [0.3, 0.3, 0.3, 0.3]) == 0.0


def test_linreg_slope_rising_positive():
    s = m._linreg_slope([1, 2, 3, 4, 5], [0.2, 0.3, 0.4, 0.5, 0.6])
    assert round(s, 4) == 0.1


def test_linreg_slope_too_few_points():
    assert m._linreg_slope([1], [0.5]) == 0.0
    assert m._linreg_slope([], []) == 0.0


# ---------- 曲线：连续偏离度（非 0/1） ----------

def test_curve_is_continuous_multi_metric():
    pc = {1: [{"character": "A", "drift": 0.2}],
          2: [{"character": "A", "drift": 0.4}],
          3: [{"character": "A", "drift": 0.6}]}
    curves = m.build_d5_curves(pc)
    cv = curves["A"]
    # 连续多维指标，不是单个 0/1 标志
    assert cv["n"] == 3
    assert cv["mean"] == 0.4
    assert cv["last"] == 0.6
    assert cv["slope"] == 0.2
    assert cv["volatility"] == 0.2
    # 曲线点保留 ch + cluster_id（cluster 视野）+ drift
    assert [p["ch"] for p in cv["points"]] == [1, 2, 3]
    assert all("cluster_id" in p and "drift" in p for p in cv["points"])


def test_curve_ignores_non_numeric_drift():
    pc = {1: [{"character": "A", "drift": None}],
          2: [{"character": "A", "drift": 0.5}]}
    curves = m.build_d5_curves(pc)
    assert curves["A"]["n"] == 1


def test_curve_sorted_by_chapter():
    pc = {3: [{"character": "A", "drift": 0.6}],
          1: [{"character": "A", "drift": 0.2}],
          2: [{"character": "A", "drift": 0.4}]}
    curves = m.build_d5_curves(pc)
    assert [p["ch"] for p in curves["A"]["points"]] == [1, 2, 3]


# ---------- findings：全 advisory · 趋势/高位触发 ----------

def test_findings_rising_trend_advisory():
    pc = {i: [{"character": "A", "drift": 0.2 + 0.1 * i}] for i in range(5)}
    curves = m.build_d5_curves(pc)
    finds = m.build_d5_findings(curves, dict(m._D5_GENERIC_BAND))
    assert len(finds) == 1
    f = finds[0]
    assert f["code"] == "PERSONA_DRIFT_CURVE_TREND"
    assert f["severity"] == "advisory"
    assert f["character"] == "A"
    assert "curve" in f and isinstance(f["curve"], list)


def test_findings_stable_low_no_alert():
    pc = {i: [{"character": "A", "drift": 0.1}] for i in range(5)}
    curves = m.build_d5_curves(pc)
    finds = m.build_d5_findings(curves, dict(m._D5_GENERIC_BAND))
    assert finds == []  # 平稳低位 → 不告警


def test_findings_needs_three_points():
    pc = {1: [{"character": "A", "drift": 0.9}],
          2: [{"character": "A", "drift": 0.9}]}
    curves = m.build_d5_curves(pc)
    finds = m.build_d5_findings(curves, dict(m._D5_GENERIC_BAND))
    assert finds == []  # < 3 点不判趋势（避噪声）


def test_findings_never_hard_gate():
    pc = {i: [{"character": "A", "drift": 0.8}] for i in range(5)}
    curves = m.build_d5_curves(pc)
    finds = m.build_d5_findings(curves, dict(m._D5_GENERIC_BAND))
    assert finds  # 高位应触发
    assert "hard_gate" not in json.dumps(finds, ensure_ascii=False)
    assert all(f["severity"] == "advisory" for f in finds)


def test_new_code_not_in_audit_hub_hard_gate():
    """北极星⑤：D5 新 code 绝不进 audit_hub.HARD_GATE_CODES。"""
    import audit_hub  # noqa: E402
    assert "PERSONA_DRIFT_CURVE_TREND" not in audit_hub.HARD_GATE_CODES


# ---------- 作者档第一权威 band ----------

def _mk_style_project(tmp: Path, style=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if style is not None:
        (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return tmp


def test_band_generic_when_no_style():
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_style_project(Path(d))
        band = m.compute_d5_author_band(tmp)
        assert band["_source"] == "通用"
        assert band["warn_mean"] == m._D5_GENERIC_BAND["warn_mean"]


def test_band_generic_when_empty_narrative_fingerprint():
    """蛊真人式空 narrative_fingerprint={} → 通用兜底（不臆造作者）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_style_project(Path(d), style={"narrative_fingerprint": {}})
        band = m.compute_d5_author_band(tmp)
        assert band["_source"] == "通用"


def test_band_author_tightens_on_behavior_loops():
    """行为环命中多 → 人设稳 → 带收紧（warn_mean / warn_slope 降）。"""
    style = {"narrative_fingerprint": {
        "character_behavior_loops": {"观察→推理": 6, "试探→反杀": 4}}}
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_style_project(Path(d), style=style)
        band = m.compute_d5_author_band(tmp)
        assert band["_source"] == "作者档"
        assert band["warn_mean"] < m._D5_GENERIC_BAND["warn_mean"]


def test_band_author_widens_on_high_depth():
    """高深度角色占比高 → 作者刻意多面人设 → 带放宽（容忍合理起伏）。"""
    style = {"narrative_fingerprint": {
        "character_behavior_loops": {"观察→推理": 1},
        "character_depth_grade_distribution": {"极高": 5, "高": 5, "其他": 10}}}
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_style_project(Path(d), style=style)
        band = m.compute_d5_author_band(tmp)
        assert band["_source"] == "作者档"
        assert band["warn_mean"] > m._D5_GENERIC_BAND["warn_mean"]


def test_band_clamped_to_safe_range():
    """带始终钳在安全区间 [0.35,0.75] / [0.02,0.10]，极端基线不算出反直觉带。"""
    style = {"narrative_fingerprint": {
        "character_behavior_loops": {"a": 999},
        "character_depth_grade_distribution": {"极高": 999, "其他": 1}}}
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_style_project(Path(d), style=style)
        band = m.compute_d5_author_band(tmp)
        assert 0.35 <= band["warn_mean"] <= 0.75
        assert 0.02 <= band["warn_slope"] <= 0.10


# ---------- 影子并行 mode（默认 shadow · 零回归） ----------

def test_default_mode_is_shadow():
    assert m.PERSONA_D5_MODE in ("shadow", "active", "off")
    # 进程未显式设 env 时默认 shadow（运行测试时环境干净）
    if "PERSONA_D5_MODE" not in os.environ:
        assert m.PERSONA_D5_MODE == "shadow"


# ---------- 真原文矫枉过正（金标准） ----------

def _real_style_path(book: str) -> Path:
    return _ROOT / "workspace" / "styles" / book / "作者风格.json"


def test_real_author_guzhenren_band_generic():
    """蛊真人真作者档：narrative_fingerprint 为空 → band 走通用兜底、不崩、无 hard_gate。"""
    sp = _real_style_path("蛊真人")
    if not sp.exists():
        return
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_style_project(Path(d))
        (tmp / "_数据库" / "作者风格.json").write_text(
            sp.read_text(encoding="utf-8"), encoding="utf-8")
        band = m.compute_d5_author_band(tmp)
        # 蛊真人 narrative_fingerprint=[] / {} → 通用兜底（金标准：真作者不被臆造收紧）
        assert band["_source"] == "通用"
        assert "hard_gate" not in json.dumps(band, ensure_ascii=False)


def test_real_author_jingsong_band_author():
    """惊悚乐园真作者档：有 character_behavior_loops + depth → band._source=作者档、不崩。"""
    sp = _real_style_path("惊悚乐园")
    if not sp.exists():
        return
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_style_project(Path(d))
        (tmp / "_数据库" / "作者风格.json").write_text(
            sp.read_text(encoding="utf-8"), encoding="utf-8")
        band = m.compute_d5_author_band(tmp)
        assert band["_source"] == "作者档"
        assert "hard_gate" not in json.dumps(band, ensure_ascii=False)
        # 钳在安全区间
        assert 0.35 <= band["warn_mean"] <= 0.75


# ---------- runner（只跑本模块，不依赖 pytest） ----------

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
    print(f"\n[test_l4_persona_d5] {passed} passed / {failed} failed / {len(fns)} total")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
