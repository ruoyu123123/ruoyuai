"""distill_replicate 同栈契约锁。

复刻 = Claude agent 按 skill 亲笔写复刻场景稿（--claude-scenes-dir）
→ 本脚本用 gemini 按 skill 分段润色（段级字数守恒带 [0.85, 1.30]）→ 拼接落盘评分。

本文件只测确定性的 prompt、常量、场景发现、argparse required 与单一同栈入口。
"""
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import distill_replicate as dr  # noqa: E402


def _make_scene_text(marker: str = "复刻场景") -> str:
    """造 ≥200 CJK 的场景稿文本（过 discover 的「每场景写透」闸）。"""
    return ("他停下脚步，看着门外的雨幕，" + marker + "里每个人都屏住了呼吸。") * 12


# ════════════════════════════════════════════════════════════════
# [A] 守恒带常量（与 gen_writer 同栈同款 · 同一把尺）
# ════════════════════════════════════════════════════════════════

def test_A_conservation_band_constants():
    """段级字数守恒带 = [0.85, 1.30]（压缩省略 / 注水扩写红线）。"""
    assert dr.POLISH_CJK_LOW == 0.85
    assert dr.POLISH_CJK_HIGH == 1.30


def test_A_conservation_band_matches_gen_writer():
    """v29 同栈铁律：distill_replicate 守恒带必须与 gen_writer 完全一致（同一把尺）。"""
    import gen_writer as gw
    assert dr.POLISH_CJK_LOW == gw.POLISH_CJK_LOW
    assert dr.POLISH_CJK_HIGH == gw.POLISH_CJK_HIGH


# ════════════════════════════════════════════════════════════════
# [B] build_polish_subcall_prompt（守恒数字 + 原文段 + skill/ref/seed 注入）
# ════════════════════════════════════════════════════════════════

def test_B_prompt_contains_conservation_numbers():
    """润色 prompt 必须带明确守恒字数区间 [int(src×0.85), int(src×1.30)] + 初稿字数（等体量指令）。"""
    scene = _make_scene_text()
    src_cjk = dr.cjk_count(scene)
    lo = int(src_cjk * dr.POLISH_CJK_LOW)
    hi = int(src_cjk * dr.POLISH_CJK_HIGH)
    p = dr.build_polish_subcall_prompt("SKILL", "", scene, idx=0, total=3)
    assert str(lo) in p
    assert str(hi) in p
    assert str(src_cjk) in p


def test_B_prompt_contains_scene_text():
    """润色 prompt 必须内嵌本段 Claude 亲笔初稿全文（润色对象 = 原文段）。"""
    scene = _make_scene_text("独一无二锚点串")
    p = dr.build_polish_subcall_prompt("SKILL", "", scene, idx=0, total=1)
    assert scene in p
    assert "初稿" in p


def test_B_prompt_injects_skill_before_seed():
    """skill 全文注入 · 且在种子段之前（skill = 第一权威 · 种子是 skill 后的语感起手势）。"""
    seed = "# 语感种子\n\n某段真实原文语感锚点。"
    p = dr.build_polish_subcall_prompt("我的SKILL文本", "", _make_scene_text(),
                                       idx=1, total=2, seed_section=seed)
    assert "我的SKILL文本" in p
    assert p.index("我的SKILL文本") < p.index("语感种子")


def test_B_prompt_injects_ref_text():
    """同源 ref_text 作为语感参照注入（仅语感 · 不照抄情节）。"""
    p = dr.build_polish_subcall_prompt("SKILL", "参考原文片段独特串", _make_scene_text(),
                                       idx=0, total=1)
    assert "参考原文片段独特串" in p
    assert "参考原文" in p


def test_B_prompt_header_shows_segment_index():
    """润色 prompt 头显示第 idx+1/total 段（1-based 展示）。"""
    p = dr.build_polish_subcall_prompt("SKILL", "", _make_scene_text(), idx=2, total=5)
    assert "第 3/5 段" in p


def test_B_prompt_polish_not_generate_semantics():
    """v29 语义：润色（重写不改骨架）· 非从零生成 · 禁 JSON/标题/解释。"""
    p = dr.build_polish_subcall_prompt("SKILL", "", _make_scene_text(), idx=0, total=1)
    assert "润色" in p
    assert "情节走向" in p and "完全不变" in p
    assert "不要输出任何 JSON" in p
    # 旧从零生成输出指令彻底不出现
    assert "直接输出复刻正文" not in p


def test_B_prompt_default_no_seed():
    """seed_section 默认 "" → 无种子段（零回归 · 不凭空冒出种子标题）。"""
    p = dr.build_polish_subcall_prompt("SKILL", "", _make_scene_text(), idx=0, total=1)
    assert "语感种子" not in p


# ════════════════════════════════════════════════════════════════
# [C] REPLICATE_SYSTEM_PROMPT 润色语义（保留「作者本人」框架 · 无 CoT）
# ════════════════════════════════════════════════════════════════

def test_C_system_prompt_polish_semantics():
    """system prompt = 润色语义（作者本人 + 等体量重写 + 只改笔法不改骨架）。"""
    s = dr.REPLICATE_SYSTEM_PROMPT
    assert "润色" in s
    assert "本人" in s  # 「你就是这位源作者本人」框架
    assert "不改故事骨架" in s or "只改笔法" in s
    assert "等体量" in s


