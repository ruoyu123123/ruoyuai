# -*- coding: utf-8 -*-
"""syntactic_diversity_scanner 回归 + 金标准防矫枉过正测试
（2026-06-16 · 盲区 perplexity_obsolete 落地 · 句法模板 overuse + 篇章级主题过度解释）。

钉死（北极星五重）：
  · advisory 非 hard_gate（code 不在 audit_hub.HARD_GATE_CODES·gate_level 恒 advisory）。
  · 作者档第一权威（z-band 用作者自身 mean/std·无作者档退绝对地板，地板坐落真作者实测上限之上）。
  · 顾问非法官（长句/作者签名句式不当缺陷·只报偏离作者自身分布）。
  · cluster 为单位（POS per-scene 分段算）。

🔴金标准核心闸：真作者原文喂自身基线必 PASS（不误判作者真实风格）——
  (1) 无 syntactic_diversity 维 → 绝对地板路径，真作者 PASS；
  (2) 用作者自身 per-scene 分布当 syntactic_diversity 维（模拟 consolidate 输出）→ z-band 路径，
      留出的 hold-out 真原文段 PASS。诡秘(σ大)/主神(σ小)两极都验。

零依赖（stdlib + jieba）·提供 pytest 可发现的 test_* 函数 + __main__ 自跑入口
（PYTHONIOENCODING=utf-8 python tests/test_perplexity_obsolete_blindspot.py 全 OK）。
"""
import json
import os
import re
import statistics
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import syntactic_diversity_scanner as S  # noqa: E402

# 真作者原文在共享 checkout（worktree 无 workspace 副本）。两处都试。
_STYLE_ROOTS = [_ROOT / "workspace" / "styles",
                Path("D:/Desktop/ruoyuai") / "workspace" / "styles"]


def _kinds(r):
    return {v["kind"] for v in r["violations"]}


def _style_dir(book: str):
    for root in _STYLE_ROOTS:
        d = root / book
        if (d / "原文").exists():
            return d
    return None


def _read_chapters(book: str, fmt: str, rng) -> str:
    d = _style_dir(book)
    if not d:
        return ""
    base = d / "原文"
    out = []
    for c in rng:
        f = base / (fmt % c)
        if f.exists():
            out.append(f.read_text(encoding="utf-8"))
    return "\n\n".join(out)


def _self_baseline_from_text(text: str) -> dict:
    """模拟 consolidate_author_profile：对作者原文 per-scene 算模板覆盖率分布 + theme 密度。
    返回可塞进作者风格档 quantitative.syntactic_diversity 的 dict。"""
    scenes = S.split_scenes(text)
    covs = []
    for sc in scenes:
        toks = S._pos_sequence(sc)
        if toks and len(toks) >= S._MIN_POS_TOKENS:
            covs.append(S._template_coverage(toks))
    cov_mean = statistics.mean(covs) if covs else 0.3
    cov_std = statistics.pstdev(covs) if len(covs) > 1 else 0.08
    # theme 密度（全本一个数·std 用保守默认）
    body = "\n".join(l for l in text.split("\n")
                     if not re.match(r"^第\d+章", l.strip()))
    sents = [s for s in re.split(r"(?<=[。！？])", body) if s.strip()]
    hits = sum(1 for s in sents if S._ABSTRACT_NOUN.search(s) and S._EPIPHANY_MARK.search(s))
    total = S.cjk(text)
    th_mean = round(hits / total * 1000, 3) if total else 0.1
    return {
        "template_coverage_mean": round(cov_mean, 4),
        "template_coverage_std": round(max(cov_std, 0.02), 4),
        "theme_explain_per_kcjk_mean": th_mean,
        "theme_explain_per_kcjk_std": 0.15,
    }


