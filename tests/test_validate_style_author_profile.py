"""validate_style 作者档「第一权威·更贴作者非更苛」回归测试 — 守护 2026-05-30 三处矫枉过正修。

背景（北极星⑤ 顾问非法官·作者档第一权威）：实测蛊真人 auto_008 真作者原文喂校验发现
**带作者档反而更苛**的三处 bug，全部违反「作者档应让审核更贴作者，而非更苛」：

  [B] 段长单位错配：作者档 paragraph_length_chars=null 时旧实现退回用 sentence_length.mean
      (句长 19) ×0.7-1.3 推段长 band(13-25)，把段落本就长(34)的作者从 PASS 顶成 FAIL。
      → 修：段长 band 只用**真实段长数据**（paragraph_length_chars 或 章字数÷段数），
         都缺 → 段长维度不 override（保通用 band，不收窄）。绝不用句长推段长。

  [A] STYLE_单段超长 hard_gate 误伤对话段：完整对话句(整段包在弯/方头引号内·不可中切)
      被纯按 CJK 字数判成 hard_gate。→ 修：完整对话段超长降 WARN(advisory 可豁免)，
      非对话超长段仍 hard_gate(FAIL)。

  [C] 配额词检查无视作者档：配额词里的工艺签名类(顿时/微微)与 CRAFT_SIGNATURE 重叠，
      有作者档时旧实现仍超额 FAIL，与 _chk_banned 已有的作者档降级逻辑不一致。
      → 修：有作者档时签名类配额词超额降 WARN，非签名类(突然/莫名)超额仍 FAIL。

关键纪律：让作者档真正第一权威·更贴作者，但**不放松对真问题的检测**——
AI 结构套话仍 FAIL · 真超长非对话段仍 hard_gate · 非签名类配额词超额仍 FAIL。

只测确定性纯函数（程序化校验），不碰 LLM / agent。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import validate_style as vs  # noqa: E402
from style_analyzer import CRAFT_SIGNATURE_QUOTA_WORDS  # noqa: E402


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
    # 蛊真人原文实测段均 33.9 必须落在 band 内（核心 bug：旧实现 13.35-24.78 把 33.9 顶成 FAIL）
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
    """蛊真人原文第043章带作者档校验：段长不再 FAIL（核心实证）。"""
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / "蛊真人"
    ch = proj / "原文" / "第043章.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return  # 样本缺失则跳过（CI 无样本环境）
    import json
    import chapter_io as cio
    raw = ch.read_text(encoding="utf-8")
    for sep in cio.CHANGES_SEPARATORS:
        if sep in raw:
            raw = raw.split(sep)[0].rstrip()
            break
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
    import chapter_io as cio
    raw = ch.read_text(encoding="utf-8")
    for sep in cio.CHANGES_SEPARATORS:
        if sep in raw:
            raw = raw.split(sep)[0].rstrip()
            break
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
    """对话检测必须 codepoint 区分左/右引号——只有左引号开头+右引号结尾才算完整对话段。"""
    full = _DQ_OPEN + "话" * 5 + _DQ_CLOSE
    assert vs._is_full_dialogue_para(full) is True
    # 只有左引号（未闭合，被切断的对话） → 不算完整对话段
    assert vs._is_full_dialogue_para(_DQ_OPEN + "话" * 5) is False
    # 左右引号倒置（右引号开头）→ 不算
    assert vs._is_full_dialogue_para(_DQ_CLOSE + "话" * 5 + _DQ_OPEN) is False
    # 纯叙述 → 不算
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
    """无作者档 + 同样签名类配额词超额 → 仍 FAIL（向后兼容旧行为）。"""
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
# [D] override 键名/单位不符（2026-05-30）：作者档 key tolerant 兼容 + 单位对齐
#
# 矫枉过正根源：override 读 q["dialogue_ratio"]（假设 0-1）/ q["chapter_words"]，
# 但蛊真人档实际键是 dialogue_ratio_pct（百分比 25.4）/ chapter_chars → 键名+单位不符
# → override 失效 → 退回通用 band 苛求真作者（真实 ~25% 对话被通用 30-80% 顶成 FAIL）。
# 修：tolerant 读多命名 + 百分比键 /100 归一到 validate_style 内部 0-1 ratio 单位。
# 纪律：让作者档真正第一权威（北极星⑤），但**不放松对真问题的检测**。
# ════════════════════════════════════════════════════════════════

def test_D_dialogue_ratio_pct_key_read_and_unit_converted():
    """dialogue_ratio_pct（百分比 25.409）被正确读取并 /100 归一为 0-1 ratio band。
    核心 bug：旧实现只读 dialogue_ratio（蛊真人无此键）→ override 失效。"""
    sd = {"quantitative": {"dialogue_ratio_pct": {"mean": 25.409}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["dialogue_ratio"]["min"], t["dialogue_ratio"]["max"]
    # 25.409% -> 0.25409 ± 0.15 = 0.10409 .. 0.40409（单位对齐到 0-1 ratio）
    assert abs(lo - (0.25409 - 0.15)) < 1e-6 and abs(hi - (0.25409 + 0.15)) < 1e-6, (lo, hi)
    # 蛊真人真实对话占比 ~0.205 必须落 band 内（旧实现退回通用 0.30-0.80 把 0.205 顶成 FAIL）
    assert lo <= 0.205 <= hi, (lo, hi)
    # band 绝不退回通用 0.30-0.80（证明 override 真生效）
    assert not (abs(lo - 0.30) < 1e-6 and abs(hi - 0.80) < 1e-6), (lo, hi)


def test_D_dialogue_ratio_0to1_key_still_works():
    """dialogue_ratio（0-1 ratio · 惊悚乐园键）原样读取，不被错误 /100（兼容不破坏既有正确读取）。"""
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
    """chapter_chars（蛊真人键）被 tolerant 读为章字数 band（旧实现只读 chapter_words → 失效）。"""
    sd = {"quantitative": {"chapter_chars": {"mean": 2718.888}}}
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["chapter_words"]["min"], t["chapter_words"]["max"]
    assert abs(lo - (2718.888 - 500)) < 1e-6 and abs(hi - (2718.888 + 500)) < 1e-6, (lo, hi)
    # band 绝不退回通用 1800-5800（证明 override 真生效·band 贴作者真实分布）
    assert not (lo == 1800 and hi == 5800), (lo, hi)


def test_D_chapter_words_key_still_works():
    """chapter_words（惊悚乐园键）原样读取（兼容不破坏既有正确读取）。"""
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
    旧实现只认 paragraph_length_chars，惊悚乐园缺 paragraph_count → 退通用 14-35
    把段均 52 的作者顶成 FAIL（同属矫枉过正）。"""
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
    import chapter_io as cio
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / proj_name
    ch = proj / "原文" / f"{ch_name}.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return None, None
    raw = ch.read_text(encoding="utf-8")
    for sep in cio.CHANGES_SEPARATORS:
        if sep in raw:
            raw = raw.split(sep)[0].rstrip()
            break
    import json
    sd = json.loads(sj.read_text(encoding="utf-8"))
    return raw, sd