def test_C_system_prompt_no_cot_no_fromzero():
    """system prompt 不再含 CoT「先分析后写」/ 从零「自创场景」指令（栈已转润色）。"""
    s = dr.REPLICATE_SYSTEM_PROMPT
    assert "先分析后写" not in s
    assert "自创场景" not in s


# ════════════════════════════════════════════════════════════════
# [D] discover_claude_scenes_dir（v29 required 前置 · 缺/空/过短 → exit 2）
# ════════════════════════════════════════════════════════════════

def test_D_missing_dir_exits_2():
    """场景稿目录不存在 → [ERROR] + exit 2（不兼容不降级 · 绝不回退从零生成）。"""
    with pytest.raises(SystemExit) as ei:
        dr.discover_claude_scenes_dir(Path(tempfile.gettempdir()) / "不存在的复刻目录_v29_xyz")
    assert ei.value.code == 2


def test_D_empty_dir_exits_2():
    """目录存在但无 scene_*.txt → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(SystemExit) as ei:
            dr.discover_claude_scenes_dir(Path(d))
        assert ei.value.code == 2


def test_D_short_scene_exits_2():
    """单场景稿 <200 CJK（梗概占位）→ exit 2（Claude 复刻草稿必须每场景写透）。"""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "scene_00.txt").write_text("太短了，占位梗概。", encoding="utf-8")
        with pytest.raises(SystemExit) as ei:
            dr.discover_claude_scenes_dir(Path(d))
        assert ei.value.code == 2


def test_D_happy_path_returns_sorted_scenes():
    """多场景稿 → 按文件名排序返回 [(name, text), ...]（per-scene = 天然润色分段）。"""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "scene_02.txt").write_text(_make_scene_text("场景二"), encoding="utf-8")
        (Path(d) / "scene_00.txt").write_text(_make_scene_text("场景零"), encoding="utf-8")
        (Path(d) / "scene_01.txt").write_text(_make_scene_text("场景一"), encoding="utf-8")
        scenes = dr.discover_claude_scenes_dir(Path(d))
        assert [n for n, _ in scenes] == ["scene_00.txt", "scene_01.txt", "scene_02.txt"]
        assert all(dr.cjk_count(t) >= 200 for _, t in scenes)
        assert "场景零" in scenes[0][1]


def test_D_ignores_non_scene_files():
    """只认 scene_*.txt（changes_claude.json 等其他文件忽略）。"""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "scene_00.txt").write_text(_make_scene_text(), encoding="utf-8")
        (Path(d) / "cluster_001_changes_claude.json").write_text("{}", encoding="utf-8")
        scenes = dr.discover_claude_scenes_dir(Path(d))
        assert [n for n, _ in scenes] == ["scene_00.txt"]


# ════════════════════════════════════════════════════════════════
# [E] argparse：--claude-scenes-dir required
# ════════════════════════════════════════════════════════════════

def test_E_claude_scenes_dir_required(monkeypatch):
    """缺 --claude-scenes-dir → argparse SystemExit(2)（v29 required 前置 · argparse 层拦）。"""
    monkeypatch.setattr(dr, "check_deps", lambda: None)
    monkeypatch.setattr(sys, "argv", [
        "distill_replicate.py",
        "--style-skill", "x.md", "--output", "y.txt",
        "--cluster-ref", "cluster_001", "--project", "p"])
    with pytest.raises(SystemExit) as ei:
        dr.main()
    assert ei.value.code == 2


# ════════════════════════════════════════════════════════════════
# [F] 公共接口只保留同栈复刻
# ════════════════════════════════════════════════════════════════

def test_F_from_zero_and_cot_symbols_removed():
    """同栈入口不暴露从零生成或 subcall 规划符号。"""
    for sym in ("build_cluster_subcall_prompt", "plan_cluster_subcalls",
                "subcall_max_tokens", "estimate_words_per_chapter",
                "strip_cot_analysis", "_l3b_cot_first_mode",
                "REPLICATE_SYSTEM_PROMPT_COT", "COT_FIRST_DIRECTIVE",
                "COT_ANALYSIS_MARKER", "COT_BODY_MARKER"):
        assert not hasattr(dr, sym), f"v29 已删机制不应复活: {sym}"


def test_F_critic_refine_and_rubric_removed():
    """非润色 API 调用面已收敛：L3d critic-refine 循环与 A12 rubric judge 不存在
    （API 面 = 纯润色主循环 · 防复活锁）。"""
    for sym in ("draft_refine_loop", "_knockout_accept", "_draft_refine_mode",
                "_draft_refine_rounds", "build_critic_prompt", "build_refine_prompt",
                "_extract_contract_excerpt", "DRAFT_CRITIC_DIMENSIONS",
                "distill_rubric", "_rubric_call"):
        assert not hasattr(dr, sym), f"已删非润色 API 路径不应复活: {sym}"
    # 消融同一把尺的确定性 SFS 评分（style_evaluator·非 LLM 调用）保留
    assert hasattr(dr, "_score_draft_sfs")


def test_F_v29_symbols_present():
    """v29 同栈符号就位：build_polish_subcall_prompt + discover_claude_scenes_dir + 守恒常量。"""
    assert hasattr(dr, "build_polish_subcall_prompt")
    assert hasattr(dr, "discover_claude_scenes_dir")
    assert hasattr(dr, "POLISH_CJK_LOW") and hasattr(dr, "POLISH_CJK_HIGH")
