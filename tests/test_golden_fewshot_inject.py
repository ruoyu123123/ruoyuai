# -*- coding: utf-8 -*-
"""distill_golden_few_shot 原作金句 few-shot 生成点近邻注入回归。

build_manifest 产 distill_golden_few_shot.passages_by_type（蒸馏 golden_passages 按
scene_type 选的原作金句段）；gen_writer 在 active 模式把它渲染成生成点近邻 few-shot 段
（同 rolling_anchor 范式·每类 1-2 段防爆量）。默认 shadow（文风改善需 gen-model A/B）。
守护：active 注入位置正确 + shadow/off 零回归（解析段 heading 不出现）+ 无字段/空不注入 + 每类限 2 段。
零依赖·仓库根 pytest 入口。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_writer as gw  # noqa: E402

_GFS_HEADING = "原作金句 few-shot"
_GFS_PASSAGE = "【金句内容_GFS_8888】他抬头看天，云在走，心也跟着空了。"
_GEN_POINT = "# 现在执行润色"


def _make_project(tmp, with_gfs=True, empty=False, many=False):
    db = tmp / "_数据库"
    (db / ".manifest").mkdir(parents=True, exist_ok=True)
    manifest_obj = {"note": "MANIFEST_GFS_TEST", "facts": ["x"]}
    if with_gfs:
        if empty:
            pbt = {}
        elif many:
            pbt = {"opening_passages": [f"段{i}_{_GFS_PASSAGE}" for i in range(5)]}  # 5 段·验只取 2
        else:
            pbt = {"opening_passages": [_GFS_PASSAGE], "action_passages": ["战斗金句示范。"]}
        manifest_obj["distill_golden_few_shot"] = {
            "_note": "金句 few-shot", "passages_by_type": pbt, "total_chars": 100,
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


def _build(root, gfs_mode="active", ctx_mode="active"):
    saved = {k: os.environ.get(k) for k in ("GOLDEN_FEWSHOT_INJECT_MODE", "CTX_REORDER_MODE")}
    os.environ["GOLDEN_FEWSHOT_INJECT_MODE"] = gfs_mode
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
    """env 未设 → 默认 shadow（影子·文风改善需 gen-model A/B 再 active）。"""
    prev = os.environ.pop("GOLDEN_FEWSHOT_INJECT_MODE", None)
    try:
        assert gw._golden_fewshot_inject_mode() == "shadow"
    finally:
        if prev is not None:
            os.environ["GOLDEN_FEWSHOT_INJECT_MODE"] = prev


def test_active_injects_near_gen_point():
    """active：few-shot 段出现·金句内容进 prompt·位置在 manifest 后、生成点前（风格锚区）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _s, user = _build(root, gfs_mode="active")
        pos = user.find(_GFS_HEADING)
        pos_manifest = user.find("## manifest")
        pos_gen = user.find(_GEN_POINT)
        assert pos != -1 and _GFS_PASSAGE in user
        assert pos_manifest < pos < pos_gen


def test_shadow_off_zero_regression():
    """shadow（默认）/ off → 解析段不出现（零回归·金句在 raw dump 是预期）。"""
    for mode in ("shadow", "off"):
        with tempfile.TemporaryDirectory() as td:
            root = _make_project(Path(td))
            _s, user = _build(root, gfs_mode=mode)
            assert _GFS_HEADING not in user, f"{mode} 不应注入 few-shot 解析段"


def test_no_field_no_inject():
    """manifest 无 distill_golden_few_shot → active 也不注入（零回归）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), with_gfs=False)
        _s, user = _build(root, gfs_mode="active")
        assert _GFS_HEADING not in user


def test_empty_passages_no_inject():
    """passages_by_type 空 dict → 不注入（零回归·防空段）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), empty=True)
        _s, user = _build(root, gfs_mode="active")
        assert _GFS_HEADING not in user


def test_per_type_limit_two():
    """每类限 2 段防生成点近邻爆量（5 段输入·解析段只渲染前 2 段·第 3 段起不进）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), many=True)
        _s, user = _build(root, gfs_mode="active")
        pos = user.find(_GFS_HEADING)
        pos_gen = user.find(_GEN_POINT)
        sec = user[pos:pos_gen]   # 解析段区间（raw manifest dump 在 pos 之前·不含）
        assert "段0" in sec and "段1" in sec
        assert "段2" not in sec, "第 3 段起不应进解析段（防爆量·每类限 2）"
