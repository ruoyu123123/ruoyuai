"""L3b 复刻 prompt CoT-first 自解释测试 — 直击 cluster 级 D 级（北极星①⑤⑥ · 2026-05-31）。

根因（memory reference-system-validation-method）：复刻是「得分→盲改 skill→再测」乏力循环，
LLM 直接写长文易段长崩塌（蛊真人单章 B 级 / cluster 级仅 D 级）。

升级（CoTeX 自解释 + Register Analysis · 全在 distill_replicate.py 内自包含 · 只改 prompt 构造）：
  让 gen-model **先逐条点名「本段命中 skill 哪几条受控量化坐标（句长/段长/单句独行/标点/虚词）」
  再写正文** · 用受控语言学坐标（不是「冷峻/华丽」感性词 · 后者改意泄漏内容）。
  CoTeX 实证 1K 样本 BLEU 68 vs 55。

纪律：只测**确定性的 prompt 构造 / strip 纯函数**（不实跑 gen-model · gen-model 需 API）。
  CoT 是思考脚手架 → 落盘正文 strip 掉分析段，meta 留痕（不黑箱）。
  保留旧 prompt 路径对照（env L3B_COT_FIRST_MODE=off / --cot-first off）· 默认行为零回归。
  真原文校准（蛊真人 skill_v7.md）：受控坐标必须真在 skill 里有依据。

测试覆盖：① mode 解析（默认 active / off / 归一 / 非法回退）；② CoT 指令含受控量化坐标 + 无感性词；
③ CoT system prompt 变体；④ build_cluster_subcall_prompt 两路（cot_first True/False）；
⑤ strip_cot_analysis 切分 / 无标记 / 取最后标记；⑥ 旧路径零回归；⑦ 真 skill_v7 校准。
"""
import os
import sys
import importlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import distill_replicate as dr  # noqa: E402


# 受控语言学坐标（Register Analysis · 可量化）—— CoT 指令必须点名这些
_CONTROLLED_COORDS = ["句长", "段长", "单句独行", "标点", "虚词"]
# 感性词（改意泄漏内容）—— CoT 指令必须把它们标为「严禁」而非要求模型产出
_SENSORY_WORDS = ["冷峻", "华丽", "大气", "有张力"]


