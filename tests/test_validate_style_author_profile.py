"""validate_style 作者档「第一权威·更贴作者非更苛」回归测试（北极星⑤ 顾问非法官）。

锁三条作者档 override 规则（作者档让审核更贴作者，绝不更苛）：

  [B] 段长 band 只用**真实段长数据**（paragraph_length_chars 或 章字数÷段数），
      都缺 → 段长维度不 override（保通用 band，不收窄）。绝不用句长推段长
      （句长 19 ×0.7-1.3 推出的 band 13-25 会把段落本就长(34)的作者从 PASS 顶成 FAIL）。

  [A] 完整对话段(整段包在弯/方头引号内·不可中切)超长降 WARN(advisory 可豁免)，
      非对话超长段仍 hard_gate(FAIL)——STYLE_单段超长不按纯 CJK 字数误伤对话段。

  [C] 有作者档时工艺签名类配额词(顿时/微微)超额降 WARN（与 _chk_banned 的作者档
      降级逻辑一致），非签名类(突然/莫名)超额仍 FAIL。

关键纪律：让作者档真正第一权威·更贴作者，但**不放松对真问题的检测**——
AI 结构套话仍 FAIL · 真超长非对话段仍 hard_gate · 非签名类配额词超额仍 FAIL。

只测确定性纯函数（程序化校验），不碰 LLM / agent。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import validate_style as vs  # noqa: E402
from style_analyzer import (  # noqa: E402
    CRAFT_SIGNATURE_QUOTA_WORDS,
    AI_STRUCTURAL_BANNED,
    CRAFT_SIGNATURE_BANNED,
)


def _res(results, name_contains):
    for r in results:
        if name_contains in r.name:
            return r
    raise AssertionError(f"未找到检查项 {name_contains}：{[r.name for r in results]}")


# ════════════════════════════════════════════════════════════════
# [B] 段长单位错配：作者档不该用句长推段长
# ════════════════════════════════════════════════════════════════

def test_B_para_band_from_real_para_length_not_sentence():
    """有 paragraph_length_chars 真实段长 → 用它推 band（不碰 sentence_length）。"""
    sd = {"quantitative": {
        "sentence_length": {"mean": 19.0},          # 句长 19（绝不能用来推段长）
        "paragraph_length_chars": {"mean": 34.0},   # 真实段均长 34
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["para_mean_len"]["min"], t["para_mean_len"]["max"]
    # band 应围绕真实段长 34（34×0.7=23.8 .. 34×1.3=44.2），而非围绕句长 19（13.3-24.7）
    assert abs(lo - 34 * 0.7) < 0.01 and abs(hi - 34 * 1.3) < 0.01, (lo, hi)
    # 段均长 34 必须落在 band 内（不被顶成 FAIL）
    assert lo <= 34 <= hi, (lo, hi)


def test_B_para_band_falls_back_to_chapter_chars_div_paragraphs():
    """paragraph_length_chars=null → 用 章字数.mean / 段数.mean 算真实段均长。"""
    sd = {"quantitative": {
        "sentence_length": {"mean": 19.065},
        "paragraph_length_chars": None,                 # null（蛊真人真实状态）
        "chapter_chars": {"mean": 2718.888},
        "paragraph_count": {"mean": 90.182},
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    expect = 2718.888 / 90.182  # ≈ 30.15
    lo, hi = t["para_mean_len"]["min"], t["para_mean_len"]["max"]
    assert abs(lo - expect * 0.7) < 0.1 and abs(hi - expect * 1.3) < 0.1, (lo, hi, expect)
    # 蛊真人原文实测段均 33.9 必须落在 band 内（句长推段长会给出 13.35-24.78 把 33.9 顶成 FAIL）
    assert lo <= 33.9 <= hi, (lo, hi)


def test_B_no_para_data_keeps_generic_band_not_narrowed():
    """段长数据全缺（仅有句长）→ 段长维度【不 override】，保持通用 band（不用句长收窄）。"""
    sd = {"quantitative": {"sentence_length": {"mean": 19.0}}}  # 只有句长
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    generic = dict(t["para_mean_len"])  # 通用 band 10-40
    t = vs._apply_style_overrides(t, sd)
    # band 不变（绝不收窄到句长 ±30%）
    assert t["para_mean_len"] == generic, (t["para_mean_len"], generic)
    # 段均 33.9 仍 PASS（通用 band 内）
    assert t["para_mean_len"]["min"] <= 33.9 <= t["para_mean_len"]["max"]


def test_B_real_gu_zhenren_chapter043_para_not_fail():
    """蛊真人原文第043章带作者档校验：段长不 FAIL（核心实证）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    ch = proj / "原文" / "第043章.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return  # 样本缺失则跳过（CI 无样本环境）
    import json
    raw = ch.read_text(encoding="utf-8")
    sd = json.loads(sj.read_text(encoding="utf-8"))
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    results = vs.validate_style(raw, t)
    para = _res(results, "段落均长")
    assert para.status == "PASS", (para.status, para.detail, para.target_desc)


