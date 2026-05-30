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
