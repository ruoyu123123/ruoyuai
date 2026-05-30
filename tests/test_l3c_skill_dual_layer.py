"""L3c · skill 双层结构化 + 数值契约表注入器测试（北极星①⑤ · 2026-05-31）。

根因（ZeroStylus 实证）：skill 是长文描述，长文鲁棒性弱（纯句级方法对长文仅 43% 胜率），
段级结构是长文鲁棒性来源。skill_contract_table.py 给 skill 头部塞 (a) 作者数值契约表
+ (b) 句级层/段级层双层 scaffold。

钉死守护点：
  · extract_contract_table schema 容忍：蛊真人 (sentence_length/chapter_chars/
    dialogue_ratio_pct/paragraph_count) 与 惊悚乐园 (chapter_words/dialogue_ratio 分数/
    paragraph_length.single_sentence_para_ratio_mean) 两套 key 都吃。
  · 缺字段标 None（render 显「未蒸出」）· 绝不编造数值。
  · 复用 L1a paragraph_length_chars 分位数 [p5,p50,p95]（不重算）。
  · 对话占比统一归一为百分数（蛊真人 pct / 惊悚乐园 分数×100）。
  · 虚词 Top-N 按 p50 频率降序。
  · render 含「数值契约表」+「句级层」+「段级层」双层 + cliffhanger 落点 + POV 习惯。
  · 幂等注入：重跑只替换块内、不堆叠；front-matter 后插入；零 front-matter 顶端插。
  · 顾问非法官（北极星⑤）：契约表块出现「顾问」「不是硬门禁」，不出现 hard_gate 字样。
  · 真原文金标准：两书 作者风格_FINAL.json 喂入产出非空契约 + 数值落已知真实区间
    （memory reference-system-validation-method）。

只测确定性纯函数 / 文件注入（不实跑 gen-model · 不碰 LLM / agent）。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import skill_contract_table as sct  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 合成档（钉 schema 容忍·两套 key 形态）
# ════════════════════════════════════════════════════════════════

_GU_LIKE = {  # 蛊真人 schema
    "quantitative": {
        "sentence_length": {"mean": 19.065, "std": 3.055},
        "sentence_length_std": {"mean": 10.07},
        "chapter_chars": {"mean": 2718.888, "std": 510.841},
        "paragraph_count": {"mean": 90.182},
        "dialogue_ratio_pct": {"mean": 25.409},
        "paragraph_length_chars": {"p5": 24.5175, "p50": 30.5138, "p95": 38.3219, "mean": 32.1},
        "punctuation_per_1k": {
            "comma_period_ratio": {"p50": 1.7, "mean": 1.72},
            "ellipsis": {"p50": 1.12, "mean": 1.2},
            "exclamation": {"p50": 4.84, "mean": 5.0},
            "question": {"p50": 3.07, "mean": 3.1},
            "dash": {"p50": 0.0, "mean": 0.0},
        },
        "function_words_per_1k": {
            "的": {"p50": 34.4, "mean": 34.5},
            "了": {"p50": 13.7, "mean": 13.9},
            "着": {"p50": 4.6, "mean": 4.7},
            "便": {"p50": 0.7, "mean": 0.8},
        },
    },
    "cross_chapter_diversity": {
        "ending_type_distribution": {
            "对话悬念": 0.279, "信息炸弹": 0.251, "动作留白": 0.092,
            "场景硬收": 0.053, "回环呼应": 0.039,
        },
        "transition_methods_distribution": {
            "时间跳跃独段": 0.19, "直接承接": 0.42, "悬念承接新视角": 0.13,
        },
    },
}

_JINGSONG_LIKE = {  # 惊悚乐园 schema（别名 key + 对话分数）
    "quantitative": {
        "sentence_length": {"mean": 34.181, "std": 6.8406, "intra_chapter_std_mean": 23.3418},
        "chapter_words": {"mean": 2921.428, "std": 4179.8564},
        "dialogue_ratio": {"mean": 0.2701, "std": 0.2004},
        "paragraph_length": {"single_sentence_para_ratio_mean": 0.5106,
                             "mean_chars": 51.99},
        "paragraph_length_chars": {"p5": 36.8958, "p50": 51.5278, "p95": 71.9415, "mean": 52.0},
        "punctuation_per_1k": {
            "comma_period_ratio": {"p50": 2.58},
            "ellipsis": {"p50": 7.45},
        },
        "function_words_per_1k": {
            "的": {"p50": 38.6}, "了": {"p50": 19.0}, "着": {"p50": 5.6},
        },
    },
    "cross_chapter_diversity": {
        "ending_type_distribution": {"吐槽收尾": 0.4, "信息揭示": 0.3},
        "transition_method_distribution": {"省略号独段": 0.5, "时间锚点": 0.3},  # 单数 key 别名
    },
}


# ════════════════════════════════════════════════════════════════
# [A] extract_contract_table schema 容忍
# ════════════════════════════════════════════════════════════════

def test_A_extract_gu_schema():
    """蛊真人 schema：句长/方差/段长分位/对话(pct)/章字数/虚词 全部抽出。"""
    c = sct.extract_contract_table(_GU_LIKE)
    assert c["sentence_mean"] == 19.065
    assert c["sentence_std"] == 10.07  # 来自 sentence_length_std.mean
    assert c["para_quantiles"] == {"p5": 24.5, "p50": 30.5, "p95": 38.3}
    assert c["dialogue_pct"] == 25.41  # 已是 pct
    assert c["chapter_mean"] == 2718.888
    assert c["single_sentence_ratio"] is None  # 蛊真人未蒸出·不编造
    fw = dict(c["function_words_top"])
    assert fw["的"] == 34.4 and fw["了"] == 13.7


def test_A_extract_jingsong_schema():
    """惊悚乐园 schema：别名 key（chapter_words/dialogue_ratio 分数/intra_chapter_std_mean）都吃。"""
    c = sct.extract_contract_table(_JINGSONG_LIKE)
    assert c["sentence_mean"] == 34.181
    assert c["sentence_std"] == 23.3418  # 来自 intra_chapter_std_mean
    assert c["chapter_mean"] == 2921.428  # 来自 chapter_words
    assert abs(c["single_sentence_ratio"] - 0.5106) < 1e-9  # 惊悚乐园蒸出
    assert c["dialogue_pct"] == 27.01  # 0.2701 分数 → ×100 归一


def test_A_dialogue_pct_normalization():
    """对话占比统一归一为百分数：pct 原样·分数×100。"""
    assert sct.extract_contract_table(
        {"quantitative": {"dialogue_ratio_pct": {"mean": 30.0}}})["dialogue_pct"] == 30.0
    assert sct.extract_contract_table(
        {"quantitative": {"dialogue_ratio": {"mean": 0.42}}})["dialogue_pct"] == 42.0


def test_A_function_top_sorted_and_capped():
    """虚词 Top-N 按 p50 频率降序 + 钳到 ≤ DEFAULT_FUNCTION_TOP_N。"""
    fw = {f"w{i}": {"p50": float(i)} for i in range(20)}
    c = sct.extract_contract_table({"quantitative": {"function_words_per_1k": fw}})
    top = c["function_words_top"]
    assert len(top) == sct.DEFAULT_FUNCTION_TOP_N
    vals = [v for _, v in top]
    assert vals == sorted(vals, reverse=True)  # 降序
    assert top[0][1] == 19.0  # 最高频在首


def test_A_missing_fields_none_not_fabricated():
    """空档：所有字段 None / 空·绝不编造（缺字段诚实标记）。"""
    c = sct.extract_contract_table({"quantitative": {}})
    assert c["sentence_mean"] is None and c["sentence_std"] is None
    assert c["para_quantiles"] is None
    assert c["dialogue_pct"] is None
    assert c["chapter_mean"] is None
    assert c["single_sentence_ratio"] is None
    assert c["function_words_top"] == []
    assert c["punctuation"] == {}


def test_A_old_distill_no_quantiles_para_none():
    """老蒸馏档 paragraph_length_chars 只有 mean（无 p5/p95）→ para_quantiles None（向后兼容）。"""
    c = sct.extract_contract_table(
        {"quantitative": {"paragraph_length_chars": {"mean": 30.0}}})
    assert c["para_quantiles"] is None


def test_A_flat_quantitative_accepted():
    """容忍 quantitative 直接当顶层传入（无外层包裹）。"""
    c = sct.extract_contract_table(_GU_LIKE["quantitative"])
    assert c["sentence_mean"] == 19.065


# ════════════════════════════════════════════════════════════════
# [B] render：数值契约表 + 双层结构（句级/段级）
# ════════════════════════════════════════════════════════════════

def test_B_contract_table_has_all_contract_items():
    """契约表 render 含 6 类数值契约项：句长均值/方差/段长分位/单句独行/对话/虚词指纹。"""
    md = sct.render_contract_table_md(sct.extract_contract_table(_GU_LIKE))
    assert "数值契约表" in md
    assert "句长均值" in md and "句长方差" in md
    assert "段长分位数 [p5,p50,p95]" in md
    assert "单句独行占比" in md
    assert "对话占比" in md
    assert "虚词指纹" in md
    assert "标点分布" in md
    # 段长分位数实际渲染进表
    assert "[24.5, 30.5, 38.3]" in md


def test_B_missing_field_renders_unshipped():
    """缺字段 render 显「未蒸出」（蛊真人单句独行）· 不显伪造数字。"""
    md = sct.render_contract_table_md(sct.extract_contract_table(_GU_LIKE))
    # 单句独行那行应是「未蒸出」
    line = [ln for ln in md.split("\n") if "单句独行占比" in ln][0]
    assert "未蒸出" in line


def test_B_dual_layer_scaffold_both_layers():
    """双层 scaffold 显式含「句级层」+「段级层」两层标题。"""
    md = sct.render_dual_layer_scaffold(_GU_LIKE)
    assert "双层风格结构" in md
    assert "句级层" in md
    assert "段级层" in md
    # 句级层三要素
    assert "句式" in md and "口癖" in md and "禁用词" in md
    # 段级层三要素
    assert "段落组织" in md and "cliffhanger 落点" in md and "POV 切换" in md


def test_B_segment_layer_uses_cliffhanger_distribution():
    """段级层 cliffhanger 落点用 ending_type_distribution Top-N（蛊真人对话悬念 27.9% 居首）。"""
    md = sct.render_dual_layer_scaffold(_GU_LIKE)
    assert "对话悬念" in md and "27.9%" in md
    assert "严禁连续 ≥2 章同款" in md


def test_B_segment_layer_pov_transition_alias_key():
    """段级层 POV/转场吃 transition_method_distribution 单数别名 key（惊悚乐园）。"""
    md = sct.render_dual_layer_scaffold(_JINGSONG_LIKE)
    assert "省略号独段" in md and "POV" in md


def test_B_segment_layer_no_distribution_generic_fallback():
    """无分布字段 → 段级层给通用占位提示（不编造分布·POV 不切默认）。"""
    md = sct.render_dual_layer_scaffold({})
    assert "段级层" in md
    assert "POV 不切是默认" in md
    assert "钩子" in md  # 通用 cliffhanger 提示


# ════════════════════════════════════════════════════════════════
# [C] 顾问非法官（北极星⑤）
# ════════════════════════════════════════════════════════════════

def test_C_contract_is_advisory_not_hard_gate():
    """契约表块自我声明「顾问」「不是硬门禁」（北极星⑤ · 不干涉模型判断）。

    注：header 里出现的 'advisory/hard_gate' 字样是说明文（解释裁决权仍归审核层各 scanner，
    契约表本身不充当门禁），不是契约表给自己加门禁——故不禁该词整体出现，只钉死自我声明。
    """
    header = sct.build_skill_header(_GU_LIKE)
    assert "顾问" in header
    assert "不是硬门禁" in header
    # 契约表不得把自己声明为门禁（如「契约表是 hard_gate」「契约表为硬门禁」）
    assert "契约表是硬门禁" not in header
    assert "契约表是 hard_gate" not in header


# ════════════════════════════════════════════════════════════════
# [D] 幂等注入
# ════════════════════════════════════════════════════════════════

_FRONT_MATTER_SKILL = """---
name: test-style
version: v1
---

