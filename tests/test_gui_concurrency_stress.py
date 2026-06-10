#!/usr/bin/env python3
"""GUI 并发/行为压力测试（角度③·与单测/UI模拟正交）。

不打 API。专攻对抗审查根因 A/C 的并发本质：多 tab 抢答、跨轮脏 Event、迟点污染——
用真实多线程 hammer PauseBridge，断言「同一时刻至多一个应答被接受、跨轮答案绝不泄漏、
迟到/陈旧应答全被拒」。外加 GUI PipelineRunner 真模板 e2e（走 orchestrator 全链·假 LLM）。
"""
import json
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    sys.path.insert(0, p)

from core.gui import runner as gr  # noqa: E402
from core.gui import state as gs  # noqa: E402
import plan_tracker as pt  # noqa: E402


# ============ 多 tab 抢答压力（根因 A） ============
def test_concurrent_responders_only_one_accepted():
    """模拟 N 个 tab 对同一轮 pending 同时抢答：恰好 1 个被接受，其余全拒。"""
    br = gs.PauseBridge()
    result = {}

    def worker():
        result["answer"] = br.request({"n": 11}, {}, [{"label": f"opt{i}"}
                                                       for i in range(8)])

    t = threading.Thread(target=worker)
    t.start()
    for _ in range(200):
        if br.waiting:
            break
        time.sleep(0.005)
    rid = br.pending["req_id"]

    accepted = []
    barrier = threading.Barrier(8)

    def tab(i):
        barrier.wait()                       # 8 个 tab 同时开抢
        if br.respond({"label": f"tab{i}"}, req_id=rid):
            accepted.append(i)

    tabs = [threading.Thread(target=tab, args=(i,)) for i in range(8)]
    [x.start() for x in tabs]
    [x.join(timeout=5) for x in tabs]
    t.join(timeout=5)

    assert len(accepted) == 1, f"恰好 1 个 tab 应答被接受，实得 {len(accepted)}"
    assert result["answer"]["label"] == f"tab{accepted[0]}"  # 被接受者的答案被采用


def test_no_cross_round_answer_leak_under_stress():
    """连续多轮 pause，每轮夹杂「上一轮 rid 的迟到 respond」——绝不泄漏进本轮（根因 A/C）。"""
    br = gs.PauseBridge()
    leaked = []
    for round_no in range(20):
        result = {}

        def worker():
            result["answer"] = br.request({"n": round_no}, {},
                                          [{"label": f"r{round_no}"}])

        t = threading.Thread(target=worker)
        t.start()
        for _ in range(200):
            if br.waiting:
                break
            time.sleep(0.002)
        rid = br.pending["req_id"]

        # 一堆迟到的「上一轮 rid」并发轰炸（rid-1, rid-2…全是陈旧）
        stale_threads = []
        for d in range(1, 5):
            stale_threads.append(threading.Thread(
                target=lambda dd=d: br.respond({"label": "STALE"}, req_id=rid - dd)))
        [s.start() for s in stale_threads]
        # 正确答案
        ok = br.respond({"label": f"r{round_no}"}, req_id=rid)
        [s.join(timeout=3) for s in stale_threads]
        t.join(timeout=5)
        assert ok is True
        if result["answer"]["label"] == "STALE":
            leaked.append(round_no)
    assert not leaked, f"陈旧答案泄漏进这些轮次: {leaked}"


def test_timeout_then_late_click_rejected():
    """超时后迟到点击（陈旧 dialog）→ respond 拒绝、不污染（根因 C）。"""
    br = gs.PauseBridge()
    saved = gs.PAUSE_WAIT_TIMEOUT
    gs.PAUSE_WAIT_TIMEOUT = 0.05
    try:
        result = {}
        t = threading.Thread(target=lambda: result.__setitem__(
            "a", br.request({"n": 1}, {}, [{"label": "X"}])))
        t.start()
        for _ in range(200):
            if br.waiting:
                break
            time.sleep(0.002)
        rid = br.pending["req_id"] if br.pending else None
        t.join(timeout=5)                    # 等超时
        assert result["a"] is None           # 超时返回 None
        # 用户超时后才点（陈旧）→ 拒绝
        assert br.respond({"label": "迟到"}, req_id=rid) is False
    finally:
        gs.PAUSE_WAIT_TIMEOUT = saved


