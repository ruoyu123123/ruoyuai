"""cluster_evaluator.py 专属回归测试（零依赖 · 零 LLM · 零联网）。

被测：故事块复刻 v2 章程 6 维评分器的**确定性核心算法**。

已有间接覆盖（test_distill_finalize_verify.py）只摸了 `DIM_LABELS` 常量顺序，
并通过 distill_finalize_verify.strict_gate_decision 间接消费 6 维报告 shape——
**从未真调用过任何 score_* / verdict / evaluate / cosine_sim / mae / load_json /
main() CLI**。本测试聚焦这些尚未被覆盖的核心逻辑，钉死：

  · cosine_sim / mae 的数学不变量（截尾对齐 / 零向量 / 空向量退化值）；
  · 6 个 score_* 维度的关键分支（exact/equivalent/different/missing、
    Jaccard、MAE→线性插值分段、voice ±20% 比、中性分语义）；
  · verdict_from_scores 的 6/4-5/≤3 阈值边界；
  · evaluate 装配 6 维报告的 schema 不变量 + weighted_total 算式；
  · main() CLI 退出码（缺文件 exit2 / --strict 非PASS exit2 / 正常落盘）。

main() 含 sys.exit，故走 subprocess 跑真 CLI（参照
tests/test_cross_cluster_fate_drift_aggregate.py 的 _run_cli 范式）。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_evaluator as ce  # noqa: E402

_TARGET = _SCRIPTS / "cluster_evaluator.py"


# ──────────────────────────────────────────────────────────────────────────
# cosine_sim —— 数学不变量
# ──────────────────────────────────────────────────────────────────────────
def test_cosine_sim_identical_is_one():
    """相同向量 cosine = 1.0（同向）。"""
    v = [1.0, 2.0, 3.0]
    assert abs(ce.cosine_sim(v, v) - 1.0) < 1e-9


def test_cosine_sim_orthogonal_is_zero():
    """正交向量 cosine = 0。"""
    assert abs(ce.cosine_sim([1.0, 0.0], [0.0, 1.0]) - 0.0) < 1e-9


def test_cosine_sim_scaled_is_one():
    """共线（成比例）向量 cosine = 1.0——只看方向不看模长。"""
    assert abs(ce.cosine_sim([1.0, 2.0], [2.0, 4.0]) - 1.0) < 1e-9


def test_cosine_sim_empty_and_zero_vector():
    """空向量 / 零向量 → 0.0（防 ZeroDivision）。"""
    assert ce.cosine_sim([], [1.0]) == 0.0
    assert ce.cosine_sim([1.0], []) == 0.0
    assert ce.cosine_sim([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_sim_truncates_to_shorter():
    """不等长 → 截到较短长度再算（[1,2,99] vs [1,2] 取前 2 维 → 1.0）。"""
    assert abs(ce.cosine_sim([1.0, 2.0, 99.0], [1.0, 2.0]) - 1.0) < 1e-9


# ──────────────────────────────────────────────────────────────────────────
# mae —— 平均绝对误差
# ──────────────────────────────────────────────────────────────────────────
def test_mae_basic_and_truncate():
    """逐元素绝对差均值；不等长截到较短长度。"""
    # |1-1|+|2-4| = 0+2 → /2 = 1.0
    assert abs(ce.mae([1.0, 2.0], [1.0, 4.0]) - 1.0) < 1e-9
    # 截到前 2 维：|0-0|+|1-0| = 1 → /2 = 0.5
    assert abs(ce.mae([0.0, 1.0, 5.0], [0.0, 0.0]) - 0.5) < 1e-9


def test_mae_empty_returns_one():
    """空向量 → 1.0（最大误差兜底，配合 scene 维 MAE≥0.5→0 分语义）。"""
    assert ce.mae([], [1.0]) == 1.0
    assert ce.mae([1.0], []) == 1.0


# ──────────────────────────────────────────────────────────────────────────
# 维 1: score_arc_shape —— exact / equivalent / different / missing
# ──────────────────────────────────────────────────────────────────────────
def test_arc_shape_exact_match():
    s, d = ce.score_arc_shape({"matched_reagan_shape": "Icarus"},
                              {"matched_reagan_shape": "Icarus"})
    assert s == 1.0 and d["verdict"] == "exact"


def test_arc_shape_equivalent_class():
    """Rags-to-Riches 与 Cinderella 同等价类 → 0.7 / equivalent。"""
    s, d = ce.score_arc_shape({"matched_reagan_shape": "Rags-to-Riches"},
                              {"matched_reagan_shape": "Cinderella"})
    assert s == 0.7 and d["verdict"] == "equivalent"


def test_arc_shape_different():
    """不同形状且非等价类 → 0.0 / different。"""
    s, d = ce.score_arc_shape({"matched_reagan_shape": "Icarus"},
                              {"matched_reagan_shape": "Oedipus"})
    assert s == 0.0 and d["verdict"] == "different"


def test_arc_shape_missing():
    """任一侧缺 shape（默认 Unknown）→ 0.0 / missing。"""
    s, d = ce.score_arc_shape({}, {"matched_reagan_shape": "Icarus"})
    assert s == 0.0 and d["verdict"] == "missing"
    s2, d2 = ce.score_arc_shape(None, None)
    assert s2 == 0.0 and d2["verdict"] == "missing"


# ──────────────────────────────────────────────────────────────────────────
# 维 2: score_emotion_curve —— cosine / 缺曲线
# ──────────────────────────────────────────────────────────────────────────
def test_emotion_curve_cosine_and_missing():
    s, d = ce.score_emotion_curve({"emotion_curve_normalized": [0.1, 0.5, 0.9]},
                                  {"emotion_curve_normalized": [0.1, 0.5, 0.9]})
    assert abs(s - 1.0) < 1e-9 and d["cosine"] == 1.0
    # 任一侧空 → 0.0 / missing_curve
    s2, d2 = ce.score_emotion_curve({"emotion_curve_normalized": []},
                                    {"emotion_curve_normalized": [0.1]})
    assert s2 == 0.0 and d2["verdict"] == "missing_curve"


# ──────────────────────────────────────────────────────────────────────────
# 维 3: score_connection_coverage —— Jaccard / 中性分语义
# ──────────────────────────────────────────────────────────────────────────
def _cont(types):
    return {"transitions": [{"connection_type": t} for t in types]}


def test_connection_jaccard():
    """ref={直接承接,时间跳跃} gen={直接承接,空间跳转} → inter=1 union=3 → 1/3。"""
    s, d = ce.score_connection_coverage(
        _cont(["直接承接", "时间跳跃"]), _cont(["直接承接", "空间跳转"]))
    assert abs(s - 1 / 3) < 1e-9
    assert d["jaccard"] == round(1 / 3, 3)
    assert d["missing_in_gen"] == ["时间跳跃"]
    assert d["extra_in_gen"] == ["空间跳转"]


def test_connection_no_ref_is_neutral_half():
    """ref 无衔接（数据缺失非复刻问题）→ 中性 0.5 / no_ref_connections。"""
    s, d = ce.score_connection_coverage(None, _cont(["直接承接"]))
    assert s == 0.5 and d["verdict"] == "no_ref_connections"
    s2, _ = ce.score_connection_coverage(_cont([]), _cont(["直接承接"]))
    assert s2 == 0.5


def test_connection_ref_but_no_gen_is_zero():
    """ref 有衔接但 gen 没有 → 0.0 / no_gen_connections（复刻丢了衔接）。"""
    s, d = ce.score_connection_coverage(_cont(["直接承接"]), _cont([]))
    assert s == 0.0 and d["verdict"] == "no_gen_connections"


# ──────────────────────────────────────────────────────────────────────────
# 维 4: score_kicker_distribution —— cosine / 缺钩子
# ──────────────────────────────────────────────────────────────────────────
def test_kicker_distribution_cosine_and_missing():
    s, d = ce.score_kicker_distribution({"kicker_count_per_chapter": [1, 2, 3]},
                                        {"kicker_count_per_chapter": [2, 4, 6]})
    assert abs(s - 1.0) < 1e-9  # 共线 → 1.0
    assert d["ref_total"] == 6.0 and d["gen_total"] == 12.0
    s2, d2 = ce.score_kicker_distribution({"kicker_count_per_chapter": []},
                                          {"kicker_count_per_chapter": [1]})
    assert s2 == 0.0 and d2["verdict"] == "missing_kickers"


# ──────────────────────────────────────────────────────────────────────────
# 维 5: score_scene_summary_ratio —— MAE 分段线性映射
# ──────────────────────────────────────────────────────────────────────────
def test_scene_ratio_mae_le_015_is_full():
    """MAE ≤ 0.15 → 1.0 满分（完全相同 MAE=0）。"""
    s, _ = ce.score_scene_summary_ratio({"scene_summary_ratio_per_chapter": [0.5, 0.6]},
                                        {"scene_summary_ratio_per_chapter": [0.5, 0.6]})
    assert s == 1.0


def test_scene_ratio_mae_ge_05_is_zero():
    """MAE ≥ 0.5 → 0.0（|0-1| 均值 = 1.0 ≥ 0.5）。"""
    s, d = ce.score_scene_summary_ratio({"scene_summary_ratio_per_chapter": [0.0, 0.0]},
                                        {"scene_summary_ratio_per_chapter": [1.0, 1.0]})
    assert s == 0.0 and d["mae"] == 1.0


def test_scene_ratio_mae_linear_interp():
    """0.15 < MAE < 0.5 → 线性插值。MAE=0.3 → 1-(0.3-0.15)/0.35 ≈ 0.5714。"""
    # ref=[0.0,0.0] gen=[0.3,0.3] → MAE=0.3
    s, d = ce.score_scene_summary_ratio({"scene_summary_ratio_per_chapter": [0.0, 0.0]},
                                        {"scene_summary_ratio_per_chapter": [0.3, 0.3]})
    expected = 1.0 - (0.3 - 0.15) / (0.5 - 0.15)
    assert abs(s - expected) < 1e-9
    assert d["mae"] == 0.3


def test_scene_ratio_missing_is_zero():
    s, d = ce.score_scene_summary_ratio({"scene_summary_ratio_per_chapter": []},
                                        {"scene_summary_ratio_per_chapter": [0.5]})
    assert s == 0.0 and d["verdict"] == "missing_ratios"


# ──────────────────────────────────────────────────────────────────────────
# 维 6: score_voice_pack —— ±20% 比 / 中性分 / 缺目录
# ──────────────────────────────────────────────────────────────────────────
def _write_char_arc(d: Path, fname: str, character: str, avg_len: float):
    (d / fname).write_text(
        json.dumps({"character": character,
                    "voice_signature": {"dialogue_avg_len": avg_len}},
                   ensure_ascii=False),
        encoding="utf-8")


def test_voice_pack_within_20pct_matches():
    """同名角色 voice 长度比在 [0.8,1.2] → match → score 1.0。"""
    with tempfile.TemporaryDirectory() as dd:
        ref = Path(dd) / "ref"; gen = Path(dd) / "gen"
        ref.mkdir(); gen.mkdir()
        _write_char_arc(ref, "甲_emotion_arc.json", "甲", 10.0)
        _write_char_arc(gen, "甲_emotion_arc.json", "甲", 11.0)  # ratio 1.1 → ok
        s, d = ce.score_voice_pack(ref, gen)
        assert s == 1.0
        assert d["matches"] == 1 and d["total"] == 1
        assert d["details"][0]["ok"] is True


def test_voice_pack_outside_20pct_fails():
    """长度比超出 ±20%（11→20 → ratio≈1.82）→ 不 match → score 0.0。"""
    with tempfile.TemporaryDirectory() as dd:
        ref = Path(dd) / "ref"; gen = Path(dd) / "gen"
        ref.mkdir(); gen.mkdir()
        _write_char_arc(ref, "甲_emotion_arc.json", "甲", 11.0)
        _write_char_arc(gen, "甲_emotion_arc.json", "甲", 20.0)
        s, d = ce.score_voice_pack(ref, gen)
        assert s == 0.0
        assert d["details"][0]["ok"] is False


def test_voice_pack_positional_fallback_when_name_differs():
    """复刻不复制角色名（v2 章程）→ 按顺位对齐第 N 个角色。
    ref '甲' 在 gen 找不到同名 → 取 gen 第 0 个（'乙'，长度同）→ 仍可比。"""
    with tempfile.TemporaryDirectory() as dd:
        ref = Path(dd) / "ref"; gen = Path(dd) / "gen"
        ref.mkdir(); gen.mkdir()
        _write_char_arc(ref, "甲_emotion_arc.json", "甲", 10.0)
        _write_char_arc(gen, "乙_emotion_arc.json", "乙", 10.0)  # 名不同·顺位 0 对齐
        s, d = ce.score_voice_pack(ref, gen)
        assert s == 1.0 and d["total"] == 1


def test_voice_pack_no_ref_dir_is_neutral():
    """ref 目录不存在/为空 → 中性 0.5；ref 有但 gen 空 → 偏弱 0.3。"""
    with tempfile.TemporaryDirectory() as dd:
        ref = Path(dd) / "ref"; gen = Path(dd) / "gen"
        ref.mkdir(); gen.mkdir()
        # 都空 → no_ref_character_arcs 中性 0.5
        s, d = ce.score_voice_pack(ref, gen)
        assert s == 0.5 and d["verdict"] == "no_ref_character_arcs"
        # ref 有、gen 空 → 0.3
        _write_char_arc(ref, "甲_emotion_arc.json", "甲", 10.0)
        s2, d2 = ce.score_voice_pack(ref, gen)
        assert s2 == 0.3 and d2["verdict"] == "no_gen_character_arcs"


# ──────────────────────────────────────────────────────────────────────────
# verdict_from_scores —— 6 / 4-5 / ≤3 阈值边界
# ──────────────────────────────────────────────────────────────────────────
def test_verdict_thresholds():
    assert ce.verdict_from_scores([0.7] * 6) == "PASS"          # 全 6 过（=0.7 边界过）
    assert ce.verdict_from_scores([0.7, 0.7, 0.7, 0.7, 0.7, 0.69]) == "WARN"  # 5 过
    assert ce.verdict_from_scores([0.7, 0.7, 0.7, 0.7, 0.0, 0.0]) == "WARN"   # 4 过
    assert ce.verdict_from_scores([0.7, 0.7, 0.7, 0.0, 0.0, 0.0]) == "FAIL"   # 3 过
    assert ce.verdict_from_scores([0.0] * 6) == "FAIL"          # 0 过


def test_verdict_threshold_is_inclusive_at_07():
    """恰好 0.7 算过（>= 不是 >）；0.6999 不算过。"""
    assert ce.verdict_from_scores([0.7] * 6) == "PASS"
    assert ce.verdict_from_scores([0.6999] * 6) == "FAIL"


# ──────────────────────────────────────────────────────────────────────────
# evaluate —— 装配 6 维报告 schema 不变量 + weighted_total
# ──────────────────────────────────────────────────────────────────────────
def test_evaluate_schema_and_weighted_total():
    """端到端装配：6 维全相同输入 → 多维满分 → PASS · weighted_total=各维均值*100。"""
    ref_arc = {
        "matched_reagan_shape": "Icarus",
        "emotion_curve_normalized": [0.1, 0.5, 0.9],
        "kicker_count_per_chapter": [1, 2, 3],
        "scene_summary_ratio_per_chapter": [0.5, 0.6, 0.4],
    }
    gen_arc = dict(ref_arc)
    report = ce.evaluate(ref_arc, gen_arc, None, None, None, None)
    # schema 不变量
    assert report["dims_total"] == 6
    assert len(report["scores_by_dim"]) == 6
    assert report["threshold_per_dim"] == 0.7
    assert report["schema_version"] == "1.0"
    assert [r["dim"] for r in report["scores_by_dim"]] == ce.DIM_LABELS
    # arc/emotion/kicker/scene 全 1.0；continuity 无 ref→0.5；voice 无目录→0.5
    by = {r["dim"]: r["score"] for r in report["scores_by_dim"]}
    assert by["arc 形状"] == 1.0
    assert by["emotion_curve 偏离"] == 1.0
    assert by["钩子分布"] == 1.0
    assert by["场景概述比"] == 1.0
    assert by["衔接模板覆盖"] == 0.5
    assert by["voice_pack 合规"] == 0.5
    # weighted_total = (1+1+0.5+1+1+0.5)/6 * 100 = 83.3
    assert report["weighted_total"] == round((1 + 1 + 0.5 + 1 + 1 + 0.5) / 6 * 100, 1)
    # 4 维 ≥0.7（continuity/voice 0.5 不过）→ WARN
    assert report["dims_passed"] == 4
    assert report["verdict"] == "WARN"


def test_evaluate_all_pass_gives_pass_verdict():
    """凑齐 continuity + voice 让 6 维全过 → PASS。"""
    ref_arc = {
        "matched_reagan_shape": "Man-in-a-Hole",
        "emotion_curve_normalized": [0.2, 0.8, 0.3],
        "kicker_count_per_chapter": [2, 1, 3],
        "scene_summary_ratio_per_chapter": [0.5, 0.5, 0.5],
    }
    gen_arc = dict(ref_arc)
    cont = _cont(["直接承接", "时间跳跃"])
    report = ce.evaluate(ref_arc, gen_arc, cont, dict(cont), None, None)
    by = {r["dim"]: r["score"] for r in report["scores_by_dim"]}
    assert by["衔接模板覆盖"] == 1.0  # 完全一致 Jaccard=1
    # voice 仍 0.5（无目录·中性）→ 5 维过 → WARN（守 6 维全过才 PASS）
    assert report["dims_passed"] == 5
    assert report["verdict"] == "WARN"


# ──────────────────────────────────────────────────────────────────────────
# load_json —— 缺文件 / 坏 JSON 不崩
# ──────────────────────────────────────────────────────────────────────────
def test_load_json_missing_and_malformed():
    with tempfile.TemporaryDirectory() as dd:
        d = Path(dd)
        assert ce.load_json(None) is None
        assert ce.load_json(d / "nope.json") is None  # 不存在
        bad = d / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert ce.load_json(bad) is None              # 坏 JSON 吞掉返回 None
        good = d / "good.json"
        good.write_text('{"x": 1}', encoding="utf-8")
        assert ce.load_json(good) == {"x": 1}


# ──────────────────────────────────────────────────────────────────────────
# main() CLI —— 退出码 + 报告落盘（subprocess 跑真 CLI）
# ──────────────────────────────────────────────────────────────────────────
def _run_cli(*args):
    p = subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, cwd=str(_ROOT))
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return p.returncode, out, err


def _write_arc(path: Path, shape="Icarus"):
    path.write_text(json.dumps({
        "matched_reagan_shape": shape,
        "emotion_curve_normalized": [0.1, 0.5, 0.9],
        "kicker_count_per_chapter": [1, 2, 3],
        "scene_summary_ratio_per_chapter": [0.5, 0.6, 0.4],
    }, ensure_ascii=False), encoding="utf-8")


def test_cli_missing_ref_arc_exits_2():
    """原 cluster_arc 不存在 → exit 2（硬错误）。"""
    with tempfile.TemporaryDirectory() as dd:
        d = Path(dd)
        gen = d / "gen.json"; _write_arc(gen)
        out_path = d / "report.json"
        rc, _, err = _run_cli("--ref-cluster-arc", str(d / "nope.json"),
                              "--gen-cluster-arc", str(gen),
                              "--output", str(out_path))
        assert rc == 2, err
        assert not out_path.exists()  # 硬错误前不应落盘报告


def test_cli_normal_run_writes_report_exit_0():
    """正常跑：两 arc 都在 → 落盘报告 + exit 0（默认非 strict）。"""
    with tempfile.TemporaryDirectory() as dd:
        d = Path(dd)
        ref = d / "ref.json"; gen = d / "gen.json"
        _write_arc(ref); _write_arc(gen)
        out_path = d / "sub" / "report.json"  # 触发 parent.mkdir
        rc, _, err = _run_cli("--ref-cluster-arc", str(ref),
                              "--gen-cluster-arc", str(gen),
                              "--output", str(out_path))
        assert rc == 0, err
        assert out_path.exists()
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert report["dims_total"] == 6
        assert report["verdict"] in ("PASS", "WARN", "FAIL")
        assert report["produced_by"].startswith("cluster_evaluator.py")


def test_cli_strict_non_pass_exits_2():
    """--strict 且 verdict != PASS（无 continuity/voice → 至多 4 维过 → WARN）→ exit 2。"""
    with tempfile.TemporaryDirectory() as dd:
        d = Path(dd)
        ref = d / "ref.json"; gen = d / "gen.json"
        _write_arc(ref); _write_arc(gen)
        out_path = d / "report.json"
        rc, _, err = _run_cli("--ref-cluster-arc", str(ref),
                              "--gen-cluster-arc", str(gen),
                              "--output", str(out_path),
                              "--strict")
        assert rc == 2, err
        # strict 失败前报告已落盘（写盘在 exit 前）
        assert out_path.exists()
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert report["verdict"] != "PASS"


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
    print(f"[cluster_evaluator] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
