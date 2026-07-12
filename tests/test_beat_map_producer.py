"""beat_map_update.py — cluster_beats producer 回归锁（零依赖·零 LLM·零联网）。

# 🔴 2026-06-29 beat_map接通producer

钉死 beat_map 孤儿契约债的修复：plot_structure_scanner 按显式 cluster_id 读
beat_map.cluster_beats[cluster_id]，此前全仓零内容 producer = 死码。本测试锁：

  1. derive_cluster_beats 据 scene_storyboard 确定性派生 起承转合 beat 序列（每 scene 一条·带 beat 名）；
  2. climax_marker / climax_hint 正确标 is_climax；
  3. 空 storyboard → []（向后兼容·调用方据此不写空键）；
  4. write_cluster_beats 写 beat_map.cluster_beats[cid]（保留既有顶层结构）；
  5. update() 从 事件簇.json 读 storyboard 派生回写；
  6. 🔴【防再孤儿核心锁】cluster_choice_apply.apply_choice 端到端（落点）→ beat_map.cluster_beats 非空；
  7. 🔴【scanner 端到端不跳过】plot_structure_scanner.scan_beat(cluster_id) 读到真 beat·warning=None；
  8. C03 fluid：应用 cluster_001 绝不预产 cluster_002 的 beats；
  9. 向后兼容：无 storyboard 的旧 cluster → 不写 cluster_beats 键·scanner 仍优雅降级不崩。

跑法：PYTHONIOENCODING=utf-8 python tests/test_beat_map_producer.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import beat_map_update as bmu  # noqa: E402
import cluster_choice_apply as cca  # noqa: E402
import plot_structure_scanner as pss  # noqa: E402


@contextmanager
def _env(**kv):
    old = {}
    for k, v in kv.items():
        old[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _storyboard(n=4, climax_idx=2):
    sb = []
    names = ["开场", "推进", "高潮", "收束", "余波", "尾声"]
    for i in range(n):
        sc = {"scene_idx": i, "scene": names[i % len(names)], "turn": f"转折{i}"}
        if i == climax_idx:
            sc["climax_marker"] = True
        sb.append(sc)
    return sb


# ============================================================
# 1-3. derive_cluster_beats 纯函数
# ============================================================

def test_derive_basic_four_segments():
    beats = bmu.derive_cluster_beats(_storyboard(4, climax_idx=2))
    assert len(beats) == 4, beats
    # 每条都有 beat 名（scanner 读 b.get("beat")）+ scene_index
    assert all(b.get("beat") for b in beats), beats
    assert [b["scene_index"] for b in beats] == [0, 1, 2, 3], beats
    # 首=起 尾=结
    assert beats[0]["function"] == "ki", beats
    assert beats[-1]["function"] == "ketsu", beats
    # beat 名含场景名（人类可读）
    assert "开场" in beats[0]["beat"], beats


def test_derive_climax_marker_flagged():
    beats = bmu.derive_cluster_beats(_storyboard(4, climax_idx=2))
    flags = [b["is_climax"] for b in beats]
    assert flags == [False, False, True, False], beats
    # climax 及其后 → 转(ten)
    assert beats[2]["function"] == "ten", beats


def test_derive_climax_hint_fallback():
    """无 climax_marker → 退 climax_hint_scene_index。"""
    sb = [{"scene_idx": i, "scene": f"s{i}"} for i in range(5)]
    beats = bmu.derive_cluster_beats(sb, climax_hint=3)
    assert beats[3]["is_climax"] is True, beats
    assert all(b["is_climax"] is False for i, b in enumerate(beats) if i != 3), beats


def test_derive_empty_and_malformed():
    assert bmu.derive_cluster_beats([]) == []
    assert bmu.derive_cluster_beats(None) == []
    assert bmu.derive_cluster_beats("not a list") == []
    # 含非 dict 元素也不崩
    beats = bmu.derive_cluster_beats(["纯字符串场景", {"scene": "正常"}])
    assert len(beats) == 2, beats
    assert all(b.get("beat") for b in beats), beats


def test_derive_single_scene():
    beats = bmu.derive_cluster_beats([{"scene": "唯一场景"}])
    assert len(beats) == 1
    assert beats[0]["function"] == "ki"


# ============================================================
# 4-5. write_cluster_beats + update（落库）
# ============================================================

def test_write_creates_cluster_beats_key():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True)
        beats = bmu.derive_cluster_beats(_storyboard())
        res = bmu.write_cluster_beats(proj, "cluster_001", beats)
        assert res["beats"] == 4, res
        bm = json.loads((proj / "_数据库" / "beat_map.json").read_text(encoding="utf-8"))
        assert "cluster_001" in bm["cluster_beats"], bm
        assert len(bm["cluster_beats"]["cluster_001"]) == 4, bm
        assert bm.get("_cluster_beats_producer"), bm


def test_write_empty_skips_key():
    """空 beats → 不写 cluster_beats 键（向后兼容·scanner 优雅降级）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True)
        res = bmu.write_cluster_beats(proj, "cluster_001", [])
        assert res["beats"] == 0, res
        assert res.get("skipped") == "empty_storyboard", res
        # 不创建带空键的文件（或即便创建也无 cluster_001）
        bm_path = proj / "_数据库" / "beat_map.json"
        if bm_path.exists():
            bm = json.loads(bm_path.read_text(encoding="utf-8"))
            assert "cluster_001" not in bm.get("cluster_beats", {}), bm


