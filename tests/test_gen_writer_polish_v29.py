# -*- coding: utf-8 -*-
"""Claude 亲笔创作 + gemini 分段润色契约回归锁。

锁五件事：
1. discover_claude_scenes 的 required 前置语义（缺目录/空/过短 → FileNotFoundError·不降级）；
2. build_prompt 润色尾部契约（等体量指令 + 原文段注入 + 守恒带数字 + 不产 JSON 指令）；
3. 段级守恒带常量（0.85 / 1.30）；
4. 公共接口不暴露多稿、场景顺序生成或文本/changes 混合解析符号；
5. CJK 字数守恒重试仍超界 → 整段保留 Claude 亲笔原稿（与引号维度对称·亲笔优先）。
"""
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import gen_writer as gw  # noqa: E402


def _mk_scenes(root: Path, cluster_id: int, texts):
    d = root / "章节" / f"cluster_{cluster_id:03d}_draft" / "claude_scenes"
    d.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(texts):
        (d / f"scene_{i:02d}.txt").write_text(t, encoding="utf-8")
    return d


# ---------- 1. discover_claude_scenes required 前置 ----------

def test_discover_missing_dir_raises():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(FileNotFoundError):
            gw.discover_claude_scenes(Path(td), 1)


def test_discover_empty_dir_raises():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "章节" / "cluster_001_draft" / "claude_scenes").mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            gw.discover_claude_scenes(root, 1)


