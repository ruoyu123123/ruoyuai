"""consolidate_author_profile 确定性聚合测试 — 守护「照顾弱模型驱动系统」根治（2026-06-01）。

背景：蒸馏综合 agent 自由写 作者风格.json schema → 与 consumer(build_manifest/validate_style/
skill_contract_table)期望键不符 → 契约债（弱模型必崩）。consolidate_author_profile.py 从单章
metrics/dim 确定性聚合 consumer 数值字段（不靠 agent），agent 只产创意。

本测试断言（用 mock 单章 metrics/dim · 不碰 LLM/真档）：
  · consolidate 从单章 metrics 聚合出 consumer 标准 schema（含别名键 + mean）；
  · 创意字段（golden_passages/core_style_signature）被保留不覆盖；
  · skill_contract_table.extract_contract_table 能读出全字段（非 None）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402
import skill_contract_table as sct  # noqa: E402


def _mk_project(root: Path, n_chapters: int = 4) -> Path:
    """造 mock 风格库：蒸馏进度/ch{N}_metrics.json + ch{N}.json + 原文 + 作者风格.json(含创意)。"""
    proj = root / "测试书"
    dist = proj / "蒸馏进度"
    orig = proj / "原文"
    dist.mkdir(parents=True)
    orig.mkdir(parents=True)
    for n in range(1, n_chapters + 1):
        metrics = {"file": f"第{n:03d}章.txt", "profile": {
            "total_chinese_chars": 2800 + n,
            "sentence_stats": {"mean": 30.0 + n, "std": 20.0, "median": 28},
            "paragraph_stats": {"mean": 50.0 + n, "std": 30.0, "median": 48},
            "dialogue_ratio": 0.25,
            "single_sentence_para_ratio": 0.50,
            "inner_monologue_ratio": 0.10,
            "punctuation_density_per_1000": {"comma": 60.0, "period": 25.0, "comma_period_ratio": 2.4},
            "function_word_fingerprint_per_1000": {"的": 38.0, "了": 18.0},
            "vocabulary_richness": {"type_token_ratio": 0.55, "hapax_ratio": 0.40},
        }}
        (dist / f"ch{n}_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
        single = {
            "chapter": n,
            "cross_chapter": {"dim16_开头类型": "纯对话" if n % 2 else "动作切入",
                              "dim18_章末类型": "信息炸弹" if n % 2 else "对话悬念"},
            "narrative_craft": {"dim34_叙事距离变化": "前段拉远全景、后段贴近角色意识"},
            "narrative_fingerprint": {"dim43_角色行为循环": [{"行为": "紧张时摸下巴"}],
                                      "dim47_人物丰满度": "A级丰满立体"},
        }
        (dist / f"ch{n}.json").write_text(json.dumps(single, ensure_ascii=False), encoding="utf-8")
        # 原文：每段独立(供段长分位数)
        (orig / f"第{n:03d}章.txt").write_text(
            f"第{n:03d}章 测试\n" + "\n".join(["这是一个测试段落用于聚合段长分位数。"] * 8), encoding="utf-8")
    # 作者风格.json：agent 产的创意字段（应被保留）+ 自由写的乱 schema
    (proj / "作者风格.json").write_text(json.dumps({
        "source": "测试书",
        "core_style_signature": {"标签": "全知说书人"},  # 创意·应保留
        "golden_passages": {"golden_opening": "原文段落示例"},  # 创意·应保留
        "quantitative": {"_doc": "agent 自由写", "sentence_length": {"_doc": "保留"}},
    }, ensure_ascii=False), encoding="utf-8")
    return proj


def test_consolidate_produces_consumer_schema():
    """consolidate 从单章 metrics 聚合出 consumer 标准 schema（含别名 + mean）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        cap.consolidate(proj, 4)
        sd = json.loads((proj / "作者风格.json").read_text(encoding="utf-8"))
        q = sd["quantitative"]
        # 必需键（consumer 硬依赖）
        assert q["sentence_length"]["mean"] is not None
        assert q["dialogue_ratio"]["mean"] is not None
        # skill_contract_table 期望别名键
        assert "intra_chapter_std_mean" in q["sentence_length"]
        assert q["dialogue_ratio_pct"]["mean"] is not None
        assert q["chapter_words"]["mean"] is not None
        assert q["paragraph_length"]["single_sentence_para_ratio_mean"] is not None
        # build_manifest D1 基线
        assert q["inner_monologue_ratio"]["mean"] is not None
        # 段长含 mean（validate_style 段长 band）
        assert q["paragraph_length_chars"].get("mean") is not None


def test_consolidate_aggregates_narrative_dims():
    """narrative_craft/fingerprint/cross_chapter_diversity 从单章 dim 聚合。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        cap.consolidate(proj, 4)
        sd = json.loads((proj / "作者风格.json").read_text(encoding="utf-8"))
        assert sd["narrative_craft"]["narrative_distance_distribution"]  # D1
        nf = sd["narrative_fingerprint"]
        assert nf["character_behavior_loops"]  # D3
        assert nf["character_depth_grade_distribution"]  # D3/D5
        # dim47 A → 语义"高"（D5 band 识别高深度）
        assert any("高" in str(k) for k in nf["character_depth_grade_distribution"])
        ccd = sd["cross_chapter_diversity"]
        assert ccd["opening_type_distribution"] and ccd["ending_type_distribution"]


def test_consolidate_preserves_creative_fields():
    """创意字段（core_style_signature/golden_passages）被保留不覆盖。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        cap.consolidate(proj, 4)
        sd = json.loads((proj / "作者风格.json").read_text(encoding="utf-8"))
        assert sd["core_style_signature"]["标签"] == "全知说书人"
        assert sd["golden_passages"]["golden_opening"] == "原文段落示例"


def test_consolidated_profile_readable_by_contract_table():
    """skill_contract_table.extract_contract_table 能读出全 consumer 字段（非 None）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        cap.consolidate(proj, 4)
        sd = json.loads((proj / "作者风格.json").read_text(encoding="utf-8"))
        c = sct.extract_contract_table(sd)
        assert c["sentence_mean"] is not None
        assert c["single_sentence_ratio"] is not None
        assert c["chapter_mean"] is not None
        assert c["dialogue_pct"] is not None


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
    print(f"[consolidate_author_profile] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
