# -*- coding: utf-8 -*-
"""#6 prose_rhythm 探针7 burstiness_collapse 测试（2026-06-16·句长方差塌缩·金标准防矫枉过正）。

探针7 补盲区：探针1 看句长 mean（偏短报），但「mean 达标却句长恒定（方差塌缩·匀速碎句）」探针1
抓不到——cluster 句长 std vs 作者 std·r<0.5 minor/<0.4 major 单边偏均匀（偏高永不报·北极星③）。
金标准校准真作者 cluster_std/作者 std 最小 0.61→阈值 0.5/0.4 留余量（6 作者自验 active 零误报 0.67-1.01）。
默认 shadow（检测力待 gen-model 草稿验证再 active）。与探针1解耦防双计数。零依赖·run_tests.py/pytest 双跑。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import prose_rhythm_scanner as P  # noqa: E402


def _author_style(tmp, mean=30.0, std=23.0):
    """造 fake 作者档（quantitative.sentence_length.{mean,std}）→ style_path。"""
    sp = tmp / "作者风格.json"
    sp.write_text(json.dumps({"quantitative": {"sentence_length": {"mean": mean, "std": std}}},
                             ensure_ascii=False), encoding="utf-8")
    return sp


def _set_burst(v):
    if v is None:
        os.environ.pop("PROSE_BURSTINESS_MODE", None)
    else:
        os.environ["PROSE_BURSTINESS_MODE"] = v


def _flat_at_mean(sent_len=30, n=200):
    """句长恒定在作者 mean（std≈0·但 mean 达标·探针1 z≈0 不报·只探针7 抓方差塌缩）。"""
    sent = "他" + "走" * (sent_len - 2) + "。"  # sent_len CJK 恒长
    return "\n".join(sent for _ in range(n))


def _varied_text():
    """长短交错草稿（std 大·健康节奏·r 高不报）。"""
    short = "他停住。"
    long = "她站在窗前，望着远处连绵起伏的群山，思绪却飘回了那个遥远的午后，阳光透过梧桐叶洒下斑驳的光影里。"
    return "\n".join((short if i % 2 else long) for i in range(200))


def test_default_shadow_no_report():
    """默认 shadow：方差塌缩草稿也不报 burstiness（探针7 shadow·检测力待 gen-model 验证）。"""
    _set_burst(None)
    try:
        with tempfile.TemporaryDirectory() as td:
            sp = _author_style(Path(td))
            r = P.scan(_flat_at_mean(), style_path=sp)
            assert not [v for v in r["violations"] if v["kind"] == "burstiness_collapse"]
    finally:
        _set_burst(None)


def test_active_variance_collapse_fails_and_decoupled():
    """🔴 核心：active + mean 达标但句长恒定（std≈0·r<0.4）→ 探针7 报 major·
    探针1 因 mean 达标不报（解耦验证·防双计数）。"""
    _set_burst("active")
    try:
        with tempfile.TemporaryDirectory() as td:
            sp = _author_style(Path(td), mean=30.0, std=23.0)
            r = P.scan(_flat_at_mean(sent_len=30), style_path=sp)  # 句长恒 30=作者 mean·std≈0
            bursts = [v for v in r["violations"] if v["kind"] == "burstiness_collapse"]
            shorts = [v for v in r["violations"] if v["kind"] == "sentence_too_short"]
            assert bursts, f"探针7 应报方差塌缩·violations={r['violations']}"
            assert bursts[0]["severity"] == "major", bursts  # r≈0 < 0.4
            assert not shorts, "探针1 不应报(mean 达标)·验证解耦防双计数"
    finally:
        _set_burst(None)


def test_active_varied_text_pass():
    """active：长短交错草稿（std 大·r 高）→ 不报 burstiness（北极星③偏高永不报）。"""
    _set_burst("active")
    try:
        with tempfile.TemporaryDirectory() as td:
            sp = _author_style(Path(td), mean=30.0, std=23.0)
            r = P.scan(_varied_text(), style_path=sp)
            assert not [v for v in r["violations"] if v["kind"] == "burstiness_collapse"]
    finally:
        _set_burst(None)


def test_no_author_floor():
    """无作者档 + 句长恒定（cluster_std<5 绝对地板）→ active 报（探针1 无 std 走 ratio 兜底不报）。"""
    _set_burst("active")
    try:
        r = P.scan(_flat_at_mean(sent_len=30), style_path=None, project=None)  # 无作者档
        bursts = [v for v in r["violations"] if v["kind"] == "burstiness_collapse"]
        assert bursts and bursts[0]["author_std"] is None, r["violations"]
    finally:
        _set_burst(None)


def test_metrics_has_burstiness_ratio():
    """metrics 始终含 burstiness_ratio + sentence_std_cluster（shadow/active 都记·影子收集）。"""
    _set_burst(None)  # shadow
    try:
        with tempfile.TemporaryDirectory() as td:
            sp = _author_style(Path(td))
            r = P.scan(_varied_text(), style_path=sp)
            assert "burstiness_ratio" in r["metrics"]
            assert "sentence_std_cluster" in r["metrics"]
    finally:
        _set_burst(None)


def test_burstiness_code_not_hard_gate():
    """制度锁：burstiness_collapse 全 advisory·prose_rhythm gate_level=advisory（绝不 hard_gate·北极星⑤）。"""
    _set_burst("active")
    try:
        with tempfile.TemporaryDirectory() as td:
            sp = _author_style(Path(td))
            r = P.scan(_flat_at_mean(), style_path=sp)
            assert r["gate_level"] == "advisory"
    finally:
        _set_burst(None)
