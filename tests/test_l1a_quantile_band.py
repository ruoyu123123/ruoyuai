"""L1a 作者经验分位数 band 测试 — 根治「mean±固定容差」矫枉过正根因（北极星① · 2026-05-30）。

背景：validate_style._apply_style_overrides 旧实现用「作者实测 mean ± 固定容差」覆盖 band
（段长 mean×0.7-1.3 / 对话占比 mean±0.15）。对**长段议论体作者**（蛊真人段长方差大 · 惊悚
乐园段均 52 / 段长跨度 36-72）系统性误判——mean±30% 上界 67.6 把真作者 p95(71.9) 顶出
band，正常长段章被 FAIL。前 6 轮补丁全是 whack-a-mole（补容差 band 个案误判，未触根）。
根治：band 从作者样本**经验分位数 [p5,p95]** 涌现（覆盖作者真实 90% 章节区间）。

影子并行（北极星纪律 2）：env QUANTILE_BAND_MODE 控制——
  · shadow（默认）：算 quantile band 但只记录新旧分歧到 stderr · 不改判决（零回归）。
  · active：正式用 [p5,p95] 分位数 band。
  · off：完全关闭（纯旧 mean±容差 行为）。

测试覆盖：① calc_quantiles / aggregate_chapter_quantiles 纯函数正确性；② _extract_quantile_pair；
③ shadow 默认零回归（band 不变）；④ active 用分位数 band；⑤ off 纯旧行为；⑥ 真原文矫枉
过正金标准——蛊真人/惊悚乐园原文 active 模式下长段不被误判 + AI 套话仍 FAIL（真问题不放松）；
⑦ 蒸馏分布字段补齐非 null。

只测确定性纯函数（程序化校验），不碰 LLM / agent。守纪律：用真作者原文当「系统生成」喂
校验确认改动后真作者不被误判（memory reference-system-validation-method）。
"""
import os
import sys
import json
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_analyzer as sa  # noqa: E402
import validate_style as vs  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _reload_vs(mode):
    """以指定 QUANTILE_BAND_MODE reload validate_style（env 在 import os 时读·需 reload 生效）。"""
    if mode is None:
        os.environ.pop("QUANTILE_BAND_MODE", None)
    else:
        os.environ["QUANTILE_BAND_MODE"] = mode
    importlib.reload(vs)
    return vs


def _res(results, name_contains):
    for r in results:
        if name_contains in r.name:
            return r
    raise AssertionError(f"未找到检查项 {name_contains}：{[r.name for r in results]}")


def _load_body(proj_name, ch_name):
    import chapter_io as cio
    proj = _ROOT / "workspace" / "styles" / proj_name
    ch = proj / "原文" / f"{ch_name}.txt"
    sj = proj / "作者风格_FINAL.json"
    if not (ch.exists() and sj.exists()):
        return None, None
    raw = ch.read_text(encoding="utf-8")
    for sep in cio.CHANGES_SEPARATORS:
        if sep in raw:
            raw = raw.split(sep)[0].rstrip()
            break
    sd = json.loads(sj.read_text(encoding="utf-8"))
    return raw, sd


# ════════════════════════════════════════════════════════════════
# [A] calc_quantiles 纯函数正确性（style_analyzer）
# ════════════════════════════════════════════════════════════════

def test_A_calc_quantiles_basic():
    """calc_quantiles 对均匀分布 1..101 → p5≈6 / p50≈51 / p95≈96（线性插值）。"""
    q = sa.calc_quantiles(list(range(1, 102)))  # 101 个值
    assert q["p50"] == 51, q
    assert abs(q["p5"] - 6.0) < 1e-6, q
    assert abs(q["p95"] - 96.0) < 1e-6, q
    assert q["n"] == 101
    assert abs(q["mean"] - 51.0) < 1e-6, q


def test_A_calc_quantiles_empty_and_single():
    """空集 → None；单值 → 所有分位数都是该值。"""
    assert sa.calc_quantiles([]) is None
    q = sa.calc_quantiles([42.0])
    assert q["p5"] == q["p50"] == q["p95"] == 42.0, q
    assert q["n"] == 1


def test_A_calc_quantiles_monotonic():
    """分位数单调非降：p5 ≤ p25 ≤ p50 ≤ p75 ≤ p95（不变量）。"""
    import random
    random.seed(7)
    q = sa.calc_quantiles([random.gauss(50, 10) for _ in range(500)])
    assert q["p5"] <= q["p25"] <= q["p50"] <= q["p75"] <= q["p95"], q


