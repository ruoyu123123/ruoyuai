# -*- coding: utf-8 -*-
"""fate_dice 命运抽签回归网（第四轮 Workflow #5·固化 2 历史回归·2026-06-17）。

build_manifest 注入 writer manifest 的命运抽签·git edd8dcd 两处文档化修复落地无测试守护：
  · _filter_event min_ch/max_ch==0 falsy 短路修复（is not None·让 0 也参与边界过滤）
  · draw sum(weights)<=0 退均匀（weights=None·防 random.choices ValueError 崩溃）

覆盖纯函数 _check_world_required / _filter_event + draw 核心。零依赖范式（__main__ 自跑）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import fate_dice as fd  # noqa: E402


# ============ _check_world_required（纯函数）============
def test_world_required_empty_true():
    assert fd._check_world_required({}, {}) is True


def test_world_required_gte_pass():
    assert fd._check_world_required(
        {"factions_state": {"X": {"power": 90}}}, {"factions_state.X.power": ">= 80"}) is True


def test_world_required_gte_fail():
    assert fd._check_world_required(
        {"factions_state": {"X": {"power": 70}}}, {"factions_state.X.power": ">= 80"}) is False


def test_world_required_missing_field_false():
    """缺字段（_resolve_dotted → None）→ False。"""
    assert fd._check_world_required({}, {"a.b.c": ">= 1"}) is False


def test_world_required_no_operator_equality():
    """无运算符 → 等值比较。"""
    assert fd._check_world_required({"phase": "war"}, {"phase": "war"}) is True
    assert fd._check_world_required({"phase": "peace"}, {"phase": "war"}) is False


# ============ _filter_event（纯函数·min_ch=0 边界核心回归）============
def _ev(eid="e1", **cf):
    return {"event_id": eid, "context_filter": cf}


def test_filter_min_ch_zero_not_short_circuited():
    """🔴 回归：min_ch=0 不被 falsy 短路（is not None 修复）·ch=0 时 0<0 False → 不过滤 → 过。"""
    assert fd._filter_event(_ev(min_ch=0), 0, "", "", {}, set()) is True
    assert fd._filter_event(_ev(min_ch=5), 3, "", "", {}, set()) is False  # 3<5 过滤掉


def test_filter_max_ch_zero_boundary():
    """🔴 回归：max_ch=0·ch=1 → 1>0 → 过滤掉（0 参与边界·非被 falsy 跳过）。"""
    assert fd._filter_event(_ev(max_ch=0), 1, "", "", {}, set()) is False
    assert fd._filter_event(_ev(max_ch=0), 0, "", "", {}, set()) is True  # ch=0 不超 max_ch=0


def test_filter_recent_drawn_dedup():
    assert fd._filter_event(_ev("e1"), 5, "", "", {}, {"e1"}) is False


def test_filter_scene_type():
    assert fd._filter_event(_ev(scene_types=["battle"]), 5, "dialogue", "", {}, set()) is False
    assert fd._filter_event(_ev(scene_types=["battle"]), 5, "battle", "", {}, set()) is True


def test_filter_required_pov():
    assert fd._filter_event(_ev(required_pov="主角"), 5, "", "配角", {}, set()) is False
    assert fd._filter_event(_ev(required_pov="主角"), 5, "", "主角", {}, set()) is True


# ============ draw（fixture + monkeypatch·weights 全0 退均匀核心回归）============
def _mk_pool(td, events, world=None):
    db = Path(td) / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件池.json").write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
    (db / "世界状态.json").write_text(json.dumps(world or {}, ensure_ascii=False), encoding="utf-8")
    return Path(td)


def test_draw_weights_all_zero_uniform():
    """🔴 回归：weights 全 0 → sum<=0 → 退均匀(weights=None)·不崩 ValueError。"""
    orig = fd.random.choices
    captured = {}

    def fake_choices(pop, weights=None, k=1):
        captured["weights"] = weights
        return [pop[0]]

    fd.random.choices = fake_choices
    try:
        with tempfile.TemporaryDirectory() as td:
            root = _mk_pool(td, [_ev("e1", weight=0), _ev("e2", weight=0)])
            out = fd.draw(root, 5)
            assert captured["weights"] is None, captured  # 全 0 退均匀
            assert out["drawn"]["event_id"] == "e1"
    finally:
        fd.random.choices = orig


def test_draw_positive_weights_kept():
    """weights 有正值 → 保留（不退均匀）。"""
    orig = fd.random.choices
    captured = {}

    def fake_choices(pop, weights=None, k=1):
        captured["weights"] = weights
        return [pop[0]]

    fd.random.choices = fake_choices
    try:
        with tempfile.TemporaryDirectory() as td:
            root = _mk_pool(td, [_ev("e1", weight=3), _ev("e2", weight=1)])
            fd.draw(root, 5)
            assert captured["weights"] == [3, 1], captured
    finally:
        fd.random.choices = orig


def test_draw_no_candidates():
    """全部过滤 / 空池 → drawn None。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_pool(td, [])
        out = fd.draw(root, 5)
        assert out["drawn"] is None


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
