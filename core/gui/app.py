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
            ui.link("新建书", "/new-book").classes("text-white")
            ui.link("蒸馏风格", "/distill").classes("text-white")
            ui.link("Plan 续跑", "/plans").classes("text-white")
            ui.link("设置", "/settings").classes("text-white")


def _opt_label(opt, i):
    """走向卡/灵感卡可读标签（label/title/logline/scope_summary 兜底·不暴露裸 dict）。"""
    if not isinstance(opt, dict):
        return str(opt)[:80]
    if opt.get("label"):
        return opt["label"]
    if opt.get("title"):
        return opt["title"]
    if opt.get("logline"):           # 灵感卡用 logline（gen_creative brainstorm）
        return str(opt["logline"])[:60]
    scope = str(opt.get("scope_summary") or "")
    if scope:
        return scope.split("。")[0][:60]
    return opt.get("parent_me") or f"候选{i + 1}"


def _mount_pipeline_panel(status_label, result_label, log_view):
    """共享：走向卡/灵感卡 pause 弹窗 + 0.5s 轮询（status/日志/停顿桥）。写作台 + /new-book 复用。

    对抗审查已收口机制：req_id 代际令牌防多 tab 抢答 + 陈旧卡·per-client 日志游标·
    背景任务 _await_card 与 _tick 解耦（_tick 不阻塞·能中断陈旧卡）。"""
    card_dialog = ui.dialog().props("persistent")
    dialog_state = {"req_id": None, "done_rid": None}

    async def _await_card(rid):
        try:
            answer = await card_dialog
        except Exception:
            answer = None
        try:
            if answer is None:
                STATE.log_buffer.append(f"[gui:card] 走向卡失效（超时/已处理） req_id={rid}")
                ui.notify("走向卡已失效（已被处理或超时）——如需选择请到「Plan 续跑」页继续",
                          type="warning")
            elif not STATE.bridge.respond(answer, req_id=rid):
                STATE.log_buffer.append(f"[gui:card] 选择未生效（陈旧/超时） req_id={rid}")
                ui.notify("该选择未生效（走向已被处理或已超时）——请到「Plan 续跑」页继续",
                          type="warning")
            else:
                STATE.log_buffer.append(
                    f"[gui:card] 用户选择 req_id={rid} 选项={_opt_label(answer, 0)}")
        finally:
            dialog_state["done_rid"] = rid
            dialog_state["req_id"] = None
            card_dialog.clear()

    def _sync_cards():
        pending = STATE.bridge.pending
        if dialog_state["req_id"] is not None:
            if not pending or pending.get("req_id") != dialog_state["req_id"]:
                card_dialog.submit(None)
                card_dialog.clear()
            return
        if not pending:
            return
        rid = pending.get("req_id")
        if rid == dialog_state["done_rid"]:
            return
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
            STATE.log_buffer.append(
                f"[gui:card] 渲染走向卡 req_id={rid} 选项数={len(options)}")
            background_tasks.create(_await_card(rid))
            dialog_state["req_id"] = rid
        except Exception as e:
            dialog_state["req_id"] = None
            card_dialog.clear()
            STATE.log_buffer.append(f"[gui] 走向卡渲染失败（已复位可重弹）：{e}")

    log_cursor = {"v": 0}

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


# ============ 写作台 ============
def index():
    _header("写作台")
    STATE.log_buffer.append("[gui:page] 打开 写作台")
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
                STATE.log_buffer.append(
                    f"[gui:event] 点击 {'+'.join(commands)} "
                    f"project={p.name if p else '?'} key={key or '(空)'} "
                    f"auto={bool(auto_switch.value)}")
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

    _mount_pipeline_panel(status_label, result_label, log_view)


# ============ Plan 续跑 ============
def plans_page():
    _header("Plan 续跑")
    STATE.log_buffer.append("[gui:page] 打开 Plan续跑")
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
    STATE.log_buffer.append("[gui:page] 打开 设置")
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


