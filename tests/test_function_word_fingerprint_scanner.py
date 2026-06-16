# -*- coding: utf-8 -*-
"""round2#2 function_word_fingerprint_scanner 金标准测试（2026-06-16·功能词指纹偏离·确定性）。

🔴 金标准防矫枉过正：真作者原文喂自身基线 active → 零误报（综合 15 词 cluster/base 全作者最小 0.86·
FLOOR 0.6 留 0.26 余量）。覆盖：真作者不误报 / 压平 FAIL / 偏高不报（北极星③）/ 无作者档 skip /
code 不进 hard_gate（北极星⑤）/ 默认 shadow / 单一真理源 import FUNCTION_WORDS。
零依赖·run_tests.py / pytest 双跑。真作者档本地无（CI）→ 金标准跳过不阻塞。
"""
import glob
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import function_word_fingerprint_scanner as F  # noqa: E402


def _set_mode(v):
    if v is None:
        os.environ.pop("FUNCTION_WORD_FINGERPRINT_MODE", None)
    else:
        os.environ["FUNCTION_WORD_FINGERPRINT_MODE"] = v


def _author_fw_style(tmp, base_per_word=5.0):
    """造 fake 作者档（function_word_fingerprint_per_1000·15 词各 base_per_word·base=15×5=75）→ style_path 项目。"""
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    fw = {w: {"mean": base_per_word} for w in F.FUNCTION_WORDS}
    (db / "作者风格.json").write_text(
        json.dumps({"quantitative": {"function_word_fingerprint_per_1000": fw}}, ensure_ascii=False),
        encoding="utf-8")
    return tmp


def _flat_no_funcword(n=300):
    """功能词极少草稿（短句无虚词·function_word 综合密度 ≈ 0 < floor）。"""
    return "\n".join("他来。她走。天黑。门开。" for _ in range(n))


def _high_funcword(n=300):
    """功能词密集草稿（偏高·应不报·北极星③）。"""
    return "\n".join("他的书了着却便竟倒只又不过只是毕竟但而也都在这里。" for _ in range(n))


def test_default_shadow():
    """env 未设 → 默认 shadow（影子·检测力待 gen-model 验证）。"""
    _set_mode(None)
    assert F._mode() == "shadow"


def test_single_source_function_words():
    """单一真理源：FUNCTION_WORDS 从 style_analyzer import（15 词·勿复制）。"""
    import style_analyzer as sa
    assert F.FUNCTION_WORDS == sa.FUNCTION_WORDS
    assert len(F.FUNCTION_WORDS) == 15


def test_active_flat_fails():
    """active：功能词极少草稿（< base×0.6）→ FAIL（虚词使用偏离·文体扁平）。"""
    _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = _author_fw_style(Path(td))
            draft = Path(td) / "draft.txt"
            draft.write_text(_flat_no_funcword(), encoding="utf-8")
            r = F.scan(str(draft), project_root=str(proj))
            assert r["verdict"] == "FAIL_MINOR", r
            assert r["violations"], r
    finally:
        _set_mode(None)


def test_active_high_funcword_no_report():
    """北极星③：功能词偏高 active → PASS（偏高永不报·功能词多是作者风格）。"""
    _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = _author_fw_style(Path(td))
            draft = Path(td) / "draft.txt"
            draft.write_text(_high_funcword(), encoding="utf-8")
            r = F.scan(str(draft), project_root=str(proj))
            assert r["verdict"] == "PASS", r
    finally:
        _set_mode(None)


def test_no_author_profile_skip():
    """无作者档 function_word 基线 → skip（不臆造·北极星②）。"""
    _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "_数据库").mkdir(parents=True)
            draft = Path(td) / "draft.txt"
            draft.write_text(_flat_no_funcword(), encoding="utf-8")
            r = F.scan(str(draft), project_root=str(td))
            assert r["verdict"] == "PASS" and r["violations"] == [], r
            assert "作者档" in r.get("note", "") or "skip" in r.get("note", "").lower(), r
    finally:
        _set_mode(None)


def test_code_never_hard_gate():
    """制度锁：FUNCTION_WORD_FINGERPRINT_DRIFT 绝不进 HARD_GATE_CODES（北极星⑤）。"""
    import audit_hub
    assert "FUNCTION_WORD_FINGERPRINT_DRIFT" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("FUNCTION_WORD_FINGERPRINT_DRIFT", "error") == "advisory"


def _styles_search_roots():
    roots = [_ROOT]
    parts = _ROOT.parts
    if len(parts) >= 3 and parts[-3] == ".claude" and parts[-2] == "worktrees":
        roots.append(Path(*parts[:-3]))
    env = os.environ.get("RUOYUAI_MAIN_CHECKOUT")
    if env:
        roots.append(Path(env))
    return roots


def test_golden_real_author_no_false_positive():
    """🔴 金标准：真作者原文前 6 章 active 喂 scanner → 零误报（综合 cluster/base ≥0.86 > 0.6·防矫枉过正）。"""
    cands = []
    for root in _styles_search_roots():
        cands.extend(glob.glob(str(root / "workspace" / "styles" / "*" / "作者风格_FINAL.json")))
    ran = 0
    _set_mode("active")
    try:
        for pf in cands[:6]:
            author_dir = Path(pf).parent
            raws = sorted(glob.glob(str(author_dir / "原文" / "第0*章.txt")))[:6]
            if len(raws) < 6:
                continue
            d = json.loads(io.open(pf, encoding="utf-8").read())
            fw = (d.get("quantitative", {}) or {}).get("function_word_fingerprint_per_1000", {}) or {}
            if not fw:
                continue  # 作者档缺 fw 基线·scanner 会 skip·不测
            with tempfile.TemporaryDirectory() as td:
                proj = Path(td)
                (proj / "_数据库").mkdir(parents=True)
                shutil.copy(pf, proj / "_数据库" / "作者风格.json")
                text = "".join(io.open(f, encoding="utf-8").read() for f in raws)
                draft = proj / "draft.txt"
                draft.write_text(text, encoding="utf-8")
                r = F.scan(str(draft), project_root=str(proj))
                assert r["verdict"] == "PASS", (author_dir.name, r)
                assert r["violations"] == [], (author_dir.name, r["violations"])
                ran += 1
    finally:
        _set_mode(None)
    if ran == 0:
        print("[SKIP] 真作者档(含 fw 基线)本地无·金标准跳过")
