# -*- coding: utf-8 -*-
"""逐场景顺序生成（scene-sequential freestyle）回归锁 · 2026-07-08 修正轮。

真机 A/B 证伪 v27「一把梭自然涌现 12-25k」（gemini-3.1-pro 全渠道自发 finish=stop 于
2-3.5k CJK·每场景压成梗概体）→ 根因修复=storyboard ≥2 场景时逐场景一次一调用写透。

红线锁（与 expand / 字数兜底红线的本质区别）：
  · prompt 全链路不出现任何数字字数目标（每次调用是结构化「写一个场景」·场景内自然 stop 即完结）；
  · storyboard <2 场景 / 缺失 → 回退一把梭原路径（老行为逐字节保留·由 test_gen_writer/best_of_n 守）。
"""
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import gen_writer_scenes as gws  # noqa: E402
import gen_writer as gw  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# [A] 纯 helper（无 mock）
# ════════════════════════════════════════════════════════════════════════

def test_use_scene_sequential_threshold():
    """≥2 场景 → True；<2 / None / 非 list → False（回退一把梭）。"""
    assert gws.use_scene_sequential([{"a": 1}, {"b": 2}]) is True
    assert gws.use_scene_sequential([{"a": 1}, {"b": 2}, {"c": 3}]) is True
    assert gws.use_scene_sequential([{"a": 1}]) is False
    assert gws.use_scene_sequential([]) is False
    assert gws.use_scene_sequential(None) is False
    assert gws.use_scene_sequential("nope") is False


def test_synopsis_field_priority_and_truncation():
    """字段优先级 summary>scene_goal>dramatic_question>title>scene_title>key_beats[0]；截断。"""
    assert gws._synopsis_of({"summary": "S", "scene_goal": "G"}) == "S"
    assert gws._synopsis_of({"scene_goal": "G", "title": "T"}) == "G"
    assert gws._synopsis_of({"title": "T"}) == "T"
    assert gws._synopsis_of({"key_beats": ["", "  ", "拍子"]}) == "拍子"
    assert gws._synopsis_of({}) == "（该场景卡无梗概字段）"
    long = "字" * 300
    assert len(gws._synopsis_of({"summary": long})) == gws.SYNOPSIS_MAX_CHARS


def test_synopsis_line_format():
    assert gws.synopsis_line(0, {"summary": "血月开场"}) == "场景 1：血月开场"
    assert gws.synopsis_line(2, {"summary": "追债回溯"}) == "场景 3：追债回溯"


def test_compact_brief_marks_consumed_current_future():
    """后续场景压缩 storyboard：<idx 已写完/=idx 当前占位/>idx 未写预告；不改原 brief。"""
    brief = {"scene_storyboard": [
        {"summary": "场景A"}, {"summary": "场景B"}, {"summary": "场景C"}]}
    view = {"idx": 1}
    out = gws.compact_brief_for_scene(brief, view)
    sb = out["scene_storyboard"]
    assert sb[0]["status"].startswith("已写完") and sb[0]["synopsis"] == "场景A"
    assert "当前场景" in sb[1]["status"] and "synopsis" not in sb[1]
    assert sb[2]["status"].startswith("后续场景") and sb[2]["synopsis"] == "场景C"
    # 原 brief 未被改（浅拷贝）
    assert brief["scene_storyboard"][0] == {"summary": "场景A"}


def test_compact_brief_passthrough_when_no_storyboard():
    assert gws.compact_brief_for_scene({"x": 1}, {"idx": 1}) == {"x": 1}
    assert gws.compact_brief_for_scene("nope", {"idx": 1}) == "nope"


