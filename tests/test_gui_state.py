#!/usr/bin/env python3
"""GUI 状态层/执行器纯逻辑测试（角度①·零依赖 runner 兼容·不 import nicegui）。

覆盖：LogBuffer 轮转游标 / StderrTee 行切分 / PauseBridge 线程桥往返+超时 /
scan_project 下一步推断 / PipelineRunner 成功-暂停-失败-拒绝双跑 / profile 打码。
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    sys.path.insert(0, p)

from core.gui import runner as gr  # noqa: E402
from core.gui import state as gs  # noqa: E402


# ============ LogBuffer ============
def test_logbuffer_since_basic():
    b = gs.LogBuffer(maxlen=10)
    for i in range(3):
        b.append(f"L{i}")
    new, cur = b.since(0)
    assert new == ["L0", "L1", "L2"] and cur == 3
    new2, cur2 = b.since(cur)
    assert new2 == [] and cur2 == 3


def test_logbuffer_rotation_cursor_correct():
    b = gs.LogBuffer(maxlen=5)
    for i in range(4):
        b.append(f"L{i}")
    _, cur = b.since(0)             # 消费 L0-L3
    for i in range(4, 12):          # 追加 L4-L11（挤掉 L0-L6）
        b.append(f"L{i}")
    new, cur2 = b.since(cur)
    assert new == ["L7", "L8", "L9", "L10", "L11"]  # 窗口内全部新行·无重复
    assert cur2 == 12


def test_logbuffer_two_independent_consumers_both_get_all():
    """两个独立游标（= 两个浏览器 tab）各自 since 都拿全量·互不瓜分（根因 B）。"""
    b = gs.LogBuffer(maxlen=100)
    for i in range(10):
        b.append(f"L{i}")
    cur_a = cur_b = 0
    a, cur_a = b.since(cur_a)
    bb, cur_b = b.since(cur_b)
    assert a == bb == [f"L{i}" for i in range(10)]   # 两 tab 都看到完整 10 行
    for i in range(10, 15):
        b.append(f"L{i}")
    a2, cur_a = b.since(cur_a)
    b2, cur_b = b.since(cur_b)
    assert a2 == b2 == [f"L{i}" for i in range(10, 15)]


def test_logbuffer_thread_safety_smoke():
    b = gs.LogBuffer(maxlen=1000)

    def w(tag):
        for i in range(200):
            b.append(f"{tag}{i}")

    ts = [threading.Thread(target=w, args=(t,)) for t in "AB"]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert b.total == 400


# ============ StderrTee ============
class _FakeStream:
    def __init__(self):
        self.data = ""
        self.encoding = "utf-8"

    def write(self, s):
        self.data += s

    def flush(self):
        pass


def test_tee_splits_lines_and_passes_through():
    sink = gs.LogBuffer()
    orig = _FakeStream()
    tee = gs.StderrTee(orig, sink)
    tee.write("第一行\n第二")
    tee.write("行后半\n  \n")  # 空白行不进缓冲
    assert orig.data == "第一行\n第二行后半\n  \n"  # 原样透传
    lines, _ = sink.since(0)
    assert lines == ["第一行", "第二行后半"]


def test_tee_survives_broken_original():
    class _Broken:
        def write(self, s):
            raise OSError("windowed exe 无控制台")

        def flush(self):
            raise OSError()

    sink = gs.LogBuffer()
    tee = gs.StderrTee(_Broken(), sink)
    tee.write("仍然要进缓冲\n")
    tee.flush()
    assert sink.since(0)[0] == ["仍然要进缓冲"]


def test_install_tee_idempotent():
    saved = sys.stderr
    try:
        sink = gs.LogBuffer()
        t1 = gs.install_stderr_tee(sink)
        t2 = gs.install_stderr_tee(sink)
        assert t1 is t2  # 不嵌套包裹
    finally:
        sys.stderr = saved


# ============ PauseBridge ============
def _run_worker_until_waiting(br, step, spec, options, result):
    def worker():
        result["answer"] = br.request(step, spec, options)

    t = threading.Thread(target=worker)
    t.start()
    for _ in range(200):
        if br.waiting:
            break
        time.sleep(0.01)
    return t


def test_pause_bridge_roundtrip():
    br = gs.PauseBridge()
    result = {}
    t = _run_worker_until_waiting(br, {"n": 11}, {"type": "choice"},
                                  [{"label": "A"}, {"label": "B"}], result)
    assert br.waiting and br.pending["step"] == 11
    assert [o["label"] for o in br.pending["options"]] == ["A", "B"]
    rid = br.pending["req_id"]
    assert br.respond({"label": "B"}, req_id=rid) is True
    t.join(timeout=5)
    assert result["answer"] == {"label": "B"}
    assert not br.waiting and br.current_req_id() is None  # pending + 令牌清理


def test_pause_bridge_stale_req_id_rejected():
    """陈旧 req_id（上一轮捕获）应答 → 拒绝（多 tab 抢答 / 超时迟点·根因 A/C）。"""
    br = gs.PauseBridge()
    result = {}
    t = _run_worker_until_waiting(br, {"n": 11}, {"type": "choice"},
                                  [{"label": "A"}], result)
    rid = br.pending["req_id"]
    assert br.respond({"label": "A"}, req_id=rid) is True   # 正确轮次
    t.join(timeout=5)
    # 迟到的同 rid 再点 → 已无 live pending → 拒绝
    assert br.respond({"label": "迟到"}, req_id=rid) is False


def test_pause_bridge_second_responder_loses():
    """多 tab：第一个应答赢，第二个（同轮）被拒（不覆盖·北极星③）。"""
    br = gs.PauseBridge()
    result = {}
    t = _run_worker_until_waiting(br, {"n": 11}, {}, [{"label": "first"}], result)
    rid = br.pending["req_id"]
    assert br.respond({"label": "first"}, req_id=rid) is True
    # 工作线程还没来得及消费时，第二个 tab 抢答——_answer 已非 None → 拒
    assert br.respond({"label": "second"}, req_id=rid) is False
    t.join(timeout=5)
    assert result["answer"] == {"label": "first"}


def test_pause_bridge_respond_no_pending_rejected():
    """无人等待时 respond → False（超时后陈旧 dialog 迟点不静默吞·根因 C）。"""
    br = gs.PauseBridge()
    assert br.respond({"label": "X"}, req_id=1) is False
    assert br.current_req_id() is None


def test_pause_bridge_req_id_monotonic_across_rounds():
    br = gs.PauseBridge()
    r1 = {}
    t1 = _run_worker_until_waiting(br, {"n": 1}, {}, [{"label": "a"}], r1)
    rid1 = br.pending["req_id"]
    br.respond({"label": "a"}, req_id=rid1)
    t1.join(timeout=5)
    r2 = {}
    t2 = _run_worker_until_waiting(br, {"n": 2}, {}, [{"label": "b"}], r2)
    rid2 = br.pending["req_id"]
    assert rid2 > rid1                       # 轮次递增
    # 上一轮的 rid1 对本轮无效（跨轮脏 Event 根治）
    assert br.respond({"label": "stale"}, req_id=rid1) is False
    assert br.respond({"label": "b"}, req_id=rid2) is True
    t2.join(timeout=5)
    assert r2["answer"] == {"label": "b"}


def test_pause_bridge_timeout_returns_none():
    br = gs.PauseBridge()
    saved = gs.PAUSE_WAIT_TIMEOUT
    gs.PAUSE_WAIT_TIMEOUT = 0.05
    try:
        out = br.request({"n": 1}, {}, [])
        assert out is None         # 超时绝不替用户选（北极星③）
        assert br.current_req_id() is None  # 超时也清令牌→迟点失效
    finally:
        gs.PAUSE_WAIT_TIMEOUT = saved


# ============ scan_project ============
def _mk_project(tmp, clusters=None, summary_clusters=None, draft_for=None):
    root = Path(tmp) / "书"
    (root / "_数据库").mkdir(parents=True)
    if clusters is not None:
        (root / "_数据库" / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False),
            encoding="utf-8")
    if summary_clusters is not None:
        # 🔴 生产格式 = list[dict]（cluster_summary_store 产·非 dict-keyed）。
        # 旧 fixture 用 dict 掩盖了 consumer 的 list[dict] 解析失效（对抗审查根因 G）。
        (root / "_数据库" / "故事块摘要.json").write_text(
            json.dumps({"clusters": [{"cluster_id": c, "title": "", "chapters": {}}
                                     for c in summary_clusters]},
                       ensure_ascii=False), encoding="utf-8")
    for cid in (draft_for or []):
        d = root / "章节" / f"{cid}_draft"
        d.mkdir(parents=True)
        (d / f"{cid}_draft.txt").write_text("草稿", encoding="utf-8")
    return root


def test_scan_suggests_write_when_no_draft():
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_project(tmp, clusters=[
            {"cluster_id": "cluster_001", "status": "done"},
            {"cluster_id": "cluster_002", "status": "in_progress"}])
        info = gs.scan_project(root)
        assert info.next_action == "cluster-write" and info.next_key == "002"


def test_scan_suggests_save_when_draft_no_summary():
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_project(tmp,
                           clusters=[{"cluster_id": "cluster_001",
                                      "status": "in_progress"}],
                           summary_clusters=[],
                           draft_for=["cluster_001"])
        info = gs.scan_project(root)
        assert info.next_action == "cluster-save-state" and info.next_key == "001"


def test_scan_uninitialized_notes_outline_boundary():
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_project(tmp, clusters=[])
        info = gs.scan_project(root)
        # A9 人话文案：非技术用户指引去「新建书」（原为开发者口径「未初始化/outline」）
        assert "新建书" in info.note


def test_scan_done_cluster_with_list_dict_summary():
    """list[dict] 生产格式：已写完已保存的 cluster 正确计入 clusters_done（根因 G）。
    旧实现把 list[dict] 当 dict/str-list → done 恒 0 → 误推荐重跑 save-state。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_project(tmp,
                           clusters=[{"cluster_id": "cluster_001",
                                      "status": "in_progress"}],
                           summary_clusters=["cluster_001"],
                           draft_for=["cluster_001"])
        info = gs.scan_project(root)
        assert info.clusters_done == 1                  # 不再恒 0
        assert "已写完并保存" in info.note               # 命中曾经的死代码分支


def test_scan_bad_utf8_bytes_noted_not_crash():
    """事件簇.json 含非法 UTF-8 → UnicodeDecodeError(ValueError 子类)被捕获（根因 I）。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "书"
        (root / "_数据库").mkdir(parents=True)
        (root / "_数据库" / "事件簇.json").write_bytes(b"\xff\xfe\x00bad")
        info = gs.scan_project(root)                     # 不抛
        assert "损坏" in info.note


def test_scan_non_dict_toplevel_noted():
    """事件簇.json 顶层是数组 → .get 不抛 AttributeError（根因 I）。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "书"
        (root / "_数据库").mkdir(parents=True)
        (root / "_数据库" / "事件簇.json").write_text("[1,2,3]", encoding="utf-8")
        info = gs.scan_project(root)
        assert "损坏" in info.note


def test_scan_clusters_value_null_noted_not_typeerror():
    """{"clusters": null}（键在值非可迭代）→ 不抛 TypeError，友好提示（再审根因#4）。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "书"
        (root / "_数据库").mkdir(parents=True)
        (root / "_数据库" / "事件簇.json").write_text('{"clusters": null}',
                                                    encoding="utf-8")
        info = gs.scan_project(root)          # 不抛 TypeError
        assert "损坏" in info.note


def test_scan_clusters_value_scalar_noted():
    """{"clusters": 5} → 不抛 TypeError（再审根因#4）。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "书"
        (root / "_数据库").mkdir(parents=True)
        (root / "_数据库" / "事件簇.json").write_text('{"clusters": 5}',
                                                    encoding="utf-8")
        info = gs.scan_project(root)
        assert "损坏" in info.note


