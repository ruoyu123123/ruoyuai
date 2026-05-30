"""L4 · build_manifest._collect_deep_writing_dims 回归测试 —— 北极星①+⑤。

钉死：writer manifest 必须注入 3 个深层创作维度提示（D1 心理距离 / D2 visceral-first 情绪 /
D3 动机弧光），且**纯 prompt 注入、零检测、永远 advisory**（顾问非法官）。

守护点：
  · 三维度提示文本始终存在（作者档有无都注入通用提示兜底）
  · 作者档有对应基线时以基线为准（第一权威）：
      D1 ← narrative_craft.narrative_distance_distribution + quantitative.inner_monologue_ratio
      D2 ← writing_techniques_b3_samples.dim25_psychology_technique
      D3 ← narrative_fingerprint.character_behavior_loops + character_depth_grade_distribution
  · 作者档缺该维度 → 通用兜底，_baseline_source 标「通用」
  · gate_level 永远 advisory，全 dict 中不得出现 hard_gate（北极星⑤）
  · 真原文矫枉过正：用蛊真人 / 惊悚乐园真作者原文档喂入，确认真作者不被误判
    （金标准 · memory reference-system-validation-method）—— 因为零检测，作者必然零误报
  · 接线进 full build_manifest() dict + _cache_layout（防写了没接线）
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402


def _mk_project(tmp: Path, *, style=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if style is not None:
        (db / "作者风格.json").write_text(
            json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return tmp


# ---------- 通用提示始终存在 ----------

def test_three_dims_always_present_no_style():
    """无作者风格.json → 仍注入 D1-D3 通用创作提示（从源头降问题率）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))  # 不写 style
        s = bm.DatabaseScanner(tmp, 1)
        r = bm._collect_deep_writing_dims(s)
        assert "D1_psychic_distance" in r
        assert "D2_visceral_first_emotion" in r
        assert "D3_motivation_arc" in r
        # 每个维度都有非空 tip 文本
        for key in ("D1_psychic_distance", "D2_visceral_first_emotion", "D3_motivation_arc"):
            assert isinstance(r[key].get("tip"), str) and len(r[key]["tip"]) > 20
            assert "通用" in r[key]["_baseline_source"]


def test_d1_tip_mentions_filter_words():
    """D1 提示必须含 Deep POV 滤镜词删除指引（想/觉得/感到/意识到/看到/听到）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        s = bm.DatabaseScanner(tmp, 1)
        tip = bm._collect_deep_writing_dims(s)["D1_psychic_distance"]["tip"]
        for w in ("想", "觉得", "感到", "意识到", "看到", "听到"):
            assert w in tip, f"D1 提示缺滤镜词 {w}"
        # 心理距离/意识呈现关键概念
        assert "心理距离" in tip or "psychic" in tip.lower()


def test_d2_tip_visceral_first_order():
    """D2 提示必须体现 visceral-first 顺序（生理→认知→命名情绪）+ 摔杯子范例。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        s = bm.DatabaseScanner(tmp, 1)
        tip = bm._collect_deep_writing_dims(s)["D2_visceral_first_emotion"]["tip"]
        assert "生理" in tip
        assert "杯子" in tip  # 呼应 CLAUDE.md「不写他感到愤怒，写他把杯子摔在地上」


def test_d3_tip_ghost_lie_want_need():
    """D3 提示必须含 Ghost/Lie/Want/Need 动机分层 + 反动机透明化。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        s = bm.DatabaseScanner(tmp, 1)
        tip = bm._collect_deep_writing_dims(s)["D3_motivation_arc"]["tip"]
        for token in ("Ghost", "Lie", "Want", "Need"):
            assert token in tip, f"D3 提示缺 {token}"
        assert "透明化" in tip


# ---------- 作者档基线优先（第一权威） ----------

def test_d1_uses_author_distance_distribution():
    """作者档有 narrative_distance_distribution + inner_monologue_ratio → D1 用作者基线。"""
    style = {
        "narrative_craft": {
            "narrative_distance_distribution": {
                "近距第三人称": 80, "中距第三人称": 40, "全知视角": 10, "第一人称": 3,
            },
        },
        "quantitative": {"inner_monologue_ratio": {"mean": 0.12, "std": 0.05}},
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), style=style)
        s = bm.DatabaseScanner(tmp, 1)
        d1 = bm._collect_deep_writing_dims(s)["D1_psychic_distance"]
        assert d1["_baseline_source"] == "作者档"
        # top 3 按计数降序
        assert d1["author_baseline_distance_top"][:2] == ["近距第三人称", "中距第三人称"]
        assert d1["author_inner_monologue_ratio_mean"] == 0.12


def test_d2_uses_author_psychology_sample():
    """作者档有 dim25_psychology_technique → D2 取首样本 note（截断 ≤160）。"""
    long_note = "外显行为暗示为主" * 40  # 远超 160
    style = {
        "writing_techniques_b3_samples": {
            "dim25_psychology_technique": [
                {"chapter": 1, "note": long_note},
                {"chapter": 2, "note": "排比吐槽"},
            ],
        },
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), style=style)
        s = bm.DatabaseScanner(tmp, 1)
        d2 = bm._collect_deep_writing_dims(s)["D2_visceral_first_emotion"]
        assert d2["_baseline_source"] == "作者档"
        assert d2["author_psychology_sample"].startswith("外显行为暗示为主")
        assert len(d2["author_psychology_sample"]) <= 160  # context 预算截断


def test_d3_uses_author_behavior_loops():
    """作者档有 character_behavior_loops + depth 分布 → D3 用作者基线。"""
    style = {
        "narrative_fingerprint": {
            "character_behavior_loops": {"观察→推理": 6, "试探→反杀": 3, "隐忍→爆发": 2, "多余": 99},
            "character_depth_grade_distribution": {"其他": 249, "极高": 1, "高": 5},
        },
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), style=style)
        s = bm.DatabaseScanner(tmp, 1)
        d3 = bm._collect_deep_writing_dims(s)["D3_motivation_arc"]
        assert d3["_baseline_source"] == "作者档"
        # 只取前 3 个（context 预算）
        assert len(d3["author_behavior_loops"]) == 3
        assert "观察→推理" in d3["author_behavior_loops"]
        assert len(d3["author_depth_grade_distribution_top"]) == 3


def test_partial_style_mixes_baseline_and_generic():
    """作者档只有 D1 基线、缺 D2/D3 → D1=作者档、D2/D3=通用兜底（混合不崩）。"""
    style = {
        "narrative_craft": {"narrative_distance_distribution": {"近距第三人称": 50}},
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), style=style)
        s = bm.DatabaseScanner(tmp, 1)
        r = bm._collect_deep_writing_dims(s)
        assert r["D1_psychic_distance"]["_baseline_source"] == "作者档"
        assert "通用" in r["D2_visceral_first_emotion"]["_baseline_source"]
        assert "通用" in r["D3_motivation_arc"]["_baseline_source"]


# ---------- 北极星⑤：顾问非法官（advisory only · 零检测） ----------

def test_advisory_gate_never_hard():
    """gate_level 必须 advisory，全 dict JSON 中不得出现 hard_gate 字样。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        s = bm.DatabaseScanner(tmp, 1)
        r = bm._collect_deep_writing_dims(s)
        assert r["gate_level"] == "advisory"
        assert r["advisory_only"] is True
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)


