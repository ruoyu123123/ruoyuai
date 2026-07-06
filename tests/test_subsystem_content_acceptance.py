"""test_subsystem_content_acceptance.py — C03 子系统内容验收 + C17 浅漂移哨兵
（🔴 2026-06-27 C03/C17）。

治「34 建齐」只验文件存在·空货架蒙混 + 高级子系统顶层结构漂移哨兵。

覆盖：
  C03 --content / eval_load_bearing / plan_step_gates.check_subsystems(content_check):
    · 裸 emit → 3 个载荷文件 inert(hard·exit2)·31 个非载荷裸骨架 advisory。
    · 填好载荷 → exit0·gates ok=True。
    · 🔴 回归锁：cluster_002+ ME/storyboard 空必须显式豁免（永不命中 hard）。
    · content_check 默认 False（orchestrator 既有调用零回归）。
    · 历史旁路标记不生效 / bare advisory。
  C17 --shallow-drift:
    · MISSING_LIVE_KEY / UNKNOWN_TOP_KEY / 非 live 跳过 / 永不 exit 非0 / 永不写文件。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import scaffold_subsystems as scaf  # noqa: E402
import plan_step_gates as gates  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# fixtures
# ════════════════════════════════════════════════════════════════════
def _emit(db: Path):
    scaf.cmd_emit(["--db-dir", str(db)])


def _set(db: Path, fname: str, mutate):
    p = db / fname
    o = json.loads(p.read_text(encoding="utf-8"))
    mutate(o)
    p.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")


def _fill_load_bearing(db: Path, *, storyboard_clusters=None):
    """填好 3 个载荷文件到「机器可点火」状态。"""
    _set(db, "涟漪规则.json", lambda o: o.__setitem__("ripple_rules", [{"id": "r1"}]))
    _set(db, "大势卡.json",
         lambda o: o.__setitem__("major_events", [{"id": "ME-V1-1", "volume": 1}]))
    clusters = storyboard_clusters or [
        {"cluster_id": "cluster_001", "scene_storyboard": [{"scene": "open"}]}]
    _set(db, "事件簇.json", lambda o: o.__setitem__("clusters", clusters))


# ════════════════════════════════════════════════════════════════════
# eval_load_bearing 纯函数单测
# ════════════════════════════════════════════════════════════════════
def test_resolve_dotpath_and_nonempty():
    obj = {"clusters": [{"scene_storyboard": [1]}, {"scene_storyboard": []}]}
    assert scaf._resolve_dotpath(obj, "clusters.0.scene_storyboard") == [1]
    assert scaf._resolve_dotpath(obj, "clusters.1.scene_storyboard") == []
    assert scaf._resolve_dotpath(obj, "clusters.9.x") is None  # 越界
    assert scaf._resolve_dotpath(obj, "missing.key") is None
    assert scaf._is_nonempty([1]) and not scaf._is_nonempty([])
    assert scaf._is_nonempty({"a": 1}) and not scaf._is_nonempty({})
    assert scaf._is_nonempty("x") and not scaf._is_nonempty("")
    assert scaf._is_nonempty(0) and not scaf._is_nonempty(None)  # 0=有值·None=空


def test_eval_load_bearing_any_of_ripple_alias():
    """涟漪 any_of=[ripple_rules, rules]：任一非空即 ok（兼容 rules 别名 key）。"""
    m = {"code": "RIPPLE_RULES_EMPTY", "any_of": ["ripple_rules", "rules"]}
    assert scaf.eval_load_bearing(m, {"ripple_rules": [], "rules": []}) == ("RIPPLE_RULES_EMPTY", False)
    assert scaf.eval_load_bearing(m, {"ripple_rules": [{"x": 1}]})[1] is True
    assert scaf.eval_load_bearing(m, {"rules": [{"x": 1}]})[1] is True  # 别名命中


# ════════════════════════════════════════════════════════════════════
# C03 --content
# ════════════════════════════════════════════════════════════════════
def test_content_bare_emit_3_inert_hard_exit2():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        assert scaf.cmd_verify(["--db-dir", str(db), "--content"]) == 2


def test_content_filled_load_bearing_exit0():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        _fill_load_bearing(db)
        assert scaf.cmd_verify(["--db-dir", str(db), "--content"]) == 0


def test_content_partial_inert_exit2():
    """只填涟漪+大势·留 cluster_001 storyboard 空 → 仍 inert(hard·exit2)。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        _set(db, "涟漪规则.json", lambda o: o.__setitem__("ripple_rules", [{"id": "r"}]))
        _set(db, "大势卡.json", lambda o: o.__setitem__("major_events", [{"id": "ME"}]))
        # 事件簇仍裸（clusters=[]）→ CLUSTER001_STORYBOARD_EMPTY
        assert scaf.cmd_verify(["--db-dir", str(db), "--content"]) == 2


