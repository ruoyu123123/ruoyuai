# -*- coding: utf-8 -*-
"""test_flawed_fiction_maker.py — S5 FlawedFictions 反向校准（maker + runner）回归测试。

确定性·零 LLM·零 NN（conftest 自动清 NN 门控；NLI 描述类通路在测试里诚实 skip）。

覆盖：
  1. maker 确定性：同输入两次 build_samples → 全部样本草稿逐字节一致 + ground_truth 一致
  2. ground_truth 标注完整性：必备字段齐 + injected_text 真实出现在 char_offset 处
  3. 注入不破坏原文其他部分：剥掉注入块后逐字节还原原稿
  4. baseline 样本 = 原稿逐字节
  5. fixture 项目：目标 fact 排第一 / 事件簇 linear / 地图词典
  6. e2e 确定性回归锁：numeric 样本过 locked_fact scanner 必中；temporal/spatial
     样本过对应 scanner 必中；baseline 全层 0 violation
  7. 纯模板锁：maker 无任何 LLM 增广路径（_llm_augment / mode 参数不复活）
  8. runner 判中函数 + 召回矩阵聚合
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_CALIB_DIR = _ROOT / "core" / "ml" / "calibration"
if str(_CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(_CALIB_DIR))

import flawed_fiction_maker as maker  # noqa: E402
import flawed_fiction_runner as runner  # noqa: E402


# ════════════════════════ 合成基稿 fixture（不依赖 workspace 真项目）════════════════════════

def _make_base_draft(tmp_path: Path) -> Path:
    """120 段合成基稿：含主角名、无「岁」、无时间锚、无 fixture 地点词 → 各层 baseline 干净。"""
    paras = []
    for i in range(120):
        if i % 3 == 0:
            paras.append(f"{maker.PROTAGONIST}把解剖刀在指间转了个圈，第{i}号证物袋封了口。")
        else:
            paras.append(f"雾气贴着井台的青砖爬，第{i}块砖缝里渗着腥气。")
    p = tmp_path / "base_draft.txt"
    p.write_text("\n\n".join(paras), encoding="utf-8")
    return p


@pytest.fixture()
def built(tmp_path):
    base = _make_base_draft(tmp_path)
    out = tmp_path / "out"
    manifest = maker.build_samples(base, out)
    return base, out, manifest


def _draft_of(sample_dir: Path) -> Path:
    return sample_dir / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"


def _sample_dir(out: Path, sid: str) -> Path:
    return out / "samples" / sid


# ════════════════════════ 1. 确定性 ════════════════════════

def test_deterministic_same_input_same_output(tmp_path):
    base = _make_base_draft(tmp_path)
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    man_a = maker.build_samples(base, out_a)
    man_b = maker.build_samples(base, out_b)
    assert [s["sample_id"] for s in man_a["samples"]] == \
           [s["sample_id"] for s in man_b["samples"]]
    for entry in man_a["samples"]:
        sid = entry["sample_id"]
        assert _draft_of(_sample_dir(out_a, sid)).read_bytes() == \
               _draft_of(_sample_dir(out_b, sid)).read_bytes()
        gt_a = json.loads((_sample_dir(out_a, sid) / "ground_truth.json").read_text(encoding="utf-8"))
        gt_b = json.loads((_sample_dir(out_b, sid) / "ground_truth.json").read_text(encoding="utf-8"))
        assert gt_a == gt_b


# ════════════════════════ 2. ground_truth 完整性 ════════════════════════

_REQUIRED_GT_FIELDS = ("sample_id", "flaw_type", "character", "violated_fact",
                       "injection", "expected_layer", "expected_deterministic_detect",
                       "expect", "base_draft_sha256", "notes")


def test_ground_truth_completeness_and_offset(built):
    _, out, manifest = built
    non_baseline = [s for s in manifest["samples"] if s["flaw_type"] != "baseline"]
    assert len(non_baseline) == len(maker.FLAW_SPECS)
    for entry in non_baseline:
        gt = json.loads((_sample_dir(out, entry["sample_id"]) / "ground_truth.json")
                        .read_text(encoding="utf-8"))
        for field in _REQUIRED_GT_FIELDS:
            assert field in gt, f"{entry['sample_id']} 缺 {field}"
        inj = gt["injection"]
        text = _draft_of(_sample_dir(out, entry["sample_id"])).read_text(encoding="utf-8")
        # injected_text 必须逐字符出现在 char_offset 处
        assert text[inj["char_offset"]:inj["char_offset"] + len(inj["injected_text"])] \
               == inj["injected_text"]
        # locked_fact 类必须携带 violated_fact；temporal/spatial 必须为 None
        if gt["flaw_type"] in ("numeric", "direct_rewrite", "multi_hop"):
            assert gt["violated_fact"] in maker.FACTS.values()
        else:
            assert gt["violated_fact"] is None


# ════════════════════════ 3/4. 注入不破坏原文 + baseline 逐字节 ════════════════════════

def test_injection_preserves_rest_of_draft(built):
    base, out, manifest = built
    base_text = base.read_text(encoding="utf-8")
    for entry in manifest["samples"]:
        if entry["flaw_type"] == "baseline":
            continue
        gt = json.loads((_sample_dir(out, entry["sample_id"]) / "ground_truth.json")
                        .read_text(encoding="utf-8"))
        inj = gt["injection"]
        corrupted = _draft_of(_sample_dir(out, entry["sample_id"])).read_text(encoding="utf-8")
        start, block = inj["char_offset"], inj["injected_text"]
        restored = corrupted[:start] + corrupted[start + len(block) + len(maker.PARA_SEP):]
        # 注入在末尾（idx 超段数被 clamp）时分隔符在块前而非块后
        if restored != base_text:
            restored = corrupted[:start - len(maker.PARA_SEP)] \
                + corrupted[start + len(block):]
        assert restored == base_text, f"{entry['sample_id']} 注入破坏了原文其他部分"


def test_baseline_is_byte_identical(built):
    base, out, _ = built
    assert _draft_of(_sample_dir(out, maker.BASELINE_ID)).read_bytes() == base.read_bytes()


# ════════════════════════ 5. fixture 项目 ════════════════════════

def test_fixture_db_target_fact_first_and_linear(built):
    _, out, _ = built
    d1 = _sample_dir(out, "D1_direct_spouse")
    card = json.loads((d1 / "_数据库" / "人物卡.json").read_text(encoding="utf-8"))
    facts = card["characters"][0]["locked_facts"]
    assert facts[0]["fact_key"] == "unmarried", "目标 fact 必须排第一（防 NLI 64 对截断饿死）"
    assert {f["fact_key"] for f in facts} == set(maker.FACTS.keys())
    clusters = json.loads((d1 / "_数据库" / "事件簇.json").read_text(encoding="utf-8"))
    assert clusters["clusters"][0]["narrative_mode"] == "linear"
    atlas = json.loads((d1 / "_数据库" / "地图.json").read_text(encoding="utf-8"))
    assert atlas["locations"] == maker.MAP_LOCATIONS


# ════════════════════════ 6. e2e 确定性回归锁 ════════════════════════

def test_numeric_sample_detected_by_locked_fact_scanner(built):
    import locked_fact_cross_scene_scanner as lf
    _, out, _ = built
    s = _sample_dir(out, "N1_numeric_age_cn")
    report = lf.scan(s, _draft_of(s))
    assert report["conflicts_count"] >= 1
    assert any(c["fact"] == maker.FACTS["age"] and c["conflict_value"] == "三十五岁"
               for c in report["conflicts"])
    # N3 跨句探针：设计性漏报（同句锚定边界）
    s3 = _sample_dir(out, "N3_numeric_cross_sentence_probe")
    report3 = lf.scan(s3, _draft_of(s3))
    assert report3["conflicts_count"] == 0


def test_temporal_and_spatial_samples_detected(built, monkeypatch):
    import draft_temporal_order_scanner as dt
    import spatial_continuity_scanner as sp
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    monkeypatch.setenv("SPATIAL_CONTINUITY_MODE", "active")
    _, out, _ = built
    t1 = _sample_dir(out, "T1_temporal_abs_reversal")
    rep_t = dt.scan(_draft_of(t1), project_root=t1, cluster_arg="cluster_001")
    assert any("第四天" in str((v.get("anchor_pair") or ["", ""])[1])
               for v in rep_t["violations"])
    s1 = _sample_dir(out, "S1_spatial_teleport_chain")
    rep_s = sp.scan(_draft_of(s1), project_root=s1, cluster_arg="cluster_001")
    got = {(v["from_location"], v["to_location"]) for v in rep_s["violations"]}
    assert ("义庄大厅", "乱葬岗") in got and ("乱葬岗", "钟楼顶层") in got


def test_baseline_zero_violations_all_layers(built, monkeypatch):
    import locked_fact_cross_scene_scanner as lf
    import draft_temporal_order_scanner as dt
    import spatial_continuity_scanner as sp
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    monkeypatch.setenv("SPATIAL_CONTINUITY_MODE", "active")
    _, out, _ = built
    b = _sample_dir(out, maker.BASELINE_ID)
    draft = _draft_of(b)
    assert lf.scan(b, draft)["conflicts_count"] == 0
    assert dt.scan(draft, project_root=b, cluster_arg="cluster_001")["violations"] == []
    assert sp.scan(draft, project_root=b, cluster_arg="cluster_001")["violations"] == []


# ════════════════════════ 7. 纯模板锁（LLM 增广路径不复活） ════════════════════════

def test_llm_augment_path_removed():
    """maker 是纯确定性模板注入器——LLM 增广符号与 mode 参数不存在（防复活锁）。"""
    import inspect
    assert not hasattr(maker, "_llm_augment")
    assert not hasattr(maker, "REAL_API_ENV")
    assert "mode" not in inspect.signature(maker.build_samples).parameters


# ════════════════════════ 8. runner 判中 + 矩阵聚合 ════════════════════════

def _gt_stub(**kw):
    gt = {"violated_fact": "秦烬二十九岁",
          "expect": {"conflict_value": "三十五岁"},
          "injection": {"char_offset": 100, "injected_text": "x" * 30}}
    gt.update(kw)
    return gt


def test_match_numeric_requires_fact_value_and_span():
    gt = _gt_stub()
    hit_report = {"conflicts": [{"fact": "秦烬二十九岁", "conflict_value": "三十五岁",
                                 "position": 110}]}
    assert runner._match_numeric(hit_report, gt) == (True, 0)
    # 值对但位置在注入区间外 → off_target 而非命中（防假召回）
    off_report = {"conflicts": [{"fact": "秦烬二十九岁", "conflict_value": "三十五岁",
                                 "position": 9999}]}
    assert runner._match_numeric(off_report, gt) == (False, 1)


def test_match_nli_distinguishes_skip_from_miss():
    gt = _gt_stub(violated_fact="秦烬未婚，没有妻子", expect={"kind": "nli_contradiction"})
    skipped = {"descriptive": {"executed": False, "note": "NLI 后端未启用", "violations": []}}
    hit, off, skip = runner._match_nli(skipped, gt)
    assert (hit, off) == (False, 0) and "NLI" in skip
    ran = {"descriptive": {"executed": True, "violations": [
        {"fact": "秦烬未婚，没有妻子", "position": 105}]}}
    assert runner._match_nli(ran, gt) == (True, 0, None)


def test_build_matrix_recall_and_baseline_fp():
    results = [
        {"flaw_type": "numeric",
         "detected": {"locked_fact_numeric": True, "locked_fact_nli": False,
                      "temporal_order": False, "spatial_continuity": False},
         "raw_counts": {"numeric_conflicts": 1, "nli_violations": 0,
                        "temporal_violations": 0, "spatial_violations": 0}},
        {"flaw_type": "numeric",
         "detected": {"locked_fact_numeric": False, "locked_fact_nli": False,
                      "temporal_order": False, "spatial_continuity": False},
         "raw_counts": {"numeric_conflicts": 0, "nli_violations": 0,
                        "temporal_violations": 0, "spatial_violations": 0}},
        {"flaw_type": "baseline",
         "detected": {k: False for k in runner.LAYERS},
         "raw_counts": {"numeric_conflicts": 0, "nli_violations": 2,
                        "temporal_violations": 0, "spatial_violations": 0}},
    ]
    m = runner.build_matrix(results)
    assert m["numeric"]["locked_fact_numeric"] == {"hit": 1, "total": 2, "recall": 0.5}
    assert m["baseline"]["false_positive_counts"]["locked_fact_nli"] == 2


# ════════════════════════ 9. runner e2e（合成样本全链）════════════════════════

def test_runner_run_sample_end_to_end(built, monkeypatch):
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    monkeypatch.setenv("SPATIAL_CONTINUITY_MODE", "active")
    _, out, _ = built
    res = runner.run_sample(_sample_dir(out, "N1_numeric_age_cn"))
    assert res["detected"]["locked_fact_numeric"] is True
    assert res["detected"]["temporal_order"] is False
    # NLI 未开门控 → 诚实 skip 而非假漏报归因
    assert res["nli_skip_reason"] is not None
