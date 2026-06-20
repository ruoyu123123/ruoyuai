"""narrative_frequency_scanner 专属测试 — Genette frequency 三态（advisory · 2026-06-20）"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import narrative_frequency_scanner as nfs  # noqa: E402


def _filler(n=22):
    return "\n".join(["夜风扫过山脊石阶上落满松针他独自向上踏步影子拉得很长。"] * n)


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if baseline is not None:
        obj["frequency_baseline"] = baseline
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# ---------- detect_frequency ----------

def test_detect_iterative_hits():
    text = "每当夜幕降临他就上山打坐总是不眠不休日复一日年复一年从未中断。"
    r = nfs.detect_frequency(text)
    assert r["iterative_count"] >= 4


def test_detect_repetitive_hits():
    text = "他再次想起那一日的情景又回想起母亲的脸反复浮现挥之不去再度回到童年那一刻。"
    r = nfs.detect_frequency(text)
    assert r["repetitive_count"] >= 3


def test_detect_clean_singulative():
    r = nfs.detect_frequency("他打开剑匣抽出长剑横在膝上炉火映得刀身泛红。")
    assert r["iterative_count"] == 0
    assert r["repetitive_count"] == 0


# ---------- scan() ----------

def test_scan_active_flat_reports():
    p = _write(_filler())
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "active"
        r = nfs.scan(str(p))
        assert r["verdict"] == "FAIL_MINOR"
        assert r["warning"]
        assert r["violations"][0]["kind"] == "narrative_frequency_flat"
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_active_rich_pass():
    # 大量 iterative 锚词
    text = ("每当寒冬他都下山每日辰时起身打坐总是循一套法门日复一日不曾间断。"
            "每次入定时常神游每到月圆便闭关每逢秋分必登顶。") * 4 + _filler(15)
    p = _write(text)
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "active"
        r = nfs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["iterative_per_1k"] > 0
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    p = _write(_filler())
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "shadow"
        r = nfs.scan(str(p))
        assert r["mode"] == "shadow"
        assert r["violations"] == []
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_off_skeleton():
    p = _write(_filler())
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "off"
        r = nfs.scan(str(p))
        assert r["mode"] == "off"
        assert "iterative_per_1k" not in r
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_default_shadow():
    p = _write(_filler())
    try:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        r = nfs.scan(str(p))
        assert r["mode"] == "shadow"
    finally:
        p.unlink(missing_ok=True)


def test_scan_short_skip():
    p = _write("每当。")
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "active"
        r = nfs.scan(str(p))
        assert "太短" in r.get("note", "")
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)


# ---------- author baseline ----------

def test_baseline_author_profile():
    p = _write(_filler())
    proj = _mk_project(baseline={"iterative_per_1k_min": 0.0})  # 极低
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "active"
        r = nfs.scan(str(p), project_root=proj)
        assert r["baseline_source"] == "author_profile"
        # 阈值低·应 PASS
        assert r["verdict"] == "PASS"
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)


def test_baseline_fallback():
    p = _write(_filler())
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "active"
        r = nfs.scan(str(p))
        assert r["baseline_source"] == "default_fallback"
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)


# ---------- advisory ----------

def test_always_advisory():
    p = _write(_filler())
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "active"
        r = nfs.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_hard_gate():
    assert nfs.ISSUE_CODE == "NARRATIVE_FREQUENCY_FLAT"
    try:
        import audit_hub
        assert "NARRATIVE_FREQUENCY_FLAT" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass


def test_strip_changes():
    assert nfs._strip_changes("正文。\n---CHANGES_FACTUAL---\nlog") == "正文。"


def test_invalid_mode_fallback():
    p = _write(_filler())
    try:
        os.environ["NARRATIVE_FREQUENCY_MODE"] = "bogus"
        r = nfs.scan(str(p))
        assert r["mode"] == "shadow"
    finally:
        os.environ.pop("NARRATIVE_FREQUENCY_MODE", None)
        p.unlink(missing_ok=True)