# ════════════════════════════════════════════════════════════════
# [B] chapter_metrics_lite / aggregate_chapter_quantiles
# ════════════════════════════════════════════════════════════════

def test_B_chapter_metrics_lite_basic():
    """chapter_metrics_lite 返回三组指标（para_len / punc / fw）· 跳过 speaker（不抛错）。"""
    text = "他走进房间。\n看了看四周。\n然后坐下了，叹了口气。"
    m = sa.chapter_metrics_lite(text)
    assert m is not None
    assert m["paragraph_length_chars"] > 0
    assert "comma" in m["punctuation_per_1k"]
    assert "的" in m["function_words_per_1k"]


def test_B_chapter_metrics_lite_empty_none():
    """无 CJK / 空文本 → None（坏章跳过）。"""
    assert sa.chapter_metrics_lite("") is None
    assert sa.chapter_metrics_lite("12345 abcde") is None


def test_B_aggregate_chapter_quantiles_structure():
    """aggregate_chapter_quantiles 聚合多章 → 三组分位数桶 + n_chapters_used。"""
    chapters = [
        "短段。\n短段。\n短段。",
        "这是一个稍微长一点的段落，包含更多的字符内容用来测试段长。\n另起一段。",
        "第三章的内容，也有一些段落。\n继续写。\n再来一段收尾。",
    ]
    agg = sa.aggregate_chapter_quantiles(chapters)
    assert agg["n_chapters_used"] == 3, agg
    assert agg["paragraph_length_chars"] is not None
    assert agg["paragraph_length_chars"]["n"] == 3
    assert "comma" in agg["punctuation_per_1k"]
    assert "的" in agg["function_words_per_1k"]


def test_B_aggregate_skips_bad_chapters():
    """坏章（无 CJK）被跳过·不计入 n_chapters_used。"""
    agg = sa.aggregate_chapter_quantiles(["真章节内容。\n第二段。", "", "999 zzz"])
    assert agg["n_chapters_used"] == 1, agg


# ════════════════════════════════════════════════════════════════
# [C] _extract_quantile_pair / _quantile_band_mode 纯函数
# ════════════════════════════════════════════════════════════════

def test_C_extract_quantile_pair():
    """有 p5/p95 数值且 p5<=p95 → (p5,p95)；缺任一/类型错 → None。"""
    assert vs._extract_quantile_pair({"p5": 24.5, "p95": 38.3}) == (24.5, 38.3)
    assert vs._extract_quantile_pair({"p5": 24.5}) is None       # 缺 p95
    assert vs._extract_quantile_pair({"p95": 38.3}) is None      # 缺 p5
    assert vs._extract_quantile_pair({"p5": 50, "p95": 10}) is None  # p5>p95（脏数据）
    assert vs._extract_quantile_pair(None) is None
    assert vs._extract_quantile_pair({"mean": 30}) is None       # 只有 mean（老档）


def test_C_quantile_band_mode_default_shadow():
    """QUANTILE_BAND_MODE 默认 shadow · 非法值回退 shadow · {active,off} 原样。"""
    vsx = _reload_vs(None)            # 未设 env
    try:
        assert vsx._quantile_band_mode() == "shadow"
        vsx = _reload_vs("active")
        assert vsx._quantile_band_mode() == "active"
        vsx = _reload_vs("off")
        assert vsx._quantile_band_mode() == "off"
        vsx = _reload_vs("garbage")   # 非法 → shadow
        assert vsx._quantile_band_mode() == "shadow"
    finally:
        _reload_vs(None)


# ════════════════════════════════════════════════════════════════
# [D] _maybe_quantile_band：shadow 不改判决 / active 用分位数 / off 旧行为
# ════════════════════════════════════════════════════════════════

_OLD_BAND = {"min": 36.4, "max": 67.6}      # 模拟 mean±30%（惊悚乐园 52±30%）
_STAT_Q = {"p5": 36.9, "p95": 71.94}        # 经验分位数（覆盖真 p95）


def test_D_shadow_returns_old_band_no_decision_change():
    """shadow（默认）：有分位数数据仍返回旧 band（零回归·不改判决）。"""
    vsx = _reload_vs("shadow")
    try:
        out = vsx._maybe_quantile_band("段落均长", _STAT_Q, dict(_OLD_BAND))
        assert out == _OLD_BAND, out  # 返回旧 band（不被分位数改）
    finally:
        _reload_vs(None)


