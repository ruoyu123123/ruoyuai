# -*- coding: utf-8 -*-
"""prose_rhythm_scanner 探针10 R23 W11 Batch-II · P2 · intra_sentence_breath_chain"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import prose_rhythm_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("PROSE_INTRA_SENT_BREATH_MODE", None)
    else:
        os.environ["PROSE_INTRA_SENT_BREATH_MODE"] = m


def _mk_project(relax=False, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    body = {}
    if relax:
        body["intra_sentence_breath_relax"] = True
    if baseline:
        body["breath_group_baseline"] = baseline
    if body:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return proj


# 长链句 (单句 ≥6 逗号)
_LONG_CHAIN = (
    "他走进房间，看了看窗户，然后打开抽屉，拿出钥匙，放到桌上，又坐回沙发，长长地叹了一口气。\n"
    "她跟在后面，关上门，脱下外套，挂在椅子上，倒了杯水，递给他，转身去厨房。\n"
    "厨房里很安静，水壶在响，灯光昏暗，她站在窗边，看着外面的雨，想起小时候的事。\n"
) * 20

# 长子句 (区间 > 35 CJK)
_LONG_GROUP = (
    "在那个遥远的国度里有一座年代久远但仍然屹立不倒的古老石塔被风雨侵蚀依然散发着令人敬畏的气息。\n"
) * 20

_CLEAN = (
    "他走进房间。她抬头。两人对视片刻。\n他坐下。她递过茶。两人都没说话。\n"
) * 60


def test_no_violation_on_clean():
    bak = os.environ.get("PROSE_INTRA_SENT_BREATH_MODE")
    try:
        _set_mode("active")
        r = mod.scan(_CLEAN)
        kinds = {v["kind"] for v in r["violations"]}
        assert "intra_sentence_breath_chain_long" not in kinds
        assert "breath_group_violation" not in kinds
    finally:
        _set_mode(bak)


def test_long_chain_flagged_active():
    bak = os.environ.get("PROSE_INTRA_SENT_BREATH_MODE")
    try:
        _set_mode("active")
        r = mod.scan(_LONG_CHAIN)
        kinds = {v["kind"] for v in r["violations"]}
        assert "intra_sentence_breath_chain_long" in kinds
    finally:
        _set_mode(bak)


def test_long_chain_shadow_silent():
    bak = os.environ.get("PROSE_INTRA_SENT_BREATH_MODE")
    try:
        _set_mode("shadow")
        r = mod.scan(_LONG_CHAIN)
        kinds = {v["kind"] for v in r["violations"]}
        assert "intra_sentence_breath_chain_long" not in kinds
    finally:
        _set_mode(bak)


def test_breath_group_flagged_active():
    bak = os.environ.get("PROSE_INTRA_SENT_BREATH_MODE")
    try:
        _set_mode("active")
        r = mod.scan(_LONG_GROUP)
        kinds = {v["kind"] for v in r["violations"]}
        assert "breath_group_violation" in kinds
    finally:
        _set_mode(bak)


def test_relax_raises_thresholds():
    bak = os.environ.get("PROSE_INTRA_SENT_BREATH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(relax=True)
        r = mod.scan(_LONG_CHAIN, project=proj)
        # relax 后 threshold 上调到 ≥ 8 / ≥50CJK
        assert r["metrics"]["breath_group_max_cjk_threshold"] >= 50
        assert r["metrics"]["intra_sentence_breath_relax"] is True
    finally:
        _set_mode(bak)


def test_off_disables():
    bak = os.environ.get("PROSE_INTRA_SENT_BREATH_MODE")
    try:
        _set_mode("off")
        r = mod.scan(_LONG_CHAIN)
        kinds = {v["kind"] for v in r["violations"]}
        assert "intra_sentence_breath_chain_long" not in kinds
    finally:
        _set_mode(bak)


def test_metrics_present():
    bak = os.environ.get("PROSE_INTRA_SENT_BREATH_MODE")
    try:
        _set_mode("shadow")
        r = mod.scan(_CLEAN)
        m = r["metrics"]
        assert "long_comma_chain_sents" in m
        assert "breath_group_violations" in m
        assert "breath_group_max_cjk_threshold" in m
    finally:
        _set_mode(bak)


def test_baseline_overrides():
    bak = os.environ.get("PROSE_INTRA_SENT_BREATH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(baseline={"max_commas_per_sentence": 20, "breath_group_max_cjk": 200})
        r = mod.scan(_LONG_CHAIN, project=proj)
        kinds = {v["kind"] for v in r["violations"]}
        # 阈值大幅放宽 → 长链句不再触发
        assert "intra_sentence_breath_chain_long" not in kinds
    finally:
        _set_mode(bak)
