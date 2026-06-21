# -*- coding: utf-8 -*-
"""sfs_calibration_probe R20 W9 Batch-Z·P0 SFS 校准探针回归测试

确定性·零依赖·零 LLM/零联网。覆盖：
  1. off 骨架
  2. 数据不足 skip
  3. 同/跨数据齐全 + 占位 scorer + 同源高分 cross 低分 → PASS（分得开）
  4. 同/跨 = 同分 → poorly_calibrated（AUC≈0.5 < 0.65）
  5. shadow + poorly → 不上报
  6. active + poorly → FAIL_MINOR
  7. can_promote_to_active gate（AUC≥0.75）
  8. ROC-AUC tied rank 0.5
  9. IQR overlap 极端值
 10. quartiles 单元素
 11. char ngrams 边缘
 12. _mode 非法回落
 13. CLI subprocess 退出码
 14. scorer spec 'module:func' 加载失败回退占位
 15. flat 平面 *.txt 配对模式
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
import sfs_calibration_probe as mod  # noqa: E402

_TARGET = _SCRIPTS / "sfs_calibration_probe.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("SFS_CALIBRATION_PROBE_MODE", None)
    else:
        os.environ["SFS_CALIBRATION_PROBE_MODE"] = m


def _mk_pair_dir(pair_count, gen_a, gen_b):
    """目录模式：root/<pair_xx>/{a.txt,b.txt}。gen_a/gen_b 接收 i 返回正文。"""
    root = Path(tempfile.mkdtemp())
    for i in range(pair_count):
        sub = root / f"pair_{i:03d}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "a.txt").write_text(gen_a(i), encoding="utf-8")
        (sub / "b.txt").write_text(gen_b(i), encoding="utf-8")
    return root


def _mk_flat_dir(pair_count, gen):
    root = Path(tempfile.mkdtemp())
    for i in range(pair_count * 2):
        (root / f"file_{i:03d}.txt").write_text(gen(i), encoding="utf-8")
    return root


_TEMPLATE_A = "夜色深沉，他站在桥头，看着河水缓缓流淌，心中泛起一阵难以言喻的孤独。" * 8
_TEMPLATE_A2 = "夜色深沉，他站在桥头，望着流水，心中泛起难言的孤独。" * 8
_TEMPLATE_B = "明亮的阳光洒满海岸，孩子们追逐着海鸥，笑声穿过沙滩传向远方。" * 8


# ───── 1 off 骨架 ──
def test_off_returns_skeleton():
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("off")
        same = _mk_pair_dir(2, lambda i: _TEMPLATE_A, lambda i: _TEMPLATE_A2)
        cross = _mk_pair_dir(2, lambda i: _TEMPLATE_A, lambda i: _TEMPLATE_B)
        out = mod.probe(same, cross)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 2 数据不足 skip ──
def test_insufficient_pairs_skip():
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("active")
        same = _mk_pair_dir(3, lambda i: _TEMPLATE_A, lambda i: _TEMPLATE_A2)
        cross = _mk_pair_dir(3, lambda i: _TEMPLATE_A, lambda i: _TEMPLATE_B)
        out = mod.probe(same, cross)
        assert "数据不足" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 3 well-calibrated → PASS ──
def test_well_calibrated_pass():
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("active")
        # 同作者：a 和 a2 都基于 TEMPLATE_A·高相似
        same = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                            lambda i: _TEMPLATE_A2 + str(i))
        # 跨作者：a 是 A 体，b 是 B 体·低相似
        cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                             lambda i: _TEMPLATE_B + str(i))
        out = mod.probe(same, cross)
        assert out["verdict"] == "PASS"
        m = out["metrics"]
        assert m["delta_means"] > 5
        assert m["roc_auc"] >= 0.65
        assert out["poorly_calibrated"] is False
    finally:
        _set_mode(bak)


# ───── 4 poorly calibrated → 标 poorly ──
def test_poorly_calibrated_detection():
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("active")
        # 同作者和跨作者都用同样的混合·分不开
        same = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                            lambda i: _TEMPLATE_B + str(i))
        cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                             lambda i: _TEMPLATE_B + str(i))
        out = mod.probe(same, cross)
        assert out["poorly_calibrated"] is True
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "SFS_POORLY_CALIBRATED_FOR_AUTHOR"
    finally:
        _set_mode(bak)


# ───── 5 shadow + poorly → 不上报 ──
def test_shadow_poorly_no_report():
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("shadow")
        same = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                            lambda i: _TEMPLATE_B + str(i))
        cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                             lambda i: _TEMPLATE_B + str(i))
        out = mod.probe(same, cross)
        assert out["poorly_calibrated"] is True
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ───── 6 can_promote_to_active gate ──
def test_can_promote_gate():
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("active")
        same = _mk_pair_dir(12, lambda i: _TEMPLATE_A,
                            lambda i: _TEMPLATE_A2)
        cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A,
                             lambda i: _TEMPLATE_B)
        out = mod.probe(same, cross)
        # 高度可区分 → AUC≈1.0 → can promote
        assert out["can_promote_to_active"] is True
    finally:
        _set_mode(bak)


# ───── 7 ROC-AUC Mann-Whitney 等价 ──
def test_roc_auc_basic():
    # same 全 > cross → AUC = 1.0
    assert mod._roc_auc_mwu([10, 20, 30], [1, 2, 3]) == 1.0
    # 完全相反 → 0.0
    assert mod._roc_auc_mwu([1, 2, 3], [10, 20, 30]) == 0.0
    # tied → 0.5
    assert mod._roc_auc_mwu([5, 5], [5, 5]) == 0.5
    # 空集合 → 0.5（中性）
    assert mod._roc_auc_mwu([], [1]) == 0.5


# ───── 8 quartiles ──
def test_quartiles_single():
    q1, q2, q3 = mod._quartiles([5.0])
    assert q1 == q2 == q3 == 5.0


def test_quartiles_basic():
    q1, q2, q3 = mod._quartiles([1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert q1 <= q2 <= q3


# ───── 9 IQR overlap ──
def test_iqr_overlap_disjoint():
    # 完全分离·overlap = 0
    s = [10.0] * 5 + [20.0] * 5
    c = [1.0] * 5 + [2.0] * 5
    ov = mod._iqr_overlap(s, c)
    assert ov < 0.5


def test_iqr_overlap_identical():
    s = [5.0, 6.0, 7.0, 8.0, 9.0]
    ov = mod._iqr_overlap(s, list(s))
    assert ov == 1.0


def test_iqr_overlap_empty():
    assert mod._iqr_overlap([], [1.0]) == 1.0


# ───── 10 char ngrams ──
def test_char_ngrams_short():
    assert mod._char_ngrams("ab", 3) == set()
    assert "abc" in mod._char_ngrams("abcde", 3)


def test_placeholder_scorer_identical():
    s = mod._placeholder_sfs_score("abcdefgh", "abcdefgh")
    assert s == 100.0


def test_placeholder_scorer_disjoint():
    s = mod._placeholder_sfs_score("一二三四五六七", "甲乙丙丁戊己庚")
    assert s == 0.0


# ───── 11 _mode 非法回落 ──
def test_mode_invalid_falls_back():
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


# ───── 12 scorer spec 加载失败回退 ──
def test_scorer_spec_failed_falls_back():
    fn, ph = mod._load_scorer("nonexistent_module:func")
    assert ph is True
    assert fn is mod._placeholder_sfs_score


def test_scorer_spec_missing_falls_back():
    fn, ph = mod._load_scorer(None)
    assert ph is True


# ───── 13 flat 平面配对 ──
def test_flat_pair_dir():
    root = _mk_flat_dir(12, lambda i: _TEMPLATE_A + str(i))
    pairs = mod._iter_pair_dir(root)
    assert len(pairs) == 12


def test_subdir_pair_dir():
    root = _mk_pair_dir(5, lambda i: "x", lambda i: "y")
    pairs = mod._iter_pair_dir(root)
    assert len(pairs) == 5


def test_pair_dir_nonexistent():
    assert mod._iter_pair_dir(Path("/nonexistent_xxx_abc")) == []


# ───── 14 CLI exit ──
def _run_cli(same_dir, cross_dir, mode="active"):
    cmd = [sys.executable, str(_TARGET),
           "--same-dir", str(same_dir),
           "--cross-dir", str(cross_dir)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SFS_CALIBRATION_PROBE_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_cli_exit_0_on_clean():
    same = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                        lambda i: _TEMPLATE_A2 + str(i))
    cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                         lambda i: _TEMPLATE_B + str(i))
    r = _run_cli(same, cross)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["poorly_calibrated"] is False


def test_cli_exit_1_on_poorly():
    same = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                        lambda i: _TEMPLATE_B + str(i))
    cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                         lambda i: _TEMPLATE_B + str(i))
    r = _run_cli(same, cross)
    assert r.returncode == 1, r.stderr


# ───── 15 registry _new=true + 不在 hard_gate ──
def test_registry_entry():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg["scanners"].get("sfs_calibration_probe")
    assert s is not None
    assert s.get("_new") is True
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs
