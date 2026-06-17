"""cross_cluster_arc_progression_aggregate.py 确定性回归测试（零 LLM / 零联网）。

聚焦尚未被 tests/test_self_eval_orphan_consumption.py 覆盖的**核心阶序算法**：
- `stage_order`：Save-the-Cat 阶段 → 序号映射（含 "5:lie" 前缀剥离 / 未知 stage → -1）。
- `load_json`：缺文件 / 坏 JSON → default 兜底（不抛）。
- 逐章磁盘主路径的 STAGE_JUMP / STAGE_REGRESS / STAGE_STAGNATION / STAGE_NOT_UPDATED
  四类 finding 的阈值判定 + 退出码分级（warning→exit2 / 仅 advisory→exit1 / 健康→exit0）。
- cluster 账本分支：`_build_char_stages_from_ledger` 从逐章 arc_stage 重建 + ledger main 路径
  跑通（账本模式不产 NOT_UPDATED）。
- 缺文件 SKIP 早退（exit0）。

已有间接覆盖（不重复）：`_stage_at_ch`、`_read_declared_mckee_beats`、BEAT_STAGE_DIVERGENCE
（见 tests/test_self_eval_orphan_consumption.py）。

main() 含 sys.exit，故两路验证：① in-process 捕 SystemExit 读报告内容（断言 finding/计数）；
② subprocess 跑真 CLI 锚退出码（参照 tests/test_cross_cluster_fate_drift_aggregate.py）。
全程真 import 真调用被测函数，不 mock 被测逻辑。
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
import cross_cluster_arc_progression_aggregate as arc  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_arc_progression_aggregate.py"


# ═══════════════════════ 脚手架 ═══════════════════════

def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write_arc_state(root: Path, characters: dict) -> None:
    """写 character_arc_state.json（逐章磁盘主路径消费）。"""
    _mk_db(root)
    (root / "_数据库" / "character_arc_state.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")


def _mk_chapter_dir(root: Path, ch: int) -> None:
    """造空章目录（main 用它算 max_written_ch）。"""
    (root / "章节" / f"第{ch:03d}章").mkdir(parents=True, exist_ok=True)


def _write_ledger(root: Path, clusters: list) -> None:
    _mk_db(root)
    (root / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters},
                   ensure_ascii=False), encoding="utf-8")


def _run_main_in_process(root: Path, cluster_mode: bool = False) -> dict:
    """in-process 跑 main()，捕 SystemExit，返回最新 arc_progression 报告 JSON。"""
    argv_bak = sys.argv[:]
    env_bak = os.environ.get("CLUSTER_MODE")
    if cluster_mode:
        os.environ["CLUSTER_MODE"] = "1"
    else:
        os.environ.pop("CLUSTER_MODE", None)
    sys.argv = ["arc", str(root)]
    try:
        try:
            arc.main()
        except SystemExit:
            pass
    finally:
        sys.argv = argv_bak
        if env_bak is not None:
            os.environ["CLUSTER_MODE"] = env_bak
        else:
            os.environ.pop("CLUSTER_MODE", None)
    reports = sorted((root / "_数据库" / ".cross_chapter_scan").glob("arc_progression_*.json"))
    assert reports, "未生成 arc_progression 报告"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def _run_cli(root: Path, cluster_mode: bool = False) -> int:
    """subprocess 跑真 CLI，返回退出码（锚 0/1/2 分级）。"""
    env = dict(os.environ)
    if cluster_mode:
        env["CLUSTER_MODE"] = "1"
    else:
        env.pop("CLUSTER_MODE", None)
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(root)],
        capture_output=True, cwd=str(_ROOT), env=env)
    return p.returncode


def _codes(report: dict) -> list:
    return [f["code"] for f in report["findings"]]


# ══════════════════════════════════════════════════════════════════════════
# 纯函数：stage_order
# ══════════════════════════════════════════════════════════════════════════

def test_stage_order_known_stages_monotonic():
    """已知阶段返回 ARC_STAGE_ORDER 序号，主弧序单调递增。"""
    assert arc.stage_order("lie") == 0
    assert arc.stage_order("truth_realized") == 12
    assert arc.stage_order("final_image") == 13
    # 单调：lie < want_threatened < midpoint_revelation < all_is_lost
    assert (arc.stage_order("lie") < arc.stage_order("want_threatened")
            < arc.stage_order("midpoint_revelation") < arc.stage_order("all_is_lost"))


def test_stage_order_prefix_and_unknown():
    """"5:lie" 形式剥前缀取末段；未知 / 空 → -1。"""
    assert arc.stage_order("5:lie") == 0
    assert arc.stage_order("vol2:want_threatened") == 2
    assert arc.stage_order("  LIE  ") == 0  # strip + lower
    assert arc.stage_order("bogus_stage") == -1
    assert arc.stage_order("") == -1
    assert arc.stage_order(None) == -1


# ══════════════════════════════════════════════════════════════════════════
# 纯函数：load_json 兜底
# ══════════════════════════════════════════════════════════════════════════

def test_load_json_missing_and_corrupt_return_default():
    """缺文件 → default；坏 JSON → default（不抛）；合法 → 解析。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        missing = root / "nope.json"
        assert arc.load_json(missing, {"x": 1}) == {"x": 1}
        bad = root / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert arc.load_json(bad, []) == []
        good = root / "good.json"
        good.write_text('{"k": "v"}', encoding="utf-8")
        assert arc.load_json(good, None) == {"k": "v"}


