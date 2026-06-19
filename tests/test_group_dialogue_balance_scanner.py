"""group_dialogue_balance_scanner 专属测试 — 群戏显式点名过密检测（advisory · 2026-06-19）

钉死：
  · 群戏规模（对话行≥8）+ 显式点名过密 → active 报 FAIL_MINOR + warning
  · 隐式指称（对话行有引号但无专名归属）→ PASS
  · shadow 模式命中阈值也不上报 violations（零回归）
  · 草稿 <500 CJK → 跳过
  · 对话行不足（非群戏规模）→ 跳过群戏判定 · PASS
  · mode=off → 直接 PASS · 永远 advisory · 绝不 hard_gate
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import group_dialogue_balance_scanner as gdb  # noqa: E402

ENV = "GROUP_DIALOGUE_BALANCE_MODE"

# 无引号无归属的叙述填充（绝不含 说/道/问/喊/叫/开口 · 不计入对话行/归属）
_FILLER = "群山连绵云雾缭绕溪水潺潺向远方静静流淌。\n" * 30

# 12 行显式点名归属（专名 + 说类动词）·群戏规模
_NAME_LINES = [
    "张三笑道：“今天天气真好我们一起去爬山看风景吧。”",
    "李四说：“好啊那就这么定下来一起出发。”",
    "老钟道：“我去准备一些干粮和饮用清水。”",
    "王五问：“我们到底要从哪一条山路上去呢。”",
    "陈六喊：“快一点天色不早路上还得赶时间。”",
    "赵七叫道：“前面那条小溪边的景色最好看。”",
    "孙八开口：“要不要带上帐篷今晚就在山上过夜。”",
    "周九说道：“我看天上的云有点厚怕是要变天。”",
    "吴十冷笑道：“你们这些人就是胆子太小怕什么。”",
    "郑大问道：“万一真下雨了我们该往哪里躲避呢。”",
    "王二喊道：“别磨蹭了再不走太阳就要落下去了。”",
    "钱三道：“放心吧这条路我从小走到大闭眼都行。”",
]


def _write_draft(text: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _set_mode(mode):
    if mode is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = mode


# ---------- 核心检测逻辑 ----------

def test_detect_high_ratio():
    """12 行全显式点名 → ratio = 1.0 · 对话行 12。"""
    draft = "\n".join(_NAME_LINES) + "\n" + _FILLER
    m = gdb.detect_group_dialogue_balance(draft)
    assert m["dialogue_lines"] == 12
    assert m["name_attrib_lines"] == 12
    assert m["explicit_name_attrib_ratio"] == 1.0


def test_detect_implicit_low_ratio():
    """隐式指称（有引号无专名归属）→ ratio = 0。"""
    lines = ["“今天天气真好我们一起出去走走看看风景吧。”" for _ in range(12)]
    draft = "\n".join(lines) + "\n" + _FILLER
    m = gdb.detect_group_dialogue_balance(draft)
    assert m["dialogue_lines"] == 12
    assert m["name_attrib_lines"] == 0
    assert m["explicit_name_attrib_ratio"] == 0.0


# ---------- scan() 模式 ----------

def test_scan_active_reports_violation():
    """active + 群戏显式点名过密 → FAIL_MINOR + warning + violation。"""
    draft = "\n".join(_NAME_LINES) + "\n" + _FILLER
    p = _write_draft(draft)
    try:
        _set_mode("active")
        r = gdb.scan(str(p))
        assert r["mode"] == "active"
        assert r["dialogue_lines"] >= gdb.MIN_DIALOGUE_LINES
        assert r["explicit_name_attrib_ratio"] > gdb.RATIO_FLOOR
        assert r["verdict"] == "FAIL_MINOR"
        assert r["warning"]
        assert len(r["violations"]) == 1
        assert r["violations"][0]["kind"] == "group_dialogue_imbalance"
        assert r["violations"][0]["severity"] == "minor"
    finally:
        _set_mode(None)
        p.unlink(missing_ok=True)


def test_scan_active_clean_passes():
    """active + 隐式指称（不命中阈值）→ PASS · 无 violation。"""
    lines = ["“今天天气真好我们一起出去走走看看风景吧。”" for _ in range(12)]
    draft = "\n".join(lines) + "\n" + _FILLER
    p = _write_draft(draft)
    try:
        _set_mode("active")
        r = gdb.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["warning"] is None
        assert r["violations"] == []
    finally:
        _set_mode(None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    """shadow 命中阈值也不上报 violations（零回归）。"""
    draft = "\n".join(_NAME_LINES) + "\n" + _FILLER
    p = _write_draft(draft)
    try:
        _set_mode("shadow")
        r = gdb.scan(str(p))
        assert r["mode"] == "shadow"
        assert r["explicit_name_attrib_ratio"] > gdb.RATIO_FLOOR  # 确实命中阈值
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(None)
        p.unlink(missing_ok=True)


def test_scan_off_returns_skeleton():
    """off → 直接返回骨架 PASS。"""
    draft = "\n".join(_NAME_LINES) + "\n" + _FILLER
    p = _write_draft(draft)
    try:
        _set_mode("off")
        r = gdb.scan(str(p))
        assert r["mode"] == "off"
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
    finally:
        _set_mode(None)
        p.unlink(missing_ok=True)


def test_scan_short_draft_skip():
    """草稿 <500 CJK → 跳过。"""
    p = _write_draft("张三说：“好。”\n" * 5)
    try:
        _set_mode("active")
        r = gdb.scan(str(p))
        assert r["verdict"] == "PASS"
        assert "太短" in r.get("note", "")
    finally:
        _set_mode(None)
        p.unlink(missing_ok=True)


def test_scan_few_dialogue_lines_skip():
    """对话行 <8（非群戏规模）→ 跳过群戏判定 · PASS（即便 active 全点名）。"""
    few = ["张三说：“好的我知道了这件事情就交给我来办理。”",
           "李四道：“那就拜托你了一定要小心谨慎不能出错。”",
           "老钟问：“你们两个到底在商量些什么见不得人的事。”"]
    draft = "\n".join(few) + "\n" + _FILLER
    p = _write_draft(draft)
    try:
        _set_mode("active")
        r = gdb.scan(str(p))
        assert r["dialogue_lines"] < gdb.MIN_DIALOGUE_LINES
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert "非群戏规模" in r.get("note", "")
    finally:
        _set_mode(None)
        p.unlink(missing_ok=True)


# ---------- advisory 边界 ----------

def test_always_advisory():
    """永远 advisory · 整个 report 不出现 hard_gate。"""
    draft = "\n".join(_NAME_LINES) + "\n" + _FILLER
    p = _write_draft(draft)
    try:
        _set_mode("active")
        r = gdb.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        _set_mode(None)
        p.unlink(missing_ok=True)


def test_issue_code_not_in_hard_gate():
    """GROUP_DIALOGUE_IMBALANCE 绝不在 audit_hub.HARD_GATE_CODES。"""
    assert gdb.ISSUE_CODE == "GROUP_DIALOGUE_IMBALANCE"
    try:
        import audit_hub
        assert gdb.ISSUE_CODE not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass  # audit_hub 不在 path → 不阻断
