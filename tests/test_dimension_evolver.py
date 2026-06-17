"""dimension_evolver 回归测试 — 钉死蒸馏维度自学习演化器的确定性逻辑。

被测脚本 core/scripts/dimension_evolver.py 全是纯文本启发式：F/G 段提议提取、
关键词/主题词抽取、跨章聚类、双门槛候选评估、单项目扫描、候选升级注册表。
零 LLM、零联网。本组测试锁真实行为，防重构静默回退。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import dimension_evolver as de  # noqa: E402


# ---------- extract_proposals_from_f_text ----------

def test_extract_f_text_high_value_with_dim_keyword():
    """含「建议新增」+ dim 关键词「密度」→ value=high·标 has_dim_keyword。"""
    txt = "本章节奏偏快。建议新增比喻密度基准维度，单独建立追踪机制。"
    props = de.extract_proposals_from_f_text(txt, 5)
    assert len(props) >= 1
    hi = [p for p in props if p["value"] == "high"]
    assert hi, f"应有 high 提议，实得 {[(p['value'], p['text']) for p in props]}"
    p = hi[0]
    assert p["chapter"] == 5
    assert p["has_dim_keyword"] is True
    assert p["trigger"] in de.PROPOSAL_TRIGGERS


def test_extract_f_text_mid_value_without_dim_keyword():
    """「建议新增」但无 dim 关键词 → 降为 mid（has_dim_keyword=False）。"""
    txt = "建议新增一段关于主角童年的回忆描写来丰富情节铺垫层次。"
    props = de.extract_proposals_from_f_text(txt, 3)
    assert len(props) == 1
    assert props[0]["value"] == "mid"
    assert props[0]["has_dim_keyword"] is False


def test_extract_f_text_no_trigger_returns_empty():
    """完全没触发词 → 早返回空列表（性能短路 + 语义正确）。"""
    txt = "本章风格与基线高度一致，叙事流畅，没有任何需要调整的地方呢。"
    assert de.extract_proposals_from_f_text(txt, 1) == []


def test_extract_f_text_non_string_and_empty():
    """非字符串 / 空串 / None → 安全返回空，不崩。"""
    assert de.extract_proposals_from_f_text(None, 1) == []
    assert de.extract_proposals_from_f_text("", 1) == []
    assert de.extract_proposals_from_f_text(123, 1) == []


def test_extract_f_text_short_sentence_filtered():
    """句子短于 DEFAULT_MIN_PROPOSAL_LEN（10 字）→ 即便有触发词也被过滤。"""
    # 「建议新增」是触发词但整句 < 10 字 → split 后该片段被长度门槛丢弃
    txt = "建议新增。"
    assert de.extract_proposals_from_f_text(txt, 1) == []


# ---------- extract_proposals_from_g_field ----------

def test_extract_g_field_structured():
    """G 段结构化字段 → 直读 proposed_dim_name / value / has_dim_keyword 恒 True。"""
    g = [{
        "observation": "感叹号密度显著高于通用基线",
        "proposed_dim_name": "感叹号密度基准",
        "value_assessment": "high",
        "current_dims_missing": "标点节奏维度缺失",
        "suggested_extraction_method": "正则统计",
    }]
    out = de.extract_proposals_from_g_field(g, 8)
    assert len(out) == 1
    p = out[0]
    assert p["chapter"] == 8
    assert p["trigger"] == "G_structured"
    assert p["proposed_dim_name"] == "感叹号密度基准"
    assert p["value"] == "high"
    assert p["has_dim_keyword"] is True


def test_extract_g_field_garbage_skipped():
    """非 list / 含非 dict 元素 → 跳过不崩。"""
    assert de.extract_proposals_from_g_field("not a list", 1) == []
    assert de.extract_proposals_from_g_field(None, 1) == []
    out = de.extract_proposals_from_g_field(["str", 42, {"observation": "x"}], 1)
    assert len(out) == 1  # 只有那个 dict 被收


# ---------- extract_keywords / extract_dim_theme ----------

def test_extract_keywords_dedup_and_stopwords():
    """抽 2-5 字中文短语·去 stopword·去重·限 top_k。"""
    kws = de.extract_keywords("比喻密度比喻密度需要单独建立追踪", top_k=3)
    assert len(kws) <= 3
    assert "需要" not in kws  # stopword 过滤
    # 去重：同一短语只出现一次
    assert len(kws) == len(set(kws))


def test_extract_dim_theme_prefix_plus_keyword():
    """dim 主题词 = dim 关键词前缀（最右几字）+ 关键词。"""
    theme = de.extract_dim_theme("应当追踪比喻密度的变化趋势")
    assert theme is not None
    assert theme.endswith("密度")
    assert "比喻" in theme


def test_extract_dim_theme_none_when_no_keyword():
    """文本不含任何 DIM_TYPE_KEYWORDS → 返回 None。"""
    assert de.extract_dim_theme("主角今天去了一趟集市买东西") is None


# ---------- collect_all_proposals (文件 IO) ----------

def _mk_distill_project(tmp: Path, chapter_files: dict) -> Path:
    """造 蒸馏进度/<filename>.json。chapter_files: {filename_stem: content_dict}。"""
    proj = tmp / "测试书"
    dist = proj / "蒸馏进度"
    dist.mkdir(parents=True, exist_ok=True)
    for stem, content in chapter_files.items():
        (dist / f"{stem}.json").write_text(
            json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return proj


def test_collect_all_proposals_f_and_g_dual_track():
    """双轨：F 段自由文本 + G 段结构化都被收集·章号去重。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_distill_project(Path(d), {
            "第001章": {
                "chapter": 1,
                "F_baseline_comparison": "建议新增比喻密度基准维度，应单独建立追踪。",
            },
            "第002章": {
                "chapter": 2,
                "G_dimension_proposals": [
                    {"observation": "感叹号偏多", "proposed_dim_name": "感叹号密度",
                     "value_assessment": "high"},
                ],
            },
        })
        props = de.collect_all_proposals(proj)
        chapters = {p["chapter"] for p in props}
        assert chapters == {1, 2}
        # G 段那条带 proposed_dim_name
        assert any(p.get("proposed_dim_name") == "感叹号密度" for p in props)