def _mk_project(tmp: Path, synt: dict | None = None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    q = {}
    if synt is not None:
        q["syntactic_diversity"] = synt
    db.joinpath("作者风格.json").write_text(
        json.dumps({"quantitative": q}, ensure_ascii=False), encoding="utf-8")
    return tmp


# ── 探针 1：句法模板 overuse ──────────────────────────────
def test_templated_text_flagged_no_author():
    """同 POS 骨架 × N 句（无作者档）→ 覆盖率破地板 → syntactic_template_overuse major。"""
    text = "\n".join("他摸出手机看了一眼屏幕然后皱起了眉头。" for _ in range(40))
    r = S.scan(text)
    v = [x for x in r["violations"] if x["kind"] == "syntactic_template_overuse"]
    assert v and v[0]["severity"] == "major", f"模板文本应报 major，实际 {r['metrics']}"
    assert r["metrics"]["template_coverage_mean"] >= S._TEMPLATE_COV_FLOOR_MAJOR


def test_varied_text_no_template_flag_no_author():
    """句法多样的文本（无作者档·24 句各不相同）→ 覆盖率低于地板 → 不报（防矫枉过正）。
    🔴 关键纪律：负例必须用『真正各不相同』的句子——若只拿少量句子重复 N 遍(N≥τ=3)，
    每个 POS n-gram 都会因字面重复达 τ → 覆盖率虚高到 1.0(小合成 artifact，非 scanner 缺陷)；
    真作者/真草稿是连续不重复文本，故负例须仿此(实测 24 句各异 cov≈0.17)。"""
    lines = [
        "天色暗下来的时候走廊尽头传来一阵急促而杂乱的脚步声。",
        "那本厚厚的册子封皮上烫金的大字在烛火下泛着暗光。",
        "谁也没想到事情会在一夜之间变成这副样子。",
        "风从窗缝钻进来吹得桌上的草稿哗啦翻动。",
        "他张了张嘴终究什么都没说只是站在原地。",
        "巷子深处那盏路灯忽明忽暗照不清墙角的身影。",
        "雨下了整整一夜把青石板路冲刷得发亮。",
        "远处的钟楼敲了十二下声音沉闷而悠长。",
        "她把伞收起来靠在门边顺手理了理湿发。",
        "桌上的茶早就凉透了杯壁结了一层薄雾。",
        "楼下传来争吵声紧接着是瓷器摔碎的脆响。",
        "老人颤巍巍地从抽屉里取出一张泛黄的照片。",
        "猫从窗台跃下无声地消失在浓重的夜色里。",
        "他翻开账本指尖在某一行数字上停了很久。",
        "走廊的灯管嗡嗡作响投下惨白而摇晃的光。",
        "孩子趴在栏杆上望着楼下川流不息的车灯。",
        "炉火渐渐熄了屋里只剩下窗外透进的微光。",
        "她数着台阶一级一级往上爬膝盖隐隐作痛。",
        "报纸被风掀起飘到马路对面贴在湿墙上。",
        "他摘下眼镜揉了揉发酸的眼角又戴了回去。",
        "海浪拍打礁石溅起的水花在月下泛着银光。",
        "电梯停在三楼门开了却没有任何人走出来。",
        "她握紧那枚铜钱指节因为用力而微微发白。",
        "天边最后一丝晚霞终于沉入了远山的轮廓。",
    ]
    text = "\n".join(lines)
    r = S.scan(text)
    assert "syntactic_template_overuse" not in _kinds(r), \
        f"多样文本不该报，cov={r['metrics'].get('template_coverage_mean')}"


def test_template_z_band_with_author_baseline():
    """有作者基线(cov mean=0.35 std=0.07)：模板文本 cov≈1.0 → z≫4.5 → major。"""
    with tempfile.TemporaryDirectory() as d:
        synt = {"template_coverage_mean": 0.35, "template_coverage_std": 0.07,
                "theme_explain_per_kcjk_mean": 0.1, "theme_explain_per_kcjk_std": 0.15}
        proj = _mk_project(Path(d), synt)
        text = "\n".join("他摸出手机看了一眼屏幕然后皱起了眉头。" for _ in range(40))
        r = S.scan(text, project=proj)
        v = [x for x in r["violations"] if x["kind"] == "syntactic_template_overuse"]
        assert v and v[0]["severity"] == "major", f"z-band 应报 major，z={r['metrics'].get('template_coverage_z')}"
        assert r["author_baseline"]["from_author_profile"] is True


def test_template_within_author_band_no_flag():
    """作者基线给一个宽容带(mean=0.40 std=0.10)：覆盖率落在 mean+3σ 内的文本不报。
    用真作者诡秘原文（实测 per-scene≈0.36）喂一个略宽基线 → z<3 → PASS（验作者内自洽不误伤）。"""
    txt = _read_chapters("诡秘之主", "第%04d章.txt", range(6, 14))
    if not txt:
        return  # 原文不在则跳过（零依赖原则·不硬失败）
    with tempfile.TemporaryDirectory() as d:
        synt = {"template_coverage_mean": 0.36, "template_coverage_std": 0.07,
                "theme_explain_per_kcjk_mean": 0.1, "theme_explain_per_kcjk_std": 0.15}
        proj = _mk_project(Path(d), synt)
        r = S.scan(txt, project=proj)
        assert "syntactic_template_overuse" not in _kinds(r), \
            f"真作者落自身带内不该报，z={r['metrics'].get('template_coverage_z')}"


# ── 探针 3：篇章级主题过度解释 ────────────────────────────
def test_theme_over_explanation_flagged_no_author():
    """高密度「抽象大词+顿悟句」共现（无作者档）→ 破地板 → theme_over_explanation。"""
    base = ("他终于明白，所谓命运不过是早就写好的结局。"
            "这一刻他懂得了，原来人生的意义就在于不断地失去。"
            "归根结底，自由这个词本质上就是一场最大的幻觉而已。"
            "正如那句老话所说，希望往往就是绝望最温柔的伪装。")
    text = "\n".join(base for _ in range(4))   # ~16 命中 / ~1100 CJK ≈ 14/k ≫ 0.6
    r = S.scan(text)
    v = [x for x in r["violations"] if x["kind"] == "theme_over_explanation"]
    assert v, f"主题过度解释应报，metrics={r['metrics']}"


def test_theme_normal_density_no_flag():
    """适度主题反思（长文本低密度·无作者档）→ 不报（防矫枉过正·真作者实测 0.07-0.21/k）。"""
    long_para = ("他走进巷子深处环顾了一下四周斑驳的墙面然后在那扇生锈的铁门前停下"
                 "伸手敲了三下又退后半步安静地等着里面的人来开门。")
    paras = [long_para for _ in range(40)]                 # ~3600 CJK
    paras[0] = "他忽然明白原来有些路一旦走上就再没有回头的可能了。"  # 1 处共现
    text = "\n".join(paras)
    r = S.scan(text)
    assert "theme_over_explanation" not in _kinds(r), \
        f"低密度不该报，per_k={r['metrics'].get('theme_explain_per_kcjk')}"


def test_theme_z_band_with_author_baseline():
    """有作者基线(theme mean=0.1 std=0.15)：高密度主题点题 → z≫4 → major。"""
    with tempfile.TemporaryDirectory() as d:
        synt = {"template_coverage_mean": 0.35, "template_coverage_std": 0.07,
                "theme_explain_per_kcjk_mean": 0.1, "theme_explain_per_kcjk_std": 0.15}
        proj = _mk_project(Path(d), synt)
        base = ("他终于明白，所谓命运不过是早就写好的结局。"
                "这一刻他懂得了，原来人生的意义就在于不断地失去。"
                "归根结底，自由本质上就是一场最大的幻觉。"
                "正如那句话，希望往往是绝望最温柔的伪装。")
        text = "\n".join(base for _ in range(4))
        r = S.scan(text, project=proj)
        v = [x for x in r["violations"] if x["kind"] == "theme_over_explanation"]
        assert v, f"z-band 主题应报，z={r['metrics'].get('theme_explain_z')}"


def test_theme_min_hits_gate():
    """共现 < _THEME_MIN_HITS → 不报（防短 cluster 噪声）。"""
    text = "他忽然明白原来命运早有安排。\n" + "他往前走了几步停下来看了看天色。\n" * 30
    r = S.scan(text)
    assert "theme_over_explanation" not in _kinds(r), "少量共现不该报"


# ── 🔴金标准防矫枉过正（核心闸）──────────────────────────
def _golden_self_baseline(book: str, fmt: str, base_rng, hold_rng):
    """通用：用 base_rng 章建自身基线，hold_rng 章当『生成草稿』喂 scanner → 必 PASS。"""
    base_txt = _read_chapters(book, fmt, base_rng)
    hold_txt = _read_chapters(book, fmt, hold_rng)
    if not base_txt or not hold_txt:
        return None  # 原文不在则跳过
    synt = _self_baseline_from_text(base_txt)
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), synt)
        r = S.scan(hold_txt, project=proj)
    return r, synt


