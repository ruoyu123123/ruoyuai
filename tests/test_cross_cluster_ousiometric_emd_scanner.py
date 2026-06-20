# -*- coding: utf-8 -*-
"""cross_cluster_ousiometric_emd_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_cluster_ousiometric_emd_scanner as eemd  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("OUSIOMETRIC_EMD_MODE", None)
    else:
        os.environ["OUSIOMETRIC_EMD_MODE"] = m


def _mk_project(num_clusters=0, with_drafts=False, post_amp_low=False):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    drafts_dir = proj / "章节"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    clusters = []
    for i in range(num_clusters):
        cid = f"cluster_{i+1:03d}"
        clusters.append({"cluster_id": cid})
        if with_drafts:
            # 前段振幅 high(各种 power+danger 交替) · 后段 amp 低(单调)
            if post_amp_low and i >= num_clusters // 2:
                text = "平静走过田野。" * 800  # 低振幅 几乎无 power/danger
            else:
                # 高振幅: 力量/危险交替
                text = (("强威霸猛雷怒战斗破碎" * 50)
                        + "\n"
                        + ("危险惧怕惊恐怖凶狰狞" * 50))
            (drafts_dir / f"{cid}_draft.txt").write_text(text, encoding="utf-8")
    (db / "cluster_index.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False),
        encoding="utf-8")
    return proj


def test_off_skeleton():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("off")
        proj = _mk_project()
        rep = eemd.scan_project(proj)
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_cluster_count_skips():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=5)
        rep = eemd.scan_project(proj)
        assert "长篇门槛" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_no_drafts_skips():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=35, with_drafts=False)
        rep = eemd.scan_project(proj)
        assert "无可读" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_oscillation_basic_pass_with_balanced_drafts():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=30, with_drafts=True,
                          post_amp_low=False)
        rep = eemd.scan_project(proj)
        # 全程高振幅 → 不应报塌缩
        codes = [v.get("code") for v in rep.get("violations", [])]
        assert "OUSIOMETRIC_OSCILLATION_DEGRADED" not in codes
    finally:
        _set_mode(bak)


def test_shadow_mode():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(num_clusters=35, with_drafts=True,
                          post_amp_low=True)
        rep = eemd.scan_project(proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "OUSIOMETRIC_OSCILLATION_DEGRADED" not in hgs


def test_scan_signature_compat():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("off")
        proj = _mk_project()
        rep = eemd.scan(draft_path=None, project_root=proj)
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_local_extrema_helper():
    ext = eemd.local_extrema([1, 3, 2, 5, 4, 6, 1])
    assert isinstance(ext, list)


def test_amplitude_envelope_empty():
    assert eemd.amplitude_envelope([1, 2, 3], []) == 0.0
