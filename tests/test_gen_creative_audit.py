"""gen_creative deterministic regression tests.

The old outline_card mode has been removed from the public CLI. Direction cards
must flow through cluster_emergence_engine + novel-outline-planner + cluster
user_choice artifacts. This test keeps that removal locked while preserving the
volume_arc structural guards.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import gen_creative as gc  # noqa: E402
import gen_creative_volume_arc as gva  # noqa: E402  volume_arc 实现（2026-07-07 从 gen_creative 拆出）


# ════════════════════════════════════════════════════════════════
# Helper
# ════════════════════════════════════════════════════════════════

def _run_main(argv: list[str]) -> int | None:
    """Run main() with argv and return SystemExit code; normal return is None."""
    old_argv = sys.argv[:]
    sys.argv = argv
    try:
        gc.main()
        return None
    except SystemExit as e:
        return int(e.code or 0)
    finally:
        sys.argv = old_argv


# ════════════════════════════════════════════════════════════════
# Old entry hard rejection: outline_card is no longer public CLI
# ════════════════════════════════════════════════════════════════

def test_outline_card_mode_removed_exit2():
    """旧单章走向卡入口必须被 argparse 硬拒，不能绕过 cluster 选择链路。"""
    code = _run_main(["gen_creative.py", "--mode", "outline_card", "--count", "2"])
    assert code == 2, f"outline_card 旧入口应被硬拒 exit 2，实得 {code}"


def test_voice_sample_mode_removed_exit2():
    code = _run_main(["gen_creative.py", "--mode", "voice_sample"])
    assert code == 2


# ════════════════════════════════════════════════════════════════
# Bug 2（L459-460）：_emit_volume_arc_to_db major_events isinstance 守卫
# ════════════════════════════════════════════════════════════════

def _emit(tmp: Path, data: dict) -> dict:
    """调 _emit_volume_arc_to_db 落盘并读回 大势卡.json（断言不崩 + 内容正确）。"""
    proj = tmp / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    p_major, p_cluster = gva._emit_volume_arc_to_db(proj, data, rhythm="", framework="")
    assert p_major.exists() and p_cluster.exists(), "大势卡/事件簇 未落盘"
    return json.loads(p_major.read_text(encoding="utf-8"))


def test_emit_volume_arc_filters_non_dict_major_events():
    """major_events 含裸串 / None / 整数（gen-model 软约束下合法可达）：
    修前 `{**'str'}` TypeError: object is not a mapping → 崩建书单点调用；
    修后 isinstance(me, dict) 过滤掉坏元素，只投影合法 dict ME。"""
    with tempfile.TemporaryDirectory() as td:
        data = {
            "story_destiny": {"final_image": "末法最后一人"},
            "volumes": [{"vol": 1, "title": "卷一"}],
            "major_events": [
                {"id": "ME-V1-01", "volume": 1, "summary": "开局", "is_volume_finale": False},
                "我是一个不该出现的裸串 ME",   # 非 dict → 必须被过滤
                None,                          # 非 dict → 必须被过滤
                42,                            # 非 dict → 必须被过滤
                {"id": "ME-V1-02", "volume": 1, "status": "active", "is_volume_finale": True},
            ],
            "cluster_001": {"scope_summary": "倒叙开场"},
        }
        major = _emit(Path(td), data)   # 不抛 TypeError 即过 bug2
        mes = major["major_events"]
        # 只剩 2 条合法 dict（裸串/None/int 被剔除）
        assert len(mes) == 2, f"非 dict ME 未被过滤，实得 {len(mes)} 条: {mes}"
        ids = {m["id"] for m in mes}
        assert ids == {"ME-V1-01", "ME-V1-02"}
        # 缺 status 的补 pending；自带 status 的保留
        by_id = {m["id"]: m for m in mes}
        assert by_id["ME-V1-01"]["status"] == "pending"
        assert by_id["ME-V1-02"]["status"] == "active"


def test_emit_volume_arc_all_dict_major_events_preserved():
    """控制组：全 dict major_events → 全部保留 + status 默认 pending（守卫不误伤正常路径）。"""
    with tempfile.TemporaryDirectory() as td:
        data = {
            "story_destiny": {},
            "volumes": [],
            "major_events": [
                {"id": "ME-A", "volume": 1, "is_volume_finale": False},
                {"id": "ME-B", "status": "done", "volume": 1, "is_volume_finale": False},
                {"id": "ME-C", "volume": 1, "is_volume_finale": True},
            ],
            "cluster_001": {},
        }
        major = _emit(Path(td), data)
        mes = major["major_events"]
        assert len(mes) == 3, f"合法 ME 被误删: {mes}"
        st = {m["id"]: m["status"] for m in mes}
        assert st == {"ME-A": "pending", "ME-B": "done", "ME-C": "pending"}


def test_emit_volume_arc_empty_and_missing_major_events():
    """major_events 缺失 / 为空列表：ME 池是当前卷大势方向的唯一来源，
    _normalize_me_pool 对空池硬拒 ValueError，emit 不落盘（边界）。"""
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError, match="不能为空"):
            _emit(Path(td) / "a", {"major_events": []})
        assert not (Path(td) / "a" / "proj" / "_数据库" / "大势卡.json").exists()
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError, match="不能为空"):
            _emit(Path(td) / "b", {})   # 无 major_events 键 → .get 默认 []
        assert not (Path(td) / "b" / "proj" / "_数据库" / "大势卡.json").exists()


# ════════════════════════════════════════════════════════════════
# import 守卫
# ════════════════════════════════════════════════════════════════

def test_module_imports():
    """模块可 import（2 修后无语法/引用错）。"""
    assert hasattr(gc, "main")
    assert hasattr(gva, "_emit_volume_arc_to_db")


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
    print(f"\n{'ALL OK' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
