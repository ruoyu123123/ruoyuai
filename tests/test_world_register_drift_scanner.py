# -*- coding: utf-8 -*-
"""world_register_drift_scanner.py 专属回归测试（2026-06-20·R8 W4 Batch-F·L18 STRONG）。

覆盖任务要求：
  ① tier 解析三级（genre_packs.register_tier > 世界观.register_tier > 作者风格.register_tier > skip）
  ② 5 个 tier 词典加载（epic_fantasy/xianxia/xuanhuan/historical/modern_urban）
  ③ forward 方向（高语域题材 + 当代俚语/工程黑话 + active → FAIL_MINOR）
  ④ reverse 方向（modern_urban 题材 + 高语域古风词 + active → FAIL_MINOR）
  ⑤ 作者档 author_register_tier_baseline.drift_per_1k 当 floor（自身用过不报）
  ⑥ voice_pack.voice_meta.allow_register_drift = True 豁免
  ⑦ shadow 高密度命中只记不判（零回归）
  ⑧ off → 骨架（不读草稿不门控）
  ⑨ 与 R6 anachronism 显式去重（同 cluster 草稿可同时跑互不干扰）
  ⑩ _strip_changes / _cjk_count / _read_json / detect_drift / overlap 去重
  ⑪ main() CLI subprocess 退出码
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import world_register_drift_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "world_register_drift_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("REGISTER_DRIFT_MODE", None)
    else:
        os.environ["REGISTER_DRIFT_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, register_tier=None, genre=None, world_register_tier=None,
                style_register_tier=None,
                voice_meta=None, author_baseline=None):
    """造项目目录·按需写 _数据库/各子系统."""
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    # 作者风格.json
    style_obj = {}
    if genre:
        style_obj["genre_tags"] = [genre]
    if style_register_tier:
        style_obj["register_tier"] = style_register_tier
    if author_baseline is not None:
        style_obj["author_register_tier_baseline"] = author_baseline
    if style_obj:
        (db / "作者风格.json").write_text(
            json.dumps(style_obj, ensure_ascii=False), encoding="utf-8")
    # 世界观.json
    if world_register_tier is not None:
        (db / "世界观.json").write_text(
            json.dumps({"register_tier": world_register_tier},
                       ensure_ascii=False), encoding="utf-8")
    # voice_pack.json
    if voice_meta is not None:
        (db / "voice_pack.json").write_text(
            json.dumps({"voice_meta": voice_meta}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 高密度漂移草稿（forward）：仙侠/玄幻背景里出现当代俚语+管理黑话 ·>500 CJK
_FORWARD_DRIFT_DRAFT = (
    "他掐诀念咒，KPI 拉满，复盘了三日。师弟说真香，老六罢了。"
    "宗门要破圈搞 OKR，下沉到外门弟子，闭环管理。" * 18)

# 高密度漂移草稿（reverse）：现代场景里出现"朕/陛下/卿" ·>500 CJK
_REVERSE_DRIFT_DRAFT = (
    "他打开手机查看消息，回了一句『陛下圣明，微臣这就去办』。同事说『朕看你今天加班』。"
    "群里有人发『诸位卿家请回禀』。" * 22)

# 古风干净草稿（无漂移词）
_CLEAN_DRAFT = "他提剑走进山门，青石阶上落满松针。师兄在丹房前煮茶，炉火映着他的眉眼。" * 25


# ── ⑧ off → 骨架 ────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(genre="xianxia")
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
        assert "per_1k" not in out and "register_tier" not in out
    finally:
        _set_mode(bak)


# ── ③ forward·仙侠+当代俚语/工程黑话+active → FAIL_MINOR ──────────────────
def test_active_xianxia_forward_drift_fail_minor():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia")
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=proj)
        assert out["register_tier"] == "xianxia"
        assert out["direction"] == "forward"
        assert out["drift_count"] > 0 and out["per_1k"] > 0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"] and out["violations"][0]["severity"] == "minor"
        assert out["gate_level"] == "advisory"
        cc = out["category_counts"]
        # 应抓到管理黑话 + 俚语
        assert any("黑话" in k for k in cc.keys())
        assert any("俚语" in k for k in cc.keys())
    finally:
        _set_mode(bak)


# ── ④ reverse·modern_urban+高语域古风词+active → FAIL_MINOR ───────────────
def test_active_modern_urban_reverse_drift_fail_minor():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="urban_supernatural")
        out = mod.scan(_write(_REVERSE_DRIFT_DRAFT), project_root=proj)
        assert out["register_tier"] == "modern_urban"
        assert out["direction"] == "reverse"
        assert out["drift_count"] > 0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert "高语域古风词" in out["warning"]
        cc = out["category_counts"]
        # 应抓到帝王自称/敬称
        assert any(("帝王" in k) or ("敬称" in k) for k in cc.keys())
    finally:
        _set_mode(bak)


# ── 干净草稿·xianxia → PASS ──────────────────────────────────────────────
def test_clean_xianxia_pass():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia")
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert out["register_tier"] == "xianxia"
        assert out["drift_count"] == 0
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── ① tier 解析三级 ──────────────────────────────────────────────────────
def test_tier_resolved_from_genre_pack():
    """genre=xianxia → 经 pack 路由出 xianxia tier。"""
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia")
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert out["genre"] == "xianxia"
        assert out["register_tier"] == "xianxia"
    finally:
        _set_mode(bak)


def test_tier_resolved_from_worldview_fallback():
    """genre 无 register_tier 路由 → 退世界观.json register_tier。"""
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="mystery",  # mystery 包未声明 register_tier
                           world_register_tier="epic_fantasy")
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert out["register_tier"] == "epic_fantasy"
    finally:
        _set_mode(bak)


def test_tier_resolved_from_style_fallback():
    """世界观无 → 退作者风格.json register_tier。"""
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="mystery",
                           style_register_tier="historical")
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=proj)
        assert out["register_tier"] == "historical"
    finally:
        _set_mode(bak)


def test_no_tier_skips():
    """全无 tier 声明 → skip（北极星②：作者未标语域不擅判）。"""
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()  # 啥都没
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=proj)
        assert out["register_tier"] is None
        assert "未声明 register_tier" in out.get("note", "")
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "per_1k" not in out
    finally:
        _set_mode(bak)


def test_no_project_skips():
    """project_root=None → 同样 skip。"""
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=None)
        assert out["register_tier"] is None
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_unknown_tier_skips():
    """register_tier 值在词典里不存在 → skip。"""
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(world_register_tier="nonexistent_tier_xyz")
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=proj)
        assert out["register_tier"] == "nonexistent_tier_xyz"
        assert "无 tier 词典" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑤ 作者档 baseline 当 floor ────────────────────────────────────────────
def test_author_baseline_raises_floor_no_report():
    """作者档 author_register_tier_baseline.drift_per_1k 巨大 → floor 抬高 → 不报."""
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            genre="xianxia",
            author_baseline={"drift_per_1k": 1000.0})  # 远高于本草稿密度（实测 per_1k ~430）
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=proj)
        assert out["baseline_per_1k"] == 1000.0
        assert out["threshold"] > 1000.0
        # floor 抬高 → per_1k < floor → 不报
        assert out["per_1k"] < out["threshold"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑥ voice_pack 豁免 ────────────────────────────────────────────────────
def test_voice_pack_meta_exempt():
    """voice_pack.voice_meta.allow_register_drift=True → 豁免（戏谑/穿越元）。"""
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            genre="xianxia",
            voice_meta={"allow_register_drift": True, "tone": "meta_parody"})
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=proj)
        assert out.get("voice_pack_exempt") is True
        assert "豁免" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_voice_pack_meta_no_exempt_when_false():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            genre="xianxia",
            voice_meta={"allow_register_drift": False})
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=proj)
        assert not out.get("voice_pack_exempt")
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


# ── ⑦ shadow 模式 ───────────────────────────────────────────────────────
def test_shadow_records_no_violation():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(genre="xianxia")
        out = mod.scan(_write(_FORWARD_DRIFT_DRAFT), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["drift_count"] > 0  # 确实命中
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 ──────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia")
        out = mod.scan(_write("KPI 复盘三日。" * 5), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 5 tier 词典文件存在 ──────────────────────────────────────────────────
def test_all_5_tier_lexicons_exist_and_valid():
    tiers = ["epic_fantasy", "xianxia", "xuanhuan", "historical", "modern_urban"]
    for tier in tiers:
        lex = mod._load_lexicon(tier)
        assert isinstance(lex, dict), tier
        assert lex.get("tier") == tier
        assert lex.get("direction") in ("forward", "reverse")
        dt = lex.get("drift_terms")
        assert isinstance(dt, dict) and dt, tier
        # 至少 1 个分类 · 每分类至少 5 个词
        for cat, terms in dt.items():
            assert isinstance(terms, list) and len(terms) >= 5, f"{tier}.{cat}"


def test_modern_urban_is_reverse():
    """modern_urban 词典 direction=reverse 且含『陛下/朕/卿』高语域词。"""
    lex = mod._load_lexicon("modern_urban")
    assert lex["direction"] == "reverse"
    all_terms = []
    for terms in lex["drift_terms"].values():
        all_terms.extend(terms)
    assert "朕" in all_terms and "陛下" in all_terms and "卿" in all_terms


def test_xianxia_is_forward_and_has_modern_slang():
    """xianxia 词典 direction=forward 且含『yyds/KPI』等当代漂移词。"""
    lex = mod._load_lexicon("xianxia")
    assert lex["direction"] == "forward"
    all_terms = []
    for terms in lex["drift_terms"].values():
        all_terms.extend(terms)
    assert "yyds" in all_terms and "KPI" in all_terms


# ── ⑩ 工具函数全分支 ───────────────────────────────────────────────────────
def test_strip_changes_factual_separator():
    raw = "古风正文。\n\n---CHANGES_FACTUAL---\n{\"foo\": 1}"
    assert mod._strip_changes(raw) == "古风正文。"


def test_strip_changes_plain_separator():
    raw = "另一段。\n---CHANGES---\nlog"
    assert mod._strip_changes(raw) == "另一段。"


def test_strip_changes_no_separator():
    raw = "纯正文无分隔符。"
    assert mod._strip_changes(raw) == raw


def test_cjk_count():
    assert mod._cjk_count("你好世界中abc123，。") == 5
    assert mod._cjk_count("") == 0


def test_read_json_missing():
    assert mod._read_json(Path(tempfile.mkdtemp()) / "no.json") is None


def test_read_json_bad():
    p = Path(tempfile.mkdtemp()) / "bad.json"
    p.write_text("{ broken", encoding="utf-8")
    assert mod._read_json(p) is None


def test_detect_drift_overlap_dedup():
    """长词优先匹配·短词不与长词重叠（破大防 vs 破防）。"""
    lex = {"drift_terms": {"俚语": ["破大防", "破防"]}}
    hits = mod.detect_drift("他破大防了。", lex)
    # 仅命中"破大防"（包含"破防"段）·"破防"不重叠抓
    assert [h["term"] for h in hits] == ["破大防"]


def test_detect_drift_empty_lex():
    assert mod.detect_drift("text", {}) == []
    assert mod.detect_drift("text", {"drift_terms": {}}) == []


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_default_shadow_when_unset():
    bak = os.environ.get("REGISTER_DRIFT_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_voice_pack_missing_no_exempt():
    """无 voice_pack.json → 不豁免。"""
    proj = _mk_project(genre="xianxia")
    assert mod._voice_pack_allows_drift(proj) is False


def test_voice_pack_no_meta_no_exempt():
    """voice_pack 无 voice_meta → 不豁免。"""
    proj = _mk_project(genre="xianxia", voice_meta=None)
    assert mod._voice_pack_allows_drift(proj) is False


# ── ⑨ 与 R6 anachronism 显式去重 ────────────────────────────────────────
def test_coexists_with_anachronism_scanner():
    """同 cluster 草稿同时跑两个 scanner·issue code 各异·互不干扰。"""
    # 仙侠草稿带 现代俚语 + 古代场景出现汽车（同时构成两种问题）
    text = ("他驾鹤升仙，掏出手机拨号，KPI 拉满，复盘三日。" * 20 +
            "汽车驶过山门，电脑摆在丹房，老六真香。" * 25)
    bak1 = os.environ.get("REGISTER_DRIFT_MODE")
    bak2 = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        os.environ["ANACHRONISM_MODE"] = "active"
        proj = _mk_project(genre="xianxia")
        # 给 anachronism scanner 加 era 字段（独立门控）
        wv = proj / "_数据库" / "世界观.json"
        wv_obj = json.loads(wv.read_text(encoding="utf-8")) if wv.exists() else {}
        wv_obj["era"] = "仙侠"
        wv.write_text(json.dumps(wv_obj, ensure_ascii=False),
                      encoding="utf-8")
        draft_p = _write(text)
        # 跑 register drift
        r1 = mod.scan(draft_p, project_root=proj)
        # 跑 anachronism
        import anachronism_scanner as ana
        r2 = ana.scan(draft_p, project_root=proj)
        # 互不干扰：两个都能独立报
        assert r1["code"] == "REGISTER_DRIFT"
        assert r2["code"] == "ANACHRONISM_DETECTED"
        # 至少 register drift 命中
        assert r1.get("drift_count", 0) > 0
        # anachronism 命中现代物件
        assert r2.get("anachronism_count", 0) > 0
    finally:
        os.environ["ANACHRONISM_MODE"] = bak2 if bak2 else "shadow"
        if bak2 is None:
            os.environ.pop("ANACHRONISM_MODE", None)
        _set_mode(bak1)


# ── ⑪ CLI subprocess 退出码 ────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path),
         "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "REGISTER_DRIFT_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(genre="xianxia")
    p = _write(_FORWARD_DRIFT_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None and rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_clean():
    proj = _mk_project(genre="xianxia")
    p = _write(_CLEAN_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None and rep["verdict"] == "PASS"