def test_scan_summary_clusters_null_no_crash():
    """故事块摘要.json 的 clusters=null → 不抛 TypeError（再审根因#4·摘要块同缺口）。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "书"
        (root / "_数据库").mkdir(parents=True)
        (root / "_数据库" / "事件簇.json").write_text(
            '{"clusters": [{"cluster_id": "cluster_001", "status": "in_progress"}]}',
            encoding="utf-8")
        (root / "_数据库" / "故事块摘要.json").write_text('{"clusters": null}',
                                                      encoding="utf-8")
        info = gs.scan_project(root)          # 不抛
        assert info.clusters_done == 0


def test_scan_projects_tolerates_bad_project():
    with tempfile.TemporaryDirectory() as tmp:
        good = _mk_project(tmp, clusters=[])
        bad = Path(tmp) / "坏书"
        (bad / "_数据库").mkdir(parents=True)
        (bad / "_数据库" / "事件簇.json").write_text("{坏json", encoding="utf-8")
        infos = gs.scan_projects(Path(tmp))
        assert {i.name for i in infos} == {"书", "坏书"}
        bad_info = next(i for i in infos if i.name == "坏书")
        assert "损坏" in bad_info.note or "失败" in bad_info.note


# ============ PipelineRunner ============
class _FakeSummary:
    def __init__(self, plan_id="p1", paused_at=None, completed=None):
        self.plan_id = plan_id
        self.paused_at = paused_at
        # runner._work 完本短路会扫 summary.completed 的 detail（与真 RunSummary 对齐）
        self.completed = completed or []


def _run_and_wait(r: gr.PipelineRunner, *args, **kw):
    assert r.start(*args, **kw)
    r._thread.join(timeout=10)
    assert not r._thread.is_alive()


def test_runner_success_chain():
    st = gs.AppState()
    r = gr.PipelineRunner(st)
    calls = []
    saved = gr.orc.run_command
    gr.orc.run_command = lambda cmd, proj, **kw: (
        calls.append((cmd, kw.get("resume_plan_id"))), _FakeSummary())[-1]
    try:
        _run_and_wait(r, ["cluster-write", "cluster-save-state"], "书", "001")
    finally:
        gr.orc.run_command = saved
    assert [c[0] for c in calls] == ["cluster-write", "cluster-save-state"]
    assert calls[0][1] is None and calls[1][1] is None
    assert st.last_result.startswith("✅") and not st.running


def test_runner_resume_only_first_command():
    st = gs.AppState()
    r = gr.PipelineRunner(st)
    calls = []
    saved = gr.orc.run_command
    gr.orc.run_command = lambda cmd, proj, **kw: (
        calls.append(kw.get("resume_plan_id")), _FakeSummary())[-1]
    try:
        _run_and_wait(r, ["cluster-write", "cluster-save-state"], "书", "001",
                      resume_plan_id="plan_x")
    finally:
        gr.orc.run_command = saved
    assert calls == ["plan_x", None]  # 续跑 id 只用于第一条命令


def test_runner_pause_stops_chain():
    st = gs.AppState()
    r = gr.PipelineRunner(st)
    calls = []
    saved = gr.orc.run_command
    gr.orc.run_command = lambda cmd, proj, **kw: (
        calls.append(cmd), _FakeSummary(paused_at=11))[-1]
    try:
        _run_and_wait(r, ["cluster-save-state", "cluster-write"], "书", "001")
    finally:
        gr.orc.run_command = saved
    assert calls == ["cluster-save-state"]      # 暂停后不进下一条
    assert "⏸" in st.last_result and not st.running


def test_runner_error_recorded_not_raised():
    st = gs.AppState()
    r = gr.PipelineRunner(st)
    saved = gr.orc.run_command

    def _boom(cmd, proj, **kw):
        raise gr.orc.OrchestratorError("step 3 脚本退出码 2")

    gr.orc.run_command = _boom
    try:
        _run_and_wait(r, ["cluster-write"], "书", "001")
    finally:
        gr.orc.run_command = saved
    assert "❌" in st.last_result and "退出码 2" in st.last_result
    assert not st.running           # 锁释放·可再启动


def test_runner_rejects_concurrent_start():
    st = gs.AppState()
    r = gr.PipelineRunner(st)
    gate = threading.Event()
    saved = gr.orc.run_command
    gr.orc.run_command = lambda cmd, proj, **kw: (gate.wait(5), _FakeSummary())[-1]
    try:
        assert r.start(["cluster-write"], "书", "001")
        assert not r.start(["cluster-write"], "书", "001")  # 双跑拒绝
        gate.set()
        r._thread.join(timeout=10)
    finally:
        gr.orc.run_command = saved
    # 跑完后可以再次启动
    gr.orc.run_command = lambda cmd, proj, **kw: _FakeSummary()
    try:
        _run_and_wait(r, ["cluster-write"], "书", "001")
    finally:
        gr.orc.run_command = saved


def test_runner_thread_start_failure_releases_lock():
    """thread.start() 抛异常 → 复位 running + 释放锁，可再次启动（根因 D）。"""
    st = gs.AppState()
    r = gr.PipelineRunner(st)
    saved_start = threading.Thread.start

    def _boom(self):
        raise RuntimeError("can't start new thread")

    threading.Thread.start = _boom
    try:
        ok = r.start(["cluster-write"], "书", "001")
        assert ok is False
        assert not st.running                         # running 复位
        assert "无法启动" in st.last_result
    finally:
        threading.Thread.start = saved_start
    # 锁已释放 → 可正常再启动
    saved = gr.orc.run_command
    gr.orc.run_command = lambda cmd, proj, **kw: _FakeSummary()
    try:
        _run_and_wait(r, ["cluster-write"], "书", "001")
    finally:
        gr.orc.run_command = saved
    assert st.last_result.startswith("✅")


def test_runner_passes_bridge_and_callback():
    st = gs.AppState()
    r = gr.PipelineRunner(st)
    seen = {}
    saved = gr.orc.run_command

    def _capture(cmd, proj, **kw):
        seen["pause_handler"] = kw.get("pause_handler")
        kw["step_callback"](3, "cluster-quality", 7)
        return _FakeSummary()

    gr.orc.run_command = _capture
    try:
        _run_and_wait(r, ["cluster-write"], "书", "001")
    finally:
        gr.orc.run_command = saved
    assert seen["pause_handler"] == st.bridge.request  # 走向卡桥接到 UI


# ============ profile 打码 ============
def test_profiles_masked_never_leak_full_key():
    data = gr.list_profiles_masked()
    for row in data["profiles"]:
        assert "sk-" not in row["api_key"][6:], "key 主体必须打码"
        assert len(row["api_key"]) < 20


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
