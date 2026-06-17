# -*- coding: utf-8 -*-
"""dramatic_irony_scanner.py 专属回归测试（2026-06-17·确定性·零依赖·零 LLM/零联网）。

聚焦【现有 test_dramatic_irony.py / test_tell_scanners_goldstandard.py 未覆盖】的纯逻辑分支：
  - 辅助函数：_strip_changes（CHANGES 分隔符切割）/ _cjk_count（CJK 边界计数）；
  - _author_reader_adv：作者档 reader_advantage_pct 读取的全部 missing/error/嵌套缺失分支；
  - _mode：非法值回落 active + 显式 shadow；
  - scan 边界：cjk<500 跳过 / 草稿读取失败 note / shadow 模式只记不判 / per_1k 与 sample_markers 计算；
  - main() CLI：sys.exit 退出码（warning→1 否则 0）走 subprocess 跑真 argparse。

现有两文件已锁 detect 基本抓词、active 高密度上报、低密度 PASS、IRONY_TELL_FLOOR 数值、
off 跳过、gate_level 恒 advisory、真作者零误报、默认 active —— 本文件不重复，只补上述空白。
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
import dramatic_irony_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "dramatic_irony_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("DRAMATIC_IRONY_MODE", None)
    else:
        os.environ["DRAMATIC_IRONY_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(reader_adv=None, profile_obj="__default__"):
    """造带 _数据库/作者风格.json 的项目目录。
    profile_obj 给原始对象覆盖整个 JSON（测嵌套缺失/类型异常）；否则按 reader_adv 装配标准结构。
    """
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if profile_obj != "__default__":
        obj = profile_obj
    elif reader_adv is None:
        obj = {"knowledge_gap_profile": {}}
    else:
        obj = {"knowledge_gap_profile": {"reader_advantage_pct": reader_adv}}
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# ── _strip_changes：CHANGES 分隔符切割 ────────────────────────────────────────
def test_strip_changes_cuts_at_factual_separator():
    """---CHANGES_FACTUAL--- 之后的内容被切掉，只留正文（且 rstrip 尾部空白）。"""
    raw = "正文内容在这里。\n\n---CHANGES_FACTUAL---\n{\"foo\": 1}"
    assert mod._strip_changes(raw) == "正文内容在这里。"


def test_strip_changes_cuts_at_plain_separator():
    """---CHANGES--- 同样被识别并切割。"""
    raw = "另一段正文。   \n---CHANGES---\nchangelog"
    assert mod._strip_changes(raw) == "另一段正文。"


def test_strip_changes_no_separator_returns_unchanged():
    """无分隔符时原样返回（不误删正文）。"""
    raw = "纯正文没有任何分隔符。"
    assert mod._strip_changes(raw) == raw


# ── _cjk_count：CJK 边界计数 ──────────────────────────────────────────────────
def test_cjk_count_only_counts_cjk():
    """只数中日韩统一表意区字符·ASCII/数字/标点不计入。"""
    # 5 个 CJK 汉字 + 英文 + 数字 + 全角标点（。在 U+3002 不在 一..鿿 区间，不计）
    assert mod._cjk_count("你好世界中abc123，。") == 5


def test_cjk_count_empty_is_zero():
    assert mod._cjk_count("") == 0


# ── _author_reader_adv：作者档读取的全部分支 ─────────────────────────────────
def test_author_reader_adv_none_project():
    assert mod._author_reader_adv(None) is None


def test_author_reader_adv_file_missing():
    """项目存在但无 作者风格.json → None（不抛）。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert mod._author_reader_adv(proj) is None


def test_author_reader_adv_reads_value():
    proj = _mk_project(reader_adv=0.42)
    assert mod._author_reader_adv(proj) == 0.42


def test_author_reader_adv_missing_nested_key():
    """有 knowledge_gap_profile 但无 reader_advantage_pct → None。"""
    proj = _mk_project(reader_adv=None)  # kg={} 空字典
    assert mod._author_reader_adv(proj) is None


def test_author_reader_adv_kg_not_dict():
    """knowledge_gap_profile 不是 dict（如 None/列表）→ None 不抛。"""
    proj = _mk_project(profile_obj={"knowledge_gap_profile": None})
    assert mod._author_reader_adv(proj) is None
    proj2 = _mk_project(profile_obj={"knowledge_gap_profile": [1, 2]})
    assert mod._author_reader_adv(proj2) is None


