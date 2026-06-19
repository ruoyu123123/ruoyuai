"""scene_grounding_scanner 专属测试 — 白房间综合症/欠写检测（advisory · 2026-06-19）

钉死：
  · 场景开头 0 接地锚点 → white_room · ratio 正确
  · 场景开头有空间/感官锚点 → 不算 white_room → PASS
  · 承接同一地点（仍/还在/这里）→ 豁免（非白房间）
  · mode=off → 不跑 · mode=shadow → 不上报(零回归) · mode=active → 上报
  · 永远 advisory · 绝不 hard_gate
  · 草稿 < 500 CJK → 跳过
  · 作者档极简留白风 → 豁免
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import scene_grounding_scanner as sgs  # noqa: E402


# 一个 ~50 CJK 的纯抽象对话单元：0 空间/感官锚点·0 延续词（白房间素材）
_ABSTRACT_UNIT = (
    "「你为什么要否定我所做的一切。」\n"
    "「我没有否定你，只是觉得我们想法不同。」\n"
    "「不同？你分明就是不信任我。」\n"
    "「够了，这样争论毫无意义。」"
)
# 一个含接地锚点的开头（窗/院/里/风/声）
_GROUNDED_PREFIX = "他独自站在窗边，望着院里的那棵老树，听着远处传来的风声。\n"


def _scene_block(prefix: str = "") -> str:
    """4 个抽象单元拼成一个场景（~200 CJK·单元间单空行=同场景）·可选接地前缀。"""
    body = "\n\n".join([_ABSTRACT_UNIT] * 4)
    return (prefix + body) if prefix else body


def _white_room_draft() -> str:
    """3 个白房间场景（双空行分隔=换场）·总 ~600 CJK·ratio 1.0。"""
    return "\n\n\n".join([_scene_block(), _scene_block(), _scene_block()])


def _grounded_draft() -> str:
    """3 个均接地的场景·ratio 0.0。"""
    return "\n\n\n".join([_scene_block(_GROUNDED_PREFIX)] * 3)


def _write_draft(text: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


# ---------- 核心检测逻辑 ----------

def test_abstract_unit_has_no_anchor():
    """白房间素材自身确实 0 接地锚点 + 0 延续词（守护合成数据有效性）。"""
    assert sgs.GROUNDING.search(_ABSTRACT_UNIT) is None
    assert sgs.CONTINUATION.search(_ABSTRACT_UNIT) is None


def test_split_scenes_three():
    """双空行分隔 → 切出 3 个场景。"""
    scenes = sgs.split_scenes(_white_room_draft())
    assert len(scenes) == 3


def test_detect_white_rooms_all_ungrounded():
    """3 个纯抽象场景全是白房间 → ratio 1.0。"""
    r = sgs.detect_white_rooms(_white_room_draft())
    assert r["total_scenes"] == 3
    assert r["ungrounded_count"] == 3
    assert r["ungrounded_scene_ratio"] == 1.0


def test_detect_grounded_clean():
    """场景开头有空间/感官锚点 → 0 白房间。"""
    r = sgs.detect_white_rooms(_grounded_draft())
    assert r["total_scenes"] == 3
    assert r["ungrounded_count"] == 0


def test_continuation_exempt():
    """承接同一地点（这里/仍在）→ 豁免·不算白房间。"""
    cont = "这里仍旧维持着先前的样子。\n" + _ABSTRACT_UNIT
    draft = "\n\n\n".join([cont, cont, cont, _scene_block()])
    r = sgs.detect_white_rooms(draft)
    # 前 3 场景含延续词被豁免·只剩第 4 个纯抽象算白房间
    assert r["ungrounded_count"] == 1


# ---------- scan() 模式 ----------

def test_scan_active_reports_violation():
    """active 模式 ratio 超阈值 → FAIL_MINOR + warning + violation。"""
    p = _write_draft(_white_room_draft())
    try:
        os.environ["SCENE_GROUNDING_MODE"] = "active"
        r = sgs.scan(str(p))
        assert r["verdict"] == "FAIL_MINOR"
        assert r["warning"]
        assert len(r["violations"]) == 1
        assert r["violations"][0]["kind"] == "scene_grounding_thin"
        assert r["violations"][0]["severity"] == "minor"
        assert r["violations_count"] == 1
    finally:
        os.environ.pop("SCENE_GROUNDING_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_clean_pass():
    """active 模式但场景全接地 → PASS·无 violation。"""
    p = _write_draft(_grounded_draft())
    try:
        os.environ["SCENE_GROUNDING_MODE"] = "active"
        r = sgs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
    finally:
        os.environ.pop("SCENE_GROUNDING_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    """shadow 模式即便 ratio 超阈值也不上报（零回归）。"""
    p = _write_draft(_white_room_draft())
    try:
        os.environ["SCENE_GROUNDING_MODE"] = "shadow"
        r = sgs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert r["mode"] == "shadow"
    finally:
        os.environ.pop("SCENE_GROUNDING_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_off_skeleton():
    """off 模式 → 直接返回骨架。"""
    p = _write_draft(_white_room_draft())
    try:
        os.environ["SCENE_GROUNDING_MODE"] = "off"
        r = sgs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["mode"] == "off"
        assert r["violations"] == []
    finally:
        os.environ.pop("SCENE_GROUNDING_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_short_skip():
    """草稿 < 500 CJK → 跳过。"""
    p = _write_draft(_ABSTRACT_UNIT)  # ~50 CJK
    try:
        os.environ["SCENE_GROUNDING_MODE"] = "active"
        r = sgs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert "太短" in r.get("note", "")
    finally:
        os.environ.pop("SCENE_GROUNDING_MODE", None)
        p.unlink(missing_ok=True)


def test_minimalist_author_exempt():
    """作者档极简留白风 → 豁免（即便 ratio 超阈值也不上报）。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"minimalist": True}, ensure_ascii=False), encoding="utf-8")
    p = _write_draft(_white_room_draft())
    try:
        os.environ["SCENE_GROUNDING_MODE"] = "active"
        r = sgs.scan(str(p), str(proj))
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert "极简留白" in r.get("note", "")
    finally:
        os.environ.pop("SCENE_GROUNDING_MODE", None)
        p.unlink(missing_ok=True)


# ---------- advisory 边界 ----------

def test_always_advisory():
    """永远 advisory · 全报文无 hard_gate 字样。"""
    p = _write_draft(_white_room_draft())
    try:
        os.environ["SCENE_GROUNDING_MODE"] = "active"
        r = sgs.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        os.environ.pop("SCENE_GROUNDING_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_in_hard_gate():
    """SCENE_GROUNDING_THIN 不在 audit_hub.HARD_GATE_CODES。"""
    try:
        import audit_hub
        assert "SCENE_GROUNDING_THIN" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass  # audit_hub 不在 path → 不阻断
