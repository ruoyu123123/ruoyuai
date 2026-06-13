"""阶段3：题材自适应（genre 包单一真理源 + 路由 + 注入 + scanner advisory 边界）。

钉死：
  · genre_dimension_packs.json 合法·get_pack 路由·unknown 退化纯通用池
  · 题材专属 scanner 全 advisory·不进 HARD_GATE_CODES（hard_gate 不随题材变·北极星⑤）
  · build_manifest._collect_genre_pack_directives 按 genre 注入·unknown→None（零回归）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import scaffold_genre_packs as gp  # noqa: E402
import build_manifest as bm  # noqa: E402
import romance_pacing_scanner as rps  # noqa: E402
import litrpg_structure_scanner as lps  # noqa: E402


# ---------- 单一真理源 ----------

def test_packs_verify_ok():
    assert gp._verify() == 0


def test_canonical_genres_complete():
    cg = gp.canonical_genres()
    for g in ("romance", "horror_game", "unknown"):
        assert g in cg


def test_get_pack_routing():
    assert gp.get_pack("romance").get("judge_dims")
    assert gp.get_pack("horror_game").get("judge_dims")
    # unknown / 无包 / None → {} 退化纯通用池（零回归）
    assert gp.get_pack("unknown") == {}
    assert gp.get_pack("xianxia") == {}     # 无专属包
    assert gp.get_pack(None) == {}
    assert gp.get_pack("") == {}


def test_get_scanner_and_directives():
    assert gp.get_scanner("romance") == "romance_pacing_scanner"
    assert gp.get_scanner("horror_game") == "litrpg_structure_scanner"
    assert gp.get_scanner("unknown") is None
    assert gp.get_writer_directives("romance")
    assert gp.get_writer_directives("unknown") == []


# ---------- scanner advisory 边界（hard_gate 不随题材变） ----------

def test_genre_scanners_always_advisory():
    """题材 scanner gate_level 必 advisory·code 不在 audit_hub.HARD_GATE_CODES。"""
    import audit_hub
    txt = "\n\n".join("他平静地走在路上想着事情。" for _ in range(30))
    for scan in (rps.scan, lps.scan):
        r = scan(txt)
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    assert "ROMANCE_PACING" not in audit_hub.HARD_GATE_CODES
    assert "LITRPG_STRUCTURE" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("ROMANCE_PACING") == "advisory"
    assert audit_hub._gate_level_for("LITRPG_STRUCTURE") == "advisory"


def test_litrpg_flags_no_panel():
    """游戏向 scanner：无系统面板标记→flag（>500 CJK）。"""
    txt = "\n\n".join("他平静地走在长长的道路上面慢慢思考着许多寻常普通的日常琐事情。" for _ in range(30))
    r = lps.scan(txt)
    assert any(v["kind"] == "no_system_panel" for v in r["violations"]), r["metrics"]


def test_litrpg_passes_with_panel():
    """有面板标记→PASS。"""
    txt = "\n\n".join("【系统提示】等级提升！属性+10 经验值 999。他看着面板。副本开启。" for _ in range(20))
    r = lps.scan(txt)
    assert r["verdict"] == "PASS"


# ---------- manifest 注入路由 ----------

def _set(m):
    if m is None:
        os.environ.pop("GENRE_INJECT_MODE", None)
    else:
        os.environ["GENRE_INJECT_MODE"] = m


def _mk(tmp: Path, genre_tags=None, book="测试书") -> Path:
    proj = tmp / book
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    style = {"author": "x", "quantitative": {"sentence_length": {"mean": 28}}}
    if genre_tags:
        style["genre_tags"] = genre_tags
    (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return proj


def test_manifest_inject_active_romance():
    _set("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = _mk(Path(d), genre_tags=["romance"])
            s = bm.DatabaseScanner(proj, 1)
            r = bm._collect_genre_pack_directives(s)
            assert r is not None
            assert r["genre"] == "romance"
            assert r["gate_level"] == "advisory"
            assert r["directives"]
    finally:
        _set(None)


def test_manifest_unknown_genre_none():
    """unknown genre → None（退化纯通用池·零回归）。"""
    _set("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = _mk(Path(d), genre_tags=None, book="平凡故事")  # 无关键词→unknown
            s = bm.DatabaseScanner(proj, 1)
            assert bm._collect_genre_pack_directives(s) is None
    finally:
        _set(None)


def test_manifest_shadow_default_none():
    _set(None)  # 默认 shadow
    with tempfile.TemporaryDirectory() as d:
        proj = _mk(Path(d), genre_tags=["romance"])
        s = bm.DatabaseScanner(proj, 1)
        assert bm._collect_genre_pack_directives(s) is None  # shadow 不注入


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"[genre_packs] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
