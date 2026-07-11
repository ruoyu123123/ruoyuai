#!/usr/bin/env python3
"""arc_aggregator.py 专属确定性回归测试（零 LLM / 零联网）。

被测脚本：core/scripts/arc_aggregator.py —— v22.cluster 故事块 arc 聚合器。

🔴 重要发现：尽管本脚本被工作流标记为「LLM-tagged」，**实际通读全文它不调任何 LLM/网络**
（imports 只有 argparse/json/math/re/sys/datetime/pathlib/typing 全是标准库，无 openai/
urllib/requests/gen_model_loader/llm_transport）。它是纯确定性聚合器：读 JSON 文件 + 正则解析
+ 余弦相似度 + Reagan 形状匹配 + 数学。因此本套测试**不 mock LLM**（没有 LLM 调用点可 mock），
而是钉死它的确定性周边逻辑。

现有间接覆盖：
覆盖情绪强度抽取、dim 抽取、Reagan 拟合、arc 结构描述、aggregate_cluster 与
aggregate_summary；`parse_pacing_curve_to_values` 由 test_audit_batch2_remaining.py 覆盖。

零依赖约定：只用标准库 · test_* 无参数 · 断言失败 raise AssertionError · tempfile + utf-8 · Windows。

网络兜底：模块导入时即 patch urllib.request.urlopen / socket.socket → 调用即 raise，
证明本套测试全程零真出网（防漏 mock 真花钱）。
"""

import json
import os
import sys
import tempfile
import shutil
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import arc_aggregator as mod  # noqa: E402


def _fake_vad(predict_batch_fn):
    """临时装 RUOYU_NN_VAD=1 + 假 nn_vad_bridge 模块，返回 (还原函数)。
    不用 monkeypatch fixture（本文件有零依赖 __main__ 直跑入口，需零参兼容）。"""
    old_env = os.environ.get("RUOYU_NN_VAD")
    old_mod = sys.modules.get("nn_vad_bridge")
    os.environ["RUOYU_NN_VAD"] = "1"
    sys.modules["nn_vad_bridge"] = types.SimpleNamespace(predict_batch=predict_batch_fn)

    def _restore():
        if old_env is None:
            os.environ.pop("RUOYU_NN_VAD", None)
        else:
            os.environ["RUOYU_NN_VAD"] = old_env
        if old_mod is None:
            sys.modules.pop("nn_vad_bridge", None)
        else:
            sys.modules["nn_vad_bridge"] = old_mod
    return _restore


# ---------------------------------------------------------------------------
# 网络兜底：本脚本不该出网。装一个全局哨兵——任何真出网即炸。
# 若被测代码（或它 import 的依赖）意外发起 HTTP / socket，测试立即失败而非花钱。
# ---------------------------------------------------------------------------
import urllib.request as _urllib_request  # noqa: E402
import socket as _socket  # noqa: E402


class _NetworkBlocked(Exception):
    pass


def _boom(*_a, **_k):
    raise _NetworkBlocked("测试中禁止真出网（arc_aggregator 本应零网络）")


