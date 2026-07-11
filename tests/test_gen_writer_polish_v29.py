# -*- coding: utf-8 -*-
"""v29 分段润色契约回归锁（2026-07-11 架构转向：Claude 亲笔创作 + gemini 润色）。

锁四件事：
1. discover_claude_scenes 的 required 前置语义（缺目录/空/过短 → FileNotFoundError·不降级）；
2. build_prompt 润色尾部契约（等体量指令 + 原文段注入 + 守恒带数字 + 不产 JSON 指令）；
3. 段级守恒带常量（0.85 / 1.30 · 实验依据 workspace/_temp_research/四组生成对比_20260711）；
4. 从零生成路径已清除（best_of_n / scene_sequential / split_text_and_changes 符号不存在）。
"""
import json
import sys
import tempfile
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
