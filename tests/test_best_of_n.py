"""best-of-N + AV-judge 配对重排择优测试（写作端 · 非迭代规避同质化 · 北极星①⑤⑥ · 2026-05-31）。

根因（本批任务说明 · arxiv 实证）：单稿直生 + av_judge 只事后单次诊断不回灌；self-refine 反复
迭代会同质化（模型把自己输出当锚反复收敛）。best-of-N 走「N 稿并行生成 + 配对判别 + 综合择优」——
selection（择优）≠ refine（迭代改），天然规避同质化。

择优用 SFS（统计指纹·透明裁判）+ AV-judge 配对走味计数（读者视角·只 select 不强判）综合分排序。
env BEST_OF_N 默认 2（active 真生效）· 1=关（退回单稿·零回归）· 全 advisory 绝不 hard_gate。

纪律：只测**确定性逻辑**（N 解析 / temperature 抖动 / 配对重排 / 综合择优 / 降级 / trace），
  gen-model + AV-judge LLM 调用全 mock（不实跑 · 需 API）。
覆盖：[A] N 解析（默认 2 / 1=关 / 非法回退 / 钳上限）；[B] temperature 多样性（N 稿真有差异）；
  [C] N 稿生成（mock gen-model · 候选失败跳过不中断）；[D] 配对重排（av_judge.pairwise_drift_count
  mock · 走味计数）；[E] 综合择优（SFS - 走味惩罚降序 / 缺项降级 / 单稿等价）；[F] trace 透明
  （选中理由 + 各候选分数不黑箱）；[G] pipeline 集成（无锚优雅降级 / 全失败 raise）。
"""
import importlib
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import gen_writer as gw  # noqa: E402
import av_judge as av  # noqa: E402
import gen_model_loader as gml  # noqa: E402


# ════════════════════════════════════════════════════════════════
# mock 基础设施
# ════════════════════════════════════════════════════════════════

class _P:
    """mock profile（带可改 temperature · best-of-N 抖动用）。"""
    def __init__(self, name="active", model="m", temperature=0.8):
        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = None
        self.api_key = "sk-test"
        self.base_url = "http://localhost/v1"


class _Loader:
    """mock loader：get_callable_profiles 返回给定 profile 列表。"""
    def __init__(self, profiles):
        self._profiles = profiles

    def get_callable_profiles(self):
        return self._profiles


def _reload_gw(n):
    """以指定 BEST_OF_N reload gen_writer（env 在函数内读 · reload 求稳）。"""
    if n is None:
        os.environ.pop("BEST_OF_N", None)
    else:
        os.environ["BEST_OF_N"] = str(n)
    importlib.reload(gw)
    return gw


# ════════════════════════════════════════════════════════════════
# [A] BEST_OF_N env 解析（默认 2 active / 1=关 / 非法回退 / 钳上限）
# ════════════════════════════════════════════════════════════════

def test_A_default_is_2_active():
    """BEST_OF_N 未设 → 默认 2（active 放量 · 用户：默认关掉写它干什么）。"""
    g = _reload_gw(None)
    try:
        assert g._best_of_n() == 2
    finally:
        _reload_gw(None)


def test_A_one_is_off():
    """BEST_OF_N=1 → 关闭择优（退回单稿直生 · 唯一关闭口 · 零回归）。"""
    try:
        assert _reload_gw("1")._best_of_n() == 1
    finally:
        _reload_gw(None)


def test_A_explicit_3():
    try:
        assert _reload_gw("3")._best_of_n() == 3
    finally:
        _reload_gw(None)


def test_A_garbage_falls_back_default():
    """空 / 非法值回退默认 2（active）· 不崩。"""
    for v in ("", "abc", "x", "true"):
        try:
            assert _reload_gw(v)._best_of_n() == 2, v
        finally:
            _reload_gw(None)


def test_A_clamps_to_max():
    """超 BEST_OF_N_MAX 钳到上限（防 token 失控）；< 1 钳到 1。"""
    try:
        assert _reload_gw("99")._best_of_n() == gw.BEST_OF_N_MAX
        assert _reload_gw("0")._best_of_n() == 1
        assert _reload_gw("-5")._best_of_n() == 1
    finally:
        _reload_gw(None)


# ════════════════════════════════════════════════════════════════
# [B] temperature 多样性（N 稿真有差异 = best-of-N 价值来源 · 非迭代）
# ════════════════════════════════════════════════════════════════

def test_B_first_temp_is_base():
    """第一稿用 profile 原始 temperature（单稿等价 · 基线行为不变）。"""
    temps = gw._candidate_temperatures(0.7, 3)
    assert temps[0] == 0.7


def test_B_temps_are_distinct():
    """N 稿 temperature 两两不同（多样性 · 避免 N 个一样的稿白烧 token）。"""
    temps = gw._candidate_temperatures(0.6, 3)
    assert len(set(temps)) == 3, temps


def test_B_temps_clamped_range():
    """高基线温度 + 抖动钳到 [0.2, 1.2] 合理区间（不发散跑偏作者风格）。"""
    temps = gw._candidate_temperatures(1.15, 4)
    for t in temps:
        assert 0.2 <= t <= 1.2, t


def test_B_single_n_one_temp():
    """N=1 → 只有 1 个温度（= 原 profile · 单稿等价）。"""
    temps = gw._candidate_temperatures(0.8, 1)
    assert temps == [0.8]


