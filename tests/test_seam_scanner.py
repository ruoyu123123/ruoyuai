"""scene_seam_scanner 回归测试 — 场景/段落衔接手法分布对账（advisory）。

守护「教了输出不查」闭环 scanner 的三件事：
  1. 衔接手法提取/归类（纯规则中文标志词 · 段首前缀 · 上下文相关类目过滤）；
  2. 正文 vs 作者蒸馏衔接分布对账（L1 落差 · 缺位/过用 · 仅记录不上浮）；
  3. AI 套话连接检测（HARD/DUAL 双档 · 段首前缀杜绝子串误伤）+ advisory 永不 hard_gate。

并以 2 位真作者（蛊真人/惊悚乐园）原文做「不矫枉过正」金标准：真作者过 active 校验
（AI 套话轴 rate=0/hits=0、零 active issue、exit 0）。advisory scanner，只测确定性纯函数 +
真作者样本回查，不碰 LLM/agent。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import scene_seam_scanner as s  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 1. 衔接手法提取 / 归类
# ════════════════════════════════════════════════════════════════

def test_classify_transition_markers():
    """真·过渡标志词开头 → 归对应 canonical 类目（纯规则段首前缀）。"""
    assert s.classify_seam_head("次日清晨，方源醒来。") == "时间过渡"
    assert s.classify_seam_head("于是他决定动手。") == "因果连接"
    assert s.classify_seam_head("忽然，一声尖叫传来。") == "钩子转场"
    assert s.classify_seam_head("来到山门前，他停下脚步。") == "空间转场"
    assert s.classify_seam_head("说罢，他转身离去。") == "动作承接"


def test_hook_marker_within_head_window():
    """钩子/因果/时间类允许在段首前 8 字内出现（不强制最前缀）。"""
    assert s.classify_seam_head("方源忽然停下脚步。") == "钩子转场"


def test_context_dependent_dialogue_not_seam_midscene():
    """场景内对话开头段不是衔接点 → 返回 None（不污染分布 · 防矫枉过正）。"""
    assert s.classify_seam_head("「你来了。」他说。", is_after_break=False) is None
    # 紧跟场景分隔符时才算（新场景以对话开场）
    assert s.classify_seam_head("「你来了。」他说。", is_after_break=True) == "对话切场"


def test_plain_continuation_para_not_seam():
    """普通续写段（无标志词、非分隔符后）不是衔接点 → None。"""
    assert s.classify_seam_head("他握紧手中的剑，掌心冒汗。", is_after_break=False) is None
    # 紧跟分隔符的无标志词段 = 真·硬切无过渡
    assert s.classify_seam_head("他握紧手中的剑，掌心冒汗。", is_after_break=True) == "硬切无过渡"


def test_distribution_scan_only_counts_real_seams():
    """衔接分布只计真衔接点：分隔符 + 过渡笔法段，普通续写段不计。"""
    text = "\n".join([
        "方源走进山门。",          # 段首无标志词 · 非 after_break → 不计
        "他环视四周。",            # 同上 → 不计
        "",
        "———",
        "次日，他再次出发。",       # 分隔符后 + 时间过渡 → 计（after_break）
        "穿过竹林，抵达谷底。",     # 空间转场 → 计
    ])
    res = s.scan_seam_distribution(text)
    # 至少抓到分隔符虚拟点 + 时间过渡；硬切/普通段不灌爆
    assert res["seam_points"] >= 2, res
    assert "时间过渡" in res["distribution"], res


# ════════════════════════════════════════════════════════════════
# 2. 分布对账（L1 落差）
# ════════════════════════════════════════════════════════════════

def test_reconcile_l1_distance():
    """L1 距离 = 各类目占比差绝对值之和（0=完全一致 · 满分 2.0）。"""
    same = s.reconcile_distribution({"时间过渡": 0.5, "空间转场": 0.5},
                                    {"时间过渡": 0.5, "空间转场": 0.5})
    assert same["l1_distance"] == 0.0, same
    diff = s.reconcile_distribution({"时间过渡": 1.0}, {"空间转场": 1.0})
    assert abs(diff["l1_distance"] - 2.0) < 1e-9, diff


def test_reconcile_underused_and_overused():
    """作者主力类目正文缺位 → underused；作者低占比却被正文堆叠 → overused。"""
    r = s.reconcile_distribution(
        text_dist={"硬切无过渡": 0.7, "钩子转场": 0.3},
        author_dist={"时间过渡": 0.4, "动作承接": 0.3, "硬切无过渡": 0.3},
    )
    under_cats = {p["category"] for p in r["author_method_underused"]}
    assert "时间过渡" in under_cats and "动作承接" in under_cats, r
    over_cats = {p["category"] for p in r["method_overused"]}
    assert "钩子转场" in over_cats, r


def test_l1_drift_never_surfaces_as_issue():
    """全分布 L1 落差只记录 distribution_note · 绝不上浮成 active issue（防矫枉过正）。"""
    # 构造一段全硬切草稿（与任何作者分布都拉开 L1），断言不产生 distribution_drift issue
    text = "\n\n".join(["———\n方源握剑。" for _ in range(8)])
    style = {"cross_chapter_diversity": {
        "transition_method_distribution": {"时间过渡": 50, "动作承接": 50}}}
    draft = _write_tmp(text)
    style_p = _write_tmp(json.dumps(style, ensure_ascii=False), suffix=".json")
    rep = s.scan(draft, style_p)
    kinds = {i["kind"] for i in rep["issues"]}
    assert "distribution_drift" not in kinds, rep["issues"]
    assert rep["distribution_note"] is not None, rep
    assert "l1_distance" in rep["distribution_note"], rep


# ════════════════════════════════════════════════════════════════
# 3. AI 套话连接检测（HARD / DUAL 双档 · 段首前缀）
# ════════════════════════════════════════════════════════════════

def test_cliche_hard_marker_flagged():
    """HARD 档评书腔过场词（话分两头/镜头一转）段首前缀命中即判。"""
    text = "\n\n".join(["———\n话分两头，且说那族长。",
                        "———\n镜头一转，切到密室。"])
    res = s.detect_cliche_connectors(text, never_connectors=[])
    conns = {h["connector"] for h in res["cliche_hits"]}
    assert "话分两头" in conns, res
    assert any(h["tier"] == "hard" for h in res["cliche_hits"]), res


def test_cliche_substring_not_flagged():
    """子串误伤防护：正文中『这话说的』『那蛊却说』不是段首过场词 → 不判。"""
    text = "\n".join([
        "族长这话说的含蓄，但大家都懂。",   # 含『话说』但非段首前缀
        "那蛊却说道：我才是最强的。",        # 含『却说』但非段首前缀
    ])
    res = s.detect_cliche_connectors(text, never_connectors=[])
    assert res["cliche_hit_count"] == 0, res


def test_cliche_dual_marker_only_after_break():
    """DUAL 档双用词（与此同时/随后）只有紧跟场景分隔符作过场时才判 · 场景内合法不判。"""
    never = ["与此同时", "随后"]
    # 场景内：与此同时作时间副词 → 不判
    midscene = "方源催动真元。\n与此同时，他菜单里多出一行字。"
    assert s.detect_cliche_connectors(midscene, never)["cliche_hit_count"] == 0
    # 分隔符后作过场起新场景 → 判
    asbreak = "前情。\n\n———\n与此同时，在另一处山谷。"
    res = s.detect_cliche_connectors(asbreak, never)
    assert res["cliche_hit_count"] >= 1, res
    assert any(h["connector"] == "与此同时" and h["tier"] == "dual"
               for h in res["cliche_hits"]), res


def test_cliche_rate_overuse_triggers_advisory():
    """AI 套话连接占衔接点比例超线 → ai_cliche_connector_overuse advisory。"""
    text = "\n\n".join([
        "———\n话分两头，且说一。",
        "———\n镜头一转，切场二。",
        "———\n画面一转，切场三。",
        "———\n视角切到，切场四。",
    ])
    draft = _write_tmp(text)
    rep = s.scan(draft, None)   # 无 style 也能用内建 HARD 集
    kinds = {i["kind"] for i in rep["issues"]}
    assert "ai_cliche_connector_overuse" in kinds, rep["issues"]
    assert rep["ai_cliche_rate"] >= s._CLICHE_RATE_WARN, rep


# ════════════════════════════════════════════════════════════════
# 4. advisory 永不 hard_gate + 模式 + 作者分布加载
# ════════════════════════════════════════════════════════════════

def test_gate_level_always_advisory():
    """北极星 5：scanner gate_level 恒 advisory · 每条 issue 也 advisory · 绝不 hard_gate。"""
    text = "\n\n".join(["———\n话分两头，且说。"] * 6)
    rep = s.scan(_write_tmp(text), None)
    assert rep["gate_level"] == "advisory", rep
    for i in rep["issues"]:
        assert i["gate_level"] == "advisory", i


def test_code_not_in_hard_gate_codes():
    """SEAM_DISTRIBUTION_DRIFT 绝不进 audit_hub.HARD_GATE_CODES / registry.hard_gate_codes。"""
    import audit_hub  # noqa: E402
    assert s.ISSUE_CODE not in audit_hub.HARD_GATE_CODES
    reg = json.loads((_ROOT / "core" / "scripts" / "scanner_registry.json")
                     .read_text(encoding="utf-8"))
    assert s.ISSUE_CODE not in reg["hard_gate_codes"]
    assert "scene_seam" in reg["scanners"]


def test_shadow_mode_zero_regression(monkeypatch_env=None):
    """shadow 模式：算全量挂 shadow_issues · 顶层 issues=[] · warning=None（零回归）。"""
    import os
    text = "\n\n".join(["———\n话分两头，且说。"] * 6)
    draft = _write_tmp(text)
    old = os.environ.get("SEAM_SCANNER_MODE")
    os.environ["SEAM_SCANNER_MODE"] = "shadow"
    try:
        rep = s.scan(draft, None)
    finally:
        if old is None:
            os.environ.pop("SEAM_SCANNER_MODE", None)
        else:
            os.environ["SEAM_SCANNER_MODE"] = old
    assert rep["mode"] == "shadow", rep
    assert rep["issues"] == [], rep
    assert rep["warning"] is None, rep
    assert rep["shadow_issues"], rep   # 仍记录


def test_mode_default_active():
    """env 缺省 → active（默认全开真生效）· 非法值回退 active。"""
    import os
    old = os.environ.pop("SEAM_SCANNER_MODE", None)
    try:
        assert s._mode() == "active"
        os.environ["SEAM_SCANNER_MODE"] = "garbage"
        assert s._mode() == "active"
    finally:
        if old is None:
            os.environ.pop("SEAM_SCANNER_MODE", None)
        else:
            os.environ["SEAM_SCANNER_MODE"] = old


def test_load_author_distribution_both_schemas():
    """兼容两位真作者 schema：类目计数型 / 细粒度 evidence 权重型 → 归一 canonical 分布。"""
    # 惊悚乐园风格：类目→计数 + connection_type
    jx = {
        "style_profile": {"rhythm": {"transition_method_distribution": {
            "空间硬切": 17, "画外音承接": 15, "行动承接/省略上线": 9, "其他": 208}}},
        "anti_patterns": {"never_scene_transition": {"与此同时": {}, "随后": {}}},
    }
    a = s.load_author_seam_distribution(jx)
    assert a["distribution"], a
    # 「其他」映射不到 → 丢弃；映射到的类目归一后占比和为 1
    assert abs(sum(a["distribution"].values()) - 1.0) < 1e-6, a
    assert "硬切无过渡" in a["distribution"], a   # 空间硬切 → 硬切无过渡
    assert any("与此同时" in x for x in a["never_connectors"]), a


def test_real_author_no_overcorrection():
    """金标准·不矫枉过正：2 真作者原文簇过 active 校验（AI 套话轴 rate=0/hits=0 · 0 active issue · exit 0）。

    缺原文样本（CI 环境）→ skip（不视为失败）。"""
    for book in ("蛊真人", "惊悚乐园"):
        base = _ROOT / "workspace" / "styles" / book / "原文"
        style_p = _ROOT / "workspace" / "styles" / book / "作者风格_FINAL.json"
        if not base.is_dir() or not style_p.is_file():
            continue
        chs = sorted(base.glob("第00[1-6]章.txt"))
        if len(chs) < 3:
            continue
        text = "\n\n".join(c.read_text(encoding="utf-8") for c in chs)
        draft = _write_tmp(text)
        rep = s.scan(draft, style_p)
        assert rep["ai_cliche_rate"] == 0.0, (book, rep["ai_cliche_rate"])
        assert rep["cliche_check"]["cliche_hit_count"] == 0, (book, rep["cliche_check"])
        assert rep["issues"] == [], (book, rep["issues"])
        assert rep["warning"] is None, (book, rep["warning"])


# ════════════════════════════════════════════════════════════════
# 辅助
# ════════════════════════════════════════════════════════════════

_TMP_DIR = _ROOT / "workspace" / "_temp_research"
_tmp_seq = [0]


def _write_tmp(content: str, suffix: str = ".txt") -> Path:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    _tmp_seq[0] += 1
    p = _TMP_DIR / f"_seam_test_{_tmp_seq[0]}{suffix}"
    p.write_text(content, encoding="utf-8")
    return p