def test_no_scanner_no_detection_keys():
    """纯 prompt 注入：产出里不得有任何检测/判决性键（issue/violation/code/score）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        s = bm.DatabaseScanner(tmp, 1)
        blob = json.dumps(bm._collect_deep_writing_dims(s), ensure_ascii=False).lower()
        for forbidden in ("\"issues\"", "\"violation", "\"gate_level\": \"hard", "verdict", "judgment"):
            assert forbidden not in blob, f"出现判决性痕迹 {forbidden}"


# ---------- 真原文矫枉过正（金标准） ----------

def _real_style_path(book: str) -> Path:
    return _ROOT / "workspace" / "styles" / book / "作者风格.json"


def test_real_author_guzhenren_not_misjudged():
    """蛊真人真作者原文档喂入 → 零误报（纯注入无检测）+ 至少捡到一个真实基线。"""
    sp = _real_style_path("蛊真人")
    if not sp.exists():
        return  # 真原文缺失则跳过（不让本地环境差异挂测试）
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        (tmp / "_数据库" / "作者风格.json").write_text(
            sp.read_text(encoding="utf-8"), encoding="utf-8")
        s = bm.DatabaseScanner(tmp, 1)
        r = bm._collect_deep_writing_dims(s)
        # 金标准：零检测 → 真作者绝不被判 hard_gate
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
        # 蛊真人有 dim25_psychology_technique → D2 应捡到真实基线
        assert r["D2_visceral_first_emotion"]["_baseline_source"] == "作者档"


def test_real_author_jingsong_not_misjudged():
    """惊悚乐园真作者原文档喂入 → 零误报 + D1/D3 捡到真实基线。"""
    sp = _real_style_path("惊悚乐园")
    if not sp.exists():
        return
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        (tmp / "_数据库" / "作者风格.json").write_text(
            sp.read_text(encoding="utf-8"), encoding="utf-8")
        s = bm.DatabaseScanner(tmp, 1)
        r = bm._collect_deep_writing_dims(s)
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
        # 惊悚乐园有 narrative_distance_distribution + behavior_loops → D1/D3 真实基线
        assert r["D1_psychic_distance"]["_baseline_source"] == "作者档"
        assert r["D3_motivation_arc"]["_baseline_source"] == "作者档"


# ---------- 接线进 full manifest（防写了没接线） ----------

def test_wired_into_final_manifest():
    """字段必须真正出现在 build_manifest() dict + _cache_layout STATIC 段里。"""
    cards = [{"id": "lu", "name": "陆建国", "role": "主角"}]
    clusters = [{"cluster_id": "cluster_001", "chapter_range": [1, 4]}]
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        (tmp / "_数据库" / "人物卡.json").write_text(
            json.dumps({"characters": cards}, ensure_ascii=False), encoding="utf-8")
        (tmp / "_数据库" / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
        prog = {
            "volumes": [{"vol": 1, "title": "第一卷", "chapter_range": [1, 8]}],
            "cluster_blueprint": {
                "cluster_001": {
                    "chapter_range": [1, 4],
                    "scene_storyboard": [
                        {"ch": 1, "characters": ["陆建国"], "key_events": ["开局"],
                         "scene_type": ["悬疑"], "summary": "陆建国值夜班接待第一位访客"}
                    ],
                }
            },
        }
        (tmp / "_数据库" / "进度.json").write_text(
            json.dumps(prog, ensure_ascii=False), encoding="utf-8")
        m = bm.build_manifest(tmp, 1)
        assert m["preflight"]["passed"], m["preflight"]
        assert "deep_writing_dims" in m
        dw = m["deep_writing_dims"]
        assert dw["gate_level"] == "advisory"
        assert "D1_psychic_distance" in dw and "D2_visceral_first_emotion" in dw and "D3_motivation_arc" in dw
        # cache_layout STATIC 段登记
        static = m["_cache_layout"].get("STATIC_99_cacheable", [])
        assert "deep_writing_dims" in static


def test_full_manifest_regression_unbroken():
    """回归 0：新字段不破坏现有 manifest 结构（关键既有字段仍在）。"""
    cards = [{"id": "lu", "name": "陆建国", "role": "主角"}]
    clusters = [{"cluster_id": "cluster_001", "chapter_range": [1, 4]}]
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        (tmp / "_数据库" / "人物卡.json").write_text(
            json.dumps({"characters": cards}, ensure_ascii=False), encoding="utf-8")
        (tmp / "_数据库" / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
        prog = {
            "volumes": [{"vol": 1, "title": "第一卷", "chapter_range": [1, 8]}],
            "cluster_blueprint": {
                "cluster_001": {
                    "chapter_range": [1, 4],
                    "scene_storyboard": [
                        {"ch": 1, "characters": ["陆建国"], "key_events": ["开局"],
                         "scene_type": ["悬疑"], "summary": "值夜班"}
                    ],
                }
            },
        }
        (tmp / "_数据库" / "进度.json").write_text(
            json.dumps(prog, ensure_ascii=False), encoding="utf-8")
        m = bm.build_manifest(tmp, 1)
        # 既有关键字段不被破坏
        for k in ("chapter", "must_read", "hard_constraints", "distill_voice_packs_reference",
                  "_cache_layout", "_critical_summary", "writer_mode"):
            assert k in m, f"既有字段 {k} 丢失（回归）"
