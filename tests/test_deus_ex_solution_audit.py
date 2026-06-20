"""deus_ex_solution_audit 专属测试 — Deus Ex Solution Audit（advisory · 2026-06-20）"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import deus_ex_solution_audit as des  # noqa: E402


def _filler(n=25):
    return "\n".join(["夜色压在山脊石阶上他独自向上踏步影子拉得很长。"] * n)


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _write_manifest(is_finale: bool):
    p = Path(tempfile.mkdtemp()) / "manifest.json"
    p.write_text(json.dumps({"is_volume_finale": is_finale}, ensure_ascii=False),
                 encoding="utf-8")
    return p


def _mk_project(history=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if history:
        obj = history
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# ---------- 触发门控 ----------

def test_scan_skips_when_not_finale():
    """非 finale → 跳过。"""
    p = _write(_filler())
    m = _write_manifest(False)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["is_volume_finale"] is False
        assert "非 finale" in r.get("note", "")
        assert r["verdict"] == "PASS"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_no_manifest_skips():
    """无 manifest → 视为非 finale → skip。"""
    p = _write(_filler())
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p))
        assert r["is_volume_finale"] is False
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


# ---------- 模式 ----------

def test_off_skeleton():
    p = _write(_filler())
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "off"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["mode"] == "off"
        assert r["violations"] == []
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_default_shadow():
    p = _write(_filler())
    m = _write_manifest(True)
    try:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        r = des.scan(str(p), manifest_path=str(m))
        assert r["mode"] == "shadow"
    finally:
        p.unlink(missing_ok=True)


def test_invalid_mode_fallback():
    p = _write(_filler())
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "bogus"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["mode"] == "shadow"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_short_skip():
    p = _write("末段。")
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert "太短" in r.get("note", "")
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


# ---------- audit_deus_ex 核心 ----------

def test_audit_external_deus_ex_risk():
    """末段大量外力锚词 + 解决动作 → deus_ex_risk。"""
    body = _filler(30)
    tail = ("突然一道身影从天而降斩了恶龙。" + "没想到天降救兵恰好出现救了主角。" * 4)
    text = body + "\n" + tail
    r = des.audit_deus_ex(text)
    assert r["external_deus_hits_count"] >= 3
    assert r["deus_ex_risk"] is True


def test_audit_clean_anchored():
    """末段元素都在 body / history 中铺垫过 → 不报。"""
    # body 多次提到 林师兄 + 玄铁剑
    body = ("林师兄递给我玄铁剑。" * 3) + _filler(20)
    # tail 解决方案用同样元素
    tail = "我取出玄铁剑挡在身前林师兄出手击败了魔王。"
    text = body + "\n" + tail
    r = des.audit_deus_ex(text)
    assert r["underbacked_count"] == 0


def test_audit_history_helps_anchor():
    """body 没铺垫·history 提到过 → 仍有 anchors。"""
    body = _filler(30)
    tail = "我取出玄铁剑挡在身前击败了魔王。"
    history = "玄铁剑 玄铁剑 玄铁剑"
    r = des.audit_deus_ex(body + "\n" + tail, history_text=history)
    assert r["anchors_per_element"].get("玄铁剑", 0) >= 3


def test_audit_no_elements_no_risk():
    """末段无解决方案锚词 → 无元素 → 无风险。"""
    text = _filler(20) + "\n" + _filler(5)
    r = des.audit_deus_ex(text)
    # external 0 / elements 0 → risk False
    assert r["deus_ex_risk"] is False


# ---------- scan() active ----------

def test_scan_active_deus_ex_reports():
    """finale + 大量外力锚词 → FAIL_MINOR。"""
    body = _filler(30)
    tail = ("突然恶龙出现。" + "没想到天降救兵恰好出现。" * 3 +
            "忽然神兽降临斩了一切。" * 2)
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["verdict"] == "FAIL_MINOR"
        assert r["violations"][0]["kind"] == "deus_ex_solution"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_active_anchored_pass():
    """finale + 元素都铺垫过 → PASS。"""
    body = ("林师兄是我兄长林师兄交给我玄铁剑林师兄教我剑法。" * 3) + _filler(20)
    tail = "我取出玄铁剑林师兄出手击败了魔王。"
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["verdict"] == "PASS"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    body = _filler(30)
    tail = "突然恶龙出现没想到天降救兵恰好出现忽然神兽降临。" * 3
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "shadow"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["mode"] == "shadow"
        assert r["violations"] == []
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


# ---------- history 注入 ----------

def test_scan_history_anchors():
    """history 提到 resolution 元素 → 减少 underbacked。"""
    body = _filler(30)
    tail = "我取出玄铁剑林师兄出手击败了魔王。"
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    proj = _mk_project(history={
        "cluster_001": {"summary": "林师兄登场赠玄铁剑。",
                        "key_events": ["林师兄护我", "玄铁剑出鞘"]},
        "cluster_002": {"summary": "林师兄出手救我玄铁剑斩魔气。"},
    })
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), project_root=proj, manifest_path=str(m))
        # 历史有铺垫→不该是 underbacked
        anchors = r.get("anchors_per_element", {})
        assert sum(anchors.values()) > 0
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


# ---------- helpers ----------

def test_read_manifest_nested():
    """嵌套 cluster.is_volume_finale 读得到。"""
    p = Path(tempfile.mkdtemp()) / "m.json"
    p.write_text(json.dumps({"cluster": {"is_volume_finale": True}}), encoding="utf-8")
    assert des._read_is_volume_finale(str(p), None) is True


def test_read_manifest_bad_json():
    p = Path(tempfile.mkdtemp()) / "m.json"
    p.write_text("{ bad", encoding="utf-8")
    assert des._read_is_volume_finale(str(p), None) is False


def test_strip_changes():
    assert des._strip_changes("正文。\n---CHANGES---\nx") == "正文。"


def test_read_history_list_form():
    """故事块摘要.json 是 list 形式也能读。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps([{"summary": "片段一玄铁剑出场。"}, {"summary": "片段二林师兄登场。"}]),
        encoding="utf-8")
    h = des._read_historical_text(proj)
    assert "玄铁剑" in h
    assert "林师兄" in h


# ---------- advisory ----------

def test_always_advisory():
    body = _filler(30)
    tail = "突然恶龙出现没想到天降救兵恰好出现忽然神兽降临。" * 3
    p = _write(body + "\n" + tail)
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(p), manifest_path=str(m))
        assert r["gate_level"] == "advisory"
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_hard_gate():
    assert des.ISSUE_CODE == "DEUS_EX_SOLUTION"
    try:
        import audit_hub
        assert "DEUS_EX_SOLUTION" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass


def test_read_failure():
    m = _write_manifest(True)
    try:
        os.environ["DEUS_EX_AUDIT_MODE"] = "active"
        r = des.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"), manifest_path=str(m))
        assert "读取失败" in r.get("note", "")
    finally:
        os.environ.pop("DEUS_EX_AUDIT_MODE", None)
