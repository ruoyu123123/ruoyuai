#!/usr/bin/env python3
"""release_calendar.py — 发布节奏守护（一人公司·A2·纯本地确定性·零 LLM 零联网·2026-06-15）

把 splitter 的快产能力转成平台最吃的「不断更」红利（R2 实证 RoyalRoad 14 月数据集证
「不断更是单一最强增长变量」）。读已写章节字数 + 平台日更目标 → 排期表 + 囤稿余量 +
断更风险预警。绕开 gen-model 限速（纯本地算术·绝不调 LLM/联网）。

北极星边界：
- 发布是格式/运营层（北极星④外延）·排期建议全 advisory·绝不变「不冲到 X 不让写下一章」hard_gate。
- 数据流单向：只读章节字数·绝不让发布节奏反向干涉写作主轨。

用法：python release_calendar.py <project> [--daily 6000] [--published-chars N]
退出码：0 成功（JSON 排期）· 2 项目无章节
"""
from __future__ import annotations

import re
from pathlib import Path

# 平台日更字数目标默认值（advisory·参考·非承诺·以平台最新规则为准）
DEFAULT_DAILY_CHAR_TARGET = 6000        # 番茄/起点全勤档典型日更（保守参考）

# 囤稿风险阈值（天）——advisory 分级
RISK_SAFE_DAYS = 7                      # ≥7 天囤稿 = 安全
RISK_WARNING_DAYS = 3                   # 3-7 天 = 预警 · <3 天 = critical

_CJK_RE = re.compile(r"[一-鿿]")


def _cjk_chars(text: str) -> int:
    return len(_CJK_RE.findall(text))


def scan_project_chars(project_root) -> tuple[int, int]:
    """扫 章节/第N章/第N章.txt → (章节数, CJK 总字数)。无章节返回 (0, 0)。"""
    root = Path(project_root)
    ch_dir = root / "章节"
    if not ch_dir.exists():
        return 0, 0
    count = 0
    total = 0
    for d in sorted(ch_dir.iterdir()):
        m = re.match(r"第(\d+)章", d.name)
        if not (m and d.is_dir()):
            continue
        txt = d / f"{d.name}.txt"
        if txt.exists():
            try:
                total += _cjk_chars(txt.read_text(encoding="utf-8"))
                count += 1
            except Exception:
                pass
    return count, total


def compute_release_schedule(total_chars: int, daily_char_target: int,
                             published_chars: int = 0) -> dict:
    """排期 + 囤稿余量 + 断更风险（纯算术·确定性·无依赖）。

    buffer = 已写 - 已发；days_of_buffer = buffer / 日更目标；按阈值分 safe/warning/critical。
    """
    dct = max(1, int(daily_char_target))
    buffer_chars = max(0, int(total_chars) - max(0, int(published_chars)))
    days_of_buffer = buffer_chars / dct
    if days_of_buffer >= RISK_SAFE_DAYS:
        risk = "safe"
    elif days_of_buffer >= RISK_WARNING_DAYS:
        risk = "warning"
    else:
        risk = "critical"
    return {
        "total_chars": int(total_chars),
        "published_chars": max(0, int(published_chars)),
        "buffer_chars": buffer_chars,
        "daily_char_target": dct,
        "days_of_buffer": round(days_of_buffer, 1),
        "risk_level": risk,
        "advice": _risk_advice(risk, round(days_of_buffer, 1)),
    }


def _risk_advice(risk: str, days: float) -> str:
    if risk == "safe":
        return f"囤稿 {days} 天·节奏健康·保持稳定日更最吃平台「不断更」算法红利。"
    if risk == "warning":
        return f"囤稿仅 {days} 天·建议提速写作补囤稿（断更伤追读·平台算法降权）。"
    return f"⚠️ 囤稿仅 {days} 天·断更风险高·优先补稿或下调日更目标避免硬断更。"


def main(argv=None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(prog="release_calendar",
                                 description="发布节奏守护（纯本地·排期+囤稿+断更预警）")
    ap.add_argument("project", help="小说项目根目录")
    ap.add_argument("--daily", type=int, default=DEFAULT_DAILY_CHAR_TARGET,
                    help=f"平台日更字数目标（默认 {DEFAULT_DAILY_CHAR_TARGET}）")
    ap.add_argument("--published-chars", type=int, default=0,
                    help="已发布字数（默认 0·全部当囤稿）")
    args = ap.parse_args(argv)

    count, total = scan_project_chars(args.project)
    if count == 0:
        print(json.dumps({"error": "no_chapters", "detail": "项目无已切章节"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    schedule = compute_release_schedule(total, args.daily, args.published_chars)
    schedule["chapter_count"] = count
    print(json.dumps(schedule, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
