"""locked_fact_cross_scene_scanner 年龄冲突回归测试 — 守护 2026-05-30 修 hard_gate 假阳性。

旧 bug（已复现）：extract_numbers_near 抓 name 前后 ±50 字**所有**数字（含距离/数量/年份），
年龄绑定判据仅靠「窗口里存在『岁』字」宽存在性 → 「林惊羽今年三十八岁，那天走了三十里路」
里的「三十里」被当成「三十岁」与 fact「三十八岁」冲突 → 误报 LOCKED_FACT_CROSS_SCENE_CONFLICT。
audit_hub 把该 code 接成 **hard_gate（不可豁免）** → writer 被迫改正确文字。

本测试**双向**断言（hard_gate 修复纪律：消假阳性 + 绝不漏真阳性）：
  · 假阳性方向：距离/数量/年份等无关数字不触发冲突；
  · 真阳性方向：fact 年龄 与 正文真年龄（数字紧邻「岁」）矛盾仍报，覆盖中文/阿拉伯/百位。

advisory 边界外的 hard_gate scanner，测试只测确定性纯函数 + scan()，不碰 LLM/agent。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import locked_fact_cross_scene_scanner as lf


def _run(name, fact, draft_text):
    """搭最小项目（人物卡 + draft）跑 scan()，返回 report。"""
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "_数据库"
    db.mkdir(parents=True)
    (db / "人物卡.json").write_text(
        json.dumps(
            {"characters": [{"name": name, "locked_facts": [{"fact": fact}]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    draft = tmp / "draft.txt"
    draft.write_text(draft_text, encoding="utf-8")
    return lf.scan(tmp, draft)


# ── _cn_to_int 纯函数：中文/阿拉伯/百位 ─────────────────────────────

def test_cn_to_int_arabic():
    assert lf._cn_to_int("52") == 52
    assert lf._cn_to_int("8") == 8


def test_cn_to_int_chinese_tens():
    assert lf._cn_to_int("三十八") == 38
    assert lf._cn_to_int("二十") == 20
    assert lf._cn_to_int("十") == 10
    assert lf._cn_to_int("十八") == 18


def test_cn_to_int_chinese_single():
    assert lf._cn_to_int("八") == 8
    assert lf._cn_to_int("零") == 0


def test_cn_to_int_hundreds():
    """补『百』位：覆盖修真/玄幻超长寿命年龄。"""
    assert lf._cn_to_int("一百二十") == 120
    assert lf._cn_to_int("一百五十") == 150
    assert lf._cn_to_int("二百") == 200
    assert lf._cn_to_int("一百零五") == 105


def test_cn_to_int_unparseable_returns_none():
    """无法解析（含名字残片/混排）返回 None，不抛异常。"""
    assert lf._cn_to_int("三52") is None  # CJK + 阿拉伯混排（名字残片污染）
    assert lf._cn_to_int("abc") is None
    assert lf._cn_to_int("") is None


# ── extract_ages_near：只认『数字+岁』，杜绝距离/数量串味 ──────────

def test_extract_ages_only_matches_adjacent_sui():
    """核心：『三十八岁』被抓，『三十里』『二十个』不被抓。"""
    text = "林惊羽今年三十八岁，那天走了三十里路，买了二十个馒头。"
    ages = lf.extract_ages_near(text, "林惊羽", window=80)
    nums = [n for _, n in ages]
    assert nums == ["三十八"], nums


def test_extract_ages_name_with_cjk_digit_not_corrupted():
    """名字含中文数字（张三）+ 阿拉伯年龄（52岁）：不把名字的『三』吃进数字。
    旧混排正则会匹配出『三52』→ 解析失败 → 真年龄校验被跳过（漏报）。"""
    text = "张三今年52岁。"
    ages = lf.extract_ages_near(text, "张三", window=50)
    nums = [n for _, n in ages]
    assert nums == ["52"], nums


# ── scan() 假阳性方向：无关数字绝不报 hard_gate ─────────────────

def test_no_false_positive_distance():
    """假阳性复现守护：fact『三十八岁』+ 正文『三十里路』→ 不报冲突。"""
    r = _run("林惊羽", "林惊羽三十八岁", "林惊羽今年三十八岁，那天走了三十里路。")
    assert r["conflicts_count"] == 0, r
    assert r["code"] is None, r
    assert r["gate_level"] == "advisory", r


def test_no_false_positive_quantity():
    """数量串味：fact『二十岁』+ 正文『二十个馒头』→ 不报。"""
    r = _run("王二", "王二二十岁", "王二去集市买了二十个馒头。")
    assert r["conflicts_count"] == 0, r


def test_no_false_positive_year():
    """年份串味：fact『二十岁』+ 正文『一九三八年』（无『岁』邻接）→ 不报。"""
    r = _run("陈兵", "陈兵二十岁", "陈兵生于一九三八年，那年华北沦陷。")
    assert r["conflicts_count"] == 0, r


def test_no_conflict_when_age_consistent():
    """年龄一致（三十八岁 ↔ 三十八岁）→ 不报。"""
    r = _run("林惊羽", "林惊羽三十八岁", "林惊羽今年三十八岁。")
    assert r["conflicts_count"] == 0, r


def test_no_conflict_when_fact_has_no_age():
    """fact 不含年龄 → 年龄校验不启用，正文任何『岁』都不触发。"""
    r = _run("赵六", "赵六是个铁匠", "赵六今年四十岁。")
    assert r["conflicts_count"] == 0, r


# ── scan() 真阳性方向：真年龄矛盾绝不漏报 hard_gate ─────────────

def test_true_positive_chinese_age_conflict():
    """真阳性：fact『三十八岁』↔ 正文『四十岁』→ 必报 hard_gate。"""
    r = _run("林惊羽", "林惊羽三十八岁", "林惊羽自称四十岁了，可没人信。")
    assert r["conflicts_count"] == 1, r
    assert r["code"] == "LOCKED_FACT_CROSS_SCENE_CONFLICT", r
    assert r["gate_level"] == "hard_gate", r
    assert r["conflicts"][0]["character"] == "林惊羽"
    assert "四十" in r["conflicts"][0]["conflict_value"]


def test_true_positive_arabic_age_conflict():
    """真阳性（阿拉伯 + 名字含中文数字）：fact『52岁』↔ 正文『60岁』→ 必报。
    守护混排正则修复：旧版会因『三52』解析失败漏掉此真矛盾。"""
    r = _run("张三", "张三52岁", "张三说自己60岁。")
    assert r["conflicts_count"] == 1, r
    assert r["code"] == "LOCKED_FACT_CROSS_SCENE_CONFLICT", r


def test_true_positive_hundreds_age_conflict():
    """真阳性（百位）：fact『一百二十岁』↔ 正文『一百五十岁』→ 必报。"""
    r = _run("老祖", "老祖一百二十岁", "老祖已经一百五十岁高龄。")
    assert r["conflicts_count"] == 1, r
    assert r["code"] == "LOCKED_FACT_CROSS_SCENE_CONFLICT", r


def test_true_positive_survives_distractor_numbers():
    """混合：真矛盾（三十八↔四十）藏在距离/数量噪声中仍报，噪声不掩盖真问题。"""
    r = _run(
        "林惊羽", "林惊羽三十八岁",
        "林惊羽走了三十里路，买了二十个馒头，可档案上写他四十岁。",
    )
    assert r["conflicts_count"] == 1, r
    assert r["code"] == "LOCKED_FACT_CROSS_SCENE_CONFLICT", r
