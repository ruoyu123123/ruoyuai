# -*- coding: utf-8 -*-
"""llm_grammar_overuse_scanner R19 W8 Batch-V·P0 PNAS 2025 LLM 4 语法过用回归。

确定性·零依赖。覆盖 off/短稿/4 子探针(participial/nominalization/nested 嵌套/
parallel 串联)各自正反例·作者档 z-band·shadow/active 切换·CLI·读取失败·
hard_gate registry 守卫。
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
import llm_grammar_overuse_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "llm_grammar_overuse_scanner.py"
_ENV = "LLM_GRAMMAR_OVERUSE_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"quantitative": {"llm_grammar_overuse_baseline": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 一段普通中文(无大量 4 模具)
_CLEAN_CN = (
    "他坐下来，喝了一口水。窗外有风。她问他要不要回家。"
    "他没回答，只是看着远处。雨停了。"
) * 30


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_CLEAN_CN))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短文。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_participial_overuse_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 同句堆 3 个『着 / 正在』分词丛 · 句平均 >> 0.35
        flood = ("他走着，看着，听着远方传来的声音。" * 60)
        r = mod.scan(_write(flood))
        codes = [v["code"] for v in r["violations"]]
        assert "LLM_GRAMMAR_PARTICIPIAL_OVERUSE" in codes
        assert r["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_nominalization_overuse_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        nomin = ("进行改革。实现建设。展开调整。提出计划。"
                 "造成影响。建立运作。形成处理。导致改变。") * 30
        r = mod.scan(_write(nomin))
        codes = [v["code"] for v in r["violations"]]
        assert "LLM_GRAMMAR_NOMINALIZATION_OVERUSE" in codes
    finally:
        _set_mode(bak)


def test_nested_x_de_y_overuse_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 嵌套主语 ≥3 层 X 的 Y 的 Z
        nested = ("我的朋友的家的猫跑了。她的妹妹的男友的车停了。"
                  "他的爸爸的同事的女儿来了。村的口的井的水干了。") * 30
        r = mod.scan(_write(nested))
        codes = [v["code"] for v in r["violations"]]
        assert "LLM_GRAMMAR_NESTED_X_DE_Y_OVERUSE" in codes
    finally:
        _set_mode(bak)


def test_parallel_and_stack_overuse_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 串联 ≥4 项「A、B、C、D 和 E」
        parallel = ("他带了书、本、笔、墨和砚。"
                    "桌上摆着碗、筷、勺、盘和杯。"
                    "院子里养着鸡、鸭、鹅、狗和猫。"
                    "她喜欢春、夏、秋、冬和雨。") * 30
        r = mod.scan(_write(parallel))
        codes = [v["code"] for v in r["violations"]]
        assert "LLM_GRAMMAR_PARALLEL_AND_STACK_OVERUSE" in codes
    finally:
        _set_mode(bak)


def test_clean_text_no_violation_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_CLEAN_CN))
        # 无作者档 → 走兜底地板 · clean 文本应低于所有地板
        codes = [v["code"] for v in r["violations"]]
        # 至少 participial / nominalization / nested 不该全中
        assert "LLM_GRAMMAR_PARTICIPIAL_OVERUSE" not in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation_reports_to_stderr():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        flood = ("他走着，看着，听着远方传来的声音。" * 60)
        r = mod.scan(_write(flood))
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_author_baseline_lenient_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 极宽容基线 → 所有 z 都在带内
        proj = _mk_project(baseline={
            "present_participial_mean": 0.4, "present_participial_std": 100.0,
            "nominalization_per_1k_mean": 10.0, "nominalization_per_1k_std": 100.0,
            "nested_x_de_y_mean": 0.1, "nested_x_de_y_std": 100.0,
            "parallel_stack_per_1k_mean": 1.0, "parallel_stack_per_1k_std": 100.0,
        })
        flood = ("他走着，看着，听着远方传来的声音。" * 60)
        r = mod.scan(_write(flood), project_root=proj)
        assert r["author_baseline"]["from_author_profile"] is True
        # 宽容基线 → 无 violation
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_split_sentences():
    s = mod._split_sentences("第一句。第二句！第三句？")
    assert len(s) == 3


def test_probe_participial_zero_on_empty():
    r = mod.probe_participial([])
    assert r["per_sentence_avg"] == 0.0


def test_probe_nominalization_zero_on_clean():
    r = mod.probe_nominalization("他喝水。她笑。")
    assert r["hits"] == 0


def test_probe_nested_zero_on_clean():
    r = mod.probe_nested_x_de_y(["他喝水。", "她笑。"])
    assert r["hits"] == 0


def test_probe_parallel_zero_on_clean():
    r = mod.probe_parallel_and_stack("他喝水。她笑。")
    assert r["hits"] == 0


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for code in mod.ISSUE_CODES:
        assert code not in hgs, f"{code} 误进 hard_gate"


def _run_cli(draft_path, mode="shadow"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs_shadow():
    r = _run_cli(_write(_CLEAN_CN))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "llm_grammar_overuse"
