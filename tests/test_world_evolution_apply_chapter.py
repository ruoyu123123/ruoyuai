"""world_evolution_apply_chapter 专属回归测试 — 锁尚未被间接覆盖的核心确定性逻辑。

已有间接覆盖（不重复）：
  · tests/test_save_state_feedback_loop.py — append_hub_log (6 例) + apply_one_chapter 的 chapter_hub 接线
  · tests/test_fate_engine_me_pool.py      — respond_threads (5 例) + apply_one_chapter 的 thread_responded 接线

本文件聚焦那两套没覆盖的核心面：
  1. load_changes —— 缺文件 / 坏 JSON / 正常读，全走 graceful {}（绝不 raise）
  2. consume_opportunities —— EO 标 consumed_by_writer + consumed_at_ch / missing / 无世界状态 / 空入参
  3. apply_one_chapter 的 half_apply（H14 幂等跳过修复）—— fate_event 没匹配规则=half / 匹配=非 half /
     幂等跳过不算 half（核心确定性判定：matched_rules 空 且 非 skipped_idempotent）
  4. main() CLI 退出码 —— 预检缺文件 SKIP(0) / 缺 ch 且缺 --cluster → 2 / 正常 → 0 /
     half_apply → 1 / --cluster 展开章范围逐章 tick / --cluster 范围未回填 → 2

零依赖：仅标准库；test_* 无参数；失败 raise AssertionError；tempfile.mkdtemp + utf-8。
CLI 含 sys.exit → 走 subprocess 跑真入口（参照 tests/test_cross_cluster_fate_drift_aggregate.py 范式）。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import world_evolution_apply_chapter as wac  # noqa: E402

_TARGET = _SCRIPTS / "world_evolution_apply_chapter.py"


# ═══════════════════════ 公共脚手架 ═══════════════════════

def _db(project: Path) -> Path:
    d = project / "_数据库"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _mk_world_project(tmp: Path, world: dict | None = None,
                      rules: dict | None = None) -> Path:
    """建一个通过 main 预检（世界状态 + 涟漪规则都在）的最小项目。"""
    db = _db(tmp)
    _write_json(db / "世界状态.json", world if world is not None else {
        "schema_version": "v20.1", "current_world_time": {"ch": 1}})
    _write_json(db / "涟漪规则.json", rules if rules is not None else {"ripple_rules": []})
    return tmp


def _write_changes(project: Path, ch: int, changes: dict) -> None:
    ch_dir = project / "章节" / f"第{ch:03d}章"
    ch_dir.mkdir(parents=True, exist_ok=True)
    _write_json(ch_dir / f"第{ch:03d}章_changes.json", changes)


def _read_world(project: Path) -> dict:
    return json.loads((project / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))


def _run_cli(*args: str):
    """跑真 CLI，返回 (returncode, stdout, stderr)。

    脚本往 Windows 控制台打中文，stdout/stderr 编码随控制台 codepage（GBK）而非 utf-8，
    故捕字节后用 errors='replace' 容错解码（不让 UnicodeDecodeError 把流吞成 None）。
    """
    cp = subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True,  # bytes 模式
    )
    out = cp.stdout.decode("utf-8", errors="replace")
    err = cp.stderr.decode("utf-8", errors="replace")
    return cp.returncode, out, err


# ═══════════════════════ 1. load_changes ═══════════════════════

def test_load_changes_missing_returns_empty():
    """章 _changes.json 不存在 → 返回 {}（不 raise，apply_one_chapter 据此走空 ops）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        assert wac.load_changes(tmp, 5) == {}


def test_load_changes_malformed_json_returns_empty():
    """坏 JSON → JSONDecodeError 被吞，返回 {}（graceful · 不阻断 save-state）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch_dir = tmp / "章节" / "第005章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / "第005章_changes.json").write_text("{ 这不是合法 json ", encoding="utf-8")
        assert wac.load_changes(tmp, 5) == {}


def test_load_changes_valid_roundtrips():
    """正常 _changes.json → 原样 dict 读回（章号补零路径正确）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        payload = {"factual": {"fate_events_triggered": [{"event_id": "V1_ME_001"}]}}
        _write_changes(tmp, 42, payload)
        got = wac.load_changes(tmp, 42)
        assert got == payload
        assert got["factual"]["fate_events_triggered"][0]["event_id"] == "V1_ME_001"


# ═══════════════════════ 2. consume_opportunities ═══════════════════════

