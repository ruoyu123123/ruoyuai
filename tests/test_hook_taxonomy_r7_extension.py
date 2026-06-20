"""R7 W2 Batch-B：章末钩子 11 型 taxonomy 升级（5→11）确定性回归。

升级前 HOOK_TYPES = 5 类（悬念/冲突/反差/信息缺口/诡异）；
R7 W2 加 6 型 → 11 类：reversal_setup（反转铺垫）/ unfinished_action（未完成动作）/
new_setting（新场景登场）/ decision_pending（待决断）/ promise（承诺/誓言）/ threat（迫近威胁）。

本测试只锚 R7 新增 6 型的确定性识别（既有 5 型 + 评分算法已被 test_hook_strength_scanner.py 全覆盖）。
绝不重复既有断言。零依赖纯标准库。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import hook_strength_scanner as mod  # noqa: E402


def test_hook_types_count_eleven_total():
    """HOOK_TYPES 已从 5 升 11（R7 W2 升级真落地）。"""
    assert len(mod.HOOK_TYPES) == 11, list(mod.HOOK_TYPES)
    # 既有 5 型保留
    for k in ("suspense", "conflict", "contrast", "infogap", "eerie"):
        assert k in mod.HOOK_TYPES, k
    # R7 W2 新增 6 型登记
    for k in ("reversal_setup", "unfinished_action", "new_setting",
              "decision_pending", "promise", "threat"):
        assert k in mod.HOOK_TYPES, k


def test_reversal_setup_kw_fires():
    """反转铺垫钩：『伏笔』『布局多年』『早就料到』等命中。"""
    text = "他暗中布置许久，这一刻终于到了，果然不出所料。"
    hits = mod.detect_hooks_in_text(text)
    assert hits.get("reversal_setup", 0) >= 1, hits


def test_unfinished_action_kw_fires():
    """未完成动作钩：『刚要』『话音未落』『手刚伸出』等。"""
    text = "他刚要开口，门外就传来脚步声，话音未落人已闯入。"
    hits = mod.detect_hooks_in_text(text)
    assert hits.get("unfinished_action", 0) >= 1, hits


def test_new_setting_kw_fires():
    """新场景登场钩：『推开门』『眼前出现』『陌生的地方』等。"""
    text = "他推开门，眼前出现一片从未见过的景象。"
    hits = mod.detect_hooks_in_text(text)
    assert hits.get("new_setting", 0) >= 1, hits


def test_decision_pending_kw_fires():
    """待决断钩：『两难』『杀还是不杀』『犹豫不决』等。"""
    text = "他陷入两难，杀还是不杀，犹豫不决站在原地。"
    hits = mod.detect_hooks_in_text(text)
    assert hits.get("decision_pending", 0) >= 1, hits


def test_promise_kw_fires():
    """承诺/誓言钩：『我发誓』『总有一天』『此仇必报』等。"""
    text = "他立下誓言：总有一天，此仇必报。"
    hits = mod.detect_hooks_in_text(text)
    assert hits.get("promise", 0) >= 1, hits


def test_threat_kw_fires():
    """迫近威胁钩：『正在赶来』『山雨欲来』『迫在眉睫』等。"""
    text = "山雨欲来，他知道大祸临头，对手正在赶来。"
    hits = mod.detect_hooks_in_text(text)
    assert hits.get("threat", 0) >= 1, hits


def test_neutral_prose_no_new_type_false_positive():
    """纯中性叙述：R7 新 6 型零误报（与既有 5 型同等保守）。"""
    text = "早上他起床洗脸吃了饭走到院子里晒了会儿太阳就回屋坐下喝茶看书。"
    hits = mod.detect_hooks_in_text(text)
    for new_type in ("reversal_setup", "unfinished_action", "new_setting",
                     "decision_pending", "promise", "threat"):
        assert new_type not in hits, f"中性叙述误报 {new_type}: {hits}"


def test_type_score_cap_still_six_with_eleven_types():
    """11 类全命中 tail，type_score 仍封顶 6（每类 2 分 · 贵精不贵多守原合约）。"""
    # 塞进 8 类以上命中关键词
    tail = ("他想不通是谁。对方拔刀逼近。竟然不是他写的。话没说完就停住。"
            "门自己开了。他暗中布置许久。他刚要开口。眼前出现陌生的地方。"
            "两难抉择。我发誓总有一天。山雨欲来迫在眉睫。")
    r = mod.score_ending_hook(["前文。", "过渡。", tail])
    assert len(r["hook_type_hits"]) >= 6, r["hook_type_hits"]
    assert r["type_score"] == 6, r  # 封顶不变


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"[hook_taxonomy_r7] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
