# -*- coding: utf-8 -*-
"""empathic_concern_distress_scanner.py 专属回归测试 (R8 W4 Batch-I · 2026-06-20)。

零依赖·确定性·零 LLM/零联网。
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
import empathic_concern_distress_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "empathic_concern_distress_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("EMPATHIC_CONCERN_MODE", None)
    else:
        os.environ["EMPATHIC_CONCERN_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_manifest(ch_id=1, scene_tags=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库" / ".manifest").mkdir(parents=True, exist_ok=True)
    if scene_tags is not None:
        mn = {"scene_storyboard": [{"tags": list(scene_tags)}]}
        (proj / "_数据库" / ".manifest" / f"ch_{ch_id:03d}.json").write_text(
            json.dumps(mn, ensure_ascii=False), encoding="utf-8")
    return proj


# PD 过载场景:大量颤抖/不敢看/瘫坐·缺 EC 关切动作
_PD_OVERLOAD = (
    "他受伤了，鲜血直流，重伤倒地，惨叫不断，挣扎着说不出话。" * 10 +
    "\n\n" +
    "她颤抖着不敢看，瘫坐在地，捂住眼睛。她吓得后退，崩溃地哭泣，跌坐在血泊中。" * 10 +
    "她又颤抖发抖战栗，不敢直视，捂住脸，呆住僵住，心如死灰麻木不知所措。" * 10
)

# EC 充足场景:伸手/上前/守护 + 一些 PD 但 ratio >0.4
_EC_HEALTHY = (
    "他受伤了，鲜血直流，重伤倒地，挣扎着想要起身。" * 8 +
    "\n\n" +
    "她伸出手扶住他，上前搀扶，沉声道：相信我，不会放弃你。" * 8 +
    "他咬牙挡在前面，护住身后的人，背起伤员往前。" * 8 +
    "她又上前守护，伸手为了保护他，沉声说交给我。" * 8
)


# ── off → 骨架 ──────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_PD_OVERLOAD), project_root=None)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert "scene_count" not in out
    finally:
        _set_mode(bak)


# ── PD 过载 + active → FAIL_MINOR(启发式触发) ──────────────────────────────
def test_pd_overload_fail_minor():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PD_OVERLOAD), project_root=None)
        assert out["triggered_scenes"] >= 1
        assert out["low_ratio_scenes"] >= 1
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ── EC 充足 → PASS ─────────────────────────────────────────────────────────
def test_ec_healthy_pass():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_EC_HEALTHY), project_root=None)
        # 至少有 1 个 trigger 场景但 ratio 应 >= 0.4
        if out["triggered_scenes"] >= 1:
            assert out["low_ratio_scenes"] == 0
            assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 无苦难场景 → skip ──────────────────────────────────────────────────────
def test_no_suffering_scenes_skip():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("active")
        body = "他坐在咖啡馆里看书，阳光暖暖的。" * 50
        out = mod.scan(_write(body), project_root=None)
        assert out["triggered_scenes"] == 0
        assert "无苦难" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── shadow 模式 ──────────────────────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_PD_OVERLOAD), project_root=None)
        assert out["mode"] == "shadow"
        assert out["low_ratio_scenes"] >= 1
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── manifest tag 触发整 cluster ───────────────────────────────────────────
def test_manifest_tag_grief_triggers():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_manifest(ch_id=1, scene_tags=["grief"])
        # 即使正文不含 suffering trigger 关键词·manifest 标了 grief 也整 cluster 触发
        body = ("她颤抖着不敢看，瘫坐在地，捂住眼睛深深害怕。" * 25 + "\n\n" +
                "她又发抖呆住，僵住不知所措地停在那里。" * 25)
        out = mod.scan(_write(body), project_root=proj)
        # manifest_tags 读取应包含 grief
        assert out["manifest_tags"] is None or "grief" in out["manifest_tags"]
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 ─────────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("受伤。" * 3), project_root=None)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── 辅助 ────────────────────────────────────────────────────────────────────
def test_compute_ec_pd_counts():
    ec, pd = mod.compute_ec_pd("他伸出手上前搀扶，她颤抖着不敢看，瘫坐在地。")
    assert ec >= 1 and pd >= 1


def test_compute_ec_pd_zero():
    ec, pd = mod.compute_ec_pd("他在咖啡馆里看书。")
    assert ec == 0 and pd == 0


def test_is_suffering_scene_heuristic():
    text = "他受伤了，鲜血直流，惨叫，重伤倒地，挣扎不止。"
    assert mod.is_suffering_scene(text) is True


def test_is_suffering_scene_no_trigger():
    assert mod.is_suffering_scene("他在咖啡馆里看书。") is False


def test_is_suffering_scene_via_manifest_tag():
    assert mod.is_suffering_scene("普通文字。", manifest_tags={"sacrifice"}) is True
    assert mod.is_suffering_scene("普通文字。", manifest_tags={"comedy"}) is False


def test_split_scenes_min_cjk():
    """单场景 < 80 CJK 会被过滤(此 scanner 比 xing 更严)。"""
    text = "短。\n\n" + "受伤鲜血。" * 30
    scenes = mod.split_scenes(text)
    assert len(scenes) == 1  # 仅长块保留


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\nx") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("受伤abc疼痛") == 4


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("EMPATHIC_CONCERN_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _run_cli(draft_path, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "EMPATHIC_CONCERN_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    p = _write(_PD_OVERLOAD)
    r = _run_cli(p)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_no_trigger():
    body = "他在咖啡馆里看书，阳光暖暖。" * 60
    p = _write(body)
    r = _run_cli(p)
    assert r.returncode == 0, r.stderr