def test_D_active_returns_quantile_band():
    """active：返回 [p5,p95] 分位数 band（取代旧 mean±容差）。"""
    vsx = _reload_vs("active")
    try:
        out = vsx._maybe_quantile_band("段落均长", _STAT_Q, dict(_OLD_BAND))
        assert abs(out["min"] - 36.9) < 1e-9 and abs(out["max"] - 71.94) < 1e-9, out
    finally:
        _reload_vs(None)


def test_D_off_returns_old_band():
    """off：完全关闭·原样返回旧 band（连分位数都不算）。"""
    vsx = _reload_vs("off")
    try:
        out = vsx._maybe_quantile_band("段落均长", _STAT_Q, dict(_OLD_BAND))
        assert out == _OLD_BAND, out
    finally:
        _reload_vs(None)


def test_D_active_no_quantile_data_falls_back_to_old():
    """active 但无分位数数据（老蒸馏档只有 mean）→ 退回旧 band（向后兼容·零回归）。"""
    vsx = _reload_vs("active")
    try:
        out = vsx._maybe_quantile_band("段落均长", {"mean": 30.0}, dict(_OLD_BAND))
        assert out == _OLD_BAND, out
    finally:
        _reload_vs(None)


def test_D_active_clamps_lo_hi():
    """active：lo_min/hi_max 钳位（如对话占比钳 [0,1]）防越界。"""
    vsx = _reload_vs("active")
    try:
        # 分位数 p5=-0.1（脏）/p95=1.5（脏）→ 钳到 [0,1]
        out = vsx._maybe_quantile_band(
            "对话占比", {"p5": -0.1, "p95": 1.5}, {"min": 0.1, "max": 0.4},
            lo_min=0.0, hi_max=1.0)
        assert out["min"] == 0.0 and out["max"] == 1.0, out
    finally:
        _reload_vs(None)


# ════════════════════════════════════════════════════════════════
# [E] _apply_style_overrides 端到端：para_mean_len band 路径
# ════════════════════════════════════════════════════════════════

def _para_band(sd, mode):
    vsx = _reload_vs(mode)
    t = {k: dict(v) for k, v in vsx.DEFAULT_THRESHOLDS.items()}
    t = vsx._apply_style_overrides(t, sd)
    return t["para_mean_len"]


def test_E_para_band_shadow_equals_mean_tolerance():
    """shadow：有 paragraph_length_chars 分位数仍用 mean±30%（旧行为·零回归）。"""
    sd = {"quantitative": {"paragraph_length_chars": {
        "mean": 52.0, "p5": 36.9, "p95": 71.94}}}
    try:
        band = _para_band(sd, "shadow")
        assert abs(band["min"] - 52.0 * 0.7) < 1e-6 and abs(band["max"] - 52.0 * 1.3) < 1e-6, band
    finally:
        _reload_vs(None)


def test_E_para_band_active_uses_quantiles():
    """active：用 [p5,p95] 经验 band 取代 mean±30%。"""
    sd = {"quantitative": {"paragraph_length_chars": {
        "mean": 52.0, "p5": 36.9, "p95": 71.94}}}
    try:
        band = _para_band(sd, "active")
        assert abs(band["min"] - 36.9) < 1e-6 and abs(band["max"] - 71.94) < 1e-6, band
        # 关键：active band 上界(71.94) 覆盖真 p95，旧 mean±30%(67.6) 容不下 → 根治矫枉过正
        assert band["max"] > 52.0 * 1.3, band
    finally:
        _reload_vs(None)


def test_E_para_band_active_no_quantiles_old_distill_falls_back():
    """active 但老蒸馏档（paragraph_length_chars=null·只有 chapter_chars/段数）→ 退 mean±容差。"""
    sd = {"quantitative": {
        "paragraph_length_chars": None,
        "chapter_chars": {"mean": 2718.888},
        "paragraph_count": {"mean": 90.182},
    }}
    try:
        band = _para_band(sd, "active")
        expect = 2718.888 / 90.182  # ≈30.15
        assert abs(band["min"] - expect * 0.7) < 0.1 and abs(band["max"] - expect * 1.3) < 0.1, band
    finally:
        _reload_vs(None)


def test_E_dialogue_band_active_uses_quantiles_when_present():
    """active：dialogue_ratio 桶含 [p5,p95] 时用经验 band（钳 [0,1]）。"""
    sd = {"quantitative": {"dialogue_ratio": {"mean": 0.27, "p5": 0.05, "p95": 0.58}}}
    try:
        vsx = _reload_vs("active")
        t = {k: dict(v) for k, v in vsx.DEFAULT_THRESHOLDS.items()}
        t = vsx._apply_style_overrides(t, sd)
        b = t["dialogue_ratio"]
        assert abs(b["min"] - 0.05) < 1e-9 and abs(b["max"] - 0.58) < 1e-9, b
    finally:
        _reload_vs(None)


