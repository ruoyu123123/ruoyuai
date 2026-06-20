"""anachrony_order_scanner 专属测试 — Genette 时序 order（advisory · 2026-06-20）

钉死：
  · analepsis 三子类 + prolepsis 命中
  · 作者档 anachrony_baseline 第一权威
  · 通用兜底 floor
  · 单向只报扁平（密集回顾不报）
  · mode=off/shadow/active 行为
  · 永远 advisory · 绝不 hard_gate
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import anachrony_order_scanner as aos  # noqa: E402


def _filler(n=22):
    return "\n".join(["夜风扫过山脊石阶上落满松针他独自向上踏步影子拉得很长。"] * n)


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if baseline is not None:
        obj["anachrony_baseline"] = baseline
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# ---------- detect_anachrony ----------

def test_detect_analepsis_subtypes():
    """analepsis 三子类（internal/external/hetero）分别命中。"""
    text = "刚才他犹豫了。小时候母亲讲过故事。古书有云此地有龙。"
    r = aos.detect_anachrony(text)
    assert r["analepsis"]["internal_homo"] >= 1   # 刚才
    assert r["analepsis"]["external_homo"] >= 1   # 小时候
    assert r["analepsis"]["hetero"] >= 1          # 古书有云
    assert r["analepsis"]["total"] >= 3


def test_detect_prolepsis_hits():
    """prolepsis 锚词命中。"""
    text = "后来他才明白，多年后这段记忆仍在。"
    r = aos.detect_anachrony(text)
    assert r["prolepsis_total"] >= 2
    assert "prolepsis" in r["samples"]


def test_detect_clean_no_hits():
    """无时序锚词文本·全 singulative。"""
    r = aos.detect_anachrony("他走进山门青石阶上落满松针师兄煮茶炉火映眉眼。")
    assert r["analepsis"]["total"] == 0
    assert r["prolepsis_total"] == 0


# ---------- scan() mode ----------

def test_scan_active_flat_reports():
    """active 模式·全线性叙述无时序锚 → FAIL_MINOR + warning。"""
    p = _write(_filler())
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "active"
        r = aos.scan(str(p))
        assert r["verdict"] == "FAIL_MINOR"
        assert r["warning"]
        assert r["violations"][0]["kind"] == "anachrony_order_thin"
        assert r["violations_count"] == 1
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_active_rich_pass():
    """active 模式·有充足时序锚（混搭 analepsis+prolepsis）→ PASS。"""
    # 密集时序锚
    text = "\n".join([
        "刚才他想起小时候的事。从前父亲也是这样教的。" + _filler(2),
        "后来他才明白多年后这段记忆仍在。当年师兄曾说历史上有人到过这里。",
        _filler(20),
    ])
    p = _write(text)
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "active"
        r = aos.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["analepsis_per_1k"] > 0
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    """shadow 模式即便超阈值也不上报。"""
    p = _write(_filler())
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "shadow"
        r = aos.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["mode"] == "shadow"
        assert r["violations"] == []
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_off_skeleton():
    """off 模式直接骨架返回。"""
    p = _write(_filler())
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "off"
        r = aos.scan(str(p))
        assert r["mode"] == "off"
        assert r["violations"] == []
        assert "analepsis_per_1k" not in r
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_default_shadow():
    """未设 env → 默认 shadow。"""
    p = _write(_filler())
    try:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        r = aos.scan(str(p))
        assert r["mode"] == "shadow"
    finally:
        p.unlink(missing_ok=True)


def test_scan_short_skip():
    """草稿 < 500 CJK 跳过。"""
    p = _write("刚才。")
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "active"
        r = aos.scan(str(p))
        assert "太短" in r.get("note", "")
        assert r["verdict"] == "PASS"
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_invalid_mode_falls_to_shadow():
    """非法 env 值回退 shadow。"""
    p = _write(_filler())
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "bogus"
        r = aos.scan(str(p))
        assert r["mode"] == "shadow"
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


# ---------- author baseline ----------

def test_baseline_author_profile_first():
    """作者档 baseline 第一权威。"""
    p = _write(_filler())
    proj = _mk_project(baseline={"analepsis_per_1k_min": 5.0, "prolepsis_per_1k_min": 5.0})
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "active"
        r = aos.scan(str(p), project_root=proj)
        # 作者档极高阈值·无锚必报扁平
        assert r["baseline_source"] == "author_profile"
        assert r["thresholds"]["analepsis_floor"] == 5.0
        assert r["verdict"] == "FAIL_MINOR"
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


def test_baseline_no_project_fallback():
    """无 project_root → fallback。"""
    p = _write(_filler())
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "active"
        r = aos.scan(str(p))
        assert r["baseline_source"] == "default_fallback"
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


def test_baseline_bad_json_falls_back():
    """坏 JSON 不崩 fallback。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{ bad", encoding="utf-8")
    p = _write(_filler())
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "active"
        r = aos.scan(str(p), project_root=proj)
        assert r["baseline_source"] == "default_fallback"
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


# ---------- advisory border ----------

def test_always_advisory():
    p = _write(_filler())
    try:
        os.environ["ANACHRONY_ORDER_MODE"] = "active"
        r = aos.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_in_hard_gate():
    assert aos.ISSUE_CODE == "ANACHRONY_ORDER_THIN"
    try:
        import audit_hub
        assert "ANACHRONY_ORDER_THIN" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass


def test_strip_changes():
    """CHANGES 分隔符切除。"""
    assert aos._strip_changes("正文。\n---CHANGES---\nlog") == "正文。"


def test_read_failure():
    """读不到文件返回 note。"""
    os.environ["ANACHRONY_ORDER_MODE"] = "active"
    try:
        r = aos.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "读取失败" in r.get("note", "")
    finally:
        os.environ.pop("ANACHRONY_ORDER_MODE", None)
