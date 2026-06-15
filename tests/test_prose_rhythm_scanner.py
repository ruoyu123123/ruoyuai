"""prose_rhythm_scanner 回归测试（2026-06-03 · 「流水账作文感」检测·作者基线第一权威）。

钉死：validate_style 故意「绝不查句长」→ 句长偏短无人报警；本 scanner 补检测闭环。
三探针：句长偏离作者基线 / 主语+动作 streak / 主语开头占比。全 advisory。
金标准防矫枉过正：真作者原文喂自身基线必 PASS（test_real_author_passes）。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import prose_rhythm_scanner as P  # noqa: E402

_LONG = "他站在香案前面低头看着那本合着的册子封皮上烫金的大字在摇曳的烛火下泛着光"  # ~33 CJK


def _kinds(r):
    return {v["kind"] for v in r["violations"]}


def _mk_project(tmp: Path, author_mean=None, names=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if author_mean is not None:
        db.joinpath("作者风格.json").write_text(json.dumps(
            {"quantitative": {"sentence_length": {"mean": author_mean}}}, ensure_ascii=False),
            encoding="utf-8")
    if names:
        db.joinpath("人物卡.json").write_text(json.dumps(
            {"characters": [{"id": n, "name": n} for n in names]}, ensure_ascii=False),
            encoding="utf-8")
    return tmp


# ── 探针 1：句长偏短 ──────────────────────────────
def test_short_sentences_flagged_major():
    """全短句（~7 CJK）vs 通用基线 26 → ratio<0.55 → sentence_too_short major。"""
    text = "\n".join("他低头看手。" for _ in range(20))
    r = P.scan(text)
    v = [x for x in r["violations"] if x["kind"] == "sentence_too_short"]
    assert v and v[0]["severity"] == "major", f"应报句长偏短 major，实际 {r['violations']}"


def test_long_sentences_no_short_flag():
    """接近基线的复合长句 → 不报句长偏短（防矫枉过正）。"""
    # 多样句首避免 streak/subj 干扰，只验句长探针
    text = "\n".join([
        "天色暗下来的时候，" + _LONG + "。",
        "走廊尽头传来脚步声，" + _LONG + "。",
        "“你看这是什么，”那声音压得很低，" + _LONG + "。",
        "烛火晃了一下，" + _LONG + "。",
    ])
    r = P.scan(text)
    assert "sentence_too_short" not in _kinds(r), f"长句不该报偏短，metrics={r['metrics']}"


# ── 探针 2：主语+动作 streak ──────────────────────
def test_subject_action_streak_major():
    """连续 ≥6 句「他+动作」→ subject_action_streak major。"""
    text = "\n".join(f"他做了第{i}件事情然后停下来想了想又继续往前走了一段路。" for i in range(8))
    r = P.scan(text)
    v = [x for x in r["violations"] if x["kind"] == "subject_action_streak"]
    assert v and v[0]["severity"] == "major", f"应报流水账 streak major，max={r['metrics']['max_subject_streak']}"


def test_streak_broken_by_diverse_openings_ok():
    """句首多样（主语句间插环境/对话）→ streak<4 不报。"""
    text = "\n".join([
        "他走进房间里看了一眼四周的环境又把门轻轻带上了。",
        "窗外的天色已经暗到几乎看不清对面楼里那盏忽明忽暗的灯。",
        "“进来吧，”里面的人头也没抬地说了一句就继续低头写字。",
        "她把伞收起来靠在墙角又顺手理了理被雨打湿的头发。",
        "空气里有股很淡的霉味混着不知道哪里飘来的饭菜香气。",
    ])
    r = P.scan(text)
    assert "subject_action_streak" not in _kinds(r), f"max_streak={r['metrics']['max_subject_streak']}"


# ── 探针 3：主语开头占比 ──────────────────────────
def test_subject_start_ratio_high_flagged():
    """主语开头叙述句占比远超 24% → subject_start_ratio_high。"""
    # 交替「他X」「她Y」破 streak，但主语开头占比仍 ~100%
    text = "\n".join(
        (f"他走到第{i}个路口又停了下来环顾四周确认没人跟着才继续。" if i % 2 else
         f"环境很安静，他往前走了一段路然后在第{i}棵树旁停了下来歇脚。")
        for i in range(12))
    r = P.scan(text)
    assert "subject_start_ratio_high" in _kinds(r), f"subj%={r['metrics']['subject_start_pct']}"


# ── 探针 4：段首倒装句式模具（memory feedback_inverted_modifier_sentence_mold_overuse）──
def test_inverted_modifier_mold_flagged():
    """连续段首「前置长定语+的+主语后置」倒装模具 → inverted_modifier_mold（实证骨架）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), names=["陆参"])
        text = "\n".join([
            "摸出手机的陆参看了一眼屏幕上跳动的未接来电然后皱起了眉头。",
            "愣住的陆参半天没回过神来手里的杯子差点没拿稳掉在地上。",
            "端起塑料杯喝完半杯豆浆的陆参站起身拍了拍裤子上的灰尘。",
            "没有去接湿巾的陆参只是盯着桌上那张照片出神了很久很久。",
            "听到敲门声的他猛地从椅子上站了起来快步走向了门口处。",
        ])
        r = P.scan(text, project=proj)
        v = [x for x in r["violations"] if x["kind"] == "inverted_modifier_mold"]
        assert v, f"应报段首倒装模具，metrics={r['metrics']}"
        assert r["metrics"]["inverted_mold_count"] >= 3


