# -*- coding: utf-8 -*-
"""resource_ledger_scanner.py 专属回归测试 (R7 W2·2026-06-20)。

零依赖·确定性·零 LLM/零联网。覆盖:
  ① apocalypse_survival + 资源决策无账本 + active → FAIL_MINOR
  ② apocalypse_survival + 资源账本扎实 + active → PASS
  ③ 非 apocalypse_survival genre → skip
  ④ 无 genre → skip
  ⑤ shadow 模式即使触发也只记不判
  ⑥ off → 骨架
  ⑦ _count_ledger_anchors / _resolve_genre / main CLI
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
import resource_ledger_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "resource_ledger_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("RESOURCE_LEDGER_MODE", None)
    else:
        os.environ["RESOURCE_LEDGER_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(genre=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if genre:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"genre_tags": [genre]}, ensure_ascii=False), encoding="utf-8")
    return proj


# 末世场景但完全没有账本式描写(决策频繁·资源词稀少·没有余额修饰词靠近)
_NO_LEDGER_BODY = (
    "他举起武器开火，扣动扳机，飞快地射击。再开火，扣动扳机。"
    "前方有变异体冲来，他射击射出，开火。装弹后继续射击，再扣动扳机。"
    "他吃了，喝了。又吃了，又喝了。继续开火。"
    "他启动车辆，驾驶。又一波敌人涌来，他射击射击射击。"
    "硝烟里他穿戴整齐，包扎好伤口，注射药剂。继续射击开火。"
) * 25

# 末世场景 + 账本扎实(资源词紧贴余额修饰词·决策前后给数字)
_GOOD_LEDGER_BODY = (
    "他举起武器，弹匣里只剩三发子弹。他扣动扳机射出一发，眼前一空。剩两发。"
    "水壶见底，最后一口水滑过喉咙。罐头还剩两包压缩饼干。"
    "他咬牙开火，又一发射出，弹药耗尽。装弹换上最后一个弹匣，电池电量见底。"
    "汽油不足半箱，他启动车辆继续前进。剩四发霰弹，半瓶净水。"
    "急救包还有两份绷带，抗生素用完了。他喝了最后半口水，吃了一包饼干。"
) * 25


# ── ⑥ off → 骨架 ────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(genre="apocalypse_survival")
        out = mod.scan(_write(_NO_LEDGER_BODY), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "decision_count" not in out
    finally:
        _set_mode(bak)


# ── ① apocalypse_survival + 无账本 + active → FAIL_MINOR ───────────────────
def test_active_apoc_no_ledger_fail_minor():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="apocalypse_survival")
        out = mod.scan(_write(_NO_LEDGER_BODY), project_root=proj)
        assert out["genre"] == "apocalypse_survival"
        assert out["decision_count"] >= 3
        assert out["ledger_anchor_density_per_1k"] < 0.5
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ── ② apocalypse_survival + 账本扎实 → PASS ────────────────────────────────
def test_active_apoc_good_ledger_pass():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="apocalypse_survival")
        out = mod.scan(_write(_GOOD_LEDGER_BODY), project_root=proj)
        assert out["genre"] == "apocalypse_survival"
        assert out["ledger_anchor_density_per_1k"] >= 0.5
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── ③ 非 apocalypse_survival genre → skip ──────────────────────────────────
def test_non_apoc_genre_skips():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("active")
        for g in ["xuanhuan", "romance", "rule_anomaly", "unknown"]:
            proj = _mk_project(genre=g)
            out = mod.scan(_write(_NO_LEDGER_BODY), project_root=proj)
            assert "非 apocalypse_survival" in out.get("note", ""), g
            assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ④ 无 genre → skip ───────────────────────────────────────────────────────
def test_no_genre_skips():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre=None)
        out = mod.scan(_write(_NO_LEDGER_BODY), project_root=proj)
        assert out["genre"] is None
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑤ shadow 模式即使触发也只记不判 ────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(genre="apocalypse_survival")
        out = mod.scan(_write(_NO_LEDGER_BODY), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["decision_count"] >= 3
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 ───────────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="apocalypse_survival")
        out = mod.scan(_write("开火。" * 5), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 决策不足时即使账本薄也不报 ─────────────────────────────────────────────
def test_low_decision_no_alert():
    """资源词稀疏 + 决策动作 < 3 → 不报(可能是序章/心理戏 cluster)。"""
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="apocalypse_survival")
        # 长草稿但完全没有决策动词(只有人物心理活动)
        body = "他躺在床上看着天花板。" * 100
        out = mod.scan(_write(body), project_root=proj)
        assert out["genre"] == "apocalypse_survival"
        assert out["decision_count"] < 3
        assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


# ── _count_ledger_anchors 单测 ─────────────────────────────────────────────
def test_count_ledger_anchors_close_pair():
    n = mod._count_ledger_anchors("弹匣里只剩三发子弹。")
    assert n >= 1


def test_count_ledger_anchors_no_pair():
    """资源词与修饰词相距远 (> max_gap) → 不算锚定。"""
    text = "子弹" + "充足" + ("无关内容" * 20) + "见底"
    n = mod._count_ledger_anchors(text, max_gap=8)
    assert n == 0


def test_count_ledger_anchors_zero_no_resources():
    assert mod._count_ledger_anchors("他看着天空。") == 0


# ── _strip_changes / _cjk_count / _mode ────────────────────────────────────
def test_strip_changes_separator():
    raw = "正文。\n---CHANGES_FACTUAL---\n{}"
    assert mod._strip_changes(raw) == "正文。"


def test_cjk_count_only_cjk():
    assert mod._cjk_count("末世abc123，求生") == 4


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("RESOURCE_LEDGER_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── _resolve_genre ─────────────────────────────────────────────────────────
def test_resolve_genre_none_project():
    assert mod._resolve_genre(None) is None


def test_resolve_genre_from_author_profile():
    proj = _mk_project(genre="apocalypse_survival")
    assert mod._resolve_genre(proj) == "apocalypse_survival"


# ── main() CLI ─────────────────────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "RESOURCE_LEDGER_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(genre="apocalypse_survival")
    p = _write(_NO_LEDGER_BODY)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None and rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_on_good_ledger():
    proj = _mk_project(genre="apocalypse_survival")
    p = _write(_GOOD_LEDGER_BODY)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None and rep["verdict"] == "PASS"


# ── genre 包契约校验 (apocalypse_survival 已注册) ─────────────────────────
def test_genre_pack_registered():
    packs_file = _ROOT / "core" / "claude-home" / "templates" / "genre_dimension_packs.json"
    d = json.loads(packs_file.read_text(encoding="utf-8"))
    assert "apocalypse_survival" in d["_canonical_genres"]
    pack = d["packs"]["apocalypse_survival"]
    assert isinstance(pack["judge_dims"], dict) and len(pack["judge_dims"]) == 5
    assert pack["scanner"] == "resource_ledger_scanner"
    assert isinstance(pack["writer_directives"], list) and len(pack["writer_directives"]) >= 4
