"""从完整 cluster 正文提取声明节拍的确定性证据。"""

from __future__ import annotations


BEAT_KEYWORDS = {
    "Opening Image": ("开场", "首场"),
    "Theme Stated": ("主题", "理念"),
    "Set-Up": ("铺垫", "日常", "介绍"),
    "Catalyst": ("催化", "意外", "事件", "异常", "异变", "震惊"),
    "Debate": ("犹豫", "权衡", "反复", "纠结"),
    "Break into Two": ("决定", "出发", "踏入", "新世界"),
    "B Story": ("副线", "支线"),
    "Fun and Games": ("历险", "试炼", "挑战"),
    "Midpoint": ("反转", "转折", "中点", "突变"),
    "Bad Guys Close In": ("反派", "逼近", "压迫", "围剿"),
    "All Is Lost": ("失去", "失败", "崩溃", "绝境"),
    "Dark Night of Soul": ("黑暗", "绝望", "心死"),
    "Break into Three": ("顿悟", "新决心", "再起"),
    "Finale": ("决战", "终局", "高潮"),
    "Final Image": ("收尾", "终章"),
}


def beat_keywords_for(beat: str | None) -> list[str]:
    if not beat:
        return []
    lowered = str(beat).strip().lower()
    prefix = lowered.split("_", 1)[0]
    for name, keywords in BEAT_KEYWORDS.items():
        canonical = name.lower()
        if (
            lowered in canonical
            or canonical in lowered
            or prefix in canonical
            or any(keyword.casefold() in lowered for keyword in keywords)
        ):
            return list(keywords)
    return []


def addressed_beats(declared: list[str], text: str) -> list[str]:
    """返回正文中有明确词面证据的声明节拍。"""
    addressed = []
    lowered_text = text.casefold()
    for beat in declared:
        name = str(beat).strip()
        if not name:
            continue
        keywords = beat_keywords_for(name)
        if name.casefold() in lowered_text or any(keyword in text for keyword in keywords):
            addressed.append(name)
    return addressed


__all__ = ["BEAT_KEYWORDS", "addressed_beats", "beat_keywords_for"]