def test_consume_opportunities_marks_consumed():
    """申报消费的 EO → consumed_by_writer=true + consumed_at_ch=ch；命中计入 consumed。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d), world={
            "emergent_opportunities": [
                {"id": "EO_001", "type": "twist"},
                {"id": "EO_002", "type": "ally"},
            ]})
        r = wac.consume_opportunities(tmp, 9, ["EO_001"])
        assert r["consumed_count"] == 1
        assert r["consumed"] == ["EO_001"]
        assert r["missing"] == []
        world = _read_world(tmp)
        eo1 = next(o for o in world["emergent_opportunities"] if o["id"] == "EO_001")
        eo2 = next(o for o in world["emergent_opportunities"] if o["id"] == "EO_002")
        assert eo1["consumed_by_writer"] is True
        assert eo1["consumed_at_ch"] == 9
        assert "consumed_by_writer" not in eo2  # 未申报的 EO 不被动


def test_consume_opportunities_missing_id_recorded():
    """申报的 EO id 不存在 → 记 missing 不报错（advisory · 不阻断）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d), world={
            "emergent_opportunities": [{"id": "EO_001"}]})
        r = wac.consume_opportunities(tmp, 9, ["EO_001", "EO_999"])
        assert r["consumed"] == ["EO_001"]
        assert r["missing"] == ["EO_999"]
        assert r["consumed_count"] == 1


def test_consume_opportunities_empty_input_noop():
    """无申报 → 早返回 no-op（不读盘不写盘）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)  # 故意不建世界状态：空入参必须在 load_world 前早返回
        r = wac.consume_opportunities(tmp, 9, [])
        assert r == {"consumed_count": 0, "missing": []}


def test_consume_opportunities_no_world_is_error():
    """有申报但世界状态.json 缺 → 返回 error 字段（不 raise）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _db(tmp)  # 建库目录但不建世界状态.json
        r = wac.consume_opportunities(tmp, 9, ["EO_001"])
        assert "error" in r


# ═══════════════════════ 3. apply_one_chapter · half_apply（H14 核心确定性） ═══════════════════════

def _fate_changes(*event_ids: str) -> dict:
    return {"factual": {"fate_events_triggered": [{"event_id": e} for e in event_ids]},
            "self_eval": {}}


def test_apply_one_chapter_half_when_fate_unmatched():
    """fate_event 在涟漪规则里没匹配 → matched_rules 空 → half_apply=True（exit 1 的根据）。"""
    with tempfile.TemporaryDirectory() as d:
        # 规则池为空 → 任何 fate_event 都匹配不到 → matched_rules=[]
        tmp = _mk_world_project(Path(d), rules={"ripple_rules": []})
        _write_changes(tmp, 5, _fate_changes("V1_ME_001"))
        _summary, half = wac.apply_one_chapter(tmp, 5)
        assert half is True


def test_apply_one_chapter_not_half_when_fate_matched():
    """fate_event 命中涟漪规则 → matched_rules 非空 → half_apply=False。"""
    with tempfile.TemporaryDirectory() as d:
        # 一条 fate_event 类规则匹配 trigger_value=V1_ME_001 + 纯 narrative 后果（必成功落地）
        rules = {"ripple_rules": [{
            "rule_id": "RR_FATE_1",
            "trigger_type": "fate_event",
            "trigger_match": "V1_ME_001",
            "ripples": [{"narrative": "世界因首案震动", "reason": "fate"}],
        }]}
        tmp = _mk_world_project(Path(d), rules=rules)
        _write_changes(tmp, 5, _fate_changes("V1_ME_001"))
        _summary, half = wac.apply_one_chapter(tmp, 5)
        assert half is False
        # fate 涟漪真落了一条 narrative_consequence（锁真实副作用，非套套逻辑）
        world = _read_world(tmp)
        assert any(nc.get("text") == "世界因首案震动"
                   for nc in world.get("narrative_consequences", []))


