"""evolution_orchestrator 确定性单元测试 — 锁三角共演化分析器的纯逻辑。

只测不调 LLM / 不联网 / 不 subprocess 的确定性部分：
  - load_json / save_json（文件 IO 兜底）
  - _grade_to_score（字母评级 → 5.0 制·L10 修复点）
  - _expand_cluster_chapters（chapter_range 展开 + chapters{} 回退·L9 修复点）
  - analyze_proposer / analyze_solver / analyze_judge / analyze_judge_cluster
    （三角各角色的信号检测与阈值分支）

不测 trigger_cascade / _run_cluster / main —— 它们 subprocess 调 skill_evolver / 走 CLI。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import evolution_orchestrator as eo  # noqa: E402


# ---------------------------------------------------------------- 项目脚手架

def _mk_project(tmp: Path) -> Path:
    (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
    return tmp


def _write_brief_candidates(root: Path, key: str, candidates: list):
    wal = root / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    (wal / f"cluster_{key}_brief_candidates.json").write_text(
        json.dumps({"candidates": candidates}, ensure_ascii=False), encoding="utf-8")


def _write_user_choice(root: Path, key: str, brief: dict):
    wal = root / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    (wal / f"cluster_{key}_user_choice.json").write_text(
        json.dumps({"answer": brief}, ensure_ascii=False), encoding="utf-8")


def _write_judge_report(root: Path, ch: int, data: dict):
    jd = root / "_数据库" / ".judge_reports"
    jd.mkdir(parents=True, exist_ok=True)
    (jd / f"ch_{ch:03d}_audit-hub.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_changes(root: Path, ch: int, waivers: list):
    cd = root / "章节" / f"第{ch:03d}章"
    cd.mkdir(parents=True, exist_ok=True)
    (cd / f"第{ch:03d}章_changes.json").write_text(
        json.dumps({"self_eval": {"waivers": waivers}}, ensure_ascii=False),
        encoding="utf-8")


# ---------------------------------------------------------------- load/save_json

def test_load_json_missing_returns_default():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "nope.json"
        assert eo.load_json(p, default={"x": 1}) == {"x": 1}
        assert eo.load_json(p) is None  # 默认 default=None


def test_load_json_malformed_returns_default():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.json"
        p.write_text("{ not valid json ", encoding="utf-8")
        assert eo.load_json(p, default={}) == {}


def test_save_json_roundtrip_creates_parents():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sub" / "deep" / "out.json"
        payload = {"中文键": ["值1", 2, {"嵌套": True}]}
        eo.save_json(p, payload)
        assert p.exists()  # 父目录被自动建
        assert eo.load_json(p) == payload  # round-trip 含中文不转义


# ---------------------------------------------------------------- _grade_to_score

def test_grade_to_score_known_and_case_insensitive():
    assert eo._grade_to_score("A") == 5.0
    assert eo._grade_to_score("b+") == 4.0       # 小写归一
    assert eo._grade_to_score(" C- ") == 1.5     # 前后空白 strip
    assert eo._grade_to_score("F") == 0.0


def test_grade_to_score_invalid_returns_none():
    assert eo._grade_to_score("Z") is None       # 非法字母
    assert eo._grade_to_score(None) is None       # 缺失
    assert eo._grade_to_score(3.5) is None        # 非字符串（数值不是字母评级）


# ---------------------------------------------------------------- _expand_cluster_chapters

def test_expand_cluster_chapters_prefers_range():
    cluster = {"chapter_range": [4, 7], "chapters": {"4": {}, "99": {}}}
    # range 优先且全展开，绝不混入 chapters 里的越界 99
    assert eo._expand_cluster_chapters(cluster) == [4, 5, 6, 7]


def test_expand_cluster_chapters_falls_back_to_chapters_keys():
    # range 缺/非法 → 回退 chapters{} key（含字符串/非法 key 容错）
    cluster = {"chapter_range": [9, 3], "chapters": {"3": {}, "5": {}, "bad": {}}}
    assert eo._expand_cluster_chapters(cluster) == [3, 5]


def test_expand_cluster_chapters_empty_returns_empty():
    assert eo._expand_cluster_chapters({}) == []
    assert eo._expand_cluster_chapters({"chapter_range": "garbage", "chapters": None}) == []


# ---------------------------------------------------------------- analyze_proposer

def test_analyze_proposer_no_wal_dir():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        assert eo.analyze_proposer(root, [1, 2, 3]) == {"signal": "no_data"}


def test_analyze_proposer_flags_low_quality():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        _write_brief_candidates(root, "002", [
            {"cluster_id": "cluster_002_candidate_1", "scope_summary": "has no scenes"},
            {"cluster_id": "cluster_002_candidate_2", "scene_storyboard": [{"scene_idx": 0}]},
            "not a dict",
        ])
        r = eo.analyze_proposer(root, [1, 2, 3])
        assert r["signal"] == "low_quality"
        assert any(f["signal"] == "PROPOSER_LOW_QUALITY" for f in r["findings"])
        assert r["candidate_count"] == 3
        assert r["candidate_violations"] >= 3


def test_analyze_proposer_clean_candidates_ok():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        _write_brief_candidates(root, "002", [
            {
                "cluster_id": "cluster_002_candidate_1",
                "scope_summary": "first path",
                "scene_storyboard": [{"scene_idx": 0, "summary": "start"}],
            },
            {
                "cluster_id": "cluster_002_candidate_2",
                "scope_summary": "second path",
                "scene_storyboard": [{"scene_idx": 0, "summary": "start"}],
            },
            {
                "cluster_id": "cluster_002_candidate_3",
                "scope_summary": "third path",
                "scene_storyboard": [{"scene_idx": 0, "summary": "start"}],
            },
        ])
        _write_user_choice(root, "002", {
            "cluster_id": "cluster_002_candidate_2",
            "scope_summary": "second path",
            "scene_storyboard": [{"scene_idx": 0, "summary": "start"}],
        })
        r = eo.analyze_proposer(root, [1, 2, 3])
        assert r["signal"] == "ok"
        assert r["findings"] == []
        assert r["candidate_count"] == 3
        assert r["choice_distribution"]["candidate_2"] == 1


# ---------------------------------------------------------------- analyze_solver

def test_analyze_solver_no_judge_dir():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        assert eo.analyze_solver(root, [1]) == {"signal": "no_data"}


def test_analyze_solver_repeated_findings():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        # 同一 code 出现 3 章 → SOLVER_REPEATED_ERRORS
        for ch in (1, 2, 3):
            _write_judge_report(root, ch, {
                "overall_grade": "A",
                "issues": [{"code": "STYLE_单段超长"}],
            })
        r = eo.analyze_solver(root, [1, 2, 3])
        assert r["signal"] == "needs_evolve"
        assert any(f["signal"] == "SOLVER_REPEATED_ERRORS" for f in r["findings"])
        assert r["repeated_codes"]["STYLE_单段超长"] == 3


def test_analyze_solver_grade_decline_via_grade_fallback():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        # 无数值 score，只有字母评级 → 走 _grade_to_score 兜底（L10 修复点）
        # A(5.0) → A(5.0) → C(2.0)，跌幅 3.0 > 0.5 → SOLVER_QUALITY_DECLINE
        _write_judge_report(root, 1, {"overall_grade": "A", "issues": []})
        _write_judge_report(root, 2, {"overall_grade": "A", "issues": []})
        _write_judge_report(root, 3, {"overall_grade": "C", "issues": []})
        r = eo.analyze_solver(root, [1, 2, 3])
        assert any(f["signal"] == "SOLVER_QUALITY_DECLINE" for f in r["findings"])
        # 评分确实被字母兜底解析进 judge_scores
        assert r["judge_scores"][0][1] == 5.0
        assert r["judge_scores"][-1][1] == 2.0


def test_analyze_solver_stable_quality_ok():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        # 数值 score 平稳 + 无重复 finding → ok
        for ch in (1, 2, 3):
            _write_judge_report(root, ch, {"score": 4.0, "issues": []})
        r = eo.analyze_solver(root, [1, 2, 3])
        assert r["signal"] == "ok"
        assert r["findings"] == []
        assert [s for _, s in r["judge_scores"]] == [4.0, 4.0, 4.0]


# ---------------------------------------------------------------- analyze_judge

def test_analyze_judge_persistent_waiver():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        for ch in (1, 2, 3):
            _write_changes(root, ch, [{"code": "STYLE_单段超长", "reason": "x"}])
        r = eo.analyze_judge(root, [1, 2, 3])
        assert r["signal"] == "tool_calibration"
        assert any(f["signal"] == "JUDGE_PERSISTENT_WAIVER" for f in r["findings"])
        assert r["persistent_waivers"]["STYLE_单段超长"] == 3


def test_analyze_judge_below_threshold_ok():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        # 只 2 章豁免 < 3 阈值 → 不触发
        for ch in (1, 2):
            _write_changes(root, ch, [{"code": "X"}])
        r = eo.analyze_judge(root, [1, 2])
        assert r["signal"] == "ok"
        assert r["findings"] == []


# ---------------------------------------------------------------- analyze_judge_cluster

def test_analyze_judge_cluster_low_grade():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        clusters = [
            {"cluster_id": "cluster_001", "judge_grade": "C"},
            {"cluster_id": "cluster_002", "judge_grade": "D"},
            {"cluster_id": "cluster_003", "judge_grade": "A"},
        ]
        r = eo.analyze_judge_cluster(root, clusters)
        assert r["signal"] == "needs_evolve"
        assert any(f["signal"] == "JUDGE_CLUSTER_LOW_GRADE" for f in r["findings"])
        assert len(r["cluster_grades"]) == 3


def test_analyze_judge_cluster_falls_back_to_per_chapter():
    """无 judge_grade → 把 cluster 的章拍平走逐章 waiver 分析（账本数据兜底·非章级入口）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        # cluster 无 judge_grade，靠 chapter_range 展开后查 waiver
        for ch in (1, 2, 3):
            _write_changes(root, ch, [{"code": "FORESHADOWING_NOT_PAID"}])
        clusters = [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}]
        r = eo.analyze_judge_cluster(root, clusters)
        # 回退到 analyze_judge → 持续豁免被识别
        assert any(f["signal"] == "JUDGE_PERSISTENT_WAIVER" for f in r["findings"])


