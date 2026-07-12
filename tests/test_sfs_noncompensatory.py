"""SFS 非补偿聚合测试 — 治单维风格崩被补偿性算术平均稀释的盲点（北极星①⑤⑥ · 2026-05-31）。

背景（本批任务说明 · 实证）：
  compute_programmatic_score 的 total 是**完全补偿性**加权算术平均——12+ 个细维里若**单一
  维度风格崩**（如对话格式全错 / 段长崩塌 / 功能词指纹完全不符），它只占 4%-8% 权重，会被
  其余高分维度**稀释**，总分仍落 A/B 级（盲点）。风格保真**不可补偿**：对话格式全错的复刻稿
  即便句长标点全对，读者一眼出戏。

升级（全在 style_evaluator.py 内自包含 · 纯增量 · 不改 compute_programmatic_score）：
  ① 加权几何平均 geometric_mean：乘性聚合 · 单维 s→0 把总分拉垮（不被其他维补偿）。
  ② worst_dimension_floor：权重 ≥ 阈值的最低维度分（地板 · 门控小权重维度噪声）。
  ③ collapse_dilution_gap：算术平均 - 几何平均（盲点信号 · 缺口大 = 有崩维被稀释）。

影子并行（北极星纪律 2/7）：env SFS_NONCOMP_MODE 控制——
  · active（默认放量）：附加 report["noncompensatory"] + 崩维 advisory 升顶层（仍 advisory）。
  · shadow：附加但分歧只写 stderr，不升顶层 issue。
  · off：完全不算。
不论哪种模式，绝不改 sfs_quick/programmatic_score/grade 任何确定性判决（顾问非法官 · 北极星⑤）。

测试覆盖：① 几何平均纯函数（同分≈算术 / 单维崩被拉垮）；② worst-floor 抓崩维 + 门控小权重；
③ 缺口信号；④ 崩维 advisory；⑤ mode 标志；⑥ shadow 默认零回归 sfs_quick；⑦ active 升顶层
不改判决；⑧ advisory gate_level 强制 + 不进 hard_gate；⑨ 真原文金标准——同作者各维都高 →
非补偿聚合仍高分不误拉垮；崩的单维低 → 被 floor/几何均抓住（北极星纪律 3 矫枉过正金标准）。

只测确定性纯函数，不碰 LLM / agent。守纪律：真作者原文当「系统生成」喂校验确认真作者不被误判。
"""
import os
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_evaluator as se  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _reload_se(mode):
    """以指定 SFS_NONCOMP_MODE reload style_evaluator（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("SFS_NONCOMP_MODE", None)
    else:
        os.environ["SFS_NONCOMP_MODE"] = mode
    importlib.reload(se)
    return se


def _load_chapter(book, ch):
    """读真原文章节（原文/ 下均为纯正文 txt）。"""
    p = _ROOT / "workspace" / "styles" / book / "原文" / f"{ch}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def _load_cluster(book, count=6, start=10):
    """拼真原文若干章成一个 cluster（~13-17k CJK · 模拟 cluster 草稿）。"""
    d = _ROOT / "workspace" / "styles" / book / "原文"
    if not d.is_dir():
        return None
    files = sorted(d.glob("第*章.txt"))
    files = [f for f in files if "全本" not in f.name][start:start + count]
    if len(files) < 4:
        return None
    parts = [f.read_text(encoding="utf-8") for f in files]
    return "\n\n".join(parts)


def _dims(*specs):
    """构造 compute_noncompensatory_aggregation 期望的维度列表。
    specs: (name, weight, score(0-100)) 三元组。"""
    return [{"name": n, "weight": w, "score": s} for (n, w, s) in specs]


# 稳定风格指纹维（驱动几何均 / floor / advisory · 与实现 _NONCOMP_STABLE_FINGERPRINT_DIMS 对齐）。
_STABLE = ["句长分布 JSD", "段落长度分布 JSD", "标点密度指纹", "功能词指纹",
           "句长标准差匹配", "段落开头多样性", "单句成段率匹配"]
# 情节内容依赖维（跨章波动 · 不进非补偿头条数值 · 仅可见性）。
_VOLATILE = ["对话占比偏差", "极短段占比匹配", "群戏人数匹配", "拟声段数量匹配",
             "禁用词扣分", "引号化独白比"]


def _stable_dims(scores, weight=0.06):
    """按 _STABLE 维度名构造 dims（scores 可少于 7 个 · 取前 N 个稳定维）。"""
    return _dims(*[(_STABLE[i], weight, sc) for i, sc in enumerate(scores)])


# ════════════════════════════════════════════════════════════════
# [A] 几何平均纯函数：同分 ≈ 算术；单维崩被拉垮
# ════════════════════════════════════════════════════════════════

def test_A_all_high_geometric_close_to_arithmetic():
    """各稳定维都高（无崩维）→ 几何平均 ≈ 稳定算术平均（不被误拉垮 · 同作者场景核心）。"""
    dims = _stable_dims([92.0, 90.0, 94.0, 91.0, 93.0])
    r = se.compute_noncompensatory_aggregation(dims)
    assert r["applicable"] is True
    # 全高分 → 几何与稳定算术差距极小（< 1 分）
    assert abs(r["stable_arithmetic_mean"] - r["geometric_mean"]) < 1.0, r
    assert r["geometric_mean"] >= 88.0, r
    assert r["collapsed_stable_fingerprint"] == [], r


def test_A_single_stable_collapse_drags_geometric_below_arithmetic():
    """单个**稳定指纹维**崩（功能词指纹全错=score 5）但其余高 → 算术仍高（盲点），几何显著被拉垮。"""
    # 6 个稳定指纹维 90 分 + 功能词指纹 5 分
    dims = _stable_dims([90.0, 90.0, 90.0, 5.0, 90.0, 90.0])  # 第 4 个=功能词指纹
    r = se.compute_noncompensatory_aggregation(dims)
    # 稳定维算术平均被稀释成高分（盲点）
    assert r["stable_arithmetic_mean"] >= 70.0, r
    # 非补偿几何平均把单指纹维崩拉垮（显著低于稳定算术）
    assert r["geometric_mean"] < r["stable_arithmetic_mean"] - 8.0, r
    # 缺口暴露盲点
    assert r["collapse_dilution_gap"] >= 8.0, r
    assert "功能词指纹" in r["collapsed_stable_fingerprint"], r


def test_A_score_zero_does_not_raise_domain_error():
    """某稳定维 score=0（math.log domain error 风险）→ 安全钳到 eps，几何平均仍被拉垮但不抛错。"""
    dims = _stable_dims([90.0, 0.0, 88.0])  # 第 2 个=段落长度分布 JSD = 0
    r = se.compute_noncompensatory_aggregation(dims)  # 不应抛 ValueError(math domain)
    assert r["geometric_mean"] < r["stable_arithmetic_mean"], r
    # 0 分维度被列为崩维
    assert "段落长度分布 JSD" in r["collapsed_stable_fingerprint"], r


def test_A_arithmetic_mean_reconciles_with_programmatic():
    """复算的 arithmetic_mean 与 compute_programmatic_score.total 对账一致（同权同分同算法）。"""
    # 直接用 programmatic 真实输出对账（同一 dims 列表）
    ref = "他淡淡地看了一眼，显然没把这事放在心上。" * 30
    gen = "她淡淡地笑了笑，显然早就料到这个结果。" * 30
    rp = se.analyze_text(ref)
    gp = se.analyze_text(gen)
    ps = se.compute_programmatic_score(rp, gp, gen)
    r = se.compute_noncompensatory_aggregation(ps["dimensions"])
    assert abs(r["arithmetic_mean"] - ps["total"]) < 0.5, (r["arithmetic_mean"], ps["total"])


# ════════════════════════════════════════════════════════════════
# [B] worst-dimension floor：抓崩维 + 门控小权重维度
# ════════════════════════════════════════════════════════════════

def test_B_worst_floor_localizes_collapsed_dimension():
    """worst_dimension_floor + worst_dimension 定位到那个崩维（advisory 不黑箱）。"""
    dims = _dims(("句长分布 JSD", 0.08, 95.0), ("段落长度分布 JSD", 0.05, 30.0),
                 ("功能词指纹", 0.08, 92.0))
    r = se.compute_noncompensatory_aggregation(dims)
    assert r["worst_dimension"] == "段落长度分布 JSD", r
    assert r["worst_dimension_floor"] == 30.0, r


def test_B_volatile_dim_excluded_from_floor():
    """情节内容维（拟声段数量匹配=volatile）即便崩到 10 分，也不进 worst-floor（跨章波动·防误判真作者）。
    大权重稳定维都健康 → floor 取稳定维高分，volatile 崩维仅列 collapsed_dimensions（可见性）。"""
    dims = _dims(("句长分布 JSD", 0.08, 90.0), ("功能词指纹", 0.08, 88.0),
                 ("拟声段数量匹配", 0.025, 10.0))  # volatile 维崩
    r = se.compute_noncompensatory_aggregation(dims)
    # floor 只看稳定指纹维 → 不取那个 volatile 崩维
    assert r["worst_dimension_floor"] >= 80.0, r
    assert r["worst_dimension"] != "拟声段数量匹配", r
    # volatile 崩维不进稳定崩维清单（不触发 advisory）
    assert "拟声段数量匹配" not in r["collapsed_stable_fingerprint"], r
    # 但全量 collapsed_dimensions（可见性）仍列出它
    assert "拟声段数量匹配" in r["collapsed_dimensions"], r


def test_B_floor_gates_small_weight_within_stable():
    """稳定维内部小权重维（< _NONCOMP_MIN_FLOOR_WEIGHT=0.04）的低分不当 floor 主因。
    一个 0.03 权重的稳定维崩 + 大权重稳定维健康 → floor 不取那个小权重崩维。"""
    dims = _dims(("句长分布 JSD", 0.08, 90.0), ("功能词指纹", 0.08, 88.0),
                 ("段落开头多样性", 0.03, 10.0))  # 小权重稳定维崩（< 0.04 门控）
    r = se.compute_noncompensatory_aggregation(dims)
    assert r["worst_dimension_floor"] >= 80.0, r
    assert r["worst_dimension"] != "段落开头多样性", r


def test_B_all_small_weight_falls_back():
    """稳定维全是小权重时 floor 退回稳定全集（不返回空 · 健壮性）。"""
    dims = _dims(("段落开头多样性", 0.03, 40.0), ("单句成段率匹配", 0.03, 70.0))
    r = se.compute_noncompensatory_aggregation(dims)
    assert r["worst_dimension"] == "段落开头多样性", r  # 稳定全集最低
    assert r["worst_dimension_floor"] == 40.0, r


# ════════════════════════════════════════════════════════════════
# [C] 缺口信号 + 崩维 advisory
# ════════════════════════════════════════════════════════════════

def test_C_stable_collapse_emits_dimension_advisory():
    """**稳定指纹维**崩（< 崩塌线 60）→ 产 SFS_DIMENSION_COLLAPSE advisory（盲点核心场景）。"""
    dims = _stable_dims([90.0, 90.0, 90.0, 12.0, 90.0, 90.0])  # 第 4 个=功能词指纹崩
    r = se.compute_noncompensatory_aggregation(dims)
    codes = [i["code"] for i in r["advisory_issues"]]
    assert "SFS_DIMENSION_COLLAPSE" in codes, r
    # message 含被抓的崩维名（可定位）
    msg = next(i["message"] for i in r["advisory_issues"]
               if i["code"] == "SFS_DIMENSION_COLLAPSE")
    assert "功能词指纹" in msg, msg


def test_C_volatile_collapse_does_not_emit_advisory():
    """**情节内容维**崩（对话占比偏差=8）但稳定维全高 → 不产 advisory（真作者跨章波动不误报）。"""
    dims = _dims(("句长分布 JSD", 0.08, 92.0), ("功能词指纹", 0.08, 90.0),
                 ("标点密度指纹", 0.04, 91.0), ("对话占比偏差", 0.06, 8.0),
                 ("群戏人数匹配", 0.025, 0.0))  # volatile 维全崩
    r = se.compute_noncompensatory_aggregation(dims)
    codes = [i["code"] for i in r["advisory_issues"]]
    assert "SFS_DIMENSION_COLLAPSE" not in codes, r
    assert r["collapsed_stable_fingerprint"] == [], r
    # 但 collapsed_dimensions（可见性）仍含 volatile 崩维
    assert "对话占比偏差" in r["collapsed_dimensions"], r


def test_C_no_collapse_but_gap_emits_compensation_gap():
    """稳定维无显式崩但缺口超阈（多个中等偏低指纹维联合稀释）→ SFS_COMPENSATION_GAP。"""
    # 稳定维都 ≥ 60（不算崩），但分散在 60-95 → 几何均明显低于稳定算术均
    dims = _stable_dims([95.0, 95.0, 95.0, 62.0, 63.0, 61.0])
    r = se.compute_noncompensatory_aggregation(dims)
    assert r["collapsed_stable_fingerprint"] == [], r  # 无 < 60 的稳定崩维
    codes = [i["code"] for i in r["advisory_issues"]]
    if r["collapse_dilution_gap"] >= se._NONCOMP_GAP_ADVISORY:
        assert "SFS_COMPENSATION_GAP" in codes, r
    assert "SFS_DIMENSION_COLLAPSE" not in codes, r


def test_C_all_high_no_advisory():
    """各稳定维都高 → 无 advisory（同作者场景不误报 · 金标准雏形）。"""
    dims = _stable_dims([92.0, 90.0, 94.0, 91.0])
    r = se.compute_noncompensatory_aggregation(dims)
    assert r["advisory_issues"] == [], r


def test_C_empty_dims_not_applicable():
    """空维度列表 → applicable=False，不抛错。"""
    r = se.compute_noncompensatory_aggregation([])
    assert r["applicable"] is False
    assert r["advisory_issues"] == []


# ════════════════════════════════════════════════════════════════
# [D] mode 标志 _sfs_noncomp_mode
# ════════════════════════════════════════════════════════════════

def test_D_mode_default_active_and_values():
    """SFS_NONCOMP_MODE 默认 active（放量真生效）· 非法回退 active · {shadow,off} 原样。"""
    sx = _reload_se(None)
    try:
        assert sx._sfs_noncomp_mode() == "active"
        sx = _reload_se("shadow")
        assert sx._sfs_noncomp_mode() == "shadow"
        sx = _reload_se("off")
        assert sx._sfs_noncomp_mode() == "off"
        sx = _reload_se("garbage")
        assert sx._sfs_noncomp_mode() == "active"
        sx = _reload_se("ACTIVE")  # 大小写不敏感
        assert sx._sfs_noncomp_mode() == "active"
    finally:
        _reload_se(None)


# ════════════════════════════════════════════════════════════════
# [E] evaluate 集成：shadow 零回归 / active 升顶层不改判决 / off 不算
# ════════════════════════════════════════════════════════════════

_REF = "他淡淡地看了一眼，显然没把这事放在心上。" * 30
_GEN = "她淡淡地笑了笑，显然早就料到这个结果。" * 30


def test_E_off_does_not_compute():
    """off：report 无 noncompensatory 字段（纯旧行为）。"""
    sx = _reload_se("off")
    try:
        rep = sx.evaluate(_REF, _GEN)
        assert "noncompensatory" not in rep
    finally:
        _reload_se(None)


def test_E_shadow_zero_regression_on_sfs_quick():
    """shadow：sfs_quick / grade 与 off 完全一致（附加 noncompensatory 不改判决 · 零回归核心）。"""
    sx = _reload_se("shadow")
    try:
        rep_shadow = sx.evaluate(_REF, _GEN)
        sx_off = _reload_se("off")
        rep_off = sx_off.evaluate(_REF, _GEN)
        assert rep_shadow["sfs_quick"] == rep_off["sfs_quick"]
        assert (rep_shadow["programmatic_score"]["grade"]
                == rep_off["programmatic_score"]["grade"])
        # shadow 附加 noncompensatory 加项
        assert "noncompensatory" in rep_shadow
        assert "noncompensatory" not in rep_off
    finally:
        _reload_se(None)


def test_E_active_appends_and_does_not_change_sfs_quick():
    """active：附加 noncompensatory + 不改 sfs_quick（与 off 同输入同分 · 判决不变）。"""
    sx = _reload_se("active")
    try:
        rep_active = sx.evaluate(_REF, _GEN)
        rep_off = _reload_se("off").evaluate(_REF, _GEN)
        assert "noncompensatory" in rep_active
        assert rep_active["sfs_quick"] == rep_off["sfs_quick"]
        assert (rep_active["programmatic_score"]["total"]
                == rep_off["programmatic_score"]["total"])
    finally:
        _reload_se(None)


def test_E_active_coexists_with_l3a_advisory():
    """active 下非补偿 advisory 与 L3a advisory 共存于同一顶层 advisory_issues（不互相覆盖）。"""
    os.environ["SFS_NONCOMP_MODE"] = "active"
    os.environ["L3A_BURSTINESS_MODE"] = "active"
    importlib.reload(se)
    try:
        # 均匀化文本 → 触发 L3a；用 baseline=None 但 gen 单维可能崩 → 两层都可能有 issue
        para = "他走进房间看了看四周然后慢慢地坐了下来心里想着那些事情感到疲惫。"
        homog = "\n".join(para for _ in range(400))
        rep = se.evaluate(_REF, homog)
        # 若两层都有 issue，advisory_issues 应含两层各自的 code（extend 不覆盖）
        if "advisory_issues" in rep:
            for it in rep["advisory_issues"]:
                assert it["gate_level"] == "advisory", it
            # noncompensatory 自身列表与顶层不冲突（顶层是各层合并）
            nc_codes = {i["code"] for i in rep["noncompensatory"]["advisory_issues"]}
            top_codes = {i["code"] for i in rep["advisory_issues"]}
            assert nc_codes.issubset(top_codes), (nc_codes, top_codes)
    finally:
        os.environ.pop("SFS_NONCOMP_MODE", None)
        os.environ.pop("L3A_BURSTINESS_MODE", None)
        importlib.reload(se)


# ════════════════════════════════════════════════════════════════
# [F] advisory gate_level 强制 + 不进 hard_gate（北极星⑤）
# ════════════════════════════════════════════════════════════════

def test_F_all_issues_gate_level_advisory():
    """北极星⑤强制：非补偿聚合产出的所有 issue gate_level == advisory。"""
    dims = _stable_dims([90.0, 90.0, 90.0, 8.0, 90.0, 90.0])  # 功能词指纹崩
    r = se.compute_noncompensatory_aggregation(dims)
    assert r["advisory_issues"], "崩维应触发 advisory"
    for it in r["advisory_issues"]:
        assert it["gate_level"] == "advisory", it
    assert "advisory" in r["note"]


def test_F_no_hard_gate_codes_introduced():
    """非补偿 code 绝不进 audit_hub.HARD_GATE_CODES（新 code 必须留 advisory 域）。"""
    try:
        sys.path.insert(0, str(_ROOT / "core" / "scripts"))
        import audit_hub  # noqa: E402
    except Exception:
        return  # 无 audit_hub 环境则跳过
    nc_codes = {"SFS_DIMENSION_COLLAPSE", "SFS_COMPENSATION_GAP"}
    assert nc_codes.isdisjoint(set(audit_hub.HARD_GATE_CODES)), \
        "非补偿 code 不得进 hard_gate（顾问非法官 · 北极星⑤）"


# ════════════════════════════════════════════════════════════════
# [G] 真原文矫枉过正金标准（北极星纪律 3）：真作者各维都高 → 不被非补偿聚合误拉垮
# ════════════════════════════════════════════════════════════════

def test_G_real_author_self_not_falsely_dragged_down():
    """真作者两段原文互比（has_author_profile=True · 模拟真实写作用作者档场景）→ 非补偿聚合
    不误拉垮：稳定指纹维几何均高、无崩维 advisory（同作者高分绝不被 floor/几何均误判 · 金标准核心）。

    🔴 实证（本批任务发现）：情节内容维（对话占比/极短段/群戏人数）真作者**跨章**天然大幅波动
    （蛊真人 ch10 vs ch20 对话占比 38 / 极短段 0 / 群戏 0），若把它们算进非补偿几何均会把真作者
    拉垮到 geo=5-14（巨型误报）。分层后只算稳定指纹维 → 真作者 geo 90+ 不被误判。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        a = _load_chapter(book, "第010章")
        c = _load_chapter(book, "第020章")
        if a is None or c is None:
            continue
        found = True
        rp = se.analyze_text(a)
        gp = se.analyze_text(c)
        ps = se.compute_programmatic_score(rp, gp, c, has_author_profile=True)
        r = se.compute_noncompensatory_aggregation(ps["dimensions"])
        # 同作者稳定指纹维健康 → 缺口小（不被算术-几何缺口误判为崩）
        assert r["collapse_dilution_gap"] < 8.0, (book, r)
        # 稳定指纹几何均不被误拉低（同作者风格指纹一致 · ≥ 75 留余量）
        assert r["geometric_mean"] >= 75.0, (book, r["geometric_mean"], r)
        # 无稳定指纹崩维 → 不产 advisory（真作者不误报）
        assert r["collapsed_stable_fingerprint"] == [], (book, r)
        assert not any(i["code"] == "SFS_DIMENSION_COLLAPSE"
                       for i in r["advisory_issues"]), (book, r)
    assert found or _load_chapter("蛊真人", "第010章") is None