def test_apply_one_chapter_idempotent_skip_not_half():
    """H14：同 ME 第二次进来被引擎幂等跳过（matched_rules=[] 但 skipped_idempotent）→ 不算 half。

    这是 --cluster 逐章重放同一份 fate_events_triggered 的关键防误报路径：
    跳过返回的空 matched_rules 不得被当成「没匹配规则」误报 exit 1。
    """
    with tempfile.TemporaryDirectory() as d:
        rules = {"ripple_rules": [{
            "rule_id": "RR_FATE_1",
            "trigger_type": "fate_event",
            "trigger_match": "V1_ME_001",
            "ripples": [{"narrative": "首案震动", "reason": "fate"}],
        }]}
        tmp = _mk_world_project(Path(d), rules=rules)
        _write_changes(tmp, 5, _fate_changes("V1_ME_001"))
        _write_changes(tmp, 6, _fate_changes("V1_ME_001"))  # 同 ME 重放
        _s1, half1 = wac.apply_one_chapter(tmp, 5)  # 首次应用
        _s2, half2 = wac.apply_one_chapter(tmp, 6)  # 第二次 → 幂等跳过
        assert half1 is False
        assert half2 is False  # 跳过 ≠ 没匹配规则，不得误报
        # 确认第二次确实走了幂等跳过分支
        ops2 = {o["op"]: o for o in _s2["ops"]}
        fr = ops2["apply_fate_events"]["results"]
        assert fr[0]["skipped_idempotent"] is True
        assert fr[0]["matched_rules"] == []


def test_apply_one_chapter_writes_log_and_runs_all_ops():
    """无 fate_event 的干净章：写日志文件 + 至少含 tick / apply_fate_events / spawn_emergent_scan。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d))
        _write_changes(tmp, 7, {"factual": {}, "self_eval": {}})
        summary, half = wac.apply_one_chapter(tmp, 7)
        assert half is False
        op_names = {o["op"] for o in summary["ops"]}
        assert {"tick", "apply_fate_events", "spawn_emergent_scan"} <= op_names
        log_path = tmp / "_数据库" / ".world_evolution" / "ch007_apply.json"
        assert log_path.exists()
        logged = json.loads(log_path.read_text(encoding="utf-8"))
        assert logged["ch"] == 7


# ═══════════════════════ 4. main() CLI 退出码 ═══════════════════════

def test_cli_skip_when_world_files_missing():
    """预检：世界状态/涟漪规则缺 → SKIP 退出 0（项目未启用世界演化）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _db(tmp)  # 仅建库目录，不建两个文件
        rc, out, err = _run_cli(str(tmp), "5")
        assert rc == 0, err
        assert "SKIP" in out


def test_cli_exit2_when_no_ch_and_no_cluster():
    """既无位置参 ch 又无 --cluster → 退出 2（参数缺失致命）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d))
        rc, out, err = _run_cli(str(tmp))  # 只给 project，不给 ch / --cluster
        assert rc == 2, out + err


def test_cli_exit0_clean_chapter():
    """位置参章级 · 无未匹配 fate_event → 正常退出 0。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d))
        _write_changes(tmp, 5, {"factual": {}, "self_eval": {}})
        rc, out, err = _run_cli(str(tmp), "5")
        assert rc == 0, out + err
        assert (tmp / "_数据库" / ".world_evolution" / "ch005_apply.json").exists()


def test_cli_exit1_on_unmatched_fate_event():
    """有 fate_event 但涟漪规则缺定义 → half_apply → 退出 1 + WARN。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d), rules={"ripple_rules": []})
        _write_changes(tmp, 5, _fate_changes("V1_ME_001"))
        rc, out, err = _run_cli(str(tmp), "5")
        assert rc == 1, out + err
        assert "WARN" in out


def test_cli_cluster_expands_range_and_ticks_each():
    """--cluster：按 事件簇.json.chapter_range 展开逐章 tick；干净 cluster 退出 0。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d))
        _write_json(tmp / "_数据库" / "事件簇.json", {
            "clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}]})
        for ch in (1, 2, 3):
            _write_changes(tmp, ch, {"factual": {}, "self_eval": {}})
        rc, out, err = _run_cli(str(tmp), "--cluster", "cluster_001")
        assert rc == 0, out + err
        # 三章各写一份日志（逐章 tick 真展开）
        ev = tmp / "_数据库" / ".world_evolution"
        for ch in (1, 2, 3):
            assert (ev / f"ch{ch:03d}_apply.json").exists(), f"ch{ch} 日志缺"


def test_cli_cluster_exit2_when_range_unfilled():
    """--cluster 但 chapter_range 未回填（splitter 没切完）→ 退出 2 + FATAL。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d))
        # 有 cluster 但无 chapter_range → cluster_id_to_range 返回 None
        _write_json(tmp / "_数据库" / "事件簇.json", {
            "clusters": [{"cluster_id": "cluster_001"}]})
        rc, out, err = _run_cli(str(tmp), "--cluster", "cluster_001")
        assert rc == 2, out + err
        assert "FATAL" in err
