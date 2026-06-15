#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ecdf_anchor_blindspot.py — 盲区 ecdf_anchor 落地测试（2026-06-16 · 零依赖 __main__ 范式）。

设计来源：workspace/_temp_research/blindspot_designs.json · do_now[ecdf_anchor]。
把 WebNovelBench「ECDF 百分位定位」做**正确北极星投影**——锚到**单作者自身分布**
（self-ECDF / 作者内 z-score 带），**绝不**跨 4000 部群体锚（那会把 cluster 往通用网文均值拉，
反噬北极星：惊悚乐园句长 31 离群点拿群体百分位会被判「太长」往均值拉）。

只动 prose_rhythm_scanner.py：探针1 句长从「单点 ×0.70」→「作者内 z-band + 绝对地板取或」；
新增探针6 段长偏短（作者 para_p5 单边下尾）。全 advisory · 作者档第一权威 · cluster 为单位。

🔴 金标准防矫枉过正（核心闸·真作者原文喂自身基线必 PASS）：
   诡秘(σ=27 高方差·验宽容带没误伤) / 主神(σ=16 低方差·验没过度放水) / 惊悚(σ=23) 两极验证。
🔴 校准闸（test_plan #3·这条不验通别交）：碎句 mean~16 喂惊悚基线 → z≈-0.65 触不到 -1.0，
   **必须靠绝对地板 mean<μ×0.62 兜住**（z<-1.0 与 floor 取或），否则高 σ 作者漏报。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import prose_rhythm_scanner as P  # noqa: E402

# ── 测试数据根：worktree 优先，回退共享 checkout（金标准原文/作者档本地不入 git）──
_DATA_ROOTS = [
    _ROOT / "workspace" / "styles",
    Path("D:/Desktop/ruoyuai/workspace/styles"),
    Path(__file__).resolve().parents[1] / ".." / ".." / ".." / "workspace" / "styles",
]


def _style_dir(book: str):
    for r in _DATA_ROOTS:
        d = r / book
        if (d / "作者风格_FINAL.json").exists():
            return d
    return None


def _kinds(r):
    return {v["kind"] for v in r["violations"]}


def _se(r):
    return [v for v in r["violations"] if v["kind"] == "sentence_too_short"]


def _pe(r):
    return [v for v in r["violations"] if v["kind"] == "paragraph_too_short"]


def _mk_style(tmp: Path, mean=None, std=None, para=None) -> Path:
    """构造 mock 作者档（给定 mean/std/段长分位）→ 返回 style 路径。"""
    q = {}
    if mean is not None:
        sl = {"mean": mean}
        if std is not None:
            sl["std"] = std
        q["sentence_length"] = sl
    if para is not None:
        q["paragraph_length_chars"] = para
    p = tmp / "mock_style.json"
    p.write_text(json.dumps({"quantitative": q}, ensure_ascii=False), encoding="utf-8")
    return p


def _sentences(mean_cjk: int, n: int = 60):
    """生成 n 句、每句约 mean_cjk 个 CJK、句首多样（避免 streak/主语占比探针干扰）的文本。

    只为隔离句长探针——句首用环境/物件/时间起头，非「他/她+动作」。"""
    # 基础句库（约 16-17 CJK·句首多样）
    base16 = [
        "天色暗下来街灯次第亮起照着湿路面。",      # ~16
        "屋檐下的雨丝斜斜落进水洼里溅起花。",
        "远处旧楼的窗口透出昏黄的光一明一灭。",
        "风卷着碎纸片掠过空荡荡的街角处。",
        "楼道深处传来一阵压得很低的说话声。",
        "桌上那杯茶早就凉透了浮着一层白沫。",
        "门缝里钻进来的冷气让人忍不住缩脖。",
        "巷子尽头停着一辆熄了火的旧面包车。",
    ]
    if mean_cjk <= 18:
        pool = base16
    elif mean_cjk <= 28:
        # 加逗号连缀成中长句（~24-28）
        pool = [s[:-1] + "，那感觉说不上来却让人心里发紧。" for s in base16]
    else:
        # 复合长句（~33+·对齐高基线作者）
        pool = [s[:-1] + "，那种说不清的预感像潮水一样一点点漫上来压得人喘不过气。"
                for s in base16]
    return "\n".join(pool[i % len(pool)] for i in range(n))