def test_B_real_chapter043_ai_slop_still_fails_with_profile():
    """同一原文注入 AI 结构套话 → 禁用词仍 FAIL（修段长不放松对真问题的检测）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    ch = proj / "原文" / "第043章.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return
    import json
    raw = ch.read_text(encoding="utf-8")
    sd = json.loads(sj.read_text(encoding="utf-8"))
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    injected = "与此同时，他走了出去。\n\n" * 3 + raw
    results = vs.validate_style(injected, t)
    banned = _res(results, "禁用词")
    assert banned.status == "FAIL", (banned.status, banned.detail)
    assert "AI结构套话" in banned.name, banned.name


# ════════════════════════════════════════════════════════════════
# [A] 单段超长 hard_gate 对话段豁免 / 非对话段仍 hard_gate
# ════════════════════════════════════════════════════════════════

_DQ_OPEN = "“"   # “
_DQ_CLOSE = "”"  # ”
_CJK_FILL = "你"      # 任意 CJK 填充字


def test_A_full_dialogue_over_hard_exempted_to_warn():
    """完整对话段（整段被弯引号成对包裹）超 hard_gate → 降 WARN（豁免，不 FAIL）。"""
    para = _DQ_OPEN + _CJK_FILL * 127 + _DQ_CLOSE  # 127 CJK > 120
    text = "开篇一句话。\n\n" + para
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "WARN", (res.status, res.detail)
    assert "豁免" in res.detail or "豁免" in res.target_desc, (res.detail, res.target_desc)


def test_A_non_dialogue_over_hard_still_fails():
    """非对话超长段（叙述体 > 120 CJK，超例外）→ 仍 FAIL（绝不放过真超长）。"""
    p1 = "他" * 127
    p2 = "她" * 127
    text = p1 + "\n\n" + p2  # 2 段非对话超 hard_gate（超例外 1）
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)


def test_L_paratext_cannot_smuggle_over_300_runaway():
    """(ps:)后记 marker 后接 >300 纯叙述·绝对上限 300 墙仍拦——
    paratext 只豁免天花板例外·不豁免 300 绝对墙·防把 360 字纯叙述伪装成后记绕墙。"""
    paratext_mark = "（ps：占位后记说明）"
    runaway = "这是一段没有任何对话的纯叙述失控内容" * 20  # ~360 字 >300 纯叙述非对话
    text = "正文引子。\n\n" + paratext_mark + "\n\n" + runaway
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)
    assert "绝对上限" in res.detail, res.detail   # 被 300 墙拦(真失控)·非天花板例外漏放


def test_A_dialogue_exempt_but_nondialogue_still_fails_mixed():
    """对话超长段豁免，同时存在的非对话超长段仍触发 FAIL（既豁免对话又不放过真超长）。"""
    dialog = _DQ_OPEN + _CJK_FILL * 130 + _DQ_CLOSE   # 对话超长（豁免）
    nondialog_a = "他" * 127                          # 非对话超长 1
    nondialog_b = "她" * 127                          # 非对话超长 2（超例外 1）
    text = dialog + "\n\n" + nondialog_a + "\n\n" + nondialog_b
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)
    assert "豁免" in res.detail, res.detail  # 报告里点明对话段已豁免


def test_A_corner_quote_dialogue_also_exempted():
    """方头引号「…」完整对话段同样豁免（codepoint 判左右引号成对）。"""
    para = "「" + _CJK_FILL * 130 + "」"  # 「…」
    text = "引子。\n\n" + para
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "WARN", (res.status, res.detail)


def test_A_dialogue_detector_distinguishes_open_close_codepoints():
    """对话检测必须 codepoint 区分左/右引号——纯引号段（左开右闭）算完整对话段。"""
    full = _DQ_OPEN + "话" * 5 + _DQ_CLOSE
    assert vs._is_full_dialogue_para(full) is True
    # 段首开引号但段内未闭合（闭引落下一视觉段·跨段对话延续）→ 豁免（路径③）。
    assert vs._is_full_dialogue_para(_DQ_OPEN + "话" * 5) is True
    # 左右引号倒置（右引号开头·非开引号段首·引号不成对覆盖低）→ 不算
    assert vs._is_full_dialogue_para(_DQ_CLOSE + "话" * 5 + _DQ_OPEN) is False
    # 纯叙述（无引号）→ 不算
    assert vs._is_full_dialogue_para("他走了出去。") is False


# ════════════════════════════════════════════════════════════════
# [C] 配额词作者档降级
# ════════════════════════════════════════════════════════════════

def test_C_signature_quota_word_downgraded_with_profile():
    """有作者档 + 工艺签名类配额词(顿时/微微/似乎/仿佛)超额 → 降 WARN（可豁免），不 FAIL。"""
    # 前置不变量：顿时/微微/似乎/仿佛 是签名类配额词
    for w in ("顿时", "微微", "似乎", "仿佛"):
        assert w in CRAFT_SIGNATURE_QUOTA_WORDS, w
    text = "他顿时愣住了。" * 8  # 顿时 命中 8 > 配额 5
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t["_has_author_profile"] = True
    res = vs._chk_quota(text, vs.analyze_text(text), t)
    assert res.status == "WARN", (res.status, res.detail)
    assert "降级" in res.name or "可豁免" in res.detail, (res.name, res.detail)


def test_C_signature_hedge_quota_words_also_downgraded():
    """似乎/仿佛（软 hedge 签名词）有作者档时超额同样降 WARN。"""
    text = "他似乎想起了什么。" * 8  # 似乎 命中 8 > 5
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t["_has_author_profile"] = True
    res = vs._chk_quota(text, vs.analyze_text(text), t)
    assert res.status == "WARN", (res.status, res.detail)


def test_C_signature_quota_word_fails_without_profile():
    """无作者档 + 同样签名类配额词超额 → 仍 FAIL。"""
    text = "他顿时愣住了。" * 8
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    # 无作者档：不设 _has_author_profile
    res = vs._chk_quota(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)


def test_C_non_signature_quota_word_still_fails_with_profile():
    """有作者档时，**非签名类**配额词(突然/莫名)超额 → 仍 FAIL（只降签名类，不放过其他）。"""
    assert "突然" not in CRAFT_SIGNATURE_QUOTA_WORDS  # 突然 是配额词但非工艺签名
    text = "他突然停下了脚步。" * 8  # 突然 命中 8 > 5
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t["_has_author_profile"] = True
    res = vs._chk_quota(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)


def test_C_mixed_quota_nonsignature_dominates_to_fail():
    """有作者档时签名类+非签名类都超额 → 因非签名类超额整体 FAIL（不被签名类降级掩盖）。"""
    text = ("他突然停下。" * 8) + ("她顿时愣住。" * 8)  # 突然(非签名)+顿时(签名) 各 8
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t["_has_author_profile"] = True
    res = vs._chk_quota(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)
    # 报告应同时标注签名类被降级
    assert "降级" in res.detail, res.detail


def test_C_under_quota_passes_regardless_of_profile():
    """配额内（≤5）→ PASS（两个分支都不误报）。"""
    text = "他顿时愣住。" * 3  # 顿时 命中 3 ≤ 5
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t["_has_author_profile"] = True
    res = vs._chk_quota(text, vs.analyze_text(text), t)
    assert res.status == "PASS", (res.status, res.detail)


# ════════════════════════════════════════════════════════════════
# [D] 作者档 key tolerant 兼容 + 单位对齐
#
# override tolerant 读多命名（dialogue_ratio 0-1 / dialogue_ratio_pct 百分比、
# chapter_words / chapter_chars），百分比键 /100 归一到 validate_style 内部 0-1 ratio
# 单位——防键名/单位不符导致 override 失效退回通用 band 苛求真作者
# （如蛊真人真实 ~25% 对话被通用 30-80% 顶成 FAIL）。
# 纪律：让作者档真正第一权威（北极星⑤），但**不放松对真问题的检测**。
# ════════════════════════════════════════════════════════════════

def test_D_dialogue_ratio_pct_key_read_and_unit_converted():
    """dialogue_ratio_pct（百分比 25.409）被正确读取并 /100 归一为 0-1 ratio band。
    防回归：只读 dialogue_ratio 键会漏读蛊真人档（其键为 dialogue_ratio_pct）→ override 失效。"""
    sd = {"quantitative": {"dialogue_ratio_pct": {"mean": 25.409}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["dialogue_ratio"]["min"], t["dialogue_ratio"]["max"]
    # 25.409% -> 0.25409 ± 0.15 = 0.10409 .. 0.40409（单位对齐到 0-1 ratio）
    assert abs(lo - (0.25409 - 0.15)) < 1e-6 and abs(hi - (0.25409 + 0.15)) < 1e-6, (lo, hi)
    # 蛊真人真实对话占比 ~0.205 必须落 band 内（退回通用 0.30-0.80 会把 0.205 顶成 FAIL）
    assert lo <= 0.205 <= hi, (lo, hi)
    # band 绝不退回通用 0.30-0.80（证明 override 真生效）
    assert not (abs(lo - 0.30) < 1e-6 and abs(hi - 0.80) < 1e-6), (lo, hi)


def test_D_dialogue_ratio_0to1_key_still_works():
    """dialogue_ratio（0-1 ratio · 惊悚乐园键）原样读取，不被错误 /100。"""
    sd = {"quantitative": {"dialogue_ratio": {"mean": 0.2701}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["dialogue_ratio"]["min"], t["dialogue_ratio"]["max"]
    assert abs(lo - (0.2701 - 0.15)) < 1e-6 and abs(hi - (0.2701 + 0.15)) < 1e-6, (lo, hi)
    assert lo <= 0.22 <= hi  # 惊悚乐园真实 ~0.22 落 band 内


def test_D_dialogue_ratio_prefers_0to1_key_over_pct():
    """同时存在 dialogue_ratio(0-1) 与 dialogue_ratio_pct → 优先 0-1 ratio 键（明确单位优先）。"""
    sd = {"quantitative": {
        "dialogue_ratio": {"mean": 0.30},
        "dialogue_ratio_pct": {"mean": 99.0},  # 若误用会得到 0.99±0.15 = 离谱 band
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["dialogue_ratio"]["min"], t["dialogue_ratio"]["max"]
    assert abs(lo - 0.15) < 1e-6 and abs(hi - 0.45) < 1e-6, (lo, hi)


def test_D_dialogue_ratio_misfiled_pct_in_ratio_key_normalized():
    """容错：百分比(25.4)被误写进 0-1 ratio 键(值>1) → 仍按百分比 /100 归一（防双重错配）。"""
    sd = {"quantitative": {"dialogue_ratio": {"mean": 25.4}}}  # 误把 pct 写进 ratio 键
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["dialogue_ratio"]["min"], t["dialogue_ratio"]["max"]
    # 归一为 0.254 ± 0.15（而非 25.4±0.15 这种被 min(1.0,..) 钳成 [0,1] 的废 band）
    assert abs(lo - (0.254 - 0.15)) < 1e-6 and abs(hi - (0.254 + 0.15)) < 1e-6, (lo, hi)


def test_D_dialogue_ratio_absent_keeps_generic_band():
    """两类对话占比键都缺 → 不 override，保持通用 band（不误收窄）。"""
    sd = {"quantitative": {"sentence_length": {"mean": 19.0}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    generic = dict(t["dialogue_ratio"])
    t = vs._apply_style_overrides(t, sd)
    assert t["dialogue_ratio"] == generic, (t["dialogue_ratio"], generic)


def test_D_chapter_chars_key_tolerant():
    """chapter_chars（蛊真人键）被 tolerant 读为章字数 band（只读 chapter_words 会漏读此键）。"""
    sd = {"quantitative": {"chapter_chars": {"mean": 2718.888}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["chapter_words"]["min"], t["chapter_words"]["max"]
    assert abs(lo - (2718.888 - 500)) < 1e-6 and abs(hi - (2718.888 + 500)) < 1e-6, (lo, hi)
    # band 绝不退回通用 1800-5800（证明 override 真生效·band 贴作者真实分布）
    assert not (lo == 1800 and hi == 5800), (lo, hi)


def test_D_chapter_words_key_still_works():
    """chapter_words（惊悚乐园键）原样读取。"""
    sd = {"quantitative": {"chapter_words": {"mean": 2921.428}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["chapter_words"]["min"], t["chapter_words"]["max"]
    assert abs(lo - (2921.428 - 500)) < 1e-6 and abs(hi - (2921.428 + 500)) < 1e-6, (lo, hi)


def test_D_chapter_words_prefers_chapter_words_over_chars():
    """同时存在 chapter_words 与 chapter_chars → 优先 chapter_words（首选明确命名）。"""
    sd = {"quantitative": {
        "chapter_words": {"mean": 3000.0},
        "chapter_chars": {"mean": 9999.0},
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert abs(t["chapter_words"]["min"] - 2500.0) < 1e-6, t["chapter_words"]


def test_D_para_mean_from_paragraph_length_mean_chars_key():
    """惊悚乐园键 paragraph_length.mean_chars=52 → 段长 band 用真实段长推（不退通用苛求）。
    防回归：只认 paragraph_length_chars 键会漏读此档（惊悚乐园缺 paragraph_count）
    → 退通用 14-35 把段均 52 的作者顶成 FAIL。"""
    sd = {"quantitative": {
        "sentence_length": {"mean": 34.181},          # 句长（绝不能用来推段长）
        "paragraph_length": {"mean_chars": 51.9997},   # 真实段均长 52（惊悚乐园键）
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["para_mean_len"]["min"], t["para_mean_len"]["max"]
    assert abs(lo - 51.9997 * 0.7) < 0.01 and abs(hi - 51.9997 * 1.3) < 0.01, (lo, hi)
    assert lo <= 51.9997 <= hi


def test_D_helper_dialogue_ratio_extractor_units():
    """直测 _extract_author_dialogue_ratio：归一到 0-1 ratio 的纯函数行为。"""
    assert abs(vs._extract_author_dialogue_ratio({"dialogue_ratio_pct": {"mean": 25.4}}) - 0.254) < 1e-9
    assert abs(vs._extract_author_dialogue_ratio({"dialogue_ratio": {"mean": 0.27}}) - 0.27) < 1e-9
    assert vs._extract_author_dialogue_ratio({}) is None
    assert vs._extract_author_dialogue_ratio({"sentence_length": {"mean": 19}}) is None


def test_D_helper_chapter_words_extractor():
    """直测 _extract_author_chapter_words：tolerant 多命名。"""
    assert abs(vs._extract_author_chapter_words({"chapter_chars": {"mean": 2718.888}}) - 2718.888) < 1e-9
    assert abs(vs._extract_author_chapter_words({"chapter_words": {"mean": 2921.428}}) - 2921.428) < 1e-9
    assert vs._extract_author_chapter_words({}) is None


# ── 两书真原文回归（带作者档 · 实证 override 生效 + 真问题不放松）──

def _load_body(proj_name, ch_name):
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / proj_name
    ch = proj / "原文" / f"{ch_name}.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return None, None
    raw = ch.read_text(encoding="utf-8")
    import json
    sd = json.loads(sj.read_text(encoding="utf-8"))
    return raw, sd


def test_D_real_gu_zhenren_dialogue_not_fail_with_profile():
    """蛊真人原文 ch043 带作者档：对话占比不 FAIL（核心实证 · pct 键漏读会退通用 30-80% 把真实 ~20% 顶成 FAIL）。"""
    raw, sd = _load_body("蛊真人", "第043章")
    if raw is None:
        return
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    results = vs.validate_style(raw, t)
    dlg = _res(results, "对话占比")
    assert dlg.status != "FAIL", (dlg.status, dlg.detail, dlg.target_desc)


def test_D_real_gu_zhenren_dialogue_would_fail_under_generic_band():
    """反证：同一蛊真人原文用通用 band 对话占比 FAIL —— 证明 override 必须生效才不苛求真作者。"""
    raw, sd = _load_body("蛊真人", "第043章")
    if raw is None:
        return
    t_generic = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    results = vs.validate_style(raw, t_generic)
    dlg = _res(results, "对话占比")
    assert dlg.status == "FAIL", (dlg.status, dlg.detail)  # 通用 30-80% 把真实 ~20% 顶 FAIL


def test_D_real_jingsong_dialogue_not_fail_with_profile():
    """惊悚乐园原文 ch043 带作者档：对话占比不 FAIL（0-1 ratio 键正确读取）。"""
    raw, sd = _load_body("惊悚乐园", "第043章")
    if raw is None:
        return
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    results = vs.validate_style(raw, t)
    dlg = _res(results, "对话占比")
    assert dlg.status != "FAIL", (dlg.status, dlg.detail, dlg.target_desc)


def test_D_real_both_books_ai_slop_still_fails_with_profile():
    """两书原文注入 AI 结构套话 → 禁用词仍 FAIL（修 override 不放松对真问题的检测）。"""
    for name in ("蛊真人", "惊悚乐园"):
        raw, sd = _load_body(name, "第043章")
        if raw is None:
            continue
        t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
        t = vs._apply_style_overrides(t, sd)
        injected = "与此同时，他走了出去。\n\n" * 3 + raw
        results = vs.validate_style(injected, t)
        banned = _res(results, "禁用词")
        assert banned.status == "FAIL", (name, banned.status, banned.detail)
        assert "AI结构套话" in banned.name, (name, banned.name)


# ════════════════════════════════════════════════════════════════
# [E] 「事实上」分类（北极星⑤）
#
# 「事实上」归 CRAFT_SIGNATURE_BANNED（工艺签名词）而非 AI 结构套话——实测蛊真人原文
# 118 章高频用「事实上」作议论体签名连接词 = 真作者笔法非机器腔，无条件硬 FAIL 会
# 苛求真作者（违反北极星⑤顾问非法官）。有作者档降 WARN 可豁免，无作者档仍 FAIL。
# 纪律：仅「事实上」（实证真作者高频），其余 3 个 AI 结构套话（与此同时/值得一提的是/
# 不仅如此）未证伪、仍是典型机器腔，保持永久 FAIL。
# ════════════════════════════════════════════════════════════════


def test_E_shishishang_reclassified_to_craft_signature():
    """前置不变量：「事实上」在工艺签名词而非 AI 结构套话（其余 3 个仍是 AI 结构套话）。"""
    assert "事实上" not in AI_STRUCTURAL_BANNED, AI_STRUCTURAL_BANNED
    assert "事实上" in CRAFT_SIGNATURE_BANNED, CRAFT_SIGNATURE_BANNED
    # 其余 3 个 AI 结构套话不动
    for w in ("与此同时", "值得一提的是", "不仅如此"):
        assert w in AI_STRUCTURAL_BANNED, (w, AI_STRUCTURAL_BANNED)
    # 两类仍互斥
    assert set(AI_STRUCTURAL_BANNED).isdisjoint(set(CRAFT_SIGNATURE_BANNED))


def test_E_shishishang_with_profile_downgraded_to_warn():
    """有作者档 +「事实上」命中 → 禁用词降 WARN（作者签名连接词可豁免），不 FAIL。"""
    text = "事实上，他早就料到了这个结果。\n\n他转身离开。"
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t["_has_author_profile"] = True
    res = vs._chk_banned(text, vs.analyze_text(text), t)
    assert res.status == "WARN", (res.status, res.detail, res.name)
    # 应走「工艺签名词」分支（非「AI结构套话」硬毙分支）
    assert "AI结构套话" not in res.name, res.name


def test_E_shishishang_without_profile_still_fails():
    """无作者档 +「事实上」命中 → 仍 FAIL（通用写作防 AI 腔）。"""
    text = "事实上，他早就料到了这个结果。\n\n他转身离开。"
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    # 不设 _has_author_profile
    res = vs._chk_banned(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail, res.name)


def test_E_other_ai_slop_still_fails_with_profile():
    """有作者档 +「与此同时/值得一提的是/不仅如此」→ 仍 FAIL（绝不放过真 AI 腔）。"""
    for w in ("与此同时", "值得一提的是", "不仅如此"):
        text = f"{w}，他走了出去。\n\n外面下起了雨。"
        t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
        t["_has_author_profile"] = True
        res = vs._chk_banned(text, vs.analyze_text(text), t)
        assert res.status == "FAIL", (w, res.status, res.detail, res.name)
        assert "AI结构套话" in res.name, (w, res.name)


def test_E_shishishang_mixed_with_ai_slop_fails_due_to_ai_slop():
    """有作者档 +「事实上」(可豁免) 与「与此同时」(硬毙) 同时命中 → 整体仍 FAIL（AI 套话压倒）。"""
    text = "事实上他笑了。\n\n与此同时，门外有脚步声。"
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t["_has_author_profile"] = True
    res = vs._chk_banned(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail, res.name)
    assert "AI结构套话" in res.name, res.name


def test_E_real_gu_zhenren_shishishang_chapter_warn_not_fail_with_profile():
    """蛊真人真原文（含「事实上」的章）带作者档：禁用词 ≠ FAIL（核心实证·真作者签名词不被苛求）。
    反证：同章不带作者档时「事实上」命中 → FAIL（证明通用规则确会苛求真作者）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    sj = proj / "作者风格_FINAL.json"
    if not sj.exists():
        return  # 样本缺失则跳过（CI 无样本环境）
    import json
    # 找一个原文含「事实上」的章（实证锚点：第004/009/019/023章）
    body = None
    for cand in ("第004章", "第009章", "第019章", "第023章"):
        ch = proj / "原文" / f"{cand}.txt"
        if not ch.exists():
            continue
        raw = ch.read_text(encoding="utf-8")
        if "事实上" in raw:
            body = raw
            break
    if body is None:
        return  # 没有含「事实上」的样本章则跳过
    sd = json.loads(sj.read_text(encoding="utf-8"))

    # ① 带作者档：禁用词不 FAIL（「事实上」降级·真作者签名词不被硬毙）。
    # 直测 _chk_banned（命中工艺签名词时检查项会被重命名为「工艺签名词(作者档下降级)」）。
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    banned_with = vs._chk_banned(body, vs.analyze_text(body), t)
    assert banned_with.status != "FAIL", (banned_with.status, banned_with.detail, banned_with.name)

    # ② 反证·不带作者档：同章「事实上」命中 → FAIL（通用规则会苛求真作者）
    t_generic = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    banned_none = vs._chk_banned(body, vs.analyze_text(body), t_generic)
    assert banned_none.status == "FAIL", (banned_none.status, banned_none.detail)


