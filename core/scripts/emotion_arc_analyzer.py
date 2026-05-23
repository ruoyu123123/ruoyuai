#!/usr/bin/env python3
"""
emotion_arc_analyzer.py — 章节情绪弧线分析器（8段式轨迹+弧形分类）

设计思路参考 AI_NovelGenerator，为若渝AI重写适配版。

用法：
  python emotion_arc_analyzer.py <章节txt路径>

【v19 顾问制】情绪弧线问题（issues：扁平/幅度小/种类单一/张力未释放）都是
「情绪节奏建议」非「客观错误」—— 冷静叙事章 / 留白章故意压低情绪起伏可能合理。
输出 dict 带 gate_level="advisory"，advisory 即「AI 有充分理由可豁免」。
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写


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


_POS_HIGH =["狂喜","兴奋","激动","震撼","惊喜","狂热","沸腾","澎湃","欣喜若狂","热血沸腾","欢呼","振奋","豪情","大笑","痛快"]
_POS_LOW = ["欣慰","满足","安心","温暖","舒适","平静","宁静","释然","从容","坦然","惬意","愉悦","微笑","轻松","安宁"]
_NEG_HIGH = ["愤怒","暴怒","恐惧","绝望","崩溃","疯狂","狂怒","惊恐","歇斯底里","怒不可遏","痛不欲生","悲愤","惊骇","震怒"]
_NEG_LOW = ["忧伤","失落","惆怅","无奈","寂寞","孤独","落寞","黯然","惋惜","遗憾","叹息","苦涩","酸楚","凄凉","哀伤"]
_TENSION = ["紧张","不安","焦虑","忐忑","惶恐","警觉","戒备","心跳加速","屏住呼吸","冷汗","危险","威胁","逼近","千钧一发","迫在眉睫"]
_RELIEF = ["松了口气","如释重负","放下心来","终于","安全","化险为夷","转危为安","虚惊一场"]

_SEGMENTS = 8

def _count(text: str, markers: list[str]) -> int:
    return sum(text.count(m) for m in markers)

def _segment_metrics(seg: str) -> tuple[float, float, float]:
    ph, pl, nh, nl = _count(seg, _POS_HIGH), _count(seg, _POS_LOW), _count(seg, _NEG_HIGH), _count(seg, _NEG_LOW)
    tens = _count(seg, _TENSION)
    total = ph + pl + nh + nl
    if total == 0:
        return 0.0, 0.0, tens / max(len(seg) / 100, 1)
    valence = ((ph + pl) - (nh + nl)) / total
    intensity = (ph + nh) / total
    return valence, intensity, tens / max(len(seg) / 100, 1)

def _classify_arc(vals: list[float], ints: list[float]) -> str:
    if not vals or len(vals) < 3:
        return "flat"
    combined = [abs(v) + i for v, i in zip(vals, ints)]
    if max(combined) < 0.1:
        return "flat"
    diffs = [combined[i+1] - combined[i] for i in range(len(combined)-1)]
    rising = sum(1 for d in diffs if d > 0.05)
    falling = sum(1 for d in diffs if d < -0.05)
    n = len(diffs)
    if rising >= n * 0.7: return "rising"
    if falling >= n * 0.7: return "falling"
    peak_idx = combined.index(max(combined))
    if 1 <= peak_idx <= len(combined)-2 and combined[peak_idx] > max(combined[0], 0.01) * 1.5:
        return "peak"
    sign_changes = sum(1 for i in range(1, len(diffs)) if diffs[i] * diffs[i-1] < 0 and abs(diffs[i]) > 0.05)
    if sign_changes >= 3: return "oscillating"
    if rising > falling: return "rising"
    if falling > rising: return "falling"
    return "flat"

def analyze_emotional_arc(text: str) -> dict:
    if not text or len(text) < 500:
        return {"error": "文本过短", "arc_shape": "unknown"}
    seg_len = len(text) // _SEGMENTS
    if seg_len < 50:
        return {"error": "文本过短", "arc_shape": "unknown"}
    segments = [text[i*seg_len:(i+1)*seg_len] for i in range(_SEGMENTS)]
    if len(text) > _SEGMENTS * seg_len:
        segments[-1] += text[_SEGMENTS * seg_len:]

    vals, ints, tens = [], [], []
    for seg in segments:
        v, i, t = _segment_metrics(seg)
        vals.append(v); ints.append(i); tens.append(t)

    arc = _classify_arc(vals, ints)
    v_range = max(vals) - min(vals)
    i_range = max(ints) - min(ints)
    emo_range = (v_range + i_range) / 2

    varieties = set()
    if _count(text, _POS_HIGH) > 0: varieties.add("pos_high")
    if _count(text, _POS_LOW) > 0: varieties.add("pos_low")
    if _count(text, _NEG_HIGH) > 0: varieties.add("neg_high")
    if _count(text, _NEG_LOW) > 0: varieties.add("neg_low")
    if _count(text, _TENSION) > 0: varieties.add("tension")
    if _count(text, _RELIEF) > 0: varieties.add("relief")

    peak_pos = 0.0
    if tens and max(tens) > 0:
        peak_pos = tens.index(max(tens)) / max(len(tens)-1, 1)

    has_resolution = _count(text, _RELIEF) > 0

    issues = []
    if arc == "flat": issues.append("情绪曲线扁平——全章缺乏情绪起伏")
    if emo_range < 0.1 and arc != "flat": issues.append("情绪幅度过小——高低差不明显")
    if len(varieties) <= 1: issues.append("情绪种类单一——只有一种情绪类型")
    if max(tens) > 0 and not has_resolution: issues.append("紧张未释放——有张力高点但无缓解")

    score = 0.5
    if arc in ("peak", "oscillating", "rising"): score += 0.2
    if emo_range > 0.3: score += 0.15
    if len(varieties) >= 3: score += 0.1
    if has_resolution: score += 0.05
    score -= len(issues) * 0.1
    score = max(0.0, min(1.0, score))

    return {
        "segment_count": len(segments),
        "arc_shape": arc,
        "valence_trajectory": [round(v, 2) for v in vals],
        "intensity_trajectory": [round(v, 2) for v in ints],
        "tension_trajectory": [round(v, 2) for v in tens],
        "emotional_range": round(emo_range, 2),
        "emotional_variety": len(varieties),
        "tension_peak_position": round(peak_pos, 2),
        "has_resolution": has_resolution,
        "arc_quality_score": round(score, 3),
        "issues": issues,
        # v19 顾问制：情绪弧线问题都是「建议」非「客观错误」，gate_level=advisory 即可豁免
        "gate_level": "advisory",
    }

def main():
    if len(sys.argv) < 2:
        print("用法: python emotion_arc_analyzer.py <章节txt>", file=sys.stderr); sys.exit(2)
    # v18：统一走 chapter_io 取纯正文。旧实现只 split "---CHANGES---"，
    # 匹配不到 writer 实际输出的 "---CHANGES_FACTUAL---" → 情绪段被 JSON 污染。
    text = _read_body_compat(Path(sys.argv[1]))
    result = analyze_emotional_arc(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("issues"): sys.exit(1)

if __name__ == "__main__":
    main()
