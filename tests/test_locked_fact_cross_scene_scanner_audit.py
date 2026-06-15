"""locked_fact_cross_scene_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 中 file==locked_fact_cross_scene_scanner.py 的两条修复
（北极星⑤克制：LOCKED_FACT_CROSS_SCENE_CONFLICT 是 hard_gate 穿帮检测器，修的是
确定性数据投影/正则锚定的健壮性，不干涉模型创作判断）：

  · [L125 · fact 侧锚定] 旧代码 `_AGE_RE.search(fact)` 直接对整条 fact 跑贪婪
    `[零一二...百]+`。当角色名以中文数字字结尾（张三/周七/王五——中文小说极常见）且
    locked_fact 写成「名+中文年龄」时，名字尾字会被吃进年龄数字：
      '张三三十八岁' → '三三十八' → _cn_to_int 解析失败 → continue 静默跳过
                      → 真年龄矛盾漏报（hard_gate 真阳性丢失，最坏）；
      '周七十八岁'（名 周七，年龄 18）→ '七十八' → 错值 78。
    修复：抽取年龄前先剥掉 name 前缀（fact.startswith(name) 时），对齐 context 侧锚定
    （北极星⑥ 作者已对 context 侧做了此修，fact 侧被遗漏）。

  · [L84 · extract_ages_near 同句约束] 旧代码在角色名 ±50 字窗口内收集**所有**「数字+岁」，
    scan() 再把首个 != fact_age 的当冲突。相邻句里**另一个角色**的年龄会被误归到本角色：
      林惊羽 fact='林惊羽三十八岁', draft='林惊羽今年三十八岁。他对面坐着的老者四十五岁。'
      → 旧代码报 1 处冲突（四十五岁 误归林惊羽），喂 UNWAIVABLE hard_gate 假阳性。
    修复：把窗口按句末符（。！？；\\n）切段，只采纳与 keyword **同句**的年龄。

守护点：
  1. [L125] 名以中文数字结尾 + 中文年龄 → fact_age 正确解析，真矛盾被检出（旧码静默漏报）；
  2. [L125] '周七十八岁' 解析为 18 而非错值 78；
  3. [L125] 阿拉伯年龄、名不结尾数字等控制组无回归；fact 不以 name 起头时不误剥；
  4. [L84] 相邻句另一角色的年龄不再被误归（hard_gate 假阳性消除）；
  5. [L84] 同句内真年龄矛盾仍触发（不漏报真阳性）；
  6. 年龄一致 / 缺年龄事实 / 缺草稿等控制组安全（不崩、不误报）。

跑法：PYTHONIOENCODING=utf-8 python tests/test_locked_fact_cross_scene_scanner_audit.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import locked_fact_cross_scene_scanner as M  # noqa: E402


# ============================================================
# fixture：临时项目落 人物卡.json + 草稿，跑 scan()
# ============================================================

def _scan(chars, draft_text):
    """把 characters 写进临时项目 _数据库/人物卡.json + 落草稿，返回 scan() 报告 dict。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "人物卡.json").write_text(
            json.dumps({"characters": chars}, ensure_ascii=False), encoding="utf-8"
        )
        draft = proj / "draft.txt"
        draft.write_text(draft_text, encoding="utf-8")
        return M.scan(proj, draft)


def _char(name, fact):
    return {"name": name, "locked_facts": [{"fact": fact}]}


# ============================================================
# [L125] fact 侧锚定：名以中文数字结尾不再吞掉年龄
# ============================================================

def test_name_ending_cjk_digit_fact_age_now_parsed():
    """[L125 核心] 名 张三（结尾『三』）+ fact '张三三十八岁'(38)：
    旧代码贪婪匹配 fact 得 '三三十八' → _cn_to_int=None → continue 静默跳过整条年龄校验
    → 真矛盾（draft 九十岁）漏报。修复后剥 name 前缀得 '三十八岁'→38，矛盾被检出。"""
    rep = _scan([_char("张三", "张三三十八岁")], "张三说自己已经九十岁了。")
    assert "_fatal" not in rep, rep
    assert rep["conflicts_count"] == 1, rep
    assert rep["gate_level"] == "hard_gate", rep
    assert rep["conflicts"][0]["conflict_value"] == "九十岁", rep


def test_name_ending_digit_no_wrong_fused_value():
    """[L125] 名 周七（结尾『七』）+ fact '周七十八岁'（真年龄 18）：
    旧代码会得 '七十八'→错值 78。修复后剥前缀得 '十八岁'→18，draft 五十岁 → 冲突。"""
    rep = _scan([_char("周七", "周七十八岁")], "周七今年才五十岁。")
    assert rep["conflicts_count"] == 1, rep
    assert rep["conflicts"][0]["conflict_value"] == "五十岁", rep


def test_name_ending_digit_consistent_age_no_false_positive():
    """[L125] 名结尾中文数字 + fact 18 + draft 也 18 → 一致，不误报。
    （证明剥前缀后解析出的真年龄是 18，不是错值 78：若是 78 则 draft 十八岁会被判矛盾。）"""
    rep = _scan([_char("周七", "周七十八岁")], "周七今年十八岁。")
    assert rep["conflicts_count"] == 0, rep


def test_arabic_age_still_works_control():
    """[L125 控制组] 张三52岁（阿拉伯年龄）：fact_body 仍能抽出 52，draft 三十岁 → 冲突。无回归。"""
    rep = _scan([_char("张三", "张三52岁")], "张三今年三十岁。")
    assert rep["conflicts_count"] == 1, rep
    assert rep["conflicts"][0]["conflict_value"] == "三十岁", rep