# ════════════════════════════════════════════════════════════════
# [F] tolerant 切段 _split_paras（北极星⑤）
#
# 很多真作者原文整章无双换行 \n\n（只用单 \n + U+3000 缩进，如惊悚乐园第025章
# \n\n=0 / 单\n=58），一律 `text.split("\n\n")` 会把整章当成 1 个巨段 →
# 段落均长/单句独行占比/单段超长/长段计数全部错算（单句独行误成 0% FAIL，
# 单段超长既可能误触发也可能误掩盖 hard_gate）。
# _split_paras 的 tolerant 策略：有 \n\n 用 \n\n 切（双换行格式作者不变），
# 无 \n\n 退单 \n 切（与 style_analyzer.split_paragraphs 对齐·避免同脚本两套段定义矛盾）。
# 纪律：绝不破坏 \n\n 作者（蛊真人 \n\n 仍优先）·
# 真超长非对话段仍 hard_gate（_chk_para_max 不放松）。
# ════════════════════════════════════════════════════════════════


def test_F_split_double_newline_text_unchanged():
    """有 \n\n 的文本：用 \n\n 切段（不被退化到单 \n）。3 段 \n\n 文本 → 3 段。"""
    text = "第一段话。\n\n第二段话。\n\n第三段话。"
    paras = vs._split_paras(text)
    assert len(paras) == 3, paras
    assert paras == ["第一段话。", "第二段话。", "第三段话。"], paras


def test_F_split_double_newline_not_oversplit_by_single():
    """\n\n 文本里段内的单 \n（软换行）不被当段分隔——仍按 \n\n 切。"""
    # 第一段内部有单 \n（如诗行/段内软换行），整体应仍是 2 段（按 \n\n）
    text = "上联\n下联。\n\n第二段独立。"
    paras = vs._split_paras(text)
    assert len(paras) == 2, paras
    assert "上联\n下联。" == paras[0], paras  # 段内单 \n 保留，不切


def test_F_split_single_newline_text_falls_back():
    """无 \n\n 的文本（单 \n 分段）：退回单 \n 切（与 style_analyzer 对齐）。3 行 → 3 段。"""
    text = "　　第一段话。\n　　第二段话。\n　　第三段话。"  # U+3000 缩进 + 单 \n
    assert "\n\n" not in text
    paras = vs._split_paras(text)
    assert len(paras) == 3, paras


def test_F_split_drops_blank_and_non_cjk_lines():
    """空段 / 无 CJK 段被丢弃（与 style_analyzer.split_paragraphs 一致 count_chinese>0）。"""
    text = "真段落一。\n\n   \n\n12345\n\n真段落二。"  # 空白段 + 纯数字段
    paras = vs._split_paras(text)
    assert paras == ["真段落一。", "真段落二。"], paras


def test_F_single_line_ratio_correct_under_single_newline():
    """单 \n 分段文本：单句独行占比按真实多段算（防回归：按 \n\n 切成 1 巨段 → 0% FAIL）。"""
    # 5 个单句独行短段（每段 1 个句末符 + 短）· 全单 \n 分隔（无 \n\n）
    text = "他来了。\n她笑了。\n风停了。\n门开了。\n雨下了。"
    assert "\n\n" not in text
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_single_line_ratio(text, vs.analyze_text(text), t)
    # 按 \n\n 切会成 1 段 → 0/1 = 0% → FAIL；真实切段 5/5 = 100% → PASS
    assert res.status == "PASS", (res.status, res.detail)
    assert "5/5" in res.detail or "100" in res.detail, res.detail