def test_D_real_gu_zhenren_dialogue_not_fail_with_profile():
    """蛊真人原文 ch043 带作者档：对话占比不再 FAIL（核心实证 · 旧 pct 键不符时退通用 30-80% 把真实 ~20% 顶成 FAIL）。"""
    raw, sd = _load_body("蛊真人", "第043章")
    if raw is None:
        return
    t = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    t = vs._apply_style_overrides(t, sd)
    results = vs.validate_style(raw, t)
    dlg = _res(results, "对话占比")
    assert dlg.status != "FAIL", (dlg.status, dlg.detail, dlg.target_desc)


def test_D_real_gu_zhenren_dialogue_would_fail_under_generic_band():
    """反证：同一蛊真人原文用通用 band（=旧键名不符退化）对话占比 FAIL —— 证明确有矫枉过正。"""
    raw, sd = _load_body("蛊真人", "第043章")
    if raw is None:
        return
    t_generic = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    results = vs.validate_style(raw, t_generic)
    dlg = _res(results, "对话占比")
    assert dlg.status == "FAIL", (dlg.status, dlg.detail)  # 通用 30-80% 把真实 ~20% 顶 FAIL


def test_D_real_jingsong_dialogue_not_fail_with_profile():
    """惊悚乐园原文 ch043 带作者档：对话占比不 FAIL（0-1 ratio 键仍正确读取·不被回退破坏）。"""
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
