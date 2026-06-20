# -*- coding: utf-8 -*-
"""revision_homogenization_scanner 专属测试 (R9 W5 Batch-M·F2·2026-06-20)

钉死：
  · pre_fix/post_fix 三维指纹 (function_word/sent_len_mean/pstdev) 计算
  · delta_sfs >= 2.0 → REVISION_REDUCED_AUTHOR_FIDELITY (active)
  · 作者档基线 baseline_source=author_profile 优先
  · 无作者档 → pair_fallback (两草稿互相对比)
  · --blind-subset 实验旗
  · shadow / off / active 三态
  · 永远 advisory · 不在 HARD_GATE_CODES
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import revision_homogenization_scanner as rh  # noqa: E402


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(*, profile=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = profile or {}
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


def _set_mode(m):
    if m is None:
        os.environ.pop("REVISION_HOMOGENIZATION_MODE", None)
    else:
        os.environ["REVISION_HOMOGENIZATION_MODE"] = m


# 作者风格 (长句·function word 密集): 多 verbose 子句
_AUTHOR_LIKE = (
    "他在山脊的青石阶上独自向上攀登的时候，便看见了那座被雾气笼罩的古寺，"
    "却没想到山门里竟传来低低的钟声。" * 20)

# 中性 LLM 化 (短句·少 function word): 主语+动作流水
_LLM_LIKE = (
    "他爬山。看见古寺。雾气厚。他停下。" * 60)


def test_off_returns_skeleton():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("off")
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_drafts_skip():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        rep = rh.scan(str(_write("短。")), str(_write("更短。")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_homogenization_detected_active():
    """post_fix 风格离作者基线更远 → FAIL"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        # 作者基线: 长句 (mean ~35)·function_word 密集 (~150/千字)
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
            "sentence_length_mean": {"mean": 35.0, "std": 3.0},
            "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "REVISION_REDUCED_AUTHOR_FIDELITY" in codes
        assert rep["verdict"] == "FAIL_MINOR"
        assert rep["delta_sfs"] >= 2.0
    finally:
        _set_mode(bak)


def test_clean_revision_passes():
    """post_fix 风格仍贴作者基线 → PASS"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
            "sentence_length_mean": {"mean": 35.0, "std": 3.0},
            "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
        })
        # 两份都贴近作者风格
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_AUTHOR_LIKE)),
                      project_root=proj)
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_shadow_no_report():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
            "sentence_length_mean": {"mean": 35.0, "std": 3.0},
            "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_pair_fallback_when_no_profile():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)))
        assert rep["baseline_source"] == "pair_fallback"
        # 退路依然能算 pre/post SFS
        assert "pre_fix_sfs" in rep
        assert "post_fix_sfs" in rep
        assert "delta_sfs" in rep
    finally:
        _set_mode(bak)


def test_baseline_author_profile_priority():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
            "sentence_length_mean": {"mean": 35.0, "std": 3.0},
            "sentence_length_pstdev": {"mean": 8.0, "std": 1.5},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        assert rep["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)


def test_blind_subset_flag():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        # 多段
        text_a = "\n".join([_AUTHOR_LIKE[:200]] * 20)
        text_b = "\n".join([_LLM_LIKE[:200]] * 20)
        rep = rh.scan(str(_write(text_a)), str(_write(text_b)), blind_subset=4)
        assert rep.get("blind_subset_n") == 4
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate_registry():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "REVISION_REDUCED_AUTHOR_FIDELITY" not in hgs


def test_code_not_in_audit_hub_hard_gate():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
    import audit_hub
    assert "REVISION_REDUCED_AUTHOR_FIDELITY" not in audit_hub.HARD_GATE_CODES


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        os.environ["REVISION_HOMOGENIZATION_MODE"] = "junk"
        assert rh._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        rep = rh.scan(str(Path(tempfile.mkdtemp()) / "miss1.txt"),
                      str(Path(tempfile.mkdtemp()) / "miss2.txt"))
        assert "读取失败" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_fingerprint_three_dims():
    fp = rh._fingerprint(_AUTHOR_LIKE)
    assert fp is not None
    assert "function_word_per_1k" in fp
    assert "sent_len_mean" in fp
    assert "sent_len_pstdev" in fp
    assert fp["function_word_per_1k"] >= 0


def test_baseline_partial_dims_works():
    """作者档只声明部分维度·依然可比"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 10.0},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        assert rep["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)


def test_no_change_zero_delta():
    """相同 pre/post → delta_sfs ≈ 0"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_AUTHOR_LIKE)))
        assert abs(rep["delta_sfs"]) < 1e-3
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_delta_sfs_threshold_2():
    """delta_sfs < 2.0 → 不报"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        # 两份非常相似 → delta 小
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": 150.0, "std": 50.0},
            "sentence_length_mean": {"mean": 35.0, "std": 20.0},
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_AUTHOR_LIKE[:-100] + "他说。")),
                      project_root=proj)
        # 相似草稿 + 宽 std → 不报
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_blind_subset_zero_no_op():
    """blind_subset=0 → 不裁切"""
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        text_a = "\n".join([_AUTHOR_LIKE[:200]] * 10)
        rep = rh.scan(str(_write(text_a)), str(_write(text_a)), blind_subset=0)
        # blind_subset 0 不写字段
        assert "blind_subset_n" not in rep
    finally:
        _set_mode(bak)


def test_invalid_profile_falls_back():
    bak = os.environ.get("REVISION_HOMOGENIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={
            "function_word_fingerprint_per_1000": {"mean": "bad", "std": -1}
        })
        rep = rh.scan(str(_write(_AUTHOR_LIKE)), str(_write(_LLM_LIKE)),
                      project_root=proj)
        # 无有效 baseline → 退路
        assert rep["baseline_source"] == "pair_fallback"
    finally:
        _set_mode(bak)


def test_sfs_distance_pair_zero_when_identical():
    fp = rh._fingerprint(_AUTHOR_LIKE)
    assert rh._sfs_distance_pair(fp, fp) == 0.0