def test_write_preserves_existing_beat_map():
    """既有 beat_map 顶层结构（framework/beats[]）不被覆盖。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True)
        (db / "beat_map.json").write_text(
            json.dumps({"schema_version": "v27", "framework": "save_the_cat",
                        "beats": [{"n": 1, "name": "Opening Image"}]},
                       ensure_ascii=False), encoding="utf-8")
        bmu.write_cluster_beats(proj, "cluster_001", bmu.derive_cluster_beats(_storyboard()))
        bm = json.loads((db / "beat_map.json").read_text(encoding="utf-8"))
        assert bm["framework"] == "save_the_cat", bm
        assert bm["beats"][0]["name"] == "Opening Image", bm
        assert "cluster_001" in bm["cluster_beats"], bm


def test_update_from_event_cluster_json():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True)
        (db / "事件簇.json").write_text(json.dumps({
            "clusters": [{"cluster_id": "cluster_001",
                          "scene_storyboard": _storyboard(5, climax_idx=3),
                          "climax_hint_scene_index": 3}]
        }, ensure_ascii=False), encoding="utf-8")
        res = bmu.update(proj, "cluster_001")
        assert res["beats"] == 5, res
        bm = json.loads((db / "beat_map.json").read_text(encoding="utf-8"))
        assert len(bm["cluster_beats"]["cluster_001"]) == 5, bm


def test_update_cluster_not_found_skips():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True)
        (db / "事件簇.json").write_text(json.dumps({"clusters": []}, ensure_ascii=False),
                                       encoding="utf-8")
        res = bmu.update(proj, "cluster_009")
        assert res["beats"] == 0, res
        assert res.get("skipped") == "cluster_not_found", res


# ============================================================
# 6. 🔴 防再孤儿核心锁：cluster_choice_apply（落点）端到端产 cluster_beats
# ============================================================

def _write_choice(proj: Path, key: str, brief: dict) -> Path:
    wal = proj / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    p = wal / f"cluster_{key}_user_choice.json"
    p.write_text(json.dumps({"answer": brief}, ensure_ascii=False), encoding="utf-8")
    return p


def test_cluster_choice_apply_end_to_end_produces_beats():
    """🔴 防再孤儿锁：cluster_choice_apply.apply_choice（唯一确定性落点·outline step6.5 +
    save-state emergence 都走它）后，beat_map.cluster_beats 必非空 → cluster_beats 永远有 producer。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True)
        brief = {
            "cluster_id": "cluster_001",
            "scope_summary": "测试簇",
            "scene_storyboard": _storyboard(4, climax_idx=2),
            "narrative_mode": "in_medias_res",
            "climax_hint_scene_index": 2,
        }
        choice = _write_choice(proj, "001", brief)
        res = cca.apply_choice(proj, "001", choice)
        assert res["cluster_id"] == "cluster_001", res
        # 落点已产 beat_map.cluster_beats
        bm_path = proj / "_数据库" / "beat_map.json"
        assert bm_path.exists(), "落点未产 beat_map.json"
        bm = json.loads(bm_path.read_text(encoding="utf-8"))
        cb = bm.get("cluster_beats", {})
        assert cb.get("cluster_001"), f"cluster_beats[cluster_001] 空 = 孤儿复发! {bm}"
        assert len(cb["cluster_001"]) == 4, cb