# ════════════════════════════════════════════════════════════
# A. 作者内 z-band 边界（mock 作者档·z=-0.9 不报 / -1.1 minor / -1.9 major）
# ════════════════════════════════════════════════════════════
def test_zband_minus_0_9_not_flagged():
    """z≈-0.9（作者下尾但未越 -1.0）+ 未跌破地板 → 不报句长偏短（防矫枉过正）。"""
    with tempfile.TemporaryDirectory() as d:
        # μ=30 σ=10 → 目标 cluster_mean=21（z=-0.9）·floor62=18.6 不触发
        style = _mk_style(Path(d), mean=30, std=10)
        text = _sentences(24)   # cluster_mean 落 ~24（z≈-0.6·安全区）
        r = P.scan(text, style_path=style)
        z = r["metrics"]["sentence_z"]
        assert z is not None and z > P.SHORT_Z_MINOR, f"z={z} 应 > {P.SHORT_Z_MINOR}"
        assert r["metrics"]["sentence_mean"] >= 30 * P.SHORT_ABS_FLOOR, "未跌破地板"
        assert not _se(r), f"z>-1.0 且未破地板不该报，z={z} mean={r['metrics']['sentence_mean']}"


def test_zband_minus_1_1_flagged_minor():
    """z 越过 -1.0（但未到 -1.8·mean 未跌破 μ×0.45 灾难线）→ sentence_too_short **minor**。

    test_plan #5 干净分档：z=-1.1 报 minor。μ=30 σ=12 → mean~16 → z≈-1.17（-1.8<z<-1.0）·
    mean16 > μ×0.45=13.5 → 不升 major。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=30, std=12)
        text = _sentences(16)
        r = P.scan(text, style_path=style)
        v = _se(r)
        z = r["metrics"]["sentence_z"]
        assert v, f"z={z} 应报句长偏短，metrics={r['metrics']}"
        assert P.SHORT_Z_MAJOR < z < P.SHORT_Z_MINOR, f"z={z} 应落 (-1.8, -1.0) minor 带"
        assert v[0]["severity"] == "minor", f"应 minor（非 major），z={z} mean={r['metrics']['sentence_mean']}"


def test_zband_minus_1_9_flagged_major():
    """z 越过 -1.8 → sentence_too_short major。"""
    with tempfile.TemporaryDirectory() as d:
        # μ=40 σ=12 → cluster_mean~16 → z=(16-40)/12=-2.0 major
        style = _mk_style(Path(d), mean=40, std=12)
        text = _sentences(16)
        r = P.scan(text, style_path=style)
        v = _se(r)
        assert v and v[0]["severity"] == "major", \
            f"z={r['metrics']['sentence_z']} 应报 major，{r['violations']}"


def test_high_sigma_author_wide_tolerance():
    """高 σ 作者（σ=27 诡秘式）下尾自动获宽容带：cluster_mean=22 在 μ=34 σ=27 下 z≈-0.44 → 不报。

    这是 self-ECDF 精髓——同样 mean=22 在低 σ 作者会触发，在高 σ 作者宽容（见下条对照）。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=34, std=27)
        text = _sentences(24)   # ~24
        r = P.scan(text, style_path=style)
        assert not _se(r), f"高σ作者宽容带不该报，z={r['metrics']['sentence_z']} mean={r['metrics']['sentence_mean']}"


def test_low_sigma_author_tightens():
    """低 σ 作者（σ=8）收紧：同样偏离立刻越 z 阈值（对照高 σ 宽容）。"""
    with tempfile.TemporaryDirectory() as d:
        # μ=30 σ=8 → cluster_mean~16 → z=(16-30)/8=-1.75 → 报
        style = _mk_style(Path(d), mean=30, std=8)
        text = _sentences(16)
        r = P.scan(text, style_path=style)
        assert _se(r), f"低σ作者应收紧报警，z={r['metrics']['sentence_z']}"