# ============ 新建书（创建书籍·阶段2） ============
def new_book():
    import json
    from core.gui.state import NOVELS_DIR

    _header("新建书")
    STATE.log_buffer.append("[gui:page] 打开 新建书")
    ui.label("填书名 + 题材，点「开始建书」——AI 会带你选风格、选灵感卡、定框架，"
             "然后生成大纲 + 全套设定。").classes("text-sm text-gray-600")

    with ui.row().classes("w-full gap-4 items-start"):
        with ui.column().classes("w-1/3 gap-2"):
            name_input = ui.input("书名").classes("w-full").mark("book-name")
            topic_input = ui.textarea("题材方向（想写什么·一两句话）")\
                .classes("w-full").mark("book-topic")
            auto_switch = ui.switch("全自动（所有选择取第一候选）").mark("nb-auto")
            warn_label = ui.label("").classes("text-xs text-red-600").mark("nb-warn")

            def _start_build():
                book = (name_input.value or "").strip()
                STATE.log_buffer.append(f"[gui:event] 点击 开始建书 book={book or '(空)'}")
                if not book:
                    ui.notify("先填书名", type="warning")
                    return
                # 前置：active profile 须有 key（否则跑到调研/brainstorm 才 401）
                try:
                    data = list_profiles_masked()
                    active = data.get("active")
                    has_key = any(p["name"] == active and p.get("key_in_keyring")
                                  for p in data.get("profiles", []))
                    if not has_key:
                        warn_label.set_text("⚠️ 当前模型还没填密钥——先去「设置」录入你的 key 再建书")
                        return
                except Exception:
                    pass
                proj = NOVELS_DIR / book
                if (proj / "_数据库").exists():
                    ui.notify(f"《{book}》已存在——换个书名或去写作台续写", type="warning")
                    return
                # 🔴 死锁②前置：RUNNER.start 前真实 mkdir + 写 book_meta.json（topic 给 plan data_flow）
                wal = proj / "_数据库" / ".wal"
                wal.mkdir(parents=True, exist_ok=True)
                (wal / "book_meta.json").write_text(
                    json.dumps({"topic": (topic_input.value or "").strip() or "网络小说"},
                               ensure_ascii=False), encoding="utf-8")
                ok = RUNNER.start(["outline"], book, "",
                                  auto_pilot=bool(auto_switch.value))
                if not ok:
                    ui.notify("已有流水线在运行", type="warning")
                else:
                    warn_label.set_text("")
                    ui.notify(f"开始建《{book}》——跟着弹出的卡片选择就行", type="positive")

            ui.button("📖 开始建书", on_click=_start_build)\
                .props("color=primary").mark("btn-build")
            status_label = ui.label("空闲").classes("text-sm font-mono")\
                .mark("nb-status")
            result_label = ui.label("").classes("text-sm").mark("nb-result")
            ui.link("建好后 → 去写作台写第一章", "/").classes("text-sm")

        with ui.column().classes("flex-1"):
            ui.label("建书日志").classes("text-sm text-gray-500")
            log_view = ui.log(max_lines=400).classes("w-full h-96 font-mono text-xs")

    _mount_pipeline_panel(status_label, result_label, log_view)


