# -*- coding: utf-8 -*-
"""db_schema_validate.py --post-edit 手改后契约重校验回归网（🔴 2026-06-27 W5）。

根因（completeness critic 揪出的零约束裸奔环节）：用户经 /db 手改子系统 JSON 后无强制重新
schema 校验——手改可破坏契约后直接被 build_manifest 消费（C03/C17 只在 outline/save-state
触发·不覆盖 /db 之后的手改）。本测试锁 revalidate_after_manual 行为：

  · 结构破损（characters list→dict TYPE_MISMATCH）→ exit 2
  · 大势卡 ME 池引用断裂（volumes 定义但 major_events 空 / prereq 悬空）→ exit 2
  · 涟漪规则/事件簇 载荷被清空（touched·非 bare）→ exit 2（C03 LOAD_BEARING_EMPTY）
  · JSON 损坏 / 文件不存在 → exit 2
  · 北极星④⑤：合法稀疏不误拦——空 characters list / bare scaffold / 良构数据 → exit 0

零依赖范式（__main__ 自跑）。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import db_schema_validate as dsv  # noqa: E402
import scaffold_subsystems as scaf  # noqa: E402


def _write(db: Path, name: str, obj) -> Path:
    db.mkdir(parents=True, exist_ok=True)
    p = db / f"{name}.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


# ============ 结构契约（SCHEMA_RULES）============
def test_chars_list_ok():
    """良构人物卡（characters 是 list）→ exit 0。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "人物卡",
                   {"schema_version": 1, "characters": [{"id": "x", "name": "魏无咎", "role": "主"}]})
        assert dsv.revalidate_after_manual(p) == 0


def test_chars_dict_type_mismatch_blocked():
    """🔴 手改把 characters list→dict（TYPE_MISMATCH）→ exit 2（不静默迁移·如实报告）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "人物卡",
                   {"schema_version": 1, "characters": {"1": {"id": "x", "name": "a", "role": "主"}}})
        assert dsv.revalidate_after_manual(p) == 2
        # 北极星：--post-edit 不静默迁移用户手改·盘上仍是 dict
        after = json.loads(p.read_text(encoding="utf-8"))
        assert isinstance(after["characters"], dict)


def test_empty_characters_list_passes():
    """🔴 北极星④⑤：空 characters list（freestyle 项目合法稀疏·build_manifest 从 storyboard 加载）→ exit 0 不误拦。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "人物卡", {"schema_version": "v27", "characters": []})
        assert dsv.revalidate_after_manual(p) == 0


# ============ JSON 损坏 / 文件缺失 ============
def test_json_broken_blocked():
    """手改引入非法 JSON → exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        db.mkdir(parents=True)
        p = db / "人物卡.json"
        p.write_text('{"characters": [,]}', encoding="utf-8")  # 非法
        assert dsv.revalidate_after_manual(p) == 2


def test_missing_file_blocked():
    """文件不存在 → exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        assert dsv.revalidate_after_manual(Path(td) / "_数据库" / "不存在.json") == 2


# ============ 大势卡 ME 池引用完整性 ============
def test_grand_trend_wellformed_ok():
    """良构 ME 池（id+volume·每卷有 finale·prereq 可解析）→ exit 0。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "大势卡", {"volumes": [{"vol": 1}], "major_events": [
            {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False, "prerequisites": []},
            {"id": "ME-V1-02", "volume": 1, "is_volume_finale": True, "prerequisites": ["ME-V1-01"]},
        ]})
        assert dsv.revalidate_after_manual(p) == 0


def test_grand_trend_me_pool_empty_blocked():
    """🔴 手改清空 major_events 但卷已定义 → ME_POOL_EMPTY → exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "大势卡", {"volumes": [{"vol": 1}], "major_events": []})
        assert dsv.revalidate_after_manual(p) == 2