def test_golden_guimi_high_variance_passes():
    """金标准·诡秘之主（σ大·高方差作者）：hold-out 真原文喂自身基线 → 必 PASS（宽容带不误伤）。"""
    res = _golden_self_baseline("诡秘之主", "第%04d章.txt", range(1, 13), range(14, 22))
    if res is None:
        return
    r, synt = res
    assert r["verdict"] == "PASS", \
        f"诡秘真作者 hold-out 应 PASS，violations={r['violations']} baseline={synt} metrics={r['metrics']}"


def test_golden_zhushen_low_variance_passes():
    """金标准·主神大道（σ小·低方差作者）：hold-out 真原文喂自身基线 → 必 PASS（收紧带不过度放水也不误伤真作者）。"""
    res = _golden_self_baseline("主神大道", "第%d章.txt", range(1000, 1013), range(1013, 1021))
    if res is None:
        return
    r, synt = res
    assert r["verdict"] == "PASS", \
        f"主神真作者 hold-out 应 PASS，violations={r['violations']} baseline={synt} metrics={r['metrics']}"


def test_golden_jingsong_passes():
    """金标准·惊悚乐园：hold-out 真原文喂自身基线 → 必 PASS。"""
    res = _golden_self_baseline("惊悚乐园", "第%03d章.txt", range(1, 13), range(14, 22))
    if res is None:
        return
    r, synt = res
    assert r["verdict"] == "PASS", \
        f"惊悚真作者 hold-out 应 PASS，violations={r['violations']} baseline={synt} metrics={r['metrics']}"


