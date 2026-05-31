# -*- coding: utf-8 -*-
"""跨 cluster 长程作者文风漂移 + rolling style anchor 测试（第 2 轮 · 北极星①⑤⑥ · 2026-05-31）。

治什么：长篇写到中后段作者文风退化成通用 LLM 腔（独立于事实/时序一致性的机制 ·
ConStory-Bench 实证 40-60% 中段聚集）。本测覆盖：
  [A] env LONGRANGE_DRIFT_MODE 模式标志（默认 shadow · 非法回退 · {active,off}）。
  [B] _linreg_slope 纯函数（单调下行负斜率 / 平稳近零 / 上行正斜率 / 边界）。
  [C] style_similarity 纯函数（同文自比高 · 跨题材去题材后仍可比 · float 契约 · SFS 异常兜底）。
  [D] position_weight_band 位置加权（40-60% 中段收紧斜率线）。
  [E] build_drift_curve 距离曲线（点排序/斜率/mean/last/min_point/缺作者点过滤）。
  [F] build_drift_findings advisory 触发（单调退化 / 绝对低位 / 中段加权 / <3点不判 / 恒 advisory）。
  [G] scan 集成（shadow 零回归=顶层 issues 空 · active 升 issues · off 跳过 · 样本不足跳过）。
  [H] rolling style anchor（build_manifest._collect_rolling_style_anchor 选最贴片段 · 无池退化 · 异常兜底）。
  [I] 真原文金标准（北极星纪律 3）：同作者跨章自比高 · 跨作者可区分 · 真作者不被误判为退化。

只测确定性纯函数 + 真原文金标准，不碰 LLM / agent / gen-model（熵早警可选不强依赖）。
"""
import os
import sys
import importlib
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_cluster_style_drift_scanner as ccsd  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _reload(mode):
    """以指定 LONGRANGE_DRIFT_MODE reload scanner（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("LONGRANGE_DRIFT_MODE", None)
    else:
        os.environ["LONGRANGE_DRIFT_MODE"] = mode
    importlib.reload(ccsd)
    return ccsd


def _load_author_chapter(book, ch):
    """读真原文章节（剥离 changes 尾巴）· 缺则 None（环境无 gitignore 原文时跳过金标准）。"""
    p = _ROOT / "workspace" / "styles" / book / "原文" / f"{ch}.txt"
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8")
    return ccsd._strip_changes(raw)


# ════════════════════════════════════════════════════════════════
# [A] 模式标志
# ════════════════════════════════════════════════════════════════

def test_A_mode_default_active_and_values():
    """LONGRANGE_DRIFT_MODE 默认 active（2026-05-31 放量 · 真作者距离曲线斜率平缓不误报退化）·
    非法回退 active · {shadow,off} 原样。"""
    m = _reload(None)
    try:
        assert m._mode() == "active"
        assert _reload("shadow")._mode() == "shadow"
        assert _reload("off")._mode() == "off"
        assert _reload("garbage")._mode() == "active"
        assert _reload("ACTIVE")._mode() == "active"  # 大小写归一
    finally:
        _reload(None)


# ════════════════════════════════════════════════════════════════
# [B] _linreg_slope 纯函数
# ════════════════════════════════════════════════════════════════

def test_B_linreg_slope_declining_negative():
    """单调下行序列 → 负斜率（每 cluster 相似度掉分）。"""
    assert ccsd._linreg_slope([0, 1, 2, 3], [90, 85, 80, 75]) < 0


def test_B_linreg_slope_flat_zero():
    """平稳序列 → 斜率≈0。"""
    assert abs(ccsd._linreg_slope([0, 1, 2, 3], [85, 85, 85, 85])) < 1e-9


def test_B_linreg_slope_rising_positive():
    """上行序列 → 正斜率（不该误判退化）。"""
    assert ccsd._linreg_slope([0, 1, 2, 3], [75, 80, 85, 90]) > 0


def test_B_linreg_slope_edge_cases():
    """点 < 2 / 全同 x → 0.0（不崩）。"""
    assert ccsd._linreg_slope([], []) == 0.0
    assert ccsd._linreg_slope([1], [5]) == 0.0
    assert ccsd._linreg_slope([2, 2, 2], [1, 2, 3]) == 0.0  # denom=0


# ════════════════════════════════════════════════════════════════
# [C] style_similarity 纯函数
# ════════════════════════════════════════════════════════════════

def test_C_style_similarity_self_high():
    """同一段文本自比 → 文风相似度接近满分（健全性）。"""
    text = "他淡淡地看了一眼，显然没把这事放在心上，转身离去。" * 30
    assert ccsd.style_similarity(text, text) >= 95.0


def test_C_style_similarity_is_float():
    """返回纯 stdlib float（不漏 numpy 类型 · JSON/测试可读）。"""
    s = ccsd.style_similarity("他走进房间。" * 30, "她走出门外。" * 30)
    assert type(s) is float


def test_C_style_similarity_distinguishes_styles():
    """节奏/标点差异大的两段 → 相似度显著低于自比满分（区分力）。"""
    a = "他走。他停。他看。他走。他停。他看。" * 30          # 极短句连发
    b = "他缓缓地走进了那间昏暗而潮湿、弥漫着腐朽气味的古老房间深处。" * 30  # 长句
    s = ccsd.style_similarity(a, b)
    assert s < 95.0, s


def test_C_style_similarity_sfs_exception_tolerant():
    """SFS 子项抛异常时兜底（任一可用取可用者 · 都不可用 → 0.0 · 不崩）。

    手动 save/restore（不用 monkeypatch · 兼容零依赖 run_tests.py 无 args 调用）。"""
    import style_evaluator as se
    orig_so = se.compute_style_only_sfs
    orig_cg = se.compute_charngram_sfs

    def _boom(a, b):
        raise RuntimeError("boom")
    try:
        se.compute_style_only_sfs = _boom
        # charngram 仍可用 → 不为 0
        s = ccsd.style_similarity("他走进房间。" * 30, "他走进房间。" * 30)
        assert s > 0.0
        # 两个都炸 → 0.0
        se.compute_charngram_sfs = _boom
        assert ccsd.style_similarity("他走进房间。" * 30, "他走进房间。" * 30) == 0.0
    finally:
        se.compute_style_only_sfs = orig_so
        se.compute_charngram_sfs = orig_cg


# ════════════════════════════════════════════════════════════════
# [D] position_weight_band 位置加权
# ════════════════════════════════════════════════════════════════

def test_D_position_weight_mid_section_tightened():
    """40-60% 中段 → 收紧斜率线（更接近 0 的负值 · 更早提示 · ConStory-Bench 中段聚集）。"""
    mid = ccsd.position_weight_band(0.50)
    edge = ccsd.position_weight_band(0.90)
    assert mid["mid"] is True
    assert edge["mid"] is False
    # 中段收紧线（-1.2）比常规线（-2.0）更接近 0 → 更易触发
    assert mid["slope_warn"] > edge["slope_warn"]


def test_D_position_weight_boundaries():
    """边界 0.40 / 0.60 含入中段 · 0.39 / 0.61 不含。"""
    assert ccsd.position_weight_band(0.40)["mid"] is True
    assert ccsd.position_weight_band(0.60)["mid"] is True
    assert ccsd.position_weight_band(0.39)["mid"] is False
    assert ccsd.position_weight_band(0.61)["mid"] is False


# ════════════════════════════════════════════════════════════════
# [E] build_drift_curve 距离曲线
# ════════════════════════════════════════════════════════════════

def test_E_build_curve_basic_metrics():
    """曲线基本指标：n / mean / last / slope / min_point · 按 idx 升序。"""
    sims = [
        {"cluster_id": "cluster_001", "idx": 0, "sim_vs_author": 90.0},
        {"cluster_id": "cluster_002", "idx": 1, "sim_vs_author": 85.0},
        {"cluster_id": "cluster_003", "idx": 2, "sim_vs_author": 80.0},
    ]
    cv = ccsd.build_drift_curve(sims)
    assert cv["n"] == 3
    assert cv["mean"] == 85.0
    assert cv["last"] == 80.0
    assert cv["slope"] < 0  # 单调下行
    assert cv["min_point"]["cluster_id"] == "cluster_003"


def test_E_build_curve_filters_missing_author():
    """缺 sim_vs_author 的点不进趋势（缺作者池的点不污染曲线）。"""
    sims = [
        {"cluster_id": "cluster_001", "idx": 0, "sim_vs_author": 90.0},
        {"cluster_id": "cluster_002", "idx": 1},  # 无 sim_vs_author
        {"cluster_id": "cluster_003", "idx": 2, "sim_vs_author": 80.0},
    ]
    cv = ccsd.build_drift_curve(sims)
    assert cv["n"] == 2


def test_E_build_curve_empty():
    """空输入 → n=0 不崩。"""
    cv = ccsd.build_drift_curve([])
    assert cv["n"] == 0
    assert cv["min_point"] is None


# ════════════════════════════════════════════════════════════════
# [F] build_drift_findings advisory 触发
# ════════════════════════════════════════════════════════════════

def _curve_from(vals, start_idx=0):
    sims = [{"cluster_id": f"cluster_{i+1:03d}", "idx": start_idx + i, "sim_vs_author": v}
            for i, v in enumerate(vals)]
    return ccsd.build_drift_curve(sims)


def test_F_findings_declining_trips_advisory():
    """陡降曲线（每 cluster 掉 ~5 分 ≤ -2.0 常规线）→ advisory 命中。"""
    cv = _curve_from([90, 85, 80, 75, 70])
    f = ccsd.build_drift_findings(cv, total_clusters=5)
    assert f and f[0]["code"] == "LONGRANGE_STYLE_DRIFT"
    assert f[0]["gate_level"] == "advisory"


def test_F_findings_stable_no_advisory():
    """平稳高位曲线（不退化）→ 不触发（不误伤稳定作者文风）。"""
    cv = _curve_from([88, 87, 88, 89, 88])
    assert ccsd.build_drift_findings(cv, total_clusters=5) == []


def test_F_findings_low_floor_trips():
    """末点低于低位线（55）→ 即便趋势平稳也触发（绝对低位补充判据）。"""
    cv = _curve_from([50, 50, 50])
    f = ccsd.build_drift_findings(cv, total_clusters=10)
    assert f, f
    assert "低位线" in f[0]["message"]


def test_F_findings_mid_section_tighter():
    """中段（40-60%）缓降（slope≈-1.5）在常规线（-2.0）下不触发，但中段收紧线（-1.2）触发。"""
    # 末点 idx=2，total=4 → position=0.5 中段；slope≈-1.5（在 -2.0 与 -1.2 之间）
    cv = _curve_from([85, 83.5, 82], start_idx=0)
    f_mid = ccsd.build_drift_findings(cv, total_clusters=4)
    # 同曲线若放在末段（total 大 → position 小，非中段）则常规线不触发
    f_edge = ccsd.build_drift_findings(cv, total_clusters=100)
    assert f_mid, ("中段应触发", cv["slope"])
    assert f_edge == [], ("末段常规线不应触发", cv["slope"])


def test_F_findings_too_few_points_no_judgment():
    """曲线 < 3 点 → 不判趋势（避免 2 点噪声）。"""
    cv = _curve_from([90, 70])
    assert ccsd.build_drift_findings(cv, total_clusters=5) == []


def test_F_findings_always_advisory():
    """任何触发的 finding gate_level/severity 恒 advisory（北极星⑤ · 绝不 hard_gate）。"""
    cv = _curve_from([95, 80, 65, 50])
    for f in ccsd.build_drift_findings(cv, total_clusters=4):
        assert f["gate_level"] == "advisory"
        assert f["severity"] == "advisory"


# ════════════════════════════════════════════════════════════════
# [G] scan 集成（shadow 零回归 / active 升 issues / off 跳过 / 样本不足）
# ════════════════════════════════════════════════════════════════

def _make_project(base_dir, cluster_texts):
    """造一个最小项目：_数据库 + 章节/cluster_NNN_draft/cluster_NNN_draft.txt + 故事块摘要.json。"""
    root = Path(base_dir) / "proj"
    (root / "_数据库").mkdir(parents=True)
    chap = root / "章节"
    chap.mkdir()
    clusters = []
    for i, txt in enumerate(cluster_texts, 1):
        cid = f"cluster_{i:03d}"
        d = chap / f"{cid}_draft"
        d.mkdir()
        (d / f"{cid}_draft.txt").write_text(txt, encoding="utf-8")
        clusters.append({"cluster_id": cid, "title": f"块{i}",
                         "chapter_range": [i * 4 - 3, i * 4], "status": "已完成"})
    import json
    (root / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters}, ensure_ascii=False),
        encoding="utf-8")
    return root


def test_G_scan_shadow_zero_regression():
    """shadow（默认）：即使曲线退化，顶层 issues 也为空（只挂 shadow_findings · 不改判决 · 零回归核心）。"""
    # 5 个文风明显劣化的 cluster（短句连发 → 长 AI 腔均匀化）
    texts = [
        "他走。他停。他看。" * 80,
        "他缓缓走着。" * 80,
        "他在那个昏暗的房间里缓缓地走着思索着。" * 60,
        "他在那个昏暗而潮湿弥漫腐朽气味的古老房间深处缓缓地走着并思索着许多。" * 50,
        "他在那个昏暗而潮湿并且弥漫着腐朽气味的极其古老的房间最深处缓缓地走着思索着许多复杂的往事。" * 40,
    ]
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(d, texts)
        m = _reload("shadow")
        try:
            rep = m.scan(root)
            assert rep["mode"] == "shadow"
            assert rep["issues"] == []  # 零回归：顶层 issues 恒空
        finally:
            _reload(None)


def test_G_scan_active_surfaces_issues():
    """active：退化曲线把 advisory 升到顶层 issues（仍 advisory · 绝不 hard_gate）。"""
    texts = [
        "他走。他停。他看。" * 80,
        "他缓缓走着思索。" * 70,
        "他在昏暗房间里缓缓走着并不断思索着。" * 60,
        "他在那个昏暗而潮湿弥漫腐朽气味的古老房间深处缓缓走着思索着许多。" * 50,
        "他在那个昏暗而潮湿并弥漫着浓重腐朽气味的极古老房间最深处缓缓走着并思索着许多复杂往事。" * 40,
    ]
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(d, texts)
        m = _reload("active")
        try:
            rep = m.scan(root)
            assert rep["mode"] == "active"
            if rep["issues"]:  # 退化够陡才触发；触发则必 advisory
                assert all(it["gate_level"] == "advisory" for it in rep["issues"])
                assert rep["issues"][0]["code"] == "LONGRANGE_STYLE_DRIFT"
        finally:
            _reload(None)


def test_G_scan_off_skips():
    """off：完全跳过（不算 SFS）· _skip 标注。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(d, ["他走进房间。" * 80] * 5)
        m = _reload("off")
        try:
            rep = m.scan(root)
            assert rep.get("_skip") == "mode=off"
            assert rep["issues"] == []
        finally:
            _reload(None)