def test_diverse_heads_no_inverted_flag():
    """段首多样（直接主语/环境/对话/所属）→ 不报倒装（防矫枉过正）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), names=["陆参"])
        text = "\n".join([
            "陆参摸出手机看了一眼屏幕上跳动的未接来电然后皱起了眉头想了想。",
            "窗外的天色已经暗到几乎看不清对面楼里那盏忽明忽暗的旧灯了。",
            "“你到底来不来，”电话那头的声音压得很低带着点说不出的不耐烦。",
            "桌上那张照片的边缘已经发黄看得出来有些年头没人动过了。",
            "他站起身拍了拍裤子上的灰尘快步走向了门口顺手关掉了灯。",
        ])
        r = P.scan(text, project=proj)
        assert "inverted_modifier_mold" not in _kinds(r), \
            f"多样段首不该报，count={r['metrics']['inverted_mold_count']}"


def test_inverted_possessive_not_flagged():
    """所属定语「X的[物]」不误报（的后非主语集·如桌上的杯子）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), names=["陆参"])
        text = "\n".join([
            "桌上的杯子已经空了大半只剩下杯底一点凉掉的残茶在晃。",
            "墙角的照片落了厚厚一层灰看不清上面那个人的脸了。",
            "窗台的花盆里那株绿萝长得格外茂盛叶子绿得几乎发亮。",
        ])
        r = P.scan(text, project=proj)
        assert r["metrics"]["inverted_mold_count"] == 0, "所属定语不该算倒装"


def test_inverted_metrics_present():
    """metrics 含倒装指标（供 audit_hub 消费）。"""
    r = P.scan("他低头看手。\n她抬头看天。", project=None)
    assert "inverted_mold_count" in r["metrics"]
    assert "inverted_mold_pct" in r["metrics"]
    assert "inverted_mold_max_streak" in r["metrics"]


# ── 探针 5：强度副词通胀（极其/死死/毫无/猛地·与倒装同批 gen-model 套路）──
def test_intensity_adverb_inflation_flagged():
    """强度副词高密度通胀 → intensity_adverb_inflation。"""
    text = "\n".join([
        "他极其愤怒地死死攥住拳头毫无保留地猛地一拳狠狠砸在了桌子上面。",
        "她极其害怕地紧紧抓住门框毫无办法只能死死盯着那个疯狂逼近的影子。",
        "他极其用力地牢牢按住对方的肩膀拼命想要把那扇门死死顶住别让它开。",
    ])
    r = P.scan(text)
    v = [x for x in r["violations"] if x["kind"] == "intensity_adverb_inflation"]
    assert v, f"应报强度副词通胀，metrics={r['metrics']}"