# ════════════════════════════════════════════════════════════════
# [C] N 稿生成（mock gen-model · 候选失败跳过不中断）
# ════════════════════════════════════════════════════════════════

def _patch_call_gen_model(g, replies, fail_indices=()):
    """把 g.call_gen_model 打成按调用次序返回 replies 的桩（fail_indices 抛 Exhausted）。

    返回 (restore_fn, call_log)。call_log 记录每次调用时 active profile 的 temperature。
    """
    log = {"temps": [], "n": 0}
    seq = list(replies)
    orig = g.call_gen_model

    def fake(loader, system, user, min_cjk=None, creative=False, **kwargs):
        i = log["n"]
        log["n"] += 1
        log["temps"].append(loader.get_callable_profiles()[0].temperature)
        if i in fail_indices:
            raise gml.GenModelExhaustedError([(f"p{i}", "mock fail")])
        return seq[i], _P(name=f"used{i}")

    g.call_gen_model = fake
    return (lambda: setattr(g, "call_gen_model", orig)), log


def test_C_generates_n_drafts():
    """生成 N 个候选 · 每个独立调一次 gen-model。"""
    g = _reload_gw(None)
    loader = _Loader([_P(temperature=0.7)])
    restore, log = _patch_call_gen_model(g, ["稿A", "稿B", "稿C"])
    try:
        drafts = g.generate_n_drafts(loader, "sys", "usr", 3)
    finally:
        restore()
        _reload_gw(None)
    assert len(drafts) == 3
    assert [d["reply"] for d in drafts] == ["稿A", "稿B", "稿C"]
    assert all(d["error"] is None for d in drafts)


def test_C_each_draft_uses_distinct_temperature():
    """每个候选调用时 active profile 的 temperature 不同（抖动真生效 · 生成后还原）。"""
    g = _reload_gw(None)
    p = _P(temperature=0.6)
    loader = _Loader([p])
    restore, log = _patch_call_gen_model(g, ["A", "B", "C"])
    try:
        g.generate_n_drafts(loader, "sys", "usr", 3)
    finally:
        restore()
        _reload_gw(None)
    assert len(set(log["temps"])) == 3, log["temps"]
    # 生成结束后 profile.temperature 已还原（不污染 loader）
    assert p.temperature == 0.6


def test_C_failed_candidate_skipped_not_fatal():
    """单个候选生成失败 → 标 error 跳过，不中断其余候选。"""
    g = _reload_gw(None)
    loader = _Loader([_P()])
    restore, _ = _patch_call_gen_model(g, ["A", None, "C"], fail_indices=(1,))
    try:
        drafts = g.generate_n_drafts(loader, "sys", "usr", 3)
    finally:
        restore()
        _reload_gw(None)
    assert drafts[0]["error"] is None and drafts[0]["reply"] == "A"
    assert drafts[1]["error"] is not None  # 失败候选标了 error
    assert drafts[2]["error"] is None and drafts[2]["reply"] == "C"


# ════════════════════════════════════════════════════════════════
# [D] 配对重排（av_judge.pairwise_drift_count · 走味计数）
# ════════════════════════════════════════════════════════════════

def test_D_pairwise_drift_count_counts_drift_dims():
    """pairwise_drift_count：mock gen-model 回复 → 走味维度数（薄复用 av_judge）。"""
    reply = ("```json\n" + json.dumps({"dimensions": {
        "词汇选择": {"verdict": av.DRIFT_VERDICT, "reason": "r"},
        "句法": {"verdict": av.MATCH_VERDICT},
        "话语连接词": {"verdict": av.DRIFT_VERDICT},
        "语用语气": {"verdict": av.MATCH_VERDICT},
    }}, ensure_ascii=False) + "\n```")
    orig = av.call_gen_model
    av.call_gen_model = lambda loader, sys_, usr, tag="": (reply, _P(), 0.1)
    try:
        out = av.pairwise_drift_count(_Loader([_P()]), "作者锚", "仿写B")
    finally:
        av.call_gen_model = orig
    assert out["drift_count"] == 2
    assert set(out["drift_dims"]) == {"词汇选择", "话语连接词"}
    assert out["error"] is None


def test_D_pairwise_drift_count_all_match():
    """全命中 → drift_count=0（最像作者）。"""
    reply = "```json\n" + json.dumps({"dimensions": {
        n: {"verdict": av.MATCH_VERDICT} for n in
        ["词汇选择", "句法", "话语连接词", "语用语气"]}}, ensure_ascii=False) + "\n```"
    orig = av.call_gen_model
    av.call_gen_model = lambda loader, sys_, usr, tag="": (reply, _P(), 0.1)
    try:
        out = av.pairwise_drift_count(_Loader([_P()]), "锚", "B")
    finally:
        av.call_gen_model = orig
    assert out["drift_count"] == 0
    assert out["drift_dims"] == []


def test_D_pairwise_drift_count_gen_model_fail_degrades():
    """gen-model 全失败 → error 非空 + drift_count=None（不抛错 · advisory 降级）。"""
    orig = av.call_gen_model

    def boom(*a, **k):
        raise gml.GenModelExhaustedError([("p", "all down")])
    av.call_gen_model = boom
    try:
        out = av.pairwise_drift_count(_Loader([_P()]), "锚", "B")
    finally:
        av.call_gen_model = orig
    assert out["drift_count"] is None
    assert out["error"] is not None


# ════════════════════════════════════════════════════════════════
# [E] 综合择优（SFS - 走味惩罚降序 / 缺项降级 / 单稿等价）
# ════════════════════════════════════════════════════════════════

