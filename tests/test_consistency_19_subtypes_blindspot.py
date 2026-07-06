# -*- coding: utf-8 -*-
"""盲区落地测试 · consistency_19_subtypes（B 件 · ConStory 时间线&因果一致性 · 确定性子集）

落地内容（design 的 B 件 · 见 workspace/_temp_research/blindspot_designs.json）：
把 locked_fact_cross_scene_scanner 的「年龄专用」泛化为「**恒定数值类锁定事实**通用对账」，
覆盖 ConStory「绝对时间矛盾」的确定性子集（fact 含「N岁/第N天」且正文同角色同句冲突值 → 报）。

🔴 北极星铁律（违则白做）逐条钉死：
  (a) **不新增 hard_gate code**：泛化沿用既有 LOCKED_FACT_CROSS_SCENE_CONFLICT，
      **不动** audit_hub.HARD_GATE_CODES / STRUCTURE.md §11（零新增·制度锁测试见 test_no_new_hard_gate_code）。
  (b) **作者档/项目第一权威**：恒定单位集从项目 `_数据库/locked_fact_units.json` opt-in 读，
      缺则退保底「岁」（与历史行为兼容）。绝不臆造「从世界观读等级」的不存在通路。
  (c) **顾问非法官 + 防矫枉过正**：单调递增修真品级（品/阶/层/级/段/重）**绝不报**——
      角色升阶（三段→九段）是合法成长非穿帮，对其 M≠N 会制造假 hard_gate（金标准核心反例）。
  (d) **cluster 为单位**：scan() 吃 cluster 草稿整块。

🔴 金标准防矫枉过正（test_plan 核心闸 · 真作者原文喂自身基线必 PASS）：
  test_golden_real_author_no_false_hard_gate 取**真作者原文 cluster**（σ27 诡秘 + σ15 低方差作者两极）
  当「系统生成」喂 scanner → 必须 conflicts_count==0（不误判作者真实风格为穿帮）。
  两极验证对齐 design：高方差/低方差作者都不被误伤。

零依赖 · 通过仓库根 `py -m pytest` 发现 test_* 函数。
跑：PYTHONIOENCODING=utf-8 python tests/test_consistency_19_subtypes_blindspot.py
"""
import glob
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import locked_fact_cross_scene_scanner as lf  # noqa: E402


# ───────────────────────── 测试脚手架 ─────────────────────────

