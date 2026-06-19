"""dispreferred_turn_shape_scanner 专属测试 — 非偏好回应『裸拒绝』检测（advisory · 2026-06-20）

钉死：
  · 对话引号内以否决词开头 + 0 缓冲 → 裸拒绝 · bare_ratio 正确
  · 同条引号含缓冲词（嗯/抱歉/可是/其实…）→ 非裸 → PASS
  · dispref_total < 4（样本不足）→ 不报
  · 弯引号“”与方角引号「」都识别 · 去标点后开头判 OPENER
  · mode=off → 骨架 · mode=shadow → 不上报(零回归) · mode=active → 上报
  · 永远 advisory · 绝不 hard_gate · 草稿 < 500 CJK → 跳过
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import dispreferred_turn_shape_scanner as dts  # noqa: E402


# ~28 CJK/行的无引号叙述填充（凑过 500 CJK 门槛·不引入对话/缓冲词）
def _filler(n: int = 22) -> str:
    return "\n".join(["夜色压在山道上他独自往前走脚步声在石阶间一下下回响谁也没跟上来。"] * n)


# 裸拒绝对话（弯引号·开头否决词·同句 0 缓冲）
_BARE = [
    "“不行。”",
    "“做不到。这件事我办不到。”",
    "“拒绝。我不会去那种地方。”",
    "“没门，你死了这条心吧。”",
    "“不可能，绝对不可能让你得逞。”",
]
# 带缓冲的非偏好对话（开头否决词·但同句有 延迟/hedge/account）
_BUFFERED = [
    "“不行，抱歉，我真的帮不了你这个忙。”",
    "“做不到，唉，这事我怕是没那个本事。”",
    "“不同意，可是我能理解你为什么这么想。”",
    "“拒绝，其实我也很为难，只是实在没办法。”",
]


def _wrap(dialogues: list) -> str:
    return _filler() + "\n\n" + "\n".join(dialogues) + "\n\n" + _filler()


def _write_draft(text: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


# ---------- 核心检测逻辑（守护合成数据有效性） ----------

def test_opener_detection():
    """去标点后开头以否决词开头 → True；缓冲/无关开头 → False。"""
    assert dts._is_dispref_opener("不行。") is True
    assert dts._is_dispref_opener("——不行，我说了不去。") is True   # 前导标点剥除
    assert dts._is_dispref_opener("做不到，这事太难。") is True
    assert dts._is_dispref_opener("嗯……我再想想。") is False        # 非否决词开头
    assert dts._is_dispref_opener("我很乐意帮忙。") is False


def test_detect_all_bare():
    """5 条均裸拒绝 → dispref_total 5 · bare_count 5 · ratio 1.0。"""
    r = dts.detect_bare_dispreferred("\n".join(_BARE))
    assert r["dispref_total"] == 5
    assert r["bare_count"] == 5
    assert r["bare_ratio"] == 1.0


def test_detect_buffered_not_bare():
    """开头否决词但同句含缓冲 → 计入 dispref_total 但 0 裸。"""
    r = dts.detect_bare_dispreferred("\n".join(_BUFFERED))
    assert r["dispref_total"] == 4
    assert r["bare_count"] == 0
    assert r["bare_ratio"] == 0.0


def test_detect_corner_brackets():
    """方角引号「」同样识别。"""
    r = dts.detect_bare_dispreferred("「不行。」\n「拒绝，没什么好谈的。」")
    assert r["dispref_total"] == 2
    assert r["bare_count"] == 2


# ---------- scan() 模式 ----------

def test_scan_active_reports_violation():
    """active 模式 bare_ratio 超阈值 → FAIL_MINOR + warning + violation。"""
    p = _write_draft(_wrap(_BARE))
    try:
        os.environ["DISPREFERRED_TURN_SHAPE_MODE"] = "active"
        r = dts.scan(str(p))
        assert r["verdict"] == "FAIL_MINOR"
        assert r["warning"]
        assert len(r["violations"]) == 1
        assert r["violations"][0]["kind"] == "dispreferred_turn_bare"
        assert r["violations"][0]["severity"] == "minor"
        assert r["violations_count"] == 1
        assert r["dispref_total"] == 5
    finally:
        os.environ.pop("DISPREFERRED_TURN_SHAPE_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_clean_pass():
    """active 模式但拒绝均带缓冲 → PASS·无 violation。"""
    p = _write_draft(_wrap(_BUFFERED))
    try:
        os.environ["DISPREFERRED_TURN_SHAPE_MODE"] = "active"
        r = dts.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert r["bare_ratio"] == 0.0
    finally:
        os.environ.pop("DISPREFERRED_TURN_SHAPE_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_min_dispref_guard():
    """非偏好回应样本 < 4（仅 3 条裸拒绝）→ 不报（样本不足）。"""
    p = _write_draft(_wrap(_BARE[:3]))
    try:
        os.environ["DISPREFERRED_TURN_SHAPE_MODE"] = "active"
        r = dts.scan(str(p))
        assert r["dispref_total"] == 3
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
    finally:
        os.environ.pop("DISPREFERRED_TURN_SHAPE_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    """shadow 模式即便超阈值也不上报（零回归）。"""
    p = _write_draft(_wrap(_BARE))
    try:
        os.environ["DISPREFERRED_TURN_SHAPE_MODE"] = "shadow"
        r = dts.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert r["mode"] == "shadow"
    finally:
        os.environ.pop("DISPREFERRED_TURN_SHAPE_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_default_mode_shadow():
    """未设 env → 默认 shadow（零回归）。"""
    p = _write_draft(_wrap(_BARE))
    try:
        os.environ.pop("DISPREFERRED_TURN_SHAPE_MODE", None)
        r = dts.scan(str(p))
        assert r["mode"] == "shadow"
        assert r["violations"] == []
    finally:
        p.unlink(missing_ok=True)


def test_scan_off_skeleton():
    """off 模式 → 直接返回骨架。"""
    p = _write_draft(_wrap(_BARE))
    try:
        os.environ["DISPREFERRED_TURN_SHAPE_MODE"] = "off"
        r = dts.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["mode"] == "off"
        assert r["violations"] == []
    finally:
        os.environ.pop("DISPREFERRED_TURN_SHAPE_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_short_skip():
    """草稿 < 500 CJK → 跳过。"""
    p = _write_draft("\n".join(_BARE))  # 仅对话·远 < 500 CJK
    try:
        os.environ["DISPREFERRED_TURN_SHAPE_MODE"] = "active"
        r = dts.scan(str(p))
        assert r["verdict"] == "PASS"
        assert "太短" in r.get("note", "")
    finally:
        os.environ.pop("DISPREFERRED_TURN_SHAPE_MODE", None)
        p.unlink(missing_ok=True)


# ---------- advisory 边界 ----------

def test_always_advisory():
    """永远 advisory · 全报文无 hard_gate 字样。"""
    p = _write_draft(_wrap(_BARE))
    try:
        os.environ["DISPREFERRED_TURN_SHAPE_MODE"] = "active"
        r = dts.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        os.environ.pop("DISPREFERRED_TURN_SHAPE_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_in_hard_gate():
    """DISPREFERRED_TURN_BARE 不在 audit_hub.HARD_GATE_CODES。"""
    assert dts.ISSUE_CODE == "DISPREFERRED_TURN_BARE"
    try:
        import audit_hub
        assert "DISPREFERRED_TURN_BARE" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass  # audit_hub 不在 path → 不阻断