# 写作风格 Skill

## 身份
你是测试作者的复刻者。
"""

_NO_FRONT_MATTER_SKILL = "# 裸 skill\n\n## 章型\n内容。\n"


def test_D_inject_after_front_matter():
    """有 YAML front-matter → 块插入 --- 结束后、正文前。"""
    header = sct.build_skill_header(_GU_LIKE)
    out = sct.inject_header_into_skill(_FRONT_MATTER_SKILL, header)
    assert sct.has_contract_block(out)
    # front-matter 仍在最前
    assert out.startswith("---\nname: test-style")
    # 契约块在正文「# 写作风格 Skill」之前
    assert out.index(sct.SENTINEL_BEGIN) < out.index("# 写作风格 Skill")


def test_D_inject_no_front_matter_top():
    """无 front-matter → 块插到文件最顶端。"""
    header = sct.build_skill_header(_GU_LIKE)
    out = sct.inject_header_into_skill(_NO_FRONT_MATTER_SKILL, header)
    assert out.startswith(sct.SENTINEL_BEGIN)
    assert "# 裸 skill" in out


def test_D_idempotent_reinject_no_stacking():
    """重跑注入只替换块内·不堆叠（BEGIN 哨兵恒为 1 个）。"""
    header = sct.build_skill_header(_GU_LIKE)
    once = sct.inject_header_into_skill(_FRONT_MATTER_SKILL, header)
    twice = sct.inject_header_into_skill(once, header)
    assert once == twice  # 完全幂等
    assert once.count(sct.SENTINEL_BEGIN) == 1
    assert once.count(sct.SENTINEL_END) == 1


def test_D_reinject_updates_block_content():
    """档数据变化 → 重注入替换块内为新数值（不残留旧块）。"""
    h_gu = sct.build_skill_header(_GU_LIKE)
    h_js = sct.build_skill_header(_JINGSONG_LIKE)
    out1 = sct.inject_header_into_skill(_FRONT_MATTER_SKILL, h_gu)
    out2 = sct.inject_header_into_skill(out1, h_js)
    assert out2.count(sct.SENTINEL_BEGIN) == 1
    # 新块用惊悚乐园数值（句长 34.181），旧蛊真人值（19.065）不残留
    assert "34.181" in out2
    assert "19.065" not in out2


def test_D_body_outside_block_preserved():
    """注入不破坏哨兵块外的正文（# 身份 段保留）。"""
    header = sct.build_skill_header(_GU_LIKE)
    out = sct.inject_header_into_skill(_FRONT_MATTER_SKILL, header)
    assert "## 身份" in out
    assert "你是测试作者的复刻者。" in out