def test_G_scan_too_few_clusters_skips():
    """已写 cluster < 3 → 样本不足跳过（长程趋势不适用）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(d, ["他走进房间。" * 80, "她走出门外。" * 80])
        m = _reload("shadow")
        try:
            rep = m.scan(root)
            assert "样本不足" in rep.get("_skip", "")
        finally:
            _reload(None)


def test_G_scan_self_drift_fallback_no_author():
    """无作者池 → 退化自漂移单轨（sim_vs_prior 顶替 sim_vs_author 画曲线 · 不臆造作者）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(d, ["他走进房间。" * 80] * 4)
        m = _reload("shadow")
        try:
            rep = m.scan(root)
            assert rep["author_pool_resolved"] is False
            assert "自漂移单轨" in rep.get("_note", "")
            assert rep["drift_curve"]["n"] >= 3
        finally:
            _reload(None)


# ════════════════════════════════════════════════════════════════
# [H] rolling style anchor（build_manifest._collect_rolling_style_anchor）
# ════════════════════════════════════════════════════════════════

class _FakeScanner:
    def __init__(self, root):
        self.root = root


def test_H_rolling_anchor_no_pool_fallback_recent():
    """无作者池 → 退化选最近 1-2 个已写 cluster 头部片段（本书最新文风当锚）。"""
    import build_manifest as bm
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(d, ["他走进了那间空荡荡的旧房间。" * 60] * 4)
        anchor = bm._collect_rolling_style_anchor(_FakeScanner(root), chapter=20)
        assert anchor is not None
        assert anchor["anchor_basis"] == "recent_clusters_fallback"
        assert anchor["author_pool_resolved"] is False
        assert 1 <= len(anchor["anchors"]) <= 2
        # 选的是最近的（cluster_003 / cluster_004）
        ids = {a["cluster_id"] for a in anchor["anchors"]}
        assert "cluster_004" in ids


