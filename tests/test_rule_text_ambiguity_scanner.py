# -*- coding: utf-8 -*-
"""rule_text_ambiguity_scanner.py 专属回归测试 (R7 W2·2026-06-20)。

零依赖·确定性·零 LLM/零联网。覆盖:
  ① rule_anomaly genre + 全直陈规则块 + active → FAIL_MINOR (ratio<0.30)
  ② rule_anomaly genre + 半真半假规则块 (歧义+陷阱混合) + active → PASS
  ③ 非 rule_anomaly genre → skip
  ④ 无 genre → skip
  ⑤ shadow 模式即使触发也只记不判
  ⑥ off → 骨架
  ⑦ 短稿 / 读取失败 / _strip_changes / _cjk_count / _resolve_genre / main CLI
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
import rule_text_ambiguity_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "rule_text_ambiguity_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("RULE_TEXT_AMBIGUITY_MODE", None)
    else:
        os.environ["RULE_TEXT_AMBIGUITY_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(genre=None, *, from_user_pref=False):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if genre:
        if from_user_pref:
            (proj / "_数据库" / "用户偏好.json").write_text(
                json.dumps({"genre": genre}, ensure_ascii=False), encoding="utf-8")
        else:
            (proj / "_数据库" / "作者风格.json").write_text(
                json.dumps({"genre_tags": [genre]}, ensure_ascii=False), encoding="utf-8")
    return proj


# 全直陈规则块草稿(条款全是直接陈述·零歧义零陷阱标志)·>500 CJK
_DIRECT_RULES_BODY = """
我推开门走进昏暗的房间，墙上贴着一张泛黄的纸。我走近一看，纸上写着：

规则：
1. 早上六点起床做操。
2. 八点准时吃早餐。
3. 中午十二点回到自己的房间。
4. 下午三点参加集体活动。
5. 晚上九点熄灯睡觉。
6. 周日上午打扫卫生。
7. 周三下午观看电影。

我把这些条款看了一遍，心里大致有了底。
""" + ("这是一段填充的正文内容。" * 50)

# 半真半假规则块(每条都有歧义或陷阱信号)
_HALFTRUE_RULES_BODY = """
我推开门走进昏暗的房间，墙上贴着一张泛黄的纸。我走近一看，纸上写着：

注意事项：
1. 早上六点必须起床，否则会被记为缺勤。
2. 据说八点吃早餐最安全，但也许不绝对。
3. 中午十二点切勿走出走廊，否则便会出事。
4. 下午三点或许有人敲门，不可应答。
5. 若听见歌声则必须捂住耳朵。
6. 晚上九点不得开灯，可能会引来不该来的东西。
7. 千万不要相信镜子里的人，否则将会消失。

我看完这些条款，背上一阵发凉。
""" + ("我反复揣摩这些条款的真意。" * 50)


# ── ⑥ off → 骨架 ────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(genre="rule_anomaly")
        out = mod.scan(_write(_DIRECT_RULES_BODY), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "rule_items_total" not in out
    finally:
        _set_mode(bak)


# ── ① rule_anomaly + 全直陈 + active → FAIL_MINOR ────────────────────────────
def test_active_rule_anomaly_direct_rules_fail_minor():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="rule_anomaly")
        out = mod.scan(_write(_DIRECT_RULES_BODY), project_root=proj)
        assert out["genre"] == "rule_anomaly"
        assert out["rule_items_total"] >= 5
        assert out["ambiguity_ratio"] < 0.30
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"] and out["violations"][0]["severity"] == "minor"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ── ② rule_anomaly + 半真半假 + active → PASS ───────────────────────────────
def test_active_rule_anomaly_halftrue_pass():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="rule_anomaly")
        out = mod.scan(_write(_HALFTRUE_RULES_BODY), project_root=proj)
        assert out["genre"] == "rule_anomaly"
        assert out["rule_items_total"] >= 5
        assert out["ambiguity_ratio"] >= 0.30  # 大部分条款带歧义/陷阱
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── ③ 非 rule_anomaly genre → skip ──────────────────────────────────────────
def test_non_rule_anomaly_genre_skips():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("active")
        for g in ["xuanhuan", "romance", "scheming_politics", "unknown"]:
            proj = _mk_project(genre=g)
            out = mod.scan(_write(_DIRECT_RULES_BODY), project_root=proj)
            assert "非 rule_anomaly" in out.get("note", ""), g
            assert out["verdict"] == "PASS"
            assert "rule_items_total" not in out
    finally:
        _set_mode(bak)


# ── ④ 无 genre → skip ───────────────────────────────────────────────────────
def test_no_genre_skips():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre=None)
        out = mod.scan(_write(_DIRECT_RULES_BODY), project_root=proj)
        assert out["genre"] is None
        assert "非 rule_anomaly" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_project_skips():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DIRECT_RULES_BODY), project_root=None)
        assert out["genre"] is None
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ⑤ shadow 模式即使触发也只记不判 ────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(genre="rule_anomaly")
        out = mod.scan(_write(_DIRECT_RULES_BODY), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["rule_items_total"] >= 5
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 ───────────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="rule_anomaly")
        out = mod.scan(_write("规则：1. 短。"), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 用户偏好.json 作为 genre 来源 ────────────────────────────────────────────
def test_genre_from_user_pref_json():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="rule_anomaly", from_user_pref=True)
        out = mod.scan(_write(_DIRECT_RULES_BODY), project_root=proj)
        assert out["genre"] == "rule_anomaly"
        # 直陈规则 → 应触发
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


# ── 规则块识别:无块头但有连续编号项也算 ──────────────────────────────────────
def test_extract_numbered_items_without_header():
    text = """