def test_scene_gen_point_tail_has_card_no_wordcount():
    """生成点尾部含当前场景卡；idx≥1 含前情梗概+已写末尾锚+衔接指令；全链路无数字字数目标。"""
    view0 = {"idx": 0, "total": 3, "scene_card": {"summary": "开场血月"},
             "consumed_lines": [], "prev_tail": ""}
    tail0 = gws.scene_gen_point_tail(view0)
    assert "开场血月" in tail0
    assert "当前场景卡" in tail0
    assert "不要" in tail0 and "CHANGES" in tail0  # 本次不产 CHANGES
    # idx=0 无「已写正文末尾」段、无衔接续写指令
    assert "已写正文末尾" not in tail0

    view1 = {"idx": 1, "total": 3, "scene_card": {"summary": "追债回溯"},
             "consumed_lines": ["场景 1：开场血月"], "prev_tail": "……血月悬顶。"}
    tail1 = gws.scene_gen_point_tail(view1)
    assert "前情梗概" in tail1 and "开场血月" in tail1
    assert "已写正文末尾" in tail1 and "血月悬顶" in tail1
    assert "直接衔接续写" in tail1

    # 🔴红线：prompt 层不得出现「N 字」「N000 字」「字数」类数字目标
    for t in (tail0, tail1):
        assert not re.search(r"\d{3,}\s*字", t), "prompt 不得含数字字数目标"
        assert "字数目标" not in t


def test_extract_changes_block_variants():
    """围栏块取最后一个；裸 JSON 可解析才收；都无 → 空串（同一把梭容错口径）。"""
    fenced = "正文\n```json\n{\"a\":1}\n```\n```json\n{\"b\":2}\n```"
    assert gws.extract_changes_block(fenced) == "```json\n{\"b\":2}\n```"
    bare = '{"factual": {}, "self_eval": {}}'
    got = gws.extract_changes_block(bare)
    assert got.startswith("```json") and json.loads(got.split("\n", 1)[1].rsplit("\n```", 1)[0])
    assert gws.extract_changes_block("没有任何 JSON") == ""
    assert gws.extract_changes_block("") == ""


# ════════════════════════════════════════════════════════════════════════
# [B] pipeline（monkeypatch gen_writer 模块级函数）
# ════════════════════════════════════════════════════════════════════════

class _Profile:
    def __init__(self, name="p", model="gemini-3.1-pro-preview"):
        self.name = name
        self.model = model


def _install_mocks(monkeypatch, scene_bodies, changes_reply='```json\n{"factual": {}}\n```',
                   empty_scene=None, fail_then_succeed=None):
    """打桩 build_prompt / call_gen_model / best_of_n_pipeline / split_text_and_changes。

    返回 call_log 记录每次调用的类型与 scene_view / prompt。
    scene_bodies: 每场景正文（按序）；empty_scene: 令某场景**永远**返回空正文（测响亮失败）；
    fail_then_succeed: {scene_idx: fail_times} 令该场景前 fail_times 次空、之后成功（测有界重试）。
    """
    log = {"build_prompt": [], "call_gen_model": [], "best_of_n": [], "changes_calls": 0,
           "scene_attempts": {}}
    fts = dict(fail_then_succeed or {})

    def _body_for(idx):
        if empty_scene is not None and idx == empty_scene:
            return ""
        log["scene_attempts"][idx] = log["scene_attempts"].get(idx, 0) + 1
        if idx in fts and log["scene_attempts"][idx] <= fts[idx]:
            return ""  # 前 N 次空
        return scene_bodies[idx]

    def fake_build_prompt(project_root, cluster_id, ch_start, scene_view=None):
        log["build_prompt"].append(dict(scene_view) if scene_view else None)
        idx = int(scene_view.get("idx", 0)) if scene_view else -1
        # 把 scene_view 的关键信息编进 user 便于断言（真 build_prompt 会调 scene_gen_point_tail）
        tail = gws.scene_gen_point_tail(scene_view) if scene_view else ""
        return (f"SYS", f"USER[idx={idx}]{tail}", 0)

    def fake_call_gen_model(loader, system, user, creative=False, prior_assistant=None,
                            cont_reason="length", return_finish=False):
        if cont_reason == "changes_only":
            log["changes_calls"] += 1
            log["call_gen_model"].append(("changes", prior_assistant))
            return (changes_reply, _Profile(), "stop") if return_finish else (changes_reply, _Profile())
        # 场景正文调用：从 user 里解 idx
        m = re.search(r"idx=(-?\d+)", user)
        idx = int(m.group(1)) if m else 0
        log["call_gen_model"].append(("scene", idx))
        body = _body_for(idx)
        return (body, _Profile(), "stop") if return_finish else (body, _Profile())

    def fake_best_of_n(loader, system, user, project_root, n, creative=False,
                       force_blind_revise_off=False):
        m = re.search(r"idx=(-?\d+)", user)
        idx = int(m.group(1)) if m else 0
        log["best_of_n"].append({"idx": idx, "n": n, "blind_off": force_blind_revise_off})
        return (_body_for(idx), _Profile(), {"best_of_n": n, "selected": 0})

    monkeypatch.setattr(gw, "build_prompt", fake_build_prompt)
    monkeypatch.setattr(gw, "call_gen_model", fake_call_gen_model)
    monkeypatch.setattr(gw, "best_of_n_pipeline", fake_best_of_n)
    # split_text_and_changes 是真函数（剥 CHANGES 块）——用真的即可
    return log