def test_content_without_flag_is_existence_only_exit0():
    """不带 --content → 维持纯存在性语义（裸 emit 也 exit0）。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        assert scaf.cmd_verify(["--db-dir", str(db)]) == 0


# ════════════════════════════════════════════════════════════════════
# 🔴 回归锁：cluster_002+ ME/storyboard 空必须显式豁免
# ════════════════════════════════════════════════════════════════════
def test_regression_cluster_002_empty_storyboard_exempt():
    """cluster_001 storyboard 填好·cluster_002 storyboard 空 → 不得 inert（fluid 涌现合法）。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        _fill_load_bearing(db, storyboard_clusters=[
            {"cluster_id": "cluster_001", "scene_storyboard": [{"scene": "open"}]},
            {"cluster_id": "cluster_002", "scene_storyboard": []},   # 未涌现·应被豁免
            {"cluster_id": "cluster_003"},                            # 连 key 都没有
        ])
        assert scaf.cmd_verify(["--db-dir", str(db), "--content"]) == 0, \
            "cluster_002+ 空 storyboard 误判 hard（违回归锁/事件簇 fluid 涌现）"


def test_regression_me_pool_nonempty_ignores_future_cluster_me():
    """大势卡 ME 池只含 cluster_001 的 volume-1 ME（无 cluster_002 ME）→ 池非空即 ok。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        _fill_load_bearing(db)  # major_events=[volume-1 ME]·无 cluster_002 ME
        m = {"code": "GRAND_TREND_ME_POOL_EMPTY", "any_of": ["major_events"]}
        o = json.loads((db / "大势卡.json").read_text(encoding="utf-8"))
        assert scaf.eval_load_bearing(m, o)[1] is True  # 池非空·不逐 cluster 校验


# ════════════════════════════════════════════════════════════════════
# C03 plan_step_gates.check_subsystems(content_check=...)
# ════════════════════════════════════════════════════════════════════
def test_gate_content_check_default_false_backward_compat():
    """默认 content_check=False：全 34 件在(含空载荷)即 ok=True（orchestrator 既有调用零回归）。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        r = gates.check_subsystems(db)
        assert r["ok"] and r["gate_level"] == gates.GATE_HARD
        assert gates.check_subsystems(db, content_check=False)["ok"]


def test_gate_content_check_true_inert_blocks_hard():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        r = gates.check_subsystems(db, content_check=True)
        assert not r["ok"]
        assert r["gate_level"] == gates.GATE_HARD and not r["waivable"]
        codes = {c for _n, c in r["inert"]}
        assert codes == {"RIPPLE_RULES_EMPTY", "GRAND_TREND_ME_POOL_EMPTY",
                         "CLUSTER001_STORYBOARD_EMPTY"}


def test_gate_content_check_true_filled_ok():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        _fill_load_bearing(db)
        assert gates.check_subsystems(db, content_check=True)["ok"]


def test_gate_content_check_bypass_marker_does_not_override_inert():
    """历史 .subsystems_bypass.json 标记不得覆盖载荷 hard_gate。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        (db / ".subsystems_bypass.json").write_text("{}", encoding="utf-8")
        r = gates.check_subsystems(db, content_check=True)
        assert not r["ok"]
        assert r["gate_level"] == gates.GATE_HARD


def test_gate_content_check_missing_takes_priority():
    """缺文件优先于内容检查：缺件 → existence hard block（不进 content）。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        (db / "人物卡.json").unlink()
        r = gates.check_subsystems(db, content_check=True)
        assert not r["ok"] and "missing" in r


