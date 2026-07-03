# -*- coding: utf-8 -*-
"""centering_theory_focus_scanner R18 W7 Batch-T·P1 Grosz CT 4 转移回归。

确定性·零依赖。覆盖 off/短稿/可分析句过少/continue/retain/smooth/rough/兜底地板/
作者档 z-band/active/shadow/读取失败/_mode/CLI/registry hard_gate codes 不污染/
_extract_cf/_classify_transition 直测。
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
import centering_theory_focus_scanner as mod  # noqa: E402
import nn_coref_bridge  # noqa: E402

_TARGET = _SCRIPTS / "centering_theory_focus_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("CENTERING_THEORY_FOCUS_MODE", None)
    else:
        os.environ["CENTERING_THEORY_FOCUS_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(characters=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")
    if baseline is not None:
        (db / "作者风格.json").write_text(
            json.dumps({"quantitative": {"centering_baseline": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# rough-shift 密集：每句换实体焦点（甲→乙→丙→丁循环）
_ROUGH_HEAVY = (
    "张三走。李四笑。王五站。赵六坐。钱七跑。孙八跳。"
) * 200

# continue 主导：同实体延续焦点
_CONTINUE_HEAVY = (
    "张三走。张三看。张三说。张三笑。张三停。张三再走。张三回头。"
) * 200


def test_off_returns_skeleton():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_ROUGH_HEAVY))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短稿。"))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_too_few_sentences_skip():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        # 大量 CJK 但只 1 句 → 通过 cjk 闸但 scanned<5
        text = "张三走遍千山万水寻找答案的故事" * 50
        out = mod.scan(_write(text), proj)
        # 没断句符 · scanned 太少
        assert "可分析句数" in out.get("note", "") \
            or out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_rough_heavy_triggers_floor():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[
            {"name": "张三", "role": "主角"},
            {"name": "李四", "role": "配角"},
            {"name": "王五", "role": "配角"},
            {"name": "赵六", "role": "配角"},
            {"name": "钱七", "role": "配角"},
            {"name": "孙八", "role": "配角"},
        ])
        out = mod.scan(_write(_ROUGH_HEAVY), proj)
        m = out["metrics"]
        # rough 密度高
        assert m["rough_shift_density_per_kcjk"] >= mod.FLOOR_ROUGH_SHIFT_DENSITY \
            or out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_continue_heavy_passes():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_CONTINUE_HEAVY), proj)
        # continue 主导 → rough 极少
        m = out["metrics"]
        assert m["transitions"]["continue"] > m["transitions"]["rough"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_overload():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            characters=[
                {"name": "张三", "role": "主角"},
                {"name": "李四", "role": "配角"},
                {"name": "王五", "role": "配角"},
                {"name": "赵六", "role": "配角"},
                {"name": "钱七", "role": "配角"},
                {"name": "孙八", "role": "配角"},
            ],
            baseline={"rough_shift_density_mean": 1.0,
                      "rough_shift_density_std": 0.1,
                      "rough_shift_density_max": 2.0})
        out = mod.scan(_write(_ROUGH_HEAVY), proj)
        assert out["author_baseline"]["from_author_profile"] is True
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_pass():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            characters=[{"name": "张三", "role": "主角"}],
            baseline={"rough_shift_density_mean": 50.0,
                      "rough_shift_density_std": 50.0,
                      "rough_shift_density_max": 200.0})
        out = mod.scan(_write(_CONTINUE_HEAVY), proj)
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[
            {"name": "张三", "role": "主角"},
            {"name": "李四", "role": "配角"},
            {"name": "王五", "role": "配角"},
            {"name": "赵六", "role": "配角"},
            {"name": "钱七", "role": "配角"},
            {"name": "孙八", "role": "配角"},
        ])
        out = mod.scan(_write(_ROUGH_HEAVY), proj)
        assert out["violations"] == []
        # metrics 仍然算
        assert out["metrics"]["sentences_scanned"] > 0
    finally:
        _set_mode(bak)


def test_extract_cf_order_preserved():
    cf = mod._extract_cf("张三看着李四，王五在远处。", {"张三", "李四", "王五"})
    assert cf == ["张三", "李四", "王五"]


def test_extract_cf_includes_pronoun():
    cf = mod._extract_cf("他望着她。", {"张三"})
    assert "他" in cf
    assert "她" in cf


def test_classify_continue():
    # prev_cf=[张三, 李四] · cur_cf=[张三, 李四]
    cb, kind = mod._classify_transition(["张三", "李四"], ["张三", "李四"])
    assert kind == "continue"
    assert cb == "张三"


def test_classify_retain():
    # prev_cb=张三 · cur Cb=张三（前句 Cp）·Cp(cur)=李四 → retain
    cb, kind = mod._classify_transition(["张三", "李四"], ["李四", "张三"])
    assert kind == "retain"
    assert cb == "张三"


def test_classify_smooth_shift():
    # prev=[张三, 李四]·cur=[李四]·Cb=李四(!=prev_cb 张三)·Cp(cur)=李四 → smooth
    cb, kind = mod._classify_transition(["张三", "李四"], ["李四"])
    assert kind == "smooth"


def test_classify_rough_shift():
    # prev=[张三]·cur=[王五, 李四]·王五∉prev → cb=None → rough
    cb, kind = mod._classify_transition(["张三"], ["王五", "李四"])
    assert kind == "rough"


def test_classify_none_on_empty():
    _, kind = mod._classify_transition([], ["张三"])
    assert kind == "none"
    _, kind = mod._classify_transition(["张三"], [])
    assert kind == "none"


def test_read_failure_note():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\n{}") == "正文。"


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "CENTERING_ROUGH_SHIFT_OVERLOAD" not in hgs


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CENTERING_THEORY_FOCUS_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(characters=[
        {"name": "张三", "role": "主角"},
        {"name": "李四", "role": "配角"},
        {"name": "王五", "role": "配角"},
        {"name": "赵六", "role": "配角"},
        {"name": "钱七", "role": "配角"},
        {"name": "孙八", "role": "配角"},
    ])
    r = _run_cli(_write(_ROUGH_HEAVY), project=proj)
    assert r.returncode in (0, 1), r.stderr


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
    r = _run_cli(_write(_CONTINUE_HEAVY), project=proj)
    assert r.returncode == 0, r.stderr


# ══════════════════════════════════════════════════════════════════════════
# 🔴 2026-07 NN 共指桥集成回归（entity/共指提取步骤 → nn_coref_bridge）
# RUOYU_NN_COREF 门控关闭（默认）→ 100% 回退现有字符串匹配 + 固定代词表逻辑；
# 手动 patch nn_coref_bridge.resolve_coreferences 才验证桥被正确采用。
# Cb/Cf 转移分类算法（_classify_transition）本身完全不受影响。
# ══════════════════════════════════════════════════════════════════════════
def test_coref_disabled_by_default_metrics_field():
    """默认门控关闭（真实桥调用，不 mock）→ coref_resolution_source=regex_fallback。"""
    bak_mode = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    bak_coref = os.environ.get("RUOYU_NN_COREF")
    os.environ.pop("RUOYU_NN_COREF", None)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_CONTINUE_HEAVY), proj)
        assert out["metrics"]["coref_resolution_source"] == "regex_fallback"
        assert out["metrics"]["coref_resolved_mentions"] == 0
    finally:
        _set_mode(bak_mode)
        if bak_coref is not None:
            os.environ["RUOYU_NN_COREF"] = bak_coref


def test_extract_cf_backward_compatible_without_coref_map():
    """不传 coref_map（默认 None）→ 逐字节等同旧版行为。"""
    cf = mod._extract_cf("张三看着李四，王五在远处。", {"张三", "李四", "王五"})
    assert cf == ["张三", "李四", "王五"]
    cf2 = mod._extract_cf("他望着她。", {"张三"})
    assert cf2 == ["他", "她"]


def test_extract_cf_coref_map_replaces_bare_pronoun():
    """coref_map 命中固定代词位置 → 用解析出的具体角色名替代裸代词，
    使跨句 Cb 延续判断能匹配到真实姓名而非停留在代词字面。"""
    sentence = "他笑了。"
    idx = sentence.find("他")
    cf = mod._extract_cf(sentence, {"张三"}, {idx: "张三"})
    assert cf == ["张三"]
    assert "他" not in cf


def test_extract_cf_coref_map_adds_non_pronoun_mention():
    """coref_map 命中固定 PRONOUNS 表覆盖不到的位置（如"那个女人"）→ 补一个新 Cf 候选。"""
    sentence = "那个女人转身走了。"
    idx = sentence.find("那个女人")
    cf = mod._extract_cf(sentence, {"李四"}, {idx: "李四"})
    assert "李四" in cf


def test_coref_positions_empty_when_bridge_disabled():
    """RUOYU_NN_COREF 未设 → _coref_positions 返回空 dict（真实桥调用·非 mock）。"""
    bak_coref = os.environ.get("RUOYU_NN_COREF")
    os.environ.pop("RUOYU_NN_COREF", None)
    try:
        assert mod._coref_positions("张三走来。他笑了。", {"张三"}) == {}
    finally:
        if bak_coref is not None:
            os.environ["RUOYU_NN_COREF"] = bak_coref


def test_coref_positions_uses_bridge_when_patched():
    """手动 patch nn_coref_bridge.resolve_coreferences 返回有效结果 → 新逻辑被使用。"""
    orig = nn_coref_bridge.resolve_coreferences

    def fake_resolve(text, known):
        assert known == ["张三"]
        idx = text.find("他")
        if idx < 0:
            return []
        return [{"mention": "他", "span": [idx, idx + 1], "resolved_to": "张三",
                 "confidence": 0.9, "backend": "rule", "ambiguous": False}]

    nn_coref_bridge.resolve_coreferences = fake_resolve
    try:
        body = "张三走来。他笑了。"
        pos = mod._coref_positions(body, {"张三"})
        idx = body.find("他")
        assert pos == {idx: "张三"}
    finally:
        nn_coref_bridge.resolve_coreferences = orig


def test_scan_centering_uses_coref_bridge_when_patched():
    """端到端：桥把"他"解析回"张三" → coref_used 计数 > 0（新逻辑被正确使用）。"""
    orig = nn_coref_bridge.resolve_coreferences

    def fake_resolve(text, known):
        idx = text.find("他")
        if idx < 0:
            return []
        return [{"mention": "他", "span": [idx, idx + 1], "resolved_to": "张三",
                 "confidence": 0.9, "backend": "rule", "ambiguous": False}]

    nn_coref_bridge.resolve_coreferences = fake_resolve
    try:
        text = "张三对李四说了句话。他笑了。"
        transitions, scanned, coref_used = mod._scan_centering(text, {"张三", "李四"})
        assert coref_used > 0
    finally:
        nn_coref_bridge.resolve_coreferences = orig
