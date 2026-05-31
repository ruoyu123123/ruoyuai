"""L3d draft-level critic-refine + knockout 测试（PerFine 式 · 北极星①⑤⑥ · 2026-05-31）。

根因（L3b 自认 · memory reference-system-validation-method）：复刻是「得分→盲改 skill→再测」
乏力循环——没有 draft 改稿、没有保最优。一旦草稿出来就定稿，差距只能靠下一轮蒸 skill 修。

升级（PerFine · arxiv 2510.24469 · GEval +7-13% · 3-5 轮稳 · 全在 distill_replicate.py 内自包含）：
  复刻出稿后——critic LLM（同 gen-model profile）按 tone/词汇/句式/topicality 出**结构化 feedback
  直接改当前草稿**（draft-level · 非改 skill）→ SFS 分当裁判 → **knockout 跨轮保更高分草稿**（3-5 轮）。

纪律：
  · critic feedback 是 advisory（落 meta 留痕 · 不黑箱）；SFS 当裁判透明（确定性 · 不进 hard_gate）；
  · critic prompt 引用作者**数值契约表**条目（受控量化坐标 · 严禁感性词）；
  · 默认 active（用户：默认关闭写他干什么）· env DRAFT_REFINE_MODE=off 可关；
  · 测试只验**确定性的 mode 解析 / prompt 构造 / knockout 纯函数 / refine 编排**——
    gen-model（critic+refine 调用）+ SFS 裁判全 **mock** 注入（不实跑 · 需 API + numpy/scipy）。

测试覆盖：① mode 解析（默认 active / off / 归一 / 非法回退 active）；② rounds 解析钳 [1,5]；
  ③ critic prompt（含 4 维 + 受控坐标 + 严禁感性词 + 契约表）；④ refine prompt（只改风格不改骨架）；
  ⑤ 契约表抽取（哨兵块 / 标题 / 回退头部）；⑥ knockout 纯函数 4 组合（保最优）；
  ⑦ draft_refine_loop 编排（mock critic+refine+SFS · 升分采纳 / 退化淘汰保最优 / 裁判不可用降级）；
  ⑧ 真 skill_v7 校准（契约表受控坐标有真依据）。
"""
import os
import sys
import importlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import distill_replicate as dr  # noqa: E402


_CRITIC_DIMS = ["tone", "vocabulary", "syntax", "topicality"]
_CONTROLLED_COORDS = ["句长", "段长", "单句独行", "标点", "虚词"]
_SENSORY_WORDS = ["冷峻", "华丽", "大气", "有张力"]


def _reload_dr(mode_env=None, rounds_env=None):
    """以指定 DRAFT_REFINE_MODE / DRAFT_REFINE_ROUNDS reload（env 在函数内读 · reload 求稳）。"""
    for k, v in (("DRAFT_REFINE_MODE", mode_env), ("DRAFT_REFINE_ROUNDS", rounds_env)):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    importlib.reload(dr)
    return dr


# ════════════════════════════════════════════════════════════════
# [A] mode 解析 _draft_refine_mode（默认 active · 用户：默认关闭写他干什么）
# ════════════════════════════════════════════════════════════════

def test_A_mode_default_active():
    """DRAFT_REFINE_MODE 未设 → 默认 active（默认全开真生效）。"""
    dx = _reload_dr(None)
    try:
        assert dx._draft_refine_mode() == "active"
    finally:
        _reload_dr(None)


def test_A_mode_explicit_off():
    """显式 off / OFF / Off → off（唯一关闭手段 · A/B 对照）。"""
    for v in ("off", "OFF", "Off"):
        dx = _reload_dr(v)
        try:
            assert dx._draft_refine_mode() == "off", v
        finally:
            _reload_dr(None)


def test_A_mode_on_normalized_active():
    """on / 1 / true / refine / active → active（归一）。"""
    for v in ("on", "1", "true", "refine", "active", "ACTIVE"):
        dx = _reload_dr(v)
        try:
            assert dx._draft_refine_mode() == "active", v
        finally:
            _reload_dr(None)


def test_A_mode_garbage_falls_back_active():
    """空 / 非法值 → 回退默认 active（只有显式 off 才关）。"""
    for v in ("garbage", "", "0", "false", "legacy"):
        dx = _reload_dr(v)
        try:
            assert dx._draft_refine_mode() == "active", v
        finally:
            _reload_dr(None)


# ════════════════════════════════════════════════════════════════
# [A2] rounds 解析 _draft_refine_rounds（PerFine 3-5 轮稳 · 默认 3 · 钳 [1,5]）
# ════════════════════════════════════════════════════════════════