# ════════════════════════════════════════════════════════════════
# [E] 真原文金标准（两书 作者风格_FINAL.json）
#     memory reference-system-validation-method
# ════════════════════════════════════════════════════════════════

def _load_style(name):
    p = _ROOT / "workspace" / "styles" / name / "作者风格_FINAL.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def test_E_gu_real_json_contract_non_empty():
    """蛊真人真档：契约表非空 + 段长分位落已知真实区间（p50≈30·p95≈38）+ 对话归一 pct。"""
    sd = _load_style("蛊真人")
    if sd is None:
        return  # CI 无样本环境跳过
    c = sct.extract_contract_table(sd)
    assert c["sentence_mean"] is not None
    assert c["function_words_top"], "虚词 Top 不应为空"
    pq = c["para_quantiles"]
    assert pq is not None
    assert 28 <= pq["p50"] <= 33 and 36 <= pq["p95"] <= 41, pq
    # 对话占比应是 pct（~25），不是分数（~0.25）
    assert 20 <= c["dialogue_pct"] <= 30, c["dialogue_pct"]


def test_E_jingsong_real_json_single_sentence_ratio():
    """惊悚乐园真档：单句独行占比蒸出（~0.51）+ 段长 p95≈72（长段作者）。"""
    sd = _load_style("惊悚乐园")
    if sd is None:
        return
    c = sct.extract_contract_table(sd)
    assert c["single_sentence_ratio"] is not None
    assert 0.45 <= c["single_sentence_ratio"] <= 0.60, c["single_sentence_ratio"]
    pq = c["para_quantiles"]
    assert pq is not None and 68 <= pq["p95"] <= 76, pq


