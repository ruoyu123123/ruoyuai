"""narrative_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 中 file==narrative_scanner.py 的两处修复
（北极星⑤克制：advisory 检测器·确定性数据投影健壮性·不干涉模型创作判断）：

  · [Bug1 · L250] G4 micro-tension `check_paragraph_tension` 取段末末句
    旧代码：`last_sentence = p.split("。")[-2] if "。" in p else p`
    段落中段有「。」但以钩子符（？/……/」）收尾（网文最常见刻意钩子形态）时，
    [-2] 取到倒数第二句、漏掉真正的句末终结符 → 段末张力恒判 0 → 反向告诉
    writer「段末缺钩子」，与项目主推的钩子结尾目标（黄金三章/章末是钩子）相悖。
    修复：对齐姊妹 hook_strength_scanner.py 的「找末句」写法——
        _sents = re.split(r"(?<=[。！？!?」”])|(?<=…)(?!…)", p.strip())
        last_sentence = next((s for s in reversed(_sents) if s and s.strip()), p)
        last_sentence = last_sentence.strip() or p[-30:]
    （lookbehind 切句末终结符之后；省略号 …… 整段视为一个终结符，仅在 … 串末尾切，
      不在串中把 …… 切成 …+…，否则末句只剩单 …、TENSION_END_KW 要求 …… 仍漏。）

  · [Bug2 · L286] G5 repetition `scan_repetition` 同段重复名词计数
    旧代码：`counts[m.group(0)] += 1`
    CONCRETE_NOUN 命中片段带前置修饰字（茶杯/门钥匙），同一核心名词被切成多个
    不同 Counter key → max 计数恒为 1 → 永不触发 > 3 阈值（漏报）。
    实测：「他拿起茶杯，又放下茶杯，再端起茶杯，最后摔了茶杯」（茶杯×4）旧代码 max=1。
    当前实现把命中片段归一为稳定的具体名词 key：
        counts[_repetition_core_key(m.group(0))] += 1
      _repetition_core_key 把片段归一到「核心名词后缀 + 其前 1 字」。

守护点：
  Bug1: 1. ？/……/」 钩子收尾的段不再误判张力 0；2. 平铺陈述段仍 0（无回归）；
        3. 多句平铺 + 钩子收尾时取到真末句；4. 空段/无终结符段不崩。
  Bug2: 1. 有分隔重复（茶杯×4）正确合并计数到 4 → 上报 > 3；
        2. 不同名词各 1 次不误报；3. 归一 key 行为正确。
  反向：复刻旧（pre-fix）逻辑，证明 Bug1/Bug2 在旧代码下确实漏判（钉死 bug 真实性）。

跑法：PYTHONIOENCODING=utf-8 python tests/test_narrative_scanner_audit.py
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import narrative_scanner as ns  # noqa: E402


# ============================================================
# Bug1 [L250] check_paragraph_tension：钩子收尾段不再误判张力 0
# ============================================================

def test_bug1_question_hook_ending_scored():
    """段中有「。」但以问号收尾 → 旧 [-2] 取「他停下脚步」漏判 0；
    修复后取真末句「你到底是谁？」→ TENSION_END_KW 命中 → score>=3。"""
    score = ns.check_paragraph_tension("他停下脚步。你到底是谁？")
    assert score >= 3, score


def test_bug1_ellipsis_hook_ending_scored():
    """段中有「。」但以省略号 …… 收尾 → 旧 [-2] 漏判 0；
    修复后取真末句「门后的影子动了一下……」→ 命中 …… → score>=3。
    （关键：省略号串不被切成单 …，否则末句只剩 … 仍漏。）"""
    score = ns.check_paragraph_tension("……房间。门后的影子动了一下……")
    assert score >= 3, score


def test_bug1_quote_hook_ending_handled():
    """以右引号 」收尾（对话钩子）→ 末句取到含 」的句子（不崩、按规则评分）。"""
    score = ns.check_paragraph_tension("他冷笑一声。「你以为你能逃得掉？」")
    # 末句含 ？→ TENSION 命中
    assert score >= 3, score


def test_bug1_multi_sentence_takes_true_last():
    """多句平铺 + 末句带钩子：取最后一句而非倒数第二句。"""
    p = "他走进房间。他坐下来。他突然听见了什么？"
    score = ns.check_paragraph_tension(p)
    assert score >= 3, score


def test_bug1_plain_ending_no_false_tension():
    """无回归：纯陈述句收尾（无任何钩子词）→ 不命中 TENSION/ACTION → 段末张力低。
    （仅可能因独立短行 +1，绝不会因末句提取错误虚高。）"""
    p = "他走进房间，看了一圈，然后慢慢地坐了下来，闭上了眼睛安静地休息着没有再动弹"
    score = ns.check_paragraph_tension(p)
    assert score == 0, score


def test_bug1_empty_and_no_terminator_no_crash():
    """空段 / 无句末终结符段：不崩（鲁棒性）。"""
    # 不抛异常即可；返回值在 0-5 区间
    for p in ("", "   ", "他慢慢走过去没有任何声音"):
        s = ns.check_paragraph_tension(p)
        assert 0 <= s <= 5, (p, s)


def test_bug1_action_incomplete_still_works():
    """无回归：动作未完成钩子（伸手/刚要…）仍被 ACTION_INCOMPLETE 命中。"""
    score = ns.check_paragraph_tension("门开了。他刚要开口")
    assert score >= 2, score


# ============================================================
# Bug2 [L286] scan_repetition：有分隔的同名词重复正确合并计数
# ============================================================

def test_bug2_repeated_noun_with_separators_detected():
    """茶杯×4（有分隔）→ 旧 m.group(0) 切成 4 个不同 key（拿起茶杯/放下茶杯…）max=1 漏报；
    修复后归一到核心名词 → count=4 > 3 → 上报。"""
    rep = ns.scan_repetition(["他拿起茶杯，又放下茶杯，再端起茶杯，最后摔了茶杯。"])
    assert rep["violations_count"] == 1, rep
    assert rep["violations_top5"][0]["count"] == 4, rep["violations_top5"]


def test_bug2_repeated_multichar_suffix_noun():
    """多字后缀名词（钥匙）+ 一致前缀字「铜」重复 4 次（有分隔）→ 归一到核心「铜钥匙」
    → count=4 > 3 → 上报（验证多字后缀路径，与 茶杯 单字后缀路径对称）。"""
    rep = ns.scan_repetition(["他盯着铜钥匙，又转着铜钥匙，再摩挲铜钥匙，最后亲吻铜钥匙。"])
    assert rep["violations_count"] == 1, rep
    assert rep["violations_top5"][0]["count"] == 4, rep["violations_top5"]


def test_bug2_distinct_nouns_not_flagged():
    """无回归：不同核心名词各出现 1 次 → 不误报。"""
    rep = ns.scan_repetition(["他握着长剑，看了看烟斗，摸了摸戒指，提起灯。"])
    assert rep["violations_count"] == 0, rep


def test_bug2_below_threshold_not_flagged():
    """同名词只重复 3 次（== 阈值，非 > 3）→ 不报（阈值语义不变）。"""
    rep = ns.scan_repetition(["他拿起茶杯，又放下茶杯，再端起茶杯。"])
    assert rep["violations_count"] == 0, rep


def test_bug2_core_key_normalization():
    """_repetition_core_key 归一行为：把命中片段归一到「核心名词后缀 + 其前 1 字」。
    这是把『动词/修饰 + 同名词』的前缀剥掉、让同名词合并计数的关键。"""
    # 单字后缀：长前缀片段归一到「茶杯」（去掉「拿起/放下」动词前缀）→ 同名词合并
    assert ns._repetition_core_key("拿起茶杯") == "茶杯", ns._repetition_core_key("拿起茶杯")
    assert ns._repetition_core_key("放下茶杯") == "茶杯", ns._repetition_core_key("放下茶杯")
    assert ns._repetition_core_key("拿起茶杯") == ns._repetition_core_key("放下茶杯")
    # 多字后缀整体保留 + 其前 1 字（铜钥匙 的核心是「铜钥匙」本身：后缀 钥匙 + 前 1 字 铜）
    assert ns._repetition_core_key("盯着铜钥匙") == "铜钥匙", ns._repetition_core_key("盯着铜钥匙")
    # 无任何已知后缀 → 原样返回（不误归一）
    assert ns._repetition_core_key("某种东西") == "某种东西", ns._repetition_core_key("某种东西")
    # 已知残留边界（triage 明示·非本次根治）：同核心『异前缀字』不合并
    #   门钥匙 vs 铜钥匙 前缀字不同（门≠铜）→ core 不同 → 不合并（需后续重设计 CONCRETE_NOUN 捕获组）
    assert ns._repetition_core_key("门钥匙") != ns._repetition_core_key("铜钥匙")


def test_bug2_empty_and_no_match_no_crash():
    """空段 / 无具体名词段：不崩，0 违规。"""
    rep = ns.scan_repetition(["", "他想了想就走了", "天气很好阳光明媚"])
    assert rep["violations_count"] == 0, rep


# ============================================================
# 反向证明：旧逻辑确实漏判（钉死 bug 真实性）
# ============================================================

def test_old_bug1_logic_missed_hook():
    """复刻旧 Bug1 末句提取（p.split("。")[-2]），证明问号钩子段被误判张力 0。"""
    TENSION = ns.TENSION_END_KW
    ACTION = ns.ACTION_INCOMPLETE

    def _old_score(p):
        last = p.split("。")[-2] if "。" in p else p
        last = last.strip() or p[-30:]
        sc = 0
        if TENSION.search(last):
            sc += 3
        if ACTION.search(last):
            sc += 2
        if len(p.split("\n")[-1]) <= 10:
            sc += 1
        return min(5, sc)

    p = "他停下脚步。你到底是谁？"
    # 旧逻辑漏掉问号 → 不加 3 分（仅可能因短行 +1，绝拿不到 >=3）
    assert _old_score(p) < 3, _old_score(p)
    # 新逻辑修复
    assert ns.check_paragraph_tension(p) >= 3


def test_old_bug2_logic_undercounted():
    """复刻旧 Bug2 计数（counts[m.group(0)]），证明茶杯×4 被切成 max=1 漏报。"""
    p = "他拿起茶杯，又放下茶杯，再端起茶杯，最后摔了茶杯。"
    counts = Counter()
    for m in ns.CONCRETE_NOUN.finditer(p):
        counts[m.group(0)] += 1  # 旧逻辑：原片段当 key
    old_max = max(counts.values()) if counts else 0
    assert old_max <= 1, (old_max, dict(counts))  # 旧逻辑每个不同片段各 1
    # 新逻辑正确合并到 4
    rep = ns.scan_repetition([p])
    assert rep["violations_top5"][0]["count"] == 4, rep["violations_top5"]


# ============================================================
# import 健壮性：模块可正常 import（不崩）
# ============================================================

def test_module_imports_clean():
    """narrative_scanner 模块导入成功 + 关键符号存在（钉死修复未破坏模块结构）。"""
    assert callable(ns.check_paragraph_tension)
    assert callable(ns.scan_repetition)
    assert callable(ns._repetition_core_key)
    assert isinstance(ns.CONCRETE_NOUN, re.Pattern)
    assert isinstance(ns._NOUN_SUFFIX, re.Pattern)


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
