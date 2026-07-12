# -*- coding: utf-8 -*-
"""deep_writing_dims 注入 —— D1/D2/D3 创作维度解析段注入生成点近邻。

守护：active 注入位置正确（manifest 后·生成点前）+ shadow（默认）/off 零回归 +
无字段/空 tip 不注入。默认 shadow（tip 大段创作提示·注入价值 + context 成本需 gen-model A/B）。
零依赖·仓库根 pytest 入口。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_writer as gw  # noqa: E402

_DD_HEADING = "深层创作维度"
_DD_TIP = "【深层维度内容_DD_5151】先生理本能反应再认知最后命名情绪。"
_GEN_POINT = "# 现在执行润色"


def _make_project(tmp, with_dd=True, empty_tip=False):
    db = tmp / "_数据库"
    (db / ".manifest").mkdir(parents=True, exist_ok=True)
    manifest_obj = {"note": "MANIFEST_DD_TEST", "facts": ["x"]}
    if with_dd:
        tip = "" if empty_tip else _DD_TIP
        manifest_obj["deep_writing_dims"] = {
            "advisory_only": True,
            "D1_psychic_distance": {"label": "心理距离", "tip": tip},
            "D2_visceral_first_emotion": {"label": "情绪顺序", "tip": "先生理后命名。"},
            "D3_motivation_arc": {"label": "动机弧光", "tip": "Ghost→Lie→Want。"},
        }
    for ci in (1, 2):
        (db / ".manifest" / f"ch_{ci:03d}.json").write_text(
            json.dumps(manifest_obj, ensure_ascii=False), encoding="utf-8")
    (db / "作者风格_skill.md").write_text("# 风格\n句长偏短。\n", encoding="utf-8")
    (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {
        "cluster_001": {"scene_storyboard": [{"ch": 1, "beat": "开场"}]}}}, ensure_ascii=False), encoding="utf-8")
    (db / "人物卡.json").write_text(json.dumps({"主角": {"name": "阿渝"}}, ensure_ascii=False), encoding="utf-8")
    (db / "用户偏好.json").write_text(json.dumps({"tone": "冷峻"}, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "scope_summary": "开场。"}]}, ensure_ascii=False), encoding="utf-8")
    return tmp


def _build(root, dd_mode="active", ctx_mode="active"):
    saved = {k: os.environ.get(k) for k in ("DEEP_DIMS_INJECT_MODE", "CTX_REORDER_MODE")}
    os.environ["DEEP_DIMS_INJECT_MODE"] = dd_mode
    os.environ["CTX_REORDER_MODE"] = ctx_mode
    try:
        system, user, _trace = gw.build_prompt(root, cluster_id=1, ch_start=1, polish_view={'idx': 0, 'total': 1, 'scene_text': '井边的场景稿正文。' * 10})
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return system, user


def test_default_shadow():
    """env 未设 → 默认 shadow（影子·tip 大段注入价值需 gen-model A/B）。"""
    prev = os.environ.pop("DEEP_DIMS_INJECT_MODE", None)
    try:
        assert gw._deep_dims_inject_mode() == "shadow"
    finally:
        if prev is not None:
            os.environ["DEEP_DIMS_INJECT_MODE"] = prev


def test_active_injects_near_gen_point():
    """active：深层维度段出现·tip 进 prompt·位置在 manifest 后、生成点前（风格锚区）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _s, user = _build(root, dd_mode="active")
        pos = user.find(_DD_HEADING)
        pos_manifest = user.find("## manifest")
        pos_gen = user.find(_GEN_POINT)
        assert pos != -1 and _DD_TIP in user
        assert pos_manifest < pos < pos_gen


def test_shadow_off_zero_regression():
    """shadow（默认）/ off → 解析段不出现（零回归·tip 在 raw dump 是预期）。"""
    for mode in ("shadow", "off"):
        with tempfile.TemporaryDirectory() as td:
            root = _make_project(Path(td))
            _s, user = _build(root, dd_mode=mode)
            assert _DD_HEADING not in user, f"{mode} 不应注入深层维度解析段"


def test_no_field_no_inject():
    """manifest 无 deep_writing_dims → active 也不注入（零回归）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), with_dd=False)
        _s, user = _build(root, dd_mode="active")
        assert _DD_HEADING not in user


def test_empty_tip_partial():
    """D1 tip 空但 D2/D3 有 → 仍注入段（D2/D3·D1 跳过·防空维度）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), empty_tip=True)
        _s, user = _build(root, dd_mode="active")
        assert _DD_HEADING in user      # D2/D3 有 tip
        assert _DD_TIP not in user      # D1 空 tip 跳过