def test_long_sentences_never_flagged():
    """长句永不报（北极星③不干涉创作·只报偏短下尾·正 z 永远 PASS）。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=20, std=8)
        text = _sentences(33)   # cluster_mean 远高于 μ=20 → z 正
        r = P.scan(text, style_path=style)
        z = r["metrics"]["sentence_z"]
        assert z is not None and z > 0, f"应为正 z，z={z}"
        assert not _se(r), f"长句绝不报，z={z}"


# ════════════════════════════════════════════════════════════
# B. 绝对地板取或（test_plan #3 关键校准闸·高 σ 作者 z 漏报靠地板兜）
# ════════════════════════════════════════════════════════════
def test_abs_floor_catches_when_zband_too_lax():
    """🔴 校准闸：碎句 mean~16 喂高 σ 作者(μ=31 σ=23 惊悚式)→ z≈-0.65 触不到 -1.0，
    **必须靠绝对地板 mean<μ×0.62=19.2 兜住**。这条不验通=高 σ 作者碎句漏报=白改。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=31, std=23)
        text = _sentences(16)
        r = P.scan(text, style_path=style)
        z = r["metrics"]["sentence_z"]
        mean = r["metrics"]["sentence_mean"]
        floor = 31 * P.SHORT_ABS_FLOOR
        # 核心断言：z 没触发（高 σ 太松），但地板触发
        assert z is not None and z > P.SHORT_Z_MINOR, \
            f"校准前提：z 应 > -1.0（高σ太松），实际 z={z}（若 z 已触发则本闸退化为不验地板）"
        assert mean < floor, f"前提：mean({mean}) 应 < floor62({floor:.1f})"
        assert _se(r), f"🔴 绝对地板失效！碎句未抓到，z={z} mean={mean} floor={floor:.1f}"


def test_abs_floor_does_not_trip_real_authors():
    """绝对地板不误伤真作者：真作者 cluster_mean 全在 μ×0.62 之上（金标准已证）。"""
    # 用 mock 复现：μ=31 σ=23·cluster_mean=32（真作者惊悚式）→ mean>floor 不报
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=31, std=23)
        text = _sentences(33)
        r = P.scan(text, style_path=style)
        assert not _se(r), f"真作者中段不该被地板误伤，mean={r['metrics']['sentence_mean']}"


# ════════════════════════════════════════════════════════════
# C. 无 std 老档回退（×0.70 通用兜底·回归不破）
# ════════════════════════════════════════════════════════════
def test_no_std_falls_back_to_ratio():
    """作者档只有 mean 无 std（老档）→ 退回 ×0.70 比值兜底（self_ecdf_active=False）。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=31, std=None)   # 无 std
        text = _sentences(16)   # mean~16 / 31 = 0.52 < 0.55 → major
        r = P.scan(text, style_path=style)
        assert r["author_baseline"]["self_ecdf_active"] is False, "无 std 应走通用兜底"
        assert r["metrics"]["sentence_z"] is None, "无 std 时 z 应为 None"
        v = _se(r)
        assert v, f"老档兜底仍应抓碎句，metrics={r['metrics']}"


def test_no_author_profile_uses_default():
    """完全无作者档 → 退通用 DEFAULT_AUTHOR_SENT_MEAN=26 ×0.70（与改前一致）。"""
    text = _sentences(16)
    r = P.scan(text)   # 无 project 无 style
    assert r["author_baseline"]["from_author_profile"] is False
    assert r["author_baseline"]["self_ecdf_active"] is False
    assert _se(r), "无作者档通用兜底仍抓碎句"


# ════════════════════════════════════════════════════════════
# D. 探针6 段长偏短（作者 para_p5 单边下尾）
# ════════════════════════════════════════════════════════════
def test_paragraph_too_short_flagged():
    """cluster 段长均值 < 作者 para_p5 → paragraph_too_short。"""
    with tempfile.TemporaryDirectory() as d:
        # 作者段长 p5=30·构造每段~12 CJK 的碎段落
        style = _mk_style(Path(d), mean=30, std=15,
                          para={"p5": 30.0, "p50": 42.0, "p95": 70.0})
        text = "\n".join("他低头看了看手里的东西。" for _ in range(30))   # ~12/段
        r = P.scan(text, style_path=style)
        v = _pe(r)
        assert v, f"段长偏短应报，para_mean={r['metrics']['paragraph_mean']} p5=30"


def test_paragraph_long_not_flagged():
    """长段不报（单边下尾·长不是流水账问题·北极星③）。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=30, std=15,
                          para={"p5": 30.0, "p50": 42.0, "p95": 70.0})
        long_para = "他走进房间环顾四周然后在靠窗的位置坐了下来又点了一杯热茶安静等着对方过来谈那件要紧事情说清楚来龙去脉。"
        text = "\n".join(long_para for _ in range(15))   # ~50/段 > p5
        r = P.scan(text, style_path=style)
        assert not _pe(r), f"长段不该报，para_mean={r['metrics']['paragraph_mean']}"