def _scored(idx, sfs=None, av_drift=None, composite="__auto__", body_cjk=18000):
    """造一个已打分候选（composite 默认按公式自动算）。

    body_cjk 仅作候选元数据留痕——纯 freestyle 契约下择优绝不按 CJK 长短选稿
    （见 test_E_no_signal_never_selects_by_cjk）。
    """
    if composite == "__auto__":
        if sfs is not None:
            composite = round(sfs - gw.AV_DRIFT_PENALTY_PER_DIM * (av_drift or 0), 2)
        else:
            composite = None
    return {"idx": idx, "reply": f"稿{idx}", "profile": _P(),
            "temperature": 0.7, "body_cjk": body_cjk,
            "score": {"sfs": sfs, "av_drift_count": av_drift, "av_drift_dims": [],
                      "composite": composite, "errors": []},
            "error": None}


def test_E_select_highest_composite():
    """综合分降序：SFS 高 + 走味少 → composite 最高 → 选中。"""
    scored = [
        _scored(0, sfs=70.0, av_drift=2),   # composite 50
        _scored(1, sfs=80.0, av_drift=0),   # composite 80 ← 最佳
        _scored(2, sfs=85.0, av_drift=3),   # composite 55
    ]
    best, reason = gw.select_best_draft(scored)
    assert best == 1, (best, reason)
    assert "composite" in reason


def test_E_sfs_alone_when_no_av():
    """仅 SFS（AV-judge 缺）→ composite=SFS → 选 SFS 最高。"""
    scored = [_scored(0, sfs=60.0, av_drift=None),
              _scored(1, sfs=75.0, av_drift=None),
              _scored(2, sfs=70.0, av_drift=None)]
    best, _ = gw.select_best_draft(scored)
    assert best == 1


def test_E_drift_penalty_can_flip_ranking():
    """走味惩罚能翻转排序：SFS 略低但零走味 > SFS 略高但多走味（读者视角参与择优）。"""
    scored = [_scored(0, sfs=82.0, av_drift=3),   # 52
              _scored(1, sfs=78.0, av_drift=0)]   # 78 ← 翻转胜出
    best, _ = gw.select_best_draft(scored)
    assert best == 1


def test_E_fallback_av_only_when_no_sfs():
    """无 SFS 锚（composite 全 None）→ 按走味数升序兜底。"""
    scored = [{"idx": 0, "reply": "A", "profile": _P(), "temperature": 0.7,
               "body_cjk": 1, "score": {"sfs": None, "av_drift_count": 3,
                                        "av_drift_dims": [], "composite": None,
                                        "errors": []}, "error": None},
              {"idx": 1, "reply": "B", "profile": _P(), "temperature": 0.8,
               "body_cjk": 1, "score": {"sfs": None, "av_drift_count": 1,
                                        "av_drift_dims": [], "composite": None,
                                        "errors": []}, "error": None}]
    best, reason = gw.select_best_draft(scored)
    assert best == 1, (best, reason)
    assert "走味" in reason


def test_E_no_signal_falls_back_first():
    """无任何打分信号（SFS/AV 全缺）→ 退回第一稿（零回归保底）。"""
    scored = [_scored(0, sfs=None, av_drift=None),
              _scored(1, sfs=None, av_drift=None)]
    best, reason = gw.select_best_draft(scored)
    assert best == 0
    assert "第一稿" in reason


def test_E_no_signal_never_selects_by_cjk():
    """🔴 2026-07-05 纯 freestyle 契约：无打分信号时绝不按 CJK 长短择稿（北极星⑤）。

    历史：expand 软下限时代曾有「字数兜底」分支（治短稿丢达标稿 bug 的补丁）；本轮
    expand/FREESTYLE_MIN_CJK 机制整体清除（纯 freestyle·字数自然涌现）后，长短不再是
    择稿信号——短稿风险由 cluster-write step3 质检与 splitter pending_tail 在下游承接，
    不在候选选择时机械覆盖模型产出。
    """
    src = (_ROOT / "core" / "scripts" / "gen_writer.py").read_text(encoding="utf-8")
    assert "FREESTYLE_MIN_CJK" not in src, "expand 软下限机制应已整体清除（纯 freestyle）"
    assert "字数兜底" not in src, "按 CJK 择稿的兜底分支不得复活"
    scored = [_scored(0, sfs=None, av_drift=None, body_cjk=4399),
              _scored(1, sfs=None, av_drift=None, body_cjk=13606)]
    best, reason = gw.select_best_draft(scored)
    assert best == 0, (best, reason)
    assert "不按 CJK" in reason or "第一稿" in reason


def test_E_single_candidate():
    """唯一候选 → 直接选中（N=1 或仅 1 稿成功）。"""
    best, reason = gw.select_best_draft([_scored(0, sfs=50.0)])
    assert best == 0


def test_E_empty_raises():
    """全部生成失败（无候选）→ raise（与单稿全失败一致）。"""
    raised = False
    try:
        gw.select_best_draft([])
    except ValueError:
        raised = True
    assert raised


# ════════════════════════════════════════════════════════════════
# [F] score_candidate（SFS + AV 综合分 · 缺项降级 · 全 advisory 不抛错）
# ════════════════════════════════════════════════════════════════

def test_F_score_no_author_ref_all_none():
    """无 author_ref → SFS/AV 都跳过 · composite=None（无锚优雅降级 · 不抛错）。"""
    out = gw.score_candidate("一段正文。", "", _Loader([_P()]), use_av_judge=True)
    assert out["sfs"] is None
    assert out["av_drift_count"] is None
    assert out["composite"] is None
    assert out["errors"] == []