def test_discover_too_short_scene_raises():
    """场景稿 <200 CJK = 梗概占位，拒收（Claude 草稿必须每场景写透）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_scenes(root, 1, ["太短的场景。"])
        with pytest.raises(FileNotFoundError):
            gw.discover_claude_scenes(root, 1)


def test_discover_ok_returns_sorted_and_changes():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        s0 = "井边锁喉的完整场景正文。" * 30
        s1 = "解剖刀揭底的完整场景正文。" * 30
        d = _mk_scenes(root, 1, [s0, s1])
        changes = {"self_eval": {"waivers": [], "claude_draft_cjk": 700}}
        (d.parent / "cluster_001_changes_claude.json").write_text(
            json.dumps(changes, ensure_ascii=False), encoding="utf-8")
        files, cc = gw.discover_claude_scenes(root, 1)
        assert [n for n, _ in files] == ["scene_00.txt", "scene_01.txt"]
        assert files[0][1] == s0
        assert cc["self_eval"]["claude_draft_cjk"] == 700


# ---------- 2. build_prompt 润色尾部契约 ----------

def test_build_prompt_requires_polish_view():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError):
            gw.build_prompt(Path(td), 1, 1)


def test_polish_tail_contract(monkeypatch):
    monkeypatch.setenv("SNIPPET_SEED_MODE", "off")  # 禁种子注入·测试确定性
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = root / "_数据库"
        db.mkdir(parents=True)
        (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {}}, ensure_ascii=False),
                                      encoding="utf-8")
        (db / "事件簇.json").write_text(json.dumps({"clusters": []}, ensure_ascii=False),
                                        encoding="utf-8")
        src = "厉鬼掐住秦烬脖子的原文段落。" * 20
        _, user, _ = gw.build_prompt(root, 1, 1,
                                     polish_view={"idx": 1, "total": 3, "scene_text": src})
        # 原文段注入
        assert src in user
        # 等体量守恒带数字（0.85 / 1.30 由常量换算）
        src_cjk = gw.cio.count_cjk(src)
        assert str(int(src_cjk * gw.POLISH_CJK_LOW)) in user
        assert str(int(src_cjk * gw.POLISH_CJK_HIGH)) in user
        # 分段定位 + 不产 JSON
        assert "第 2/3 段" in user.replace(" ", "").replace("第2/3段", "第 2/3 段") or "2/3" in user
        assert "不要输出任何 JSON" in user
        # 关键事实锁定指令在场
        assert "锁定事实" in user


# ---------- 3. 守恒带常量 ----------

def test_conservation_band_constants():
    assert gw.POLISH_CJK_LOW == 0.85
    assert gw.POLISH_CJK_HIGH == 1.30


# ---------- 4. 从零生成路径已清除（防复活回归锁） ----------

def test_zero_gen_symbols_removed():
    for sym in ("best_of_n_pipeline", "generate_n_drafts", "select_best_draft",
                "blind_revise_round", "split_text_and_changes", "_best_of_n"):
        assert not hasattr(gw, sym), f"从零生成家族符号复活: {sym}"
    # scenes 模块整体退役
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("gen_writer_scenes")


def test_clean_polished_body_strips_json_block():
    reply = "正文段落。\n\n```json\n{\"a\": 1}\n```\n"
    assert gw.clean_polished_body(reply) == "正文段落。"


# ---------- 5. CJK 字数守恒重试仍超界 → 保留 Claude 原段（回归锁·2026-07-18） ----------
# 实战撞坑：真机 cluster_003 scene_02 首稿 ratio=1.71（2534→4342 CJK），带指令重试后
# 仍 ratio=1.71（几乎原地踏步），旧代码「取离守恒中心更近者」不管是否仍超界都收下——
# 71% 膨胀的注水稿被无条件接受，违反 gen_writer.py 自身文档的「上限 1.30 注水扩写红线」。

_SRC_CJK = "厉鬼掐住秦烬的脖子，指节泛白。" * 20  # 约 300+ CJK，src


def _run_polish_pipeline(monkeypatch, replies):
    """跑单场景 polish_pipeline：build_prompt/call_gen_model 打桩，replies 按序弹出。"""
    calls = {"n": 0}

    def fake_build_prompt(project_root, cluster_id, ch_start, polish_view=None):
        assert polish_view is not None
        return "SYS", "USER", {"snippet_seed_mode": "off", "injected": False}

    def fake_call(loader, system, user, creative=False):
        idx = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return replies[idx], types.SimpleNamespace(name="fake", model="fake-model")

    monkeypatch.setattr(gw, "build_prompt", fake_build_prompt)
    monkeypatch.setattr(gw, "call_gen_model", fake_call)
    body, _profile, trace = gw.polish_pipeline(
        None, Path("."), 1, 1, [("scene_00.txt", _SRC_CJK)])
    return body, trace, calls["n"]


def test_cjk_no_violation_no_retry(monkeypatch):
    """润色稿字数在守恒带内 → 不触发重试。"""
    body, trace, n_calls = _run_polish_pipeline(monkeypatch, [_SRC_CJK])
    assert n_calls == 1
    sc = trace["scenes"][0]
    assert sc["retried"] is False and sc["cjk_kept_claude"] is False
    assert trace["cjk_guard"] == {"scenes_triggered": 0, "scenes_retried": 0, "scenes_kept_claude": 0}


def test_cjk_violation_retry_recovers(monkeypatch):
    """首稿超界 → 带字数指令重试 1 次 → 重试稿落回守恒带 → 收重试稿。"""
    bloated_once = _SRC_CJK * 2       # ratio≈2.0 超界
    recovered = _SRC_CJK + "多了几句润色补的细节描写用于凑数但仍在带内。"
    body, trace, n_calls = _run_polish_pipeline(monkeypatch, [bloated_once, recovered])
    assert n_calls == 2
    assert body == recovered
    sc = trace["scenes"][0]
    assert sc["retried"] is True and sc["cjk_kept_claude"] is False
    assert trace["cjk_guard"] == {"scenes_triggered": 1, "scenes_retried": 1, "scenes_kept_claude": 0}


def test_cjk_violation_retry_still_out_of_band_keeps_claude(monkeypatch):
    """重试稿仍超界（含 cluster_003 scene_02 实战复现：重试几乎原地踏步）
    → 整段保留 Claude 亲笔原稿，不收违反等体量红线的注水/压缩稿。"""
    bloated = _SRC_CJK * 3            # ratio≈3.0，远超 1.30
    still_bloated = _SRC_CJK * 2 + _SRC_CJK[: int(len(_SRC_CJK) * 0.9)]  # ratio≈2.9，重试仍远超守恒带
    body, trace, n_calls = _run_polish_pipeline(monkeypatch, [bloated, still_bloated])
    assert n_calls == 2
    assert body == _SRC_CJK, "重试仍超界必须回退 Claude 原段，不得收超界稿"
    sc = trace["scenes"][0]
    assert sc["cjk_kept_claude"] is True
    assert sc["ratio"] == 1.0
    assert trace["cjk_guard"] == {"scenes_triggered": 1, "scenes_retried": 1, "scenes_kept_claude": 1}
