#!/usr/bin/env python3
"""GUI 模拟 UI 测试（角度②·NiceGUI 官方 User 夹具·无浏览器）。

覆盖：页面渲染 / 缺 key 警告 / 假流水线端到端（按钮→线程→状态→日志→结果）/
走向卡弹窗全流程（工作线程停顿 → dialog → 点选 → 桥返回答案）/ plans / settings。

运行：python -m pytest tests/gui -q
"""
import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from nicegui.testing import User

import core.gui.app as app_module
import core.gui.runner as gr
import core.gui.state as gs


class _FakeSummary:
    def __init__(self, plan_id="plan_t", paused_at=None):
        self.plan_id = plan_id
        self.paused_at = paused_at
        self.completed = []   # 对齐 orchestrator.RunSummary（runner 查 book_complete 短路）


@pytest.fixture
def fake_project(user, tmp_path, monkeypatch):
    """临时小说项目 + NOVELS_DIR 沙盒 + 页面重注册。

    依赖 user 夹具保证在 nicegui_reset_globals（每测清 Client.page_routes）
    之后调用 init_pages()——官方「无 main.py 项目」测试模式。
    """
    novels = tmp_path / "novels"
    root = novels / "测试书"
    (root / "_数据库").mkdir(parents=True)
    (root / "_数据库" / "事件簇.json").write_text(json.dumps({
        "clusters": [{"cluster_id": "cluster_001", "status": "in_progress"}]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gs, "NOVELS_DIR", novels)
    # 重置共享状态（页面测试间隔离）——bridge 必须换新实例，否则上个测试残留的
    # pending/_active_rid/被 patch 的方法会污染下个测试（test 间共享 STATE 单例）。
    app_module.STATE.projects = []
    app_module.STATE.selected = ""
    app_module.STATE.last_result = ""
    app_module.STATE.running = False
    app_module.STATE.bridge = gs.PauseBridge()
    app_module.init_pages()
    return root


async def test_index_renders_core_controls(user: User, fake_project) -> None:
    await user.open("/")
    await user.should_see(marker="project-select")
    await user.should_see(marker="key-input")
    await user.should_see(marker="btn-write")
    await user.should_see(marker="btn-save")
    await user.should_see(marker="btn-both")
    await user.should_see(marker="status-label")
    await user.should_see("空闲")


async def test_project_info_suggests_next_action(user: User, fake_project) -> None:
    await user.open("/")
    await user.should_see("cluster-write")   # 推断出下一步
    # key 输入自动填建议值
    key_el = user.find(marker="key-input").elements.pop()
    assert key_el.value == "001"


async def test_write_without_key_warns(user: User, fake_project) -> None:
    await user.open("/")
    user.find(marker="key-input").elements.pop().set_value("")
    user.find(marker="btn-write").click()
    await user.should_see("还没有可写的故事块")   # 人话文案（B 组·非技术用户）


async def test_pipeline_button_runs_and_reports(user: User, fake_project,
                                                monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(gr.orc, "run_command",
                        lambda cmd, proj, **kw: (calls.append((cmd, proj)),
                                                 _FakeSummary())[-1])
    await user.open("/")
    user.find(marker="btn-write").click()
    await user.should_see("✅ 全部完成")     # timer 轮询把结果推上 UI
    assert calls == [("cluster-write", "测试书")]


async def test_trajectory_card_dialog_full_loop(user: User, fake_project,
                                                monkeypatch) -> None:
    """最关键流程：工作线程停顿（走向卡）→ UI 弹窗 → 点选 → 桥返回 → 流水线继续。"""
    answers = {}

    def _fake_run(cmd, proj, **kw):
        ans = kw["pause_handler"](
            {"n": 11},
            {"type": "choice", "prompt": "选择下一故事块走向"},
            [{"label": "走向A·主线推进"}, {"label": "走向B·支线碰撞"}])
        answers["picked"] = ans
        return _FakeSummary()

    monkeypatch.setattr(gr.orc, "run_command", _fake_run)
    await user.open("/")
    user.find(marker="btn-save").click()
    await user.should_see("选择下一故事块走向")          # 弹窗出现
    await user.should_see("走向B·支线碰撞")
    user.find(marker="card-opt-1").click()              # 选第二张卡 → submit → respond
    # 关键：用 await asyncio.sleep 轮询（而非 thread.join）——后者会阻塞 asyncio
    # 事件循环，导致 dialog submit→respond 永远不执行而死锁。await 让出循环 →
    # submit 触发 → 工作线程（真实 OS 线程）解阻塞拿到答案 → 真实时间也流逝。
    for _ in range(100):
        if "picked" in answers and not app_module.STATE.running:
            break
        await asyncio.sleep(0.05)
    assert answers["picked"] == {"label": "走向B·支线碰撞"}  # 桥把用户选择送回工作线程
    assert not app_module.STATE.bridge.waiting               # 桥已清理
    # ダイアログ表示中は _tick が await card_dialog でブロック→次 tick 無し。
    # respond 後、result_label を更新する「次の tick」(0.5s) が発火するまで待つ。
    # A11 完成信号按命令定制：btn-save → cluster-save-state 的人话提示
    assert app_module.STATE.last_result.startswith("✅ 已保存")
    await asyncio.sleep(0.6)                                 # 次 tick が label を更新
    await user.should_see("✅ 已保存")                       # timer 把结果推上 UI


async def test_stale_card_reclaimed_when_pending_clears(user: User, fake_project,
                                                        monkeypatch) -> None:
    """超时/被抢答后 bridge.pending 清空 → 下一次 _tick 主动关陈旧卡并提示（根因 C）。"""
    release = threading.Event()

    def _fake_run(cmd, proj, **kw):
        # 弹卡后不应答，直接模拟 worker 超时退出（pending 被 request 清空）——
        # 这里直接调 pause_handler 但用线程外部控制：登记 pending 后由测试清空。
        st = app_module.STATE
        # 手动登记一个 pending（不阻塞），模拟「卡已弹但桥随后失效」
        st.bridge._req_counter += 1
        st.bridge.pending = {"step": 11, "spec": {"prompt": "选择下一故事块走向"},
                             "options": [{"label": "X"}], "req_id": st.bridge._req_counter}
        st.bridge._active_rid = st.bridge._req_counter
        release.wait(3)
        # 模拟超时：清空 pending（等价 request 超时返回）
        st.bridge.pending = None
        st.bridge._active_rid = None
        return _FakeSummary()

    monkeypatch.setattr(gr.orc, "run_command", _fake_run)
    await user.open("/")
    user.find(marker="btn-save").click()
    await user.should_see(marker="card-opt-0")       # 卡弹出
    release.set()                                    # 触发「超时清空 pending」
    # 下一 tick：_sync_cards 见 pending 失效 → submit(None) → 后台任务关卡 + 提示。
    # 等 worker 跑完 + 给 tick 时间回收（≥1 个 0.5s tick）。
    for _ in range(80):
        if not app_module.STATE.running:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.6)                          # 让回收 tick 发火
    await user.should_not_see(marker="card-opt-0")   # 陈旧卡已被回收（根因 C）


async def test_no_duplicate_card_after_pick_before_pending_clears(user: User,
                                                                  fake_project) -> None:
    """用户选完后~worker清pending前的窗口里，_tick 不对同 rid 重弹卡（再审根因#3）。
    确定性：直接管 bridge.pending（不跑 run_command/不 patch respond），手控时机。"""
    br = app_module.STATE.bridge
    await user.open("/")
    # 手工登记一轮 pending（等价 worker request 到走向卡）
    br._req_counter += 1
    rid = br._req_counter
    br.pending = {"step": 11, "spec": {"prompt": "选择下一故事块走向"},
                  "options": [{"label": "A"}], "req_id": rid}
    br._active_rid = rid
    await user.should_see(marker="card-opt-0")       # 下一 tick 弹卡
    user.find(marker="card-opt-0").click()           # 选 A → submit → _await_card 应答
    # 故意**不清 pending**（模拟 worker 慢），多个 tick 流逝
    await asyncio.sleep(1.2)
    # done_rid 守卫：同 rid 不重弹——卡不再出现（无守卫则会再 pop 一张 card-opt-0）
    await user.should_not_see(marker="card-opt-0")
    assert br._answer == {"label": "A"}              # 选择已被桥接收（未被重弹覆盖）
    # 收尾：清 pending（避免残留影响其它断言）
    br.pending = None
    br._active_rid = None


async def test_pipeline_error_surfaces_in_ui(user: User, fake_project,
                                             monkeypatch) -> None:
    def _boom(cmd, proj, **kw):
        raise gr.orc.OrchestratorError("step 3 脚本退出码 2")

    monkeypatch.setattr(gr.orc, "run_command", _boom)
    await user.open("/")
    user.find(marker="btn-write").click()
    await user.should_see("流水线停下")
    await user.should_see("空闲")           # 锁释放回空闲


async def test_plans_page_lists_and_empty_state(user: User, fake_project,
                                                monkeypatch) -> None:
    monkeypatch.setattr(app_module.RUNNER, "list_resumable", lambda: [])
    await user.open("/plans")
    await user.should_see(marker="no-plans")


async def test_plans_page_shows_resumable(user: User, fake_project,
                                          monkeypatch) -> None:
    monkeypatch.setattr(app_module.RUNNER, "list_resumable", lambda: [
        {"plan_id": "书_001_cluster-write_xxx", "command": "cluster-write",
         "project": "测试书", "key": "001", "progress": "3/7"}])
    await user.open("/plans")
    # B7 美化：badge 人话命令名 + 项目名 + 进度分离渲染
    await user.should_see("写故事块")
    await user.should_see("测试书")
    await user.should_see("进度 3/7")
    await user.should_see("从断点续跑")


async def test_settings_page_masks_keys(user: User, fake_project) -> None:
    await user.open("/settings")
    await user.should_see(marker="active-profile")
    # v28 BYOK：设置页从只读 table 升级为 per-profile 录入卡片（详见 test_gui_settings.py）
    # 全量 key 绝不出现在页面数据里（masked 字段 < 20 字符 + key_in_keyring 布尔）
    data = gr.list_profiles_masked()
    for row in data["profiles"]:
        assert len(row["api_key"]) < 20
