"""save_state.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 对 save_state.py 的确定性状态投影健壮性修复
（北极星⑤：硬化数据落地·不干涉模型创作判断）：

  · [L470] apply_changes 时间线段：旧代码 `if "current_time" in tl and ta.get("period"):`
    只校验键存在不校验值类型，随后 `tl["current_time"]["period"] = ...` 做 item assignment。
    若 时间线.json 的 current_time 是 str/None（旧 schema / migrate_data_model_v2 在野遗留 /
    脏数据），二者都能过 `in` 检查 → L471 抛 TypeError。

    该 TypeError 会被 cmd_apply_cluster_changes 逐章 try/except 捕获 → 标该章 failed + 继续 +
    cluster 仍 exit 0，但**本章后续的 time_log 追加 + .wal/applied 摘要写盘被整段跳过** →
    审计丢失 + 数据库部分更新。

    修复：`if isinstance(tl.get("current_time"), dict) and ta.get("period"):`，与本函数
    `isinstance(progress, dict)` 守卫 + 消费端 build_manifest.py `or {}` 防御对齐。

🔴 2026-06-28 审计清理B类：item_transfers → 道具.json holder/status 回库路径已从 apply_changes
移除（属 B 类违规·道具由 novel-archivist→apply_archive.py 写）。原「malformed current_time 不吞掉
后续 item_transfers」用例改测「不吞掉后续 time_log 追加 + summary 写盘」，并钉死 item_transfers
现为不触碰道具状态（道具.json 不再被 writer 自报触碰）。

守护点：
  (1) current_time 为 str/None 时整块跳过、不抛 TypeError、apply_changes 返回 0；
  (2) 该章 time_advance 的 time_log 追加 + .wal/applied 摘要仍写盘（修复的真正价值）；
      且 item_transfers 不再回库 道具.json（B 类清理后不触碰道具状态）；
  (3) current_time 为 dict 的 happy-path 照常滚动 period/cluster；
  (4) current_time 键缺失时不崩、不伪造。

跑法：PYTHONIOENCODING=utf-8 python tests/test_save_state_audit.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state as ss  # noqa: E402
import cluster_lookup  # noqa: E402


# ============================================================
# 公共脚手架：写一个最小项目，跑 apply_changes(root, ch)，回读结果
# ============================================================

def _setup_project(tmp: Path, *, timeline: dict, changes: dict,
                   items: dict | None = None) -> Path:
    """构造最小项目目录，写入 时间线.json / 道具.json / .wal/第{ch}章_parsed.json。

    返回 project root（apply_changes 从 root/_数据库/.wal/第{ch}章_parsed.json 读 changes，
    把结果落地到 root/_数据库/*.json）。
    """
    root = tmp
    db = root / "_数据库"
    wal = db / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    (db / "时间线.json").write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    (db / "进度.json").write_text(
        json.dumps({"completed": 0, "current": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    if items is not None:
        (db / "道具.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return root


def _write_parsed(root: Path, ch: int, changes: dict) -> None:
    cid = cluster_lookup.normalize_cluster_id(ch)
    (root / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [{"cluster_id": cid, "chapter_range": [ch, ch]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    parsed_path = root / "_数据库" / ".wal" / f"第{ch}章_parsed.json"
    parsed_path.write_text(json.dumps({"changes": changes}, ensure_ascii=False), encoding="utf-8")


def _load(root: Path, name: str):
    return json.loads((root / "_数据库" / name).read_text(encoding="utf-8"))


# ============================================================
# [L470] 守护点 1：malformed current_time 不再 TypeError
# ============================================================

def test_current_time_string_does_not_crash():
    """[L470 核心] current_time 是字符串（旧 schema）+ time_advance.period 时不抛 TypeError。

    旧代码 `if "current_time" in tl`：'current_time' 键存在 → 进分支 → "黎明"["period"]=...
    抛 TypeError。修复后 isinstance 守卫使整块跳过。
    """
    with tempfile.TemporaryDirectory() as d:
        root = _setup_project(
            Path(d),
            timeline={"current_time": "黎明", "time_log": []},  # str！旧 schema
            changes={},
        )
        ch = 7
        _write_parsed(root, ch, {"time_advance": {"period": "正午", "elapsed": "半日"}})
        # 修复后不应抛异常；返回 0（有变更）。
        rc = ss.apply_changes(root, ch)
        assert rc == 0, f"expected rc=0, got {rc}"
        # current_time 仍是原字符串（整块被跳过，未被破坏）。
        tl = _load(root, "时间线.json")
        assert tl["current_time"] == "黎明"
        # time_log 仍照常追加（period 块跳过不影响 time_log append）。
        assert tl["time_log"] and tl["time_log"][-1]["ch"] == ch


def test_current_time_none_does_not_crash():
    """current_time 显式为 null（脏数据）+ period 时不抛 TypeError。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_project(
            Path(d),
            timeline={"current_time": None, "time_log": []},  # None！
            changes={},
        )
        ch = 3
        _write_parsed(root, ch, {"time_advance": {"period": "傍晚"}})
        rc = ss.apply_changes(root, ch)
        assert rc == 0, f"expected rc=0, got {rc}"
        tl = _load(root, "时间线.json")
        assert tl["current_time"] is None  # 未被赋值破坏
        assert tl["time_log"][-1]["ch"] == ch


def test_old_code_expression_would_crash_on_malformed():
    """对照：复刻旧代码表达式，证明 str/None 的 current_time 确会触发 TypeError
    （即本修复钉死的真实缺陷·非纸面推断）。"""
    for bad in ("黎明", None):
        tl = {"current_time": bad}
        # 旧守卫 `"current_time" in tl` 对二者都为 True：
        assert ("current_time" in tl) is True
        # 旧 item assignment 必抛 TypeError：
        raised = False
        try:
            tl["current_time"]["period"] = "正午"  # noqa: B018  复刻旧代码
        except TypeError:
            raised = True
        assert raised, f"old-code path should TypeError on current_time={bad!r}"
        # 新守卫则安全跳过：
        assert isinstance(tl.get("current_time"), dict) is False


# ============================================================
# [L470] 守护点 2：malformed current_time 不再吞掉后续 time_log 追加 + summary 写盘
#   （🔴 2026-06-28 审计清理B类：原断言 item_transfers 回库改为断言 time_log + summary）
# ============================================================

def test_malformed_current_time_still_appends_timelog_and_summary():
    """[L470 真正价值·B 类清理后] current_time 是 str 时，本章 time_advance 的 time_log 追加
    + .wal/第{ch}章_applied.json 摘要写盘**仍要发生**（守卫使时间线段不中断 apply_changes）。

    同时钉死新契约：writer changes.item_transfers 不再回库 道具.json（B 类违规已移除·道具由
    novel-archivist→apply_archive.py 写）→ 道具.json 原样不动。
    """
    with tempfile.TemporaryDirectory() as d:
        root = _setup_project(
            Path(d),
            timeline={"current_time": "黎明", "time_log": []},  # 触发旧 bug 的 str
            changes={},
            items={"items": [{"name": "断刃", "holder": "重黎", "status": "完整"}]},
        )
        ch = 9
        _write_parsed(root, ch, {
            "time_advance": {"period": "正午", "elapsed": "半日", "key_events": ["渡江"]},  # 旧代码在此崩
            "item_transfers": [                            # B 类清理后：不触碰道具状态
                {"item": "断刃", "to": "夸父", "new_status": "破损"}
            ],
        })
        rc = ss.apply_changes(root, ch)
        assert rc == 0, f"expected rc=0, got {rc}"
        # current_time 是 str → 守卫整块跳过、不破坏；但 time_log 追加仍发生（段未中断）。
        tl = _load(root, "时间线.json")
        assert tl["current_time"] == "黎明"  # 未被破坏
        assert tl["time_log"] and tl["time_log"][-1]["ch"] == ch
        # 新契约：item_transfers 不再回库 → 道具持有人原样不变。
        items = _load(root, "道具.json")
        knife = next(it for it in items["items"] if it["name"] == "断刃")
        assert knife["holder"] == "重黎", f"item_transfers 不应回库，holder 不应变，实得 {knife['holder']}"
        assert knife["status"] == "完整"
        # .wal/applied 摘要写盘发生（审计未丢失）。
        applied_path = root / "_数据库" / ".wal" / f"第{ch}章_applied.json"
        assert applied_path.exists(), "applied summary must be persisted"
        applied = json.loads(applied_path.read_text(encoding="utf-8"))
        assert any("时间线" in a for a in applied["applied"]), applied["applied"]


# ============================================================
# [L470] 守护点 3：dict 形态 happy-path 仍正确滚动
# ============================================================

def test_current_time_dict_rolls_period_and_cluster():
    """current_time 为 dict 的正常形态：period 更新成新值，当前定位改写为 cluster。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_project(
            Path(d),
            timeline={"current_time": {"period": "黎明", "chapter": 1}, "time_log": []},
            changes={},
        )
        ch = 12
        _write_parsed(root, ch, {"time_advance": {"period": "深夜", "elapsed": "三日"}})
        rc = ss.apply_changes(root, ch)
        assert rc == 0, f"expected rc=0, got {rc}"
        tl = _load(root, "时间线.json")
        assert tl["current_time"]["period"] == "深夜"
        assert "chapter" not in tl["current_time"]
        assert tl["current_time"]["cluster"] == "cluster_012"
        assert tl["current_time"]["chapter_in_cluster"] == ch


def test_current_time_dict_without_period_in_changes_only_logs():
    """current_time 是 dict 但 time_advance 无 period → 不动 current_time，只 append time_log。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_project(
            Path(d),
            timeline={"current_time": {"period": "黎明", "cluster": "cluster_001"}, "time_log": []},
            changes={},
        )
        ch = 5
        _write_parsed(root, ch, {"time_advance": {"elapsed": "片刻"}})  # 无 period
        rc = ss.apply_changes(root, ch)
        assert rc == 0, f"expected rc=0, got {rc}"
        tl = _load(root, "时间线.json")
        # current_time 不变（period 缺失 → 守卫第二条件 ta.get("period") 为假）。
        assert tl["current_time"] == {"period": "黎明", "cluster": "cluster_001"}
        assert tl["time_log"][-1]["ch"] == ch


# ============================================================
# [L470] 守护点 4：current_time 键缺失安全
# ============================================================

def test_current_time_key_absent_is_safe():
    """时间线.json 无 current_time 键 + period → 不崩、不伪造 current_time。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_project(
            Path(d),
            timeline={"time_log": []},  # 没有 current_time
            changes={},
        )
        ch = 4
        _write_parsed(root, ch, {"time_advance": {"period": "正午"}})
        rc = ss.apply_changes(root, ch)
        assert rc == 0, f"expected rc=0, got {rc}"
        tl = _load(root, "时间线.json")
        # 守卫 isinstance(tl.get("current_time"), dict) 为 False → 不创建 current_time。
        assert "current_time" not in tl
        assert tl["time_log"][-1]["ch"] == ch


def _raise_runtime(name):
    def _inner(*_args, **_kwargs):
        raise RuntimeError(f"{name} boom")
    return _inner


@pytest.mark.parametrize("failing_helper", [
    "_writeback_cluster_progress",
    "_mark_cluster_me_completed",
    "_register_brief_foreshadowings",
    "_apply_foreshadower_payoffs",
])
def test_cmd_apply_cluster_changes_blocks_on_required_projection_failures(monkeypatch, failing_helper):
    """required 状态投影任一环节异常 → cmd_apply_cluster_changes 必须返回 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "_数据库" / ".wal").mkdir(parents=True, exist_ok=True)
        with monkeypatch.context() as m:
            m.setattr(ss, "_get_cluster_chapter_range", lambda _root, _key: [1])
            m.setattr(ss, "cmd_parse", lambda _root, _ch: 0)
            m.setattr(ss, "apply_changes", lambda _root, _ch: 0)
            m.setattr(ss, "_run_writer_truth_check", lambda _root, _chapters: {"lies_total": 0, "errors": []})
            for helper in (
                "_writeback_cluster_progress",
                "_mark_cluster_me_completed",
                "_register_brief_foreshadowings",
                "_apply_foreshadower_payoffs",
            ):
                m.setattr(ss, helper, lambda *_args: None)
            m.setattr(ss, failing_helper, _raise_runtime(failing_helper))

            rc = ss.cmd_apply_cluster_changes(root, "001")

        assert rc == 2
        summary_path = root / "_数据库" / ".wal" / "001_apply_cluster.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["writer_truth_check"] is None
        assert failing_helper in summary["fatal_error"]


# ============================================================
# 零依赖 __main__ runner
# ============================================================

if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    failed = 0
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
    print(f"\n{passed}/{passed + failed} passed"
          + (f", {failed} FAILED" if failed else ""))
    sys.exit(1 if failed else 0)
