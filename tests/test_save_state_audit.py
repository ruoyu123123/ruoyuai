"""save_state.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 对 save_state.py 的确定性状态投影健壮性修复
（北极星⑤：硬化数据落地·不干涉模型创作判断）：

  · [L470] apply_changes 时间线段：旧代码 `if "current_time" in tl and ta.get("period"):`
    只校验键存在不校验值类型，随后 `tl["current_time"]["period"] = ...` 做 item assignment。
    若 时间线.json 的 current_time 是 str/None（旧 schema / migrate_data_model_v2 在野遗留 /
    脏数据），二者都能过 `in` 检查 → L471 抛 TypeError。

    该 TypeError 会被 cmd_apply_cluster_changes 逐章 try/except（L880-889）捕获 →
    标该章 failed + 继续 + cluster 仍 exit 0，但**本章后续的 道具.json（item_transfers）落地
    + .wal/applied 摘要写盘被整段跳过** → 道具持有人静默不更新（可能触发 ITEM_HOLDER_ABSENT
    误判）+ 审计丢失 + 数据库部分更新。

    修复：`if isinstance(tl.get("current_time"), dict) and ta.get("period"):`，与本函数 L443
    `isinstance(progress, dict)` 守卫 + 消费端 build_manifest.py:469 `or {}` 防御对齐。

守护点：
  (1) current_time 为 str/None 时整块跳过、不抛 TypeError、apply_changes 返回 0；
  (2) 该章后续的 item_transfers 仍落地、.wal/applied 摘要仍写盘（修复的真正价值）；
  (3) current_time 为 dict 的 happy-path 照常滚动 period/chapter；
  (4) current_time 键缺失时不崩、不伪造。

跑法：PYTHONIOENCODING=utf-8 python tests/test_save_state_audit.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state as ss  # noqa: E402


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
    if items is not None:
        (db / "道具.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return root


def _write_parsed(root: Path, ch: int, changes: dict) -> None:
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
# [L470] 守护点 2：malformed current_time 不再吞掉后续 item_transfers + summary 写盘
# ============================================================

def test_malformed_current_time_still_applies_item_transfers_and_summary():
    """[L470 真正价值] current_time 是 str 时，本章后续的 道具.json（item_transfers）落地
    + .wal/第{ch}章_applied.json 摘要写盘**仍要发生**。

    旧代码 L471 TypeError 会在时间线段中断 apply_changes，整段后续（道具落地、summary 写盘）
    被跳过——cmd_apply_cluster_changes 的 try/except 只把它标 failed 但 cluster 仍 exit 0，
    造成道具持有人静默不更新 + 审计丢失。修复后该路径不再中断。
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
            "time_advance": {"period": "正午"},          # 旧代码在此崩
            "item_transfers": [                            # 旧代码崩后这块被跳过
                {"item": "断刃", "to": "夸父", "new_status": "破损"}
            ],
        })
        rc = ss.apply_changes(root, ch)
        assert rc == 0, f"expected rc=0, got {rc}"
        # 道具持有人已更新（修复后不再被时间线段崩溃吞掉）。
        items = _load(root, "道具.json")
        knife = next(it for it in items["items"] if it["name"] == "断刃")
        assert knife["holder"] == "夸父", f"holder should update to 夸父, got {knife['holder']}"
        assert knife["status"] == "破损"
        # .wal/applied 摘要写盘发生（审计未丢失）。
        applied_path = root / "_数据库" / ".wal" / f"第{ch}章_applied.json"
        assert applied_path.exists(), "applied summary must be persisted"
        applied = json.loads(applied_path.read_text(encoding="utf-8"))
        assert any("断刃" in a for a in applied["applied"]), applied["applied"]


# ============================================================
# [L470] 守护点 3：dict 形态 happy-path 仍正确滚动
# ============================================================

def test_current_time_dict_rolls_period_and_chapter():
    """current_time 为 dict 的正常形态：period 更新成新值、chapter 滚到本章。"""
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
        assert tl["current_time"]["chapter"] == ch


def test_current_time_dict_without_period_in_changes_only_logs():
    """current_time 是 dict 但 time_advance 无 period → 不动 current_time，只 append time_log。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_project(
            Path(d),
            timeline={"current_time": {"period": "黎明", "chapter": 1}, "time_log": []},
            changes={},
        )
        ch = 5
        _write_parsed(root, ch, {"time_advance": {"elapsed": "片刻"}})  # 无 period
        rc = ss.apply_changes(root, ch)
        assert rc == 0, f"expected rc=0, got {rc}"
        tl = _load(root, "时间线.json")
        # current_time 不变（period 缺失 → 守卫第二条件 ta.get("period") 为假）。
        assert tl["current_time"] == {"period": "黎明", "chapter": 1}
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