def test_A2_rounds_default_3():
    dx = _reload_dr(None, None)
    try:
        assert dx._draft_refine_rounds() == 3
    finally:
        _reload_dr(None, None)


def test_A2_rounds_clamped_to_range():
    """超界钳到 [1,5]：0→1，99→5，负数→1。"""
    cases = {"1": 1, "4": 4, "5": 5, "0": 1, "99": 5, "-3": 1}
    for raw, want in cases.items():
        dx = _reload_dr(None, raw)
        try:
            assert dx._draft_refine_rounds() == want, (raw, want)
        finally:
            _reload_dr(None, None)


def test_A2_rounds_garbage_default_3():
    for raw in ("abc", "", "3.5"):
        dx = _reload_dr(None, raw)
        try:
            assert dx._draft_refine_rounds() == 3, raw
        finally:
            _reload_dr(None, None)


# ════════════════════════════════════════════════════════════════
# [B] critic prompt build_critic_prompt（4 维 + 受控坐标 + 严禁感性词 + 契约表）
# ════════════════════════════════════════════════════════════════

def test_B_critic_dimensions_constant():
    """critic 评的四类维度 = PerFine rubric（tone/vocabulary/syntax/topicality）。"""
    assert dr.DRAFT_CRITIC_DIMENSIONS == _CRITIC_DIMS


def test_B_critic_prompt_names_all_four_dims():
    """critic prompt 必须逐类点名 4 维度。"""
    p = dr.build_critic_prompt("草稿正文。他停下脚步。", "## 数值契约表\n句长均值 18 字", "原文片段")
    for d in _CRITIC_DIMS:
        assert d in p, d


def test_B_critic_prompt_uses_controlled_coords():
    """critic prompt 引用受控量化坐标（句长/段长/单句独行/标点/虚词）当 rubric。"""
    p = dr.build_critic_prompt("草稿", "契约表内容", "")
    for c in _CONTROLLED_COORDS:
        assert c in p, c


def test_B_critic_prompt_forbids_sensory_words():
    """北极星：感性词只能作为「严禁」出现，不能要求 critic 产出（感性词改意泄漏内容）。"""
    p = dr.build_critic_prompt("草稿", "契约表", "")
    assert "严禁" in p
    for w in _SENSORY_WORDS:
        if w in p:
            assert "严禁" in p, w


def test_B_critic_prompt_injects_contract_excerpt():
    """contract_excerpt 注入 critic prompt（作者数值契约表 = 第一权威 rubric）。"""
    p = dr.build_critic_prompt("草稿", "句长均值 22 字 · 单句独行占比 45%", "")
    assert "句长均值 22 字" in p
    assert "数值契约表" in p


def test_B_critic_prompt_says_not_rewrite():
    """critic 只出结构化清单、不重写正文（PerFine：critic 与 refine 分工）。"""
    p = dr.build_critic_prompt("草稿", "契约表", "")
    assert "不重写" in p or "只出" in p


# ════════════════════════════════════════════════════════════════
# [C] refine prompt build_refine_prompt（只改风格不改骨架 + 落实清单 + 对齐契约表）
# ════════════════════════════════════════════════════════════════

def test_C_refine_prompt_only_style_not_skeleton():
    """refine 纪律：只改风格、不改故事骨架（防写飞 · 退化由 knockout 兜底）。"""
    p = dr.build_refine_prompt("草稿正文", "[syntax]\n- 句子太短", "契约表")
    assert "不改故事骨架" in p or "只改风格" in p


def test_C_refine_prompt_includes_feedback_and_draft():
    """refine prompt 同时含 critic feedback + 待改写草稿（据清单逐条改）。"""
    p = dr.build_refine_prompt("这是当前草稿文字。", "[tone]\n- 语气偏冷", "契约表")
    assert "这是当前草稿文字。" in p
    assert "语气偏冷" in p


def test_C_refine_prompt_aligns_contract():
    """refine prompt 强调对齐数值契约表（句长/段长/单句独行/虚词）。"""
    p = dr.build_refine_prompt("草稿", "feedback", "契约表内容")
    assert "数值契约表" in p
    assert "直接输出" in p  # 直接出改写正文（无引言/解释）


# ════════════════════════════════════════════════════════════════
# [D] 契约表抽取 _extract_contract_excerpt（哨兵块 / 标题 / 回退头部）
# ════════════════════════════════════════════════════════════════

