#!/usr/bin/env python3
"""app.py — 若渝AI 图形界面（NiceGUI · 脱离 Claude CLI · 2026-06-10）

唯一 import nicegui 的模块。页面：
- /          写作台：项目选择 + 进度 + 一键写故事块/保存状态/连跑 + 实时日志 + 走向卡弹窗
- /plans     Plan 续跑：活跃 plan 列表 + 断点续跑
- /settings  设置：gen-model profile 概览（key 打码）

官方成熟模式（减少排错）：
- 走向卡 = awaitable `ui.dialog().props('persistent')` + `.submit(choice)`
- 长任务 = PipelineRunner 工作线程（不阻塞事件循环），UI 经 `ui.timer` 轮询 AppState
- 日志 = `ui.log` + LogBuffer 单调游标增量推送
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
for p in (str(_REPO), str(_REPO / "core" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from nicegui import background_tasks, ui  # noqa: E402

from core.gui.runner import PipelineRunner, list_profiles_masked  # noqa: E402
from core.gui.state import AppState, install_stderr_tee  # noqa: E402

STATE = AppState()
RUNNER = PipelineRunner(STATE)


def _header(title: str):
    with ui.header().classes("items-center justify-between"):
        ui.label(f"若渝AI · {title}").classes("text-lg font-bold")
        with ui.row().classes("gap-2"):
            ui.link("写作台", "/").classes("text-white")
            ui.link("Plan 续跑", "/plans").classes("text-white")
            ui.link("设置", "/settings").classes("text-white")


# ============ 写作台 ============
def index():
    _header("写作台")
    STATE.refresh_projects()

    with ui.row().classes("w-full gap-4 items-start"):
        # —— 左：项目 + 操作 ——
        with ui.column().classes("w-1/3 gap-2"):
            project_select = ui.select(
                options=[p.name for p in STATE.projects] or ["（无项目）"],
                value=STATE.selected or None, label="小说项目",
            ).classes("w-full").mark("project-select")

            info_label = ui.label("").classes("text-sm text-gray-600")\
                .mark("project-info")
            note_label = ui.label("").classes("text-xs text-orange-600")

            key_input = ui.input("cluster key（如 001）").classes("w-full")\
                .mark("key-input")
            auto_switch = ui.switch("全自动（走向卡取引擎第一候选·显式开关）")\
                .mark("auto-pilot")

            def _sync_project():
                STATE.selected = project_select.value or ""
                p = STATE.project()
                if p:
                    info_label.set_text(
                        f"已写 {p.chapters_written} 章 · cluster {p.clusters_done}"
                        f"/{p.clusters_total} 已保存 · 建议：{p.next_action or '—'} "
                        f"key={p.next_key or '—'}")
                    note_label.set_text(p.note)
                    if not key_input.value and p.next_key:
                        key_input.set_value(p.next_key)

            project_select.on_value_change(lambda e: _sync_project())
            _sync_project()

            def _start(commands: list[str]):
                p = STATE.project()
                key = (key_input.value or "").strip()
                if not p:
                    ui.notify("先选项目", type="warning")
                    return
                if not key:
                    ui.notify("填 cluster key（如 001）", type="warning")
                    return
                ok = RUNNER.start(commands, p.name, key,
                                  auto_pilot=bool(auto_switch.value))
                if not ok:
                    ui.notify("已有流水线在运行", type="warning")

            with ui.row().classes("gap-2"):
                ui.button("✍ 写故事块",
                          on_click=lambda: _start(["cluster-write"]))\
                    .mark("btn-write")
                ui.button("💾 保存状态",
                          on_click=lambda: _start(["cluster-save-state"]))\
                    .mark("btn-save")
                ui.button("⚡ 连跑（写+存）",
                          on_click=lambda: _start(
                              ["cluster-write", "cluster-save-state"]))\
                    .props("color=primary").mark("btn-both")

            status_label = ui.label("空闲").classes("text-sm font-mono")\
                .mark("status-label")
            result_label = ui.label("").classes("text-sm").mark("result-label")
            ui.button("🔄 刷新项目",
                      on_click=lambda: (STATE.refresh_projects(),
                                        project_select.set_options(
                                            [p.name for p in STATE.projects]),
                                        _sync_project())).props("flat dense")

        # —— 右：实时日志 ——
        with ui.column().classes("flex-1"):
            ui.label("流水线日志").classes("text-sm text-gray-500")
            log_view = ui.log(max_lines=400).classes("w-full h-96 font-mono text-xs")

    # —— 走向卡弹窗（awaitable dialog · persistent 不可点旁边关掉） ——
    # dialog_open 存本 client 正在显示的 req_id（None=未显示）——绑定代际令牌，
    # 防多 tab 抢答 + 超时陈旧卡迟点污染下一轮（对抗审查根因 A/C）。
    card_dialog = ui.dialog().props("persistent")
    # req_id=本 client 正显示的轮次（None=未显示）；done_rid=本 client 刚应答完的轮次
    # （防「应答后~worker清pending前」窗口里 _tick 对同 rid 重弹卡·根因#3）。
    dialog_state = {"req_id": None, "done_rid": None}

    def _opt_label(opt, i):
        if not isinstance(opt, dict):
            return str(opt)[:80]
        if opt.get("label"):
            return opt["label"]
        if opt.get("title"):
            return opt["title"]
        # 涌现候选无 label/title——取 scope_summary 首句（含 ME 标题）作可读兜底，
        # 不暴露裸 dict repr（对抗审查根因 E）。
        scope = str(opt.get("scope_summary") or "")
        if scope:
            return scope.split("。")[0][:60]
        return opt.get("parent_me") or f"候选{i + 1}"

    async def _await_card(rid):
        """后台任务：等用户点选 → 带 req_id 应答。与 _tick 解耦，故 _tick 不被阻塞、
        仍能每 0.5s 检测「桥失效」并 submit(None) 中断本任务（根因 C 的关键）。"""
        try:
            answer = await card_dialog
        except Exception:
            answer = None
        try:
            if answer is None:
                # 被 _tick 中断（桥失效/超时/被抢答）——提示去续跑，不应答
                ui.notify("走向卡已失效（已被处理或超时）——如需选择请到「Plan 续跑」页继续",
                          type="warning")
            elif not STATE.bridge.respond(answer, req_id=rid):
                # 多 tab 后手点击 / 迟点 → 桥拒绝，不污染下一轮走向（北极星③）
                ui.notify("该选择未生效（走向已被处理或已超时）——请到「Plan 续跑」页继续",
                          type="warning")
        finally:
            dialog_state["done_rid"] = rid   # 记刚处理完的轮次（防 _tick 同 rid 重弹·根因#3）
            dialog_state["req_id"] = None
            card_dialog.clear()              # 应答后清卡元素（不留隐藏残件·下次 pop 也会清）

    def _sync_cards():
        """每 tick 同步执行（不阻塞）：弹新卡 / 回收陈旧卡。"""
        pending = STATE.bridge.pending
        if dialog_state["req_id"] is not None:
            # 本 client 正显示卡，但桥已无 pending 或换了 req_id（超时/被抢答）
            # → submit(None) 中断后台 _await_card 任务（它负责关卡 + 提示·根因 C）。
            if not pending or pending.get("req_id") != dialog_state["req_id"]:
                card_dialog.submit(None)   # 解析后台 _await_card（它复位 req_id+提示）
                card_dialog.clear()         # 移除陈旧卡元素（关卡不自动清子元素）
            return
        if not pending:
            return
        rid = pending.get("req_id")
        # 防重弹：本轮 rid 已被本 client 应答完（worker 尚未清 pending 的窗口）→ 不重弹（根因#3）
        if rid == dialog_state["done_rid"]:
            return
        # 先建卡 + 起后台任务，**成功后**才置 req_id——避免构造异常留下 req_id 非 None
        # 却无 _await_card 复位它的死锁（根因#2 非原子）。
        try:
            card_dialog.clear()
            options = pending.get("options") or []
            spec = pending.get("spec") or {}
            with card_dialog, ui.card().classes("w-[36rem]"):
                ui.label(spec.get("prompt") or "需要你的选择").classes("font-bold")
                if options:
                    for i, opt in enumerate(options):
                        desc = (opt.get("description") or opt.get("scope_summary") or "")\
                            if isinstance(opt, dict) else ""
                        with ui.card().classes("w-full"):
                            ui.label(f"[{i + 1}] {_opt_label(opt, i)}").classes("font-medium")
                            if desc:
                                ui.label(str(desc)[:160]).classes("text-xs text-gray-600")
                            ui.button("选这个",
                                      on_click=lambda _, o=opt: card_dialog.submit(o))\
                                .props("dense flat").mark(f"card-opt-{i}")
                else:
                    free = ui.input(spec.get("prompt") or "输入")
                    ui.button("提交", on_click=lambda: card_dialog.submit(
                        int(free.value) if spec.get("type") == "integer"
                        and str(free.value).isdigit() else free.value))
            card_dialog.open()
            background_tasks.create(_await_card(rid))
            dialog_state["req_id"] = rid     # ★ 仅在卡 + 任务都建成后才置（原子保证）
        except Exception as e:
            dialog_state["req_id"] = None
            card_dialog.clear()
            STATE.log_buffer.append(f"[gui] 走向卡渲染失败（已复位可重弹）：{e}")

    # —— 轮询：状态 chip + 日志增量（per-client 游标）+ 停顿桥 ——
    log_cursor = {"v": 0}   # per-client：每个 tab 各持独立游标（根因 B）

    def _tick():
        if STATE.running:
            status_label.set_text(
                f"运行中 {STATE.current_command} · step {STATE.current_step or '…'}")
        else:
            status_label.set_text("空闲")
        result_label.set_text(STATE.last_result)
        new, log_cursor["v"] = STATE.log_buffer.since(log_cursor["v"])
        for line in new:
            log_view.push(line)
        _sync_cards()

    ui.timer(0.5, _tick)


# ============ Plan 续跑 ============
def plans_page():
    _header("Plan 续跑")
    rows_holder = ui.column().classes("w-full gap-2")

    def _render():
        rows_holder.clear()
        items = RUNNER.list_resumable()
        with rows_holder:
            if not items:
                ui.label("没有未完成的 plan").classes("text-gray-500")\
                    .mark("no-plans")
            for it in items:
                with ui.card().classes("w-full"):
                    ui.label(f"{it['command']} · {it['project']} · key={it['key']}"
                             f" · 进度 {it['progress']}").classes("font-mono text-sm")
                    ui.label(it["plan_id"]).classes("text-xs text-gray-500")
                    ui.button("▶ 从断点续跑", on_click=lambda _, x=it: _resume(x))\
                        .props("dense")

    def _resume(it: dict):
        ok = RUNNER.start([it["command"]], it["project"], it["key"] or "",
                          resume_plan_id=it["plan_id"])
        ui.notify("已启动续跑（写作台看日志）" if ok else "已有流水线在运行",
                  type="positive" if ok else "warning")

    _render()
    ui.button("🔄 刷新", on_click=_render).props("flat")


# ============ 设置（BYOK 密钥管理）============
def settings_page():
    _header("设置")
    from core.gui.runner import (save_api_key, clear_api_key, profile_key_status,
                                  test_profile_connection, switch_active)
    try:
        data = list_profiles_masked()
    except Exception as e:
        ui.label(f"读取 profile 失败：{e}").classes("text-red-600")
        return

    ui.label("🔒 你的 API 密钥用 Windows 凭据管理器加密存储，绝不写入任何文件、绝不上传。")\
        .classes("text-sm text-gray-600")
    if not data.get("keyring_available", True):
        ui.label("⚠️ 本机安全存储不可用——密钥将无法保存，请联系支持。")\
            .classes("text-sm text-red-600").mark("keyring-unavailable")

    # 切换当前使用的模型（dev 改 .env / 分发版写用户态覆盖·不碰只读内置 config）
    with ui.row().classes("items-center gap-2"):
        ui.label("当前模型").classes("font-bold").mark("active-profile")
        names = [p["name"] for p in data["profiles"]] or [data["active"]]
        active_sel = ui.select(options=names, value=data["active"] or None)\
            .mark("active-select")

        def _switch():
            v = active_sel.value
            if v and switch_active(v):
                ui.notify(f"已切换到 {v}", type="positive")
                _render()
            else:
                ui.notify("切换失败", type="negative")
        active_sel.on_value_change(lambda e: _switch())

    rows_holder = ui.column().classes("w-full gap-2")

    def _render():
        rows_holder.clear()
        fresh = list_profiles_masked()
        with rows_holder:
            for prof in fresh["profiles"]:
                name = prof["name"]
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(name).classes("font-medium font-mono")
                        if prof["active"]:
                            ui.badge("当前使用").props("color=blue")
                        configured = prof["key_in_keyring"] or (
                            prof["api_key"] not in ("未配置",))
                        ui.badge("已配置" if configured else "未配置")\
                            .props(f"color={'green' if configured else 'grey'}")\
                            .mark(f"badge-{name}")
                    ui.label(f"{prof['model']} · {prof['base_url']} · "
                             f"{prof['protocol']}").classes("text-xs text-gray-500")
                    with ui.row().classes("items-center gap-2 w-full"):
                        key_input = ui.input(placeholder="粘贴你的 API key（如 sk-…）")\
                            .props("type=password").classes("flex-1")\
                            .mark(f"key-input-{name}")

                        def _save(n=name, ki=key_input):
                            val = (ki.value or "").strip()
                            if not val:
                                ui.notify("请先粘贴密钥", type="warning")
                                return
                            if save_api_key(n, val):
                                ki.set_value("")            # 立即清空·明文不留 DOM
                                ui.notify("已保存到本机安全存储", type="positive")
                                _render()
                            else:
                                ui.notify("保存失败：本机安全存储不可用", type="negative")

                        def _clear(n=name):
                            clear_api_key(n)
                            ui.notify("已清除该 profile 的密钥", type="info")
                            _render()

                        def _test(n=name):
                            async def _run():
                                from nicegui import run
                                ui.notify("测试连接中…", type="ongoing")
                                r = await run.io_bound(test_profile_connection, n)
                                ui.notify(r["message"],
                                          type="positive" if r["ok"] else "negative")
                            return _run()

                        ui.button("保存", on_click=_save).props("dense")\
                            .mark(f"btn-save-key-{name}")
                        ui.button("清除", on_click=_clear).props("flat dense")\
                            .mark(f"btn-clear-key-{name}")
                        ui.button("测试连接", on_click=_test).props("flat dense")\
                            .mark(f"btn-test-key-{name}")

    _render()
    ui.button("🔄 刷新", on_click=_render).props("flat")


def init_pages():
    """注册全部页面。NiceGUI 测试框架每测重置 Client.page_routes——
    页面注册必须可重复调用（官方「无 main.py 项目」测试模式）。"""
    ui.page("/")(index)
    ui.page("/plans")(plans_page)
    ui.page("/settings")(settings_page)


def main(native: bool = False, port: int = 8080):
    install_stderr_tee(STATE.log_buffer)
    init_pages()
    ui.run(title="若渝AI", port=port, native=native, reload=False,
           show=not native)


if __name__ in {"__main__", "__mp_main__"}:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", action="store_true", help="桌面窗口模式（需 pywebview）")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    main(native=args.native, port=args.port)
