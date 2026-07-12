# -*- coding: utf-8 -*-
"""xiezi_kernel_recall_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import xiezi_kernel_recall_scanner as xk  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("XIEZI_KERNEL_RECALL_MODE", None)
    else:
        os.environ["XIEZI_KERNEL_RECALL_MODE"] = m


def _mk_project(enable=True, symbols=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if enable:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"huaben_zhanghui_pastiche": True}, ensure_ascii=False),
            encoding="utf-8")
    if symbols is not None:
        (proj / "_数据库" / ".cross_cluster_scan").mkdir(parents=True,
                                                       exist_ok=True)
        (proj / "_数据库" / ".cross_cluster_scan" /
         "xiezi_kernel.json").write_text(
            json.dumps({"kernel_symbols": symbols}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _manifest(p, **kw):
    p.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")
    return p


def test_off_skeleton():
    bak = os.environ.get("XIEZI_KERNEL_RECALL_MODE")
    try:
        _set_mode("off")
        rep = xk.scan(str(_write("正文" * 500)))
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_not_enabled_skips():
    bak = os.environ.get("XIEZI_KERNEL_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(enable=False)
        rep = xk.scan(str(_write("正文" * 500)), project_root=proj)
        assert "跳过" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_first_cluster_homology_low_score():
    bak = os.environ.get("XIEZI_KERNEL_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(enable=True)
        mfd = Path(tempfile.mkdtemp()) / "m.json"
        _manifest(mfd, cluster_id="cluster_001", cluster_index=1)
        # 平淡草稿无四要素
        text = "正文" * 500
        rep = xk.scan(str(_write(text)), project_root=proj,
                      manifest_path=str(mfd))
        codes = [v["code"] for v in rep["violations"]]
        assert "XIEZI_HOMOLOGY_THIN" in codes
    finally:
        _set_mode(bak)


def test_final_volume_missing_kernel_recall():
    bak = os.environ.get("XIEZI_KERNEL_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(enable=True, symbols=[
            {"symbol": "通灵宝玉", "planted_cluster": "cluster_001"}])
        mfd = Path(tempfile.mkdtemp()) / "m.json"
        _manifest(mfd, cluster_id="cluster_050", is_volume_finale=True)
        text = "末卷正文" * 500
        rep = xk.scan(str(_write(text)), project_root=proj,
                      manifest_path=str(mfd))
        codes = [v["code"] for v in rep["violations"]]
        assert "XIEZI_KERNEL_NOT_RECALLED" in codes
    finally:
        _set_mode(bak)


def test_final_volume_with_recall_passes():
    bak = os.environ.get("XIEZI_KERNEL_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(enable=True, symbols=[
            {"symbol": "通灵宝玉"}])
        mfd = Path(tempfile.mkdtemp()) / "m.json"
        _manifest(mfd, cluster_id="cluster_050", is_volume_finale=True)
        text = "通灵宝玉在末卷召回" + "正文" * 500
        rep = xk.scan(str(_write(text)), project_root=proj,
                      manifest_path=str(mfd))
        codes = [v["code"] for v in rep["violations"]]
        assert "XIEZI_KERNEL_NOT_RECALLED" not in codes
    finally:
        _set_mode(bak)


def test_homology_passes_with_breaks():
    bak = os.environ.get("XIEZI_KERNEL_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(enable=True)
        mfd = Path(tempfile.mkdtemp()) / "m.json"
        _manifest(mfd, cluster_id="cluster_001", cluster_index=1)
        # 含时间断层 + 地点断层 + kernel 命名
        text = ("百年前那地方山岭名为通灵谷" + "正文" * 500)
        rep = xk.scan(str(_write(text)), project_root=proj,
                      manifest_path=str(mfd))
        # homology_score 应 >= 2 → 无 XIEZI_HOMOLOGY_THIN
        codes = [v["code"] for v in rep["violations"]]
        assert "XIEZI_HOMOLOGY_THIN" not in codes
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "XIEZI_KERNEL_NOT_RECALLED" not in hgs
    assert "XIEZI_HOMOLOGY_THIN" not in hgs


def test_shadow_mode_no_violation():
    bak = os.environ.get("XIEZI_KERNEL_RECALL_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(enable=True, symbols=[{"symbol": "通灵宝玉"}])
        mfd = Path(tempfile.mkdtemp()) / "m.json"
        _manifest(mfd, cluster_id="cluster_050", is_volume_finale=True)
        rep = xk.scan(str(_write("正文" * 500)), project_root=proj,
                      manifest_path=str(mfd))
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_short_draft_skips():
    bak = os.environ.get("XIEZI_KERNEL_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(enable=True)
        rep = xk.scan(str(_write("短")), project_root=proj)
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)