def test_F_score_composite_formula():
    """有 author_ref：SFS + AV 综合分 = sfs - 惩罚*走味数（mock 两个评分器）。"""
    g = _reload_gw(None)
    # mock style_evaluator.compute_style_only_sfs + av_judge.pairwise_drift_count
    import style_evaluator as se
    import av_judge as avj
    orig_sfs = se.compute_style_only_sfs
    orig_av = avj.pairwise_drift_count
    se.compute_style_only_sfs = lambda ref, gen, **k: {"style_only_sfs": 72.0, "subscores": {}}
    avj.pairwise_drift_count = lambda loader, a, b, **k: {
        "drift_count": 2, "drift_dims": ["词汇选择", "句法"], "error": None}
    try:
        out = g.score_candidate("正文B", "作者锚原文", _Loader([_P()]), use_av_judge=True)
    finally:
        se.compute_style_only_sfs = orig_sfs
        avj.pairwise_drift_count = orig_av
        _reload_gw(None)
    assert out["sfs"] == 72.0
    assert out["av_drift_count"] == 2
    # composite = 72 - 10*2 = 52
    assert out["composite"] == 52.0


def test_F_score_av_fail_uses_sfs_only():
    """AV-judge 失败 → 仅用 SFS（composite=sfs · 不抛错 · advisory 降级）。"""
    g = _reload_gw(None)
    import style_evaluator as se
    import av_judge as avj
    orig_sfs = se.compute_style_only_sfs
    orig_av = avj.pairwise_drift_count
    se.compute_style_only_sfs = lambda ref, gen, **k: {"style_only_sfs": 66.0, "subscores": {}}
    avj.pairwise_drift_count = lambda loader, a, b, **k: {
        "drift_count": None, "drift_dims": [], "error": "gen-model 全挂"}
    try:
        out = g.score_candidate("正文", "锚", _Loader([_P()]), use_av_judge=True)
    finally:
        se.compute_style_only_sfs = orig_sfs
        avj.pairwise_drift_count = orig_av
        _reload_gw(None)
    assert out["sfs"] == 66.0
    assert out["av_drift_count"] is None
    assert out["composite"] == 66.0  # 无走味惩罚
    assert any("av_judge" in e for e in out["errors"])


# ════════════════════════════════════════════════════════════════
# [G] pipeline 集成（trace 透明 / 无锚降级 / 全失败 raise）
# ════════════════════════════════════════════════════════════════

def test_G_pipeline_picks_best_and_traces(tmp_path=None):
    """端到端：N 稿 → 打分 → 择优 · trace 记录每候选分数 + 选中理由（不黑箱 · 北极星⑤）。"""
    g = _reload_gw(None)
    proj = _ROOT / "tests" / "__nonexistent_proj_for_bestofn__"  # 无原文池 → 仍生成 N 稿
    loader = _Loader([_P(temperature=0.7)])

    # mock：3 稿生成 + author_ref 找到 + 打分（让 idx=1 综合分最高）
    restore, _ = _patch_call_gen_model(g, ["稿0正文。", "稿1正文。", "稿2正文。"])
    orig_ref = g.gather_author_ref_text
    orig_score = g.score_candidate
    g.gather_author_ref_text = lambda root, **k: "作者真迹原文锚"
    _scores = {0: {"sfs": 60.0, "av_drift_count": 1, "av_drift_dims": [], "composite": 50.0, "errors": [], "sfs_subscores": {}},
               1: {"sfs": 80.0, "av_drift_count": 0, "av_drift_dims": [], "composite": 80.0, "errors": [], "sfs_subscores": {}},
               2: {"sfs": 75.0, "av_drift_count": 2, "av_drift_dims": [], "composite": 55.0, "errors": [], "sfs_subscores": {}}}
    _call = {"i": 0}

    def fake_score(body, ref, loader_, use_av_judge=True):
        i = _call["i"]
        _call["i"] += 1
        return _scores[i]
    g.score_candidate = fake_score
    try:
        reply, profile, trace = g.best_of_n_pipeline(loader, "sys", "usr", proj, 3)
    finally:
        restore()
        g.gather_author_ref_text = orig_ref
        g.score_candidate = orig_score
        _reload_gw(None)
    assert reply == "稿1正文。"  # idx=1 综合分最高
    assert trace["selected_idx"] == 1
    assert trace["best_of_n"] == 3
    assert trace["candidates_scored"] == 3
    assert len(trace["candidates"]) == 3
    assert "selection_reason" in trace and trace["selection_reason"]


def test_G_pipeline_no_author_ref_degrades_to_first():
    """无作者原文池 → 跳 SFS/AV 打分 · 退回第一稿（优雅降级 · 不报错）。"""
    g = _reload_gw(None)
    proj = _ROOT / "tests" / "__nonexistent_proj2__"
    loader = _Loader([_P()])
    restore, _ = _patch_call_gen_model(g, ["稿0。", "稿1。"])
    orig_ref = g.gather_author_ref_text
    g.gather_author_ref_text = lambda root, **k: ""  # 无原文池
    try:
        reply, profile, trace = g.best_of_n_pipeline(loader, "sys", "usr", proj, 2)
    finally:
        restore()
        g.gather_author_ref_text = orig_ref
        _reload_gw(None)
    assert reply == "稿0。"  # 第一稿
    assert trace["author_ref_found"] is False
    assert trace["selected_idx"] == 0