def test_F_para_max_not_one_giant_para_under_single_newline():
    """单 \n 分段文本：单段超长按真实分段算，不误把整篇当 1 个巨段。"""
    # 全是短段，单 \n 分隔。按 \n\n 切会合成 1 个巨段（全篇 CJK 字数）→ 误触 hard_gate。
    text = "\n".join("短段。" for _ in range(40))
    assert "\n\n" not in text
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "PASS", (res.status, res.detail)  # 每段仅 3 字，绝不超长


def test_F_real_jingsong_chapter025_single_line_ratio_not_zero():
    """惊悚乐园原文第025章（\n\n=0·单\n=58）：单句独行占比按真实切段算 → PASS（非 0%）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "惊悚乐园"
    ch = proj / "原文" / "第025章.txt"
    if not ch.exists():
        return  # CI 无样本环境则跳过
    raw = ch.read_text(encoding="utf-8")
    assert raw.count("\n\n") == 0, raw.count("\n\n")  # 前置不变量：确无 \n\n
    # 真实切段 ≈59 段（按 \n\n 切会塌成 1 段）
    paras = vs._split_paras(raw)
    assert len(paras) >= 50, len(paras)
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_single_line_ratio(raw, vs.analyze_text(raw), t)
    # 核心实证：非 0% → 真实约 50% PASS
    assert res.status == "PASS", (res.status, res.detail)
    assert "0/1" not in res.detail and "0.0%" not in res.detail, res.detail


def test_F_real_gu_zhenren_chapter043_split_unchanged():
    """蛊真人原文第043章（\n\n=56）：tolerant 切段段数不变（\n\n 仍优先·不退化）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    ch = proj / "原文" / "第043章.txt"
    if not ch.exists():
        return
    raw = ch.read_text(encoding="utf-8")
    assert raw.count("\n\n") > 0  # 前置不变量：有 \n\n
    # tolerant 切段 == 直接按 \n\n 切（去空 + 去无 CJK）—— 蛊真人切段不被修改影响
    expect = [p.strip() for p in raw.split("\n\n") if p.strip() and vs._CJK_RE.search(p)]
    paras = vs._split_paras(raw)
    assert paras == expect, (len(paras), len(expect))


# ════════════════════════════════════════════════════════════════
# [G] long_para_per_chapter 作者档 override（北极星⑤）
#
# 通用 max_ratio 2% 按短段吐槽爽文（段均 ~15-20 字）标定；长段签名作者（蛊真人段均
# ~30 字 / 实测 per-chapter 长段率 p90 6.3% · 惊悚乐园段均 ~52 字 / gt50=43.8%）
# 不放宽会被通用 2% 顶成 FAIL（"带作者档反更苛"，违反原则⑤）。
# _apply_style_overrides 的 long_para_per_chapter override——从作者档真实长段分布
# （gt80/gt50 桶）或段均长幂律外推 max_ratio；仅 max_ratio（cluster 视野）放宽，chapter
# 视野绝对数 max 不动；有数据才放宽，无数据保通用 2%。
# 纪律：只放宽 80-120 字 advisory 计数阈值 · 绝不放松 > 120 字非对话段 hard_gate（那是
# _chk_para_max 管的 STYLE_单段超长）。
# ════════════════════════════════════════════════════════════════


def test_G_long_para_ratio_from_gt50_distribution():
    """有显式长段分布桶 gt50 → max_ratio 取 gt50 的一半（惊悚乐园 gt50=0.4379 → 0.219）。"""
    q = {"paragraph_length_distribution": {"gt50": 0.4379}}
    r = vs._extract_author_long_para_ratio(q, None)
    assert abs(r - 0.4379 * 0.5) < 1e-9, r


def test_G_long_para_ratio_prefers_gt80_over_gt50():
    """gt80 桶（更精确）优先于 gt50。"""
    q = {"paragraph_length_distribution": {"gt80": 0.12, "gt50": 0.40}}
    r = vs._extract_author_long_para_ratio(q, None)
    assert abs(r - 0.12) < 1e-9, r


def test_G_long_para_ratio_falls_back_to_para_mean():
    """无分布桶 → 用段均长幂律外推（蛊真人段均 ~30 → 覆盖其 p90 长段率 ~6%）。"""
    r = vs._extract_author_long_para_ratio({}, 30.0)
    expect = 0.02 * (30.0 / 20.0) ** 2.5  # ≈ 0.0551
    assert abs(r - expect) < 1e-6, (r, expect)
    # 必须高于通用 2%（否则 override 无意义），且覆盖蛊真人 p90(6.25%) 量级
    assert r > 0.02, r
    assert 0.04 <= r <= 0.08, r  # 覆盖该作者正常长段率范围


def test_G_long_para_ratio_capped():
    """幂律外推封顶 0.15（防极端段均长把阈值放成离谱值）。"""
    r = vs._extract_author_long_para_ratio({}, 200.0)  # 离谱大段均
    assert r == 0.15, r


def test_G_long_para_ratio_none_when_short_para_author():
    """段均 ≤20 字（短段作者）且无分布 → None（不放宽·保通用 2%）。"""
    assert vs._extract_author_long_para_ratio({}, 18.0) is None
    assert vs._extract_author_long_para_ratio({}, None) is None
    assert vs._extract_author_long_para_ratio({}, 20.0) is None  # 锚点本身不放宽


def test_G_override_only_applies_to_max_ratio_cluster_view():
    """override 只动 max_ratio（cluster 视野）；chapter 视野绝对数 max 不被改。"""
    sd = {"quantitative": {"paragraph_length_distribution": {"gt50": 0.4379}}}
    # chapter 视野（DEFAULT 用 {"max": 3}）—— 不该被 override
    t_chapter = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t_chapter = vs._apply_style_overrides(t_chapter, sd)
    assert "max" in t_chapter["long_para_per_chapter"], t_chapter["long_para_per_chapter"]
    assert t_chapter["long_para_per_chapter"]["max"] == 3, t_chapter["long_para_per_chapter"]
    # cluster 视野（CLUSTER 用 {"max_ratio": 0.02}）—— 应被放宽
    t_cluster = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t_cluster = vs._apply_style_overrides(t_cluster, sd)
    assert "max_ratio" in t_cluster["long_para_per_chapter"]
    assert abs(t_cluster["long_para_per_chapter"]["max_ratio"] - 0.4379 * 0.5) < 1e-9, t_cluster["long_para_per_chapter"]


def test_G_override_not_narrowing_below_generic():
    """作者长段率推得低于通用 2% → 不收窄（保通用 2%，override 只放宽不收紧）。"""
    sd = {"quantitative": {"paragraph_length_distribution": {"gt50": 0.02}}}  # gt50/2=1% < 2%
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    # 推得 1% < 通用 2% → 不 override（保 2%）
    assert abs(t["long_para_per_chapter"]["max_ratio"] - 0.02) < 1e-9, t["long_para_per_chapter"]


def test_G_no_profile_data_keeps_generic_2pct():
    """无任何长段分布/段均长数据 → cluster 视野保通用 2%（不放宽）。"""
    sd = {"quantitative": {"sentence_length": {"mean": 19.0}}}  # 只有句长（不能推段长）
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert abs(t["long_para_per_chapter"]["max_ratio"] - 0.02) < 1e-9, t["long_para_per_chapter"]


def test_G_real_gu_zhenren_normal_chapter_long_para_not_fail_cluster():
    """蛊真人原文第043章 CLUSTER_MODE + 作者档：长段计数不 FAIL（核心实证·正常章）。"""
    raw, sd = _load_body("蛊真人", "第043章")
    if raw is None:
        return
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    results = vs.validate_style(raw, t)
    lp = _res(results, "长段计数")
    assert lp.status != "FAIL", (lp.status, lp.detail, lp.target_desc)


