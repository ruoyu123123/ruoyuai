# -*- coding: utf-8 -*-
"""prose_rhythm_scanner 探针9 (R20 W9 Batch-CC P2) · 300CJK±50 payload share
确定性·零依赖·零 LLM/零联网。
"""
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
        os.environ.pop("PROSE_300CJK_PAYLOAD_MODE", None)
    else:
        os.environ["PROSE_300CJK_PAYLOAD_MODE"] = m


def _mk_project(target=None, std=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if target is not None or std is not None:
        b = {}
        if target is not None:
            b["target"] = target
        if std is not None:
            b["std"] = std
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"paragraph_300cjk_share_baseline": b}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 全部 300字段 (5 段 × 300 字)
_PARAGRAPH_300 = "\n\n".join(["他走进房间。" * 50] * 5)

# 全部碎段 (5 段 × 60 字)
_PARAGRAPH_TINY = "\n\n".join(["他走进房间。" * 10] * 5)


def test_300cjk_share_field_exists():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("shadow")
        r = mod.scan(_PARAGRAPH_300)
        assert "paragraph_300cjk_share" in r["metrics"]
        # 5 段 × 300字段 → 占比 1.0
        assert r["metrics"]["paragraph_300cjk_share"] > 0.5
    finally:
        _set_mode(bak)


def test_300cjk_share_low_for_tiny_paragraphs():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("shadow")
        r = mod.scan(_PARAGRAPH_TINY)
        assert r["metrics"]["paragraph_300cjk_share"] == 0.0
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("shadow")
        r = mod.scan(_PARAGRAPH_TINY)
        kinds = {v["kind"] for v in r["violations"]}
        assert "paragraph_300cjk_payload_off_band" not in kinds
    finally:
        _set_mode(bak)


def test_active_off_band_flagged_when_low():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("active")
        # target=0.5 std=0.05 → share=0 → z=(0-0.5)/0.05=-10 → 报
        proj = _mk_project(target=0.5, std=0.05)
        r = mod.scan(_PARAGRAPH_TINY, project=proj)
        kinds = {v["kind"] for v in r["violations"]}
        assert "paragraph_300cjk_payload_off_band" in kinds
    finally:
        _set_mode(bak)


def test_active_off_band_flagged_when_high():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("active")
        # target=0.0 std=0.01 → share=1 → z=100 → 报偏多
        proj = _mk_project(target=0.0, std=0.01)
        r = mod.scan(_PARAGRAPH_300, project=proj)
        kinds = {v["kind"] for v in r["violations"]}
        assert "paragraph_300cjk_payload_off_band" in kinds
        # 找出对应 violation
        viol = next(v for v in r["violations"]
                    if v["kind"] == "paragraph_300cjk_payload_off_band")
        assert viol["z"] > 0
        assert viol["code"] == "PARAGRAPH_300CJK_PAYLOAD_OFF_BAND"
    finally:
        _set_mode(bak)


def test_baseline_target_in_author_baseline():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(target=0.30, std=0.05)
        r = mod.scan(_PARAGRAPH_TINY, project=proj)
        ab = r["author_baseline"]
        assert ab["para_300_target"] == 0.30
        assert ab["para_300_std"] == 0.05
    finally:
        _set_mode(bak)


def test_fallback_when_no_baseline():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("active")
        # 无 baseline · 用 default target=0.20 std=0.10
        r = mod.scan(_PARAGRAPH_TINY)
        # share=0·z=(0-0.2)/0.1=-2 > 1.5 → 报
        kinds = {v["kind"] for v in r["violations"]}
        assert "paragraph_300cjk_payload_off_band" in kinds
    finally:
        _set_mode(bak)


def test_too_few_paragraphs_skipped():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("active")
        # 仅 2 段 → 跳过(<5)
        r = mod.scan("\n\n".join(["他走进房间。" * 20] * 2))
        kinds = {v["kind"] for v in r["violations"]}
        assert "paragraph_300cjk_payload_off_band" not in kinds
    finally:
        _set_mode(bak)


def test_z_metric_present():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("shadow")
        r = mod.scan(_PARAGRAPH_300)
        assert "paragraph_300cjk_z" in r["metrics"]
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert "PARAGRAPH_300CJK_PAYLOAD_OFF_BAND" not in audit_hub.HARD_GATE_CODES


def test_mid_paragraph_in_band():
    bak = os.environ.get("PROSE_300CJK_PAYLOAD_MODE")
    try:
        _set_mode("shadow")
        # 300CJK 段 (300字 / 段 × 5 段)
        r = mod.scan(_PARAGRAPH_300)
        # 段长 300 在 [250, 350]
        assert r["metrics"]["paragraph_300cjk_share"] >= 0.5
    finally:
        _set_mode(bak)
