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

import re  # noqa: E402

from nicegui import background_tasks, ui  # noqa: E402

from core.gui.runner import (PipelineRunner, active_key_ready,  # noqa: E402
                              list_profiles_masked)
from core.gui.state import (AppState, classify_line,  # noqa: E402
                            install_stderr_tee)
from core.gui.theme import COMMAND_LABELS, apply_theme  # noqa: E402
from core.gui.widgets import book_card, chapter_catalog  # noqa: E402

# P4 日志着色（NovelAI 来源着色 × iA「颜色只传信息」·classify_line 与持久日志同源）
_LOG_LV_CLS = {"ERROR": "text-red-400", "WARN": "text-amber-400"}


def _log_line_classes(line: str) -> str:
    level, comp = classify_line(line)
    if level in _LOG_LV_CLS:
        return _LOG_LV_CLS[level]
    return "text-emerald-300" if comp == "orch" else "text-slate-300"


def _log_title(text: str):
    """日志区标题 + 运行态脉冲点（墨蓝呼吸·bind STATE.running）。"""
    with ui.row().classes("items-center gap-2"):
        ui.element("div").classes("pulse-dot")\
            .bind_visibility_from(STATE, "running")
        ui.label(text).classes("text-sm text-gray-500")

STATE = AppState()
RUNNER = PipelineRunner(STATE)

_NAV = [("写作台", "/"), ("新建书", "/new-book"), ("蒸馏风格", "/distill"),
        ("Plan 续跑", "/plans"), ("设置", "/settings")]

# Windows 文件名保留字（A7·书名/风格名校验）
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _valid_name(s: str) -> bool:
    """书名/风格名将成为目录名：禁 Windows 非法字符 + 保留字 + 首尾空格点（防目录穿越）。"""
    return bool(s) and not re.search(r'[\\/:*?"<>|]', s) \
        and s.strip(" .") == s and s.upper() not in _RESERVED


def _check_key_ready(warn_label=None) -> bool:
    """A3 四入口统一 key 预检（与流水线真实消费路径一致·见 runner.active_key_ready）。"""
    ready, note = active_key_ready()
    if not ready:
        msg = f"⚠️ {note}"
        if warn_label is not None:
            warn_label.set_text(msg)
        else:
            ui.notify(note, type="warning")
    elif warn_label is not None:
        warn_label.set_text("")
    return ready


def _page_shell(title: str, route: str):
    """B2 页面骨架：主题 + header（当前页高亮）+ 返回统一内容容器。"""
    apply_theme()
    with ui.header().classes("items-center justify-between px-6"):
        ui.label(f"若渝AI · {title}").classes("text-lg font-bold tracking-wide")
        with ui.row().classes("gap-4"):
            for name, path in _NAV:
                cls = "text-white font-bold underline underline-offset-4" \
                    if path == route else "text-white/75 hover:text-white"
                ui.link(name, path).classes(cls)
    return ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4")


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


def _mount_pipeline_panel(status_label, result_label, log_view, *,
                          buttons: tuple = (), on_idle=None):
    """共享：走向卡 pause 弹窗 + 0.5s 轮询（status/日志/停顿桥）+ footer 全局状态条。

    buttons：动作按钮列表——运行中统一禁用（B4·防重复点击）。
    on_idle：任务完成边沿回调（A4·runs_finished 单调计数比对·刷新项目/风格/next_key）。
    对抗审查已收口机制：req_id 代际令牌防多 tab 抢答 + 陈旧卡·per-client 日志游标·
    背景任务 _await_card 与 _tick 解耦（_tick 不阻塞·能中断陈旧卡）。"""
    card_dialog = ui.dialog().props("persistent")
    dialog_state = {"req_id": None, "done_rid": None}

    # —— B2/B3 全局状态条（固定底部·5 页一致可见）+ P2 字数仪式（橙瓜底栏 ×
    #    Scrivener Session Target：全书 N 万字 ｜ 本次 +M 字描金闪）——
    with ui.footer().classes("bg-white text-gray-800 border-t px-6 py-1 items-center gap-4"):
        foot_progress = ui.linear_progress(value=0, show_value=False)\
            .props("instant-feedback").classes("w-40")
        foot_status = ui.label("空闲").classes("text-xs font-mono")
        foot_result = ui.label("").classes("text-xs truncate flex-1")
        foot_words = ui.label("").classes("text-xs text-gray-600")\
            .style("font-variant-numeric:tabular-nums").mark("foot-words")
        foot_delta = ui.label("").classes("text-xs word-delta")\
            .style("font-variant-numeric:tabular-nums")

    def _selected_chars() -> int:
        """must_fix#3：跨页可用——直接重扫 selected 项目（_WC_CACHE 命中纯 stat 开销）。"""
        try:
            from core.gui.state import scan_project
            p = STATE.project()
            if p:
                return scan_project(p.root).total_chars
        except Exception:
            pass
        return 0

    def _refresh_foot_words():
        try:
            from core.gui.state import scan_project
            p = STATE.project()
            if p:
                fresh = scan_project(p.root)
                foot_words.set_text(f"《{fresh.name}》{fresh.total_wan} 万字")
            else:
                foot_words.set_text("")
        except Exception:
            pass

    def _notify_safe(msg, **kw):
        """background task 无 slot 上下文·裸 ui.notify 抛 RuntimeError（轮次1 实测
        Traceback）——降级只写日志（通知本就非关键·日志区可见）。"""
        try:
            ui.notify(msg, **kw)
        except RuntimeError:
            STATE.log_buffer.append(f"[gui] {msg}")

    async def _await_card(rid):
        try:
            answer = await card_dialog
        except Exception:
            answer = None
        try:
            if answer is None:
                STATE.log_buffer.append(f"[gui:card] 走向卡失效（超时/已处理） req_id={rid}")
                _notify_safe("该选择已失效——有新卡会自动弹出；若任务已停，"
                             "去「Plan 续跑」从断点继续", type="warning")
            elif not STATE.bridge.respond(answer, req_id=rid):
                STATE.log_buffer.append(f"[gui:card] 选择未生效（陈旧/超时） req_id={rid}")
                _notify_safe("该选择未生效（已在别处选过或超时）——有新卡会自动弹出",
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
            # B5 走向卡美化：整卡可点 + hover + badge 序号 + primary 标题条
            with card_dialog, ui.card().classes(
                    "w-[40rem] max-h-[80vh] overflow-auto rounded-xl p-0"):
                with ui.row().classes("w-full bg-primary text-white px-4 py-2 "
                                      "items-center gap-2"):
                    ui.icon("alt_route")
                    ui.label(spec.get("prompt") or "需要你的选择")\
                        .classes("font-bold")
                with ui.column().classes("w-full p-4 gap-2"):
                    if options:
                        for i, opt in enumerate(options):
                            desc = (opt.get("description") or opt.get("scope_summary")
                                    or "") if isinstance(opt, dict) else ""
                            with ui.card().classes(
                                    "w-full cursor-pointer hover:shadow-md "
                                    "hover:border-primary transition-all")\
                                    .on("click", lambda _, o=opt: card_dialog.submit(o)):
                                with ui.row().classes("items-center gap-2"):
                                    ui.badge(str(i + 1)).props("color=primary")
                                    ui.label(_opt_label(opt, i)).classes("font-medium")
                                if desc:
                                    ui.label(str(desc)[:200])\
                                        .classes("text-xs text-gray-600")
                                ui.button("选这个",
                                          on_click=lambda _, o=opt:
                                          card_dialog.submit(o))\
                                    .props("unelevated color=primary dense")\
                                    .mark(f"card-opt-{i}")
                    else:
                        free = ui.input(spec.get("prompt") or "输入").classes("w-full")
                        ui.button("提交", on_click=lambda: card_dialog.submit(
                            int(free.value) if spec.get("type") == "integer"
                            and str(free.value).isdigit() else free.value))\
                            .props("unelevated color=primary")
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
    seen = {"v": STATE.runs_finished}      # A4 per-client 完成边沿
    run_edge = {"running": STATE.running, "words_before": None}  # P2 字数仪式

    _refresh_foot_words()                  # 进页即显示当前书字数

    def _tick():
        if STATE.running:
            cmd_cn = COMMAND_LABELS.get(STATE.current_command, STATE.current_command)
            txt = f"运行中 {cmd_cn} · {STATE.current_step or '…'}"
            status_label.set_text(txt)
            foot_status.set_text(txt)
            # B3 进度条：current_step 形如 "3/7 名称"
            m = re.match(r"(\d+)(?:\.\d+)?/(\d+)", STATE.current_step or "")
            foot_progress.set_value(
                min(int(m.group(1)) / max(int(m.group(2)), 1), 1.0) if m else 0)
        else:
            status_label.set_text("空闲")
            foot_status.set_text("空闲")
            foot_progress.set_value(0)
        result_label.set_text(STATE.last_result)
        foot_result.set_text(STATE.last_result)
        for b in buttons:                  # B4 运行中按钮统一禁用
            b.set_enabled(not STATE.running)
        # P2 字数仪式：开跑边沿记基线·完成边沿算增量（AI 替人码字的 session 正反馈）
        if STATE.running and not run_edge["running"]:
            run_edge["words_before"] = _selected_chars()
        run_edge["running"] = STATE.running
        if seen["v"] != STATE.runs_finished:   # A4 完成边沿 → 刷新数据
            seen["v"] = STATE.runs_finished
            if on_idle is not None:
                try:
                    on_idle()
                except Exception as e:
                    STATE.log_buffer.append(f"[gui] 完成回调失败：{e}")
            _refresh_foot_words()
            before = run_edge.get("words_before")
            if before is not None:
                delta = _selected_chars() - before
                run_edge["words_before"] = None
                if delta > 0:
                    foot_delta.set_text(f"本次 +{delta:,} 字")
                    try:
                        ui.notify(f"🎉 这一轮写了 {delta:,} 字",
                                  type="positive", timeout=6000)
                    except Exception:
                        pass
        new, log_cursor["v"] = STATE.log_buffer.since(log_cursor["v"])
        for line in new:
            log_view.push(line, classes=_log_line_classes(line))
        _sync_cards()

    ui.timer(0.5, _tick)


# ============ 写作台 ============
def index():
    shell = _page_shell("写作台", "/")
    STATE.log_buffer.append("[gui:page] 打开 写作台")
    STATE.refresh_projects()

    with shell:
        # B6 首开引导卡（无项目时·非技术用户三步指引）
        if not STATE.projects:
            with ui.card().classes("w-full bg-blue-50 border-l-4 border-primary"):
                ui.label("第一次用？三步开始：").classes("font-bold")
                with ui.row().classes("gap-2 items-center flex-wrap"):
                    ui.label("1️⃣")
                    ui.link("去「设置」粘贴你的 API 密钥", "/settings")
                    ui.label("→ 2️⃣")
                    ui.link("去「蒸馏风格」学一个作者", "/distill")
                    ui.label("→ 3️⃣")
                    ui.link("去「新建书」建第一本书", "/new-book")

        with ui.row().classes("w-full gap-4 items-start"):
            # —— 左：项目 + 操作 ——
            with ui.column().classes("w-1/3 gap-2"):
                names0 = [p.name for p in STATE.projects]
                project_select = ui.select(
                    options=names0 or ["（无项目）"],
                    value=STATE.selected or None, label="小说项目",
                ).classes("w-full").mark("project-select")
                if not names0:        # A10：无项目禁用·防假选项污染 STATE.selected
                    project_select.props("disable")

                # P1 当前书仪表卡（书架卡范式·下拉是选择器·卡片是展示层·两层并存）
                refresh_card = book_card()

                info_label = ui.label("").classes("text-sm text-gray-600")\
                    .mark("project-info")
                note_label = ui.label("").classes("text-xs text-orange-600")

                key_input = ui.input("写到第几块（自动填·一般不用改）")\
                    .classes("w-full").mark("key-input")
                auto_switch = ui.switch("全自动（走向卡取引擎第一候选·显式开关）")\
                    .mark("auto-pilot")

                _catalog = {"fn": lambda: None}        # P3 目录在右栏创建·容器后绑定

                def _sync_project():
                    STATE.selected = project_select.value or ""
                    p = STATE.project()
                    refresh_card(p)                    # P1 仪表卡
                    _catalog["fn"]()                   # P3 章节目录
                    if p:
                        info_label.set_text(
                            f"已写 {p.chapters_written} 章 · 故事块 {p.clusters_done}"
                            f"/{p.clusters_total} 已保存 · 建议：{p.next_action or '—'} "
                            f"key={p.next_key or '—'}")
                        note_label.set_text(p.note)
                        if not key_input.value and p.next_key:
                            key_input.set_value(p.next_key)

                def _on_idle():
                    """A4：任务完成边沿——刷新项目 + 下拉 + 无条件覆盖 next_key
                    （防陈旧 key 重写已完成 cluster·数据破坏级修复）。"""
                    STATE.refresh_projects()
                    names = [p.name for p in STATE.projects]
                    project_select.set_options(names or ["（无项目）"])
                    if names:
                        project_select.props(remove="disable")
                        if STATE.selected not in names:
                            project_select.set_value(names[0])
                    _sync_project()
                    p = STATE.project()
                    if p and p.next_key:
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
                        ui.notify("还没有可写的故事块——先去「新建书」建大纲",
                                  type="warning")
                        return
                    if not _check_key_ready():     # A3：写作台原来完全没预检
                        return
                    ok = RUNNER.start(commands, p.name, key,
                                      auto_pilot=bool(auto_switch.value))
                    if not ok:
                        # A12：线程启动失败时展示真实原因
                        ui.notify(STATE.last_result or "已有任务在运行",
                                  type="warning")

                with ui.row().classes("gap-2"):
                    btn_w = ui.button("✍ 写故事块",
                                      on_click=lambda: _start(["cluster-write"]))\
                        .props("outline color=primary").mark("btn-write")
                    btn_s = ui.button("💾 保存状态",
                                      on_click=lambda: _start(["cluster-save-state"]))\
                        .props("outline color=primary").mark("btn-save")
                    btn_b = ui.button("⚡ 连跑（写+存）",
                                      on_click=lambda: _start(
                                          ["cluster-write", "cluster-save-state"]))\
                        .props("unelevated color=primary").mark("btn-both")

                status_label = ui.label("空闲").classes("text-sm font-mono")\
                    .mark("status-label")
                result_label = ui.label("").classes("text-sm").mark("result-label")
                ui.button("🔄 刷新项目", on_click=_on_idle).props("flat dense")

            # —— 右：章节目录（P3·Binder 范式）+ 实时日志 ——
            with ui.column().classes("flex-1"):
                def _scan_sel_chapters():
                    from core.gui.state import scan_chapters
                    p = STATE.project()
                    return scan_chapters(p.root) if p else []
                _catalog["fn"] = chapter_catalog(_scan_sel_chapters)
                _catalog["fn"]()
                _log_title("流水线日志")
                log_view = ui.log(max_lines=400).classes(
                    "w-full h-96 font-mono text-xs dark-panel text-slate-200 rounded-lg")

    def _on_idle_full():
        _on_idle()
        _catalog["fn"]()                   # 完成边沿刷新目录（新章出现）

    _mount_pipeline_panel(status_label, result_label, log_view,
                          buttons=(btn_w, btn_s, btn_b), on_idle=_on_idle_full)


# ============ Plan 续跑 ============
def plans_page():
    shell = _page_shell("Plan 续跑", "/plans")
    STATE.log_buffer.append("[gui:page] 打开 Plan续跑")

    with shell:
        ui.label("上次任务中断了？从这里接着跑——选中的卡片和日志就在本页。")\
            .classes("text-sm text-gray-600")
        auto_sw = ui.switch("全自动续跑（选择取第一候选）").mark("plans-auto")
        rows_holder = ui.column().classes("w-full gap-2")

        def _render():
            rows_holder.clear()
            items = RUNNER.list_resumable()
            with rows_holder:
                if not items:
                    ui.label("🎉 没有未完成的任务").classes("text-gray-500")\
                        .mark("no-plans")
                    ui.label("一切正常——写作中断时这里会出现续跑入口").classes("text-xs text-gray-400")
                for it in items:
                    cmd_cn = COMMAND_LABELS.get(it["command"], it["command"])
                    try:
                        done, total = it["progress"].split("/")
                        ratio = int(done) / max(int(total), 1)
                    except (ValueError, ZeroDivisionError):
                        ratio = 0
                    with ui.card().classes("w-full"):
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.badge(cmd_cn).props("color=primary")
                            ui.label(it["project"]).classes("font-medium")
                            ui.label(f"进度 {it['progress']}")\
                                .classes("text-sm text-gray-600")
                        ui.linear_progress(value=ratio, show_value=False)\
                            .classes("w-full")
                        ui.label(it["plan_id"]).classes("text-xs text-gray-400")
                        ui.button("▶ 从断点续跑", on_click=lambda _, x=it: _resume(x))\
                            .props("unelevated color=primary dense")

        def _resume(it: dict):
            ok = RUNNER.start([it["command"]], it["project"], it["key"] or "",
                              resume_plan_id=it["plan_id"],
                              auto_pilot=bool(auto_sw.value))
            mode = "全自动" if auto_sw.value else "有选择时会弹卡"
            ui.notify(f"已启动续跑（{mode}）" if ok
                      else (STATE.last_result or "已有任务在运行"),
                      type="positive" if ok else "warning")

        _render()
        ui.button("🔄 刷新", on_click=_render).props("flat")

        # A1 完整版：本页也挂状态/日志/走向卡——续跑后人不用跳页
        status_label = ui.label("空闲").classes("text-sm font-mono")\
            .mark("plans-status")
        result_label = ui.label("").classes("text-sm").mark("plans-result")
        _log_title("任务日志")
        log_view = ui.log(max_lines=400).classes(
            "w-full h-72 font-mono text-xs dark-panel text-slate-200 rounded-lg")

    _mount_pipeline_panel(status_label, result_label, log_view, on_idle=_render)


# ============ 设置（BYOK 密钥管理）============
def settings_page():
    shell = _page_shell("设置", "/settings")
    STATE.log_buffer.append("[gui:page] 打开 设置")
    from core.gui.runner import (save_api_key, clear_api_key,
                                  test_profile_connection, switch_active)
    try:
        data = list_profiles_masked()
    except Exception as e:
        with shell:
            ui.label(f"读取 profile 失败：{e}").classes("text-red-600")
        return

    shell.__enter__()
    ui.label("🔒 你的 API 密钥用 Windows 凭据管理器加密存储，绝不写入任何文件、绝不上传。")\
        .classes("text-sm text-gray-600")
    ui.label("💡 把密钥录到标了「当前使用」的那张卡上（左侧有蓝色竖条）——"
             "录到别的卡上不会生效。")\
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
                STATE.log_buffer.append(f"[gui:event] 切换模型 → {v}")
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
                card_cls = "w-full border-l-4 border-primary" if prof["active"] \
                    else "w-full"
                with ui.card().classes(card_cls):
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

                        ui.button("保存", on_click=_save)\
                            .props("unelevated color=primary dense")\
                            .mark(f"btn-save-key-{name}")
                        ui.button("清除", on_click=_clear).props("flat dense")\
                            .mark(f"btn-clear-key-{name}")

                        test_btn = ui.button("测试连接").props("flat dense")\
                            .mark(f"btn-test-key-{name}")

                        def _test(n=name, btn=test_btn):
                            async def _run():
                                from nicegui import run
                                STATE.log_buffer.append(
                                    f"[gui:event] 测试连接 profile={n}")
                                btn.disable()        # B4 防抖：测试中禁点
                                # 🔴 实测修：ui.notify(type="ongoing") 是永久型且
                                # fire-and-forget 无法关闭 → 「测试连接中」永挂。
                                # 换可关闭的 ui.notification + finally dismiss。
                                notif = ui.notification("测试连接中…", spinner=True,
                                                        timeout=None)
                                try:
                                    r = await run.io_bound(test_profile_connection, n)
                                    ui.notify(r["message"],
                                              type="positive" if r["ok"] else "negative")
                                finally:
                                    notif.dismiss()
                                    btn.enable()
                            return _run()
                        test_btn.on_click(_test)

    _render()
    ui.button("🔄 刷新", on_click=_render).props("flat")
    shell.__exit__(None, None, None)   # 配对 __enter__（整页元素都进统一容器）


# ============ 新建书（创建书籍·阶段2） ============
def new_book():
    import json
    from core.gui.state import NOVELS_DIR

    shell = _page_shell("新建书", "/new-book")
    STATE.log_buffer.append("[gui:page] 打开 新建书")

    with shell:
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
                    STATE.log_buffer.append(
                        f"[gui:event] 点击 开始建书 book={book or '(空)'}")
                    if not book:
                        ui.notify("先填书名", type="warning")
                        return
                    if not _valid_name(book):     # A7：目录名合法性（防穿越/保留字）
                        ui.notify('书名不能包含 \\ / : * ? " < > | 这些符号',
                                  type="warning")
                        return
                    if STATE.running:             # A2：先查再 mkdir·防幽灵书锁死书名
                        ui.notify("已有任务在运行，等它完成再建书", type="warning")
                        return
                    # 轮次2 实测：零成本本地重名检查先行·_check_key_ready 有文件 IO+凭据
                    # 查询（并发下偶发 >0.8s）·放后面会让拒绝延迟不确定
                    proj = NOVELS_DIR / book
                    if (proj / "_数据库").exists():
                        ui.notify(f"《{book}》已存在——若上次建书中断，"
                                  f"去「Plan 续跑」页从断点继续；想重建请换个书名",
                                  type="warning")
                        return
                    if not _check_key_ready(warn_label):   # A3 统一预检
                        return
                    # 🔴 死锁②前置：RUNNER.start 前 mkdir + 写 book_meta.json（给 plan data_flow）
                    wal = proj / "_数据库" / ".wal"
                    wal.mkdir(parents=True, exist_ok=True)
                    (wal / "book_meta.json").write_text(
                        json.dumps({"topic": (topic_input.value or "").strip()
                                    or "网络小说"}, ensure_ascii=False), encoding="utf-8")
                    ok = RUNNER.start(["outline"], book, "",
                                      auto_pilot=bool(auto_switch.value))
                    if not ok:
                        # A2：启动被拒（竞态）→ 回滚本次创建物·不留幽灵书
                        import shutil
                        shutil.rmtree(proj / "_数据库", ignore_errors=True)
                        try:
                            proj.rmdir()
                        except OSError:
                            pass
                        ui.notify(STATE.last_result or "已有任务在运行", type="warning")
                    else:
                        warn_label.set_text("")
                        ui.notify(f"开始建《{book}》——跟着弹出的卡片选择就行",
                                  type="positive")

                btn_build = ui.button("📖 开始建书", on_click=_start_build)\
                    .props("unelevated color=primary").mark("btn-build")
                status_label = ui.label("空闲").classes("text-sm font-mono")\
                    .mark("nb-status")
                result_label = ui.label("").classes("text-sm").mark("nb-result")
                ui.link("建好后 → 去写作台写第一章", "/").classes("text-sm")

            with ui.column().classes("flex-1"):
                _log_title("建书日志")
                log_view = ui.log(max_lines=400).classes(
                    "w-full h-96 font-mono text-xs dark-panel text-slate-200 rounded-lg")

    _mount_pipeline_panel(status_label, result_label, log_view, buttons=(btn_build,))


# ============ 蒸馏（学作者风格·阶段3） ============
def distill():
    from core.gui.runner import scan_distill_styles

    shell = _page_shell("蒸馏风格", "/distill")
    STATE.log_buffer.append("[gui:page] 打开 蒸馏风格")

    with shell:
        # A6③：中断的蒸馏任务直接在本页续（不用知道 /plans 是啥）
        distill_plans = [it for it in RUNNER.list_resumable()
                         if it["command"] == "distill-style"]
        if distill_plans:
            with ui.card().classes("w-full bg-amber-50 border-l-4 border-warning"):
                ui.label("有学到一半的风格：").classes("font-bold text-sm")
                for it in distill_plans:
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"《{it['project']}》进度 {it['progress']}")\
                            .classes("text-sm")
                        ui.button("▶ 继续学", on_click=lambda _, x=it: RUNNER.start(
                            [x["command"]], x["project"], x["key"] or "",
                            resume_plan_id=x["plan_id"]))\
                            .props("dense unelevated color=warning")

        # —— ① 学新风格（全程蒸馏·新用户旅程第一站·排前）——
        with ui.row().classes("w-full gap-4 items-start"):
            with ui.column().classes("w-1/3 gap-2"):
                ui.label("学新风格（喂作者作品 → 学出风格档）").classes("font-bold")
                fd_name = ui.input("风格库名（如 某作者）").classes("w-full")\
                    .mark("fd-name")
                # A5：大文本走 HTTP 上传（websocket 1MB 限制·全本小说粘贴必断连）
                uploaded = {"text": ""}
                ui.upload(label="上传作者作品 .txt（可多选·推荐·支持全本）",
                          multiple=True, max_file_size=200 * 1024 * 1024,
                          on_upload=lambda e: uploaded.__setitem__(
                              "text", uploaded["text"] +
                              e.content.read().decode("utf-8", errors="replace")))\
                    .props("accept=.txt").classes("w-full").mark("fd-upload")
                fd_text = ui.textarea("或直接粘贴正文（含「第N章」标题·"
                                      "粘贴上限约 50 万字，全本请用上传）")\
                    .classes("w-full").mark("fd-text")
                fd_auto = ui.switch("全自动").mark("fd-auto")
                fd_warn = ui.label("").classes("text-xs text-red-600").mark("fd-warn")

                def _start_full_distill():
                    name = (fd_name.value or "").strip()
                    text = (uploaded["text"] or fd_text.value or "").strip()
                    STATE.log_buffer.append(
                        f"[gui:event] 点击 学风格 name={name or '(空)'} 文本{len(text)}字")
                    if not name:
                        ui.notify("先填风格库名", type="warning")
                        return
                    if not _valid_name(name):     # A7
                        ui.notify('名字不能包含 \\ / : * ? " < > | 这些符号',
                                  type="warning")
                        return
                    if len(text) < 500:
                        fd_warn.set_text("⚠️ 作品正文太少（建议几十章·至少几千字）")
                        return
                    if STATE.running:             # A6②：拆开两种拒因
                        ui.notify("已有任务在运行，等它完成再学", type="warning")
                        return
                    if not _check_key_ready(fd_warn):   # A3 统一预检
                        return
                    ok = RUNNER.start_full_distill(name, text,
                                                   auto_pilot=bool(fd_auto.value))
                    if ok:
                        fd_warn.set_text("")
                        ui.notify(f"开始学《{name}》风格——看右边日志", type="positive")
                    else:
                        ui.notify(f"《{name}》风格库已存在——如之前学到一半，"
                                  f"用上方「继续学」按钮", type="warning")

                btn_fd = ui.button("🎓 开始学风格", on_click=_start_full_distill)\
                    .props("unelevated color=primary").mark("btn-full-distill")
                status_label = ui.label("空闲").classes("text-sm font-mono")\
                    .mark("distill-status")
                result_label = ui.label("").classes("text-sm font-bold")\
                    .mark("distill-result")

            with ui.column().classes("flex-1"):
                _log_title("蒸馏日志")
                log_view = ui.log(max_lines=400).classes(
                    "w-full h-96 font-mono text-xs dark-panel text-slate-200 rounded-lg")

        ui.separator()
        # —— ② 复刻测试（已有风格 → 仿写打分）——
        with ui.row().classes("w-full gap-4 items-start"):
            with ui.column().classes("w-1/3 gap-2"):
                ui.label("复刻测试（已学风格 → AI 仿写一段 → 打分看像不像）")\
                    .classes("font-bold")
                try:
                    styles = scan_distill_styles()
                except Exception:
                    styles = []
                names = [s["name"] for s in styles]
                style_sel = ui.select(options=names or ["（无可复刻风格）"],
                                      value=(names[0] if names else None),
                                      label="风格库（有 skill + 原文≥5 章）")\
                    .classes("w-full").mark("distill-style")
                if not names:
                    style_sel.props("disable")
                    ui.label("还没有可复刻的风格——先在上面「学新风格」学一个")                        .classes("text-xs text-gray-400")
                ref_input = ui.input("参考故事块（默认 cluster_001·一般不用改）",
                                     value="cluster_001")\
                    .classes("w-full").mark("distill-ref")
                warn = ui.label("").classes("text-xs text-red-600").mark("distill-warn")

                def _refresh_styles():
                    """A4：学完风格 → 下拉自动出现新风格。"""
                    try:
                        fresh = [s["name"] for s in scan_distill_styles()]
                    except Exception:
                        fresh = []
                    style_sel.set_options(fresh or ["（无可复刻风格）"])
                    if fresh:
                        style_sel.props(remove="disable")
                        if style_sel.value not in fresh:
                            style_sel.set_value(fresh[0])

                def _start_replicate():
                    STATE.log_buffer.append(
                        f"[gui:event] 点击 测复刻 style={style_sel.value} "
                        f"ref={(ref_input.value or 'cluster_001').strip()}")
                    cur = [o for o in (style_sel.options or [])
                           if o != "（无可复刻风格）"]
                    if not cur:
                        ui.notify("没有可复刻的风格库（先在上面学一个）", type="warning")
                        return
                    if not _check_key_ready(warn):     # A3 统一预检
                        return
                    ok = RUNNER.run_replicate(style_sel.value,
                                              (ref_input.value or "cluster_001").strip())
                    if ok:
                        warn.set_text("")
                        ui.notify(f"开始复刻 {style_sel.value} —— 看上方日志和分数",
                                  type="positive")
                    else:
                        ui.notify(STATE.last_result or "已有任务在运行", type="warning")

                btn_rep = ui.button("🎭 测复刻", on_click=_start_replicate)\
                    .props("outline color=primary").mark("btn-replicate")
            with ui.column().classes("flex-1"):
                ui.label("提示：学好的风格会出现在左边列表，也能在「新建书」里选它当"
                         "写作基线。复刻分（SFS）越高越像该作者。")\
                    .classes("text-xs text-gray-500")

    _mount_pipeline_panel(status_label, result_label, log_view,
                          buttons=(btn_fd, btn_rep), on_idle=_refresh_styles)


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
    # A5 保底：socket.io 默认 1MB 缓冲——大 textarea 提交会静默断连（治本走 ui.upload）
    try:
        from nicegui import core
        core.sio.eio.max_http_buffer_size = 64 * 1024 * 1024
    except Exception:
        pass
    init_pages()
    ui.run(title="若渝AI", port=port, native=native, reload=False,
           show=not native, language="zh-CN")


if __name__ in {"__main__", "__mp_main__"}:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", action="store_true", help="桌面窗口模式（需 pywebview）")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    main(native=args.native, port=args.port)
