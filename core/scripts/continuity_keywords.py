# 🔴 2026-06-27 SYS-5 ① cliffhanger 关键词提取共享逻辑（单一来源 · 零 LLM 确定性）
"""continuity_keywords.py — cliffhanger 关键词/stop 词共享逻辑

【为什么抽公共函数】cluster_summary_builder（producer · 预算 cliffhanger_resonance_next）
与 cross_cluster_continuity_aggregate（consumer · 账本缺字段时回退正文重算）两边都要算
「前章 ending 关键词 ∩ 后章首段关键词」的重叠分。若两边各用一套关键词/stop 逻辑，则
builder 预算的分与 scanner 回退算的分**不可比** → cliffhanger 遥测失真。此模块是单一来源。

由 cross_cluster_continuity_aggregate.extract_keywords 历史实现原样上移（保持 stop 表 +
正则 + 动态主角名过滤完全一致），continuity scanner 改 import 本模块以对齐。
"""
from __future__ import annotations

import re

# 通用 stop 词（高频功能词 / 代词短语 → 过滤后 cliffhanger 重叠才反映实质内容）
STOP_WORDS = {
    "他的", "她的", "自己", "一个", "一下", "什么", "这种", "那个", "这个", "那种",
    "已经", "还是", "就是", "不是", "没有", "他在", "他想", "他说", "她说",
}


def extract_keywords(text: str, top_n: int = 20, protagonist: str | None = None) -> set[str]:
    """简易关键词：长度 ≥2 的中文/英文 + 时间戳 + 数字串。

    protagonist：当前项目主角名 → 加入 stop 过滤（主角名几乎每段都出现，不过滤会让
    cliffhanger 关键词重叠虚高）。不传则不滤主角名。零 LLM 确定性。

    🔴 2026-06-27 SYS-5 ①：长中文 run（≥4 字、无内部标点）会被正则吞成一个 mega-token
    （整句「会议彻底陷入僵局」=1 token），与对方文本几乎不可能逐字相等 → cliffhanger 重叠
    结构性恒 0%（假阴/噪声告警）。对纯中文长 run 补 2-gram 切分，让实质词（「会议」从
    「会议彻底陷入僵局」）能跨章命中，恢复遥测可信度。bigram 同样过 stop 过滤。
    """
    tokens = re.findall(r"[一-鿿]{2,}|[A-Za-z]{3,}|\d+[:：]\d+|\d{3,}", text or "")
    stop = set(STOP_WORDS)
    if protagonist:
        stop.add(protagonist)
    out: set[str] = set()
    for t in tokens:
        if t not in stop:
            out.add(t)
        if len(t) >= 4 and _is_pure_cjk(t):
            for i in range(len(t) - 1):
                bg = t[i:i + 2]
                if bg not in stop:
                    out.add(bg)
    return out


def _is_pure_cjk(tok: str) -> bool:
    return bool(_PURE_CJK_RE.fullmatch(tok))


_PURE_CJK_RE = re.compile(r"[一-鿿]+")
