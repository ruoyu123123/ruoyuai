#!/usr/bin/env python3
"""损坏 plan 可见性测试（2026-06-13）。

根治「损坏 plan 静默消失」：plans 目录里一个非法 JSON 文件以前会被
find_active_plans 的 except 分支静默 continue 掉——GUI Plan 续跑页看不见、
也清不掉。现在：
- find_active_plans append {"corrupt": True, "path", "plan_id", "plan": {}}
- runner.list_resumable 透传 corrupt 条目（command="损坏"/progress="?"）
- runner.clear_corrupt_plan 把文件移到同目录 .corrupt/ 子目录（可恢复·
  运行中拒绝）

零依赖·tempfile 沙箱·不碰真 plans 目录。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    sys.path.insert(0, p)

from core.gui import runner as gr  # noqa: E402
from core.gui import state as gs  # noqa: E402
import plan_tracker as pt  # noqa: E402


# ============ 沙箱（照 test_gui_concurrency_stress._Sandbox 范式） ============
class _Sandbox:
    """临时 plans 目录沙箱：1 个正常活跃 plan + 1 个非法 JSON（损坏 plan）。"""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.plans = self.tmp / ".plans"
        for d in (self.plans, self.tmp / "novels", self.tmp / "styles"):
            d.mkdir(parents=True)
        # 正常活跃 plan（未 completed/aborted·有 scripts → resumable）
        self.good_id = "测试书_001_cluster-write_good"
        (self.plans / f"{self.good_id}.json").write_text(json.dumps({
            "id": self.good_id,
            "command": "cluster-write",
            "project": "测试书",
            "key": "001",
            "steps": [
                {"n": 1, "name": "s1", "status": "completed",
                 "scripts": ["echo 1"]},
                {"n": 2, "name": "s2", "status": "pending",
                 "scripts": ["echo 2"]},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        # 损坏 plan：非法 JSON（模拟写盘中断/手滑截断）
        self.bad_id = "测试书_001_cluster-write_bad"
        self.bad_file = self.plans / f"{self.bad_id}.json"
        self.bad_file.write_text("{这不是合法 JSON!!", encoding="utf-8")
        self._saved = {}

    def __enter__(self):
        for k, v in [("GLOBAL_PLANS_DIR", self.plans),
                     ("PROJECTS_DIR", self.tmp / "novels"),
                     ("STYLES_DIR", self.tmp / "styles"),
                     ("ATTEST_KEY_PATH", self.plans / ".attest_key")]:
            self._saved[k] = getattr(pt, k)
            setattr(pt, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            setattr(pt, k, v)


# ============ ① find_active_plans：corrupt 条目浮出 ============
def test_find_active_plans_surfaces_corrupt_entry():
    """非法 JSON 不再静默消失：corrupt 条目带 path/plan_id/空 plan dict。"""
    with _Sandbox() as sb:
        entries = pt.find_active_plans()
        corrupt = [e for e in entries if e.get("corrupt")]
        normal = [e for e in entries if not e.get("corrupt")]
        # 损坏条目出现，结构齐全
        assert len(corrupt) == 1, f"应恰有 1 个 corrupt 条目，实得 {len(corrupt)}"
        c = corrupt[0]
        assert c["plan_id"] == sb.bad_id            # = 文件名 stem
        assert c["path"].endswith(f"{sb.bad_id}.json")
        assert c["plan"] == {}                       # 既有消费者 tolerant 锚
        # 既有消费者范式 entry["plan"].get(...) 不抛（hook/测试都这么用）
        assert c["plan"].get("project") is None
        # 正常 plan 不受影响（结构不变·无 corrupt 键真值）
        assert len(normal) == 1
        assert normal[0]["plan"]["id"] == sb.good_id
        assert not normal[0].get("corrupt")
        assert "tampered" in normal[0]               # 原有字段保持


# ============ ② list_resumable：corrupt 透传 ============
def test_list_resumable_passthrough_corrupt_and_keeps_normal():
    """corrupt 条目透传 GUI（command=损坏/progress=?/resumable=False/path）。"""
    with _Sandbox() as sb:
        runner = gr.PipelineRunner(gs.AppState())
        items = runner.list_resumable()
        corrupt = [it for it in items if it.get("corrupt")]
        normal = [it for it in items if not it.get("corrupt")]
        assert len(corrupt) == 1
        c = corrupt[0]
        assert c["plan_id"] == sb.bad_id
        assert c["command"] == "损坏"
        assert c["progress"] == "?"
        assert c["resumable"] is False
        assert c["path"].endswith(f"{sb.bad_id}.json")
        # 正常 plan 不受影响：进度/续跑能力照旧
        assert len(normal) == 1
        assert normal[0]["plan_id"] == sb.good_id
        assert normal[0]["progress"] == "1/2"
        assert normal[0]["resumable"] is True
        # 蒸馏页消费方按 command 过滤——损坏条目不会误入
        assert all(it["command"] != "distill-style" for it in corrupt)


# ============ ③ clear_corrupt_plan：移 .corrupt/ 可恢复·运行中拒绝 ============
def test_clear_corrupt_plan_moves_file_and_refuses_while_running():
    with _Sandbox() as sb:
        st = gs.AppState()
        runner = gr.PipelineRunner(st)
        path = [it for it in runner.list_resumable()
                if it.get("corrupt")][0]["path"]
        # 运行中一律拒绝（照 abort_plan 范式）——文件原地不动
        st.running = True
        assert runner.clear_corrupt_plan(path) is False
        assert sb.bad_file.exists()
        # 空闲时清除 = 移到同目录 .corrupt/ 子目录（不直接删·可恢复）
        st.running = False
        assert runner.clear_corrupt_plan(path) is True
        assert not sb.bad_file.exists()
        backup = sb.plans / ".corrupt" / f"{sb.bad_id}.json"
        assert backup.exists(), "清除必须是搬运备份，不是删除"
        assert backup.read_text(encoding="utf-8") == "{这不是合法 JSON!!"
        # 清除后列表不再出现 corrupt 条目（_scan 只 glob 顶层 *.json）
        items = runner.list_resumable()
        assert all(not it.get("corrupt") for it in items)
        assert [it["plan_id"] for it in items] == [sb.good_id]
        # 幂等兜底：文件已不存在 → 返回 False 不抛
        assert runner.clear_corrupt_plan(path) is False


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
    sys.exit(1 if fails else 0)
