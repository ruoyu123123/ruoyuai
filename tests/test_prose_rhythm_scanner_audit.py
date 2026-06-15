"""prose_rhythm_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json [L116] 一处检测正确性修复（北极星⑤：advisory 句法节奏检测·
不干涉创作判断）：

  · [L116/L118/L177] 对话起始引号字符集漏中文弯引号 U+201C(") / U+201D(")。
    旧字面量 '""\'「『（(' 只含 ASCII 直引号 + 「『（( ——项目正典对话 100% 用弯引号
    （凿窍纪/鲛人泪实测），故以弯引号开头的对话行/段被三探针误当【叙述句】计入分母：
      - 探针2 subject_action_streak：弯引号对话句打断本应连续的主语 streak（漏报）；
      - 探针3 subject_start_ratio_high：弯引号对话句稀释主语占比分母（边界翻车成静默）；
      - 探针4 inverted_modifier_mold：弯引号对话段被纳入倒装段判定母数。
    修复：把三处字面量统一抽成模块级常量 _DIALOG_OPEN_CHARS（含 U+201C/U+201D），
    与姊妹 scanner cross_scene_voice_drift_scanner / validate_style 对齐。
    守护点：弯引号开头的对话句/段被正确排除出叙述统计；ASCII 引号、「『（( 仍排除；
    纯叙述行不受影响；三处共用一份常量（防再次漏改）。

跑法：PYTHONIOENCODING=utf-8 python tests/test_prose_rhythm_scanner_audit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import prose_rhythm_scanner as P  # noqa: E402

# 弯引号（项目正典对话格式）。
LQ = "“"  # "
RQ = "”"  # "


def _kinds(r):
    return {v["kind"] for v in r["violations"]}


# ============================================================
# [L116] 常量不变量 —— 字符集必须含弯引号且保留旧字符
# ============================================================

def test_constant_contains_curly_quotes():
    """[L116 核心] _DIALOG_OPEN_CHARS 必须含 U+201C/U+201D（修复点）。"""
    chars = P._DIALOG_OPEN_CHARS
    assert "“" in chars, f"缺左弯引号 U+201C：{chars!r}"
    assert "”" in chars, f"缺右弯引号 U+201D：{chars!r}"


def test_constant_retains_legacy_chars():
    """修复不得丢失旧有引号字符（ASCII 直引号/单引号 + 「『（(）。"""
    chars = P._DIALOG_OPEN_CHARS
    for c in ['"', "'", "「", "『", "（", "("]:
        assert c in chars, f"丢失旧字符 {c!r}：{chars!r}"


def test_constant_exact_codepoints():
    """逐码点钉死字符集 = ASCII双×2 + 弯引号×2 + ASCII单 + 「『（(（防误增误删）。"""
    got = [ord(c) for c in P._DIALOG_OPEN_CHARS]
    expect = [0x22, 0x22, 0x201c, 0x201d, 0x27, 0x300c, 0x300e, 0xff08, 0x28]
    assert got == expect, f"码点不符：{[hex(x) for x in got]}"


def test_three_sites_reference_shared_constant():
    """L116/L118/L177 三处统一引用同一常量（源码层防漏改：旧字面量应彻底消失）。"""
    src = (_SCRIPTS / "prose_rhythm_scanner.py").read_text(encoding="utf-8")
    # 旧字面量片段 `「『（(` 现在只应出现在常量定义那一行。
    occurrences = src.count("「『（(")
    assert occurrences == 1, (
        f"`「『（(` 应只在常量定义出现 1 次，实际 {occurrences} 次"
        "（说明仍有内联字面量未改为引用 _DIALOG_OPEN_CHARS）"
    )
    assert src.count("_DIALOG_OPEN_CHARS") >= 4, "常量定义 + 至少 3 处引用"


# ============================================================
# [L116] 探针 2：弯引号对话句不打断主语 streak
# ============================================================

def test_curly_dialogue_excluded_from_subject_streak():
    """[L116] 主语句之间插入弯引号对话行：修复后对话被跳过，6 句主语连成 streak=6。

    旧代码：弯引号对话被当叙述句(subj=False)插入序列 → 把 streak 切碎成 1，且计入分母。
    """
    narr = "他踏进门。"            # 主语(他)开头叙述句
    dia = LQ + "你是谁" + RQ + "。"  # 弯引号对话行
    text = "\n".join([narr, dia, narr, dia, narr, dia, narr, dia, narr, dia, narr])
    r = P.scan(text)
    m = r["metrics"]
    assert m["narrative_sentences"] == 6, f"对话应被排除，叙述句应为 6：{m}"
    assert m["subject_start_pct"] == 100.0, f"6 句全主语开头应为 100%：{m}"
    assert m["max_subject_streak"] == 6, f"streak 不应被对话打断，应为 6：{m}"
    # 连续 6 ≥ STREAK_MAJOR → 触发 subject_action_streak（修复让其能触发）。
    assert "subject_action_streak" in _kinds(r), r["violations"]


def test_old_behavior_would_dilute_without_fix():
    """对照锚点：用旧字符集（无弯引号）手算同一文本 → streak 被切碎、分母含对话。

    不依赖旧代码，直接用「假设字符集不含弯引号」复算，证明弯引号纳入分母会改变结论。
    """
    narr = "他踏进门。"
    dia = LQ + "你是谁" + RQ + "。"
    text = "\n".join([narr, dia, narr, dia, narr, dia, narr, dia, narr, dia, narr])
    legacy_open = "\"\"'「『（("  # 旧字符集（无 U+201C/U+201D）

    import re as _re
    subj_words = P.PRONOUNS
    narr_is_subj = []
    body = [l.strip() for l in text.split("\n") if l.strip()]
    for p in body:
        for s in _re.split(r"(?<=[。！？…])", p):
            s = s.strip()
            if P.cjk(s) < 2:
                continue
            head_raw = s.lstrip("　 ")
            if head_raw[:1] in legacy_open:   # 旧逻辑：弯引号不在集合 → 不跳过
                continue
            head = head_raw.lstrip(legacy_open)
            narr_is_subj.append(any(head.startswith(w) for w in subj_words))
    # 旧逻辑：对话行被当叙述句(subj=False) → 分母虚高、streak 被切断。
    streak = mx = 0
    for x in narr_is_subj:
        streak = streak + 1 if x else 0
        mx = max(mx, streak)
    assert len(narr_is_subj) == 11, "旧逻辑把 5 条对话也算进叙述句 → 11"
    assert mx == 1, "旧逻辑下主语 streak 被对话切成 1"
    # 与修复后（narr_n=6, streak=6）形成鲜明对照 → 证明 bug 真实改变结论。


# ============================================================
# [L116] 探针 3：弯引号对话句不稀释主语占比
# ============================================================

def test_curly_dialogue_not_counted_in_subject_ratio_denominator():
    """[L116] 叙述句分母只数真叙述句；弯引号对话不进分母（占比不被稀释）。"""
    subj_narr = "他抬起头。"          # 主语开头叙述句
    nonsubj_narr = "门外传来脚步声。"   # 非主语开头叙述句
    dia = LQ + "别动" + RQ + "。"      # 弯引号对话
    # 真叙述句 4 条（2 主语 + 2 非主语）+ 4 条对话。
    text = "\n".join([subj_narr, dia, nonsubj_narr, dia, subj_narr, dia, nonsubj_narr, dia])
    r = P.scan(text)
    m = r["metrics"]
    assert m["narrative_sentences"] == 4, f"分母应只含 4 条真叙述句：{m}"
    assert m["subject_start_pct"] == 50.0, f"2/4 主语开头 → 50%（未被对话稀释）：{m}"


# ============================================================
# [L116/L177] 探针 4：弯引号对话段不计入倒装段母数
# ============================================================

def test_curly_dialogue_paragraph_excluded_from_inverted_mold():
    """[L177] 倒装段首探针：弯引号开头的对话段被标 False，不进倒装命中。"""
    tmp_names = ["陆衍"]
    # 用临时项目提供人物卡（探针4 需 subj_words 才构造倒装正则）。
    import tempfile
    import json as _json
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        db.joinpath("人物卡.json").write_text(
            _json.dumps({"characters": [{"id": n, "name": n} for n in tmp_names]},
                        ensure_ascii=False), encoding="utf-8")
        inverted = "摸出手机的陆衍站在原地。"      # 倒装段首（前置定语+的+主语后置）
        dialogue = LQ + "摸出手机的陆衍" + RQ + "，他重复了一遍。"  # 弯引号开头对话段
        # 3 段倒装 + 2 段弯引号对话（对话段即便字面含倒装短语也不应计入）。
        text = "\n".join([inverted, dialogue, inverted, dialogue, inverted])
        r = P.scan(text, project=proj)
        m = r["metrics"]
        # 修复后：对话段 head 以弯引号开头 → 标 False，倒装命中只来自 3 段真倒装段。
        assert m["inverted_mold_count"] == 3, f"倒装命中应只数 3 段真倒装段：{m}"


def test_non_dialogue_narrative_unaffected():
    """回归保护：不含任何引号的纯叙述文本，统计与修复前等价（未误伤）。"""
    text = "\n".join(["他走进房间。", "门外传来脚步声。", "她回头看了一眼。"])
    r = P.scan(text)
    m = r["metrics"]
    assert m["narrative_sentences"] == 3, f"3 条叙述句全计入：{m}"
    # 主语句 = 他/她 两句 → 2/3 ≈ 66.7%。
    assert m["subject_start_pct"] == round(2 / 3 * 100, 1), m


def test_ascii_quote_dialogue_still_excluded():
    """回归保护：ASCII 直引号开头的对话句仍被排除（修复未破坏旧行为）。"""
    narr = "他踏进门。"
    dia = '"你是谁"。'   # ASCII 直引号
    text = "\n".join([narr, dia, narr, dia, narr])
    r = P.scan(text)
    m = r["metrics"]
    assert m["narrative_sentences"] == 3, f"ASCII 引号对话仍应被排除：{m}"


def test_scan_does_not_crash_on_curly_quotes():
    """烟测：含大量弯引号的文本 scan 不崩、verdict/gate_level 字段完整。"""
    dia = LQ + "这是一段比较长的对话内容用来测试不会崩溃" + RQ + "。"
    text = "\n".join([dia] * 30)
    r = P.scan(text)
    assert r["scanner"] == "prose_rhythm"
    assert r["gate_level"] == "advisory"
    assert r["verdict"] in ("PASS", "FAIL_MINOR", "FAIL_MAJOR")


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