def test_collect_all_proposals_missing_dir():
    """没有 蒸馏进度 目录 → 返回空列表，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "空项目"
        proj.mkdir()
        assert de.collect_all_proposals(proj) == []


def test_collect_all_proposals_f_dict_form():
    """F 段是 dict（多子字段）→ 取所有 str values 解析。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_distill_project(Path(d), {
            "第003章": {
                "chapter": 3,
                "F_comparison": {
                    "句长": "建议新增句长分布指标维度做精细追踪。",
                    "其它": 12345,  # 非 str 应被跳过
                },
            },
        })
        props = de.collect_all_proposals(proj)
        assert props, "F dict 形态应能提出提议"
        assert all(p["chapter"] == 3 for p in props)


# ---------- cluster_proposals ----------

def test_cluster_proposals_by_theme():
    """同 dim 主题词（比喻密度）→ 聚到同一 cluster。"""
    props = [
        {"chapter": 1, "trigger": "建议", "text": "建议追踪比喻密度变化",
         "has_dim_keyword": True, "value": "high"},
        {"chapter": 2, "trigger": "建议", "text": "应区分比喻密度的高低档位",
         "has_dim_keyword": True, "value": "mid"},
    ]
    clusters = de.cluster_proposals(props)
    # 这两条主题词都含「密度」前缀「比喻」→ 应在一个 cluster
    merged = [c for c in clusters if len(c) == 2]
    assert merged, f"同主题应合并，得 cluster 大小 {[len(c) for c in clusters]}"


def test_cluster_proposals_similar_suffix_merge():
    """1.5 阶段：感叹号/省略号不同主题词 → 二次合并到「标点密度」super 族。"""
    props = [
        {"chapter": 1, "trigger": "建议", "text": "应追踪感叹号密度",
         "has_dim_keyword": True, "value": "high"},
        {"chapter": 2, "trigger": "建议", "text": "应追踪省略号密度",
         "has_dim_keyword": True, "value": "high"},
    ]
    clusters = de.cluster_proposals(props)
    # 感叹号密度 + 省略号密度 → 都含 SIMILAR_SUFFIX_GROUPS["标点密度"] 的 suffix → 合并
    big = [c for c in clusters if len(c) == 2]
    assert big, f"标点族应二次合并，得 {[len(c) for c in clusters]}"


