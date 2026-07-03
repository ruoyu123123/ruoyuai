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


# ───── 16 🔴 2026-07-02 embedding_store 真接线（默认 scorer 真后端升级）──────────────

def _char_freq_embedding(text: str, dim: int = 32) -> list:
    """确定性、内容感知的假 embedding（字符频率向量）。"""
    import math as _math
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = _math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def test_has_real_embedding_backend_gate():
    old = os.environ.get("EMBED_BACKEND")
    try:
        os.environ.pop("EMBED_BACKEND", None)
        assert mod._has_real_embedding_backend() is False
        os.environ["EMBED_BACKEND"] = "hash"
        assert mod._has_real_embedding_backend() is False
        os.environ["EMBED_BACKEND"] = "mstyle"
        assert mod._has_real_embedding_backend() is True
    finally:
        if old is not None:
            os.environ["EMBED_BACKEND"] = old
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_load_scorer_default_gate_off_stays_placeholder(monkeypatch):
    """零回归证明：无真后端（默认环境）→ _load_scorer(None) 逐字节保持原行为
    （_placeholder_sfs_score, True）。"""
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    for k in [k for k in os.environ if k.startswith("GEN_EMBED__")]:
        monkeypatch.delenv(k, raising=False)
    fn, is_placeholder = mod._load_scorer(None)
    assert fn is mod._placeholder_sfs_score
    assert is_placeholder is True


def test_load_scorer_default_real_backend_upgrades_to_embedding(monkeypatch):
    """真后端命中：_load_scorer(None) 默认 scorer 升级为 _embedding_sfs_score
    （is_placeholder=False），不再是 char-3gram Jaccard。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    fn, is_placeholder = mod._load_scorer(None)
    assert fn is mod._embedding_sfs_score
    assert is_placeholder is False


def test_load_scorer_explicit_spec_overrides_real_backend(monkeypatch):
    """--scorer 显式覆盖通道优先级最高，即便真后端已配置也不受影响。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    fn, is_placeholder = mod._load_scorer("sfs_calibration_probe:_placeholder_sfs_score")
    assert fn is mod._placeholder_sfs_score
    assert is_placeholder is False   # 显式加载成功 → is_placeholder 标 False（沿用既有语义）


def test_embedding_sfs_score_reflects_similarity(monkeypatch):
    """_embedding_sfs_score：内容感知假向量下，相同文本得分应显著高于完全不同文本。"""
    import embedding_store
    monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)
    same = mod._embedding_sfs_score(_TEMPLATE_A, _TEMPLATE_A)
    different = mod._embedding_sfs_score(_TEMPLATE_A, _TEMPLATE_B)
    assert same == 100.0          # 余弦=1.0 → 线性映射满分
    assert same > different


def test_embedding_sfs_score_dimension_mismatch_falls_back(monkeypatch):
    """embedding 维度不一致 → 静默回退 _placeholder_sfs_score（不冒充语义）。"""
    import embedding_store

    def _mismatched(text):
        return [0.1] * (8 if "阳光洒满" in text else 32)   # 只让 B 模板降维·制造维度不一致

    monkeypatch.setattr(embedding_store, "compute_embedding", _mismatched)
    score = mod._embedding_sfs_score(_TEMPLATE_A, _TEMPLATE_B)
    assert score == mod._placeholder_sfs_score(_TEMPLATE_A, _TEMPLATE_B)


def test_probe_batches_prefetch_once_for_default_embedding_scorer(monkeypatch):
    """🔴 2026-07-03 Wave-4：默认 embedding scorer 命中时，probe() 对 same+cross 全部
    文本只触发一次批量 prefetch_embeddings（而非每对各自 2 次 compute_embedding 撞真后端）。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    import embedding_store
    monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)
    prefetch_calls = []

    def _recording_prefetch(texts):
        prefetch_calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)),
                "cache_hits": 0, "computed": len(set(texts))}

    monkeypatch.setattr(embedding_store, "prefetch_embeddings", _recording_prefetch)
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("active")
        same = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                            lambda i: _TEMPLATE_A2 + str(i))
        cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                             lambda i: _TEMPLATE_B + str(i))
        out = mod.probe(same, cross)
        assert len(prefetch_calls) == 1, "same+cross 应只触发一次批量 prefetch"
        # 12 same 对 + 12 cross 对，每对 2 条文本(a.txt+b.txt) = 48 条
        assert len(prefetch_calls[0]) == 48
        assert out["_placeholder_scorer"] is False
    finally:
        _set_mode(bak)


def test_probe_placeholder_scorer_never_calls_prefetch(monkeypatch):
    """占位 scorer（无真后端）不应触发批量 prefetch——批量优化只作用于默认 embedding scorer。"""
    import embedding_store
    calls = {"n": 0}

    def _counting_prefetch(texts):
        calls["n"] += 1
        return {"total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0}

    monkeypatch.setattr(embedding_store, "prefetch_embeddings", _counting_prefetch)
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("active")
        same = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                            lambda i: _TEMPLATE_A2 + str(i))
        cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                             lambda i: _TEMPLATE_B + str(i))
        out = mod.probe(same, cross)
        assert calls["n"] == 0, "占位 scorer 路径不应调用 prefetch_embeddings"
        assert out["_placeholder_scorer"] is True
    finally:
        _set_mode(bak)


def test_probe_uses_real_embedding_scorer_end_to_end(monkeypatch):
    """probe() 端到端：真后端 + 不传 --scorer → _placeholder_scorer=False（用了 embedding 默认
    scorer），且分辨力判定仍走同一套统计管线（same/cross 分数分布够开时 PASS）。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    import embedding_store
    monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)
    bak = os.environ.get("SFS_CALIBRATION_PROBE_MODE")
    try:
        _set_mode("active")
        same = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                            lambda i: _TEMPLATE_A2 + str(i))
        cross = _mk_pair_dir(12, lambda i: _TEMPLATE_A + str(i),
                             lambda i: _TEMPLATE_B + str(i))
        out = mod.probe(same, cross)
        assert out["_placeholder_scorer"] is False
    finally:
        _set_mode(bak)