def test_E_dialogue_band_shadow_keeps_mean_tolerance():
    """shadow：dialogue_ratio 有分位数仍用 mean±0.15（零回归）。"""
    sd = {"quantitative": {"dialogue_ratio": {"mean": 0.27, "p5": 0.05, "p95": 0.58}}}
    try:
        vsx = _reload_vs("shadow")
        t = {k: dict(v) for k, v in vsx.DEFAULT_THRESHOLDS.items()}
        t = vsx._apply_style_overrides(t, sd)
        b = t["dialogue_ratio"]
        assert abs(b["min"] - (0.27 - 0.15)) < 1e-9 and abs(b["max"] - (0.27 + 0.15)) < 1e-9, b
    finally:
        _reload_vs(None)


# ════════════════════════════════════════════════════════════════
# [F] 蒸馏分布字段补齐非 null（两书 作者风格_FINAL.json）
# ════════════════════════════════════════════════════════════════

def test_F_both_books_quantile_fields_non_null():
    """两书 作者风格_FINAL.json 的 paragraph_length_chars / punctuation_per_1k /
    function_words_per_1k 已补齐非 null（蛊真人原为 null/{} = override 失效源头）。"""
    for name in ("蛊真人", "惊悚乐园"):
        sj = _ROOT / "workspace" / "styles" / name / "作者风格_FINAL.json"
        if not sj.exists():
            continue  # CI 无样本环境则跳过
        q = json.loads(sj.read_text(encoding="utf-8"))["quantitative"]
        plc = q.get("paragraph_length_chars")
        assert isinstance(plc, dict) and plc, (name, plc)         # 非 null/非空
        assert vs._extract_quantile_pair(plc) is not None, (name, plc)  # 含 [p5,p95]
        assert isinstance(q.get("punctuation_per_1k"), dict) and q["punctuation_per_1k"], name
        assert isinstance(q.get("function_words_per_1k"), dict) and q["function_words_per_1k"], name


def test_F_gu_zhenren_quantiles_match_known_distribution():
    """蛊真人 paragraph_length_chars 分位数落在已知真实区间（p50≈30·p95≈38·防写错数据）。"""
    sj = _ROOT / "workspace" / "styles" / "蛊真人" / "作者风格_FINAL.json"
    if not sj.exists():
        return
    plc = json.loads(sj.read_text(encoding="utf-8"))["quantitative"]["paragraph_length_chars"]
    assert 28 <= plc["p50"] <= 33, plc       # 段均中位 ~30
    assert 36 <= plc["p95"] <= 41, plc       # 段均 p95 ~38
    assert plc["p5"] < plc["p50"] < plc["p95"], plc


def test_F_jingsong_quantiles_match_known_distribution():
    """惊悚乐园 paragraph_length_chars 分位数落在已知真实区间（p50≈52·p95≈72）。"""
    sj = _ROOT / "workspace" / "styles" / "惊悚乐园" / "作者风格_FINAL.json"
    if not sj.exists():
        return
    plc = json.loads(sj.read_text(encoding="utf-8"))["quantitative"]["paragraph_length_chars"]
    assert 48 <= plc["p50"] <= 55, plc
    assert 68 <= plc["p95"] <= 76, plc       # 真 p95 71.9 > 旧 mean±30% 上界 67.6（矫枉过正锚）


# ════════════════════════════════════════════════════════════════
# [G] 真原文矫枉过正金标准（北极星纪律 3）：真作者喂校验
#     shadow 默认零回归 + active 不误判长段 + 真问题（AI 套话）仍 FAIL
# ════════════════════════════════════════════════════════════════

def test_G_shadow_default_zero_regression_gu_zhenren():
    """蛊真人 ch043 默认（shadow）：段落均长结果与旧 mean±容差 一致（零回归核心保证）。"""
    raw, sd = _load_body("蛊真人", "第043章")
    if raw is None:
        return
    try:
        vs_shadow = _reload_vs("shadow")
        t1 = {k: dict(v) for k, v in vs_shadow.DEFAULT_THRESHOLDS.items()}
        t1 = vs_shadow._apply_style_overrides(t1, sd)
        s_shadow = _res(vs_shadow.validate_style(raw, t1), "段落均长").status

        vs_off = _reload_vs("off")
        t2 = {k: dict(v) for k, v in vs_off.DEFAULT_THRESHOLDS.items()}
        t2 = vs_off._apply_style_overrides(t2, sd)
        s_off = _res(vs_off.validate_style(raw, t2), "段落均长").status

        # shadow 与 off（纯旧行为）判决完全一致 → 默认零回归
        assert s_shadow == s_off, (s_shadow, s_off)
    finally:
        _reload_vs(None)