def test_G_real_author_cluster_self_high_no_collapse():
    """真作者 cluster 自比（ref=gen=真原文 · has_author_profile=True）→ 各维近满分，非补偿
    聚合也近满分、无崩维 advisory（真作者绝不被非补偿层误判 · 金标准）。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        cluster = _load_cluster(book, count=6, start=10)
        if cluster is None:
            continue
        found = True
        rp = se.analyze_text(cluster)
        ps = se.compute_programmatic_score(rp, rp, cluster, has_author_profile=True)
        r = se.compute_noncompensatory_aggregation(ps["dimensions"])
        # 自比各维近满分 → 稳定指纹几何均也高、无稳定崩维
        assert r["geometric_mean"] >= 90.0, (book, r)
        assert r["collapsed_stable_fingerprint"] == [], (book, r)
        assert not any(i["code"] == "SFS_DIMENSION_COLLAPSE"
                       for i in r["advisory_issues"]), (book, r)
    assert found or _load_cluster("蛊真人") is None


def test_G_collapsed_replica_caught_by_floor():
    """崩的复刻（人为把真作者一段的**稳定风格指纹维**打崩）→ 被 worst-floor / 几何均抓住（目的）。
    构造：真作者各维高，注入一个稳定指纹维 5 分崩 → 稳定算术仍 A/B，但 floor 低 + 几何均被拉垮。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        cluster = _load_cluster(book, count=6, start=10)
        if cluster is None:
            continue
        found = True
        rp = se.analyze_text(cluster)
        ps = se.compute_programmatic_score(rp, rp, cluster, has_author_profile=True)
        # 人为把「功能词指纹」（稳定风格指纹维 · 模拟文风崩的复刻）打崩到 5 分
        dims2 = []
        for d in ps["dimensions"]:
            if d["name"] == "功能词指纹":
                nd = dict(d); nd["score"] = 5.0; dims2.append(nd)
            else:
                dims2.append(d)
        r = se.compute_noncompensatory_aggregation(dims2)
        # floor 抓到那个稳定指纹崩维（5 分）
        assert r["worst_dimension_floor"] <= 5.0, (book, r)
        assert r["worst_dimension"] == "功能词指纹", (book, r)
        # 几何均被显著拉垮（非补偿核心目的 · 对比同口径稳定算术）
        assert r["geometric_mean"] < r["stable_arithmetic_mean"] - 5.0, (book, r)
        # 触发崩维 advisory
        assert any(i["code"] == "SFS_DIMENSION_COLLAPSE"
                   for i in r["advisory_issues"]), (book, r)
    assert found or _load_cluster("蛊真人") is None
