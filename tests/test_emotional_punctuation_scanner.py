# -*- coding: utf-8 -*-
"""emotional_punctuation_scanner 金标准测试（2026-06-16·穷尽核查#2·情绪标点综合密度）。

🔴 金标准防矫枉过正（核心闸）：真作者原文喂自身基线 active → 零误报（综合避单类误报·
   FLOOR_RATIO 0.3 < 真作者各 cluster 最小 0.44）。覆盖：真作者不误报 / 压平 FAIL / 偏高不报
   （北极星③）/ 无作者档 skip（北极星②）/ code 不进 hard_gate（北极星⑤）/ 默认 shadow。
零依赖·仓库根 pytest 入口。真作者档本地无（CI）→ 金标准跳过不阻塞。
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
import emotional_punctuation_scanner as EP  # noqa: E402


def _set_mode(v):
    if v is None:
        os.environ.pop("EMOTIONAL_PUNCT_MODE", None)
    else:
        os.environ["EMOTIONAL_PUNCT_MODE"] = v


def _styles_search_roots():
    roots = [_ROOT]
    parts = _ROOT.parts
    if len(parts) >= 3 and parts[-3] == ".claude" and parts[-2] == "worktrees":
        roots.append(Path(*parts[:-3]))
    env = os.environ.get("RUOYUAI_MAIN_CHECKOUT")
    if env:
        roots.append(Path(env))
    return roots


def _pg(pd, k):
    v = pd.get(k, {})
    if isinstance(v, dict):
        return v.get("mean")
    return v if isinstance(v, (int, float)) else None


def _discover_authors(limit=5):
    """发现有作者档（含 3 类情绪标点 mean）+ 原文前 6 章的作者·返回 [(prof_path, [6 章])]。"""
    out, seen = [], set()
    for root in _styles_search_roots():
        cands = (glob.glob(str(root / "workspace" / "styles" / "*" / "作者风格_FINAL.json"))
                 + glob.glob(str(root / "workspace" / "styles" / "*" / "作者风格.json")))
        for pf in cands:
            author = Path(pf).parent.name
            if author in seen or "test" in author.lower():
                continue
            raws = sorted(glob.glob(str(Path(pf).parent / "原文" / "第0*章.txt")))[:6]
            if len(raws) < 6:
                continue
            try:
                d = json.loads(io.open(pf, encoding="utf-8").read())
            except Exception:
                continue
            pd = (d.get("quantitative", {}) or {}).get("punctuation_density_per_1000", {}) or {}
            if all(isinstance(_pg(pd, k), (int, float)) for k in ("exclamation", "question", "ellipsis")):
                seen.add(author)
                out.append((pf, raws))
    return out[:limit]


def _mk_project(prof_path, draft_text):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "_数据库").mkdir(parents=True)
    shutil.copy(prof_path, tmp / "_数据库" / "作者风格.json")
    draft = tmp / "draft.txt"
    draft.write_text(draft_text, encoding="utf-8")
    return tmp, str(draft)


def test_real_author_no_false_positive_active():
    """🔴 金标准：真作者原文前 6 章 active → 零误报（综合 FLOOR_RATIO 0.3 < 真作者最小 0.44）。"""
    authors = _discover_authors()
    if not authors:
        print("[SKIP] 真作者档/原文本地无（CI）·金标准跳过")
        return
    _set_mode("active")
    ran = 0
    try:
        for pf, raws in authors:
            text = "".join(io.open(f, encoding="utf-8").read() for f in raws)
            proj, draft = _mk_project(pf, text)
            rep = EP.scan(draft, project_root=str(proj))
            assert rep["verdict"] == "PASS", (Path(pf).parent.name, rep)
            assert rep["violations"] == [], (Path(pf).parent.name, rep["violations"])
            ran += 1
    finally:
        _set_mode(None)
    assert ran >= 1


def test_flat_draft_fails_active():
    """灵敏度：情绪标点全无的草稿 active → FAIL（情绪扁平·comb_density 0 < floor）。"""
    authors = _discover_authors(limit=1)
    if not authors:
        print("[SKIP] 无作者档·跳过")
        return
    _set_mode("active")
    try:
        flat = "他走进房间。她坐在椅子上。桌上放着一本书。窗外下着雨。" * 80
        proj, draft = _mk_project(authors[0][0], flat)
        rep = EP.scan(draft, project_root=str(proj))
        assert rep["verdict"] == "FAIL_MINOR", rep
        assert rep["violations"], rep
    finally:
        _set_mode(None)


def test_high_emotional_punct_no_report():
    """北极星③：情绪标点偏高 active → PASS（偏高永不报·高情绪标点是作者风格）。"""
    authors = _discover_authors(limit=1)
    if not authors:
        print("[SKIP] 无作者档·跳过")
        return
    _set_mode("active")
    try:
        high = "真的吗？！太好了！这怎么可能……他喊道。不！别走！为什么？" * 80
        proj, draft = _mk_project(authors[0][0], high)
        rep = EP.scan(draft, project_root=str(proj))
        assert rep["verdict"] == "PASS", rep
    finally:
        _set_mode(None)


def test_no_author_profile_skip():
    """无作者档 → skip（不臆造基线·北极星②第一权威）。"""
    _set_mode("active")
    try:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "_数据库").mkdir(parents=True)
        draft = tmp / "draft.txt"
        draft.write_text("他走进房间。" * 200, encoding="utf-8")
        rep = EP.scan(str(draft), project_root=str(tmp))
        assert rep["verdict"] == "PASS" and rep["violations"] == [], rep
        assert "作者档" in rep.get("note", "") or "skip" in rep.get("note", "").lower(), rep
    finally:
        _set_mode(None)


def test_code_never_hard_gate():
    """制度锁：EMOTIONAL_PUNCT_SPARSE 绝不进 HARD_GATE_CODES（北极星⑤·advisory 可豁免）。"""
    import audit_hub
    assert "EMOTIONAL_PUNCT_SPARSE" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("EMOTIONAL_PUNCT_SPARSE", "error") == "advisory"


def test_default_mode_shadow():
    """env 未设 → 默认 shadow（影子·检测力待 gen-model 草稿验证再 active）。"""
    _set_mode(None)
    assert EP._mode() == "shadow"