def test_pipeline_call_count_and_first_scene_best_of_n(monkeypatch):
    """3 场景 · n=5：首场景走 best_of_n(blind off)、后 2 场景单发、+1 次 CHANGES 调用。"""
    log = _install_mocks(monkeypatch, ["甲正文", "乙正文", "丙正文"])
    cards = [{"summary": "A"}, {"summary": "B"}, {"summary": "C"}]
    reply, prof, bon, scene_trace = gws.scene_sequential_pipeline(
        loader=object(), project_root="/proj", cluster_id=1, ch_start=1,
        scene_cards=cards, n=5, base_system="SYS", base_user="USER")
    # 首场景 best_of_n 一次
    assert len(log["best_of_n"]) == 1 and log["best_of_n"][0]["idx"] == 0
    assert log["best_of_n"][0]["blind_off"] is True and log["best_of_n"][0]["n"] == 5
    # 后 2 场景单发 + 1 次 changes
    scene_calls = [c for c in log["call_gen_model"] if c[0] == "scene"]
    assert [c[1] for c in scene_calls] == [1, 2]  # idx 1、2 单发
    assert log["changes_calls"] == 1
    # bon_trace 首场景 scope/mode 标记
    assert bon["scope"] == "first_scene_only"
    assert bon["mode"] == "scene_sequential_first_scene_n_pick_1"


def test_pipeline_concat_order_and_changes_tail(monkeypatch):
    """场景正文按序拼接 + 尾部单次 CHANGES 块；拼接稿顺序完整。"""
    log = _install_mocks(monkeypatch, ["第一段", "第二段"])
    cards = [{"summary": "A"}, {"summary": "B"}]
    reply, prof, bon, scene_trace = gws.scene_sequential_pipeline(
        object(), "/proj", 1, 1, cards, n=1, base_system="SYS", base_user="USER")
    body, changes = gw.split_text_and_changes(reply)
    assert body.strip() == "第一段\n\n第二段"
    assert changes.get("factual") == {}


def test_pipeline_n1_no_best_of_n(monkeypatch):
    """BEST_OF_N=1：首场景也单发（不走 best_of_n）；bon_trace 记单发说明。"""
    log = _install_mocks(monkeypatch, ["甲", "乙"])
    cards = [{"summary": "A"}, {"summary": "B"}]
    reply, prof, bon, scene_trace = gws.scene_sequential_pipeline(
        object(), "/proj", 1, 1, cards, n=1)
    assert log["best_of_n"] == []
    assert [c[1] for c in log["call_gen_model"] if c[0] == "scene"] == [0, 1]
    assert bon["best_of_n"] == 1
    assert scene_trace["first_scene_best_of_n"] is False


def test_pipeline_subsequent_scene_prompt_has_prev_tail(monkeypatch):
    """后续场景的 scene_view 带 prev_tail（已写正文末尾锚）+ consumed_lines（前情梗概）。"""
    log = _install_mocks(monkeypatch, ["开场正文内容", "后续正文内容"])
    cards = [{"summary": "开场"}, {"summary": "后续"}]
    gws.scene_sequential_pipeline(object(), "/proj", 1, 1, cards, n=1)
    views = [v for v in log["build_prompt"] if v is not None]
    # 场景 0：无 prev_tail、无 consumed
    v0 = next(v for v in views if v["idx"] == 0)
    assert v0["prev_tail"] == "" and v0["consumed_lines"] == []
    # 场景 1：prev_tail 含场景 0 正文、consumed 含场景 1 梗概行
    v1 = next(v for v in views if v["idx"] == 1)
    assert "开场正文内容" in v1["prev_tail"]
    assert v1["consumed_lines"] == ["场景 1：开场"]


