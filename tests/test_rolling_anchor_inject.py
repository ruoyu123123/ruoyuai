# -*- coding: utf-8 -*-
"""#3 rolling_style_anchor 升格 —— 动态文风锚从 raw-JSON dead-zone 升格生成点近邻回归测试（2026-06-16）。

穷尽核查 wyo52es0z #3：build_manifest._collect_rolling_style_anchor（治 D 级长程文风退化·用本书已写
得最像作者的片段对抗回归均值退化成通用 LLM 腔）此前只 raw JSON 躺 manifest dump 中段 dead zone·
writer 难识别为写作目标。本批 gen_writer 显式解析升格到生成点近邻风格锚区（同族 style_fp/rhythm）。

守护：active 注入且位置正确（manifest 后·生成点前·风格锚区）；shadow（默认）/off 零回归；
缺字段/snippet 全空不注入。默认 shadow（位置升格的文风改善效果需 gen-model A/B 定论·先影子）。
零依赖·run_tests.py / pytest 双跑。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_writer as gw  # noqa: E402

_ANCHOR_HEADING = "本书文风动态锚"
_ANCHOR_SNIPPET = "【动态锚内容_RSA_4242】他立在檐下，雨丝斜织，半晌未语。"
_GEN_POINT = "# 现在请写正文"


def _make_project(tmp: Path, with_anchor=True, empty_snippet=False) -> Path:
    db = tmp / "_数据库"
    (db / ".manifest").mkdir(parents=True, exist_ok=True)
    manifest_obj = {"note": "MANIFEST_ANCHOR_TEST", "facts": ["地点=沙盒"]}
    if with_anchor:
        snip = "" if empty_snippet else _ANCHOR_SNIPPET
        manifest_obj["rolling_style_anchor"] = {
            "anchor_basis": "vs_author_reference",
            "author_pool_resolved": True,
            "anchors": [{"cluster_id": "cluster_001", "snippet": snip,
                         "similarity_to_author": 0.88}],
            "_doc": "本书最贴作者文风片段·看齐。",
        }
    for _ci in (1, 2):
        (db / ".manifest" / f"ch_{_ci:03d}.json").write_text(
            json.dumps(manifest_obj, ensure_ascii=False), encoding="utf-8")
    (db / "作者风格_skill.md").write_text("# 作者风格档\n\n句长偏短。\n", encoding="utf-8")
    (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {
        "cluster_001": {"scene_storyboard": [{"ch": 1, "beat": "开场"}]}}}, ensure_ascii=False),
        encoding="utf-8")
    (db / "人物卡.json").write_text(json.dumps({"主角": {"name": "阿渝"}}, ensure_ascii=False),
                                  encoding="utf-8")
    (db / "用户偏好.json").write_text(json.dumps({"tone": "冷峻"}, ensure_ascii=False),
                                    encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "scope_summary": "开场。"}]}, ensure_ascii=False),
        encoding="utf-8")
    return tmp


def _build(root, anchor_mode="active", ctx_mode="active"):
    saved = {k: os.environ.get(k) for k in ("ROLLING_ANCHOR_INJECT_MODE", "CTX_REORDER_MODE")}
    os.environ["ROLLING_ANCHOR_INJECT_MODE"] = anchor_mode
    os.environ["CTX_REORDER_MODE"] = ctx_mode
    try:
        system, user, _trace = gw.build_prompt(root, cluster_id=1, ch_start=1)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return system, user


def test_default_mode_shadow():
    """env 未设 → 默认 shadow（影子·文风改善需 gen-model A/B 再 active）。"""
    prev = os.environ.pop("ROLLING_ANCHOR_INJECT_MODE", None)
    try:
        assert gw._rolling_anchor_inject_mode() == "shadow"
    finally:
        if prev is not None:
            os.environ["ROLLING_ANCHOR_INJECT_MODE"] = prev


def test_active_injects_anchor_near_gen_point():
    """active：动态锚段出现·snippet 内容进 prompt·位置在 manifest 后、生成点前（风格锚区）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, anchor_mode="active")
        pos_anchor = user.find(_ANCHOR_HEADING)
        pos_manifest = user.find("## manifest")
        pos_gen = user.find(_GEN_POINT)
        assert pos_anchor != -1, "active 动态锚段必须出现"
        assert _ANCHOR_SNIPPET in user, "锚 snippet 内容必须进 prompt"
        assert pos_anchor > pos_manifest, "动态锚应在 manifest 段之后（生成点近邻）"
        assert pos_anchor < pos_gen, "动态锚应在生成点之前"


def test_shadow_off_zero_regression():
    """shadow（默认）/ off → 段不注入（零回归）。"""
    for mode in ("shadow", "off"):
        with tempfile.TemporaryDirectory() as td:
            root = _make_project(Path(td))
            _system, user = _build(root, anchor_mode=mode)
            # 注：snippet 在 raw manifest dump（{manifest} 全量注入·feedback_no_token_saving 不截断·
            # shadow 也有）·非回归；只验【解析段标题】不出现（shadow/off 不做生成点近邻升格·零回归）。
            assert _ANCHOR_HEADING not in user, f"{mode} 不应注入动态锚解析段"


def test_no_anchor_field_no_inject():
    """manifest 无 rolling_style_anchor 字段 → active 也不注入（零回归）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), with_anchor=False)
        _system, user = _build(root, anchor_mode="active")
        assert _ANCHOR_HEADING not in user


def test_empty_snippet_no_inject():
    """anchors 存在但 snippet 全空 → 不注入（零回归·防空段）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), empty_snippet=True)
        _system, user = _build(root, anchor_mode="active")
        assert _ANCHOR_HEADING not in user


def test_active_in_off_ctx_branch_too():
    """ROLLING_ANCHOR 与 CTX_REORDER 正交：CTX off（原版 join）下 active 仍注入锚段。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, anchor_mode="active", ctx_mode="off")
        assert _ANCHOR_HEADING in user
        assert _ANCHOR_SNIPPET in user


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print(f"  [OK] {_n}")
    print("rolling_anchor_inject tests all passed")