def test_intensity_single_word_overused():
    """单个强度副词高频（极其 ≥12）→ 报（单词过用）。"""
    text = "\n".join(f"他极其认真地看了第{i}遍那份极其重要的文件感到极其满意极了。" for i in range(7))
    r = P.scan(text)
    v = [x for x in r["violations"] if x["kind"] == "intensity_adverb_inflation"]
    assert v, f"单词过用应报，metrics={r['metrics']}"
    assert r["metrics"]["intensity_adverb_total"] >= 12


def test_intensity_moderate_no_flag():
    """适度强度副词（长文本低密度·2 个死死/~800 CJK ≈ 2.4/千 < 3.0）→ 不报（防矫枉过正）。

    短文本密度虚高(1 词/280CJK=3.6/千)非真实场景——prose_rhythm 是 cluster 草稿(10k-25k)scanner，
    真草稿密度真实(金标准 test_real_author_passes 已验长文本不误伤)。本测试用长文本模拟真实密度。"""
    long_para = ("他走进房间环顾了一下四周的环境然后在靠窗的那个位置慢慢坐了下来"
                 "又顺手点了一杯热茶安静地等着对方过来谈那件要紧的事情。")   # ~55 CJK
    paras = [long_para for _ in range(15)]    # ~825 CJK
    paras[0] = "她死死盯着屏幕看了很久又死死攥着手里那支笔才终于慢慢松了口气。"  # 2 个死死
    text = "\n".join(paras)
    r = P.scan(text)
    assert "intensity_adverb_inflation" not in _kinds(r), \
        f"低密度不该报，per_1k={r['metrics']['intensity_adverb_per_1k']}"


def test_intensity_metrics_present():
    r = P.scan("他走进房间坐了下来又站了起来。", project=None)
    assert "intensity_adverb_total" in r["metrics"]
    assert "intensity_adverb_per_1k" in r["metrics"]


# ── 作者基线第一权威 ──────────────────────────────
def test_author_baseline_from_profile():
    """传作者档(mean=31) → 句长偏短按 31 判（比通用 26 更严）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), author_mean=31)
        text = "\n".join("他点了点头然后转身走开了没再多说一句话。" for _ in range(15))  # ~18 CJK
        r = P.scan(text, project=proj)
        assert r["author_baseline"]["from_author_profile"] is True
        assert r["author_baseline"]["sentence_mean"] == 31
        assert "sentence_too_short" in _kinds(r)


def test_names_from_renwu_card_detected():
    """人物卡角色名开头也算主语流水账（非仅代词）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), names=["池迟", "苏挽"])
        text = "\n".join((f"池迟翻开第{i}页。" if i % 2 else f"苏挽看了第{i}眼。") for i in range(8))
        r = P.scan(text, project=proj)
        assert r["metrics"]["max_subject_streak"] >= 6, "人名开头应计入 streak"


# ── 总体契约 ──────────────────────────────────────
def test_gate_level_always_advisory():
    text = "\n".join("他低头。" for _ in range(20))
    r = P.scan(text)
    assert r["verdict"] == "FAIL_MAJOR"
    assert r["gate_level"] == "advisory"  # 北极星⑤顾问非法官


def test_violations_schema_for_audit_hub():
    text = "\n".join("他低头看手。" for _ in range(20))
    r = P.scan(text)
    assert r["scanner"] == "prose_rhythm"
    for v in r["violations"]:
        assert "kind" in v and v["severity"] in ("major", "minor")


def test_real_author_passes_own_baseline():
    """金标准防矫枉过正：惊悚乐园真作者原文喂同作者基线 → PASS（句长/主语占比/streak 全达标）。"""
    jroot = _SCRIPTS.parents[0] / "workspace" / "styles" / "惊悚乐园" / "原文"
    wproj = _SCRIPTS.parents[0] / "workspace" / "novels" / "无脸者守则"
    if not jroot.exists() or not wproj.exists():
        return  # 文件不在则跳过（不硬失败·零依赖原则）
    chs = [jroot / f"第{c:03d}章.txt" for c in range(6, 14)]
    text = "\n\n".join(p.read_text(encoding="utf-8") for p in chs if p.exists())
    if not text:
        return
    r = P.scan(text, project=wproj)
    assert r["verdict"] == "PASS", f"真作者应 PASS，violations={r['violations']}"
