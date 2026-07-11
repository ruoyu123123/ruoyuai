# -*- coding: utf-8 -*-
"""trope_clash_registry + load_clash_registry R11 W6 MODEST 回归"""
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import load_clash_registry as lcr  # noqa: E402


def test_registry_loads():
    cl = lcr.list_clashes()
    assert len(cl) >= 10
    for entry in cl:
        assert "pair" in entry and len(entry["pair"]) == 2
        assert "axis" in entry
        assert "fusion_hint" in entry


def test_registry_json_valid():
    p = _SCRIPTS / "trope_clash_registry.json"
    obj = json.loads(p.read_text(encoding="utf-8"))
    assert "clashes" in obj
    assert isinstance(obj["clashes"], list)


def test_resolve_pair_hit():
    hints = lcr.resolve_clashes(["apocalypse_survival", "romance"])
    assert hints
    assert hints[0]["source"] == "registry_seed"
    assert sorted(hints[0]["pair"]) == ["apocalypse_survival", "romance"]


def test_resolve_pair_miss():
    """unknown pair → no hint"""
    hints = lcr.resolve_clashes(["xianxia", "xuanhuan"])
    assert hints == []


def test_resolve_single_pack_empty():
    assert lcr.resolve_clashes(["xianxia"]) == []


def test_resolve_empty_input():
    assert lcr.resolve_clashes([]) == []
    assert lcr.resolve_clashes(None) == []


def test_resolve_author_override_wins():
    override = {"apocalypse_survival+romance": "AUTHOR_OVERRIDE_HINT"}
    hints = lcr.resolve_clashes(["apocalypse_survival", "romance"], override)
    assert hints[0]["source"] == "author_override"
    assert "OVERRIDE" in hints[0]["fusion_hint"]


def test_resolve_dedupe():
    """同 pack 出现两次不重复"""
    hints = lcr.resolve_clashes(
        ["apocalypse_survival", "apocalypse_survival", "romance"])
    assert len(hints) == 1


def test_resolve_multi_pair():
    """多 pack 多 pair 命中"""
    hints = lcr.resolve_clashes(
        ["apocalypse_survival", "romance", "horror_game", "slice_of_life"])
    pairs = {tuple(sorted(h["pair"])) for h in hints}
    assert ("apocalypse_survival", "romance") in pairs
    assert ("horror_game", "slice_of_life") in pairs


def test_main_cli_runs():
    import subprocess
    r = subprocess.run(
        [sys.executable, str(_SCRIPTS / "load_clash_registry.py"),
         "apocalypse_survival", "romance"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    obj = json.loads(r.stdout)
    assert obj