开头一段散文。

1. 第一条规则。
2. 第二条规则。
3. 第三条规则。

后续散文。
"""
    items = mod._extract_rule_items(text)
    assert len(items) == 3


def test_extract_lone_number_not_rule_block():
    """单独一个编号项(非连续)不当规则块。"""
    text = "散文中提到 1. 一个点。然后继续散文。\n\n散文段。"
    items = mod._extract_rule_items(text)
    assert items == []


def test_extract_with_header_picks_all_numbered():
    """有块头即使只 1 条编号项也被识别(块头开启上下文)。"""
    text = "守则：\n1. 单条规则。\n\n其他散文。"
    items = mod._extract_rule_items(text)
    assert len(items) == 1


def test_extract_chinese_numerals():
    text = "条款：\n一、第一条。\n二、第二条。\n三、第三条。"
    items = mod._extract_rule_items(text)
    assert len(items) == 3


# ── _classify_item ─────────────────────────────────────────────────────────
def test_classify_pure_direct():
    c = mod._classify_item("早上六点起床。")
    assert not c["has_ambiguity"] and not c["has_trap"] and not c["is_half_true"]


def test_classify_ambiguity():
    c = mod._classify_item("或许早上六点起床。")
    assert c["has_ambiguity"] and c["is_half_true"]


def test_classify_trap():
    c = mod._classify_item("不可在中午外出，否则便会失踪。")
    assert c["has_trap"] and c["is_half_true"]


# ── _strip_changes / _cjk_count ─────────────────────────────────────────────
def test_strip_changes_separator():
    raw = "正文。\n---CHANGES---\n{}"
    assert mod._strip_changes(raw) == "正文。"


def test_cjk_count_only_cjk():
    assert mod._cjk_count("你好abc123，世界") == 4  # 你/好/世/界=4


# ── _mode 非法回落 ─────────────────────────────────────────────────────────
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("RULE_TEXT_AMBIGUITY_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── _resolve_genre 全分支 ─────────────────────────────────────────────────
def test_resolve_genre_none_project():
    assert mod._resolve_genre(None) is None


def test_resolve_genre_from_author_profile_first():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"genre_tags": ["rule_anomaly", "horror"]}, ensure_ascii=False),
        encoding="utf-8")
    assert mod._resolve_genre(proj) == "rule_anomaly"


def test_resolve_genre_bad_json_falls_through():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text("{ bad json", encoding="utf-8")
    (proj / "_数据库" / "用户偏好.json").write_text(
        json.dumps({"genre": "rule_anomaly"}, ensure_ascii=False), encoding="utf-8")
    assert mod._resolve_genre(proj) == "rule_anomaly"


# ── main() CLI subprocess 退出码 ───────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "RULE_TEXT_AMBIGUITY_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(genre="rule_anomaly")
    p = _write(_DIRECT_RULES_BODY)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None and rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_on_halftrue():
    proj = _mk_project(genre="rule_anomaly")
    p = _write(_HALFTRUE_RULES_BODY)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None and rep["verdict"] == "PASS"


# ── genre 包契约校验 (rule_anomaly 已注册) ──────────────────────────────────
def test_genre_pack_registered():
    packs_file = _ROOT / "core" / "claude-home" / "templates" / "genre_dimension_packs.json"
    d = json.loads(packs_file.read_text(encoding="utf-8"))
    assert "rule_anomaly" in d["_canonical_genres"]
    pack = d["packs"]["rule_anomaly"]
    assert isinstance(pack["judge_dims"], dict) and len(pack["judge_dims"]) == 5
    assert pack["scanner"] == "rule_text_ambiguity_scanner"
    assert isinstance(pack["writer_directives"], list) and len(pack["writer_directives"]) >= 4
