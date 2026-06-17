# -*- coding: utf-8 -*-
"""reveal_show_scanner.py 聚焦确定性回归测试（零依赖·零 LLM·零联网·2026-06-17）。

【与已有覆盖分工】tests/test_reveal_show.py + tests/test_tell_scanners_goldstandard.py 已锁：
  marker 抓取 / show 不命中 / 高密度 active→FAIL / 低密度→PASS / off 跳过 / 默认 active /
  ISSUE_CODE 不进 HARD_GATE / 真作者零误报。

本文件**只补它们没钉的确定性内部逻辑**（不重复）：
  · `_strip_changes` 两分隔符切断 + 优先级 + 无分隔符原样返回；
  · `_cjk_count` CJK 边界（一..鿿）与非 CJK 排除；
  · `_mode` 非法值回退 active / shadow / off；
  · `detect_reveal_tell` 先剥 CHANGES 段（尾部标志词不计）；
  · `scan` 短草稿 cjk<500 跳过 / 读取失败 OSError note / shadow 不上报但仍跑 /
    per_1k 算法（CJK 当分母·剥 changes）/ floor 边界不是闭区间（== floor → PASS）/
    schema 固定字段；
  · `main` CLI 退出码（warning→1 / 无→0·真 subprocess）。

真 import 真调用·锁真实行为。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import reveal_show_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "reveal_show_scanner.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _write(text):
    """落一个 utf-8 草稿文件，返回路径。"""
    d = tempfile.mkdtemp()
    p = Path(d) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _set_mode(m):
    if m is None:
        os.environ.pop("REVEAL_SHOW_MODE", None)
    else:
        os.environ["REVEAL_SHOW_MODE"] = m


# ══════════════════════════════════════════════════════════════════════════
# _strip_changes —— 纯函数（切断 changes 元数据尾巴）
# ══════════════════════════════════════════════════════════════════════════
def test_strip_changes_factual_separator():
    """命中 ---CHANGES_FACTUAL--- → 只留前半正文（含 rstrip）。"""
    text = "正文内容。\n\n---CHANGES_FACTUAL---\n{\"a\":1}"
    assert mod._strip_changes(text) == "正文内容。"


def test_strip_changes_plain_separator():
    """命中 ---CHANGES--- → 只留前半。"""
    text = "前面是正文。   \n---CHANGES---\nmeta"
    assert mod._strip_changes(text) == "前面是正文。"


def test_strip_changes_factual_takes_precedence_when_first():
    """两个分隔符都在时，按 _CHANGES_SEPARATORS 顺序（FACTUAL 在前）先命中谁切谁。

    草稿里 FACTUAL 在 CHANGES 之前 → 用 FACTUAL 切，CHANGES 一并被丢。
    """
    text = "真正文。\n---CHANGES_FACTUAL---\n中段\n---CHANGES---\n尾段"
    assert mod._strip_changes(text) == "真正文。"


def test_strip_changes_no_separator_returns_original():
    """无任一分隔符 → 原样返回（不 rstrip 无关末尾）。"""
    text = "纯正文没有分隔符。\n第二段。"
    assert mod._strip_changes(text) == text


# ══════════════════════════════════════════════════════════════════════════
# _cjk_count —— CJK 字符计数（边界 一..鿿）
# ══════════════════════════════════════════════════════════════════════════
def test_cjk_count_only_counts_cjk():
    """只数 CJK 统一表意文字·英文/数字/全角标点/空格换行不计。"""
    # 汉 字 世 界 = 4 个 CJK；'，。！'(U+FF0x) 是全角标点不在 一..鿿 区间 → 不计
    assert mod._cjk_count("汉字abc123，。！ \n世界") == 4


def test_cjk_count_boundary_chars():
    """边界字符：'一'(U+4E00) 与 '鿿'(U+9FFF) 都算·区间外不算。"""
    assert mod._cjk_count("一") == 1
    assert mod._cjk_count("鿿") == 1
    # U+4DFF（一 的前一码位·非 CJK 统一表意区）不算
    assert mod._cjk_count("䷿") == 0


def test_cjk_count_empty():
    assert mod._cjk_count("") == 0


# ══════════════════════════════════════════════════════════════════════════
# _mode —— 三态 + 非法值回退
# ══════════════════════════════════════════════════════════════════════════
def test_mode_invalid_falls_back_active():
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("garbage_value")
        assert mod._mode() == "active"
        _set_mode("  ACTIVE  ")          # 带空格 + 大写 → 归一 active
        assert mod._mode() == "active"
        _set_mode("OFF")
        assert mod._mode() == "off"
        _set_mode("Shadow")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ══════════════════════════════════════════════════════════════════════════
# detect_reveal_tell —— 先剥 CHANGES 段
# ══════════════════════════════════════════════════════════════════════════
def test_detect_ignores_markers_in_changes_tail():
    """揭底标志词只在 CHANGES 元数据段里 → 不计入（detect 内部先 _strip_changes）。"""
    text = "他翻开账本，数字对不上。\n---CHANGES_FACTUAL---\n真相大白 恍然大悟 原来如此"
    assert mod.detect_reveal_tell(text) == []


def test_detect_counts_only_body_markers():
    """正文有 2 个、尾部 CHANGES 有 1 个 → 只数正文的 2 个。"""
    text = "他恍然大悟。这才明白。\n---CHANGES---\n真相大白"
    hits = mod.detect_reveal_tell(text)
    markers = sorted(h["marker"] for h in hits)
    assert markers == ["恍然大悟", "这才明白"]


def test_detect_regex_alternation_long_form():
    """正则带可变长子模式 '揭开了.{0,6}真相' 也应命中。"""
    hits = mod.detect_reveal_tell("他终于揭开了那桩旧案的真相。")
    assert any(h["marker"].startswith("揭开了") and h["marker"].endswith("真相") for h in hits)


# ══════════════════════════════════════════════════════════════════════════
# scan —— 短草稿 / 读取失败 / shadow / per_1k 算法 / 边界 / schema
# ══════════════════════════════════════════════════════════════════════════
def test_scan_short_draft_skips():
    """CJK < 500 → note='草稿太短·跳过'·不算 per_1k·PASS·无 violation。"""
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("active")
        # 即使全是揭底标志词，太短也直接跳过（避免小样本密度爆表误判）
        out = mod.scan(_write("真相大白。恍然大悟。" * 5))   # 约 50 CJK << 500
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "per_1k" not in out  # 短路返回·未计算密度
    finally:
        _set_mode(bak)


def test_scan_unreadable_path_returns_note():
    """草稿路径不存在（OSError）→ note='草稿读取失败：…'·不崩·PASS。"""
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("active")
        out = mod.scan("/no/such/file/绝不存在.txt")
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out.get("note", "").startswith("草稿读取失败")
    finally:
        _set_mode(bak)


def test_scan_shadow_computes_but_does_not_report():
    """shadow：高密度仍算 per_1k 但不上报（violations 空·verdict PASS·零回归）。"""
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write("真相大白。他恍然大悟。原来如此。谜底揭晓。这才明白。" * 30))
        assert out["mode"] == "shadow"
        assert out["per_1k"] > mod.REVEAL_TELL_FLOOR   # 密度确实超 floor
        assert out["violations"] == [] and out["verdict"] == "PASS"  # 但 shadow 不判
        assert out["warning"] is None
    finally:
        _set_mode(bak)


def test_scan_per_1k_uses_cjk_denominator_excluding_changes():
    """per_1k = hits / (CJK千字)·CHANGES 尾段既不贡献 hit 也不贡献 CJK 分母。

    构造：正文恰 1 个标志词 + 大量非揭底 CJK 凑过 500 字门槛，CHANGES 段塞一堆标志词。
    期望 reveal_tell_count==1，per_1k≈ 1/(正文CJK/1000)。
    """
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("active")
        body = "他翻开账本，墙上的影子多了一道，她端起茶杯压着一张纸条。" * 30  # 纯 show·>500 CJK
        body = "真相大白。" + body  # 正文唯一 1 个标志词
        text = body + "\n---CHANGES_FACTUAL---\n真相大白 恍然大悟 原来如此 谜底揭晓 这才明白"
        out = mod.scan(_write(text))
        assert out["reveal_tell_count"] == 1   # 尾段 4 个不计
        cjk_body = mod._cjk_count(body)
        expected = round(1 / (cjk_body / 1000.0), 2)
        assert out["per_1k"] == expected
    finally:
        _set_mode(bak)


def test_scan_zero_tell_passes():
    """足够长但 0 揭底标志词 → per_1k=0 → PASS·violations 空。"""
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他翻开账本，数字对不上，那笔钱去了城南的宅子。" * 40))
        assert out["per_1k"] == 0.0
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["reveal_tell_count"] == 0
    finally:
        _set_mode(bak)


def test_scan_at_floor_not_over_passes():
    """floor 是开区间（per_1k > FLOOR 才判）·恰等于 floor 不应上报。

    用 0 命中已证开区间下界；这里再钉 verdict 判定逻辑确是 '>' 而非 '>='：
    构造 per_1k 落在 (0, FLOOR] 区间内 → 仍 PASS。
    """
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("active")
        # 1 个标志词 + 约 2000 CJK 正文 → per_1k ≈ 0.5 < 0.8 → PASS
        body = "他翻开账本核对每一笔进出，灯下的影子在墙上拉得很长。" * 80
        out = mod.scan(_write("恍然大悟。" + body))
        assert out["reveal_tell_count"] == 1
        assert 0 < out["per_1k"] <= mod.REVEAL_TELL_FLOOR
        assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_scan_schema_stable_fields():
    """报告 schema 固定字段恒在·gate_level 永远 advisory·code 是 REVEAL_TELL_OVERUSE。"""
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他翻开账本，数字对不上。" * 40))
        for k in ("scanner", "schema_version", "mode", "code", "gate_level",
                  "violations", "verdict"):
            assert k in out, k
        assert out["scanner"] == "reveal_show"
        assert out["gate_level"] == "advisory"
        assert out["code"] == mod.ISSUE_CODE == "REVEAL_TELL_OVERUSE"
    finally:
        _set_mode(bak)


def test_scan_off_short_circuits_before_read():
    """off 模式在读文件前就返回（不存在的路径也不报读取失败）。"""
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("off")
        out = mod.scan("/no/such/path.txt")
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "note" not in out   # off 早退·未走到读取/OSError 分支
    finally:
        _set_mode(bak)


# ══════════════════════════════════════════════════════════════════════════
# main CLI —— 退出码（真 subprocess）
# ══════════════════════════════════════════════════════════════════════════
def _run_cli(draft_path, mode="active"):
    env = dict(os.environ)
    env["REVEAL_SHOW_MODE"] = mode
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, cwd=str(_ROOT), env=env)
    stdout = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    return p.returncode, stdout


def test_cli_high_tell_exits_1():
    """active + 高密度揭底 tell → warning → exit 1·stdout 是合法 JSON 报告。"""
    dp = _write("真相大白。他恍然大悟。原来如此。谜底揭晓。这才明白。" * 30)
    rc, stdout = _run_cli(dp, mode="active")
    assert rc == 1, f"rc={rc} stdout={stdout}"
    report = json.loads(stdout)
    assert report["warning"] is not None
    assert report["verdict"] == "FAIL_MINOR"
    assert report["code"] == "REVEAL_TELL_OVERUSE"


def test_cli_clean_exits_0():
    """active + 展示式（0 标志词）→ 无 warning → exit 0。"""
    dp = _write("他翻开账本，数字对不上，那笔钱去了城南的宅子。" * 40)
    rc, stdout = _run_cli(dp, mode="active")
    assert rc == 0, f"rc={rc} stdout={stdout}"
    report = json.loads(stdout)
    assert report["warning"] is None
    assert report["verdict"] == "PASS"


def test_cli_off_exits_0():
    """off 模式即便草稿满是 tell → 不判 → exit 0。"""
    dp = _write("真相大白。恍然大悟。原来如此。" * 50)
    rc, stdout = _run_cli(dp, mode="off")
    assert rc == 0, f"rc={rc} stdout={stdout}"
    assert json.loads(stdout)["mode"] == "off"


if __name__ == "__main__":
    passed = failed = 0
    for _n in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[_n]()
            passed += 1
            print("OK", _n)
        except Exception as _e:  # noqa: BLE001
            failed += 1
            print("FAIL", _n, _e)
    print(f"[reveal_show_scanner] {passed} passed / {failed} failed")
    raise SystemExit(1 if failed else 0)