# ============ 蒸馏（学作者风格·阶段3） ============
def distill():
    from core.gui.runner import scan_distill_styles

    _header("蒸馏风格")
    STATE.log_buffer.append("[gui:page] 打开 蒸馏风格")
    ui.label("「复刻测试」：拿一个已学好的风格库，让 AI 仿写一段、打分看像不像。"
             "（学新风格的完整蒸馏正在接入）").classes("text-sm text-gray-600")

    with ui.row().classes("w-full gap-4 items-start"):
        with ui.column().classes("w-1/3 gap-2"):
            ui.label("复刻测试（导入现成风格 → 仿写 → 看 SFS 分）").classes("font-bold")
            try:
                styles = scan_distill_styles()
            except Exception:
                styles = []
            names = [s["name"] for s in styles]
            style_sel = ui.select(options=names or ["（无可复刻风格）"],
                                  value=(names[0] if names else None),
                                  label="风格库（有 skill + 原文≥5 章）")\
                .classes("w-full").mark("distill-style")
            ref_input = ui.input("cluster 参考（默认 cluster_001）", value="cluster_001")\
                .classes("w-full").mark("distill-ref")
            warn = ui.label("").classes("text-xs text-red-600").mark("distill-warn")

            def _start_replicate():
                STATE.log_buffer.append(
                    f"[gui:event] 点击 测复刻 style={style_sel.value} "
                    f"ref={(ref_input.value or 'cluster_001').strip()}")
                if not names:
                    ui.notify("没有可复刻的风格库（需有 skill + 原文≥5 章）", type="warning")
                    return
                try:
                    data = list_profiles_masked()
                    active = data.get("active")
                    if not any(p["name"] == active and p.get("key_in_keyring")
                               for p in data.get("profiles", [])):
                        warn.set_text("⚠️ 当前模型还没填密钥——先去「设置」录入 key")
                        return
                except Exception:
                    pass
                ok = RUNNER.run_replicate(style_sel.value,
                                          (ref_input.value or "cluster_001").strip())
                warn.set_text("" if ok else "")
                if ok:
                    ui.notify(f"开始复刻 {style_sel.value} —— 看右边日志和分数", type="positive")
                else:
                    ui.notify("已有任务在运行", type="warning")

            ui.button("🎭 测复刻", on_click=_start_replicate)\
                .props("color=primary").mark("btn-replicate")
            status_label = ui.label("空闲").classes("text-sm font-mono")\
                .mark("distill-status")
            result_label = ui.label("").classes("text-sm font-bold").mark("distill-result")

        with ui.column().classes("flex-1"):
            ui.label("蒸馏日志").classes("text-sm text-gray-500")
            log_view = ui.log(max_lines=400).classes("w-full h-96 font-mono text-xs")

    ui.separator()
    # —— 学新风格（全程蒸馏·阶段3）——
    with ui.row().classes("w-full gap-4 items-start"):
        with ui.column().classes("w-1/3 gap-2"):
            ui.label("学新风格（喂作者作品 → 学出风格档）").classes("font-bold")
            fd_name = ui.input("风格库名（如 某作者）").classes("w-full").mark("fd-name")
            fd_text = ui.textarea("作者作品正文（一大段·含「第N章」标题·越多越准）")\
                .classes("w-full").mark("fd-text")
            fd_auto = ui.switch("全自动").mark("fd-auto")
            fd_warn = ui.label("").classes("text-xs text-red-600").mark("fd-warn")

            def _start_full_distill():
                name = (fd_name.value or "").strip()
                text = (fd_text.value or "").strip()
                if not name:
                    ui.notify("先填风格库名", type="warning")
                    return
                if len(text) < 500:
                    fd_warn.set_text("⚠️ 作品正文太少（建议贴几十章·至少几千字）")
                    return
                try:
                    data = list_profiles_masked()
                    active = data.get("active")
                    if not any(p["name"] == active and p.get("key_in_keyring")
                               for p in data.get("profiles", [])):
                        fd_warn.set_text("⚠️ 当前模型还没填密钥——先去「设置」录入 key")
                        return
                except Exception:
                    pass
                ok = RUNNER.start_full_distill(name, text,
                                               auto_pilot=bool(fd_auto.value))
                if ok:
                    fd_warn.set_text("")
                    ui.notify(f"开始学《{name}》风格——看右边日志", type="positive")
                else:
                    ui.notify("已有任务在运行 / 该风格库已存在", type="warning")

            ui.button("🎓 开始学风格", on_click=_start_full_distill)\
                .props("color=secondary").mark("btn-full-distill")
        with ui.column().classes("flex-1"):
            ui.label("提示：学完后会出现在上面「复刻测试」的风格库列表里，"
                     "也能在「新建书」里选它当写作基线。").classes("text-xs text-gray-500")

    _mount_pipeline_panel(status_label, result_label, log_view)


def init_pages():
    """注册全部页面。NiceGUI 测试框架每测重置 Client.page_routes——
    页面注册必须可重复调用（官方「无 main.py 项目」测试模式）。"""
    ui.page("/")(index)
    ui.page("/new-book")(new_book)
    ui.page("/distill")(distill)
    ui.page("/plans")(plans_page)
    ui.page("/settings")(settings_page)


def main(native: bool = False, port: int = 8080):
    install_stderr_tee(STATE.log_buffer)
    # 启动即打印日志文件路径（实测时方便取日志分析界面+命令状态）
    lp = STATE.log_buffer.log_file_path
    if lp:
        msg = f"[gui:run] 日志文件：{lp}"
        STATE.log_buffer.append(msg)
        print(msg, file=sys.stderr)
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
