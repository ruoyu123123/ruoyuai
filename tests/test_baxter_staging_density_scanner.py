# -*- coding: utf-8 -*-
"""baxter_staging_density_scanner R20 W9 Batch-AA · P1 · Baxter 身体/空间/道具调度密度
确定性·零依赖·零 LLM/零联网。
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
import baxter_staging_density_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "baxter_staging_density_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("BAXTER_STAGING_MODE", None)
    else:
        os.environ["BAXTER_STAGING_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None, characters=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"staging_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    if characters is not None:
        (proj / "_数据库" / "角色池.json").write_text(
            json.dumps({"emerged": characters}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 高 staging 密度
_RICH_STAGING = (
    "她抬手按了按额头。他走到窗前，拿起茶杯。她皱眉，放下笔，坐下。"
    "他俯身翻开账本。她转身退后，递给他一把钥匙。门外脚步声靠近。"
) * 10

# 全对话无 staging(talking heads)
_TALKING_HEADS = (
    "他说道：“你来了。”\n她说：“嗯。”\n他笑道：“坐吧。”\n她道：“好。”\n"
    "他说：“喝水。”\n她说：“谢谢。”\n他道：“慢点。”\n她说：“嗯。”\n"
) * 25

# staging 稀薄(只有名词无动作)
_THIN_STAGING = "天气冷。屋子小。窗户旧。墙上有画。桌上是茶。地板潮湿。" * 40


def test_off_returns_skeleton():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_RICH_STAGING), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "staging_per_kcjk" not in out
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_THIN_STAGING), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_thin_staging_flagged():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_THIN_STAGING), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "STAGING_THIN" in codes
        assert out["staging_per_kcjk"] < 4.0
    finally:
        _set_mode(bak)


def test_active_rich_staging_pass():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_RICH_STAGING), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "STAGING_THIN" not in codes
        assert out["staging_per_kcjk"] >= 4.0
    finally:
        _set_mode(bak)


def test_talking_heads_detected():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=["他", "她"])
        out = mod.scan(_write(_TALKING_HEADS), proj)
        # 对话密度高 + active_chars≥2 → talking_heads gating 生效
        codes = {f["code"] for f in out.get("flags", [])}
        assert out["dialogue_gated"] is True
        # tag>staging 时报 STAGING_TALKING_HEADS(对话密集但调度无)
        assert "STAGING_TALKING_HEADS" in codes or "STAGING_THIN" in codes
    finally:
        _set_mode(bak)


def test_author_baseline_overrides_fallback():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("active")
        baseline = {"staging_per_kcjk_low": 0.0, "bucket_share_high": 1.0,
                    "bucket_share_low": 0.0, "talking_heads_ratio_max": 1000.0,
                    "dialogue_density_p50": 0.99}
        out = mod.scan(_write(_THIN_STAGING), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        # 极宽 baseline → 不报 STAGING_THIN
        codes = {f["code"] for f in out.get("flags", [])}
        assert "STAGING_THIN" not in codes
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他抬头。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("BAXTER_STAGING_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_lexicon_loaded():
    lex = mod._load_lexicon()
    assert "buckets" in lex
    buckets = lex["buckets"]
    assert "body_cue" in buckets
    assert "space_cue" in buckets
    assert "prop_cue" in buckets
    assert "posture_shift" in buckets
    # 60-80 条 total
    total = sum(len(v) for v in buckets.values())
    assert total >= 60


def test_count_bucket_hits():
    n = mod._count_bucket_hits("抬手抬头抬手", ["抬手", "抬头"])
    assert n == 3  # 2 抬手 + 1 抬头


def test_dialogue_density():
    text = "他说“你好”。她说“嗯”。"
    d = mod._dialogue_density(text)
    assert d > 0


def test_per_character_share_no_names():
    out = mod._per_character_staging_share("文本", [], ["走"])
    assert out["chars_total"] == 0


def test_per_character_share_with_match():
    text = "李四走到门前。张三抬头看了看。"
    out = mod._per_character_staging_share(text, ["李四", "张三"], ["走到", "抬头"])
    assert out["active_chars"] == 2
    assert out["with_staging"] == 2


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "BAXTER_STAGING_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_THIN_STAGING)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "baxter_staging"