# ══════════════════════════════════════════════════════════════════════════
# 逐章磁盘主路径：STAGE_JUMP / REGRESS / STAGNATION + 退出码
# ══════════════════════════════════════════════════════════════════════════

def test_stage_jump_detected_as_warning_exit2():
    """单次跳 ≥4 阶且 ch_gap<50 → STAGE_JUMP warning → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # lie(0) → all_is_lost(9)：跨 9 阶，章距 10 < 50 → JUMP
        _write_arc_state(root, {"主角": {
            "stages_by_chapter": {"1": "lie", "11": "all_is_lost"},
            "current_stage_at_ch": "11:all_is_lost", "_last_updated_at_ch": 11,
        }})
        _mk_chapter_dir(root, 11)
        report = _run_main_in_process(root)
        assert "STAGE_JUMP" in _codes(report)
        jump = next(f for f in report["findings"] if f["code"] == "STAGE_JUMP")
        assert jump["severity"] == "warning"
        assert jump["stage_gap"] == 9
        assert jump["ch_gap"] == 10
        assert report["summary"]["warning"] >= 1
        assert _run_cli(root) == 2


def test_stage_jump_not_flagged_when_ch_gap_large():
    """跨 ≥4 阶但章距 ≥50（缓慢推进）→ 不算 JUMP（阈值 ch_gap<50）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # lie(0) → all_is_lost(9)，但章距 60 ≥ 50 → 合理慢推，无 JUMP
        _write_arc_state(root, {"主角": {
            "stages_by_chapter": {"1": "lie", "61": "all_is_lost"},
            "current_stage_at_ch": "61:all_is_lost", "_last_updated_at_ch": 61,
        }})
        _mk_chapter_dir(root, 61)
        report = _run_main_in_process(root)
        assert "STAGE_JUMP" not in _codes(report)


def test_stage_regress_detected():
    """阶序倒退（want_threatened → lie）→ STAGE_REGRESS warning。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_arc_state(root, {"主角": {
            "stages_by_chapter": {"5": "want_threatened", "8": "lie"},
            "current_stage_at_ch": "8:lie", "_last_updated_at_ch": 8,
        }})
        _mk_chapter_dir(root, 8)
        report = _run_main_in_process(root)
        reg = next(f for f in report["findings"] if f["code"] == "STAGE_REGRESS")
        assert reg["severity"] == "warning"
        assert reg["from"]["stage"] == "want_threatened"
        assert reg["to"]["stage"] == "lie"


def test_stage_stagnation_advisory_only_exit1():
    """同 stage 跨 ≥10 章 → STAGE_STAGNATION advisory（无 warning）→ exit 1。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # lie 从 ch1 停滞到 ch15（gap 14 ≥ 10），无跳/无倒退
        _write_arc_state(root, {"主角": {
            "stages_by_chapter": {"1": "lie", "15": "lie"},
            "current_stage_at_ch": "15:lie", "_last_updated_at_ch": 15,
        }})
        _mk_chapter_dir(root, 15)
        report = _run_main_in_process(root)
        stag = next(f for f in report["findings"] if f["code"] == "STAGE_STAGNATION")
        assert stag["severity"] == "advisory"
        assert stag["duration"] == 14
        assert report["summary"]["warning"] == 0
        assert report["summary"]["advisory"] >= 1
        assert _run_cli(root) == 1


def test_stagnation_boundary_exactly_10_flagged_9_not():
    """边界：ch_gap 恰好 10（>=10）算停滞；gap 9 不算。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # gap=9 → 不停滞；额外一条 gap=10 → 停滞
        _write_arc_state(root, {
            "甲": {"stages_by_chapter": {"1": "debate", "10": "debate"},  # gap 9
                   "current_stage_at_ch": "10:debate", "_last_updated_at_ch": 10},
            "乙": {"stages_by_chapter": {"1": "fun_and_games", "11": "fun_and_games"},  # gap 10
                   "current_stage_at_ch": "11:fun_and_games", "_last_updated_at_ch": 11},
        })
        _mk_chapter_dir(root, 11)
        report = _run_main_in_process(root)
        stags = [f for f in report["findings"] if f["code"] == "STAGE_STAGNATION"]
        chars = {f["character"] for f in stags}
        assert "乙" in chars       # gap 10 → 停滞
        assert "甲" not in chars   # gap 9 → 不停滞


def test_healthy_progression_exit0():
    """阶序逐步推进（每步 1 阶、章距适中）→ 0 finding → exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_arc_state(root, {"主角": {
            "stages_by_chapter": {"1": "lie", "4": "lie_cracking", "8": "want_threatened"},
            "current_stage_at_ch": "8:want_threatened", "_last_updated_at_ch": 8,
        }})
        _mk_chapter_dir(root, 8)
        report = _run_main_in_process(root)
        assert report["findings"] == []
        assert report["summary"] == {"warning": 0, "advisory": 0}
        assert _run_cli(root) == 0


