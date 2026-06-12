#!/usr/bin/env python3
"""widgets.py — 写作平台感组件（2026-06-12 调研落地 · 零业务逻辑纯展示层）。

P1 当前书仪表卡（橙瓜书架卡 × Scrivener 进度环 × Novelcrafter context chips）
P3 只读章节目录（Scrivener Binder · 只导航不编辑·严守「驱动器非编辑器」）
抽独立文件：app.py 已 ~800 行·守用户全局「单文件尽量 ≤1000 行」约束。
"""
from __future__ import annotations

from nicegui import ui

from core.gui.theme import COMMAND_LABELS


def book_card():
    """当前书仪表卡：封面色块（书名首字）+ 大数字万字 + context chips。

    返回 refresh(info) 闭包——info 为 state.ProjectInfo 或 None。"""
    with ui.card().classes("w-full p-0 overflow-hidden").mark("book-card"):
        with ui.row().classes("w-full items-stretch gap-0 no-wrap"):
            with ui.element("div").classes(
                    "w-16 bg-primary flex items-center justify-center shrink-0"):
                cover_char = ui.label("书").classes(
                    "text-3xl font-bold").style("color:#F7F4EC")
            with ui.column().classes("flex-1 p-3 gap-1 min-w-0"):
                book_title = ui.label("—").classes("font-bold text-base truncate")
                with ui.row().classes("items-baseline gap-1"):
                    stat_words = ui.label("0").classes("stat-number")
                    ui.label("万字").classes("text-xs text-gray-500")
                    stat_meta = ui.label("").classes("text-xs text-gray-500 ml-2")
                chips_row = ui.row().classes("gap-1 flex-wrap")

    def refresh(info):
        if info is None:
            book_title.set_text("—")
            cover_char.set_text("书")
            stat_words.set_text("0")
            stat_meta.set_text("")
            chips_row.clear()
            return
        book_title.set_text(info.name)
        cover_char.set_text(info.name[:1] or "书")
        stat_words.set_text(info.total_wan)
        stat_meta.set_text(f"已写 {info.chapters_written} 章 · "
                           f"故事块 {info.clusters_done}/{info.clusters_total}")
        chips_row.clear()
        with chips_row:
            if info.style_name:
                ui.chip(f"风格 · {info.style_name}")\
                    .props("outline color=primary dense")
            if info.next_key:
                ui.chip(f"下一块 · {info.next_key}")\
                    .props("outline color=primary dense")
            act = COMMAND_LABELS.get(info.next_action)
            if act:
                ui.chip(f"建议 · {act}").props("outline color=secondary dense")

    return refresh


def chapter_catalog(scan_fn):
    """只读章节目录（折叠面板·默认收起不挤日志区）。

    scan_fn() → list[{num, title, chars}]（state.scan_chapters 的偏函数）。
    返回 refresh() 闭包。只读：行上不挂任何点击——驱动器不提供编辑入口。"""
    with ui.expansion("📚 章节目录").classes("w-full")\
            .props("dense header-class=text-sm").mark("chapter-list"):
        holder = ui.column().classes("w-full gap-0 max-h-64 overflow-auto")

    def refresh():
        try:
            chapters = scan_fn() or []
        except Exception:
            chapters = []
        holder.clear()
        with holder:
            if not chapters:
                ui.label("还没有章节——点「写故事块」开始")\
                    .classes("text-xs text-gray-400 py-2")
                return
            for c in chapters:
                with ui.row().classes(
                        "w-full items-center gap-2 py-0.5 border-b "
                        "border-gray-100 no-wrap"):
                    ui.label(f"第{c['num']:03d}章").classes(
                        "text-xs font-mono text-gray-500 shrink-0")
                    ui.label(c["title"] or "—").classes(
                        "text-sm truncate flex-1 min-w-0")
                    ui.label(f"{c['chars']:,} 字").classes(
                        "text-xs text-gray-400 shrink-0")\
                        .style("font-variant-numeric:tabular-nums")

    return refresh