# ============ GUI PipelineRunner 真模板 e2e（角度③：经 GUI 入口驱动全链） ============
class _Sandbox:
    def __init__(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.templates = self.tmp / "templates"
        self.projects = self.tmp / "novels"
        for d in (self.templates, self.projects, self.tmp / "styles",
                  self.tmp / ".plans"):
            d.mkdir(parents=True)
        real_plans = _ROOT / "core" / "claude-home" / "plans"
        for f in real_plans.glob("*.plan.json"):
            (self.templates / f.name).write_text(f.read_text(encoding="utf-8"),
                                                 encoding="utf-8")
        self.proj = self.projects / "并发书"
        (self.proj / "_数据库").mkdir(parents=True)
        self._saved = {}

    def __enter__(self):
        for k, v in [("TEMPLATES_DIR", self.templates),
                     ("PROJECTS_DIR", self.projects),
                     ("STYLES_DIR", self.tmp / "styles"),
                     ("GLOBAL_PLANS_DIR", self.tmp / ".plans"),
                     ("ATTEST_KEY_PATH", self.tmp / ".plans" / ".attest_key")]:
            self._saved[k] = getattr(pt, k)
            setattr(pt, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            setattr(pt, k, v)


def test_gui_runner_drives_real_cluster_write_via_worker_thread():
    """经 GUI PipelineRunner（工作线程）跑真 cluster-write 模板全 7 步（假脚本/假judge）。
    证明 GUI 入口 → orchestrator → plan_tracker 全链在工作线程里跑通。"""
    with _Sandbox() as sb:
        st = gs.AppState()
        runner = gr.PipelineRunner(st)

        # 用 sandbox-aware 的 orchestrator.run_command 包装：注入假 runner/dispatch
        import orchestrator as orc

        def _side_runner(cmd, *, repo_root=None, label=""):
            db = sb.proj / "_数据库"
            if "build_manifest.py" in cmd:
                (db / ".manifest").mkdir(parents=True, exist_ok=True)
                (db / ".manifest" / "ch_001.json").write_text("{}", encoding="utf-8")
            elif "gen_writer.py" in cmd:
                d = sb.proj / "章节" / "cluster_001_draft"
                d.mkdir(parents=True, exist_ok=True)
                (d / "cluster_001_draft.txt").write_text("正文" * 3000, encoding="utf-8")
                (d / "cluster_001_changes.json").write_text(
                    json.dumps({"factual": {}, "self_eval": {}}), encoding="utf-8")
            elif "audit_hub.py" in cmd:
                (db / ".audit").mkdir(parents=True, exist_ok=True)
                (db / ".audit" / "cluster_001_audit.json").write_text("{}", encoding="utf-8")
            elif "chapter_splitter.py" in cmd:
                (db / ".wal").mkdir(parents=True, exist_ok=True)
                (db / ".wal" / "splitter_cluster_001_decisions.json").write_text(
                    json.dumps({"chapter_range": [1, 4]}), encoding="utf-8")
            return 0

        def _side_dispatch(agent, step, ctx):
            data = {"verdict": "pass", "new_issues_this_round": [],
                    "cluster_id": "cluster_001", "summary": "s",
                    "emotion": {"value": 1}, "violations": [],
                    "judge_id": "x", "specific_findings": {}, "entries": []}
            jrp = step.get("judge_report_path")
            out = jrp.get(agent) if isinstance(jrp, dict) else jrp
            if out:
                p = Path(orc.resolve_placeholders(out, ctx))
                if not p.is_absolute():
                    p = Path(ctx["project_root"]) / p
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            class _O:
                pass
            o = _O(); o.data, o.ok = data, True
            return o

        saved = orc.run_command
        orc.run_command = lambda cmd, proj, **kw: saved(
            cmd, proj, script_runner=_side_runner, judge_dispatch=_side_dispatch,
            **{k: v for k, v in kw.items()
               if k not in ("script_runner", "judge_dispatch")})
        try:
            assert runner.start(["cluster-write"], "并发书", "001")
            runner._thread.join(timeout=30)
            assert not runner._thread.is_alive()
        finally:
            orc.run_command = saved
        assert st.last_result.startswith("✅"), st.last_result
        assert not st.running
        # 经 GUI 入口确实把 plan 跑到完成
        actives = [a for a in pt.find_active_plans()
                   if a["plan"].get("project") == "并发书"]
        assert actives == []  # 无残留未完成 plan（已 end_plan）


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