def test_G_real_jingsong_long_para_not_fail_with_profile_cluster():
    """惊悚乐园原文第025章 CLUSTER_MODE + 作者档：长段计数不 FAIL（gt50 分布放宽生效）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "惊悚乐园"
    ch = proj / "原文" / "第025章.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return
    import json
    raw = ch.read_text(encoding="utf-8")
    sd = json.loads(sj.read_text(encoding="utf-8"))
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    results = vs.validate_style(raw, t)
    lp = _res(results, "长段计数")
    assert lp.status != "FAIL", (lp.status, lp.detail, lp.target_desc)


def test_G_real_long_para_override_does_not_relax_hardgate():
    """放宽 long_para（80-120 字 advisory 计数）绝不放松**超天花板**非对话段 hard_gate。

    gt50=0.4379 会同时把 max_para_chars 天花板放宽到 250（长段签名作者·floor 250），
    故用 2 段**> 250**（超放宽后天花板）的非对话段验证——long_para advisory
    层放宽绝不放松超天花板 hard_gate FAIL。"""
    sd = {"quantitative": {"paragraph_length_distribution": {"gt50": 0.4379}}}
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert t["para_max_chars"]["hard_gate"] == 250, t["para_max_chars"]  # gt50 签名 → 天花板 250
    text = ("他" * 270) + "\n\n" + ("她" * 270)  # 2 段非对话 > 250（超例外 1·仍 <300）
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)  # hard_gate 绝不被 advisory 放松


# ════════════════════════════════════════════════════════════════
# [H] 说话人前缀对话段豁免（北极星⑤）
#
# 中文网文**最高频**对话形式「说话人/动作前缀 + ：/，X道： + 整段引号包裹主体」首字是
# CJK 说话人名而非引号——只认「段首=开引号 且 段尾=闭引号」的纯引号段会把它判非对话
# → 超 hard_gate 触发 STYLE_单段超长 FAIL = 真作者对话段误伤。实证：蛊真人 ch444
# 段45『葛光便答：“…”』(146字)、段56『蛮多大怒：“…”』(124字)；ch646 段4『方源答道：
# “…”』(195字)、段30『墨瑶意志大笑一阵，语气又缓和道：“…”』(167字)。
# _is_full_dialogue_para 识别「说话人前缀（以全/半角冒号收尾·无句末符）
# + 引号包裹主体 + 段尾闭引号」也算完整对话段 → 降 WARN（对话不可中切·advisory 可豁免）。
# 纪律 a：只认真对话（提示语冒号 + 引号成对）·不把普通叙述段误判成对话豁免。
# ════════════════════════════════════════════════════════════════

_DQ_O = "“"   # 开弯引号 U+201C
_DQ_C = "”"   # 闭弯引号 U+201D


def test_H_speaker_prefix_dialogue_recognized():
    """说话人前缀 + 冒号 + 引号包裹主体 + 段尾闭引号 → 识别为完整对话段（豁免）。"""
    para = "葛光便答：" + _DQ_O + "话" * 130 + _DQ_C
    assert vs._is_full_dialogue_para(para) is True


def test_H_action_prefix_with_comma_lead_recognized():
    """带逗号引导从句的动作前缀（…，X道：）同样识别（前缀短 + 无句末符 + 冒号收尾）。"""
    para = "墨瑶意志大笑一阵，语气又缓和道：" + _DQ_O + "话" * 150 + _DQ_C
    assert vs._is_full_dialogue_para(para) is True


def test_H_pure_quote_para_still_recognized():
    """纯引号段（无前缀）仍识别（不破坏既有 ① 路径）。"""
    assert vs._is_full_dialogue_para(_DQ_O + "话" * 127 + _DQ_C) is True
    assert vs._is_full_dialogue_para("「" + "话" * 130 + "」") is True


def test_H_half_width_colon_with_space_recognized():
    """半角冒号 + 冒号与引号间空格（如 `小明说: "…"`）容错识别。"""
    para = "小明说: " + _DQ_O + "话" * 130 + _DQ_C
    assert vs._is_full_dialogue_para(para) is True


def test_H_narration_with_embedded_quote_not_exempted():
    """纪律 a：叙述段内嵌引语但**段尾非闭引号**（引号后还有叙述）→ 不算对话豁免。"""
    para = "他说：" + _DQ_O + "好的" + _DQ_C + "，然后转身走了，外面下起了大雨。" + "啊" * 100
    assert vs._is_full_dialogue_para(para) is False


def test_H_long_narration_block_plus_short_quote_not_exempted():
    """纪律 a：长叙述块（含句末符·前缀超长）+ 尾部短引语 → 仍是应受门禁的真长叙述段，不豁免。"""
    para = "天色渐暗，乌云压城，远处传来雷声。他站在窗前，望着外面发呆。许久之后他才缓缓道：" \
           + _DQ_O + "走吧。" + _DQ_C
    assert vs._is_full_dialogue_para(para) is False  # 前缀含句末符「。」→ 非单条提示语


def test_H_long_prefix_but_quote_dominant_now_exempted():
    """长动作前缀（>20 CJK）+ 引号包裹主体占段主体（≥50%）→ 豁免。
    对话豁免按对话主导（引号占比）判别·不限前缀长度——前缀长度上限会把真作者长动作
    铺垫对话当非对话 FAIL（蛊 ch15 段34 前缀 26 / 段38 前缀 35 / ch18 段9 前缀 37）。"""
    long_prefix = "甲" * 25 + "缓缓道："  # 25+ CJK 前缀（不受长度上限约束）
    para = long_prefix + _DQ_O + "话" * 100 + _DQ_C  # 引号主体 100/~131 ≈ 76% > 50%
    assert vs._is_full_dialogue_para(para) is True
    # 纪律 a 守墙：引号占比【低】的真长叙述块（长前缀 + 短引语）仍不豁免（覆盖率 < 50%）。
    narr = "甲" * 120 + "缓缓道：" + _DQ_O + "走。" + _DQ_C
    assert vs._is_full_dialogue_para(narr) is False


def test_H_unclosed_quote_not_exempted():
    """被中切的对话（开引号未闭合·段尾非闭引号）→ 不算完整对话段（不豁免）。"""
    para = "葛光便答：" + _DQ_O + "叔叔猜对了一半" + "啊" * 100  # 无闭引号
    assert vs._is_full_dialogue_para(para) is False


def test_H_colon_without_quote_not_exempted():
    """冒号引导但无引号包裹（心理活动直述）→ 非引号对话段，不豁免。"""
    assert vs._is_full_dialogue_para("他想道：天要塌了。") is False


def test_H_real_gu_zhenren_ch444_speaker_prefix_dialogue_not_hardgate():
    """蛊真人原文 ch444 说话人前缀超长对话段（葛光便答/蛮多大怒）→ 单段超长降 WARN 非 FAIL。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    ch = proj / "原文" / "第444章.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return
    import json
    raw = ch.read_text(encoding="utf-8")
    sd = json.loads(sj.read_text(encoding="utf-8"))
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = vs._chk_para_max(raw, vs.analyze_text(raw), t)
    assert res.status != "FAIL", (res.status, res.detail)  # 说话人前缀对话段不再误伤 hard_gate


def test_H_real_gu_zhenren_ch646_speaker_prefix_dialogue_not_hardgate():
    """蛊真人原文 ch646 说话人前缀超长对话段（方源答道/墨瑶…道）→ 单段超长降 WARN 非 FAIL。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    ch = proj / "原文" / "第646章.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return
    import json
    raw = ch.read_text(encoding="utf-8")
    sd = json.loads(sj.read_text(encoding="utf-8"))
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = vs._chk_para_max(raw, vs.analyze_text(raw), t)
    assert res.status != "FAIL", (res.status, res.detail)


def test_H_speaker_prefix_dialogue_over_hard_downgrades_to_warn_not_fail():
    """单段说话人前缀对话超 hard_gate（无其他非对话超长段）→ 降 WARN（不 FAIL）。"""
    para = "蛮多大怒：" + _DQ_O + "话" * 130 + _DQ_C  # 唯一超长段·说话人前缀对话
    text = "前一句短话。\n\n" + para
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "WARN", (res.status, res.detail)
    assert "豁免" in res.detail or "豁免" in res.target_desc, (res.detail, res.target_desc)


def test_H_speaker_prefix_does_not_exempt_real_long_narration():
    """纪律 a 综合：同段含说话人前缀对话（豁免）+ 另有 2 段非对话超长叙述 → 仍 FAIL（不放过真超长）。"""
    dlg = "葛光便答：" + _DQ_O + "话" * 130 + _DQ_C  # 对话超长（豁免）
    narr_a = "他" * 130   # 非对话超长 1
    narr_b = "她" * 130   # 非对话超长 2（超例外 1）
    text = dlg + "\n\n" + narr_a + "\n\n" + narr_b
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)
    assert "豁免" in res.detail, res.detail  # 报告点明对话段已豁免


# ════════════════════════════════════════════════════════════════
# [I] 长段签名作者 max_para_chars hard_gate 天花板作者档驱动 override（北极星⑤）
#
# 惊悚乐园长段是头号签名（画外音对读者 + 游戏 info-dump · 实证作者档
# paragraph_length.mean_chars=52 / gt50=0.4379 · 真作者非对话段 130-245 字 · 全库 p99=174），
# 通用 120 硬墙会误伤（121 字精心长段 = fatal），且蒸馏产物不产显式 max_para_chars 字段。
# _apply_style_overrides 的 max_para_chars 天花板**作者档驱动 override**——作者档实证有
# 长段签名（gt80/gt50 高 或 段均 ≥40）时放宽天花板到 mean×4（钳 [120,300]）；非签名/无档仍 120。
# 纪律 b/c：只对实证长段作者放宽·保绝对上限 300（防真失控）·无作者档仍 120（防 AI 滥用）。
# ════════════════════════════════════════════════════════════════


def test_I_jingsong_signature_raises_hardgate_ceiling():
    """惊悚乐园档（mean 52 / gt50 0.4379）→ max_para_chars 天花板放宽到 250（覆盖真实开篇
    info-dump 长段 209/212·floor 250 > mean×4=208 > 120）。"""
    sd = {"quantitative": {
        "paragraph_length": {"mean_chars": 51.9997},
        "paragraph_length_distribution": {"le5": 0.0373, "31to50": 0.2126, "gt50": 0.4379},
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert t["para_max_chars"]["hard_gate"] == 250, t["para_max_chars"]
    assert t["para_max_chars"]["hard_gate"] > 120, t["para_max_chars"]
    assert t["para_max_chars"]["hard_gate"] < 300, t["para_max_chars"]  # 仍 <300 绝对上限


def test_I_explicit_max_para_chars_takes_priority():
    """作者档显式 max_para_chars（最权威明示）→ 直接用（钳 ≤300）。"""
    sd = {"quantitative": {"max_para_chars": 180}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert t["para_max_chars"]["hard_gate"] == 180, t["para_max_chars"]


def test_I_no_signature_author_keeps_120():
    """无长段签名作者（段均 ~30 · 无分布桶 · 如蛊真人）→ 天花板仍 120（不放宽）。"""
    sd = {"quantitative": {
        "chapter_chars": {"mean": 2718.888},
        "paragraph_count": {"mean": 90.182},  # 段均 ≈30 < 40 → 无长段签名
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert t["para_max_chars"]["hard_gate"] == 120, t["para_max_chars"]


def test_I_no_profile_keeps_120():
    """无作者档（仅句长）→ 天花板仍 120（防 AI 滥用超长段）。"""
    sd = {"quantitative": {"sentence_length": {"mean": 19.0}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert t["para_max_chars"]["hard_gate"] == 120, t["para_max_chars"]


def test_I_ceiling_clamped_to_absolute_cap_300():
    """段均极大的作者（mean 100 → ×4=400）→ 天花板钳到绝对上限 300（防真失控）。"""
    sd = {"quantitative": {
        "paragraph_length": {"mean_chars": 100.0},
        "paragraph_length_distribution": {"gt50": 0.6},
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert t["para_max_chars"]["hard_gate"] == 300, t["para_max_chars"]


def test_I_helper_signature_detection():
    """直测 _extract_author_max_para_chars 长段签名判别 + 钳位（floor 250）。"""
    # gt50 ≥ 0.20 签名 → max(mean×4=208, floor 250) = 250
    assert vs._extract_author_max_para_chars({"paragraph_length_distribution": {"gt50": 0.4379}}, 52.0) == 250
    # gt80 ≥ 0.05 签名（无段均·退用 208 锚）→ max(208, floor 250) = 250
    assert vs._extract_author_max_para_chars({"paragraph_length_distribution": {"gt80": 0.08}}, None) == 250
    # 段均 ≥40 签名（无分布·mean×4=180）→ max(180, floor 250) = 250
    assert vs._extract_author_max_para_chars({}, 45.0) == 250
    # 段均极大（mean×4 > floor）→ 取 mean×4 钳 ≤300：mean 70 → 280
    assert vs._extract_author_max_para_chars({}, 70.0) == 280
    # 无签名（短段·无分布）→ None
    assert vs._extract_author_max_para_chars({}, 30.0) is None
    assert vs._extract_author_max_para_chars({}, None) is None
    # gt50 < 0.20 且段均 < 40 → 无签名 → None
    assert vs._extract_author_max_para_chars({"paragraph_length_distribution": {"gt50": 0.10}}, 30.0) is None


def test_I_runaway_over_abs_cap_still_fails_even_with_signature():
    """构造单段 500 字纯叙述 + 惊悚乐园档（天花板 208）→ 超绝对上限 300 仍 FAIL（不可豁免·纪律 c）。"""
    sd = {"quantitative": {
        "paragraph_length": {"mean_chars": 51.9997},
        "paragraph_length_distribution": {"gt50": 0.4379},
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    text = "他" * 500  # 单段纯叙述 500 字 > 绝对上限 300
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)
    assert "绝对上限" in res.detail, res.detail


def test_I_ai_superlong_narration_without_profile_still_fails_at_120():
    """无作者档 AI 超长叙述段（>120 非对话·超例外）→ 仍 FAIL（守 120 防滥用·纪律 c）。"""
    text = ("他" * 150) + "\n\n" + ("她" * 150)  # 2 段非对话 > 120（超例外 1）
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}  # 无作者档
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)


def test_I_generic_narration_over_120_with_short_para_author_still_fails():
    """普通叙述段（非对话·非长段签名作者）>120 → 仍 FAIL（短段作者天花板仍 120）。"""
    sd = {"quantitative": {  # 短段作者：段均 ~30 无分布签名
        "chapter_chars": {"mean": 2718.888},
        "paragraph_count": {"mean": 90.182},
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    text = ("他" * 130) + "\n\n" + ("她" * 130)  # 2 段非对话 > 120（超例外 1）
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)


def test_I_real_jingsong_long_para_chapter_not_hardgate_with_profile():
    """惊悚乐园真原文长段章带作者档：单段超长不再系统性 FAIL（核心实证·画外音/info-dump 长段不误伤）。
    反证：同章不带作者档（通用 120）单段超长 FAIL（证明确有矫枉过正）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "惊悚乐园"
    sj = proj / "作者风格_FINAL.json"
    if not sj.exists():
        return
    import json
    # 找一个真有长段（>120 非对话段）但 ≤208 的章（实证锚：第002/003/004 等长段章）
    body = None
    for cand in ("第002章", "第003章", "第004章", "第005章", "第006章", "第007章"):
        ch = proj / "原文" / f"{cand}.txt"
        if not ch.exists():
            continue
        raw = ch.read_text(encoding="utf-8")
        # 该章须含 >120 非对话段（旧 120 会 FAIL）且无 >300 真失控段
        lens_nd = [len(vs._CJK_RE.findall(pp)) for pp in vs._split_paras(raw)
                   if not vs._is_full_dialogue_para(pp)]
        if any(120 < n <= 208 for n in lens_nd) and not any(n > 300 for n in lens_nd):
            body = raw
            break
    if body is None:
        return
    sd = json.loads(sj.read_text(encoding="utf-8"))
    # ① 带作者档：天花板放宽 208 → 不 FAIL
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res_with = vs._chk_para_max(body, vs.analyze_text(body), t)
    assert res_with.status != "FAIL", (res_with.status, res_with.detail, res_with.target_desc)
    # ② 反证·不带作者档（通用 120）：同章单段超长 FAIL（矫枉过正实证）
    t_gen = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    res_none = vs._chk_para_max(body, vs.analyze_text(body), t_gen)
    assert res_none.status == "FAIL", (res_none.status, res_none.detail)