def test_analyze_judge_cluster_no_data():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        # 无 grade 且无可展开章 → no_data
        r = eo.analyze_judge_cluster(root, [{"cluster_id": "cluster_001"}])
        assert r["signal"] == "no_data"
        assert r["cluster_grades"] == []


# ---------------------------------------------------------------- CLI cluster-only 回归锁

def test_cli_is_cluster_only_no_ch_entry():
    """回归锁（W3 旁路移除 2026-07-05）：--ch 章级双形入口已删——系统 cluster-only，
    plan step 只用 --cluster。锁 CLI 面：源码不得再出现 --ch / --cycle 章级参数。"""
    import inspect
    src = inspect.getsource(eo)
    assert '"--ch"' not in src and "'--ch'" not in src, "--ch 章级入口不得复活"
    assert '"--cycle"' not in src and "'--cycle'" not in src, "--cycle 章级窗口参数不得复活"
    assert '"--cluster"' in src, "--cluster 必须是唯一分析入口"
    # trigger_cascade 不再有 chapter 分支：cluster_key 为必填位置签名（无 None 默认）
    sig = inspect.signature(eo.trigger_cascade)
    assert sig.parameters["cluster_key"].default is inspect.Parameter.empty, \
        "trigger_cascade 的 cluster_key 必填，不得留章级默认分支"


# ---------------------------------------------------------------- 自跑入口

def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[evolution_orchestrator] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