def test_cluster_choice_apply_raises_when_beat_update_breaks():
    """有 storyboard 却产不出 beat_map 时必须暴露，不能让主链继续带坏状态前进。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True)
        brief = {
            "cluster_id": "cluster_001",
            "scope_summary": "测试簇",
            "scene_storyboard": _storyboard(2),
        }
        choice = _write_choice(proj, "001", brief)
        original_update = bmu.update
        bmu.update = lambda *_args: {
            "cluster_id": "cluster_001",
            "beats": 0,
            "skipped": "forced_failure",
        }
        try:
            try:
                cca.apply_choice(proj, "001", choice)
                raise AssertionError("beat_map 派生失败应抛出")
            except RuntimeError as e:
                assert "beat_map 派生失败" in str(e)
        finally:
            bmu.update = original_update


# ============================================================
# 7. 🔴 scanner 端到端不跳过：plot_structure_scanner 读到真 beat
# ============================================================

def test_scanner_reads_produced_beats_end_to_end():
    """produce → plot_structure_scanner.scan_beat(cluster_id) 读到真 beat·
    beats_declared_count>0·warning=None（不再「未声明 beat 序列」死码告警）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True)
        brief = {
            "cluster_id": "cluster_001",
            "scope_summary": "x",
            "scene_storyboard": _storyboard(4, climax_idx=2),
            "narrative_mode": "in_medias_res",
        }
        cca.apply_choice(proj, "001", _write_choice(proj, "001", brief))
        rep = pss.scan_beat(proj, "cluster_001", "催化事件突然发生。")
        assert rep["cluster_id"] == "cluster_001", rep
        assert rep["beats_declared_count"] == 4, rep
        assert rep["warning"] is None, rep  # 真有 beat → 不再告警死码
        assert "routing_fallback" not in rep, rep


# ============================================================
# 8. C03 fluid：只产 active cluster·不预设 cluster_002+
# ============================================================

def test_fluid_only_active_cluster_no_preset():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True)
        brief = {"cluster_id": "cluster_001", "scope_summary": "x",
                 "scene_storyboard": _storyboard(3, climax_idx=1)}
        cca.apply_choice(proj, "001", _write_choice(proj, "001", brief))
        bm = json.loads((proj / "_数据库" / "beat_map.json").read_text(encoding="utf-8"))
        cb = bm.get("cluster_beats", {})
        # 只有 cluster_001·绝不预产 cluster_002
        assert list(cb.keys()) == ["cluster_001"], cb


# ============================================================
# 9. 无 storyboard 的 cluster → scanner 明确报告缺失（不崩）
# ============================================================

def test_missing_storyboard_reports_missing_beats():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True)
        (db / "事件簇.json").write_text(json.dumps({
            "clusters": [{"cluster_id": "cluster_001", "scope_summary": "无 storyboard 旧簇"}]
        }, ensure_ascii=False), encoding="utf-8")
        res = bmu.update(proj, "cluster_001")
        assert res["beats"] == 0, res
        # scanner 在无 cluster_beats 时仍优雅返回（warning 非崩溃）
        rep = pss.scan_beat(proj, "cluster_001", "正文")
        assert rep["beats_declared_count"] == 0, rep
        # advisory warning（优雅降级·不崩）
        assert "warning" in rep, rep


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"[OK] {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed" + (f", {failed} FAILED" if failed else ""))
    return failed == 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(0 if _run() else 1)