# ---------- evaluate_cluster_as_candidate ----------

def _prep_cluster(props):
    """cluster_proposals 会给 props 注入 _keywords/_dim_theme，单独评估前先注入。"""
    for p in props:
        p["_keywords"] = set(de.extract_keywords(p["text"]))
        p["_dim_theme"] = de.extract_dim_theme(p["text"])
    return props


def test_evaluate_candidate_below_min_chapters():
    """只出现在 1 章但门槛 min_chapters=2 → 不够格返回 None。"""
    cluster = _prep_cluster([
        {"chapter": 1, "trigger": "建议", "text": "建议新增比喻密度维度",
         "has_dim_keyword": True, "value": "high"},
    ])
    assert de.evaluate_cluster_as_candidate(cluster, min_chapters=2, high_value_ratio=0.2) is None


def test_evaluate_candidate_below_valid_ratio():
    """valid_ratio（high+mid 占比）低于阈值 → None。"""
    # 4 条全 low，valid_ratio = 0 < 0.2
    cluster = _prep_cluster([
        {"chapter": 1, "trigger": "新发现", "text": "新发现某个有趣的细节描写呢",
         "has_dim_keyword": False, "value": "low"},
        {"chapter": 2, "trigger": "新发现", "text": "新发现另一个有趣的描写片段呢",
         "has_dim_keyword": False, "value": "low"},
    ])
    assert de.evaluate_cluster_as_candidate(cluster, min_chapters=2, high_value_ratio=0.2) is None


def test_evaluate_candidate_happy_path():
    """≥2 章 + valid_ratio 达标 → 产候选·字段齐全·命名走 dim_theme。"""
    cluster = _prep_cluster([
        {"chapter": 1, "trigger": "建议", "text": "建议追踪比喻密度变化趋势",
         "has_dim_keyword": True, "value": "high"},
        {"chapter": 4, "trigger": "建议", "text": "应区分比喻密度的高低档位",
         "has_dim_keyword": True, "value": "mid"},
    ])
    cand = de.evaluate_cluster_as_candidate(cluster, min_chapters=2, high_value_ratio=0.2)
    assert cand is not None
    assert cand["appearance_count"] == 2
    assert cand["appearance_chapters"] == [1, 4]
    assert cand["chapter_span"] == "ch1-4"
    assert cand["value_distribution"] == {"high": 1, "mid": 1, "low": 0}
    assert 0 <= cand["valid_value_ratio"] <= 1
    assert "比喻" in cand["candidate_dim_name"] or "密度" in cand["candidate_dim_name"]
    assert len(cand["sample_proposals"]) <= 3


def test_evaluate_candidate_g_name_priority():
    """G 段 proposed_dim_name 优先于 dim_theme 命名。"""
    cluster = _prep_cluster([
        {"chapter": 1, "trigger": "G_structured", "text": "感叹号偏多需追踪密度",
         "has_dim_keyword": True, "value": "high", "proposed_dim_name": "感叹号密度基准"},
        {"chapter": 2, "trigger": "G_structured", "text": "感叹号继续偏高的密度情况",
         "has_dim_keyword": True, "value": "high", "proposed_dim_name": "感叹号密度基准"},
    ])
    cand = de.evaluate_cluster_as_candidate(cluster, min_chapters=2, high_value_ratio=0.2)
    assert cand is not None
    assert cand["candidate_dim_name"] == "感叹号密度基准"


# ---------- scan_project (端到端确定性管线) ----------