def test_gate_content_check_three_codes_in_hard_gate_codes():
    """3 码必须落 audit_hub.HARD_GATE_CODES（三方一致的代码侧锚）。"""
    import audit_hub
    for c in ("RIPPLE_RULES_EMPTY", "GRAND_TREND_ME_POOL_EMPTY",
              "CLUSTER001_STORYBOARD_EMPTY"):
        assert c in audit_hub.HARD_GATE_CODES
        assert audit_hub._gate_level_for(c, "error") == "hard_gate"


# ════════════════════════════════════════════════════════════════════
# C17 --shallow-drift（advisory-only）
# ════════════════════════════════════════════════════════════════════
def test_shallow_drift_clean_exit0(capsys):
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        rc = scaf.cmd_verify(["--db-dir", str(db), "--shallow-drift"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "零漂移" in out
        # deferred 子系统跳过
        assert "webnovel_bench_mapping: consumption.status=deferred" in out


def test_shallow_drift_missing_live_key_advisory_exit0(capsys):
    """高级 live 子系统缺顶层 key → ⚠ MISSING_LIVE_KEY·但永不 exit 非0。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        # 枢纽场景 skeleton 顶层含 hubs/rhythm_targets/cluster_hub_log → 删一个
        _set(db, "枢纽场景.json", lambda o: o.pop("hubs", None))
        rc = scaf.cmd_verify(["--db-dir", str(db), "--shallow-drift"])
        out = capsys.readouterr().out
        assert rc == 0  # advisory·永不阻断
        assert "MISSING_LIVE_KEY 枢纽场景.hubs" in out


def test_shallow_drift_unknown_top_key_info_exit0(capsys):
    """项目有 skeleton 无的顶层 key → ℹ UNKNOWN_TOP_KEY（仅 info·fluid 可能合法）·exit0。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        _set(db, "时钟表.json", lambda o: o.__setitem__("emergent_clock_xyz", []))
        rc = scaf.cmd_verify(["--db-dir", str(db), "--shallow-drift"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "UNKNOWN_TOP_KEY 时钟表.emergent_clock_xyz" in out


def test_shallow_drift_never_writes_files():
    """C17 北极星护栏：永不写文件（只读 + stdout）。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        before = {p.name: p.stat().st_mtime_ns for p in db.glob("*.json")}
        before_count = len(list(db.glob("*")))
        scaf.cmd_verify(["--db-dir", str(db), "--shallow-drift"])
        after = {p.name: p.stat().st_mtime_ns for p in db.glob("*.json")}
        assert before == after, "shallow-drift 不得改写任何文件 mtime"
        assert len(list(db.glob("*"))) == before_count, "shallow-drift 不得新建文件"


def test_shallow_drift_meta_keys_excluded(capsys):
    """_ 前缀 meta（_doc/_schema/_metadata…）+ consumption/schema_version 不算漂移。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        _emit(db)
        # 主角压力档 skeleton 带 schema_version·加一个 _ 前缀 key 不应被 UNKNOWN_TOP_KEY 抓
        _set(db, "主角压力档.json", lambda o: o.__setitem__("_private_note", "x"))
        rc = scaf.cmd_verify(["--db-dir", str(db), "--shallow-drift"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "_private_note" not in out


# ════════════════════════════════════════════════════════════════════
# advanced 集合规模哨兵
# ════════════════════════════════════════════════════════════════════
def test_advanced_subsystems_count_16():
    """高级 16 子系统（基础 18 之外）·全在 canonical 34 内。"""
    canonical, _ = scaf._load_skeletons()
    assert len(scaf.ADVANCED_SUBSYSTEMS) == 16
    assert set(scaf.ADVANCED_SUBSYSTEMS).issubset(set(canonical))


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