def test_grand_trend_dangling_prereq_blocked():
    """🔴 手改让 prereq 指向不存在 ME → PREREQ_UNRESOLVED → exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "大势卡", {"volumes": [{"vol": 1}], "major_events": [
            {"id": "ME-V1-01", "volume": 1, "is_volume_finale": True, "prerequisites": ["ME-V9-99"]},
        ]})
        assert dsv.revalidate_after_manual(p) == 2


def test_grand_trend_bare_scaffold_advisory_passes():
    """🔴 北极星：bare scaffold（volumes/major_events 皆空·outline 未填）→ advisory·exit 0 不误拦。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "大势卡",
                   {"schema_version": "v27", "volumes": [], "major_events": []})
        assert dsv.revalidate_after_manual(p) == 0


# ============ C03 载荷非空（涟漪规则 / 事件簇）============
def test_ripple_filled_ok():
    """涟漪规则有 ripple_rules → exit 0。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "涟漪规则",
                   {"schema_version": 1,
                    "ripple_rules": [{"id": "r1", "trigger_type": "minor_event",
                                      "trigger_match": "x", "ripples": []}],
                    "_touched": "edit"})
        assert dsv.revalidate_after_manual(p) == 0


def test_ripple_emptied_load_bearing_blocked():
    """🔴 手改清空涟漪规则 rules（touched·非 bare）→ RIPPLE_RULES_EMPTY → exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "涟漪规则",
                   {"schema_version": 1, "rules": [], "ripple_rules": [], "_touched": "manual edit"})
        assert dsv.revalidate_after_manual(p) == 2


def test_ripple_bare_scaffold_escape_passes():
    """🔴 北极星：涟漪规则 == 骨架（bare·outline 未填）→ fluid 起步合法·exit 0 不误拦。"""
    _canonical, skeletons = scaf._load_skeletons()
    skel = skeletons["涟漪规则"]
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "涟漪规则", skel)  # 与骨架完全相同
        assert dsv.revalidate_after_manual(p) == 0


def test_cluster_storyboard_emptied_blocked():
    """🔴 手改清空事件簇 cluster_001 scene_storyboard（touched）→ CLUSTER001_STORYBOARD_EMPTY → exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "事件簇",
                   {"schema_version": 1, "clusters": [{"cluster_id": "cluster_001", "scene_storyboard": []}],
                    "_touched": "edit"})
        assert dsv.revalidate_after_manual(p) == 2


def test_cluster_storyboard_filled_ok():
    """事件簇 cluster_001 storyboard 非空 → exit 0。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "事件簇",
                   {"schema_version": 1, "clusters": [
                       {"cluster_id": "cluster_001", "scene_storyboard": [{"scene": "开场强冲突"}]}]})
        assert dsv.revalidate_after_manual(p) == 0


# ============ 非深 schema 子系统：合法 JSON 即过 ============
def test_non_deep_schema_subsystem_passes():
    """高级子系统（无 SCHEMA_RULES·无载荷标记）合法 JSON → exit 0（只查契约不查内容）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "主角压力档", {"schema_version": "v27", "stress": []})
        assert dsv.revalidate_after_manual(p) == 0


# ============ CLI 入口（--post-edit / --revalidate-after-manual）============
def test_cli_post_edit_flag_blocks():
    """CLI --post-edit 透传：破契约文件 → sys.exit(2)。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "人物卡",
                   {"schema_version": 1, "characters": {"1": {"id": "x", "name": "a", "role": "主"}}})
        argv = ["db_schema_validate.py", td, "--post-edit", str(p)]
        old = sys.argv
        sys.argv = argv
        try:
            code = None
            try:
                dsv.main()
            except SystemExit as e:
                code = e.code
            assert code == 2, code
        finally:
            sys.argv = old


def test_cli_revalidate_alias_passes():
    """CLI --revalidate-after-manual 别名透传：良构文件 → sys.exit(0)。"""
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td) / "_数据库", "人物卡",
                   {"schema_version": 1, "characters": [{"id": "x", "name": "a", "role": "主"}]})
        old = sys.argv
        sys.argv = ["db_schema_validate.py", td, "--revalidate-after-manual", str(p)]
        try:
            code = None
            try:
                dsv.main()
            except SystemExit as e:
                code = e.code
            assert code == 0, code
        finally:
            sys.argv = old


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
