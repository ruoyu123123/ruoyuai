# -*- coding: utf-8 -*-
"""cross_family_judge_check 守门测试（A 方案回滚后·2026-06-20·永远 skipped 占位）。

A 方案：删 GUI + 回滚 BYOK Claude/Anthropic 后，本模块降级为占位 stub（详见
cross_family_judge_check.py docstring 与 module）。所有 maybe_run 路径 → skipped；
本测试守护 6 个 gate 返回的 reason 可区分性 + 永远 skipped 不阻断主链（北极星② / ⑤）。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_family_judge_check as cfj  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CROSS_FAMILY_JUDGE_MODE", None)
    else:
        os.environ["CROSS_FAMILY_JUDGE_MODE"] = m


# ============ 6 守门测试（gate 返回 reason 可区分） ============
def test_off_mode_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        _set_mode("off")
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped"
        assert r["reason"] == "mode=off"
    finally:
        _set_mode(bak)


def test_ineligible_judge_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        _set_mode("active")
        r = cfj.maybe_run(judge_name="kicker", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped"
        assert "eligible" in r["reason"]
    finally:
        _set_mode(bak)


def test_non_finale_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        _set_mode("active")
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=False,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped"
        assert "finale" in r["reason"]
    finally:
        _set_mode(bak)


def test_no_draft_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        _set_mode("active")
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text=None)
        assert r["status"] == "skipped"
        assert "无草稿" in r["reason"]
    finally:
        _set_mode(bak)


def test_mode_default_shadow():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        os.environ.pop("CROSS_FAMILY_JUDGE_MODE", None)
        assert cfj._mode() == "shadow"
    finally:
        _set_mode(bak)


# ============ A 方案占位 stub：所有合法触发路径 → skipped + reason 提示 phase 2 ============
def test_active_finale_with_draft_returns_skipped_phase2_marker():
    """所有 gate 通过（mode=active + eligible + finale + draft）→ 仍 skipped·
    reason 含 phase 2 提示（主代理 inline 文件协议尚未实装）。"""
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        _set_mode("active")
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped", r
        assert "BYOK" in r["reason"] or "inline" in r["reason"], r["reason"]
        # verdict_pair 仍能正确回填 gemini 侧（contract 兼容）
        assert r["verdict_pair"] == ["pass", None]
    finally:
        _set_mode(bak)


def test_shadow_default_eligible_finale_with_draft_skipped():
    """默认 shadow 模式 + 合法触发 → 仍 skipped（永不阻断主链·advisory）。"""
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        os.environ.pop("CROSS_FAMILY_JUDGE_MODE", None)
        r = cfj.maybe_run(judge_name="voice", is_finale_subcluster=True,
                          gemini_verdict="issues", draft_text="x")
        assert r["status"] == "skipped"
        assert r["mode"] == "shadow"
    finally:
        _set_mode(bak)


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
