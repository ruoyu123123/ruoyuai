"""judge_consensus 回归测试 — 多 Judge 共识仲裁器的纯逻辑（零 LLM / 零网络）。

钉死中位裁决（偶数保守取低 + 钳位）、一致度、persona 分组/分歧、以及
merge_reports 的多条升级（escalate）触发条件，防重构静默回退。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import judge_consensus as jc  # noqa: E402


def test_median_grade_odd_and_majority():
    """奇数个 judge → 取中位；全相同 → 原样返回。"""
    # A=4,B=3 → sorted [3,4,4]，奇数中位 nums[1]=4=A
    assert jc.median_grade(["A", "B", "A"]) == "A"
    assert jc.median_grade(["A", "A", "A"]) == "A"
    assert jc.median_grade(["B", "B", "C"]) == "B"   # [2,3,3] med=3=B
    assert jc.median_grade([]) == "C"                 # 空 → 默认 C


def test_median_grade_even_is_conservative():
    """偶数个 judge：取两中位平均并向下取整（宁低勿高 self-protection）。"""
    # ['A','C'] = [2,4] → floor((2+4)/2)=3=B
    assert jc.median_grade(["A", "C"]) == "B"
    # ['B','D'] = [1,3] → floor((1+3)/2)=2=C
    assert jc.median_grade(["B", "D"]) == "C"
    # ['A','B'] = [3,4] → floor((3+4)/2)=floor(3.5)=3=B（不会偏高到 A）
    assert jc.median_grade(["A", "B"]) == "B"


def test_median_grade_unknown_grade_clamped():
    """非法 grade 视为 2(C)；结果钳到 [1,4] 合法区间。"""
    # 'X' → 2，['X','X'] = [2,2] → floor(2)=2=C
    assert jc.median_grade(["X", "X"]) == "C"
    # 全 D：[1,1] → floor(1)=1=D，确认下界不越界
    assert jc.median_grade(["D", "D"]) == "D"


def test_agreement_score():
    """相同 grade 占比；函数本身不 round（round 在 merge 层），空列表 → 0.0。"""
    assert jc.agreement_score(["A", "A", "A"]) == 1.0
    assert abs(jc.agreement_score(["A", "A", "C"]) - 2 / 3) < 1e-9  # 原始浮点 0.666...
    assert jc.agreement_score(["A", "C"]) == 0.5
    assert abs(jc.agreement_score(["A", "B", "C"]) - 1 / 3) < 1e-9
    assert jc.agreement_score([]) == 0.0


def test_persona_breakdown_groups_and_default():
    """缺 persona → 'default'；按组算平均 grade。"""
    reports = [
        {"persona": "common_reader", "overall_grade": "A"},
        {"persona": "harsh_critic", "overall_grade": "C"},
        {"overall_grade": "B"},  # 无 persona → default
    ]
    by = jc.persona_breakdown(reports)
    assert set(by.keys()) == {"common_reader", "harsh_critic", "default"}
    assert by["common_reader"]["count"] == 1
    assert by["common_reader"]["avg_grade_num"] == 4.0
    assert by["common_reader"]["avg_grade"] == "A"
    assert by["harsh_critic"]["avg_grade"] == "C"
    assert by["default"]["avg_grade"] == "B"


def test_persona_dissent_severity():
    """跨 persona 极差 = max-min 的 grade level；<2 组 → 0。"""
    by = jc.persona_breakdown([
        {"persona": "common_reader", "overall_grade": "A"},  # 4.0
        {"persona": "harsh_critic", "overall_grade": "C"},   # 2.0
    ])
    assert jc.persona_dissent_severity(by) == 2.0
    # 单组无对比基础 → 0
    single = jc.persona_breakdown([{"persona": "x", "overall_grade": "A"}])
    assert jc.persona_dissent_severity(single) == 0.0
    # 空 breakdown → 0
    assert jc.persona_dissent_severity({}) == 0.0


def test_merge_reports_empty():
    """空 reports → error 短路。"""
    assert jc.merge_reports([]) == {"error": "no reports"}


def test_merge_reports_happy_consensus_no_escalate():
    """高分高信心 + 充分 evidence + 一致 → 不升级。"""
    base_ev = ["原文quote一", "原文quote二"]
    reports = [
        {"judge_id": "j1", "overall_grade": "A", "confidence": 0.9,
         "chapter": 3, "evidence_quotes": base_ev},
        {"judge_id": "j2", "overall_grade": "A", "confidence": 0.85,
         "chapter": 3, "evidence_quotes": base_ev},
        {"judge_id": "j3", "overall_grade": "A", "confidence": 0.88,
         "chapter": 3, "evidence_quotes": base_ev},
    ]
    out = jc.merge_reports(reports)
    assert out["consensus_grade"] == "A"
    assert out["agreement_score"] == 1.0
    assert out["chapter"] == 3
    assert out["escalate_to_user"] is False
    assert out["escalate_reasons"] == []
    assert out["evidence_quality"] == 1.0
    assert out["reports_with_evidence"] == 3
    assert out["grades_distribution"] == {"A": 3}
    assert out["dissent"] == []
    assert out["schema_version"] == "1.3"
    cf = out["calibration_features"]
    assert cf["feature_schema"] == "judge_reliability_calibration_v1"
    assert cf["model_status"] == "shadow_features_only"
    assert cf["gate_level"] == "advisory"
    assert cf["n_reports"] == 3
    assert cf["mean_evidence_quotes"] == 2.0
    assert cf["escalate_to_user"] is False


def test_merge_reports_d_grade_forces_escalate_and_dissent():
    """任一 judge 给 D → 升级；与共识不同的 judge 进 dissent。"""
    ev = ["q1", "q2"]
    reports = [
        {"judge_id": "j1", "overall_grade": "A", "confidence": 0.9, "evidence_quotes": ev},
        {"judge_id": "j2", "overall_grade": "A", "confidence": 0.9, "evidence_quotes": ev},
        {"judge_id": "j3", "overall_grade": "D", "confidence": 0.9, "evidence_quotes": ev,
         "reasoning_trace": ["崩了"], "uncertainty_flags": ["不确定x"]},
    ]
    out = jc.merge_reports(reports)
    # [4,4,1] 中位 4 = A 共识
    assert out["consensus_grade"] == "A"
    assert out["escalate_to_user"] is True
    assert any("D 级" in r for r in out["escalate_reasons"])
    # D judge 与共识不同 → dissent 记录
    assert len(out["dissent"]) == 1
    assert out["dissent"][0]["judge_id"] == "j3"
    assert out["dissent"][0]["grade"] == "D"
    assert out["dissent"][0]["uncertainty_flags"] == ["不确定x"]
    # uncertainty 聚合
    assert "不确定x" in out["all_uncertainty_flags"]


def test_merge_reports_two_judge_split_escalates():
    """2 judge 各执一词（agreement=0.5）→ 触发 <=0.5 升级。"""
    ev = ["q1", "q2"]
    reports = [
        {"judge_id": "j1", "overall_grade": "A", "confidence": 0.9, "evidence_quotes": ev},
        {"judge_id": "j2", "overall_grade": "C", "confidence": 0.9, "evidence_quotes": ev},
    ]
    out = jc.merge_reports(reports)
    assert out["agreement_score"] == 0.5
    assert out["escalate_to_user"] is True
    assert any("严重分歧" in r for r in out["escalate_reasons"])
    # ['A','C'] 偶数保守 → B
    assert out["consensus_grade"] == "B"


def test_merge_reports_low_evidence_escalates():
    """多数 judge 无 ≥2 条 quote → evidence_quality<0.5 升级。"""
    reports = [
        {"judge_id": "j1", "overall_grade": "A", "confidence": 0.9, "evidence_quotes": ["只一条"]},
        {"judge_id": "j2", "overall_grade": "A", "confidence": 0.9},  # 完全无 evidence
    ]
    out = jc.merge_reports(reports)
    assert out["reports_with_evidence"] == 0
    assert out["evidence_quality"] == 0.0
    assert out["escalate_to_user"] is True
    assert any("evidence 不足" in r for r in out["escalate_reasons"])


def test_merge_reports_persona_dissent_escalates_and_findings_merge():
    """persona 间分歧 >=2 grade levels → 升级；specific_findings 按 key 聚合成 list。"""
    ev = ["q1", "q2"]
    reports = [
        {"judge_id": "j1", "persona": "common_reader", "overall_grade": "A",
         "confidence": 0.9, "evidence_quotes": ev,
         "specific_findings": {"voice_match": True}},
        {"judge_id": "j2", "persona": "harsh_critic", "overall_grade": "C",
         "confidence": 0.9, "evidence_quotes": ev,
         "specific_findings": {"voice_match": False}},
    ]
    out = jc.merge_reports(reports)
    assert out["persona_dissent_severity"] == 2.0
    assert out["escalate_to_user"] is True
    assert any("persona 间分歧" in r for r in out["escalate_reasons"])
    # findings 同 key 聚成 list（保留两个 judge 的值）
    assert out["majority_findings_merged"]["voice_match"] == [True, False]


def test_calibration_features_empty_reports():
    cf = jc.calibration_features([])
    assert cf["feature_schema"] == "judge_reliability_calibration_v1"
    assert cf["n_reports"] == 0
    assert cf["grade_range"] == 0
    assert cf["gate_level"] == "advisory"


def test_main_cli_roundtrip(capsys=None):
    """CLI merge：读真实 json 文件 → stdout 输出可解析的 consensus JSON。"""
    import io
    import contextlib
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        p1 = root / "r1.json"
        p2 = root / "r2.json"
        ev = ["q1", "q2"]
        p1.write_text(json.dumps(
            {"judge_id": "j1", "overall_grade": "A", "confidence": 0.9, "evidence_quotes": ev}),
            encoding="utf-8")
        p2.write_text(json.dumps(
            {"judge_id": "j2", "overall_grade": "A", "confidence": 0.9, "evidence_quotes": ev}),
            encoding="utf-8")
        argv_bak = sys.argv
        sys.argv = ["judge_consensus.py", "merge", str(p1), str(p2)]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                jc.main()
        finally:
            sys.argv = argv_bak
        out = json.loads(buf.getvalue())
        assert out["consensus_grade"] == "A"
        assert out["judges_participated"] == ["j1", "j2"]
        assert out["escalate_to_user"] is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[judge_consensus] {passed} passed / {failed} failed / {passed + failed} total")
    raise SystemExit(0 if failed == 0 else 1)
