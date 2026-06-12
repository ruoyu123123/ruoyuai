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


GOLD = "#C9A86A"        # 描金 · 字数增量仪式
DARK_PANEL = "#232F42"  # 墨蓝加深 · 日志区（与品牌同族·替代外来 slate）


def apply_theme():
    """每页开头调用（NiceGUI 按页渲染·ui.colors 全局生效但 head_html 须每页注入）。

    写作平台调研落地（橙瓜/Scrivener/iA/Novelcrafter 合成·2026-06-12）：
    排印三档字号纪律·tabular-nums 大数字·墨蓝呼吸脉冲点·描金增量闪烁。"""
    ui.colors(primary=PRIMARY, secondary=SECONDARY, accent=ACCENT,
              dark="#1D2A38", positive="#2E7D32", negative=ACCENT,
              warning="#B7791F", info="#3E5C76")
    ui.add_head_html(
        '<style>'
        f':root{{--ry-ink:{PRIMARY};--ry-paper:{PAPER};'
        f'--ry-gold:{GOLD};--ry-dark:{DARK_PANEL}}}'
        f'body{{background:{PAPER};font-size:15px;line-height:1.6;'
        'font-family:"Noto Sans SC","Microsoft YaHei",system-ui,sans-serif}'
        '.nicegui-log{border-radius:8px}'
        '.stat-number{font-size:28px;font-weight:700;color:var(--ry-ink);'
        'font-variant-numeric:tabular-nums;line-height:1.1}'
        '.dark-panel{background:var(--ry-dark) !important}'
        '.pulse-dot{width:8px;height:8px;border-radius:50%;'
        'background:var(--ry-ink);animation:ry-pulse 1.6s ease-in-out infinite}'
        '@keyframes ry-pulse{50%{opacity:.25}}'
        '.word-delta{color:var(--ry-gold);font-weight:600}'
        '.line-clamp-2{display:-webkit-box;-webkit-line-clamp:2;'
        '-webkit-box-orient:vertical;overflow:hidden}'
        '</style>')