def test_D_extract_prefers_sentinel_block():
    """优先抓 L3c 哨兵块（L3C_CONTRACT_BEGIN..END）— 复用注入块不另算。"""
    skill = ("前言无关内容\n"
             "<!-- L3C_CONTRACT_BEGIN -->\n句长均值 19 字 | 单句独行 45%\n<!-- L3C_CONTRACT_END -->\n"
             "后面其他章节")
    ex = dr._extract_contract_excerpt(skill)
    assert "句长均值 19 字" in ex
    assert "后面其他章节" not in ex  # 只取哨兵块内


def test_D_extract_falls_back_to_title():
    """无哨兵 → 抓「数值契约表」标题后片段。"""
    skill = "开头\n## 数值契约表\n句长均值 20 字\n更多内容"
    ex = dr._extract_contract_excerpt(skill)
    assert "数值契约表" in ex
    assert "句长均值 20 字" in ex


def test_D_extract_falls_back_to_head():
    """既无哨兵也无标题 → 回退 skill 头部（critic 仍看得见量化基线）。"""
    skill = "纯文本 skill 无契约表 " + "x" * 5000
    ex = dr._extract_contract_excerpt(skill, max_chars=1400)
    assert len(ex) <= 1400
    assert ex == skill[:1400]


# ════════════════════════════════════════════════════════════════
# [E] knockout 纯函数 _knockout_accept（保最优 · 4 组合钉死）
# ════════════════════════════════════════════════════════════════

def test_E_knockout_both_scored_accept_higher():
    """两者都有分 → 候选 ≥ best 才采纳（严格保最优）。"""
    assert dr._knockout_accept(80.0, 85.0) is True   # 升分采纳
    assert dr._knockout_accept(80.0, 80.0) is True   # 持平采纳（候选是新改稿）
    assert dr._knockout_accept(80.0, 75.0) is False  # 退化淘汰 · 保 best


def test_E_knockout_candidate_unscored_best_scored_reject():
    """候选无分、best 有分 → 不采纳（不拿没裁判背书的候选换掉有分 best）。"""
    assert dr._knockout_accept(80.0, None) is False


def test_E_knockout_best_unscored_candidate_scored_accept():
    """best 无分（首稿裁判失败）、候选有分 → 采纳（候选首次拿到裁判分）。"""
    assert dr._knockout_accept(None, 70.0) is True


def test_E_knockout_both_unscored_accept_degraded():
    """两者都无分（裁判全程不可用）→ 采纳候选（降级：至少吃 refine 改稿）。"""
    assert dr._knockout_accept(None, None) is True


# ════════════════════════════════════════════════════════════════
# [F] draft_refine_loop 编排（mock critic+refine+SFS · 不实跑 gen-model/SFS）
# ════════════════════════════════════════════════════════════════

def _make_call_fn(refine_outputs):
    """mock call_fn：critic 调用回固定 feedback；refine 调用按序回 refine_outputs。

    通过 tag 前缀区分 critic / refine（与 draft_refine_loop 内 tag 约定一致）。
    """
    refine_iter = iter(refine_outputs)

    def call_fn(system, user, tag=""):
        if tag.startswith("critic"):
            return "[tone]\n- 语气偏冷\n[vocabulary]\n- 已对齐\n[syntax]\n- 句子太短\n[topicality]\n- 已对齐"
        if tag.startswith("refine"):
            return next(refine_iter)
        return ""
    return call_fn


def test_F_loop_accepts_improving_candidates():
    """SFS 单调升分 → 每轮采纳 · 最终保留最后（最高分）稿 · trace 记每轮决策。"""
    scores = {"初稿草稿": 70.0, "改稿一": 80.0, "改稿二": 88.0}
    call_fn = _make_call_fn(["改稿一", "改稿二"])

    def score_fn(refs, draft):
        return scores[draft]

    best, trace = dr.draft_refine_loop(
        loader=None, system_prompt="SYS", initial_draft="初稿草稿",
        style_skill_md="## 数值契约表\n句长均值 18 字", ref_texts=["原文"],
        rounds=2, score_fn=score_fn, call_fn=call_fn)
    assert best == "改稿二"
    assert trace["rounds_run"] == 2
    assert trace["initial_score"] == 70.0
    assert trace["final_best_score"] == 88.0
    assert all(rt["accepted"] for rt in trace["rounds"])
    # critic feedback 留痕（不黑箱）
    assert "语气偏冷" in trace["rounds"][0]["critic_feedback"]


