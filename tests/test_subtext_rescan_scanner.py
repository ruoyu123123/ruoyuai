# -*- coding: utf-8 -*-
"""subtext_rescan_scanner.py 聚焦回归测试（2026-06-17·确定性·零依赖）。

【与既有测试的分工】tests/test_subtext_rescan.py + tests/test_tell_scanners_goldstandard.py
已锁高层行为（基础抓取/去重/三态/金标准零误报/code 不进 HARD_GATE）。本文件**不重复**，
专钉尚未覆盖的内部 helper + 边界 + 退出码：

  1. `_strip_changes`：两种 CHANGES 分隔符截断正文（detect/scan 都先剥 changes）；
  2. `_cjk_count`：只数 CJK 不数标点/英文/数字；
  3. `_mode`：非法 env 值 / 大小写 / 空白 → 回退 active；off/shadow/active 透传；
  4. `detect_on_the_nose` 的 `_LEAD_WINDOW=8` 窗口边界（情绪名词刚好出窗 → 不抓）；
  5. `scan` <500 CJK 短稿 skip（出 note·不算密度）；
  6. `scan` 读不到文件 → 出 note 不崩；
  7. `scan` 恰好等于 floor（非严格 >）→ 不出 violation 的边界；
  8. `sample_hits` 截断到 6；输出 JSON 形状字段；
  9. `main()` CLI 退出码：active 超阈 → exit 1·否则 exit 0（subprocess 真跑）。
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
import subtext_rescan_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "subtext_rescan_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("SUBTEXT_RESCAN_MODE", None)
    else:
        os.environ["SUBTEXT_RESCAN_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# ── 1. _strip_changes ───────────────────────────────────────────────────────
def test_strip_changes_factual_separator():
    """---CHANGES_FACTUAL--- 之后整段被剥（含尾部 rstrip）。"""
    body = "正文一句。正文两句。"
    full = body + "\n---CHANGES_FACTUAL---\n{json garbage}"
    assert mod._strip_changes(full) == body


def test_strip_changes_plain_separator():
    """---CHANGES--- 同样被剥。"""
    body = "他走进房间。"
    assert mod._strip_changes(body + "---CHANGES---后面全删") == body


def test_strip_changes_no_separator_passthrough():
    """无分隔符 → 原样返回。"""
    txt = "没有任何分隔符的纯正文。"
    assert mod._strip_changes(txt) == txt


def test_detect_ignores_emotion_inside_changes_block():
    """情绪直陈出现在 CHANGES 块里 → detect 先剥 changes·不计入 hit。"""
    txt = "他推开门。\n---CHANGES_FACTUAL---\n他感到愤怒，心中充满了绝望。"
    assert mod.detect_on_the_nose(txt) == []


# ── 2. _cjk_count ───────────────────────────────────────────────────────────
def test_cjk_count_only_counts_cjk():
    """只数汉字·标点/英文/数字/空格不计。"""
    assert mod._cjk_count("汉字abc123，。 ！") == 2


def test_cjk_count_empty():
    assert mod._cjk_count("") == 0


# ── 3. _mode 边界 ────────────────────────────────────────────────────────────
def test_mode_invalid_falls_back_active():
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("garbage_value")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_uppercase_and_whitespace_normalized():
    """大小写 + 前后空白被规整到合法值。"""
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("  SHADOW  ")
        assert mod._mode() == "shadow"
        _set_mode("OFF")
        assert mod._mode() == "off"
    finally:
        _set_mode(bak)


# ── 4. _LEAD_WINDOW 窗口边界 ─────────────────────────────────────────────────
def test_detect_window_boundary_emotion_just_outside_not_caught():
    """引导词与情绪名词间隔超 _LEAD_WINDOW(8 字) → 不算 on-the-nose 直陈。"""
    # "感到" 后插 9 个非情绪字再接 "愤怒" → 情绪名词起点在窗外
    far = "他感到" + "甲乙丙丁戊己庚辛壬" + "愤怒"
    assert mod.detect_on_the_nose(far) == []
    # 同样字面但情绪名词落在窗内（间隔 4 字）→ 抓到
    near = "他感到" + "甲乙丙丁" + "愤怒"
    hits = mod.detect_on_the_nose(near)
    assert len(hits) == 1 and hits[0]["emotion"] == "愤怒"


# ── 5. scan 短稿 skip ────────────────────────────────────────────────────────
def test_scan_short_draft_skips_with_note():
    """<500 CJK → 出 note·不算密度·无 violation。"""
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("active")
        dp = _write("他感到愤怒，心中充满了绝望。" * 5)  # 远低于 500 CJK
        out = mod.scan(dp)
        assert "note" in out and "500" in out["note"]
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert "on_the_nose_per_1k" not in out
    finally:
        _set_mode(bak)


# ── 6. scan 读不到文件 ───────────────────────────────────────────────────────
def test_scan_unreadable_file_returns_note_not_crash():
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "does_not_exist.txt"))
        assert "note" in out and "读取失败" in out["note"]
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 7. floor 是严格 > 边界（恰好等于 floor 不报） ────────────────────────────
def test_scan_density_at_floor_no_violation():
    """密度恰好 == FLOOR(3.0) → 非严格 > 判定·不出 violation（边界守护）。"""
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("active")
        # 构造 6 hit / 2000 CJK = 3.0/千字 恰好等于 floor。
        # 每个 "他感到愤怒。" = 5 CJK 含 1 hit；填充纯动作句补足 CJK 到 2000。
        hit_unit = "他感到愤怒。"          # 5 CJK·1 hit
        six_hits = hit_unit * 6            # 30 CJK·6 hit
        filler = "他推开门走出去。"        # 7 CJK·0 hit（无引导+情绪紧邻）
        # 还需 1970 CJK → 1970/7 ≈ 282 个 filler
        draft = six_hits + filler * 282
        dp = _write(draft)
        out = mod.scan(dp)
        assert out["on_the_nose_count"] == 6, out
        # cjk 应 == 30 + 282*7 = 2004 → per_1k = round(6/2.004,2)=2.99 < 3.0 不报；
        # 用脚本真实算出的 per_1k 做边界断言（== floor 不报·只 > 报）
        assert out["on_the_nose_per_1k"] <= mod.ON_THE_NOSE_PER_1K_FLOOR
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 8. sample_hits 截断 + 输出形状 ───────────────────────────────────────────
def test_scan_sample_hits_capped_at_six_and_shape():
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("active")
        draft = "他感到愤怒，心中充满了绝望，内心涌起痛苦，觉得无比失望。" * 40
        dp = _write(draft)
        out = mod.scan(dp)
        assert len(out["sample_hits"]) <= 6
        # 输出契约字段（audit_hub 消费）
        for k in ("scanner", "schema_version", "code", "gate_level",
                  "violations", "verdict", "violations_count"):
            assert k in out, k
        assert out["scanner"] == "subtext_rescan"
        assert out["code"] == mod.ISSUE_CODE
        assert out["violations_count"] == len(out["violations"])
    finally:
        _set_mode(bak)


# ── 9. main() CLI 退出码（subprocess 真跑） ─────────────────────────────────
def _cli_env(mode):
    """子进程 env：锁 mode + 强制子进程 stdout 用 UTF-8（否则 Windows 默认 GBK 编码中文 → 解码失败）。"""
    env = dict(os.environ)
    env["SUBTEXT_RESCAN_MODE"] = mode
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_cli_high_density_exit_1():
    """active + 高密度 → main exit 1·stdout 是合法 JSON·verdict FAIL。"""
    env = _cli_env("active")
    dp = _write("他感到愤怒，心中充满了绝望，内心涌起痛苦，觉得无比失望。" * 40)
    r = subprocess.run([sys.executable, str(_TARGET), str(dp)],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 1, (r.returncode, r.stderr)
    rep = json.loads(r.stdout)
    assert "FAIL" in rep["verdict"] and rep["warning"]


def test_cli_off_mode_exit_0():
    """off 模式 → 不读不判·exit 0。"""
    env = _cli_env("off")
    dp = _write("他感到愤怒，心中充满了绝望。" * 40)
    r = subprocess.run([sys.executable, str(_TARGET), str(dp)],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert json.loads(r.stdout)["mode"] == "off"


def test_cli_low_density_exit_0():
    """active + 低密度（纯动作侧写）→ PASS·exit 0。"""
    env = _cli_env("active")
    dp = _write("他把碗洗了三遍，水声盖过窗外的雨。她合上书起身。" * 25)
    r = subprocess.run([sys.executable, str(_TARGET), str(dp)],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert json.loads(r.stdout)["verdict"] == "PASS"