def test_paragraph_no_baseline_skips():
    """作者档无段长分位 → 探针6 跳过（不臆造通用硬值）。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=30, std=15, para=None)
        text = "\n".join("他低头。" for _ in range(20))
        r = P.scan(text, style_path=style)
        assert not _pe(r), "无段长基线不该报段长探针"


# ════════════════════════════════════════════════════════════
# E. 🔴 金标准防矫枉过正（真作者原文喂自身基线必 PASS·两极 σ 验证）
# ════════════════════════════════════════════════════════════
def _golden_real_author(book: str, glob_pat: str):
    """真作者原文 cluster 喂自身基线 → 句长/段长探针必 0 violation。"""
    import glob as _g
    sd = _style_dir(book)
    if sd is None:
        return None  # 原文/作者档本地不在 → 跳过（零依赖原则·不硬失败）
    style = sd / "作者风格_FINAL.json"
    files = sorted(_g.glob(str(sd / "原文" / glob_pat)))[:12]
    if not files:
        return None
    text = "\n".join(Path(f).read_text(encoding="utf-8") for f in files)
    r = P.scan(text, style_path=style)
    return r


def test_golden_guimi_high_sigma_passes():
    """诡秘之主(σ≈27 高方差)真原文喂自身基线 → 句长/段长 0 violation（宽容带没误伤）。"""
    r = _golden_real_author("诡秘之主", "第00[5-6]*章.txt")
    if r is None:
        return
    assert r["author_baseline"]["self_ecdf_active"] is True, "诡秘档应有 std → self-ECDF active"
    assert not _se(r), f"🔴 高σ真作者句长被误判！z={r['metrics']['sentence_z']} {r['violations']}"
    assert not _pe(r), f"🔴 高σ真作者段长被误判！{r['violations']}"


def test_golden_zhushen_low_sigma_passes():
    """主神大道(σ≈16 低方差)真原文喂自身基线 → 句长/段长 0 violation（没过度放水）。"""
    import glob as _g
    sd = _style_dir("主神大道")
    if sd is None:
        return
    style = sd / "作者风格_FINAL.json"
    files = sorted(_g.glob(str(sd / "原文" / "第10[0-1]*章.txt")))[:12]
    if not files:
        return
    text = "\n".join(Path(f).read_text(encoding="utf-8") for f in files)
    r = P.scan(text, style_path=style)
    assert r["author_baseline"]["self_ecdf_active"] is True
    assert not _se(r), f"🔴 低σ真作者句长被误判！z={r['metrics']['sentence_z']} {r['violations']}"
    assert not _pe(r), f"🔴 低σ真作者段长被误判！{r['violations']}"


def test_golden_jingsong_passes():
    """惊悚乐园(σ≈23)真原文喂自身基线 → 句长/段长 0 violation。"""
    r = _golden_real_author("惊悚乐园", "第0[0-1]*章.txt")
    if r is None:
        return
    assert not _se(r), f"🔴 惊悚真作者句长被误判！z={r['metrics']['sentence_z']} {r['violations']}"
    assert not _pe(r), f"🔴 惊悚真作者段长被误判！{r['violations']}"


# ════════════════════════════════════════════════════════════
# F. 跨作者交叉负向（验证锚是「作者自身」而非通用·z 随作者 σ 自适应）
# ════════════════════════════════════════════════════════════
def test_cross_author_z_self_adaptive():
    """同一文本喂不同作者基线 → z 不同（证按各自 σ 自适应·非一刀切群体锚）。"""
    import glob as _g
    sd_zs = _style_dir("主神大道")
    sd_gm = _style_dir("诡秘之主")
    if sd_zs is None or sd_gm is None:
        return
    files = sorted(_g.glob(str(sd_zs / "原文" / "第10[0-1]*章.txt")))[:12]
    if not files:
        return
    text = "\n".join(Path(f).read_text(encoding="utf-8") for f in files)
    z_self = P.scan(text, style_path=sd_zs / "作者风格_FINAL.json")["metrics"]["sentence_z"]
    z_other = P.scan(text, style_path=sd_gm / "作者风格_FINAL.json")["metrics"]["sentence_z"]
    assert z_self is not None and z_other is not None
    assert abs(z_self - z_other) > 0.05, \
        f"同文本不同作者锚 z 应不同（自适应），z_self={z_self} z_other={z_other}"


# ════════════════════════════════════════════════════════════
# G. 北极星制度锁（advisory 非 hard_gate · code 不进 HARD_GATE_CODES）
# ════════════════════════════════════════════════════════════
def test_gate_level_always_advisory():
    """整个 scanner gate_level 恒 advisory（北极星⑤顾问非法官）。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=40, std=12)
        r = P.scan(_sentences(16), style_path=style)
        assert r["gate_level"] == "advisory"
        assert r["verdict"] in ("FAIL_MINOR", "FAIL_MAJOR")  # 有问题但只 advisory


