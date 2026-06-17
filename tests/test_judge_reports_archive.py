"""judge_reports_archive.py 回归测试 — 钉死 JudgeReport 归档的确定性逻辑。

覆盖纯函数：grade⇄score 映射、cluster 级综合 grade 聚合（众数/平票中位偏严）、
逐章 judge 信号提取（audit-hub 优先）、各 report builder（缺字段返回 None）、
chapter_range 解析、load/save_json 容错；以及 _archive_one_chapter 在
dry-run（不写 _css 账本）下的端到端汇集行为。

零依赖：仅标准库；文件 IO 走 tempfile，绝不写真项目目录。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import judge_reports_archive as mod  # noqa: E402


# ---------------------------------------------------------------------------
# grade ⇄ score 映射 + score→grade 钳位
# ---------------------------------------------------------------------------
def test_grade_to_score_mapping_and_garbage():
    assert mod._grade_to_score("A") == 95.0
    assert mod._grade_to_score("b") == 82.0          # 小写归一
    assert mod._grade_to_score("  C ") == 68.0       # 去空白
    assert mod._grade_to_score("D") == 50.0
    assert mod._grade_to_score("N/A") is None        # 非法字母
    assert mod._grade_to_score("") is None
    assert mod._grade_to_score(None) is None         # 非 str
    assert mod._grade_to_score(95) is None           # 数值不被当 grade


def test_score_to_grade_thresholds():
    # 阈值表：>=90 A / >=78 B / >=60 C / else D
    assert mod._score_to_grade(95.0) == "A"
    assert mod._score_to_grade(90.0) == "A"          # 边界含等号
    assert mod._score_to_grade(89.99) == "B"
    assert mod._score_to_grade(78.0) == "B"
    assert mod._score_to_grade(77.0) == "C"
    assert mod._score_to_grade(60.0) == "C"
    assert mod._score_to_grade(59.0) == "D"
    assert mod._score_to_grade(0.0) == "D"
    assert mod._score_to_grade(-5.0) == "D"          # 兜底分支


# ---------------------------------------------------------------------------
# cluster 级综合 grade 聚合：众数 / 平票取中位（偏严）
# ---------------------------------------------------------------------------
def test_aggregate_cluster_grade_majority():
    # 唯一众数直接取
    assert mod._aggregate_cluster_grade(["A", "A", "B"]) == "A"
    assert mod._aggregate_cluster_grade(["C", "B", "B", "C", "B"]) == "B"


def test_aggregate_cluster_grade_empty_and_garbage():
    # 全空 / 全非法 → None（无 judge 信号则不写 judge_grade）
    assert mod._aggregate_cluster_grade([]) is None
    assert mod._aggregate_cluster_grade(["N/A", None, "", "X"]) is None
    # 混入垃圾只取合法值：仅一个 A → A
    assert mod._aggregate_cluster_grade(["A", "N/A", None]) == "A"


def test_aggregate_cluster_grade_tie_breaks_to_median_strict():
    # A(95)/C(68) 平票 → 偶数个取双中位均值 (95+68)/2=81.5 → >=78 → B（偏严）
    assert mod._aggregate_cluster_grade(["A", "C"]) == "B"
    # A(95)/B(82) 平票 → (95+82)/2=88.5 → <90 → B（偏严，不抬到 A）
    assert mod._aggregate_cluster_grade(["A", "B"]) == "B"
    # 大小写不影响平票判定
    assert mod._aggregate_cluster_grade(["a", "c"]) == "B"
    # 奇数个三方平票 A/B/C → sorted=[68,82,95]，取 scores[1]=82 → B
    assert mod._aggregate_cluster_grade(["A", "B", "C"]) == "B"


# ---------------------------------------------------------------------------
# 逐章 judge 信号提取：audit-hub 优先，否则取首个有 grade 的 report
# ---------------------------------------------------------------------------
def test_chapter_judge_signals_audit_hub_priority():
    valid = {
        "audit-hub": {"overall_grade": "B", "waivers": [{"code": "X1"}]},
        "other": {"overall_grade": "A", "waivers": [{"code": "X2"}]},
    }
    score, grade, waivers = mod._chapter_judge_signals(valid)
    assert grade == "B"                # audit-hub 赢，不取 other 的 A
    assert score == 82.0
    # waivers 汇总两个 report
    codes = sorted(w["code"] for w in waivers)
    assert codes == ["X1", "X2"]


def test_chapter_judge_signals_fallback_when_audit_na():
    # audit-hub 的 grade 非法 → 回退取首个有合法 grade 的 report
    valid = {
        "audit-hub": {"overall_grade": "N/A"},
        "summarizer": {"overall_grade": "N/A"},
        "truth": {"overall_grade": "A"},
    }
    score, grade, waivers = mod._chapter_judge_signals(valid)
    assert grade == "A"
    assert score == 95.0
    assert waivers == []


def test_chapter_judge_signals_no_valid_grade():
    valid = {"summarizer": {"overall_grade": "N/A"}, "reflector": {}}
    score, grade, waivers = mod._chapter_judge_signals(valid)
    assert grade is None
    assert score is None
    assert waivers == []


# ---------------------------------------------------------------------------
# report builders：缺字段返回 None，正常字段映射正确
# ---------------------------------------------------------------------------
def test_build_validator_report_grade_logic():
    # fatal=0 error=0 → A
    a = mod.build_validator_report_from_audit({"summary": {}}, 3)
    assert a["overall_grade"] == "A"
    assert a["chapter"] == 3
    # fatal=0 error=2 → B (error<=2)
    b = mod.build_validator_report_from_audit({"summary": {"error": 2}}, 3)
    assert b["overall_grade"] == "B"
    # fatal=0 error=3 → C
    c = mod.build_validator_report_from_audit({"summary": {"error": 3}}, 3)
    assert c["overall_grade"] == "C"
    # fatal>=1 → D
    d = mod.build_validator_report_from_audit({"summary": {"fatal": 1, "error": 0}}, 3)
    assert d["overall_grade"] == "D"
    # 空 audit → None
    assert mod.build_validator_report_from_audit({}, 3) is None


def test_build_validator_report_issues_truncation():
    # issues 只取前 10 条进 issues_summary
    audit = {"summary": {}, "issues": [{"code": f"I{i}", "severity": "warn", "dimension": "d"} for i in range(15)]}
    r = mod.build_validator_report_from_audit(audit, 1)
    assert len(r["specific_findings"]["issues_summary"]) == 10


def test_build_writer_self_eval_report():
    assert mod.build_writer_self_eval_report({}, 5) is None          # 无 self_eval
    changes = {
        "self_eval": {
            "applied_style": {
                "opening_type": "in_medias_res",
                "anchors_hit": ["a", "b"],
                "core_techniques_applied": ["t1"],
                "hooks_count": 3,
            },
            "waivers": [{"code": "W1"}],
            "continuity_check": "ok",
        }
    }
    r = mod.build_writer_self_eval_report(changes, 5)
    assert r["overall_grade"] == "N/A"                               # writer 不自评 grade
    fs = r["specific_findings"]
    assert fs["opening_type"] == "in_medias_res"
    assert fs["anchors_hit_count"] == 2
    assert fs["core_techniques_count"] == 1
    assert fs["hooks_count"] == 3
    assert r["waivers"] == [{"code": "W1"}]


def test_build_truth_check_report_lie_count_grades():
    assert mod.build_truth_check_report({}, 2) is None               # 无 truth_check
    no_lie = mod.build_truth_check_report({"truth_check": {"lie_count": 0}}, 2)
    assert no_lie["overall_grade"] == "A"
    has_lie = mod.build_truth_check_report({"truth_check": {"lie_count": 1}}, 2)
    assert has_lie["overall_grade"] == "B"


def test_build_summarizer_and_reflector_reports():
    assert mod.build_summarizer_report({}, 1) is None
    assert mod.build_reflector_report({}, 1) is None
    s = mod.build_summarizer_report({"summary_words": 200, "key_details": ["a", "b"],
                                     "emotion": {"value": 0.5, "trend": "up"}}, 7)
    assert s["specific_findings"]["key_details_count"] == 2
    assert s["specific_findings"]["emotion_value"] == 0.5
    refl = mod.build_reflector_report({"entries": [
        {"category": "success", "id": "e1"},
        {"category": "failure", "id": "e2"},
        {"category": "success", "id": "e3"},
    ], "note": "hello"}, 7)
    assert refl["specific_findings"]["new_success_patterns"] == 2
    assert refl["specific_findings"]["new_failure_patterns"] == 1
    assert refl["specific_findings"]["experience_ids"] == ["e1", "e2", "e3"]


# ---------------------------------------------------------------------------
# load_json / save_json 容错 + 往返
# ---------------------------------------------------------------------------
def test_load_json_missing_and_corrupt():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert mod.load_json(root / "nope.json", {"x": 1}) == {"x": 1}   # 缺文件 → default
        bad = root / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert mod.load_json(bad, {"d": 2}) == {"d": 2}                  # 损坏 → default
        good = root / "good.json"
        mod.save_json(good, {"k": "值"})                                # 中文往返
        assert mod.load_json(good) == {"k": "值"}


def test_save_json_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "a" / "b" / "c.json"
        mod.save_json(target, {"ok": True})
        assert target.exists()
        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


# ---------------------------------------------------------------------------
# _resolve_chapter_range：从 事件簇.json 解析 cluster → 章节列表
# ---------------------------------------------------------------------------
def _mk_project_with_clusters(root: Path, clusters: list) -> Path:
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(json.dumps({"clusters": clusters}, ensure_ascii=False),
                                    encoding="utf-8")
    return root


def test_resolve_chapter_range_happy_and_normalized_key():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project_with_clusters(Path(d), [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "chapter_range": [4, 6]},
        ])
        assert mod._resolve_chapter_range(root, "002") == [4, 5, 6]
        assert mod._resolve_chapter_range(root, "cluster_002") == [4, 5, 6]   # 带前缀亦可
        assert mod._resolve_chapter_range(root, "001") == [1, 2, 3]


def test_resolve_chapter_range_missing_and_bad():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 文件不存在 → []
        assert mod._resolve_chapter_range(root, "001") == []
        _mk_project_with_clusters(root, [
            {"cluster_id": "cluster_001", "chapter_range": "bad"},   # 非法 range
            {"cluster_id": "cluster_003", "chapter_range": [9, 9]},
        ])
        assert mod._resolve_chapter_range(root, "001") == []         # range 非 [a,b] → []
        assert mod._resolve_chapter_range(root, "999") == []         # 未匹配 cluster → []
        assert mod._resolve_chapter_range(root, "003") == [9]        # 单章 range


# ---------------------------------------------------------------------------
# _archive_one_chapter：dry-run 端到端（不触 _css 账本写入）
# ---------------------------------------------------------------------------
def test_archive_one_chapter_dry_run_collects_signals():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        (db / ".audit").mkdir(parents=True)
        # audit：error=0 → validator grade A
        mod.save_json(db / ".audit" / "ch_001_audit.json",
                      {"summary": {"fatal": 0, "error": 0}, "verdict": "pass"})
        # changes：带 self_eval → writer-self-eval report
        ch_dir = root / "章节" / "第001章"
        mod.save_json(ch_dir / "第001章_changes.json",
                      {"self_eval": {"applied_style": {"opening_type": "x"}, "waivers": []}})
        r = mod._archive_one_chapter(root, 1, dry_run=True, cluster_id=None)
        assert r["ch"] == 1
        assert r["written"] == 0                                     # dry-run 不落盘
        assert "audit-hub" in r["judges"]
        assert "writer-self-eval" in r["judges"]
        # audit-hub grade=A → 综合 grade A / score 95
        assert r["grade"] == "A"
        assert r["score"] == 95.0
        # dry-run 不写 .judge_reports 文件
        assert not (db / ".judge_reports" / "ch_001_audit-hub.json").exists()


def test_archive_one_chapter_no_signals_returns_empty_judges():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "_数据库").mkdir(parents=True)
        r = mod._archive_one_chapter(root, 1, dry_run=True, cluster_id=None)
        assert r["judges"] == []
        assert r["grade"] is None
        assert r["score"] is None
        assert r["written"] == 0


def test_archive_one_chapter_writes_files_when_not_dry_run():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        (db / ".audit").mkdir(parents=True)
        mod.save_json(db / ".audit" / "ch_002_audit.json", {"summary": {"error": 5}})
        # 非 dry-run、cluster_id=None → 写 .judge_reports 文件但不触 cluster 账本
        r = mod._archive_one_chapter(root, 2, dry_run=False, cluster_id=None)
        assert r["written"] >= 1
        out = db / ".judge_reports" / "ch_002_audit-hub.json"
        assert out.exists()
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["chapter"] == 2
        # error=5 (>2, fatal=0) → C
        assert rep["overall_grade"] == "C"
        assert r["grade"] == "C"
