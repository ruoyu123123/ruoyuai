# -*- coding: utf-8 -*-
"""cross_family_judge_check inline-only 守门测试（2026-06-20 A 方案）。

模式：主代理 Claude Code spawn Agent(claude) 复审 finale subcluster
后 save_inline_verdict_for_main_agent 写 .wal·orchestrator default_judge_dispatch
末端 maybe_run 自动捡用 → outcome.data['cross_family_check']。

守门维度：
  - 6 个 gate（off / 非 eligible / 非 finale / 无 draft / 无 inline / mode 默认）
  - inline 命中正常路径 + draft_sha 不匹配静默 skip + JSON 损坏静默 skip
  - save_inline_verdict_for_main_agent 写盘 + 目录不存在不抛
  - agreement 双方 verdict 比对（agree / disagree / unknown_gemini）

北极星：advisory shadow·永不阻断主链·失败静默 skip。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_family_judge_check as cfj  # noqa: E402


@pytest.fixture
def mode_isolated(monkeypatch):
    """每个测试 env CROSS_FAMILY_JUDGE_MODE 互不污染。"""
    monkeypatch.delenv("CROSS_FAMILY_JUDGE_MODE", raising=False)
    yield monkeypatch


def _make_project(tmp_path: Path) -> Path:
    """生成最小 _数据库/.wal/ 目录。"""
    (tmp_path / "_数据库" / ".wal").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_inline(tmp_path: Path, draft_text: str, judge: str, *,
                  verdict="pass", reason="ok", sha_override=None,
                  claude_model="via-claude-code-agent",
                  payload_override=None, raw_text=None):
    """写 _数据库/.wal/claude_verdict_<sha>_<judge>.json。
    sha_override / payload_override / raw_text 给 negative 用例伪造畸形数据。"""
    sha = sha_override or cfj._draft_sha12(draft_text)
    p = tmp_path / "_数据库" / ".wal" / f"claude_verdict_{sha}_{judge}.json"
    if raw_text is not None:
        p.write_text(raw_text, encoding="utf-8")
        return p
    payload = {
        "draft_sha256_prefix": sha,
        "judge_name": judge,
        "verdict": verdict,
        "reason": reason,
        "source": "inline_agent_spawn",
        "claude_model": claude_model,
    }
    if payload_override:
        payload.update(payload_override)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


# ============ 6 个 gate 守门 ============
def test_mode_off_skips(mode_isolated, tmp_path):
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "off")
    pr = _make_project(tmp_path)
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text="x", project_root=pr)
    assert r["status"] == "skipped"
    assert r["reason"] == "mode=off"


def test_ineligible_judge_skips(mode_isolated, tmp_path):
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    r = cfj.maybe_run(judge_name="kicker", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text="x", project_root=pr)
    assert r["status"] == "skipped"
    assert "eligible" in r["reason"]


def test_non_finale_skips(mode_isolated, tmp_path):
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=False,
                      gemini_verdict="pass", draft_text="x", project_root=pr)
    assert r["status"] == "skipped"
    assert "finale" in r["reason"]


def test_no_draft_skips(mode_isolated, tmp_path):
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=None, project_root=pr)
    assert r["status"] == "skipped"
    assert "无草稿" in r["reason"]


def test_mode_default_shadow(mode_isolated):
    assert cfj._mode() == "shadow"


def test_no_inline_file_skips_with_phase_hint(mode_isolated, tmp_path):
    """合法触发但没主代理写 .wal → skipped + reason 提示。"""
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text="some draft",
                      project_root=pr)
    assert r["status"] == "skipped"
    assert "inline verdict" in r["reason"]


# ============ inline 文件命中 / hash 不匹配 / 损坏 ============
def test_inline_verdict_hit_agree(mode_isolated, tmp_path):
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    draft = "cluster_001 末块草稿正文若干"
    _write_inline(pr, draft, "audit", verdict="pass", reason="claude 也通过")
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=draft, project_root=pr)
    assert r["status"] == "completed"
    assert r["verdict_pair"] == ["pass", "pass"]
    assert r["source"] == "inline_agent_spawn"
    assert r["claude_verdict"] == "pass"
    assert r["agreement"] == "agree"


def test_inline_verdict_hit_disagree(mode_isolated, tmp_path):
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    draft = "草稿 x"
    _write_inline(pr, draft, "voice", verdict="issues",
                  reason="claude 觉得有 voice 漂移")
    r = cfj.maybe_run(judge_name="voice", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=draft, project_root=pr)
    assert r["status"] == "completed"
    assert r["agreement"] == "disagree"
    assert r["claude_verdict"] == "issues"


def test_inline_verdict_hit_shadow_default(mode_isolated, tmp_path):
    """默认 shadow 也读 inline 文件（advisory · 不阻断）。"""
    pr = _make_project(tmp_path)
    draft = "草稿 y"
    _write_inline(pr, draft, "audit", verdict="pass")
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=draft, project_root=pr)
    assert r["status"] == "completed"
    assert r["mode"] == "shadow"


def test_inline_verdict_hash_mismatch_skips(mode_isolated, tmp_path):
    """draft 改了但 .wal 文件还在 → sha 不匹配静默 skip 不用陈旧裁决。"""
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    draft_old = "旧草稿"
    draft_new = "新草稿"
    _write_inline(pr, draft_old, "audit", verdict="pass")
    # 同 judge 名但文件锚在 old 的 sha → new draft 不能命中
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=draft_new,
                      project_root=pr)
    assert r["status"] == "skipped"
    assert "inline verdict" in r["reason"]


def test_inline_verdict_inner_sha_field_mismatch_skips(mode_isolated, tmp_path):
    """文件名锚对但内部 draft_sha256_prefix 字段不一致 → 静默 skip。"""
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    draft = "草稿 abc"
    real_sha = cfj._draft_sha12(draft)
    # 文件按 real_sha 落盘·但 payload 内 sha 写错
    _write_inline(pr, draft, "audit", verdict="pass",
                  payload_override={"draft_sha256_prefix": "0" * 12})
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=draft, project_root=pr)
    assert real_sha != "0" * 12
    assert r["status"] == "skipped"


def test_inline_verdict_corrupted_json_silently_skips(mode_isolated, tmp_path):
    """JSON 损坏 → 静默 skip 不抛。"""
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    draft = "草稿 z"
    _write_inline(pr, draft, "audit", raw_text="{not: valid json")
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=draft, project_root=pr)
    assert r["status"] == "skipped"


def test_inline_verdict_invalid_verdict_value_skips(mode_isolated, tmp_path):
    """payload 里 verdict 不在 pass/issues 白名单 → 静默 skip。"""
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    draft = "草稿 q"
    _write_inline(pr, draft, "audit", verdict="maybe")
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=draft, project_root=pr)
    assert r["status"] == "skipped"


def test_inline_verdict_unknown_gemini_marks_agreement(mode_isolated, tmp_path):
    """gemini_verdict 非 pass/issues → agreement='unknown_gemini_verdict'。"""
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    draft = "草稿 q2"
    _write_inline(pr, draft, "voice", verdict="pass")
    r = cfj.maybe_run(judge_name="voice", is_finale_subcluster=True,
                      gemini_verdict=None, draft_text=draft, project_root=pr)
    assert r["status"] == "completed"
    assert r["agreement"] == "unknown_gemini_verdict"


# ============ save_inline_verdict_for_main_agent ============
def test_save_inline_verdict_writes_file(tmp_path):
    pr = _make_project(tmp_path)
    draft = "draft body 123"
    ok = cfj.save_inline_verdict_for_main_agent(
        draft, "audit", "pass", "claude 通过", pr)
    assert ok is True
    p = cfj._inline_verdict_path(pr, draft, "audit")
    assert p.exists()
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["verdict"] == "pass"
    assert d["judge_name"] == "audit"
    assert d["draft_sha256_prefix"] == cfj._draft_sha12(draft)
    assert d["source"] == "inline_agent_spawn"


def test_save_inline_verdict_then_read_round_trip(mode_isolated, tmp_path):
    """写入 → maybe_run 立即捡用 → status=completed。"""
    mode_isolated.setenv("CROSS_FAMILY_JUDGE_MODE", "active")
    pr = _make_project(tmp_path)
    draft = "round trip draft"
    cfj.save_inline_verdict_for_main_agent(
        draft, "audit", "issues", "claude 拒", pr)
    r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                      gemini_verdict="pass", draft_text=draft, project_root=pr)
    assert r["status"] == "completed"
    assert r["claude_verdict"] == "issues"
    assert r["agreement"] == "disagree"


def test_save_inline_verdict_missing_dir_returns_false(tmp_path):
    """目录 _数据库/.wal 不存在 → 返 False·不抛。"""
    # 不调 _make_project：tmp_path 下没 _数据库/.wal
    ok = cfj.save_inline_verdict_for_main_agent(
        "draft", "audit", "pass", "x", tmp_path)
    assert ok is False


def test_save_inline_verdict_invalid_verdict_returns_false(tmp_path):
    pr = _make_project(tmp_path)
    ok = cfj.save_inline_verdict_for_main_agent(
        "draft", "audit", "maybe", "x", pr)
    assert ok is False


def test_save_inline_verdict_empty_inputs_return_false(tmp_path):
    pr = _make_project(tmp_path)
    assert cfj.save_inline_verdict_for_main_agent("", "audit", "pass", "x", pr) is False
    assert cfj.save_inline_verdict_for_main_agent("draft", "", "pass", "x", pr) is False
    assert cfj.save_inline_verdict_for_main_agent("draft", "audit", "pass", "x", None) is False


# ============ helper 直接覆盖 ============
def test_draft_sha12_deterministic_12_chars():
    s = cfj._draft_sha12("hello world")
    assert len(s) == 12
    assert s == cfj._draft_sha12("hello world")
    assert s != cfj._draft_sha12("hello world ")


def test_inline_verdict_path_layout(tmp_path):
    p = cfj._inline_verdict_path(tmp_path, "abc", "audit")
    assert p.name.startswith("claude_verdict_")
    assert p.name.endswith("_audit.json")
    assert p.parent.name == ".wal"
    assert p.parent.parent.name == "_数据库"


def test_try_load_inline_none_when_empty_inputs(tmp_path):
    assert cfj._try_load_inline_verdict("", "audit", tmp_path) is None
    assert cfj._try_load_inline_verdict("d", "", tmp_path) is None
    assert cfj._try_load_inline_verdict("d", "audit", None) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
