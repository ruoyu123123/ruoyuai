"""repeat_noun_density_scanner 回归测试 — 守护 2026-05-30 修 #2 白名单漏检缺陷。

旧 bug：docstring 称做「那/这 + 量词? + 名词」模式提取，但实现是 ~50 项硬白名单
子串匹配 → 白名单外的高频重复名词（那把剑 / 那道光 / 作者特有名词）全漏检。
本测试断言：
  · 正则提取候选名词 token（兑现 docstring），白名单外的「那把剑」等能被抓；
  · 白名单时代的「那只手」等仍能抓（不回归）；
  · 窗口内同名词 < 阈值 → PASS（不误报）；
  · gate_level 恒为 advisory（顾问非门禁，绝不卡流水线）。

advisory scanner，目标=兑现 docstring 扩大召回。测试只测确定性纯函数，不碰 LLM/agent。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import repeat_noun_density_scanner as r


def test_extract_tokens_returns_noun_not_whitelist_phrase():
    """正则提取 group(2) 名词部分（兑现 docstring），不再做白名单子串匹配。"""
    assert r.extract_tokens("他握紧那把剑，掌心冒汗。") == {"剑"}
    assert r.extract_tokens("这个声音，很陌生。") == {"声音"}
    assert r.extract_tokens("那道光、刺得睁不开眼。") == {"光"}
    # 无指示词 → 空集
    assert r.extract_tokens("风从窗缝灌进来。") == set()


def test_non_whitelist_noun_now_detected():
    """核心回归：白名单外的「那把剑」窗口内 ≥4 次 → 触发 advisory（旧白名单全漏）。"""
    text = "\n".join([
        "他握紧那把剑，掌心冒汗。",
        "这把剑，曾属于父亲。",
        "远处传来脚步声。",
        "那把剑、泛着冷光。",
        "他盯着这把剑，久久不语。",
    ])
    res = r.scan_chapter(text)
    assert res["violations_count"] >= 1, res
    tokens = {v["token"] for v in res["violations"]}
    assert "剑" in tokens, res
    # 命中窗口里「剑」出现 4 次
    v = next(v for v in res["violations"] if v["token"] == "剑")
    assert v["count_in_window"] >= 4, v


def test_whitelist_era_noun_still_detected():
    """不回归：白名单时代覆盖的「那只手」窗口内 ≥4 次仍能抓（now 经正则 keyed on 手）。"""
    text = "\n".join([
        "那只手，伸了过来。",
        "又是那只手，冰凉。",
        "门外有动静。",
        "还是那只手，攥住了门把。",
        "那只手，终于松开。",
    ])
    res = r.scan_chapter(text)
    tokens = {v["token"] for v in res["violations"]}
    assert "手" in tokens, res


def test_below_threshold_passes():
    """窗口内同名词只出现 2 次（< threshold 4）→ PASS，不误报。"""
    text = "\n".join([
        "那道光。",
        "墙上有影子。",
        "那道光。",
        "风停了。",
        "屋里很暗。",
    ])
    res = r.scan_chapter(text)
    assert res["violations_count"] == 0, res
    assert res["verdict"] == "PASS", res


def test_gate_level_always_advisory():
    """顾问非门禁：gate_level 恒为 advisory，绝不卡流水线（北极星原则 ⑤）。"""
    text = "\n".join([
        "他握紧那把剑。",
        "这把剑，是他的命。",
        "无关一句。",
        "那把剑，又出鞘了。",
        "还是那把剑，寒光逼人。",
    ])
    res = r.scan_chapter(text)
    assert res["gate_level"] == "advisory", res


def test_count_token_in_window_counts_demonstrative_phrase():
    """count_token_in_window 按「指示词+名词」组合计数，裸名词出现不计。

    注：名词槽是 1-3 字贪婪捕获，名词后接 CJK 字会被吃进 token；故用
    名词后接标点（确定性切断）的形态做断言——这正是 scanner 可靠命中的场景。"""
    paras = [
        "他握紧那把剑，掌心冒汗。",   # 那+把+剑（剑后标点）→ 计
        "剑客收剑入鞘。",            # 裸「剑」无指示词 → 不计
        "这把剑，泛着冷光。",        # 这+把+剑（剑后标点）→ 计
    ]
    cnt = r.count_token_in_window(paras, 0, 3, "剑")
    assert cnt == 2, cnt
