"""阶段1：build_manifest._collect_author_rhythm_signature 注入测试（北极星①+⑤）。

钉死作者叙事节奏指纹（序列级骨）的 writer 注入：
  · env RHYTHM_INJECT_MODE 默认 active（2026-06-13 终验后放量）→ 注入 directives
  · shadow（调试）→ 算+落盘但不注入（零回归）
  · active → 注入 directives（节拍转移/翻转率/张力后段保持/钩子兑现/推进密度）
  · 永远 advisory · 全 dict 无 hard_gate
  · 缺 narrative_rhythm → None（零回归）
  · active 时接线进 full build_manifest() dict + _cache_layout STATIC 段
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402
from cluster_summary_fixtures import write_cluster_summary  # noqa: E402

_RHYTHM = {
    "beat_transition_matrix": {
        "推进→缓冲": {"count": 8, "prob": 0.4},
        "缓冲→揭示": {"count": 5, "prob": 0.25}},
    "scene_turn_ratio": 0.72,
    "tension_trajectory": {"post_climax_retention": 0.58,
                           "inflection_count_mean": 2.3, "dominant_emotion_shape": "升升"},
    "hook_type_distribution": {"对话断句钩": 6, "情绪高潮钩": 3},
    "hook_payoff_gap_median": 3,
    "propulsion_density": {"三章一爆": 4},
}


def _mk(tmp: Path, *, rhythm=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    style = {"author": "测试作者", "quantitative": {"sentence_length": {"mean": 28}}}
    if rhythm is not None:
        style["narrative_rhythm"] = rhythm
    (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return tmp


def _set_mode(m):
    if m is None:
        os.environ.pop("RHYTHM_INJECT_MODE", None)
    else:
        os.environ["RHYTHM_INJECT_MODE"] = m


def test_active_default_injects():
    """2026-06-13 切 active 放量：默认（无 env）→ active → 注入 directives（零回归仅 off/旧档）。"""
    _set_mode(None)
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk(Path(d), rhythm=_RHYTHM)
        s = bm.DatabaseScanner(tmp, 1)
        r = bm._collect_author_rhythm_signature(s)
        assert r is not None
        assert "后段保持" in "\n".join(r["directives"])  # 治过早收束


def test_shadow_explicit_no_injection():
    """显式 shadow（调试）→ 返回 None（不注入·零回归），但落盘摘要。"""
    _set_mode("shadow")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk(Path(d), rhythm=_RHYTHM)
            s = bm.DatabaseScanner(tmp, 1)
            assert bm._collect_author_rhythm_signature(s) is None
            assert (tmp / "_数据库" / ".rhythm_signature" / "ch_001.json").exists()  # 落盘
    finally:
        _set_mode(None)


def test_active_injects_directives():
    """active → 注入·directives 覆盖 5 类节奏信号。"""
    _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk(Path(d), rhythm=_RHYTHM)
            s = bm.DatabaseScanner(tmp, 1)
            r = bm._collect_author_rhythm_signature(s)
            assert r is not None
            blob = "\n".join(r["directives"])
            assert "节拍转移" in blob
            assert "翻转率" in blob
            assert "后段保持" in blob       # 治过早收束
            assert "兑现" in blob
            assert r["source"] == "narrative_rhythm"
    finally:
        _set_mode(None)


def test_off_returns_none():
    _set_mode("off")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk(Path(d), rhythm=_RHYTHM)
            s = bm.DatabaseScanner(tmp, 1)
            assert bm._collect_author_rhythm_signature(s) is None
    finally:
        _set_mode(None)


def test_missing_rhythm_none():
    """无 narrative_rhythm（旧档）→ None（零回归）。"""
    _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk(Path(d), rhythm=None)
            s = bm.DatabaseScanner(tmp, 1)
            assert bm._collect_author_rhythm_signature(s) is None
    finally:
        _set_mode(None)


def test_advisory_never_hard_gate():
    """全 dict 无 hard_gate（北极星⑤·序列级也是顾问非法官）。"""
    _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk(Path(d), rhythm=_RHYTHM)
            s = bm.DatabaseScanner(tmp, 1)
            r = bm._collect_author_rhythm_signature(s)
            assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        _set_mode(None)


def test_wired_into_manifest_and_cache():
    """active 时接线进 full build_manifest() dict + _cache_layout STATIC。"""
    _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk(Path(d), rhythm=_RHYTHM)
            write_cluster_summary(tmp, [])
            (tmp / "_数据库" / "人物卡.json").write_text(
                json.dumps({"characters": [{"id": "a", "name": "甲", "role": "主角"}]},
                           ensure_ascii=False), encoding="utf-8")
            (tmp / "_数据库" / "事件簇.json").write_text(
                json.dumps({"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 4]}]},
                           ensure_ascii=False), encoding="utf-8")
            (tmp / "_数据库" / "进度.json").write_text(json.dumps({
                "volumes": [{"vol": 1, "title": "一", "chapter_range": [1, 8]}],
                "cluster_blueprint": {"cluster_001": {"chapter_range": [1, 4],
                    "scene_storyboard": [{"ch": 1, "characters": ["甲"], "key_events": ["开局"],
                                          "scene_type": ["悬疑"], "summary": "甲值夜班"}]}},
            }, ensure_ascii=False), encoding="utf-8")
            m = bm.build_manifest(tmp, 1)
            assert m.get("author_rhythm_signature") is not None
            assert "author_rhythm_signature" in m["_cache_layout"].get("STATIC_99_cacheable", [])
    finally:
        _set_mode(None)


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
    print(f"[narrative_rhythm_signature] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