def test_E_real_skill_lacks_block_then_injectable():
    """现有 skill_v7（蛊真人）原本无 L3c 块·注入后含块且幂等（真文件场景·不写盘）。"""
    skill = _ROOT / "workspace" / "styles" / "蛊真人" / "skill_v7.md"
    sd = _load_style("蛊真人")
    if sd is None or not skill.exists():
        return
    original = skill.read_text(encoding="utf-8")
    assert not sct.has_contract_block(original), "现有 skill 不应已含 L3c 块"
    header = sct.build_skill_header(sd)
    out = sct.inject_header_into_skill(original, header)
    assert sct.has_contract_block(out)
    # 现有 front-matter 保留在最前
    assert out.startswith("---\nname: 蛊真人-style-v7")
    # 幂等
    assert sct.inject_header_into_skill(out, header) == out


def test_E_both_books_render_full_header():
    """两书真档都能渲完整 header（契约表 + 双层）· 不抛错（schema 容忍端到端验证）。"""
    for name in ("蛊真人", "惊悚乐园"):
        sd = _load_style(name)
        if sd is None:
            continue
        header = sct.build_skill_header(sd)
        assert "数值契约表" in header
        assert "句级层" in header and "段级层" in header
        assert sct.SENTINEL_BEGIN in header and sct.SENTINEL_END in header