def test_name_not_prefix_of_fact_no_overstrip():
    """[L125] fact 不以 name 开头（'年龄三十八岁'）→ fact_body == fact，不误剥，仍抽出 38。"""
    rep = _scan([_char("张三", "年龄三十八岁")], "张三今年九十岁。")
    assert rep["conflicts_count"] == 1, rep


def test_name_not_ending_digit_unaffected():
    """[L125] 名不以数字结尾（林惊羽，『羽』非数字）+ 中文年龄 → 一直正常，修复不影响。"""
    rep = _scan([_char("林惊羽", "林惊羽三十八岁")], "林惊羽今年九十岁。")
    assert rep["conflicts_count"] == 1, rep


# ============================================================
# [L84] extract_ages_near 同句约束：相邻句别人的年龄不误归
# ============================================================

def test_adjacent_sentence_other_character_age_not_attributed():
    """[L84 核心] 林惊羽 fact 38，draft 第一句『林惊羽今年三十八岁』(一致)、
    第二句『他对面坐着的老者四十五岁』(别人)。旧代码把窗口内 45 当林惊羽 → 假阳性 hard_gate。
    修复后只采纳与 keyword 同句的年龄 → 45 不归林惊羽 → 0 冲突。"""
    rep = _scan(
        [_char("林惊羽", "林惊羽三十八岁")],
        "林惊羽今年三十八岁。他对面坐着的老者四十五岁。",
    )
    assert "_fatal" not in rep, rep
    assert rep["conflicts_count"] == 0, rep
    assert rep["gate_level"] == "advisory", rep


def test_in_sentence_real_contradiction_still_fires():
    """[L84] 同句内真年龄矛盾（林惊羽 fact 38，draft『林惊羽今年九十岁』）仍触发 hard_gate。
    （证明同句约束没把真阳性一并关掉。）"""
    rep = _scan([_char("林惊羽", "林惊羽三十八岁")], "林惊羽今年九十岁。")
    assert rep["conflicts_count"] == 1, rep
    assert rep["conflicts"][0]["conflict_value"] == "九十岁", rep
    assert rep["gate_level"] == "hard_gate", rep


def test_adjacent_age_separated_by_various_seps():
    """[L84] 句末符多形态（！/？/换行）都正确断句：另一角色年龄在分隔后 → 不误归。"""
    for sep in ("！", "？", "\n", "；"):
        draft = f"林惊羽今年三十八岁{sep}老者四十五岁。"
        rep = _scan([_char("林惊羽", "林惊羽三十八岁")], draft)
        assert rep["conflicts_count"] == 0, (sep, rep)


def test_other_character_age_before_keyword_same_window_not_attributed():
    """[L84] 别人年龄在 keyword **之前**的相邻句也不该误归（窗口回看方向）。"""
    rep = _scan(
        [_char("林惊羽", "林惊羽三十八岁")],
        "老者四十五岁。林惊羽今年三十八岁。",
    )
    assert rep["conflicts_count"] == 0, rep


# ============================================================
# 控制组：一致 / 无年龄事实 / 健壮性
# ============================================================

def test_consistent_age_no_conflict():
    """fact 38 + draft 同句 38 → 无冲突（基础一致路径）。"""
    rep = _scan([_char("林惊羽", "林惊羽三十八岁")], "林惊羽今年三十八岁。")
    assert rep["conflicts_count"] == 0, rep
    assert rep["gate_level"] == "advisory", rep


def test_fact_without_age_skipped():
    """fact 无『岁』（外貌/出身类）→ 不进年龄分支，facts_checked 计数但 0 冲突。"""
    rep = _scan([_char("林惊羽", "林惊羽身披黑袍，左目有疤")], "林惊羽今年四十五岁。")
    assert rep["conflicts_count"] == 0, rep
    assert rep["facts_checked"] == 1, rep


def test_distance_quantity_not_misread_as_age():
    """距离/数量数字（三十里 / 五十个）不带『岁』→ _AGE_RE 不匹配 → 不误报（既有锚定保持）。"""
    rep = _scan([_char("林惊羽", "林惊羽三十八岁")], "林惊羽走了三十里，买了五十个馒头。")
    assert rep["conflicts_count"] == 0, rep


def test_missing_draft_returns_fatal_not_crash():
    """草稿文件不存在 → 返回 _fatal（不抛异常）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        rep = M.scan(proj, proj / "nope.txt")
        assert "_fatal" in rep, rep


def test_no_character_card_safe():
    """无人物卡（空 characters）→ 0 冲突、不崩。"""
    rep = _scan([], "随便一段正文，三十八岁的某人路过。")
    assert "_fatal" not in rep, rep
    assert rep["conflicts_count"] == 0, rep
    assert rep["facts_checked"] == 0, rep


def test_malformed_locked_fact_entry_skipped():
    """locked_facts 含非 dict 条目（弱模型自由生成）→ 跳过不崩。"""
    chars = [{"name": "林惊羽", "locked_facts": ["不是字典", None, {"fact": "林惊羽三十八岁"}]}]
    rep = _scan(chars, "林惊羽今年九十岁。")
    assert "_fatal" not in rep, rep
    assert rep["conflicts_count"] == 1, rep


# ============================================================
# extract_ages_near 单元（直接调函数，证同句约束生效）
# ============================================================

def test_extract_ages_near_unit_same_sentence_only():
    """直接调 extract_ages_near：同句『三十八岁』被收，邻句『四十五岁』被同句约束剔除。"""
    text = "林惊羽今年三十八岁。老者四十五岁。"
    got = M.extract_ages_near(text, "林惊羽", window=50)
    nums = [n for _, n in got]
    assert "三十八" in nums, got
    assert "四十五" not in nums, got


# ============================================================
# 零依赖 __main__ runner
# ============================================================

if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"[OK] {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed"
          + (f", {failed} FAILED" if failed else ""))
    sys.exit(1 if failed else 0)