def test_pipeline_empty_scene_body_raises_after_retries(monkeypatch):
    """某场景永远返回空 → 重试用尽（SCENE_MAX_RETRIES+1 次）后响亮 RuntimeError。"""
    log = _install_mocks(monkeypatch, ["甲", "", "丙"], empty_scene=1)
    cards = [{"summary": "A"}, {"summary": "B"}, {"summary": "C"}]
    import pytest
    with pytest.raises(RuntimeError, match="连续"):
        gws.scene_sequential_pipeline(object(), "/proj", 1, 1, cards, n=1)
    # 场景 1（idx=1）被调用了 SCENE_MAX_RETRIES+1 次（重试全空才放弃）
    idx1_calls = [c for c in log["call_gen_model"] if c == ("scene", 1)]
    assert len(idx1_calls) == gws.SCENE_MAX_RETRIES + 1


def test_pipeline_scene_retry_then_succeed(monkeypatch):
    """场景 1 首次空（模型偶发只回元评论）→ 重试成功（不注水·不掐整块）。"""
    log = _install_mocks(monkeypatch, ["甲正文", "乙正文", "丙正文"],
                         fail_then_succeed={1: 1})  # idx=1 前 1 次空
    cards = [{"summary": "A"}, {"summary": "B"}, {"summary": "C"}]
    reply, _, _, scene_trace = gws.scene_sequential_pipeline(
        object(), "/proj", 1, 1, cards, n=1)
    body, _ = gw.split_text_and_changes(reply)
    assert body.strip() == "甲正文\n\n乙正文\n\n丙正文"  # 拼接完整·重试稿落位
    # 场景 1 被调 2 次（1 空 + 1 成功）
    assert len([c for c in log["call_gen_model"] if c == ("scene", 1)]) == 2


def test_pipeline_scene_trace_structure(monkeypatch):
    """scene_trace per-scene 遥测结构完整（mode/scenes_total/per_scene cjk+finish）。"""
    _install_mocks(monkeypatch, ["正文一二三", "正文四五六"])
    cards = [{"summary": "A"}, {"summary": "B"}]
    _, _, _, scene_trace = gws.scene_sequential_pipeline(object(), "/proj", 1, 1, cards, n=1)
    assert scene_trace["mode"] == "scene_sequential"
    assert scene_trace["scenes_total"] == 2
    assert scene_trace["blind_revise"] == "forced_off_scene_sequential"
    assert len(scene_trace["per_scene"]) == 2
    for ps in scene_trace["per_scene"]:
        assert "scene_idx" in ps and "cjk" in ps and "finish" in ps


def test_pipeline_changes_unparsed_still_lands_draft(monkeypatch):
    """CHANGES 调用未产可解析 JSON → reply 仍含拼接正文（草稿不阻断·同一把梭容错）。"""
    _install_mocks(monkeypatch, ["正文甲", "正文乙"], changes_reply="模型没产 JSON")
    cards = [{"summary": "A"}, {"summary": "B"}]
    reply, _, _, scene_trace = gws.scene_sequential_pipeline(object(), "/proj", 1, 1, cards, n=1)
    body, changes = gw.split_text_and_changes(reply)
    assert body.strip() == "正文甲\n\n正文乙"
    assert scene_trace["changes_call"]["parsed"] is False


def test_no_wordcount_number_across_all_scene_prompts(monkeypatch):
    """🔴红线全链路锁：pipeline 组装的所有 user prompt 均不含数字字数目标。"""
    log = _install_mocks(monkeypatch, ["甲正文", "乙正文", "丙正文"])
    cards = [{"summary": f"场景{i}"} for i in range(3)]
    gws.scene_sequential_pipeline(object(), "/proj", 1, 1, cards, n=1)
    # 真 build_prompt 被 mock，但 scene_gen_point_tail 是真的——直接对每个 view 生成尾部查
    for v in [x for x in log["build_prompt"] if x is not None]:
        tail = gws.scene_gen_point_tail(v)
        assert not re.search(r"\d{3,}\s*字", tail)
        assert "字数目标" not in tail