def test_G_pipeline_all_fail_raises():
    """全部候选生成失败 → raise GenModelExhaustedError（与单稿全失败一致）。"""
    g = _reload_gw(None)
    proj = _ROOT / "tests" / "__nonexistent_proj3__"
    loader = _Loader([_P()])
    restore, _ = _patch_call_gen_model(g, [None, None], fail_indices=(0, 1))
    orig_ref = g.gather_author_ref_text
    g.gather_author_ref_text = lambda root, **k: ""
    raised = False
    try:
        g.best_of_n_pipeline(loader, "sys", "usr", proj, 2)
    except gml.GenModelExhaustedError:
        raised = True
    finally:
        restore()
        g.gather_author_ref_text = orig_ref
        _reload_gw(None)
    assert raised


def test_G_save_output_records_best_of_n_trace():
    """save_output 把 best_of_n_trace 写进 changes.ecas_metadata（透明可审 · 北极星⑤）。"""
    import tempfile
    g = _reload_gw(None)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        trace = {"best_of_n": 3, "selected_idx": 1, "selection_reason": "composite 最高"}
        draft_path, cjk = g.save_output(
            root, 7, "这是一段真正的正文。", {}, 11, _P(name="winner"),
            best_of_n_trace=trace)
        changes = json.loads(
            (root / "章节" / "cluster_007_draft" / "cluster_007_changes.json").read_text(encoding="utf-8"))
    _reload_gw(None)
    bon = changes["self_eval"]["ecas_metadata"]["best_of_n"]
    assert bon["best_of_n"] == 3
    assert bon["selected_idx"] == 1


def test_G_save_output_default_single_draft_trace():
    """save_output 缺 best_of_n_trace → 默认标单稿直生（N=1 · 不黑箱）。"""
    import tempfile
    g = _reload_gw(None)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        g.save_output(root, 8, "正文内容。", {}, 1, _P())
        changes = json.loads(
            (root / "章节" / "cluster_008_draft" / "cluster_008_changes.json").read_text(encoding="utf-8"))
    _reload_gw(None)
    assert changes["self_eval"]["ecas_metadata"]["best_of_n"]["best_of_n"] == 1


# ════════════════════════════════════════════════════════════════
# [H] S9 非对称长度遥测分（LongWriter evaluation/eval_length.py 公式 ·
#     research/open_source_writing_systems_round2.md S9 · 2026-07-07）
# 🔴 落点纪律：只做遥测（selection_trace + changes 遥测字段供 learning_loop/BPR
#     当 reward 特征）——绝不参与 select_best_draft 择稿（纯 freestyle 契约·北极星⑤）。
# ════════════════════════════════════════════════════════════════

_BAND = (12000, 25000)


def _clear_band_env():
    os.environ.pop("CLUSTER_LENGTH_BAND_OVERRIDE", None)


def test_H_in_band_scores_100():
    """带内（含双边界）= 100。"""
    for y in (12000, 18000, 25000):
        assert gw.length_telemetry_score(y, _BAND) == 100.0, y


def test_H_under_band_steep_slope():
    """偏短罚陡（斜率 /2）：8000 → min/y-1=0.5 → 75；6000 → 1.0 → 50。"""
    assert gw.length_telemetry_score(8000, _BAND) == 75.0
    assert gw.length_telemetry_score(6000, _BAND) == 50.0


def test_H_over_band_gentle_slope():
    """超长罚缓（斜率 /3）：50000 → y/max-1=1.0 → 66.67。"""
    assert gw.length_telemetry_score(50000, _BAND) == 66.67


def test_H_asymmetry_under_penalized_harder():
    """同等相对偏差偏短罚更狠（LongWriter 非对称 · 治 gemini 偏短顽疾的方向性）。"""
    under = gw.length_telemetry_score(8000, _BAND)    # min/y-1=0.5 → 75
    over = gw.length_telemetry_score(37500, _BAND)    # y/max-1=0.5 → 83.33
    assert under == 75.0 and over == 83.33
    assert under < over


def test_H_extremes_clamp_zero():
    """极端偏差归零 + 空稿守卫（max(0, ·) 钳位 · 不出负分）。"""
    assert gw.length_telemetry_score(4000, _BAND) == 0.0     # min/y-1=2 → 恰好归零
    assert gw.length_telemetry_score(1000, _BAND) == 0.0     # 更短仍 0
    assert gw.length_telemetry_score(100000, _BAND) == 0.0   # y/max-1=3 → 恰好归零
    assert gw.length_telemetry_score(0, _BAND) == 0.0        # 空稿（除零守卫）
    assert gw.length_telemetry_score(-5, _BAND) == 0.0


def test_H_band_aligned_with_scanner():
    """带宽与 cluster_length_band_scanner 同源同口径：默认带一致 + env 覆盖一致。"""
    import cluster_length_band_scanner as clbs
    _clear_band_env()
    try:
        assert gw.length_telemetry_band() == clbs.DEFAULT_BAND == (12000, 25000)
        os.environ["CLUSTER_LENGTH_BAND_OVERRIDE"] = "8000,20000"
        assert gw.length_telemetry_band() == (8000, 20000)
        assert gw.length_telemetry_score(9000) == 100.0    # 覆盖带内
        assert gw.length_telemetry_score(25000) < 100.0    # 覆盖带外偏长
    finally:
        _clear_band_env()