# ════════════════════════════════════════════════════════════════
# [J] 章字数 band 的 cluster 视野（北极星①）
#
# CLUSTER_MODE 下 validate 跑在**整 cluster draft**上（cluster-write 产 cluster
# draft·splitter 才切章），CLUSTER_THRESHOLDS 把章字数 band 设成 cluster 级 8000-30000。
# 带 --style 时若用作者**单章**字数 mean(蛊真人 2719 / 惊悚 2921)±500 会塌回单章
# band(2218-3218)，整 cluster (蛊真人 ch641-646=18846 / 惊悚 ch25-30=19754) **必 FAIL**。
# 规则：仅 cluster 视野（base band 下限 ≥ 8000）时，把作者单章 mean 按估算每 cluster 章数
# (3-8) 放大成 cluster band 并与健康区间取并集；单章视野(DEFAULT/STRICT)仍 ±500。
# 纪律：只动章字数维度的 cluster 视野·不动其他维度·章字数 ∉ HARD_GATE（advisory）·单章视野不变。
# ════════════════════════════════════════════════════════════════


def test_J_cluster_view_chapter_words_band_not_collapsed_to_single():
    """CLUSTER_THRESHOLDS（base 下限 8000）+ 作者单章 mean → band 放大到 cluster 级（不塌回单章）。
    防回归：塌回单章 band（chapter_chars 2719 → 2219-3219）会把 18846 字真 cluster 顶 FAIL。"""
    sd = {"quantitative": {"chapter_chars": {"mean": 2718.888}}}  # 蛊真人单章 mean
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["chapter_words"]["min"], t["chapter_words"]["max"]
    # 绝不塌回单章 band（2219-3219）
    assert not (abs(lo - (2718.888 - 500)) < 1 and abs(hi - (2718.888 + 500)) < 1), (lo, hi)
    # cluster 级真字数（18846 / 19754）必落 band 内（修后不再 FAIL）
    assert lo <= 18846 <= hi, (lo, hi)
    assert lo <= 19754 <= hi, (lo, hi)
    # band 绝不窄于健康 cluster 区间（并集口径：带作者档不比纯 CLUSTER_THRESHOLDS 更苛）
    assert lo <= 8000 and hi >= 30000, (lo, hi)