def test_author_reader_adv_top_not_dict():
    """整个 JSON 不是 dict（如列表）→ None（isinstance prof 守卫）。"""
    proj = _mk_project(profile_obj=[1, 2, 3])
    assert mod._author_reader_adv(proj) is None


def test_author_reader_adv_bad_json():
    """坏 JSON → JSONDecodeError 被捕获 → None。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{ not valid json", encoding="utf-8")
    assert mod._author_reader_adv(proj) is None


# ── _mode：非法值回落 + 显式状态 ─────────────────────────────────────────────
def test_mode_invalid_falls_back_active():
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("bogus_mode")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_explicit_shadow_and_off():
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("shadow")
        assert mod._mode() == "shadow"
        _set_mode("OFF")  # 大小写不敏感
        assert mod._mode() == "off"
    finally:
        _set_mode(bak)


# ── scan 边界分支 ────────────────────────────────────────────────────────────
def test_scan_short_draft_skipped():
    """cjk<500 → note 跳过·不算 per_1k·verdict 仍 PASS。"""
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("active")
        # 即便堆满 tell 词，正文太短也必须跳过（边界守护）
        p = _write("他殊不知。" * 10)  # 远不足 500 CJK
        out = mod.scan(p)
        assert out["note"] == "草稿太短·跳过"
        assert "per_1k" not in out
        assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_scan_read_failure_returns_note():
    """草稿路径不存在（非 off 模式）→ OSError 捕获 → note·不抛。"""
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "does_not_exist.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_scan_shadow_records_but_no_violation():
    """shadow 模式：高密度 tell 也只记不判（verdict PASS·violations 空·零回归）。"""
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("shadow")
        draft = "他殊不知危险。她浑然不知。他做梦也没想到真相。" * 25
        p = _write(draft)
        out = mod.scan(p)
        assert out["mode"] == "shadow"
        assert out["per_1k"] > mod.IRONY_TELL_FLOOR   # 确实超标
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None                  # shadow 不上报
    finally:
        _set_mode(bak)


def test_scan_per_1k_and_sample_markers_truncated():
    """per_1k = hits/(cjk/1000) 计算正确·sample_markers 截到前 8 个。"""
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("active")
        # 构造 >8 个 hit 且正文够长（>500 CJK）
        draft = "殊不知浑然不知做梦也没想到并不知道蒙在鼓里岂知焉知哪里知道不曾想" * 30
        p = _write(draft)
        out = mod.scan(p)
        cjk = mod._cjk_count(draft)
        expected = round(out["dramatic_irony_count"] / (cjk / 1000.0), 2)
        assert out["per_1k"] == expected
        assert len(out["sample_markers"]) == 8        # 截断守护
        assert out["dramatic_irony_count"] >= 8
    finally:
        _set_mode(bak)


def test_scan_active_attaches_reader_adv_from_project():
    """active 上报时 violation 携带作者档 reader_adv（双向对账信息穿透）。"""
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(reader_adv=0.55)
        draft = "他殊不知危险。她浑然不知。众人并不知道真相。" * 30  # >500 CJK 越过短稿门槛
        p = _write(draft)
        out = mod.scan(p, project_root=proj)
        assert out["author_reader_advantage_pct"] == 0.55
        assert out["violations"] and out["violations"][0]["author_reader_adv"] == 0.55
    finally:
        _set_mode(bak)


# ── main() CLI：sys.exit 退出码（走真 subprocess） ───────────────────────────
def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "DRAMATIC_IRONY_MODE": "active", "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    """高密度 tell → main 打印 JSON 报告 + 退出码 1（warning 非空）。"""
    draft = "他殊不知危险。她浑然不知。他做梦也没想到。众人并不知道真相。" * 25
    p = _write(draft)
    r = _run_cli(str(p))
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None and rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_clean_draft():
    """展示式干净草稿 → 退出码 0·PASS。"""
    draft = ("他推开门，桌上的信封压着一枚铜钥匙。他没回头，影子在门后停住。"
             "她笑着接过茶，指尖在杯沿停了一瞬。") * 25
    p = _write(draft)
    r = _run_cli(str(p))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None and rep["verdict"] == "PASS"