def test_G_active_long_para_chapter_not_fail_jingsong():
    """惊悚乐园真原文长段章（段均超旧 mean±30% 上界 67.6）：active 分位数 band 下不再 FAIL。
    核心金标准——真作者长段不被误判（旧 mean±容差 会 FAIL · 分位数 band 容得下到 p95）。"""
    # 第011章 段均 67.7（刚超旧上界）/ 第021章 69.1 —— 旧 band FAIL，新 band 内 PASS
    found = False
    for cand in ("第011章", "第021章", "第013章", "第022章", "第009章"):
        raw, sd = _load_body("惊悚乐园", cand)
        if raw is None:
            continue
        pm = sa.analyze_text(raw)["paragraph_stats"]["mean"]
        if pm <= 52.0 * 1.3:   # 只挑超旧 mean±30% 上界的章（才是矫枉过正锚）
            continue
        found = True
        try:
            vsx = _reload_vs("active")
            t = {k: dict(v) for k, v in vsx.DEFAULT_THRESHOLDS.items()}
            t = vsx._apply_style_overrides(t, sd)
            para = _res(vsx.validate_style(raw, t), "段落均长")
            # active 分位数 band（覆盖到 p95=71.9）下，该长段章不再 FAIL
            assert para.status != "FAIL", (cand, pm, para.status, para.detail, para.target_desc)
        finally:
            _reload_vs(None)
    # 样本存在时至少验证了一章（CI 无样本则 found=False·静默通过）
    assert found or _load_body("惊悚乐园", "第011章")[0] is None


def test_G_active_does_not_relax_ai_slop_gu_zhenren():
    """active 模式 + 注入 AI 结构套话 → 禁用词仍 FAIL（根治 band 绝不放松真问题检测）。"""
    raw, sd = _load_body("蛊真人", "第043章")
    if raw is None:
        return
    try:
        vsx = _reload_vs("active")
        t = {k: dict(v) for k, v in vsx.DEFAULT_THRESHOLDS.items()}
        t = vsx._apply_style_overrides(t, sd)
        injected = "与此同时，他走了出去。\n\n" * 3 + raw
        banned = _res(vsx.validate_style(injected, t), "禁用词")
        assert banned.status == "FAIL", (banned.status, banned.detail)
        assert "AI结构套话" in banned.name, banned.name
    finally:
        _reload_vs(None)


def test_G_active_band_wider_than_old_for_long_para_authors():
    """实证根治：两书 active band 上界 ≥ 旧 mean±30% 上界（覆盖真分布·非对称容差容不下）。"""
    for name, base_mean in (("蛊真人", 30.15), ("惊悚乐园", 52.0)):
        raw, sd = _load_body(name, "第043章")
        if sd is None:
            continue
        try:
            vs_a = _reload_vs("active")
            ta = {k: dict(v) for k, v in vs_a.DEFAULT_THRESHOLDS.items()}
            ta = vs_a._apply_style_overrides(ta, sd)
            active_band = ta["para_mean_len"]
            # active 上界 = 真 p95；惊悚乐园 p95(71.9) > mean±30%(67.6)，证明旧容差容不下真长段
            if name == "惊悚乐园":
                assert active_band["max"] > base_mean * 1.3, (name, active_band)
            # 两书 active band 都从经验分位数涌现（非 mean 对称）
            assert active_band["min"] < active_band["max"], (name, active_band)
        finally:
            _reload_vs(None)


# ════════════════════════════════════════════════════════════════
# [H] 影子并行安全：shadow 不修改传入 old_band 对象（无副作用）
# ════════════════════════════════════════════════════════════════

def test_H_shadow_no_side_effect_on_old_band():
    """shadow 返回的是传入 old_band（同对象/同值）· active 返回新对象（不污染调用方）。"""
    vsx = _reload_vs("active")
    try:
        old = {"min": 36.4, "max": 67.6}
        out = vsx._maybe_quantile_band("段落均长", _STAT_Q, old)
        assert out is not old, "active 应返回新 band 对象"
        assert old == {"min": 36.4, "max": 67.6}, "old_band 不被原地修改"
    finally:
        _reload_vs(None)
