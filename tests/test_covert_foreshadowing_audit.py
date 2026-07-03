# -*- coding: utf-8 -*-
"""covert_foreshadowing_audit.py 专属回归测试
(2026-06-20·R12 W6 Batch-Q·确定性·零依赖·零 LLM/零联网)"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import covert_foreshadowing_audit as mod  # noqa: E402

_TARGET = _SCRIPTS / "covert_foreshadowing_audit.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("COVERT_FORESHADOWING_MODE", None)
    else:
        os.environ["COVERT_FORESHADOWING_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, foreshadowing=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if foreshadowing is not None:
        (proj / "_数据库" / "foreshadowing.json").write_text(
            json.dumps(foreshadowing, ensure_ascii=False), encoding="utf-8")
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"author_covert_ratio_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


_OVERT_DRAFT = ("他想，日后这件事会带来麻烦。\n"
                "她说，将来你会后悔。\n"
                "多年后，他终于明白。\n"
                "从此以后，命运改变了。\n"
                "之后的事情让人无法预料。\n") * 30

_COVERT_DRAFT = ("她无意中瞥见案上那只匣中物。\n"
                  "他下意识地摸了摸怀里的玉佩。\n"
                  "邻桌的客人随口提了一句旧事。\n"
                  "窗外远远地传来一声叹息。\n"
                  "墙上挂着的画似乎少了什么。\n") * 30


# ── 基础分支 ────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_OVERT_DRAFT), project_root=_mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert out["violations"] == [] and out["warning"] is None
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("太短。" * 5), project_root=_mk_project())
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_plants_skips():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("active")
        # 长草稿但无任何 plant 锚词
        clean = "他走到了集市。\n他买了菜。\n他回家了。\n" * 50
        out = mod.scan(_write(clean), project_root=_mk_project())
        assert out["note"] == "本 cluster 无可识别 plant·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ratio 分支 ─────────────────────────────────────
def test_thin_overt_draft_active():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_OVERT_DRAFT), project_root=_mk_project())
        assert out["plant_total"] > 0
        # overt 锚词密集 → covert_ratio 低 → THIN
        assert out["covert_ratio"] < 0.40
        assert any(v["code"] == "COVERT_FORESHADOWING_THIN" for v in out["violations"])
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_covert_draft_active():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_COVERT_DRAFT), project_root=_mk_project())
        assert out["plant_total"] > 0
        assert out["covert_ratio"] > 0.70
        codes = [v["code"] for v in out["violations"]]
        assert "COVERT_FORESHADOWING_OPAQUE" in codes
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_OVERT_DRAFT), project_root=_mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── classify_plant ──────────────────────────────────
def test_classify_plant_overt():
    assert mod.classify_plant("日后必有大乱") == "overt"


def test_classify_plant_buried():
    assert mod.classify_plant("他下意识地摸了摸怀里的物件") == "buried"


def test_classify_plant_passing():
    assert mod.classify_plant("他随口提了一句旧事") == "passing"


def test_classify_plant_objects():
    assert mod.classify_plant("案上摆着一只玉匣") == "objects"


def test_classify_plant_parallel():
    assert mod.classify_plant("远处传来一阵呐喊声") == "parallel"


def test_classify_plant_empty_defaults_overt():
    assert mod.classify_plant("") == "overt"
    assert mod.classify_plant("无锚词的纯文本") == "overt"


# ── _read_baseline ──────────────────────────────────
def test_baseline_fallback_when_no_project():
    b = mod._read_baseline(None)
    assert b["_source"] == "fallback"
    assert b["p10"] == 0.40 and b["p90"] == 0.70


def test_baseline_fallback_when_no_file():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    b = mod._read_baseline(proj)
    assert b["_source"] == "fallback"


def test_baseline_from_author_profile():
    proj = _mk_project(baseline={"p10": 0.50, "p50": 0.65, "p90": 0.80})
    b = mod._read_baseline(proj)
    assert b["_source"] == "author_profile"
    assert b["p10"] == 0.50 and b["p90"] == 0.80


def test_baseline_bad_json_falls_back():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{bad", encoding="utf-8")
    b = mod._read_baseline(proj)
    assert b["_source"] == "fallback"


def test_baseline_top_not_dict():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("[1,2]", encoding="utf-8")
    assert mod._read_baseline(proj)["_source"] == "fallback"


# ── foreshadowing.json plant ingestion ──────────────
def test_plants_from_foreshadowing_json():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("active")
        fjson = {"entries": [
            {"plant_text": "案上摆着的玉匣里藏着秘密"},  # objects
            {"plant_text": "她无意中瞥见了那张地图"},  # buried
            {"plant_text": "日后此物将重见天日"},  # overt
        ]}
        out = mod.scan(_write(_COVERT_DRAFT), project_root=_mk_project(foreshadowing=fjson))
        # 至少包含 foreshadowing.json 的 plant
        assert out["plant_total"] >= 3
    finally:
        _set_mode(bak)


def test_bad_foreshadowing_json_safe():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        (proj / "_数据库" / "foreshadowing.json").write_text("{bad", encoding="utf-8")
        out = mod.scan(_write(_OVERT_DRAFT), project_root=proj)
        assert out["verdict"] in ("PASS", "FAIL_MINOR")
        assert out["plant_total"] >= 0
    finally:
        _set_mode(bak)


# ── _strip_changes / _cjk_count ─────────────────────
def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\n{}") == "正文。"


def test_strip_changes_plain():
    assert mod._strip_changes("正文。\n---CHANGES---\nlog") == "正文。"


def test_strip_changes_none():
    assert mod._strip_changes("纯正文") == "纯正文"


def test_cjk_count_basic():
    assert mod._cjk_count("你好abc123") == 2
    assert mod._cjk_count("") == 0


# ── _mode 回落 ──────────────────────────────────────
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_default_shadow():
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── CLI ─────────────────────────────────────────────
def test_main_cli_clean_pass():
    proj = _mk_project()
    p = _write("他走了。" * 100)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "COVERT_FORESHADOWING_MODE": "active",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "PASS"


def test_main_cli_thin_fail_minor():
    proj = _mk_project()
    p = _write(_OVERT_DRAFT)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "COVERT_FORESHADOWING_MODE": "active",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


# ── 🔴 2026-07-03 zero_shot_prototype 模型优先路径测试(W3) ──────────────────
import math  # noqa: E402


def _char_freq_embedding(text, dim=32):
    """确定性 mock embedding（字符频率向量·同 test_macguffin_entanglement_scanner 手法）。"""
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _run_with_mock_embedding(fn):
    """EMBED_BACKEND=mock + monkeypatch embedding_store.compute_embedding 后跑 fn。"""
    bak_eb = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "mock"
    import embedding_store
    import zero_shot_prototype
    orig = embedding_store.compute_embedding
    embedding_store.compute_embedding = _char_freq_embedding
    zero_shot_prototype.clear_cache()
    try:
        return fn()
    finally:
        embedding_store.compute_embedding = orig
        zero_shot_prototype.clear_cache()
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_classify_plant_mode_gate_off_uses_fallback():
    mode, csrc = mod._classify_plant_mode("案上摆着一只玉匣", "objects")
    assert mode == "objects"
    assert csrc == "lexicon"


def test_classify_plant_mode_model_overrides_when_backend_available():
    """fallback 故意传错(overt)·真后端下模型应正确判 objects 并覆盖 fallback。"""
    def _do():
        mode, csrc = mod._classify_plant_mode("案上摆着一枚不起眼的旧玉佩", "overt")
        assert mode == "objects"
        assert csrc == "zero_shot_embedding"
    _run_with_mock_embedding(_do)


def test_scan_active_with_mock_backend_smoke():
    """mock 真后端下 scan() 端到端仍正常产出（不因接线而崩/挂）。"""
    bak = os.environ.get("COVERT_FORESHADOWING_MODE")
    try:
        _set_mode("active")

        def _do():
            out = mod.scan(_write(_COVERT_DRAFT), project_root=_mk_project())
            assert out["plant_total"] > 0
            assert 0.0 <= out["covert_ratio"] <= 1.0
            assert out["verdict"] in ("PASS", "FAIL_MINOR")

        _run_with_mock_embedding(_do)
    finally:
        _set_mode(bak)


def test_delivery_mode_prototypes_cover_all_buckets():
    assert set(mod._DELIVERY_MODE_PROTOTYPES.keys()) == set(mod._DELIVERY_LEXICON.keys())