def test_H_trace_contains_length_telemetry():
    """pipeline selection_trace：每候选带 length_telemetry_score + 顶层带宽（遥测入 trace）。"""
    g = _reload_gw(None)
    _clear_band_env()
    proj = _ROOT / "tests" / "__nonexistent_proj_lt__"
    loader = _Loader([_P()])
    restore, _ = _patch_call_gen_model(g, ["稿0。", "稿1。"])
    orig_ref = g.gather_author_ref_text
    g.gather_author_ref_text = lambda root, **k: ""
    try:
        _reply, _profile, trace = g.best_of_n_pipeline(loader, "sys", "usr", proj, 2)
    finally:
        restore()
        g.gather_author_ref_text = orig_ref
        _reload_gw(None)
    assert trace["length_telemetry_band"] == [12000, 25000]
    assert len(trace["candidates"]) == 2
    for c in trace["candidates"]:
        assert "length_telemetry_score" in c
        assert c["length_telemetry_score"] == 0.0  # 「稿N。」2 CJK 极短 → 归零（真遥测非占位）


def test_H_never_participates_in_selection():
    """🔴 落点纪律：长度遥测分绝不参与择稿——完美长度分翻不了 composite 排序，
    无信号时也不当 tiebreaker（select_best_draft 行为零变化 · 北极星⑤）。"""
    # ① composite 更高者胜出，即使其长度分为 0
    scored = [_scored(0, sfs=80.0, av_drift=0, body_cjk=4000),     # composite 80 · 长度分 0
              _scored(1, sfs=60.0, av_drift=0, body_cjk=18000)]   # composite 60 · 长度分 100
    for s in scored:
        s["length_telemetry_score"] = gw.length_telemetry_score(s["body_cjk"], _BAND)
    best, _reason = gw.select_best_draft(scored)
    assert best == 0, "composite 排序不受长度遥测影响"
    # ② 无打分信号 → 仍退回第一稿（长度分不当 tiebreaker）
    scored2 = [_scored(0, sfs=None, av_drift=None, body_cjk=4000),
               _scored(1, sfs=None, av_drift=None, body_cjk=18000)]
    for s in scored2:
        s["length_telemetry_score"] = gw.length_telemetry_score(s["body_cjk"], _BAND)
    best2, reason2 = gw.select_best_draft(scored2)
    assert best2 == 0, (best2, reason2)
    # ③ 源码锁：select_best_draft 函数体不引用 length_telemetry（择稿逻辑物理隔离）
    import inspect
    assert "length_telemetry" not in inspect.getsource(gw.select_best_draft)


def test_H_save_output_records_length_telemetry():
    """save_output 把 length_telemetry 写进 changes.ecas_metadata（供 learning_loop/BPR 的
    reward 特征 · 透明可审 · 北极星⑤）。"""
    import tempfile
    g = _reload_gw(None)
    _clear_band_env()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        g.save_output(root, 9, "这是一段正文。", {}, 1, _P())
        changes = json.loads(
            (root / "章节" / "cluster_009_draft" / "cluster_009_changes.json").read_text(encoding="utf-8"))
    _reload_gw(None)
    lt = changes["self_eval"]["ecas_metadata"]["length_telemetry"]
    assert lt["band"] == [12000, 25000]
    assert lt["score"] == 0.0  # 6 CJK 极短 → 归零（真实带外遥测非占位值）
    assert lt["formula"] == "longwriter_asymmetric(under/2, over/3)"


# ════════════════════════════════════════════════════════════════
# [I] S8 deviation 双嵌入多样性遥测分（DDPO arXiv:2503.17126 的 deviation 度量 ·
#     research/open_source_writing_systems_round2.md S8 · 2026-07-07 · 只抄度量不抄训练）
# 🔴 落点纪律：只做遥测（selection_trace 每候选 style/content deviation + 顶层
#     diversity_collapse_hint）——绝不参与 select_best_draft 择稿（北极星⑤ · 源码锁）。
# 嵌入桥全 mock（embedding_store daemon/venv 桥不实跑 · 需模型环境）。
# ════════════════════════════════════════════════════════════════


def _patch_embed(style_backend=("mock_style", 3, None),
                 style_vecs="__unset__", content_vecs="__unset__"):
    """把 embedding_store 双轨桩掉：_detect_backend / compute_embeddings_batch /
    compute_content_embeddings_batch（gen_writer 内部 import 同一 sys.modules 实例）。
    返回 restore_fn。"""
    import embedding_store as es
    orig = (es._detect_backend, es.compute_embeddings_batch,
            es.compute_content_embeddings_batch)
    es._detect_backend = lambda: style_backend
    if style_vecs != "__unset__":
        es.compute_embeddings_batch = lambda texts: style_vecs
    if content_vecs != "__unset__":
        es.compute_content_embeddings_batch = lambda texts: content_vecs

    def restore():
        (es._detect_backend, es.compute_embeddings_batch,
         es.compute_content_embeddings_batch) = orig
    return restore


