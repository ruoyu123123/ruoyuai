#!/usr/bin/env python3
"""
pacing_analyzer.py — 章节节奏分析器（段落分类+题材失衡检测）

设计思路参考 AI_NovelGenerator，为若渝AI重写适配版。

用法：
  python pacing_analyzer.py <章节txt路径> [--genre xuanhuan|romance|suspense|urban|general]

【v19 顾问制】节奏失衡（imbalance_flags）/ 段落单调（monotony_sequences）都是
「节奏建议」非「客观错误」—— 题材目标区间只是经验值，慢热铺垫章 / 高潮全战斗章
都可能合理偏离。输出 dict 带 gate_level="advisory"，advisory 即「AI 有充分理由可豁免」。
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写

_DLG_OPEN = re.compile('["「『"]')


def _read_body_compat(path: Path) -> str:
    """从章节 txt 路径取纯正文。能解析出 项目根+章节号 时走 cio.read_body()；
    否则退回 读原文 + 同口径剥离 CHANGES 段。杜绝各脚本各自 split。"""
    m = re.search(r"第(\d+)章", path.name)
    if m:
        ch = int(m.group(1))
        proj = (path.parent.parent.parent
                if path.parent.parent.name == "章节" else path.parent)
        try:
            return cio.read_body(proj, ch)
        except Exception:
            pass
    raw = path.read_text(encoding="utf-8")
    for sep in cio.CHANGES_SEPARATORS:
        if sep in raw:
            return raw.split(sep)[0].rstrip()
    return raw.rstrip()

_KW = {
    "action": [
        "冲", "砍", "劈", "刺", "闪", "躲", "挡", "踢", "打", "撞",
        "爆发", "攻击", "突破", "冲刺", "追", "逃", "跳", "拔刀", "挥拳",
        "轰", "炸", "碎", "裂", "崩", "震", "飞", "坠", "战斗", "搏斗",
    ],
    "description": [
        "阳光", "月光", "风", "雨", "雪", "雾", "山", "河", "湖", "海",
        "建筑", "宫殿", "街道", "房间", "走廊", "穿着", "容貌", "气质",
        "景色", "环境", "氛围", "灯", "花", "树", "石",
    ],
    "introspection": [
        "心想", "暗想", "思索", "琢磨", "回忆", "想起",
        "感觉", "觉得", "认为", "明白", "意识到",
        "心中", "内心", "脑海", "心底", "担心", "犹豫", "纠结",
    ],
    "transition": [
        "过了", "几天后", "不久", "随后", "此后", "第二天",
        "一周后", "半年后", "转眼间", "时间",
    ],
}

GENRE_TARGETS = {
    "xuanhuan":  {"action": (0.25, 0.45), "dialogue": (0.20, 0.35), "description": (0.10, 0.20), "introspection": (0.05, 0.15), "transition": (0.02, 0.10)},
    "romance":   {"action": (0.05, 0.15), "dialogue": (0.35, 0.55), "description": (0.10, 0.25), "introspection": (0.15, 0.30), "transition": (0.02, 0.10)},
    "suspense":  {"action": (0.15, 0.30), "dialogue": (0.30, 0.50), "description": (0.10, 0.20), "introspection": (0.10, 0.25), "transition": (0.02, 0.08)},
    "urban":     {"action": (0.10, 0.30), "dialogue": (0.30, 0.50), "description": (0.08, 0.20), "introspection": (0.08, 0.20), "transition": (0.02, 0.10)},
    "general":   {"action": (0.15, 0.35), "dialogue": (0.25, 0.45), "description": (0.10, 0.25), "introspection": (0.08, 0.20), "transition": (0.02, 0.10)},
}

def classify_paragraph(text: str) -> str:
    text = text.strip()
    if not text:
        return "transition"
    opens = len(_DLG_OPEN.findall(text))
    if opens > 0:
        dlg_chars = 0
        inside = False
        for ch in text:
            if ch in '"「『"': inside = True
            elif ch in '"」』': inside = False
            elif inside: dlg_chars += 1
        if len(text) > 0 and dlg_chars / len(text) > 0.3:
            return "dialogue"
    scores = {}
    for typ, kws in _KW.items():
        scores[typ] = sum(1 for kw in kws if kw in text) * (1.5 if typ == "action" else 1.2 if typ == "introspection" else 2.0 if typ == "transition" else 1.0)
    best = max(scores, key=scores.get)
    if scores[best] >= 2.0:
        return best
    return "description" if len(text) > 20 else "transition"

def detect_monotony(seq: list[str], threshold: int = 3) -> list[dict]:
    results = []
    if len(seq) < threshold: return results
    run_type, run_start, run_len = seq[0], 0, 1
    for i in range(1, len(seq)):
        if seq[i] == run_type:
            run_len += 1
        else:
            if run_len >= threshold:
                results.append({"type": run_type, "start": run_start, "length": run_len})
            run_type, run_start, run_len = seq[i], i, 1
    if run_len >= threshold:
        results.append({"type": run_type, "start": run_start, "length": run_len})
    return results

def analyze_pacing(text: str, genre: str = "general") -> dict:
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if len(paras) < 5:
        return {"error": "段落数不足", "paragraph_count": len(paras)}
    types = [classify_paragraph(p) for p in paras]
    counts = {}
    for t in ["action", "dialogue", "description", "introspection", "transition"]:
        counts[t] = types.count(t)
    total = len(types)
    dist = {k: v / total for k, v in counts.items()}
    targets = GENRE_TARGETS.get(genre, GENRE_TARGETS["general"])
    flags = []
    for typ, (lo, hi) in targets.items():
        val = dist.get(typ, 0)
        if val < lo: flags.append(f"{typ} 占比 {val:.1%} 低于目标 {lo:.0%}")
        elif val > hi: flags.append(f"{typ} 占比 {val:.1%} 高于目标 {hi:.0%}")
    mono = detect_monotony(types)
    score = 1.0
    score -= len(flags) * 0.15
    score -= len(mono) * 0.1
    score = max(0.0, min(1.0, score))
    return {
        "paragraph_count": total,
        "type_distribution": {k: round(v, 3) for k, v in dist.items()},
        "type_counts": counts,
        "monotony_sequences": mono,
        "imbalance_flags": flags,
        "pacing_score": round(score, 3),
        "genre": genre,
        # v19 顾问制：节奏失衡 / 单调都是「建议」非「客观错误」，gate_level=advisory 即可豁免
        "gate_level": "advisory",
    }

def main():
    if len(sys.argv) < 2:
        print("用法: python pacing_analyzer.py <章节txt> [--genre general]", file=sys.stderr); sys.exit(2)
    path = Path(sys.argv[1])
    genre = "general"
    for i, a in enumerate(sys.argv):
        if a == "--genre" and i + 1 < len(sys.argv): genre = sys.argv[i + 1]
    # v18：统一走 chapter_io 取纯正文。旧实现只 split "---CHANGES---"，匹配不到
    # writer 实际输出的 "---CHANGES_FACTUAL---"，导致 JSON 行被当段落 → 节奏分布失真。
    text = _read_body_compat(path)
    result = analyze_pacing(text, genre)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("imbalance_flags") or result.get("monotony_sequences"):
        sys.exit(1)

if __name__ == "__main__":
    main()