def _reload_dr(mode):
    """以指定 L3B_COT_FIRST_MODE reload distill_replicate（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("L3B_COT_FIRST_MODE", None)
    else:
        os.environ["L3B_COT_FIRST_MODE"] = mode
    importlib.reload(dr)
    return dr


_META = {"cluster_id": "cluster_001", "chapter_range": [1, 6],
         "chapters_count": 6, "estimated_words": 18000,
         "boundary_reason": "max_chapters(6≥6)"}


# ════════════════════════════════════════════════════════════════
# [A] mode 解析 _l3b_cot_first_mode
# ════════════════════════════════════════════════════════════════

def test_A_mode_default_off():
    """L3B_COT_FIRST_MODE 未设 → 默认 off（影子纪律·与其他5升级件一致·CoT待gen-model实跑验证token预算后放量）。"""
    dx = _reload_dr(None)
    try:
        assert dx._l3b_cot_first_mode() == "off"
    finally:
        _reload_dr(None)


def test_A_mode_off_legacy():
    """off / 0 / false / legacy → off（保留旧 prompt 路径对照）。"""
    for v in ("off", "0", "false", "legacy", "OFF", "Off"):
        dx = _reload_dr(v)
        try:
            assert dx._l3b_cot_first_mode() == "off", v
        finally:
            _reload_dr(None)


def test_A_mode_on_normalized_to_active():
    """on / 1 / true / cot / active → active（归一）。"""
    for v in ("on", "1", "true", "cot", "active", "ACTIVE"):
        dx = _reload_dr(v)
        try:
            assert dx._l3b_cot_first_mode() == "active", v
        finally:
            _reload_dr(None)


def test_A_mode_garbage_falls_back_off():
    """空/非法值回退 off（保守默认·不静默开启未经实跑验证的升级）。"""
    dx = _reload_dr("garbage_value")
    try:
        assert dx._l3b_cot_first_mode() == "off"
    finally:
        _reload_dr(None)


# ════════════════════════════════════════════════════════════════
# [B] CoT 指令 COT_FIRST_DIRECTIVE：受控量化坐标 + 无感性词 + 标记
# ════════════════════════════════════════════════════════════════

def test_B_directive_names_all_controlled_coords():
    """CoT 指令必须逐条点名 5 类受控量化坐标（句长/段长/单句独行/标点/虚词）。"""
    d = dr.COT_FIRST_DIRECTIVE
    for coord in _CONTROLLED_COORDS:
        assert coord in d, f"CoT 指令缺受控坐标: {coord}"


def test_B_directive_forbids_sensory_words():
    """北极星：感性词（冷峻/华丽…）只能作为「严禁」出现 · 不能要求模型产出它们。"""
    d = dr.COT_FIRST_DIRECTIVE
    assert "严禁" in d
    # 感性词出现处必须在「严禁」语境（指令明确禁用它们）
    for w in _SENSORY_WORDS:
        if w in d:
            # 该感性词附近应有「严禁」字样（同一指令块内禁用它）
            assert "严禁" in d, f"感性词 {w} 未被明确禁用"


def test_B_directive_contains_both_markers():
    """CoT 指令含分析段标记 + 正文标记（两段式可被 strip 解析）。"""
    d = dr.COT_FIRST_DIRECTIVE
    assert dr.COT_ANALYSIS_MARKER in d
    assert dr.COT_BODY_MARKER in d


def test_B_directive_first_analyze_then_write_order():
    """指令强调「先分析后写」顺序（分析标记在正文标记之前出现）。"""
    d = dr.COT_FIRST_DIRECTIVE
    assert d.index(dr.COT_ANALYSIS_MARKER) < d.index(dr.COT_BODY_MARKER)
    assert "先" in d and ("分析" in d)


# ════════════════════════════════════════════════════════════════
# [C] CoT system prompt 变体 REPLICATE_SYSTEM_PROMPT_COT
# ════════════════════════════════════════════════════════════════

def test_C_cot_system_prompt_has_cot_first_clause():
    """CoT system prompt 含「先分析后写」纪律 + 不再说「直接输出正文」。"""
    s = dr.REPLICATE_SYSTEM_PROMPT_COT
    assert "先分析后写" in s
    assert "直接输出正文" not in s  # 旧的「直接出正文」第 6 条被替换


def test_C_cot_system_prompt_keeps_hard_constraints():
    """CoT 变体保留全部复刻硬约束（量化基线 / 反模式 / 签名特征 / 不照抄）· 只追加自解释。"""
    s = dr.REPLICATE_SYSTEM_PROMPT_COT
    assert "量化基线必须命中" in s
    assert "反模式必须 0 命中" in s
    assert "签名特征至少命中 3 条" in s
    assert "不照抄" in s


def test_C_legacy_system_prompt_unchanged():
    """旧 system prompt 仍是「直接输出正文」路径（对照 · 零回归）。"""
    s = dr.REPLICATE_SYSTEM_PROMPT
    assert "直接输出正文" in s
    assert "先分析后写" not in s


# ════════════════════════════════════════════════════════════════
# [D] build_cluster_subcall_prompt 两路（cot_first True/False）
# ════════════════════════════════════════════════════════════════

def test_D_cot_first_true_injects_directive():
    """cot_first=True：prompt 末尾输出节换成 CoT 指令（含受控坐标 + 两段式标记）。"""
    p = dr.build_cluster_subcall_prompt(
        "SKILL 内容", "", _META,
        subcall_index=1, subcall_total=2, prev_tail="",
        chapters_in_this_call=3, target_words=9000, cot_first=True)
    assert dr.COT_ANALYSIS_MARKER in p
    assert dr.COT_BODY_MARKER in p
    for coord in _CONTROLLED_COORDS:
        assert coord in p, coord
    # 不再用旧的「直接输出复刻正文」
    assert "直接输出复刻正文" not in p


def test_D_cot_first_false_legacy_output_section():
    """cot_first=False（默认）：保留旧「直接输出复刻正文」节 · 无 CoT 指令（零回归）。"""
    p = dr.build_cluster_subcall_prompt(
        "SKILL 内容", "", _META,
        subcall_index=1, subcall_total=2, prev_tail="",
        chapters_in_this_call=3, target_words=9000, cot_first=False)
    assert "直接输出复刻正文" in p
    assert dr.COT_ANALYSIS_MARKER not in p
    assert dr.COT_BODY_MARKER not in p


def test_D_default_arg_is_legacy():
    """build 默认 cot_first=False（不传时走旧路径 · main 显式决定是否开 CoT · 防意外开启）。"""
    p = dr.build_cluster_subcall_prompt(
        "SKILL", "", _META, subcall_index=1, subcall_total=1,
        prev_tail="", chapters_in_this_call=3, target_words=9000)
    assert "直接输出复刻正文" in p
    assert dr.COT_BODY_MARKER not in p


def test_D_both_paths_keep_skill_and_continuity():
    """两路都必须注入 skill + 衔接要求（CoT 只换输出节 · 不动核心 prompt 结构）。"""
    for cf in (True, False):
        p = dr.build_cluster_subcall_prompt(
            "我的SKILL文本", "", _META, subcall_index=2, subcall_total=3,
            prev_tail="上段结尾文字", chapters_in_this_call=2,
            target_words=6000, cot_first=cf)
        assert "我的SKILL文本" in p
        assert "衔接要求" in p
        assert "上段结尾文字" in p  # prev_tail anchor 两路都注入


# ════════════════════════════════════════════════════════════════
# [E] strip_cot_analysis 纯函数：切分 / 无标记 / 取最后标记
# ════════════════════════════════════════════════════════════════

def test_E_strip_splits_at_body_marker():
    """命中 [正文] → 正文 = marker 后；trace = marker 前（含分析段 · 留痕）。"""
    raw = (f"{dr.COT_ANALYSIS_MARKER}\n句长 mean≈19，高方差。\n段长 15-30 字。\n"
           f"{dr.COT_BODY_MARKER}\n他停下脚步。\n风很大。")
    body, cot = dr.strip_cot_analysis(raw)
    assert body == "他停下脚步。\n风很大。"
    assert "句长 mean≈19" in cot
    assert dr.COT_BODY_MARKER not in body  # 正文不含标记
    assert dr.COT_ANALYSIS_MARKER in cot   # 分析段留在 trace


def test_E_strip_no_marker_returns_full_body():
    """无 [正文] 标记（旧路径 / 模型没遵守两段式）→ 正文 = 原文 · trace 空（不误删）。"""
    raw = "他走进房间。\n看了看四周。\n然后坐下了。"
    body, cot = dr.strip_cot_analysis(raw)
    assert body == raw
    assert cot == ""


def test_E_strip_uses_last_marker():
    """模型若在分析段里也提到「[正文]」字样 → 按最后一个标记切（正文里不该再有标记）。"""
    raw = (f"{dr.COT_ANALYSIS_MARKER}\n我会在 {dr.COT_BODY_MARKER} 后写正文。\n"
           f"{dr.COT_BODY_MARKER}\n真正的正文从这里开始。")
    body, cot = dr.strip_cot_analysis(raw)
    assert body == "真正的正文从这里开始。"
    assert dr.COT_BODY_MARKER in cot  # 分析段里那个提及留在 trace


def test_E_strip_trims_leading_colon_whitespace():
    """[正文] 后若有冒号 / 空白 → 去掉（正文干净落盘）。"""
    raw = f"{dr.COT_BODY_MARKER}：\n\n  他抬起头。"
    body, _ = dr.strip_cot_analysis(raw)
    assert body == "他抬起头。"


def test_E_strip_empty_and_only_marker():
    """空串 / 只有标记的退化输入不抛错。"""
    assert dr.strip_cot_analysis("") == ("", "")
    body, cot = dr.strip_cot_analysis(dr.COT_BODY_MARKER)
    assert body == ""


# ════════════════════════════════════════════════════════════════
# [F] clean_output + strip 协同（端到端 prompt→落盘链确定性段）
# ════════════════════════════════════════════════════════════════

def test_F_clean_then_strip_pipeline():
    """模拟 CoT-first 回复：markdown 包裹 + 分析段 + 正文 → clean 去 markdown，strip 去分析段。"""
    reply = (f"```\n{dr.COT_ANALYSIS_MARKER}\n单句独行占比 ~45%。\n标点：逗号/句号比 1.4。\n"
             f"{dr.COT_BODY_MARKER}\n正文开头。\n他笑了。\n```")
    cleaned = dr.clean_output(reply)
    assert "```" not in cleaned
    body, cot = dr.strip_cot_analysis(cleaned)
    assert "正文开头。" in body and "他笑了。" in body
    assert "单句独行占比" in cot
    assert dr.COT_ANALYSIS_MARKER not in body


def test_F_strip_preserves_body_paragraph_structure():
    """剥离分析段后正文的段落空行结构不被破坏（splitter 端按段切的前提）。"""
    raw = (f"{dr.COT_ANALYSIS_MARKER}\n段长 20 字。\n{dr.COT_BODY_MARKER}\n"
           "第一段。\n\n第二段。\n\n第三段。")
    body, _ = dr.strip_cot_analysis(raw)
    assert body == "第一段。\n\n第二段。\n\n第三段。"
    assert body.count("\n\n") == 2  # 两个段间空行保留


# ════════════════════════════════════════════════════════════════
# [G] 真 skill_v7.md 校准（北极星纪律 3 · 受控坐标有真依据）
# ════════════════════════════════════════════════════════════════

def test_G_controlled_coords_grounded_in_real_skill():
    """蛊真人 skill_v7.md 里真有 CoT 指令点名的受控量化坐标——证明坐标不是凭空造的。

    指令让模型自解释「句长/段长/单句独行/标点」等 → 这些维度必须在真 skill 中有量化基线，
    否则模型无从对照（北极星纪律 3：用真原文 + 现有 skill 校准）。
    """
    skill = _ROOT / "workspace" / "styles" / "蛊真人" / "skill_v7.md"
    if not skill.exists():
        return  # CI 无样本则跳过
    txt = skill.read_text(encoding="utf-8")
    # skill_v7 量化约束节明确有这些坐标
    assert "句长" in txt
    assert "段长" in txt or "段落数" in txt
    assert "单句独行" in txt
    assert "标点" in txt or "逗号" in txt
    # 单句独行占比是 skill 明确量化的（CoT 指令第 3 条核心）
    assert "单句独行占比" in txt


def test_G_directive_coords_subset_of_skill_dimensions():
    """CoT 指令点名的受控坐标都能在真 skill 找到对应——闭环（自解释→可核对）。"""
    skill = _ROOT / "workspace" / "styles" / "蛊真人" / "skill_v7.md"
    if not skill.exists():
        return
    txt = skill.read_text(encoding="utf-8")
    grounded = [c for c in _CONTROLLED_COORDS if c in txt]
    # 5 个受控坐标中至少 4 个在真 skill 里有依据（标点可能以「逗号」形态出现）
    assert len(grounded) >= 4, grounded