def test_I_pairwise_math_hand_computed():
    """pairwise 平均余弦距离手算 fixture：风格/内容双轨各自独立算 · 数值精确匹配。

    风格轨（dim=3 单位向量·cosine=点积）：v0=[1,0,0] v1=[0,1,0] v2=[1,0,0]
      d(0,1)=1 d(0,2)=0 d(1,2)=1 → dev = [0.5, 1.0, 0.5]
    内容轨：c0=[1,0] c1=[1,0] c2=[0,1] → dev = [0.5, 0.5, 1.0]
    """
    restore = _patch_embed(
        style_backend=("mock_style", 3, None),
        style_vecs=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]],
        content_vecs=[[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    try:
        out = gw.candidate_deviation_scores(["甲", "乙", "丙"])
    finally:
        restore()
    assert out == [
        {"style_deviation": 0.5, "content_deviation": 0.5},
        {"style_deviation": 1.0, "content_deviation": 0.5},
        {"style_deviation": 0.5, "content_deviation": 1.0},
    ]


def test_I_style_content_tracks_independent():
    """风格/内容分轨独立：单轨不可用 → 该轨全 None，另一轨照算（不互相拖垮）。"""
    # ① 内容轨不可用（bge 后端缺 → compute_content_embeddings_batch=None）→ 只有风格轨
    restore = _patch_embed(
        style_backend=("mock_style", 2, None),
        style_vecs=[[1.0, 0.0], [0.0, 1.0]], content_vecs=None)
    try:
        out = gw.candidate_deviation_scores(["甲", "乙"])
    finally:
        restore()
    assert out == [{"style_deviation": 1.0, "content_deviation": None},
                   {"style_deviation": 1.0, "content_deviation": None}]
    # ② 风格轨后端=hash（默认零配置·hash 不是风格语义）→ 风格轨诚实 None · 内容轨照算
    restore = _patch_embed(
        style_backend=("hash", 384, None),
        style_vecs=[[9.9], [9.9]],  # 即使被错误调用也不该用到（hash 短路在前）
        content_vecs=[[1.0, 0.0], [0.0, 1.0]])
    try:
        out = gw.candidate_deviation_scores(["甲", "乙"])
    finally:
        restore()
    assert out == [{"style_deviation": None, "content_deviation": 1.0},
                   {"style_deviation": None, "content_deviation": 1.0}]


def test_I_both_tracks_unavailable_skips_honest():
    """两轨全不可用（hash 后端 + 内容后端缺）→ 返回 None（诚实 skip 不伪装信号）。"""
    restore = _patch_embed(style_backend=("hash", 384, None), content_vecs=None)
    try:
        assert gw.candidate_deviation_scores(["甲", "乙"]) is None
    finally:
        restore()


def test_I_n_lt_2_skips():
    """N<2 → None（deviation 对单候选无定义 · 不管嵌入是否可用）。"""
    restore = _patch_embed(
        style_backend=("mock_style", 2, None),
        style_vecs=[[1.0, 0.0]], content_vecs=[[1.0, 0.0]])
    try:
        assert gw.candidate_deviation_scores(["单稿"]) is None
        assert gw.candidate_deviation_scores([]) is None
    finally:
        restore()


def test_I_single_candidate_embed_failure_not_fatal():
    """真后端下单候选桥失败（被 compute_embeddings_batch 兜底成 hash 384 维·与后端 dim
    不符）→ 该候选 style_deviation=None，其余候选照算（不崩 · 不让 hash 向量污染 pairwise）。"""
    restore = _patch_embed(
        style_backend=("mock_style", 3, None),
        style_vecs=[[1.0, 0.0, 0.0], [0.5] * 384, [0.0, 0.0, 1.0]],  # idx=1 维度不符=失败
        content_vecs=None)
    try:
        out = gw.candidate_deviation_scores(["甲", "乙", "丙"])
    finally:
        restore()
    assert out[0]["style_deviation"] == 1.0   # 只与 idx=2 可比 → d=1.0
    assert out[1]["style_deviation"] is None  # 失败候选诚实 None
    assert out[2]["style_deviation"] == 1.0


def test_I_collapse_hint_positive_negative():
    """diversity_collapse_hint 正反例 + env 地板可调 + 无风格信号=None（未知≠健康）。"""
    os.environ.pop("DEVIATION_COLLAPSE_FLOOR", None)
    # ① 正例：全体 style_deviation 均值 0.003 < 默认地板 0.02 → True（坍缩·假选择）
    low = [{"style_deviation": 0.001, "content_deviation": 0.5},
           {"style_deviation": 0.005, "content_deviation": 0.5}]
    assert gw.diversity_collapse_hint(low) is True
    # ② 反例：均值 0.35 >= 0.02 → False（多样性健康）
    high = [{"style_deviation": 0.3, "content_deviation": None},
            {"style_deviation": 0.4, "content_deviation": None}]
    assert gw.diversity_collapse_hint(high) is False
    # ③ deviation 整体 skip / 风格轨全 None → None（不假装 False）
    assert gw.diversity_collapse_hint(None) is None
    assert gw.diversity_collapse_hint(
        [{"style_deviation": None, "content_deviation": 0.5}] * 2) is None
    # ④ env 地板可调：DEVIATION_COLLAPSE_FLOOR=0.5 → 均值 0.35 也算坍缩
    try:
        os.environ["DEVIATION_COLLAPSE_FLOOR"] = "0.5"
        assert gw.diversity_collapse_hint(high) is True
        # 非法/负值回退默认 0.02
        os.environ["DEVIATION_COLLAPSE_FLOOR"] = "abc"
        assert gw._deviation_collapse_floor() == gw.DEVIATION_COLLAPSE_FLOOR_DEFAULT
        os.environ["DEVIATION_COLLAPSE_FLOOR"] = "-1"
        assert gw._deviation_collapse_floor() == gw.DEVIATION_COLLAPSE_FLOOR_DEFAULT
    finally:
        os.environ.pop("DEVIATION_COLLAPSE_FLOOR", None)


def test_I_trace_contains_deviation():
    """pipeline selection_trace：每候选带 style/content deviation + 顶层 collapse_hint/
    地板值（遥测入 trace → 随 best_of_n_trace 进 changes.ecas_metadata · 透明可审）。"""
    g = _reload_gw(None)
    os.environ.pop("DEVIATION_COLLAPSE_FLOOR", None)
    proj = _ROOT / "tests" / "__nonexistent_proj_dev__"
    loader = _Loader([_P()])
    restore_gen, _ = _patch_call_gen_model(g, ["稿0。", "稿1。"])
    orig_ref = g.gather_author_ref_text
    g.gather_author_ref_text = lambda root, **k: ""
    restore_embed = _patch_embed(
        style_backend=("mock_style", 2, None),
        style_vecs=[[1.0, 0.0], [0.0, 1.0]],   # 正交 → dev=1.0（健康多样）
        content_vecs=None)                      # 内容轨不可用 → None
    try:
        _reply, _profile, trace = g.best_of_n_pipeline(loader, "sys", "usr", proj, 2)
    finally:
        restore_gen()
        restore_embed()
        g.gather_author_ref_text = orig_ref
        _reload_gw(None)
    assert trace["deviation_collapse_floor"] == 0.02
    assert trace["diversity_collapse_hint"] is False  # 均值 1.0 远高于地板
    assert len(trace["candidates"]) == 2
    for c in trace["candidates"]:
        assert c["style_deviation"] == 1.0
        assert c["content_deviation"] is None


def test_I_trace_collapse_hint_true_when_identical():
    """N 候选风格向量完全相同（deviation=0）→ diversity_collapse_hint=True（坍缩记录 ·
    选择照旧走 select_best_draft 不受影响）。"""
    g = _reload_gw(None)
    os.environ.pop("DEVIATION_COLLAPSE_FLOOR", None)
    proj = _ROOT / "tests" / "__nonexistent_proj_dev2__"
    loader = _Loader([_P()])
    restore_gen, _ = _patch_call_gen_model(g, ["稿0。", "稿1。"])
    orig_ref = g.gather_author_ref_text
    g.gather_author_ref_text = lambda root, **k: ""
    restore_embed = _patch_embed(
        style_backend=("mock_style", 2, None),
        style_vecs=[[1.0, 0.0], [1.0, 0.0]],   # 同一向量 → dev=0.0 坍缩
        content_vecs=None)
    try:
        reply, _profile, trace = g.best_of_n_pipeline(loader, "sys", "usr", proj, 2)
    finally:
        restore_gen()
        restore_embed()
        g.gather_author_ref_text = orig_ref
        _reload_gw(None)
    assert trace["diversity_collapse_hint"] is True
    assert reply == "稿0。"                       # 择稿不受 collapse_hint 影响（无信号退第一稿）
    assert trace["selected_idx"] == 0


def test_I_embed_unavailable_trace_honest_none():
    """嵌入两轨全不可用 → trace 里 deviation 全 None + collapse_hint=None（诚实 skip ·
    pipeline 不崩不阻断写作）。"""
    g = _reload_gw(None)
    proj = _ROOT / "tests" / "__nonexistent_proj_dev3__"
    loader = _Loader([_P()])
    restore_gen, _ = _patch_call_gen_model(g, ["稿0。", "稿1。"])
    orig_ref = g.gather_author_ref_text
    g.gather_author_ref_text = lambda root, **k: ""
    restore_embed = _patch_embed(style_backend=("hash", 384, None), content_vecs=None)
    try:
        reply, _profile, trace = g.best_of_n_pipeline(loader, "sys", "usr", proj, 2)
    finally:
        restore_gen()
        restore_embed()
        g.gather_author_ref_text = orig_ref
        _reload_gw(None)
    assert reply == "稿0。"
    assert trace["diversity_collapse_hint"] is None
    for c in trace["candidates"]:
        assert c["style_deviation"] is None
        assert c["content_deviation"] is None


def test_I_deviation_never_participates_in_selection():
    """🔴 落点纪律：deviation 遥测绝不参与择稿——deviation 高低翻不了 composite 排序，
    无信号时也不当 tiebreaker（select_best_draft 行为零变化 · 北极星⑤）。"""
    # ① composite 更高者胜出，即使其 deviation=0（坍缩候选照样按 composite 选）
    scored = [_scored(0, sfs=80.0, av_drift=0), _scored(1, sfs=60.0, av_drift=0)]
    for s, dv in zip(scored, (0.0, 0.9)):
        s["style_deviation"] = dv
        s["content_deviation"] = dv
    best, _reason = gw.select_best_draft(scored)
    assert best == 0, "composite 排序不受 deviation 遥测影响"
    # ② 无打分信号 → 仍退回第一稿（deviation 不当 tiebreaker）
    scored2 = [_scored(0, sfs=None, av_drift=None), _scored(1, sfs=None, av_drift=None)]
    scored2[0]["style_deviation"], scored2[1]["style_deviation"] = 0.01, 0.95
    best2, reason2 = gw.select_best_draft(scored2)
    assert best2 == 0, (best2, reason2)
    # ③ 源码锁：select_best_draft 函数体不引用 deviation/collapse（择稿逻辑物理隔离 ·
    #    复用 test_H 同款 inspect 锁模式）
    import inspect
    src = inspect.getsource(gw.select_best_draft)
    assert "deviation" not in src and "collapse" not in src