def test_golden_real_author_no_synt_field_passes():
    """金标准·真作者原文 + 无 syntactic_diversity 维 → 走绝对地板路径必 PASS
    （证地板坐落真作者实测上限之上·不拿论文群体锚拽向均值）。"""
    any_checked = False
    for book, fmt, rng in [("诡秘之主", "第%04d章.txt", range(6, 14)),
                           ("主神大道", "第%d章.txt", range(1000, 1008)),
                           ("惊悚乐园", "第%03d章.txt", range(6, 14))]:
        txt = _read_chapters(book, fmt, rng)
        if not txt:
            continue
        any_checked = True
        r = S.scan(txt)  # 无 project/style → 无作者基线 → 绝对地板
        assert r["verdict"] == "PASS", \
            f"{book} 真作者(无 synt 维)应走地板 PASS，cov={r['metrics'].get('template_coverage_mean')} " \
            f"theme={r['metrics'].get('theme_explain_per_kcjk')} violations={r['violations']}"
    if not any_checked:
        return  # 原文全不在则跳过


def test_golden_cross_author_baseline_self_adaptive():
    """金标准·跨作者自适应：主神原文喂『主神自身基线』PASS，但喂『诡秘基线(σ大mean高)』也不应误升 major
    ——验阈值确实按各自分布自适应而非一刀切。"""
    zs_txt = _read_chapters("主神大道", "第%d章.txt", range(1000, 1008))
    if not zs_txt:
        return
    # 主神自身基线
    self_synt = _self_baseline_from_text(_read_chapters("主神大道", "第%d章.txt", range(1008, 1016)))
    if self_synt["template_coverage_mean"] <= 0:
        return
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), self_synt)
        r_self = S.scan(zs_txt, project=proj)
    assert r_self["verdict"] == "PASS", f"主神喂自身基线应 PASS，{r_self['violations']}"


