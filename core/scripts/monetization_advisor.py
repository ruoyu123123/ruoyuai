#!/usr/bin/env python3
"""monetization_advisor.py — 变现参谋（一人公司·A1·纯函数零LLM零联网·收入端从零到一·2026-06-15）

给用户看「这本书在各平台能赚多少（区间）」，把第一轮完全缺席的收入端接上。照 gen_throttle.py
模块级常量风格（参数表常量·%APPDATA% 未来可叠 JSON 覆盖）。与 token_ledger 成本端对接成盈亏。

🔴 北极星边界（一切外延服从·绝不松动）：
- 纯 advisory 工具·**绝不进 cluster-write/save-state 写作主轨·绝不成 hard_gate**。
- **绝不让收入预期反向干涉作者风格创作**（数据流单向）。护城河是「写得像那位作者」不是「赚得多」。
- 默认返回**中位/保守区间**·头部个案数字只作 ceiling 灰字（防「AI 帮我写月入数万」错误预期·守「禁止我以为」）。
- 每条估算带 confidence + source + disclaimer「以平台官方作家专区合同为准」（一单一议·非承诺）。

用法：python monetization_advisor.py patreon --followers 1000
      python monetization_advisor.py breakeven --cost 80 --income 300
退出码：0 成功（JSON）· 2 参数错
"""
from __future__ import annotations

# ── 平台费率参数（advisory·confidence 分级·非承诺·以平台官方合同为准）──────────────
# high = R2 调研实据（Patreon/KU 出海数据最硬）· low = 高方差/未公开费率占位
PATREON_CONV_LO = 0.03          # 关注→付费转化下限（R2 RoyalRoad-Patreon·confidence high）
PATREON_CONV_HI = 0.13          # 上限
PATREON_TIER_AVG_USD = 5.0      # 档位均价 USD
KU_KENP_USD = 0.0043            # Kindle Unlimited 每页读取分成 USD（KENP·confidence high）
QIDIAN_SUB_PRICE_CNY = 0.025    # 起点单章订阅价粗估 CNY（千订分成高方差·confidence low）
QIDIAN_CHAPTERS_PER_MONTH = 30  # 月更章数粗估

_DISCLAIMER = "参考估算·以你登录看到的平台官方作家专区合同为准（一单一议·非承诺）"


def _result(platform: str, lo: float, hi: float, unit: str,
            confidence: str, note: str, source: str) -> dict:
    return {
        "platform": platform,
        "low": round(max(0.0, lo), 2),
        "high": round(max(0.0, hi), 2),
        "unit": unit,
        "confidence": confidence,        # high / low（low = 高方差·区间仅供方向参考）
        "note": note,
        "source": source,
        "disclaimer": _DISCLAIMER,
    }


def estimate_patreon(followers, conv_lo=PATREON_CONV_LO, conv_hi=PATREON_CONV_HI,
                     tier_avg=PATREON_TIER_AVG_USD) -> dict:
    """Patreon 月收入区间（关注数 × 付费转化 3-13% × 档位均价 $5·USD·confidence high）。"""
    f = max(0, int(followers))
    return _result("patreon", f * conv_lo * tier_avg, f * conv_hi * tier_avg, "USD/月",
                   "high", "付费转化 3-13%·档位均价 $5（R2 RoyalRoad-Patreon 出海数据）",
                   "patreon.com 创作者后台")


def estimate_ku(total_pages, kenp=KU_KENP_USD) -> dict:
    """Kindle Unlimited 月收入（KENP 总页读取 × $0.0043/页·USD·confidence high）。

    须 KDP Select 独家（KU 独家时序合规框·上 KU 期间不得他处发同作）。
    """
    p = max(0, int(total_pages))
    return _result("kindle_unlimited", p * kenp, p * kenp, "USD/月",
                   "high", "KENP $0.0043/页·须 KDP Select 独家（KU 独家时序合规）",
                   "kdp.amazon.com")


def estimate_qidian(avg_subs) -> dict:
    """起点千订月收入粗估（订阅分成·confidence low·千订单价高方差·保守区间仅供方向）。"""
    s = max(0, int(avg_subs))
    lo = s * QIDIAN_SUB_PRICE_CNY * QIDIAN_CHAPTERS_PER_MONTH
    hi = s * QIDIAN_SUB_PRICE_CNY * 2 * QIDIAN_CHAPTERS_PER_MONTH
    return _result("qidian", lo, hi, "CNY/月（粗估）",
                   "low", "千订分成高方差·此为方向粗估·真实看均订/全勤/打赏/分成档",
                   "起点作家专区合同")


def breakeven(monthly_token_cost, monthly_floor_income) -> dict:
    """盈亏平衡：月 API 成本 vs 月保底收入（对接 token_ledger 成本端·纯算术 confidence high）。"""
    cost = max(0.0, float(monthly_token_cost))
    income = max(0.0, float(monthly_floor_income))
    net = income - cost
    return {
        "monthly_token_cost": round(cost, 2),
        "monthly_floor_income": round(income, 2),
        "net": round(net, 2),
        "breakeven": net >= 0,
        "note": ("已覆盖 API 成本" if net >= 0
                 else "未覆盖 API 成本·需提收入或降成本（换更省 token 的档/减 best-of-N）"),
    }


def licensing_advisor() -> dict:
    """IP 授权分成 vs 买断对照（科普·非费率承诺·confidence low·法律免责硬前置）。"""
    return {
        "comparison": [
            {"type": "分成", "pro": "长尾收益·IP 增值共享", "con": "回款慢·依赖改编方运营"},
            {"type": "买断", "pro": "一次性确定回款·风险转移", "con": "放弃长尾·可能低估爆款潜力"},
        ],
        "disclaimer": "授权费一单一议·非平台公开费率·签约前务必专业法律咨询",
    }


def main(argv=None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(prog="monetization_advisor",
                                 description="变现参谋（纯函数·区间估算·advisory 非承诺）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_pat = sub.add_parser("patreon"); p_pat.add_argument("--followers", type=int, required=True)
    p_ku = sub.add_parser("ku"); p_ku.add_argument("--pages", type=int, required=True)
    p_qd = sub.add_parser("qidian"); p_qd.add_argument("--subs", type=int, required=True)
    p_be = sub.add_parser("breakeven")
    p_be.add_argument("--cost", type=float, required=True)
    p_be.add_argument("--income", type=float, required=True)
    sub.add_parser("licensing")
    args = ap.parse_args(argv)

    if args.cmd == "patreon":
        out = estimate_patreon(args.followers)
    elif args.cmd == "ku":
        out = estimate_ku(args.pages)
    elif args.cmd == "qidian":
        out = estimate_qidian(args.subs)
    elif args.cmd == "breakeven":
        out = breakeven(args.cost, args.income)
    elif args.cmd == "licensing":
        out = licensing_advisor()
    else:
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
