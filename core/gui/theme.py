#!/usr/bin/env python3
"""theme.py — 若渝AI 统一主题（B1 · 书房墨蓝 × 宣纸暖白）。

面向写网文的非技术中文用户：沉稳不花哨·日志区保持深色终端感。
一次性定义品牌色 + 全局字体/底色，所有页面统一调用（取代零散 classes 散修）。
"""
from __future__ import annotations

from nicegui import ui

PRIMARY = "#34506B"     # 墨蓝 · header/主按钮
SECONDARY = "#4A7B6F"   # 黛绿 · 次操作（学风格等）
ACCENT = "#B5495B"      # 朱砂 · 强调/危险
PAPER = "#F7F4EC"       # 宣纸底色

# 命令名 → 人话（B3 进度可视化用）
COMMAND_LABELS = {
    "cluster-write": "写故事块",
    "cluster-save-state": "保存进度",
    "outline": "建书",
    "distill-style": "学风格",
    "复刻测试": "复刻测试",
}


def apply_theme():
    """每页开头调用（NiceGUI 按页渲染·ui.colors 全局生效但 head_html 须每页注入）。"""
    ui.colors(primary=PRIMARY, secondary=SECONDARY, accent=ACCENT,
              dark="#1D2A38", positive="#2E7D32", negative=ACCENT,
              warning="#B7791F", info="#3E5C76")
    ui.add_head_html(
        '<style>'
        f'body{{background:{PAPER};'
        'font-family:"Noto Sans SC","Microsoft YaHei",system-ui,sans-serif}'
        '.nicegui-log{border-radius:8px}'
        '</style>')
