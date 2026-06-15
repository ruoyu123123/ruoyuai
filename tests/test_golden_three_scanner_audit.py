"""golden_three_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 唯一一条 golden_three_scanner.py 修复（advisory 检测器
健壮性修·北极星⑤：只纠正错误诊断·不触碰门禁·protagonist_onstage 全程 advisory）：

  · [L173] check_protagonist_onstage 的「主角名附近动作动词」窗口：
    旧 `ctx = window[m.start():m.start() + 25]` 是单向「向后」切片——锚点用主角名
    起始处只向后取 25 字，名字「之前」一个字都不看，与 L169 注释承诺的双向「±20」
    矛盾。后果：当主角动作以「前置动作+的+主角名后置」倒装句式出现
    （攥紧拳头的陈默 / 拔出长刀的重黎），动作动词在主角名【之前】→ 旧窗口扫不到
    → has_action=False → G2 误报「主角未在 500 字内行动出场」。
    （该倒装句式模具在 gen-model 输出极常见，见 memory
    feedback-inverted-modifier-sentence-mold-overuse。）

    修复：`ctx = window[max(0, m.start() - 20):m.end() + 20]` 双向窗口。
    守护点：① 倒装（动作在名前）现在能命中；② 正向（动作在名后）仍命中；
    ③ 动作动词距离主角名 > 20 字时仍不命中（窗口有界·不引入假阳性）。

跑法：PYTHONIOENCODING=utf-8 python tests/test_golden_three_scanner_audit.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import golden_three_scanner as g  # noqa: E402


# ------------------------------------------------------------
# 旧（有 bug）单向窗口的本地复刻 —— 仅用于「证明这些用例真能抓住 bug」。
# 测试断言新实现，同时对关键用例验证旧实现会失败（回归护栏）。
# ------------------------------------------------------------

def _old_buggy_has_action(window: str, name: str) -> bool:
    """复刻修复前的 L173 单向向后窗口 window[m.start():m.start()+25]。"""
    for m in re.finditer(re.escape(name), window):
        ctx = window[m.start():m.start() + 25]
        if g.ACTION_VERB.search(ctx):
            return True
    return False


# ============================================================
# [L173] 倒装句式（动作动词在主角名【之前】）—— 核心修复点
# ============================================================

def test_inverted_modifier_mold_now_detects_action():
    """[L173 核心] 「攥紧拳头的陈默」——动作动词「攥」在主角名之前，名字之后纯描写无
    动作动词。旧单向窗口漏判 → has_action=False；修复后双向窗口命中 → True。"""
    body = "攥紧拳头的陈默，一身玄色衣袍，眉宇之间尽是冷峻肃杀的气度。"
    # 前置条件：主角名之后的文本里没有任何 ACTION_VERB（否则用例无法隔离 bug）。
    name = "陈默"
    after = body[body.index(name) + len(name): body.index(name) + len(name) + 22]
    assert not g.ACTION_VERB.search(after), "用例失效：名后含动作动词，无法隔离倒装 bug"
    # 旧实现会漏判（这条断言证明用例确实咬合 bug）：
    assert _old_buggy_has_action(body, name) is False
    # 修复后：双向窗口捕到名字之前的「攥」。
    r = g.check_protagonist_onstage(body, name)
    assert r["has_action_in_500w"] is True, r
    assert r["pass"] is True, r


def test_inverted_modifier_mold_second_sample():
    """[L173] 第二个倒装样本「拎起酒坛的重黎」——动词「拿/握/举」族在名前。"""
    body = "拍了拍灰尘的重黎，身形清瘦，神色淡漠，眉宇间满是疲惫。"
    name = "重黎"
    after = body[body.index(name) + len(name): body.index(name) + len(name) + 22]
    assert not g.ACTION_VERB.search(after), "用例失效：名后含动作动词"
    assert _old_buggy_has_action(body, name) is False
    r = g.check_protagonist_onstage(body, name)
    assert r["has_action_in_500w"] is True, r


# ============================================================
# [L173] 不回归：正向句式（动作在名后）仍命中
# ============================================================

def test_forward_mold_still_detects_action():
    """正向「陈默握紧了拳头，缓缓站起身」——动作在名后，修复前后都应命中（不回归）。"""
    body = "陈默握紧了拳头，缓缓站起身，看向那扇紧闭的门。"
    assert _old_buggy_has_action(body, "陈默") is True  # 旧实现本就 OK
    r = g.check_protagonist_onstage(body, "陈默")
    assert r["has_action_in_500w"] is True, r
    assert r["pass"] is True, r


def test_action_both_sides_detects():
    """名字两侧都有动作动词 → 命中（健全性）。"""
    body = "推开房门的陈默，转身关上门。"
    r = g.check_protagonist_onstage(body, "陈默")
    assert r["has_action_in_500w"] is True, r


# ============================================================
# [L173] 不引入假阳性：动作动词离主角名 > 20 字时仍不命中（窗口有界）
# ============================================================

def test_far_action_verb_not_falsely_matched():
    """主角名出现，但唯一的动作动词距离名字 > 20 字（前后皆远）→ 双向窗口有界，
    不应误判 has_action=True（验证修复没把窗口放得过宽）。"""
    name = "陈默"
    # 名后 30+ 字才出现「跑」；名前是无动作的环境描写。
    body = name + "静静地待在原地" + "，" * 26 + "远处有人跑了过来。"
    r = g.check_protagonist_onstage(body, name)
    assert r["has_action_in_500w"] is False, r
    assert r["pass"] is False, r


def test_name_present_no_action_anywhere_near():
    """主角名出现但附近 ±20 字内确无动作动词 → has_action=False（保持原判定）。"""
    name = "陈默"
    body = name + "，一身玄色长袍，神情冷淡，眉目疏离。窗外，风声呜咽。"
    after = body[body.index(name) + len(name): body.index(name) + len(name) + 22]
    assert not g.ACTION_VERB.search(after)
    r = g.check_protagonist_onstage(body, name)
    assert r["has_action_in_500w"] is False, r


# ============================================================
# [L173] 边界：主角名出现在正文开头（m.start()==0），max(0, ...) 不越界
# ============================================================

def test_name_at_window_start_no_index_error():
    """主角名在窗口最前（m.start()=0）→ max(0, m.start()-20)=0，不抛 IndexError；
    名后紧跟动作动词正常命中。"""
    body = "陈默推门进来，扫了一眼屋里的人。"
    r = g.check_protagonist_onstage(body, "陈默")  # 不应抛异常
    assert r["has_action_in_500w"] is True, r


def test_name_not_in_window_returns_false():
    """主角名不在前 500 字 → appears=False → pass=False（与窗口修复无关，健全性）。"""
    body = "夜色压下来，整座城静悄悄。没有一个人影出现在长街尽头。"
    r = g.check_protagonist_onstage(body, "陈默")
    assert r["appears_in_500w"] is False, r
    assert r["pass"] is False, r


def test_degraded_no_name_path_unaffected():
    """protag_name 为空 → 走降级分支（不进 L173 窗口逻辑），仍正常返回。"""
    body = "他推开门，缓缓走了进来。"
    r = g.check_protagonist_onstage(body, None)
    assert r["detection"] == "degraded_no_name", r
    assert r["has_action_in_500w"] is True, r  # 「推/走」命中降级判定


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