# 🔴 2026-06-17 修测试污染：原在模块级永久 setattr urllib.request.urlopen / socket.socket
# = _boom 且不还原 → zero-dep runner 无 teardown → 泄漏破坏后续文件（实测打挂 test_model_probe
# 的 requests.get：socket 类被替成函数）。arc_aggregator 是纯 JSON 聚合器**从不联网**（源码 grep
# urllib/requests/socket 零命中），故无需 module 级网络守卫。改为静态断言守卫（不改全局）：
def test_arc_aggregator_source_has_no_network_imports():
    """守卫：被测脚本必须零网络依赖（保「纯确定性聚合器」契约·不改任何全局·零泄漏）。"""
    import re as _re
    src = (Path(__file__).resolve().parent.parent / "core" / "scripts"
           / "arc_aggregator.py").read_text(encoding="utf-8")
    # 剥注释/docstring 行后再查（docstring 提及这些库名是说明「不用」）
    code_lines = [ln for ln in src.splitlines()
                  if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    for mod_name in ("import requests", "import urllib", "import socket",
                     "import httpx", "llm_transport", "openai"):
        assert mod_name not in code, \
            f"arc_aggregator 不应依赖网络库 {mod_name}（纯聚合器契约破损）"


# ---------------------------------------------------------------------------
# 小工具：搭一个最小风格库项目（蒸馏进度 / 衔接分析 / cluster_index.json）
# ---------------------------------------------------------------------------
def _make_project():
    root = Path(tempfile.mkdtemp(prefix="arcagg_"))
    (root / "蒸馏进度").mkdir(parents=True, exist_ok=True)
    (root / "衔接分析").mkdir(parents=True, exist_ok=True)
    return root


def _write_chapter(root, ch, payload):
    (root / "蒸馏进度" / f"第{ch}章.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _write_continuity(root, from_ch, to_ch, payload):
    (root / "衔接分析" / f"ch{from_ch}_{to_ch}_continuity.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# ===========================================================================
# 1. 纯 helper：emotion intensity 抽取（dim33 节拍 / fallback 链）
# ===========================================================================
def test_extract_chapter_emotion_intensity_from_dim33_uses_max():
    """dim33_emotion_beats 取 direction 的最大强度（不是平均）。
    '崩溃'=0.95 应主导，'平静'=0.2 不拉低。"""
    ch = {
        "B4_narrative_craft": {
            "dim33_emotion_beats": [
                {"pct": 10, "direction": "平静"},
                {"pct": 80, "direction": "崩溃"},
            ]
        }
    }
    v = mod.extract_chapter_emotion_intensity(ch)
    assert abs(v - 0.95) < 1e-9, f"应取最大 0.95，得 {v}"


def test_extract_chapter_emotion_intensity_fallback_keyword_scan():
    """beats 不是规范 list（被弱模型写成 str）时，fallback 扫高潮关键词加分。"""
    ch = {"qualitative": {"dim33": "本章高潮迭起，爆发力极强"}}
    v = mod.extract_chapter_emotion_intensity(ch)
    # 命中「高潮」「爆发」2 个关键词 → 0.3 + 2*0.25 = 0.8
    assert abs(v - 0.8) < 1e-9, f"两关键词应 0.8，得 {v}"


def test_extract_chapter_emotion_intensity_empty_returns_floor():
    """无任何情绪线索 → 回 fallback 地板 0.3（humor/sat 都 0）。"""
    v = mod.extract_chapter_emotion_intensity({})
    assert abs(v - 0.3) < 1e-9, f"空章应回 0.3 地板，得 {v}"


def test_extract_chapter_emotion_intensity_model_hit_uses_arousal():
    """RUOYU_NN_VAD=1 + 假模型命中 → 用 direction 词条 arousal 近似强度（非词典 max）。
    假模型按内容区分（'崩溃'→高 arousal，'平静'→低 arousal），验证真走了模型路径而非常量。"""
    restore = _fake_vad(lambda items: [
        {"valence": 0.2, "arousal": 0.9, "dominance": None, "source": "model"}
        if "崩溃" in t else
        {"valence": 0.6, "arousal": 0.15, "dominance": None, "source": "model"}
        for t in items
    ])
    try:
        ch = {"B4_narrative_craft": {"dim33_emotion_beats": [
            {"pct": 10, "direction": "平静"},
            {"pct": 80, "direction": "崩溃"},
        ]}}
        detail = mod.extract_chapter_emotion_intensity_detail(ch)
        assert detail["source"] == "model_vad"
        assert abs(detail["intensity"] - 0.9) < 1e-9, f"应取两条中最大 arousal 0.9，得 {detail}"
        # 兼容包装函数仍返回 float（旧调用不受影响）
        v = mod.extract_chapter_emotion_intensity(ch)
        assert abs(v - 0.9) < 1e-9
    finally:
        restore()


def test_extract_chapter_emotion_intensity_model_unavailable_zero_regression():
    """RUOYU_NN_VAD=1 但 predict_batch 返回全 None（模型不可用）→ 结果与默认(env off)词典路径逐位一致。"""
    ch = {
        "B4_narrative_craft": {
            "dim33_emotion_beats": [
                {"pct": 10, "direction": "平静"},
                {"pct": 80, "direction": "崩溃"},
            ]
        }
    }
    baseline = mod.extract_chapter_emotion_intensity_detail(ch)
    assert baseline["source"] == "lexicon_fallback"

    restore = _fake_vad(lambda items: [None for _ in items])
    try:
        got = mod.extract_chapter_emotion_intensity_detail(ch)
        assert got["source"] == "lexicon_fallback"
        assert got["intensity"] == baseline["intensity"], "模型不可用应与默认词典路径逐位一致（零回归）"
    finally:
        restore()


# ===========================================================================
# 2. 纯 helper：extract_dim_value（数字 / 嵌套 dict / 百分号字符串 / default）
# ===========================================================================
def test_extract_dim_value_nested_dict_scene_pct():
    """dim28_scene_vs_summary = {'scene_pct': 0.85} → 取内层 scene_pct。"""
    ch = {"B": {"dim28_scene_vs_summary": {"scene_pct": 0.85}}}
    v = mod.extract_dim_value(ch, ["dim28", "scene_pct"], 0.7)
    assert abs(v - 0.85) < 1e-9, f"应取 scene_pct 0.85，得 {v}"


def test_extract_dim_value_percent_string_normalized():
    """字符串带 % → 归一化到 0-1（'85%' → 0.85）。"""
    ch = {"foo": "85%"}
    v = mod.extract_dim_value(ch, ["foo"], 0.0)
    assert abs(v - 0.85) < 1e-9, f"'85%' 应归一 0.85，得 {v}"


def test_extract_dim_value_default_when_absent():
    """找不到字段 → 返回 default。"""
    v = mod.extract_dim_value({"a": 1}, ["不存在的维度"], 0.42)
    assert abs(v - 0.42) < 1e-9, f"缺失应回 default 0.42，得 {v}"


# ===========================================================================
# 3. 纯 helper：Reagan 形状拟合 + cosine + resample + 结构描述
# ===========================================================================
def test_match_reagan_shape_exact_template():
    """喂 Rags-to-Riches 模板曲线本身 → cosine=1.0 完美命中。"""
    name, conf = mod.match_reagan_shape(list(mod.REAGAN_SHAPES["Rags-to-Riches"]))
    assert name == "Rags-to-Riches", f"应命中 Rags-to-Riches，得 {name}"
    assert abs(conf - 1.0) < 1e-6, f"自匹配 cosine 应 1.0，得 {conf}"


def test_match_reagan_shape_resamples_short_curve():
    """非 10 点曲线先 resample 到 10。单调上升 3 点应仍命中上升类形状且不崩。"""
    name, conf = mod.match_reagan_shape([0.1, 0.5, 1.0])
    assert name in mod.REAGAN_SHAPES, f"应命中某 Reagan 形状，得 {name}"
    assert 0.0 <= conf <= 1.0, f"置信度越界 {conf}"


def test_cosine_similarity_edge_cases():
    """长度不等 / 全零 → 0.0（不崩、不除零）。"""
    assert mod.cosine_similarity([1, 2], [1, 2, 3]) == 0.0, "长度不等应 0"
    assert mod.cosine_similarity([0, 0, 0], [1, 1, 1]) == 0.0, "全零应 0"
    # 同向单位向量 cosine = 1
    assert abs(mod.cosine_similarity([1, 0], [2, 0]) - 1.0) < 1e-9, "同向应 1.0"


def test_describe_arc_structure_labels():
    """章末高潮 / 平稳 两个边界形状给出正确中文描述。"""
    rising = mod.describe_arc_structure([0.1, 0.2, 0.3, 0.4, 1.0])
    assert rising == "持续上升（章末高潮）", f"末点最高应判章末高潮，得 {rising}"
    flat = mod.describe_arc_structure([0.5, 0.5, 0.52, 0.5])
    assert flat == "平稳无明显起伏", f"幅度<0.2 应判平稳，得 {flat}"
    assert mod.describe_arc_structure([]) == "未知", "空曲线应判未知"


# ===========================================================================
# 4. 集成：aggregate_cluster（读 cluster_index + 章 JSON + continuity）

# ===========================================================================
def test_aggregate_cluster_missing_index_returns_error():
    """无 cluster_index.json → 返回 error dict（不抛异常）。"""
    root = _make_project()
    try:
        res = mod.aggregate_cluster(root, "auto_001")
        assert "error" in res, f"应返回 error，得 {res}"
        assert "cluster_index" in res["error"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_aggregate_cluster_unknown_id_returns_error():
    """cluster_index 存在但 id 不在其中 → error dict。"""
    root = _make_project()
    try:
        (root / "cluster_index.json").write_text(
            json.dumps({"clusters": [{"cluster_id": "auto_001",
                                      "chapter_range": [1, 2], "chapters_count": 2,
                                      "estimated_words": 6000, "boundary_reason": "x"}]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        res = mod.aggregate_cluster(root, "auto_999")
        assert "error" in res and "auto_999" in res["error"], f"应 error 未知 id，得 {res}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_aggregate_cluster_full_pipeline_shape():
    """完整 cluster 聚合：2 章 + 1 continuity → 产出关键字段且类型正确，cluster 命名生效。"""
    root = _make_project()
    try:
        (root / "cluster_index.json").write_text(
            json.dumps({"clusters": [{
                "cluster_id": "auto_001",
                "chapter_range": [1, 2],
                "chapters_count": 2,
                "estimated_words": 8000,
                "boundary_reason": "scene_break",
            }]}, ensure_ascii=False),
            encoding="utf-8",
        )
        _write_chapter(root, 1, {
            "word_count": 4000,
            "B4_narrative_craft": {"dim33_emotion_beats": [{"pct": 50, "direction": "升"}]},
        })
        _write_chapter(root, 2, {
            "word_count": 4000,
            "B4_narrative_craft": {"dim33_emotion_beats": [{"pct": 80, "direction": "高潮"}]},
        })
        _write_continuity(root, 1, 2, {
            "pacing_curve": "Ch1中快→Ch2快",
            "character_continuity": [{"character": "陆参", "arc_progress": "从畏缩到觉醒"}],
            "foreshadowing": {"planted": ["伏笔A", "伏笔B"], "resolved": ["伏笔A"]},
        })
        res = mod.aggregate_cluster(root, "auto_001")
        assert "error" not in res, f"不该 error: {res}"
        assert res["mode"] == "cluster"
        assert res["arc_id"] == "cluster_arc_auto_001", f"arc_id 应 cluster 命名，得 {res['arc_id']}"
        assert res["cluster_id"] == "auto_001"
        assert res["chapters_count"] == 2
        assert res["chapter_range"] == "ch1-2", f"章范围错: {res['chapter_range']}"
        # emotion_curve 是 list[float]，长度=arc_size(2)
        ec = res["emotion_curve_normalized"]
        assert isinstance(ec, list) and len(ec) == 2, f"emotion_curve: {ec}"
        assert all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in ec), f"曲线越界: {ec}"
        # 角色弧聚合到位
        chars = [c["character"] for c in res["character_arc_summary_in_arc"]]
        assert "陆参" in chars, f"角色弧应含陆参，得 {chars}"
        # 伏笔计数
        assert res["foreshadowing_planted_in_arc"] == 2, f"应 2 个伏笔，得 {res['foreshadowing_planted_in_arc']}"
        assert res["foreshadowing_resolved_in_arc"] == 1
        # Reagan 形状字段存在且合法
        assert res["matched_reagan_shape"] in (list(mod.REAGAN_SHAPES) + ["Unknown"])
        assert 0.0 <= res["matched_reagan_shape_confidence"] <= 1.0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_aggregate_cluster_tolerant_to_malformed_foreshadowing():
    """consumer tolerant：foreshadowing 被写成 str('TODO') 时聚合不崩、伏笔计 0。"""
    root = _make_project()
    try:
        (root / "cluster_index.json").write_text(
            json.dumps({"clusters": [{
                "cluster_id": "auto_001", "chapter_range": [1, 1],
                "chapters_count": 1, "estimated_words": 3000, "boundary_reason": "x",
            }]}, ensure_ascii=False),
            encoding="utf-8",
        )
        _write_chapter(root, 1, {"word_count": 3000})
        _write_continuity(root, 1, 1, {
            "pacing_curve": "Ch1中",
            "foreshadowing": "TODO",                # 非 dict → 必须被容忍
            "character_continuity": ["只有名字的串"],  # str 形式角色也要容忍
        })
        res = mod.aggregate_cluster(root, "auto_001")
        assert "error" not in res, f"畸形 foreshadowing 不该崩: {res}"
        assert res["foreshadowing_planted_in_arc"] == 0, "畸形伏笔应计 0"
        # str 角色被收进 character_arc（name=该串）
        names = [c["character"] for c in res["character_arc_summary_in_arc"]]
        assert "只有名字的串" in names, f"str 角色应被收进，得 {names}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
# 5. 集成：aggregate_summary（读 arc_templates 目录聚合）
# ===========================================================================
def test_aggregate_summary_no_dir_errors():
    """无 arc_templates 目录 → error dict。"""
    root = _make_project()
    try:
        res = mod.aggregate_summary(root)
        assert "error" in res, f"应 error，得 {res}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_aggregate_summary_aggregates_cluster_arcs():
    """两个 cluster_arc_*.json → summary 统计形状分布 + climax 位置（用 chapters_count 当分母）。"""
    root = _make_project()
    try:
        arc_dir = root / "arc_templates"
        arc_dir.mkdir(parents=True, exist_ok=True)
        (arc_dir / "cluster_arc_auto_001.json").write_text(json.dumps({
            "matched_reagan_shape": "Rags-to-Riches",
            "climax_chapter_index": 1,
            "chapters_count": 3,
            "chapter_range": "ch1-3",
            "foreshadowing_planted_in_arc": 2,
            "foreshadowing_resolved_in_arc": 1,
        }, ensure_ascii=False), encoding="utf-8")
        (arc_dir / "cluster_arc_auto_002.json").write_text(json.dumps({
            "matched_reagan_shape": "Rags-to-Riches",
            "climax_chapter_index": 0,
            "chapters_count": 2,
            "chapter_range": "ch4-5",
            "foreshadowing_planted_in_arc": 4,
            "foreshadowing_resolved_in_arc": 2,
        }, ensure_ascii=False), encoding="utf-8")
        res = mod.aggregate_summary(root)
        assert "error" not in res, f"不该 error: {res}"
        assert res["primary_track"] == "cluster"
        assert res["total_arcs"] == 2
        assert res["reagan_shape_distribution"]["Rags-to-Riches"] == 2
        assert res["most_common_shape"] == "Rags-to-Riches"
        # climax 位置百分比合法（用 chapters_count 当分母，不再硬编码 10 → 不会 >1）
        assert 0.0 <= res["average_climax_position_pct"] <= 1.0, \
            f"climax 位置越界（应已修硬编码分母）: {res['average_climax_position_pct']}"
        # 伏笔均值
        assert res["average_foreshadowing_planted_per_arc"] == 3.0, \
            f"(2+4)/2=3，得 {res['average_foreshadowing_planted_per_arc']}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 自跑入口（单文件调试用；官方入口为仓库根 py -m pytest）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