# ══════════════════════════════════════════════════════════════════════════
# STAGE_NOT_UPDATED：current_stage_at_ch 滞后 > 5 章
# ══════════════════════════════════════════════════════════════════════════

def test_stage_not_updated_when_lagging():
    """已写到 ch20 但 _last_updated_at_ch=5（差 15 > 5）→ STAGE_NOT_UPDATED advisory。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_arc_state(root, {"主角": {
            "stages_by_chapter": {"1": "lie", "3": "lie_cracking"},
            "current_stage_at_ch": "3:lie_cracking", "_last_updated_at_ch": 5,
        }})
        _mk_chapter_dir(root, 20)  # max_written_ch = 20
        report = _run_main_in_process(root)
        nu = next(f for f in report["findings"] if f["code"] == "STAGE_NOT_UPDATED")
        assert nu["severity"] == "advisory"
        assert nu["last_updated_at_ch"] == 5
        assert nu["max_written_ch"] == 20


# ══════════════════════════════════════════════════════════════════════════
# 缺文件 SKIP 早退
# ══════════════════════════════════════════════════════════════════════════

def test_skip_when_arc_state_missing_exit0():
    """character_arc_state.json 不存在（非 cluster 模式）→ [SKIP] 早退 exit 0，不建报告目录。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root)  # 只建 _数据库，不写 arc_state
        assert _run_cli(root) == 0
        # SKIP 提前 exit → 不生成 .cross_chapter_scan
        assert not (root / "_数据库" / ".cross_chapter_scan").exists()


# ══════════════════════════════════════════════════════════════════════════
# cluster 账本分支：_build_char_stages_from_ledger + ledger main 路径
# ══════════════════════════════════════════════════════════════════════════

def test_build_char_stages_from_ledger_reshape():
    """从账本逐章 arc_stage 重建 {char: {stages_by_chapter: {ch_str: stage}}}（跳空 stage / 非 dict）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_ledger(root, [{
            "cluster_id": "cluster_001", "chapter_range": [1, 3],
            "chapters": {
                "1": {"arc_stage": {"主角": "lie", "配角": ""}},          # 配角空 stage 跳过
                "2": {"arc_stage": {"主角": "lie_cracking"}},
                "3": {"arc_stage": "not_a_dict"},                          # 非 dict 跳过
            },
        }])
        env_bak = os.environ.get("CLUSTER_MODE")
        os.environ["CLUSTER_MODE"] = "1"
        try:
            out = arc._build_char_stages_from_ledger(root)
        finally:
            if env_bak is not None:
                os.environ["CLUSTER_MODE"] = env_bak
            else:
                os.environ.pop("CLUSTER_MODE", None)
        assert out["主角"]["stages_by_chapter"] == {"1": "lie", "2": "lie_cracking"}
        assert "配角" not in out  # 唯一一次空 stage 被跳 → 该 char 无任何记录


def test_ledger_mode_detects_jump_no_not_updated():
    """cluster 模式跑 main：账本逐章 arc_stage 出现 JUMP → warning exit2；账本模式不产 NOT_UPDATED。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_ledger(root, [{
            "cluster_id": "cluster_001", "chapter_range": [1, 12], "cluster_end_ch": 12,
            "chapters": {
                "1": {"arc_stage": {"主角": "lie"}},
                "11": {"arc_stage": {"主角": "all_is_lost"}},  # lie(0)→all_is_lost(9) 跨9阶 章距10<50
            },
        }])
        report = _run_main_in_process(root, cluster_mode=True)
        codes = _codes(report)
        assert "STAGE_JUMP" in codes
        # 账本模式分支显式 continue 跳过 NOT_UPDATED 检测（per-chapter 本就逐章同步）
        assert "STAGE_NOT_UPDATED" not in codes
        assert report["max_written_ch"] == 12  # cluster_end_ch 当锚点
        assert _run_cli(root, cluster_mode=True) == 2


def test_ledger_mode_skip_when_no_arc_stage():
    """cluster 模式但账本无 arc_stage 字段 → 回退磁盘分支；磁盘也无 arc_state → SKIP exit0。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 账本有 cluster 但 chapters 无 arc_stage 字段 → ledger_has_field False → 回退磁盘
        _write_ledger(root, [{
            "cluster_id": "cluster_001", "chapter_range": [1, 2],
            "chapters": {"1": {"cjk_count": 3000}, "2": {"cjk_count": 3100}},
        }])
        # 磁盘无 character_arc_state.json → SKIP 早退 exit0
        assert _run_cli(root, cluster_mode=True) == 0


# ═══════════════════════ 自跑入口 ═══════════════════════

def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[cross_cluster_arc_progression_aggregate] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
