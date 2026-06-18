"""阶段1 测试: patch_applier (确定性) + optimizer (mock LLM)。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from skill_opt import patch_applier, optimizer  # noqa: E402


# ---------- patch_applier ----------

SKILL_SAMPLE = """## 段1 这是第一段的标题

内容 A 第一行
内容 A 第二行

## 段2 这是中间段

内容 B

## 段3 末尾段

内容 C"""


def test_apply_replace_succeeds():
    patches = [
        {
            "op": "replace",
            "anchor": "## 段2 这是中间段",
            "old": "## 段2 这是中间段\n\n内容 B",
            "new": "## 段2 新中间段\n\n新内容",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert "新中间段" in r.new_text
    assert "内容 B" not in r.new_text
    assert len(r.applied) == 1
    assert len(r.rejected) == 0


def test_apply_delete_succeeds():
    patches = [
        {
            "op": "delete",
            "anchor": "## 段2 这是中间段",
            "old": "## 段2 这是中间段\n\n内容 B",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert "段2" not in r.new_text
    assert "段1" in r.new_text
    assert "段3" in r.new_text


def test_apply_add_after_anchor_succeeds():
    patches = [
        {
            "op": "add",
            "after_anchor": "## 段1 这是第一段的标题",
            "new": "## 新插入段\n\n插入内容",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert "新插入段" in r.new_text
    # 顺序: 段1 → 新插入 → 段2 → 段3
    idx1 = r.new_text.index("段1")
    idxn = r.new_text.index("新插入段")
    idx2 = r.new_text.index("段2")
    assert idx1 < idxn < idx2


def test_apply_add_no_anchor_inserts_at_head():
    patches = [{"op": "add", "new": "## 文首新段\n\nx"}]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert r.new_text.startswith("## 文首新段")


def test_apply_anchor_not_found_rejected():
    patches = [
        {
            "op": "replace",
            "anchor": "## 不存在的段",
            "old": "xxx",
            "new": "yyy",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert not r.success
    assert len(r.rejected) == 1
    assert "找不到" in r.rejected[0][1]


def test_apply_old_mismatch_rejected():
    patches = [
        {
            "op": "replace",
            "anchor": "## 段2 这是中间段",
            "old": "完全错误的旧内容",  # anchor 命中但 old 不对
            "new": "yyy",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert not r.success
    assert "不匹配" in r.rejected[0][1]


def test_apply_invalid_op_rejected():
    r = patch_applier.apply_patches(SKILL_SAMPLE, [{"op": "rewrite", "new": "x"}])
    assert not r.success
    assert "op 必须是" in r.rejected[0][1]


def test_apply_max_patches_truncates():
    """超过 max_patches 应截断,不应用多余的。"""
    patches = [{"op": "add", "new": f"段{i}"} for i in range(6)]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches, max_patches=3)
    # 只允许应用前 3 条 (本测试都是合法 add)
    assert len(r.applied) == 3


def test_apply_partial_success():
    """1 条好 + 1 条坏 → success=True, applied 1 / rejected 1。"""
    patches = [
        {
            "op": "replace",
            "anchor": "## 段2 这是中间段",
            "old": "## 段2 这是中间段\n\n内容 B",
            "new": "## 段2 新\n\n新",
        },
        {  # 这条坏
            "op": "delete",
            "anchor": "## 不存在",
            "old": "x",
        },
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert len(r.applied) == 1
    assert len(r.rejected) == 1
    assert "段2 新" in r.new_text


def test_apply_missing_required_fields_rejected():
    r = patch_applier.apply_patches(SKILL_SAMPLE, [{"op": "add"}])  # 缺 new
    assert not r.success
    assert "new" in r.rejected[0][1]


# ---------- optimizer (mock LLM) ----------


class _FakeProfile:
    name = "fake"
    protocol = "openai"


def _fake_stream_returning(reply: str):
    def _stream(profile, system, user, max_tokens, **kw):
        return reply, "stop"
    return _stream


def test_optimizer_extracts_patches_from_json_fence():
    reply = """这是说明
```json
{
  "patches": [
    {"op": "replace", "anchor": "## A", "old": "## A\\n\\nx", "new": "## A\\n\\ny"}
  ]
}
```
末尾文字"""
    with patch("skill_opt.optimizer.stream_once", _fake_stream_returning(reply)):
        patches, raw = optimizer.propose_patches(
            skill_text="## A\n\nx\n\n## B\n\nz",
            trajectories=[],
            profile=_FakeProfile(),
        )
        assert len(patches) == 1
        assert patches[0]["op"] == "replace"


def test_optimizer_caps_at_max_patches():
    """LLM 输出 10 条,只取前 4。"""
    items = ",".join(
        '{{"op":"add","new":"段{}"}}'.format(i).replace("{i}", str(i)) for i in range(10)
    )
    # 简化: 手写 10 条 json
    patches_json = ",".join(
        '{"op":"add","new":"段' + str(i) + '"}' for i in range(10)
    )
    reply = f'```json\n{{"patches": [{patches_json}]}}\n```'
    with patch("skill_opt.optimizer.stream_once", _fake_stream_returning(reply)):
        patches, _ = optimizer.propose_patches(
            skill_text="x",
            trajectories=[],
            profile=_FakeProfile(),
            max_patches=4,
        )
        assert len(patches) == 4


def test_optimizer_handles_transport_error():
    """transport 失败 → 返回空 patches 不崩溃。"""
    from llm_transport import TransportError

    def _boom(*a, **k):
        raise TransportError("connection refused")

    with patch("skill_opt.optimizer.stream_once", _boom):
        patches, raw = optimizer.propose_patches(
            skill_text="x",
            trajectories=[],
            profile=_FakeProfile(),
        )
        assert patches == []
        assert "TransportError" in raw


def test_optimizer_handles_malformed_json():
    """LLM 输出非 JSON → 返回空 patches。"""
    reply = "我无法生成 patch,请提供更多上下文。"
    with patch("skill_opt.optimizer.stream_once", _fake_stream_returning(reply)):
        patches, _ = optimizer.propose_patches(
            skill_text="x",
            trajectories=[],
            profile=_FakeProfile(),
        )
        assert patches == []


def test_optimizer_prompt_includes_reject_buffer():
    """reject buffer 不为空时,user prompt 必须包含 [REJECT_BUFFER]。"""
    captured = {}

    def _capture(profile, system, user, max_tokens, **kw):
        captured["user"] = user
        return '```json\n{"patches": []}\n```', "stop"

    rejects = [
        {
            "patch": {"op": "replace", "old": "a", "new": "b"},
            "reward_before": 0.8,
            "reward_after": 0.7,
            "reason": "测试",
        }
    ]
    with patch("skill_opt.optimizer.stream_once", _capture):
        optimizer.propose_patches(
            skill_text="x",
            trajectories=[],
            rejects=rejects,
            profile=_FakeProfile(),
        )
    assert "REJECT_BUFFER" in captured["user"]


def test_optimizer_prompt_marks_protected_sections():
    captured = {}

    def _capture(profile, system, user, max_tokens, **kw):
        captured["user"] = user
        return '```json\n{"patches": []}\n```', "stop"

    with patch("skill_opt.optimizer.stream_once", _capture):
        optimizer.propose_patches(
            skill_text="x",
            trajectories=[],
            protected_sections=["## 作者数值契约表", "## 句长基线"],
            profile=_FakeProfile(),
        )
    assert "PROTECTED" in captured["user"]
    assert "作者数值契约表" in captured["user"]