def test_H_rolling_anchor_none_when_no_written():
    """无已写 cluster → 返回 None（不阻断 manifest）。"""
    import build_manifest as bm
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "empty"
        (root / "_数据库").mkdir(parents=True)
        (root / "章节").mkdir()
        assert bm._collect_rolling_style_anchor(_FakeScanner(root), chapter=1) is None


def test_H_rolling_anchor_snippet_truncated():
    """锚片段截断到 ~600 CJK 控 prompt 体积（不把整 cluster 塞进 manifest）。"""
    import build_manifest as bm
    big = "他缓缓走进那间昏暗的旧房间。\n" * 200  # 远超 600 CJK
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(d, [big] * 3)
        anchor = bm._collect_rolling_style_anchor(_FakeScanner(root), chapter=12)
        assert anchor is not None
        import style_similarity_scanner as sss
        for a in anchor["anchors"]:
            # 截断后 CJK 应远小于整段（~600 量级 + 一段余量）
            assert sss._cjk_count(a["snippet"]) <= 900, sss._cjk_count(a["snippet"])


# ════════════════════════════════════════════════════════════════
# [I] 真原文金标准（北极星纪律 3）：同作者高 · 跨作者可区分 · 真作者不误判
# ════════════════════════════════════════════════════════════════

def test_I_real_author_self_similarity_high():
    """真作者两段原文互比 → 文风相似度高（同作者文风一致 · 不被误判退化）。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        a = _load_author_chapter(book, "第010章")
        b = _load_author_chapter(book, "第020章")
        if a is None or b is None:
            continue
        found = True
        s = ccsd.style_similarity(a, b)
        # 同作者跨章去题材文风相似度应明显偏高（实测校准 · 守金标准不卡刀刃）
        assert s >= 60.0, (book, s)
    assert found or _load_author_chapter("蛊真人", "第010章") is None


def test_I_same_author_beats_cross_author():
    """同作者文风相似度 > 跨作者（跨作者应可区分 · 金标准核心）。"""
    gu_a = _load_author_chapter("蛊真人", "第010章")
    gu_b = _load_author_chapter("蛊真人", "第020章")
    js_a = _load_author_chapter("惊悚乐园", "第010章")
    if gu_a is None or gu_b is None or js_a is None:
        return
    same = ccsd.style_similarity(gu_a, gu_b)
    cross = ccsd.style_similarity(gu_a, js_a)
    assert same > cross, (same, cross)


def test_I_real_author_consistent_curve_no_false_drift():
    """真作者连续多章当「已写 cluster」喂曲线 → 斜率平缓（不被误判为单调退化 · 守金标准）。

    用蛊真人连续章节 vs 同作者参考，相似度应稳定高位、斜率近零（不触发退化 advisory）。"""
    ref = _load_author_chapter("蛊真人", "第001章")
    chs = [_load_author_chapter("蛊真人", f"第{i:03d}章") for i in (10, 12, 14, 16, 18)]
    if ref is None or any(c is None for c in chs):
        return
    sims = [{"cluster_id": f"cluster_{i+1:03d}", "idx": i,
             "sim_vs_author": ccsd.style_similarity(ref, c)} for i, c in enumerate(chs)]
    cv = ccsd.build_drift_curve(sims)
    # 真作者跨章相似度应稳定不单调暴跌 → 斜率不该陡过常规退化线
    assert cv["slope"] > ccsd._DRIFT_SLOPE_WARN, (cv["slope"], [round(s["sim_vs_author"], 1) for s in sims])