def test_scan_project_no_proposals_error():
    """无任何提议 → 返回 error 字段。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_distill_project(Path(d), {
            "第001章": {"chapter": 1, "F_baseline_comparison": "完全符合基线无任何调整需求"},
        })
        result = de.scan_project(proj, min_chapters=2, high_value_ratio=0.2)
        assert "error" in result


def test_scan_project_produces_candidates_with_ids():
    """跨章重复提议 → 产候选·带 cand_NNN id·schema 字段齐。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_distill_project(Path(d), {
            "第001章": {"chapter": 1,
                       "F_baseline_comparison": "建议新增比喻密度基准维度，应单独建立追踪。"},
            "第002章": {"chapter": 2,
                       "F_baseline_comparison": "应区分比喻密度的高低档位做精细统计。"},
        })
        result = de.scan_project(proj, min_chapters=2, high_value_ratio=0.2)
        assert "error" not in result
        assert result["project"] == "测试书"
        assert result["total_proposals_extracted"] >= 2
        assert result["candidates_above_threshold"] == len(result["candidates"])
        if result["candidates"]:
            assert result["candidates"][0]["candidate_id"] == "cand_001"


# ---------- promote_candidate (注册表写入) ----------

def test_promote_candidate_writes_registry():
    """升级候选 → 写 auto_evolved_dimensions.json·entry 字段齐·status=active。"""
    with tempfile.TemporaryDirectory() as d:
        repo_root = Path(d)
        project = repo_root / "workspace" / "styles" / "测试书"
        project.mkdir(parents=True)
        cand = {
            "candidate_dim_name": "auto_比喻密度",
            "appearance_count": 3,
            "appearance_chapters": [1, 2, 4],
            "top_keywords": ["比喻", "密度"],
            "sample_proposals": [{"chapter": 1, "value": "high", "text": "建议追踪比喻密度"}],
        }
        res = de.promote_candidate(cand, project, repo_root)
        assert res["promoted"] is True
        assert res["dim_name"] == "auto_比喻密度"
        reg_file = repo_root / "core" / "claude-home" / "auto_evolved_dimensions.json"
        assert reg_file.exists()
        reg = json.loads(reg_file.read_text(encoding="utf-8"))
        entry = reg["dimensions"][0]
        assert entry["dim_name"] == "auto_比喻密度"
        assert entry["status"] == "active"
        assert entry["auto_inject_to_distill_prompt"] is True
        assert entry["promoted_from_project"] == "测试书"


def test_promote_candidate_dedup_by_name():
    """重名 → 拒绝二次升级（promoted=False·原因「已在注册表中」）。"""
    with tempfile.TemporaryDirectory() as d:
        repo_root = Path(d)
        project = repo_root / "workspace" / "styles" / "测试书"
        project.mkdir(parents=True)
        cand = {
            "candidate_dim_name": "auto_句长指标",
            "appearance_count": 2,
            "appearance_chapters": [1, 2],
            "top_keywords": ["句长"],
            "sample_proposals": [],
        }
        first = de.promote_candidate(cand, project, repo_root)
        assert first["promoted"] is True
        second = de.promote_candidate(cand, project, repo_root)
        assert second["promoted"] is False
        assert "已在注册表" in second["reason"]


def test_promote_candidate_daily_cap():
    """单次升级上限 max_per_run → 超限拒绝。"""
    with tempfile.TemporaryDirectory() as d:
        repo_root = Path(d)
        project = repo_root / "workspace" / "styles" / "测试书"
        project.mkdir(parents=True)
        # max_per_run=1，先升一个，第二个不同名应被日上限拦
        c1 = {"candidate_dim_name": "auto_维度甲", "appearance_count": 2,
              "appearance_chapters": [1, 2], "top_keywords": ["甲"], "sample_proposals": []}
        c2 = {"candidate_dim_name": "auto_维度乙", "appearance_count": 2,
              "appearance_chapters": [1, 2], "top_keywords": ["乙"], "sample_proposals": []}
        r1 = de.promote_candidate(c1, project, repo_root, max_per_run=1)
        assert r1["promoted"] is True
        r2 = de.promote_candidate(c2, project, repo_root, max_per_run=1)
        assert r2["promoted"] is False
        assert "上限" in r2["reason"]


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
    print(f"[dimension_evolver] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