def test_F_loop_knockout_keeps_best_on_regression():
    """候选退化（SFS 降分）→ knockout 丢弃 · 保留前一轮最优稿。"""
    scores = {"初稿": 85.0, "退化稿": 70.0, "好稿": 90.0}
    call_fn = _make_call_fn(["退化稿", "好稿"])

    def score_fn(refs, draft):
        return scores[draft]

    best, trace = dr.draft_refine_loop(
        loader=None, system_prompt="SYS", initial_draft="初稿",
        style_skill_md="契约表", ref_texts=["原文"],
        rounds=2, score_fn=score_fn, call_fn=call_fn)
    # 第 1 轮退化稿被淘汰（保初稿 85）；第 2 轮好稿 90 采纳
    assert best == "好稿"
    assert trace["rounds"][0]["accepted"] is False  # 退化淘汰
    assert trace["rounds"][0]["best_score_after"] == 85.0  # best 不被退化稿污染
    assert trace["rounds"][1]["accepted"] is True
    assert trace["final_best_score"] == 90.0


def test_F_loop_all_regressions_keeps_initial():
    """连续退化 → 全淘汰 · 最终保留初稿（保最优纪律 · refine 全废也不丢稿）。"""
    scores = {"初稿好": 92.0, "差稿1": 60.0, "差稿2": 55.0, "差稿3": 50.0}
    call_fn = _make_call_fn(["差稿1", "差稿2", "差稿3"])

    def score_fn(refs, draft):
        return scores[draft]

    best, trace = dr.draft_refine_loop(
        loader=None, system_prompt="SYS", initial_draft="初稿好",
        style_skill_md="契约表", ref_texts=["原文"],
        rounds=3, score_fn=score_fn, call_fn=call_fn)
    assert best == "初稿好"
    assert trace["final_best_score"] == 92.0
    assert all(rt["accepted"] is False for rt in trace["rounds"])


def test_F_loop_sfs_judge_unavailable_degrades():
    """SFS 裁判全程返回 None（无 numpy/scipy / 无 ref）→ 降级吃最后 refine 改稿（不崩）。"""
    call_fn = _make_call_fn(["改稿一", "改稿二"])

    def score_fn(refs, draft):
        return None  # 裁判不可用

    best, trace = dr.draft_refine_loop(
        loader=None, system_prompt="SYS", initial_draft="初稿",
        style_skill_md="契约表", ref_texts=[],
        rounds=2, score_fn=score_fn, call_fn=call_fn)
    # 裁判不可用 → knockout 降级采纳每轮候选 → 最终是最后一轮改稿
    assert best == "改稿二"
    assert trace["sfs_judge_available"] is False
    assert trace["final_best_score"] is None


def test_F_loop_critic_exhausted_breaks_gracefully():
    """critic gen-model 全 profile 失败 → 提前结束循环 · 保当前最优稿（不崩）。"""
    def call_fn(system, user, tag=""):
        if tag.startswith("critic"):
            raise dr.GenModelExhaustedError([("p1", "502")])
        return "不应到达的 refine"

    def score_fn(refs, draft):
        return 80.0 if draft == "初稿" else 99.0

    best, trace = dr.draft_refine_loop(
        loader=None, system_prompt="SYS", initial_draft="初稿",
        style_skill_md="契约表", ref_texts=["原文"],
        rounds=3, score_fn=score_fn, call_fn=call_fn)
    assert best == "初稿"  # critic 失败 → 没产生候选 → 保初稿
    assert trace["rounds_run"] == 1
    assert trace["rounds"][0].get("error")


def test_F_loop_trace_marks_advisory_not_hardgate():
    """trace note 标明 critic=advisory + SFS 裁判透明不进 hard_gate（北极星⑤不黑箱）。"""
    call_fn = _make_call_fn(["改稿"])
    best, trace = dr.draft_refine_loop(
        loader=None, system_prompt="SYS", initial_draft="初稿",
        style_skill_md="契约表", ref_texts=["原文"],
        rounds=1, score_fn=lambda r, d: 80.0, call_fn=call_fn)
    assert "advisory" in trace["note"]
    assert "hard_gate" in trace["note"]
    assert trace["draft_refine_enabled"] is True


# ════════════════════════════════════════════════════════════════
# [G] 真 skill_v7.md 校准（北极星纪律 3 · 契约表受控坐标有真依据）
# ════════════════════════════════════════════════════════════════

def test_G_contract_excerpt_grounded_in_real_skill():
    """蛊真人 skill_v7.md 真有契约表受控坐标 → critic rubric 不是凭空造的。"""
    skill = _ROOT / "workspace" / "styles" / "蛊真人" / "skill_v7.md"
    if not skill.exists():
        return  # CI 无样本则跳过
    txt = skill.read_text(encoding="utf-8")
    ex = dr._extract_contract_excerpt(txt)
    # 抽出的契约段（或回退头部）应含受控坐标里的核心几项
    grounded = [c for c in _CONTROLLED_COORDS if c in ex]
    assert len(grounded) >= 2, grounded