def test_J_single_chapter_view_chapter_words_band_unchanged():
    """单章视野（DEFAULT·base 下限 1800 < 8000）+ 作者单章 mean → 仍 ±500（零回归）。"""
    sd = {"quantitative": {"chapter_chars": {"mean": 2718.888}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["chapter_words"]["min"], t["chapter_words"]["max"]
    assert abs(lo - (2718.888 - 500)) < 1e-6 and abs(hi - (2718.888 + 500)) < 1e-6, (lo, hi)


def test_J_strict_chapter_view_chapter_words_band_unchanged():
    """STRICT 单章视野（base 下限 2800 < 8000）+ 作者单章 mean → 仍 ±500（零回归）。"""
    sd = {"quantitative": {"chapter_words": {"mean": 2921.428}}}
    t = {k: dict(v) for k, v in vs.STRICT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["chapter_words"]["min"], t["chapter_words"]["max"]
    assert abs(lo - (2921.428 - 500)) < 1e-6 and abs(hi - (2921.428 + 500)) < 1e-6, (lo, hi)


def test_J_cluster_band_helper_union_with_healthy_range():
    """直测 _cluster_chapter_words_band：作者单章 mean × [3,8] 与健康 band 取并集。"""
    # 蛊真人 mean 2719 × [3,8] = [8157, 21752] ∪ [8000,30000] = [8000, 30000]
    b = vs._cluster_chapter_words_band(2718.888, 8000, 30000)
    assert b["min"] == 8000 and b["max"] == 30000, b
    # 极小单章作者（mean 1200）× 3 = 3600 < 8000 → 下限取 3600（不高于健康下限·防短 cluster 误 FAIL）
    b2 = vs._cluster_chapter_words_band(1200.0, 8000, 30000)
    assert b2["min"] == 3600.0 and b2["max"] == 30000, b2
    # 厚重单章作者（mean 4500）× 8 = 36000 > 30000 → 上限取 36000（不低于健康上限·容厚重 cluster）
    b3 = vs._cluster_chapter_words_band(4500.0, 8000, 30000)
    assert b3["max"] == 36000.0 and b3["min"] == 8000, b3


def test_J_cluster_view_other_dimensions_untouched():
    """纪律：#1 只动章字数 cluster 视野，不动其他维度（对话占比/段长仍按各自 override）。"""
    sd = {"quantitative": {
        "chapter_chars": {"mean": 2718.888},
        "dialogue_ratio_pct": {"mean": 25.409},
        "paragraph_length": {"mean_chars": 30.0},
    }}
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    # 对话占比仍按 _extract_author_dialogue_ratio 归一（不被 #1 改动影响）
    assert abs(t["dialogue_ratio"]["min"] - (0.25409 - 0.15)) < 1e-6, t["dialogue_ratio"]
    # 段长仍按真实段长推 band（不被 #1 改动影响）
    assert abs(t["para_mean_len"]["min"] - 30.0 * 0.7) < 1e-6, t["para_mean_len"]


def test_J_real_gu_zhenren_cluster_chapter_words_not_fail():
    """蛊真人 ch641-646 合并整 cluster CLUSTER_MODE + 作者档：章节字数不误 FAIL（核心实证）。
    反证：塌回单章 band 会 FAIL（band 2219-3219 顶 18846 字 cluster）。"""
    import json
    import chapter_io as cio
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    sj = proj / "作者风格_FINAL.json"
    if not sj.exists():
        return
    bodies = []
    for ch in range(641, 647):
        cp = proj / "原文" / f"第{ch:03d}章.txt"
        if not cp.exists():
            return  # 样本不全则跳过
        bodies.append(cp.read_text(encoding="utf-8"))
    cluster = "\n\n".join(bodies)
    assert cio.count_words(cluster) > 8000  # 前置：确是 cluster 级字数
    sd = json.loads(sj.read_text(encoding="utf-8"))
    # ① cluster 视野 + 作者档：章字数 PASS（band 放大到 cluster 级）
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = _res(vs.validate_style(cluster, t), "章节字数")
    assert res.status == "PASS", (res.status, res.detail, res.target_desc)
    # ② 反证：单章视野（DEFAULT·塌回单章 band）会 FAIL（证明 cluster 视野放大必要）
    t_single = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t_single = vs._apply_style_overrides(t_single, sd)
    res_s = _res(vs.validate_style(cluster, t_single), "章节字数")
    assert res_s.status == "FAIL", (res_s.status, res_s.detail)


def test_J_real_jingsong_cluster_chapter_words_not_fail():
    """惊悚乐园 ch25-30 合并整 cluster CLUSTER_MODE + 作者档：章节字数不误 FAIL。"""
    import json
    import chapter_io as cio
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "惊悚乐园"
    sj = proj / "作者风格_FINAL.json"
    if not sj.exists():
        return
    bodies = []
    for ch in range(25, 31):
        cp = proj / "原文" / f"第{ch:03d}章.txt"
        if not cp.exists():
            return
        bodies.append(cp.read_text(encoding="utf-8"))
    cluster = "\n\n".join(bodies)
    assert cio.count_words(cluster) > 8000
    sd = json.loads(sj.read_text(encoding="utf-8"))
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = _res(vs.validate_style(cluster, t), "章节字数")
    assert res.status == "PASS", (res.status, res.detail, res.target_desc)


# ════════════════════════════════════════════════════════════════
# [K] _split_paras 混合格式细切（北极星①）
#
# 混合格式（章间 \n\n + 章内单 \n，如多章合并或 writer 混合输出）只按 \n\n 切会让
# **每章塌成 1 个巨段**（实测惊悚 6 章合并切出 6 段 2642-3580 字）→ 全超 300 绝对上限
# → 误触发 STYLE_单段超长 hard_gate（误伤真作者）。
# 规则：\n\n 切后，对**仍是混合格式巨段**（CJK > 200 且含 ≥2 个单 \n）的段再用单 \n 细切。
# 纪律：纯 \n\n（蛊真人·\n\n-段最大 84 字）不变 · 纯单 \n（惊悚单章·走 fallback）不变 ·
# 「单条正常长段含 1 个软换行」< 200 字 / 仅 1 个 \n → 不被细切 · 真 >300 纯叙述段仍 hard_gate。
# ════════════════════════════════════════════════════════════════


def test_K_mixed_format_giant_segment_subsplit():
    """混合格式（章间 \n\n + 章内单 \n）：\n\n 切出的巨段被单 \n 细切（不再每章 1 巨段）。"""
    # 两「章」各内部用单 \n 分短段，章间 \n\n。每章 cjk > 200（巨段）·内含 ≥2 单 \n → 触发细切。
    ch1 = "\n".join("短段甲乙丙。" for _ in range(60))   # 60 段·每段 5 CJK·共 300 cjk·内 59 个单 \n
    ch2 = "\n".join("短段丁戊己。" for _ in range(60))
    text = ch1 + "\n\n" + ch2
    paras = vs._split_paras(text)
    # 细切后 = 120 短段（而非 \n\n 切的 2 巨段）
    assert len(paras) == 120, len(paras)
    assert all(len(vs._CJK_RE.findall(p)) <= 10 for p in paras), \
        sorted((len(vs._CJK_RE.findall(p)) for p in paras), reverse=True)[:3]


def test_K_mixed_format_no_false_hardgate():
    """混合格式巨段细切后单段超长不误 hard_gate（防回归：每章 1 巨段全超绝对上限 FAIL）。"""
    # 每「章」cjk > 300（不细切则单段超绝对上限 300 → FAIL）·细切后每段仅 ~5 字
    ch1 = "\n".join("一句短段话。" for _ in range(80))   # 80 段·每段 5 CJK·共 400 cjk
    ch2 = "\n".join("另外一短段。" for _ in range(80))
    text = ch1 + "\n\n" + ch2
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "PASS", (res.status, res.detail)  # 细切后每段 ~5 字·绝不超长


def test_K_pure_double_newline_normal_paras_not_subsplit():
    """纯 \n\n 作者的正常段（< 200 字）不被细切——即使段内含 1 个软换行（上联\n下联）。"""
    # 模拟蛊真人式：每段 \n\n 分隔·段长 < 200·其中一段含 1 个软换行
    text = "正常段一。\n\n上联\n下联。\n\n正常段三。"
    paras = vs._split_paras(text)
    assert paras == ["正常段一。", "上联\n下联。", "正常段三。"], paras  # 软换行段保留不切


def test_K_double_newline_long_para_with_one_soft_newline_not_subsplit():
    """> 200 字但仅含 1 个单 \n 的长段（不满足 ≥2 内 \n）→ 不细切（守「单条正常长段」不破坏）。"""
    seg = ("描述" * 120) + "\n" + ("续写" * 60)  # >200 cjk 但只有 1 个单 \n
    text = "短段。\n\n" + seg
    paras = vs._split_paras(text)
    # seg 保持为整段（不满足 ≥2 内 \n → 不细切）
    assert seg in paras, [p[:20] for p in paras]
    assert len(paras) == 2, len(paras)


def test_K_pure_single_newline_fallback_unchanged():
    """无 \n\n 文本（纯单 \n 分段·如惊悚单章）→ 走 fallback 单 \n 切（零回归）。"""
    text = "　　第一段。\n　　第二段。\n　　第三段。"
    assert "\n\n" not in text
    paras = vs._split_paras(text)
    assert len(paras) == 3, paras


def test_K_real_jingsong_multichapter_join_subsplit_no_false_hardgate():
    """惊悚乐园 ch25-30 多章 \n\n join（混合格式）：细切后单段超长不误 hard_gate（核心实证）。
    反证：只按 \n\n 切会成 6 巨段（2642-3580 字）全超绝对上限 → FAIL。"""
    import json
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "惊悚乐园"
    sj = proj / "作者风格_FINAL.json"
    if not sj.exists():
        return
    bodies = []
    for ch in range(25, 31):
        cp = proj / "原文" / f"第{ch:03d}章.txt"
        if not cp.exists():
            return
        bodies.append(cp.read_text(encoding="utf-8"))
    cluster = "\n\n".join(bodies)
    # 前置不变量：每章内部无 \n\n（混合格式）→ \n\n 数 = 章数-1
    assert cluster.count("\n\n") == len(bodies) - 1, cluster.count("\n\n")
    # 细切出真实多段（绝非 6 巨段）·最大段落远小于绝对上限 300
    paras = vs._split_paras(cluster)
    assert len(paras) >= 200, len(paras)
    assert max(len(vs._CJK_RE.findall(p)) for p in paras) < 300, \
        sorted((len(vs._CJK_RE.findall(p)) for p in paras), reverse=True)[:3]
    sd = json.loads(sj.read_text(encoding="utf-8"))
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = vs._chk_para_max(cluster, vs.analyze_text(cluster), t)
    assert res.status != "FAIL", (res.status, res.detail)  # 不误 hard_gate


def test_K_real_gu_zhenren_chapter043_split_unchanged():
    """蛊真人原文第043章（纯 \n\n·段最大 84 字）：细切阈值不触发·切段与纯 \n\n 切完全一致（零回归）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    ch = proj / "原文" / "第043章.txt"
    if not ch.exists():
        return
    raw = ch.read_text(encoding="utf-8")
    expect = [p.strip() for p in raw.split("\n\n") if p.strip() and vs._CJK_RE.search(p)]
    paras = vs._split_paras(raw)
    assert paras == expect, (len(paras), len(expect))


def test_K_real_jingsong_chapter025_single_newline_unchanged():
    """惊悚乐园原文第025章（纯单 \n·无 \n\n）：走 fallback·切段与纯单 \n 切完全一致（零回归）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "惊悚乐园"
    ch = proj / "原文" / "第025章.txt"
    if not ch.exists():
        return
    raw = ch.read_text(encoding="utf-8")
    assert raw.count("\n\n") == 0
    expect = [p.strip() for p in raw.split("\n") if p.strip() and vs._CJK_RE.search(p)]
    paras = vs._split_paras(raw)
    assert paras == expect, (len(paras), len(expect))


def test_K_true_runaway_narration_over_abs_cap_still_hardgate():
    """混合格式细切后，真 >300 纯叙述失控段仍 hard_gate（不放松真问题·绝对上限 300 不破）。"""
    sd = {"quantitative": {  # 惊悚长段签名（天花板放宽到 250，绝对上限仍 300）
        "paragraph_length": {"mean_chars": 51.9997},
        "paragraph_length_distribution": {"gt50": 0.4379},
    }}
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    # 混合格式上下文 + 中间嵌一个 500 字纯叙述失控段
    ch1 = "\n".join("短段。" for _ in range(50))
    runaway = "他" * 500  # >300 绝对上限·纯叙述（无引号）
    text = ch1 + "\n\n" + runaway + "\n\n" + "尾段短句。"
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)
    assert "绝对上限" in res.detail, res.detail


# ════════════════════════════════════════════════════════════════
# [L] STYLE_单段超长豁免规则（北极星⑤ · 对话主导/跨段对话/paratext/天花板覆盖真实长段）
#
# 真作者原文（蛊真人 + 惊悚乐园 逐章 strict）存在 5 类合法长段（均 <300 绝对上限·非真失控），
# 无豁免会被 STYLE_单段超长 hard_gate 误伤：
#   ① 长动作前缀对话（蛊 ch15 段34/38·ch18 段9）：前缀 >20 CJK 的对话段。
#   ② 段首对话署名后置（蛊 ch28 段7）：对话在段首·署名落段尾·段尾非闭引号。
#   ③ (ps:)作者后记 paratext（蛊 ch18 段104·ch28 段82）：排版致歉/打赏后记非正文。
#   ④ 跨段未闭合引号连续对话（惊悚 ch128 段31/49）：人物长独白被拆视觉段·开引号未闭合跨段。
#   ⑤ 开篇 info-dump 纯叙述长段（惊悚 ch1 段32=209/段34=212）：略超 mean×4=208 估值。
# 豁免规则：(a) 对话主导段（成对引号包裹内容 ≥50%）豁免·覆盖①②；(b) 跨段对话（段首开引号+段内
# 未闭合开引号）豁免·覆盖④；(c) paratext（(ps/（ps 标记段 + 其后段）从 hard_gate 剔除·覆盖③；
# (d) 长段签名作者天花板 max(mean×4, floor 250) 钳 ≤300·覆盖⑤。
# 纪律：真 >300 绝对上限纯叙述段仍 hard_gate（test_K_true_runaway + 下方 test_L_*_still_hardgate）。
# ════════════════════════════════════════════════════════════════

_DO = "“"   # 开弯引号
_DC = "”"   # 闭弯引号


# ── (a) 对话主导段豁免（覆盖缺口①长动作前缀 + ②署名后置）──

def test_L_long_action_prefix_dialogue_dominant_exempted():
    """缺口①：长动作前缀（26-37 CJK）+ 引号主体占段主体 → 对话主导·豁免（is_dialog=True）。"""
    # 仿蛊 ch15 段34「花酒行者…精芒频闪：“……”」前缀 26 CJK·引号主体 ~98/124=79%
    para = "花酒行者眯起了双眼他的怒火已经平息眼缝中精芒频闪：" + _DO + "话" * 98 + _DC
    assert len(vs._CJK_RE.findall(para[:para.index(_DO)])) > 20  # 前缀 >20 CJK（不设长度上限）
    assert vs._is_full_dialogue_para(para) is True


def test_L_dialogue_first_attribution_tail_exempted():
    """缺口②：对话在段首 + 署名落段尾（段尾非闭引号）→ 对话主导（引号占比高）·豁免。"""
    # 仿蛊 ch28 段7「“……”学堂家老寒声质问。」引号主体 ~139/147=95%·段尾是「。」非闭引号
    para = _DO + "话" * 139 + _DC + "学堂家老寒声质问。"
    assert para.strip()[-1] not in vs._Q_CLOSE_CP  # 段尾非闭引号（纯引号路径接不住·靠对话主导豁免）
    assert vs._is_full_dialogue_para(para) is True


def test_L_gap1_real_gu_zhenren_ch15_not_hardgate():
    """缺口① 实证：蛊真人 ch15（段34/38 长动作前缀对话）带作者档 → 单段超长 ≠ FAIL。"""
    raw, sd = _load_body("蛊真人", "第015章")
    if raw is None:
        return
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = vs._chk_para_max(raw, vs.analyze_text(raw), t)
    assert res.status != "FAIL", (res.status, res.detail)


def test_L_gap1_real_gu_zhenren_ch18_not_hardgate():
    """缺口①③ 实证：蛊真人 ch18（段9 长动作前缀对话 + 段102-105 后记块）带作者档 → 单段超长 ≠ FAIL。"""
    raw, sd = _load_body("蛊真人", "第018章")
    if raw is None:
        return
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = vs._chk_para_max(raw, vs.analyze_text(raw), t)
    assert res.status != "FAIL", (res.status, res.detail)


def test_L_gap2_real_gu_zhenren_ch28_not_hardgate():
    """缺口②③ 实证：蛊真人 ch28（段7 署名后置对话 + 段82 (ps:)后记）带作者档 → 单段超长 ≠ FAIL。"""
    raw, sd = _load_body("蛊真人", "第028章")
    if raw is None:
        return
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = vs._chk_para_max(raw, vs.analyze_text(raw), t)
    assert res.status != "FAIL", (res.status, res.detail)


# ── (b) 跨段对话延续豁免（覆盖缺口④）──

def test_L_cross_para_unclosed_quote_dialogue_exempted():
    """缺口④：段首开引号 + 段内有未闭合开引号（闭引落下一视觉段·对话跨段延续）→ 豁免。"""
    # 仿惊悚 ch128 段31：开 “第一…” 闭 → 又开 “第二…  未闭合（闭引落下段）
    para = _DO + "第一句完整引语。" + _DC + "中间叙述。" + _DO + "第二句开了但没闭合" + "续" * 200
    assert para.strip()[0] in vs._Q_OPEN_CP
    _, unclosed = vs._quote_pair_cjk_coverage(para)
    assert unclosed is True  # 末尾确有未闭合开引号
    assert vs._is_full_dialogue_para(para) is True


def test_L_gap4_real_jingsong_ch128_not_hardgate():
    """缺口④ 实证：惊悚乐园 ch128（段31=243/段49=234 跨段未闭合引号长独白）带作者档 → 单段超长 ≠ FAIL。"""
    raw, sd = _load_body("惊悚乐园", "第128章")
    if raw is None:
        return
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    res = vs._chk_para_max(raw, vs.analyze_text(raw), t)
    assert res.status != "FAIL", (res.status, res.detail)


# ── (c) paratext 剔除（覆盖缺口③）──

def test_L_paratext_marker_para_excluded_fullwidth():
    """缺口③：「（ps：…）」全角后记长段从 hard_gate 剔除（不触发单段超长）。"""
    body = "正常短段。\n\n正常短段二。"
    ps = "（ps：" + "感谢打赏" * 40 + "）"   # 160 CJK 后记长段
    text = body + "\n\n" + ps
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "PASS", (res.status, res.detail)


def test_L_paratext_marker_para_excluded_halfwidth():
    """缺口③：半角「(ps:…)」后记长段同样剔除。"""
    body = "正文短段。"
    ps = "(ps:" + "求推荐票" * 40 + ")"
    text = body + "\n\n" + ps
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "PASS", (res.status, res.detail)


def test_L_paratext_trailing_block_excluded():
    """缺口③：marker 段 + 其后续段（trailing 后记块·如蛊 ch18 段102-105）整块剔除。"""
    body = "正文短段。\n\n正文短段二。"
    ps_marker = "（ps：感谢大家支持。）"
    afterword = "我幻想这样的情形。" + "怀念" * 60   # 后记续段长段（>120·无 marker 但属后记块）
    text = body + "\n\n" + ps_marker + "\n\n" + afterword
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "PASS", (res.status, res.detail)  # marker 后整块剔除·不触发 hard_gate


def test_L_paratext_does_not_exclude_body_before_marker():
    """纪律：marker【之前】的正文长段照常检测（绝不漏检正文·只剔 marker 起的后记块）。"""
    body_runaway = "他" * 130   # marker 之前的非对话超长正文段（应仍 hard_gate·超例外）
    body_runaway2 = "她" * 130
    ps = "（ps：感谢打赏。）"
    text = body_runaway + "\n\n" + body_runaway2 + "\n\n" + ps
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)  # marker 前正文超长仍 FAIL


# ── (d) 天花板覆盖真实长段（覆盖缺口⑤）──

def test_L_gap5_real_jingsong_ch1_infodump_not_hardgate():
    """缺口⑤ 实证：惊悚乐园 ch1（段32=209/段34=212 开篇 info-dump 纯叙述长段）带作者档 →
    单段超长 ≠ FAIL（天花板 250 覆盖 212）。反证：通用 120 会 FAIL。"""
    raw, sd = _load_body("惊悚乐园", "第001章")
    if raw is None:
        return
    # ① 带作者档：天花板 250 覆盖 209/212 → 不 FAIL
    t = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    assert t["para_max_chars"]["hard_gate"] == 250, t["para_max_chars"]
    res = vs._chk_para_max(raw, vs.analyze_text(raw), t)
    assert res.status != "FAIL", (res.status, res.detail)
    # ② 反证·不带作者档（通用 120）：同章 info-dump 长段 FAIL（证明确有矫枉过正）
    t_gen = {k: dict(v) for k, v in vs.CLUSTER_THRESHOLDS.items()}
    res_gen = vs._chk_para_max(raw, vs.analyze_text(raw), t_gen)
    assert res_gen.status == "FAIL", (res_gen.status, res_gen.detail)


def test_L_ceiling_covers_212_but_under_300():
    """缺口⑤：长段签名作者天花板 250 覆盖真实 212 长段·但 <300 绝对上限（绝不破墙）。"""
    sd = {"quantitative": {
        "paragraph_length": {"mean_chars": 51.9997},
        "paragraph_length_distribution": {"gt50": 0.4379},
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    # 单段 212 字纯叙述（开篇 info-dump·非对话但属作者真实长段）→ ≤250 天花板·不 FAIL
    text = "开篇短句。\n\n" + ("设" * 212)
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status != "FAIL", (res.status, res.detail)


# ── 纪律守墙：真 >300 绝对上限纯叙述失控段仍 hard_gate（不放松真失控）──

def test_L_true_runaway_over_300_pure_narration_still_hardgate():
    """守墙：>300 绝对上限纯叙述段（无引号·引号占比 0）即便长段签名作者天花板 250 仍 hard_gate。"""
    sd = {"quantitative": {
        "paragraph_length": {"mean_chars": 51.9997},
        "paragraph_length_distribution": {"gt50": 0.4379},
    }}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    text = "短句。\n\n" + ("失" * 350)  # 350 字纯叙述·无引号 > 300 绝对上限
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)
    assert "绝对上限" in res.detail, res.detail


def test_L_true_runaway_low_quote_ratio_over_300_still_hardgate():
    """守墙：>300 段且引号占比低（< 50%·非跨段开头）= 真失控叙述（非对话主导）→ 仍 hard_gate。"""
    sd = {"quantitative": {"paragraph_length": {"mean_chars": 51.9997},
                           "paragraph_length_distribution": {"gt50": 0.4379}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    # 350 字叙述 + 段中嵌一句短引语（引号占比 ~10% < 50%·非段首开引号）→ 非对话主导·真失控
    runaway = "失" * 160 + _DO + "短句" + _DC + "控" * 188
    assert not vs._is_full_dialogue_para(runaway)  # 引号占比低·非对话主导
    text = "短句。\n\n" + runaway
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status == "FAIL", (res.status, res.detail)
    assert "绝对上限" in res.detail, res.detail


def test_L_dialogue_dominant_over_300_exempted_not_runaway():
    """对照：>300 但对话主导（引号占比 ≥50%）= 真超长对话非失控叙述 → 不触发绝对上限 hard_gate。
    （对话不可中切·绝对上限墙只拦【非对话主导】真失控叙述段·守北极星⑤ 顾问非法官）。"""
    sd = {"quantitative": {"paragraph_length": {"mean_chars": 51.9997},
                           "paragraph_length_distribution": {"gt50": 0.4379}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    para = "某人怒道：" + _DO + "话" * 340 + _DC   # 340 字·引号主体 340/345≈99% 对话主导
    assert vs._is_full_dialogue_para(para) is True
    text = "短句。\n\n" + para
    res = vs._chk_para_max(text, vs.analyze_text(text), t)
    assert res.status != "FAIL", (res.status, res.detail)  # 对话主导段豁免（即便 >300）
