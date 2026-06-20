# -*- coding: utf-8 -*-
"""cross_family_judge_check 专属测试(advisory · shadow · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_family_judge_check as cfj  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CROSS_FAMILY_JUDGE_MODE", None)
    else:
        os.environ["CROSS_FAMILY_JUDGE_MODE"] = m


def _clear_keys():
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "RUOYU_CLAUDE_KEY"):
        os.environ.pop(k, None)


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


def test_no_claude_key_skips_silently():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_keys = {k: os.environ.get(k) for k in
                ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "RUOYU_CLAUDE_KEY")}
    try:
        _set_mode("active")
        _clear_keys()
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped"
        assert "BYOK" in r["reason"]
    finally:
        _set_mode(bak)
        for k, v in bak_keys.items():
            if v is not None:
                os.environ[k] = v


def test_agree_path_with_key():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x")
        # stub 返回 'pass' → agree
        assert r["status"] == "agree"
        assert r["verdict_pair"] == ["pass", "pass"]
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


def test_disagree_path_with_key():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="issues", draft_text="x")
        # gemini=issues vs claude stub='pass' → disagree
        assert r["status"] == "disagree"
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


def test_calibration_cache_written():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        proj = Path(tempfile.mkdtemp())
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x",
                          project_root=proj)
        cache = proj / "_数据库" / ".judge" / "cross_family_calibration.json"
        assert cache.exists()
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


def test_no_draft_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text=None)
        assert r["status"] == "skipped"
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


def test_mode_default_shadow():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        os.environ.pop("CROSS_FAMILY_JUDGE_MODE", None)
        assert cfj._mode() == "shadow"
    finally:
        _set_mode(bak)