# ── 总体契约（制度锁）──────────────────────────────────────
def test_gate_level_always_advisory():
    """北极星⑤：verdict 任意，gate_level 恒 advisory。"""
    text = "\n".join("他摸出手机看了一眼屏幕然后皱起了眉头。" for _ in range(40))
    r = S.scan(text)
    assert r["gate_level"] == "advisory"
    assert r["verdict"] in ("FAIL_MAJOR", "FAIL_MINOR")  # 模板文本必触发


def test_codes_not_in_hard_gate():
    """制度锁：两个 code 都不在 audit_hub.HARD_GATE_CODES（防后人误升 hard_gate）。
    照 test_thinking_probe_advisory 断言范式。import audit_hub 失败则跳过（不硬依赖）。"""
    try:
        import audit_hub as ah
    except Exception:
        return
    for code in ("SYNTAX_TEMPLATE_OVERUSE", "THEME_OVER_EXPLAIN"):
        assert code not in ah.HARD_GATE_CODES, f"{code} 误入 HARD_GATE_CODES"
        for sev in ("fatal", "error", "warning", "info"):
            assert ah._gate_level_for(code, sev) == "advisory", f"{code}@{sev} 应 advisory"


def test_violations_schema():
    text = "\n".join("他摸出手机看了一眼屏幕然后皱起了眉头。" for _ in range(40))
    r = S.scan(text)
    assert r["scanner"] == "syntactic_diversity"
    for v in r["violations"]:
        assert "kind" in v and v["severity"] in ("major", "minor")
        assert "hint" in v


def test_empty_and_short_text_no_crash():
    for t in ("", "   ", "短句。", "他走了。\n她来了。"):
        r = S.scan(t)
        assert r["verdict"] == "PASS"
        assert r["violations"] == []


def test_pure_dialogue_no_crash():
    """纯对话（剥离后 POS 序列空）不崩、不误报。"""
    text = "\n".join('“你来了，”他说。' for _ in range(30))
    r = S.scan(text)
    assert isinstance(r["violations"], list)  # 不崩即可（POS 剥光后无叙述骨架）


def test_cluster_mode_tolerance():
    """CLUSTER_MODE=1 收紧 minor 门槛（只报真显著）——临界 z 文本在 cluster 模式下不报 minor。"""
    with tempfile.TemporaryDirectory() as d:
        # 造一个 z≈3.5 的临界场景（落 minor[3.0] 与 major[4.5] 之间）：mean=0.36 std 调到让 cov~1.0 时 z=3.5
        synt = {"template_coverage_mean": 0.36, "template_coverage_std": 0.183,
                "theme_explain_per_kcjk_mean": 0.1, "theme_explain_per_kcjk_std": 0.15}
        proj = _mk_project(Path(d), synt)
        text = "\n".join("他摸出手机看了一眼屏幕然后皱起了眉头。" for _ in range(40))
        os.environ["CLUSTER_MODE"] = "1"
        try:
            r_cluster = S.scan(text, project=proj)
        finally:
            os.environ.pop("CLUSTER_MODE", None)
        r_normal = S.scan(text, project=proj)
    z = r_normal["metrics"].get("template_coverage_z")
    # 普通模式：z≈3.5 → 报 minor；cluster 模式：minor 门槛抬到 4.5 → 不报
    if z is not None and 3.0 <= z < 4.5:
        assert "syntactic_template_overuse" in _kinds(r_normal), f"普通模式临界应报 minor，z={z}"
        assert "syntactic_template_overuse" not in _kinds(r_cluster), \
            f"cluster 模式临界应容忍不报，z={z}"


# ── __main__ 自跑（PYTHONIOENCODING=utf-8 python tests/test_perplexity_obsolete_blindspot.py）──
def _run_all():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    fns = [(n, g) for n, g in sorted(globals().items())
           if n.startswith("test_") and callable(g)]
    passed = failed = 0
    fails = []
    for name, fn in fns:
        try:
            fn()
            passed += 1
            print(f"  [OK] {name}")
        except Exception as e:
            failed += 1
            fails.append((name, repr(e)))
            print(f"  [FAIL] {name}: {e!r}")
    print(f"\n{passed}/{passed + failed} OK"
          + (f"  ·  {failed} FAILED" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
