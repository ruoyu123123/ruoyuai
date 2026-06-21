# -*- coding: utf-8 -*-
"""writer_intent_anchor R24 W12 Batch-KK · P1"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import writer_intent_anchor as wia  # noqa: E402


def _mk_project():
    return Path(tempfile.mkdtemp())


def _args(project, key, want="A 救妹妹", antagonist="无脸者",
          stake="灵魂被吞", tone_word="冷峻", force=False):
    return SimpleNamespace(
        project=str(project), cluster_key=key,
        want=want, antagonist=antagonist,
        stake=stake, tone_word=tone_word, force=force,
    )


def test_create_writes_anchor_with_sha256():
    proj = _mk_project()
    rc = wia.cmd_create(_args(proj, "001"))
    assert rc == 0
    p = wia._anchor_path(proj, "001")
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    for k in ("want", "antagonist", "stake", "tone_word", "_sha256", "_v"):
        assert k in data
    assert len(data["_sha256"]) == 64


def test_create_rejects_empty_field():
    proj = _mk_project()
    rc = wia.cmd_create(_args(proj, "001", want=""))
    assert rc == 2


def test_create_rejects_overlong_tone_word():
    proj = _mk_project()
    rc = wia.cmd_create(_args(proj, "001", tone_word="一" * 17))
    assert rc == 2


def test_create_no_overwrite_without_force():
    proj = _mk_project()
    wia.cmd_create(_args(proj, "001"))
    rc = wia.cmd_create(_args(proj, "001", want="B 别的"))
    assert rc == 2  # 默认不覆盖


def test_create_force_overwrites():
    proj = _mk_project()
    wia.cmd_create(_args(proj, "001"))
    rc = wia.cmd_create(_args(proj, "001", want="B 别的", force=True))
    assert rc == 0


def test_verify_passes_on_intact():
    proj = _mk_project()
    wia.cmd_create(_args(proj, "001"))
    rc = wia.cmd_verify(SimpleNamespace(project=str(proj), cluster_key="001"))
    assert rc == 0


def test_verify_fails_on_tamper():
    proj = _mk_project()
    wia.cmd_create(_args(proj, "001"))
    p = wia._anchor_path(proj, "001")
    # 偷改 want 不更新 _sha256
    import os
    try:
        os.chmod(p, 0o644)  # 解锁
    except (OSError, PermissionError):
        pass
    data = json.loads(p.read_text(encoding="utf-8"))
    data["want"] = "TAMPERED"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rc = wia.cmd_verify(SimpleNamespace(project=str(proj), cluster_key="001"))
    assert rc == 2


def test_verify_fails_missing_file():
    proj = _mk_project()
    rc = wia.cmd_verify(SimpleNamespace(project=str(proj), cluster_key="999"))
    assert rc == 2


def test_read_returns_data():
    proj = _mk_project()
    wia.cmd_create(_args(proj, "001"))
    rc = wia.cmd_read(SimpleNamespace(project=str(proj), cluster_key="001"))
    assert rc == 0


def test_read_missing_returns_1():
    proj = _mk_project()
    rc = wia.cmd_read(SimpleNamespace(project=str(proj), cluster_key="missing"))
    assert rc == 1


def test_load_anchor_helper_returns_data():
    proj = _mk_project()
    wia.cmd_create(_args(proj, "001"))
    a = wia.load_anchor(proj, "001")
    assert a is not None
    assert a["want"] == "A 救妹妹"
    assert "_sha256" in a


def test_load_anchor_returns_none_on_tamper():
    proj = _mk_project()
    wia.cmd_create(_args(proj, "001"))
    p = wia._anchor_path(proj, "001")
    import os
    try:
        os.chmod(p, 0o644)
    except (OSError, PermissionError):
        pass
    data = json.loads(p.read_text(encoding="utf-8"))
    data["want"] = "evil"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    a = wia.load_anchor(proj, "001")
    assert a is None  # SHA-256 失败


def test_load_anchor_returns_none_when_missing():
    proj = _mk_project()
    assert wia.load_anchor(proj, "001") is None


def test_canonical_json_deterministic():
    a = wia._canonical_json({"b": 1, "a": 2})
    b = wia._canonical_json({"a": 2, "b": 1})
    assert a == b
    assert "_sha256" not in a


def test_compute_sha256_excludes_self():
    p = {"want": "x", "antagonist": "y", "stake": "z", "tone_word": "w"}
    p2 = dict(p)
    p2["_sha256"] = "fake"
    assert wia._compute_sha256(p) == wia._compute_sha256(p2)


def test_canonical_unicode_consistent():
    p = {"want": "悲愤", "antagonist": "怔忡"}
    s = wia._canonical_json(p)
    # 确保中文不被 \uXXXX 编码（ensure_ascii=False）
    assert "悲愤" in s