def _run(name, fact, draft_text, units=None):
    """搭最小项目（人物卡 + 可选 locked_fact_units + draft）跑 scan()，返回 report。"""
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "_数据库"
    db.mkdir(parents=True)
    facts = fact if isinstance(fact, list) else [{"fact": fact}]
    (db / "人物卡.json").write_text(
        json.dumps({"characters": [{"name": name, "locked_facts": facts}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    if units is not None:
        (db / "locked_fact_units.json").write_text(
            json.dumps({"invariant_units": units}, ensure_ascii=False),
            encoding="utf-8",
        )
    draft = tmp / "draft.txt"
    draft.write_text(draft_text, encoding="utf-8")
    return lf.scan(tmp, draft)


# ═══════════════ 1. 制度锁：不新增 hard_gate code（北极星 a）═══════════════

def test_no_new_hard_gate_code():
    """泛化**不得**新增任何 hard_gate code —— 仍只用既有 LOCKED_FACT_CROSS_SCENE_CONFLICT，
    且 audit_hub.HARD_GATE_CODES 不被本盲区改动（照 thinking_probe_advisory 制度锁范式）。"""
    import audit_hub
    hg = audit_hub.HARD_GATE_CODES
    # 既有 code 仍在（泛化只扩覆盖范围，不动清单）
    assert "LOCKED_FACT_CROSS_SCENE_CONFLICT" in hg
    # 本盲区 design 提到的 6 个 A 件判官 code 一律**不在** hard_gate（它们是 advisory 判官层，
    # 本任务不落地 A 件；此断言锁死「即便将来落地也绝不能进 hard_gate」）。
    for code in ("TIMELINE_ABSOLUTE_TIME_CONFLICT", "TIMELINE_DURATION_CONFLICT",
                 "TIMELINE_SIMULTANEITY_CONFLICT", "PLOT_CAUSELESS_EFFECT",
                 "PLOT_CAUSAL_LOGIC_VIOLATION", "PLOT_ABANDONED_ELEMENT"):
        assert code not in hg, f"{code} 误入 HARD_GATE_CODES（违北极星 a）"
        # _gate_level_for 对不在白名单的 code 必判 advisory
        assert audit_hub._gate_level_for(code, "error") == "advisory", code


# ═══════════════ 2. 防矫枉过正核心：单调递增品级绝不报（北极星 c）═══════════════

def test_monotonic_rank_never_conflicts_default():
    """默认单位集只有「岁」→ 品级（三段→九段）压根不在射程，绝不报 hard_gate。"""
    r = _run("萧炎", "萧炎斗之气三段",
             "萧炎如今已是斗之气九段，距离斗者只差一步。")
    assert r["conflicts_count"] == 0, r
    assert r["code"] is None, r
    assert r["gate_level"] == "advisory", r


def test_monotonic_rank_blocked_even_if_optin():
    """🔴 即便项目 opt-in 误填品级单位（段/品/层…），_MONOTONIC_BLOCKLIST 强制剔除 →
    角色升阶（三段→九段）仍绝不报（这是金标准最危险的误判点：把成长当穿帮）。"""
    for unit in ("段", "品", "层", "级", "阶", "重", "境", "星"):
        r = _run("萧炎", f"萧炎实力三{unit}",
                 f"萧炎如今已是九{unit}。", units=[unit])
        assert r["conflicts_count"] == 0, (unit, r)
        # 单位集里不该出现被拉黑的品级（只剩保底「岁」）
        assert unit not in r["invariant_units"], (unit, r["invariant_units"])
        assert r["invariant_units"] == ["岁"], (unit, r["invariant_units"])


# ═══════════════ 3. 泛化真阳性：opt-in 恒定单位（天）跨场景冲突报 ═══════════════

def test_optin_day_unit_conflict_reported():
    """opt-in 恒定单位「天」：fact『被困第三天』+ 正文同角色同句『第八天』→ 报（绝对时间矛盾确定性子集）。"""
    r = _run("周明", "周明被困第三天", "周明在地窖里熬到第八天才被人发现。", units=["天"])
    assert r["conflicts_count"] == 1, r
    assert r["code"] == "LOCKED_FACT_CROSS_SCENE_CONFLICT", r
    assert r["gate_level"] == "hard_gate", r
    assert r["conflicts"][0]["unit"] == "天", r
    assert "第八天" in r["conflicts"][0]["conflict_value"] or "八天" in r["conflicts"][0]["conflict_value"], r


def test_optin_day_unit_consistent_no_report():
    """opt-in「天」一致（第三天↔第三天）→ 不报。"""
    r = _run("周明", "周明被困第三天", "周明被困第三天，水已经喝光了。", units=["天"])
    assert r["conflicts_count"] == 0, r


def test_different_units_not_compared():
    """单位不同（岁 vs 天）不可比 → 不误报：fact『三岁』+ 正文『第三天』。"""
    r = _run("阿福", "阿福三岁", "阿福被抱来的第三天就会笑了。", units=["天"])
    assert r["conflicts_count"] == 0, r


# ═══════════════ 4. 向后兼容：默认仍只跑「岁」（回归不破）═══════════════

def test_default_unit_set_is_age_only():
    """缺 locked_fact_units.json → 单位集退保底 ['岁']（与历史行为完全兼容）。"""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "_数据库").mkdir(parents=True)
    assert lf._load_unit_set(tmp) == ["岁"]


def test_age_still_works_after_generalization():
    """泛化后年龄真阳性不丢：fact『三十八岁』↔ 正文『四十岁』→ 仍报 hard_gate。"""
    r = _run("林惊羽", "林惊羽三十八岁", "林惊羽自称四十岁了，可没人信。")
    assert r["conflicts_count"] == 1, r
    assert r["code"] == "LOCKED_FACT_CROSS_SCENE_CONFLICT", r
    assert r["conflicts"][0]["unit"] == "岁", r


def test_age_false_positive_distance_still_guarded():
    """泛化后『三十里』仍不被当年龄（守住 2026-05-30 假阳性修）。"""
    r = _run("林惊羽", "林惊羽三十八岁", "林惊羽今年三十八岁，那天走了三十里路。")
    assert r["conflicts_count"] == 0, r


def test_optin_invalid_unit_falls_back():
    """opt-in 非法单位（空/超长/含非 CJK）→ 全剔，退保底『岁』。"""
    r = _run("某人", "某人是铁匠", "随便写点。", units=["", "  ", "abc", "一二三四"])
    assert r["invariant_units"] == ["岁"], r


# ═══════════════ 5. 🔴 金标准防矫枉过正：真作者原文喂自身基线必 PASS ═══════════════

def _styles_search_roots():
    """真作者语料根目录候选：当前 repo + （worktree 场景下）主检出。
    worktree 不跟踪 workspace/styles（仅 README），真语料在主检出 → 回退到主检出路径。
    路径形如 ...\\.claude\\worktrees\\<id>，主检出 = 去掉这三段。"""
    roots = [_ROOT]
    parts = _ROOT.parts
    if len(parts) >= 3 and parts[-3] == ".claude" and parts[-2] == "worktrees":
        roots.append(Path(*parts[:-3]))
    # 也尊重环境变量覆盖（CI / 自定义语料位置）
    env = os.environ.get("RUOYUAI_MAIN_CHECKOUT")
    if env:
        roots.append(Path(env))
    return roots


def _discover_author_dir_by_sigma(lo, hi, exclude="clitest"):
    """按句长 std 动态发现真作者风格目录（避开测试 fixture）·返回带 原文/ 章节的目录。
    动态发现而非硬编码 garbled 路径 → 测试可移植、不依赖控制台编码。"""
    cands = []
    for root in _styles_search_roots():
        cands.extend(glob.glob(str(root / "workspace" / "styles" / "*" / "作者风格_FINAL.json")))
    for f in cands:
        d = os.path.dirname(f)
        if exclude in d:
            continue
        try:
            prof = json.load(io.open(f, encoding="utf-8"))
        except Exception:
            continue
        s = (prof.get("quantitative", {}) or {}).get("sentence_length", {}).get("std")
        raws = glob.glob(os.path.join(d, "原文", "*.txt"))
        if isinstance(s, (int, float)) and lo <= s <= hi and raws:
            return d, sorted(raws)
    return None, []


def _first_chapter_with_age(raws, limit=40):
    """在前 limit 章里找含『N岁』的真章节，返回 (text, 年龄数字字符串)。无则返回首章+None。"""
    for rf in raws[:limit]:
        try:
            t = io.open(rf, encoding="utf-8").read()
        except Exception:
            continue
        m = re.search(r"(\d+|[零一二三四五六七八九十百]+)\s*岁", t)
        if m and lf._cn_to_int(m.group(1)) is not None:
            return t, m.group(1)
    # 没有年龄章节 → 返回首章（仍可做描述类 fact 的 0 误报验证）
    try:
        return io.open(raws[0], encoding="utf-8").read(), None
    except Exception:
        return "", None


def _golden_assert_no_false_hardgate(text, real_age_str, tag):
    """对真作者章节：(1) 描述类 fact → 0 误报；(2) 若有真年龄，配一致年龄 fact → 0 误报。
    真作者原文落在自身设定中，绝不该被判穿帮 hard_gate（北极星⑤铁律）。"""
    if not text:
        return  # 章节读不出（编码异常）则跳过该极，不算失败
    # 取一个在正文出现的「2 字 CJK」候选名当角色（用真文本片段，确保 name in text）
    cand = None
    for m in re.finditer(r"[一-龥]{2,3}", text):
        w = m.group(0)
        # 避开纯数字词/常见非人名虚词起手（粗启发足够·只为构造 name in text）
        if w and all("一" <= c <= "鿿" for c in w):
            cand = w[:2]
            if text.count(cand) >= 3:  # 出现多次更像角色/高频词
                break
    if cand is None:
        cand = "无名"
    # (1) 描述类 locked_fact（不含数值）→ 数值通路根本不启用 → 必 0 误报
    r1 = lf_scan_with_card(text, cand, [{"fact": f"{cand}是主角"}])
    assert r1["conflicts_count"] == 0, (tag, "descriptive-fact false-positive", r1)
    assert r1["gate_level"] == "advisory", (tag, r1)
    # (2) 若文本含真年龄 → 配**一致**年龄 fact → 真作者文本不该自我冲突
    if real_age_str is not None:
        age_val = lf._cn_to_int(real_age_str)
        r2 = lf_scan_with_card(text, cand, [{"fact": f"{cand}{real_age_str}岁"}])
        # 同一文本里真年龄一致（fact 用文本里出现的真年龄）→ 0 冲突；
        # 即便文本另有其它『岁』数值，也只在与本角色**同句**时才比 → 真作者一般不自相矛盾。
        assert r2["conflicts_count"] == 0, (tag, f"age={age_val}", "consistent-age false-positive", r2)


def lf_scan_with_card(text, name, facts):
    """直接喂真作者章节文本 + 构造人物卡，跑 scan()。"""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "_数据库").mkdir(parents=True)
    (tmp / "_数据库" / "人物卡.json").write_text(
        json.dumps({"characters": [{"name": name, "locked_facts": facts}]}, ensure_ascii=False),
        encoding="utf-8")
    draft = tmp / "draft.txt"
    draft.write_text(text, encoding="utf-8")
    return lf.scan(tmp, draft)


def test_golden_real_author_no_false_hard_gate():
    """🔴 金标准：真作者原文（σ27 诡秘 高方差 + σ15 低方差作者两极）喂 scanner → 0 误报 hard_gate。
    这是最强反误判证据：真作者写的东西不该被确定性数值对账判成穿帮。
    若本地无真作者原文（CI 干净环境）→ 跳过（不阻塞确定性合入·素材本地不入 git）。"""
    poles = [
        ("HI_sigma27", _discover_author_dir_by_sigma(26, 28)),
        ("LO_sigma15", _discover_author_dir_by_sigma(14, 15.5)),
    ]
    ran = 0
    for tag, (d, raws) in poles:
        if not d or not raws:
            continue
        text, age = _first_chapter_with_age(raws)
        _golden_assert_no_false_hardgate(text, age, tag)
        ran += 1
    if ran == 0:
        print("[SKIP] 金标准：本地未发现真作者原文池（素材不入 git）")


def test_golden_distance_quantity_year_noise_no_false_positive():
    """金标准补强（合成·必跑·不依赖本地素材）：真作者文本常见的距离/数量/年份噪声
    密集出现在角色名附近时，配一个一致年龄 fact，绝不误报（守 hard_gate 假阳性根因）。"""
    text = ("沈墨今年二十岁，那天他走了三百里路，买了五十个铜板的干粮，"
            "想起一九三八年的旧事，又翻过二十座山头。")
    r = _run("沈墨", "沈墨二十岁", text)
    assert r["conflicts_count"] == 0, r
    assert r["code"] is None, r


if __name__ == "__main__":
    import traceback
    g = dict(globals())
    tests = [v for k, v in sorted(g.items()) if k.startswith("test_") and callable(v)]
    ok = fail = 0
    for t in tests:
        try:
            t()
            ok += 1
            print(f"[OK] {t.__name__}")
        except Exception as e:
            fail += 1
            print(f"[FAIL] {t.__name__}: {e}")
            traceback.print_exc()
    print("=" * 56)
    print(f"测试 {ok + fail} · 通过 {ok} · 失败 {fail}")
    sys.exit(1 if fail else 0)
