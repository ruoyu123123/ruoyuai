"""阶段2：作者决策原则 + 人物刻画手法（聚合合并 + writer 注入）测试。

钉死：
  · consolidate.aggregate_decisions 跨 cluster 去重合并(不取首个·不堆叠)
  · build_manifest._collect_author_decision_principles 注入(env DECISION_INJECT_MODE)·永远 advisory·零检测
  · 无作者档 → None(零回归)·默认 active 注入(2026-06-13 放量)·显式 shadow 不注入
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402
import build_manifest as bm  # noqa: E402


# ---------- 聚合合并（确定性·去重） ----------

def _mk_style(root: Path, raw_dec=None, raw_cha=None) -> Path:
    proj = root / "测试书"
    (proj / "蒸馏进度").mkdir(parents=True)
    style = {"author": "测试作者"}
    if raw_dec is not None:
        style["_raw_decisions_observations"] = raw_dec
    if raw_cha is not None:
        style["_raw_characterization_observations"] = raw_cha
    (proj / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return proj


def test_decisions_merge_dedup_across_clusters():
    """跨 cluster 嵌套观察去重合并（B1 道德滤镜的母题在多 cluster 重复 → 去重）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_style(Path(d), raw_dec=[
            {"cluster": "cluster_001", "observations": {
                "B1_道德滤镜": {"母题": "天才的孤独", "胜利代价": "胜利附带阴影"}}},
            {"cluster": "cluster_002", "observations": {
                "B1_道德滤镜": {"母题": "天才的孤独", "叙述者姿态": "冷峻审判"}}},  # 母题重复
        ])
        dec, _ = cap.aggregate_decisions(proj)
        mt = dec["B1_道德滤镜"]["母题"]
        assert mt == ["天才的孤独"]  # 去重·不堆叠成 2 份
        assert "胜利附带阴影" in dec["B1_道德滤镜"]["胜利代价"]
        assert "冷峻审判" in dec["B1_道德滤镜"]["叙述者姿态"]


def test_characterization_list_merge():
    """C2 声纹三件套(list of dict)跨 cluster 去重合并。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_style(Path(d), raw_cha=[
            {"cluster": "c1", "observations": {"C2_声纹三件套": [
                {"角色": "甲", "情绪底色": "冷"}]}},
            {"cluster": "c2", "observations": {"C2_声纹三件套": [
                {"角色": "甲", "情绪底色": "冷"},      # 重复 → 去重
                {"角色": "乙", "情绪底色": "燥"}]}},
        ])
        _, cha = cap.aggregate_decisions(proj)
        roles = [x["角色"] for x in cha["C2_声纹三件套"]]
        assert roles == ["甲", "乙"]  # 去重后 2 个


def test_empty_raw_safe():
    """无 _raw 观察 → 空·不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_style(Path(d))
        assert cap.aggregate_decisions(proj) == ({}, {})


# ---------- writer 注入 ----------

def _set(m):
    if m is None:
        os.environ.pop("DECISION_INJECT_MODE", None)
    else:
        os.environ["DECISION_INJECT_MODE"] = m


def _mk_proj_with_principles(tmp: Path, dec=None, cha=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    style = {"author": "测试", "quantitative": {"sentence_length": {"mean": 28}}}
    if dec:
        style["author_decision_principles"] = dec
    if cha:
        style["characterization_craft"] = cha
    (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return tmp


def test_active_default_injects():
    """2026-06-13 切 active 放量：默认（无 env）→ active → 注入（零回归仅 off/旧档）。"""
    _set(None)
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_proj_with_principles(Path(d), dec={"B1_道德滤镜": {"母题": ["孤独"]}})
        s = bm.DatabaseScanner(tmp, 1)
        r = bm._collect_author_decision_principles(s)
        assert r is not None
        assert r["author_decision_principles"]["B1_道德滤镜"]


def test_shadow_explicit_no_injection():
    """显式 shadow（调试）→ None（不注入），但落盘摘要。"""
    _set("shadow")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_proj_with_principles(Path(d), dec={"B1_道德滤镜": {"母题": ["孤独"]}})
            s = bm.DatabaseScanner(tmp, 1)
            assert bm._collect_author_decision_principles(s) is None
            assert (tmp / "_数据库" / ".decision_principles" / "ch_001.json").exists()
    finally:
        _set(None)


def test_active_injects_and_advisory():
    _set("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_proj_with_principles(
                Path(d), dec={"B1_道德滤镜": {"母题": ["孤独"], "胜利代价": ["藏悲凉"]}},
                cha={"C1_刻画比例": ["直接2:间接8"]})
            s = bm.DatabaseScanner(tmp, 1)
            r = bm._collect_author_decision_principles(s)
            assert r is not None
            assert r["gate_level"] == "advisory"
            assert r["advisory_only"] is True
            assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
            assert r["author_decision_principles"]["B1_道德滤镜"]
            assert r["characterization_craft"]["C1_刻画比例"]
    finally:
        _set(None)


def test_no_principles_none():
    """无 decision/characterization（旧档）→ None（零回归）。"""
    _set("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_proj_with_principles(Path(d))
            s = bm.DatabaseScanner(tmp, 1)
            assert bm._collect_author_decision_principles(s) is None
    finally:
        _set(None)


def test_no_scanner_keys():
    """零检测：输出无 violations/verdict/scanner 等检测键（创作提示非判决）。"""
    _set("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_proj_with_principles(Path(d), dec={"B1_道德滤镜": {"母题": ["孤独"]}})
            s = bm.DatabaseScanner(tmp, 1)
            r = bm._collect_author_decision_principles(s)
            for k in ("violations", "verdict", "scanner", "violations_count"):
                assert k not in r
    finally:
        _set(None)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"[author_decisions] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