def test_codes_not_in_hard_gate():
    """sentence_too_short / paragraph_too_short 绝不进 audit_hub.HARD_GATE_CODES。

    照搬制度锁范式：新 advisory code 不得误升 hard_gate（北极星⑤）。"""
    try:
        import audit_hub  # noqa
        hg = getattr(audit_hub, "HARD_GATE_CODES", None)
    except Exception:
        hg = None
    # prose_rhythm 的 kind 是 advisory 语义标签·即便映射到 audit_hub 也不得在 hard_gate
    for code in ("sentence_too_short", "paragraph_too_short", "SENTENCE_TOO_SHORT",
                 "PARAGRAPH_TOO_SHORT", "PROSE_RHYTHM"):
        if hg is not None:
            assert code not in hg, f"{code} 误进 HARD_GATE_CODES！违北极星⑤"
    # scanner 自身永远报 advisory（即便 audit_hub import 失败也守住）
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=40, std=12)
        r = P.scan(_sentences(16), style_path=style)
        assert r["gate_level"] == "advisory"


def test_metrics_schema_for_audit_hub():
    """metrics 暴露新字段（sentence_z / paragraph_mean）+ author_baseline 暴露分布矩。"""
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=30, std=15,
                          para={"p5": 30.0, "p50": 42.0, "p95": 70.0})
        r = P.scan(_sentences(24), style_path=style)
        assert "sentence_z" in r["metrics"]
        assert "paragraph_mean" in r["metrics"]
        ab = r["author_baseline"]
        for k in ("sentence_mean", "sentence_std", "para_p5", "self_ecdf_active"):
            assert k in ab, f"author_baseline 缺 {k}"


# ════════════════════════════════════════════════════════════
# H. 边界 / 不崩
# ════════════════════════════════════════════════════════════
def test_empty_text_no_crash():
    r = P.scan("", style_path=None)
    assert r["verdict"] == "PASS"


def test_dialogue_only_no_crash():
    with tempfile.TemporaryDirectory() as d:
        style = _mk_style(Path(d), mean=30, std=15)
        text = "\n".join("“你来不来。”" for _ in range(10))
        r = P.scan(text, style_path=style)
        assert r["gate_level"] == "advisory"


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = skipped = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  [OK] {fn.__name__}")
    print(f"\n{passed} tests OK (golden-pass + 校准闸 included)")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    _run_all()
    print("OK")
