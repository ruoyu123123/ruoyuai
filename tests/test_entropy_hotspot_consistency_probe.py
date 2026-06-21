# -*- coding: utf-8 -*-
"""entropy_hotspot_consistency_probe R20 W9 Batch-AA · P1 · 中段 entropy hotspot 探针
确定性·零依赖·零 LLM/零联网。**不产 issue 码** · 只写 probe 文件。
"""
import json
import os
import random
import string
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import entropy_hotspot_consistency_probe as mod  # noqa: E402

_TARGET = _SCRIPTS / "entropy_hotspot_consistency_probe.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("ENTROPY_HOTSPOT_PROBE_MODE", None)
    else:
        os.environ["ENTROPY_HOTSPOT_PROBE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


# 构造中段高 entropy(随机) + 头尾低 entropy(单一字符) 的文本
def _make_middle_high_entropy(seed=42):
    random.seed(seed)
    low_repeat = "天" * 400
    pool = [chr(c) for c in range(0x4E00, 0x4E00 + 1000)]
    high_random = "".join(random.choices(pool, k=600))
    return low_repeat + high_random + low_repeat


def test_off_mode():
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("off")
        out = mod.run(_write("text" * 1000), _mk_project())
        assert out["mode"] == "off"
        assert out["skipped"] == "mode=off"
    finally:
        _set_mode(bak)


def test_shadow_default_writes_probe():
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project()
        text = _make_middle_high_entropy()
        out = mod.run(_write(text), proj, "cluster_001")
        assert out["mode"] == "shadow"
        # probe 文件已写
        probe = proj / "_临时" / "probe" / "hotspot_cluster_001.json"
        assert probe.exists()
        data = json.loads(probe.read_text(encoding="utf-8"))
        assert data["probe"] == "entropy_hotspot_consistency"
        # 中段应捕到 hotspot
        assert len(data["hotspots"]) >= 1
    finally:
        _set_mode(bak)


def test_detect_hotspots_short_text_skipped():
    out = mod.detect_hotspots("天" * 100)
    assert out["hotspots"] == []
    assert "skipped" in out


def test_detect_hotspots_uniform_no_hotspot():
    # 全统一文字 → entropy 全相等 → std=0 → 无 hotspot
    text = "天" * 2000
    out = mod.detect_hotspots(text)
    assert out["hotspots"] == []
    assert out["blocks_total"] >= 4


def test_detect_hotspots_middle_high():
    text = _make_middle_high_entropy()
    out = mod.detect_hotspots(text)
    assert out["mean"] > 0
    assert out["std"] > 0
    # 中段位置 0.25-0.75 内应有至少 1 个 hotspot
    assert len(out["hotspots"]) >= 1
    for h in out["hotspots"]:
        assert 0.25 <= h["position"] <= 0.75
        assert h["z"] > 1.0


def test_block_entropy_basic():
    # 100% 相同字符 → entropy = 0
    assert mod._block_entropy("a" * 100) == 0.0
    # 两种字符均衡 → entropy ~= 1
    e = mod._block_entropy("ab" * 50)
    assert 0.99 <= e <= 1.01


def test_mean_std_basic():
    m, s = mod._mean_std([])
    assert m == 0.0 and s == 0.0
    m, s = mod._mean_std([2.0, 4.0])
    assert m == 3.0
    assert 0.99 <= s <= 1.01


def test_read_failure_returns_error():
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("shadow")
        out = mod.run(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("error", "")
    finally:
        _set_mode(bak)


def test_no_issue_codes_emitted():
    """探针不产 issue 码·只调度 priority bump。"""
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("shadow")
        out = mod.run(_write(_make_middle_high_entropy()), _mk_project())
        # 不应有 violations / flags / code
        assert "violations" not in out
        assert "flags" not in out
        # placeholder 标记
        assert out["_placeholder"] is True
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_main_cli_returns_json():
    text = _make_middle_high_entropy()
    p = _write(text)
    proj = _mk_project()
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj), "--cluster-id", "cluster_001"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ENTROPY_HOTSPOT_PROBE_MODE": "shadow",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["probe"] == "entropy_hotspot_consistency"
