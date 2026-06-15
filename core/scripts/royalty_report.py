#!/usr/bin/env python3
"""royalty_report.py — 财务/分成对账（一人公司·1.5·纯确定性聚合·零 LLM·2026-06-15）

多平台结算 CSV（用户手动从各平台导出整理成通用格式）→ 月度收入对账报表（按月/平台/书聚合）。
与变现参谋（monetization_advisor 事前估算）互补:这是事后对账。范式平移 world_evolution_engine
客观数值确定性算 delta。纯 Python 不调 LLM·不联网·可本机单测。

通用 CSV 格式（用户手动整理·避免抓平台后台撞 ToS）:
  date,platform,book,amount,currency
  2026-06-01,起点,某书,1234.56,CNY
  2026-06-01,patreon,某书,89.00,USD

北极星边界:纯数据搬运·不碰创作判断（北极星④外延）·决策建议（砍书/转题材）须 advisory 绝不自动执行·
输入手动 CSV 而非自动登录抓后台（避 ToS）·绝不让收入数据反向干涉作者风格创作（数据流单向）。

用法:python royalty_report.py <csv_path>
退出码:0 成功（JSON 报表）· 2 文件不存在/无有效行
"""
from __future__ import annotations

import csv
from pathlib import Path


def parse_csv(path) -> list[dict]:
    """读通用结算 CSV → [{date, platform, book, amount, currency}]。坏行跳过（容错）。"""
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    try:
        with p.open(encoding="utf-8-sig", newline="") as f:   # utf-8-sig 吃 Excel BOM
            for raw in csv.DictReader(f):
                try:
                    amt = float(str(raw.get("amount", "")).replace(",", "").strip())
                except (ValueError, TypeError):
                    continue                                   # 金额非数 → 跳过坏行
                rows.append({
                    "date": str(raw.get("date", "")).strip(),
                    "platform": str(raw.get("platform", "?")).strip() or "?",
                    "book": str(raw.get("book", "?")).strip() or "?",
                    "amount": amt,
                    "currency": str(raw.get("currency", "CNY")).strip() or "CNY",
                })
    except OSError:
        return []
    return rows


def aggregate_royalties(rows) -> dict:
    """结算明细 → 月度对账报表（按币种分组·币种内按月/平台/书聚合·纯确定性·不混加币种）。"""
    by_currency: dict = {}
    for r in rows:
        cur = r.get("currency", "CNY")
        amt = float(r.get("amount", 0) or 0)
        month = str(r.get("date", ""))[:7]                    # YYYY-MM
        platform = r.get("platform", "?")
        book = r.get("book", "?")
        c = by_currency.setdefault(
            cur, {"total": 0.0, "by_month": {}, "by_platform": {}, "by_book": {}})
        c["total"] += amt
        c["by_month"][month] = c["by_month"].get(month, 0.0) + amt
        c["by_platform"][platform] = c["by_platform"].get(platform, 0.0) + amt
        c["by_book"][book] = c["by_book"].get(book, 0.0) + amt
    for c in by_currency.values():                            # round 2 + 排序
        c["total"] = round(c["total"], 2)
        for k in ("by_month", "by_platform", "by_book"):
            c[k] = {kk: round(vv, 2) for kk, vv in sorted(c[k].items())}
    return {"by_currency": by_currency, "row_count": len(rows)}


def main(argv=None) -> int:
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser(
        prog="royalty_report", description="财务对账（多平台 CSV → 月度报表·纯确定性）")
    ap.add_argument("csv_path", help="通用结算 CSV（date,platform,book,amount,currency）")
    args = ap.parse_args(argv)
    rows = parse_csv(args.csv_path)
    if not rows:
        print(json.dumps({"error": "no_valid_rows", "detail": "CSV 不存在或无有效行"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(aggregate_royalties(rows), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
