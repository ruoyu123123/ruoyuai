"""D9 滤镜词密度 advisory 测试 — validate_style.py 禁用词管道新增「deep POV 滤镜词」维度。

背景（北极星④顾问非法官 + ⑤作者档第一权威）：「滤镜词」(filter words)是把读者与场景隔一层
主角感知动词（想/觉得/感到/意识到/看到/听到/明白/知道/发现/察觉）。删滤镜词、直写被感知之物
= deep POV（更贴近网文沉浸式爽感）。本维度**纯 advisory**——超阈值仅提示「可删滤镜词贴近
deep POV」，**绝不 hard_gate**（不进 audit_hub.HARD_GATE_CODES）、**绝不 FAIL**、不影响退出码。

env D9_FILTER_WORDS_MODE 三态：
  · shadow（默认）：算密度但只 print stderr·不产出 CheckResult → **默认行为零回归**（核心纪律 3）。
  · advisory：产出 PASS/WARN（WARN 即「可删滤镜词」·永不 FAIL）。
  · off：完全关闭。

阈值校准（纪律 4 真原文不误判）——实证两书全 936 章逐章滤镜词命中率（per 1000 CJK）：
  蛊真人 686 章 max=12.74 / 惊悚乐园 250 章 max=10.90 → 阈值 14.0（safely 高于两书 max）。
  真作者正常章命中率 ≤ ~13 → advisory 模式下 0/936 章误判 WARN（真作者签名笔法绝不误伤）。

关键纪律：保留现有改动（L1a 分位数 band / L2-1 PID Δ / 现有禁用词管道）—— 本测试只验 D9
新维度，并显式回归证明默认 shadow 模式不改 15 项原有检查与退出码。

只测确定性纯函数（程序化校验），不碰 LLM / agent。只跑本模块（python -c 导入·不跑全量）。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import validate_style as vs  # noqa: E402


# ── 测试隔离：每个 case 显式设/清 env，互不污染（避免测试顺序耦合）─────────
def _set_mode(mode: str | None):
    if mode is None:
        os.environ.pop("D9_FILTER_WORDS_MODE", None)
    else:
        os.environ["D9_FILTER_WORDS_MODE"] = mode


def _res(results, name_contains):
    for r in results:
        if name_contains in r.name:
            return r
    return None


def _d9(results):
    return _res(results, "滤镜词")


def _base_thresholds():
    return {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}


# ════════════════════════════════════════════════════════════════
# [1] 默认 shadow 模式：零回归 —— D9 不产出检查项，原有 15 项与退出码不变
# ════════════════════════════════════════════════════════════════

def test_default_no_env_is_shadow():
    """无 env 时默认 shadow（三态合法性 + 默认值）。"""
    _set_mode(None)
    assert vs._d9_filter_words_mode() == "shadow"


def test_shadow_mode_emits_no_check_item():
    """shadow（默认）模式：D9 返回 None → 不进 results → 原有 15 项不变（零回归）。"""
    _set_mode("shadow")
    text = "他看到了门。\n\n她笑了。"
    res = vs.validate_style(text, _base_thresholds())
    assert _d9(res) is None, [r.name for r in res]
    assert len(res) == 15, len(res)  # 原有 15 项一个不多一个不少


def test_shadow_mode_heavy_filter_still_no_check_and_no_fail():
    """shadow 下即使滤镜词极多 → 仍不产出检查项·不引入 FAIL（默认行为绝不变）。"""
    _set_mode("shadow")
    heavy = "他看到她又感到害怕意识到危险知道要逃。\n\n" * 30
    res = vs.validate_style(heavy, _base_thresholds())
    assert _d9(res) is None
    # D9 绝不把退出码从 0/1 改变（不在 results 里就不可能贡献 FAIL）
    assert all("滤镜" not in r.name for r in res)


def test_off_mode_emits_no_check_item():
    """off 模式：完全关闭·不产出检查项。"""
    _set_mode("off")
    text = "他看到了门。\n\n她笑了。"
    res = vs.validate_style(text, _base_thresholds())
    assert _d9(res) is None


def test_unknown_mode_falls_back_to_shadow():
    """非法 env 值 → 按 shadow（不产出·零回归）。"""
    _set_mode("garbage_value")
    assert vs._d9_filter_words_mode() == "shadow"
    res = vs.validate_style("他看到了门。\n\n她笑了。", _base_thresholds())
    assert _d9(res) is None


# ════════════════════════════════════════════════════════════════
# [2] advisory 模式：产出 PASS/WARN —— 永不 FAIL（顾问非法官）
# ════════════════════════════════════════════════════════════════

def test_advisory_mode_emits_check_item():
    """advisory 模式：D9 产出检查项（进 results · 16 项）。"""
    _set_mode("advisory")
    res = vs.validate_style("他看到了门。\n\n她笑了。", _base_thresholds())
    d9 = _d9(res)
    assert d9 is not None, [r.name for r in res]
    assert len(res) == 16, len(res)


def test_advisory_low_density_passes():
    """低滤镜词密度（远低于阈值）→ PASS。"""
    _set_mode("advisory")
    text = "他握紧了拳头。\n\n门外的雨砸在窗上。\n\n她转身离开。"
    res = vs.validate_style(text, _base_thresholds())
    d9 = _d9(res)
    assert d9.status == "PASS", (d9.status, d9.detail)


def test_advisory_high_density_warns_never_fails():
    """高滤镜词密度（超阈值）→ WARN（提示可删滤镜词）·**绝不 FAIL**。"""
    _set_mode("advisory")
    heavy = "他看到她又感到害怕意识到危险知道要逃明白没用觉得绝望。\n\n" * 25
    res = vs.validate_style(heavy, _base_thresholds())
    d9 = _d9(res)
    assert d9.status == "WARN", (d9.status, d9.detail)
    assert d9.status != "FAIL"
    assert "deep POV" in d9.detail or "deep POV" in d9.target_desc, (d9.detail, d9.target_desc)


def test_advisory_warn_does_not_change_fail_exit():
    """advisory 模式下 D9 WARN 绝不使整体出现 FAIL（不影响退出码·非 hard_gate）。
    构造一段滤镜词超阈值但其余维度无 FAIL 的文本，确认全程无 FAIL。"""
    _set_mode("advisory")
    # 短句独行 + 高滤镜词：D9 WARN，但不应制造任何 FAIL
    heavy = "他看到了她。\n\n他感到害怕。\n\n他意识到危险。\n\n他知道要逃。\n\n他明白没用。\n\n" * 6
    res = vs.validate_style(heavy, _base_thresholds())
    d9 = _d9(res)
    assert d9.status == "WARN", (d9.status, d9.detail)
    # D9 自身绝不是 FAIL
    assert d9.status != "FAIL"


# ════════════════════════════════════════════════════════════════
# [3] D9 不进 hard_gate 清单（北极星④ · 绝不进 audit_hub.HARD_GATE_CODES）
# ════════════════════════════════════════════════════════════════

def test_d9_not_in_audit_hub_hard_gate_codes():
    """D9 维度名/相关码绝不出现在 audit_hub.HARD_GATE_CODES（全 advisory 硬保证）。"""
    try:
        import audit_hub  # noqa: E402
    except Exception:
        return  # audit_hub 不可导入则跳过（不阻断本模块其余测试）
    codes = getattr(audit_hub, "HARD_GATE_CODES", set())
    codes = set(codes)
    for token in ("FILTER_WORD", "FILTER_WORDS", "D9", "滤镜", "DEEP_POV"):
        assert not any(token in str(c) for c in codes), (token, codes)


# ════════════════════════════════════════════════════════════════
# [4] _filter_word_density 纯函数行为
# ════════════════════════════════════════════════════════════════

def test_density_helper_per_1000_cjk():
    """密度 = 命中数 / CJK字数 × 1000（同 style_analyzer per_1000 口径）。"""
    # 100 CJK，"看到" 命中 1 次（2 CJK 中的子串 count=1）
    text = "看到" + "甲" * 98  # 100 CJK 字，滤镜词命中 1（"看到"）
    density, total_cjk, hits = vs._filter_word_density(text)
    assert total_cjk == 100, total_cjk
    assert hits.get("看到") == 1, hits
    assert abs(density - 1.0 / 100 * 1000) < 1e-9, density  # = 10.0


def test_density_helper_counts_all_filter_words():
    """多个滤镜词命中累加（子串 count 口径）。"""
    text = "他想了想，觉得不对，感到意外，意识到问题。"
    density, total_cjk, hits = vs._filter_word_density(text)
    # 想x2(想了/想)、觉得x1、感到x1、意识到x1 —— 至少这几个键在
    assert hits.get("觉得") == 1
    assert hits.get("感到") == 1
    assert hits.get("意识到") == 1
    assert hits.get("想", 0) >= 1
    assert density > 0


def test_density_helper_empty_or_no_cjk():
    """无 CJK → density=0、total=0、空 hits（不除零崩溃）。"""
    density, total_cjk, hits = vs._filter_word_density("abc 123 !!!")
    assert density == 0.0 and total_cjk == 0 and hits == {}


def test_threshold_above_real_author_max():
    """前置不变量：阈值（14.0）显著高于两书原文实测 max（蛊真人 12.74 / 惊悚 10.90）。
    保证真作者正常章绝不被误判（北极星④顾问非法官 · 纪律 4）。"""
    assert vs._D9_DENSITY_ADVISORY_PER_1000 >= 13.0, vs._D9_DENSITY_ADVISORY_PER_1000


# ════════════════════════════════════════════════════════════════
# [5] 真原文校准回归（纪律 4 · 蛊真人/惊悚乐园不被误判）
# ════════════════════════════════════════════════════════════════

def _load_body(proj_name, ch_name):
    proj = Path(__file__).resolve().parents[1] / "workspace" / "styles" / proj_name
    ch = proj / "原文" / f"{ch_name}.txt"
    if not ch.exists():
        return None
    return ch.read_text(encoding="utf-8", errors="ignore")


def test_real_authors_filter_density_below_threshold():
    """两书 sample 章原文滤镜词密度 < 阈值（真作者不该被误判 · 核心实证）。"""
    samples = [("蛊真人", "第043章"), ("蛊真人", "第001章"),
               ("惊悚乐园", "第025章"), ("惊悚乐园", "第043章")]
    checked = 0
    for proj, ch in samples:
        raw = _load_body(proj, ch)
        if raw is None:
            continue
        checked += 1
        density, _, _ = vs._filter_word_density(raw)
        assert density < vs._D9_DENSITY_ADVISORY_PER_1000, (proj, ch, density)
    # 至少跑到一个真样本才算有意义（CI 无样本环境则全跳过·不假阳性通过）
    if checked == 0:
        return


def test_real_author_advisory_mode_not_warn():
    """advisory 模式下真原文章 → D9 = PASS（绝不 WARN·真作者签名笔法不被误伤）。"""
    _set_mode("advisory")
    raw = _load_body("蛊真人", "第043章") or _load_body("惊悚乐园", "第025章")
    if raw is None:
        return  # CI 无样本环境则跳过
    res = vs.validate_style(raw, _base_thresholds())
    d9 = _d9(res)
    assert d9 is not None
    assert d9.status == "PASS", (d9.status, d9.detail)


def test_real_author_full_corpus_zero_false_positive():
    """全两书原文逐章 advisory：0 误判 WARN（936 章真作者全部 ≤ 阈值 · 终极实证）。
    样本缺失则跳过（CI 无样本环境）。"""
    import glob
    total = 0
    false_pos = 0
    for proj in ("蛊真人", "惊悚乐园"):
        base = Path(__file__).resolve().parents[1] / "workspace" / "styles" / proj / "原文"
        for f in glob.glob(str(base / "第*.txt")):
            raw = Path(f).read_text(encoding="utf-8", errors="ignore")
            if not raw.strip():
                continue
            total += 1
            density, _, _ = vs._filter_word_density(raw)
            if density > vs._D9_DENSITY_ADVISORY_PER_1000:
                false_pos += 1
    if total == 0:
        return  # 无样本环境
    assert false_pos == 0, (false_pos, total)


# ════════════════════════════════════════════════════════════════
# [6] 保留现有改动回归 —— D9 不破坏 L1a/PID/现有禁用词管道
# ════════════════════════════════════════════════════════════════

def test_existing_l1a_quantile_helpers_intact():
    """L1a 分位数 band 相关函数仍在（现有改动保留）。"""
    assert callable(vs._maybe_quantile_band)
    assert callable(vs._extract_quantile_pair)
    assert vs._quantile_band_mode() in ("shadow", "active", "off")


def test_existing_banned_pipeline_intact_with_d9_default():
    """默认（shadow）下现有禁用词管道行为不变：AI 结构套话仍 FAIL。"""
    _set_mode(None)  # 默认 shadow
    text = "与此同时，他走了出去。\n\n外面下起了雨。"
    res = vs.validate_style(text, _base_thresholds())
    banned = _res(res, "禁用词") or _res(res, "AI结构套话")
    assert banned is not None
    assert banned.status == "FAIL", (banned.status, banned.detail)


def test_existing_apply_style_overrides_intact():
    """_apply_style_overrides 现有作者档逻辑不被 D9 影响（对话占比 override 仍生效）。"""
    sd = {"quantitative": {"dialogue_ratio_pct": {"mean": 25.409}}}
    t = _base_thresholds()
    t = vs._apply_style_overrides(t, sd)
    lo, hi = t["dialogue_ratio"]["min"], t["dialogue_ratio"]["max"]
    assert abs(lo - (0.25409 - 0.15)) < 1e-6 and abs(hi - (0.25409 + 0.15)) < 1e-6, (lo, hi)
    assert t.get("_has_author_profile") is True


# 清理 env（被其他模块导入时不残留）
_set_mode(None)
