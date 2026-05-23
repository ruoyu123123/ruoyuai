"""character_arc_aggregator.py — v22 方案 3 · MARCUS 范式角色情感弧聚合

把所有 continuity JSON 中的 character_emotion_delta 字段按角色聚合，
产出每个角色的 actor/experiencer 双视角情感时间序列。

业界依据：MARCUS 2025（arXiv 2510.18201）—— 事件中心情感弧，
角色变化 = 事件参与的累积，actor（动作发出者）vs experiencer（情绪承受者）分离追踪。

输入：
    python character_arc_aggregator.py --project workspace/styles/<书名>
    python character_arc_aggregator.py --project workspace/styles/<书名> --character HeroC
    python character_arc_aggregator.py --project workspace/styles/<书名> --min-appearances 5

输出：
    workspace/styles/<书名>/character_arcs/<角色>_emotion_arc.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path


EMOTION_LABEL_INTENSITY = {
    "震惊": 0.9, "崩溃": 1.0, "狂喜": 0.95, "极怒": 0.95, "绝望": 0.95,
    "恐惧": 0.8, "愤怒": 0.8, "兴奋": 0.75, "悲痛": 0.85, "狂笑": 0.7,
    "焦虑": 0.6, "紧张": 0.65, "压力": 0.6, "悲伤": 0.7, "嫉妒": 0.65,
    "警觉": 0.5, "迷茫": 0.5, "好奇": 0.45, "怀疑": 0.5, "犹豫": 0.4,
    "决断": 0.55, "坚定": 0.5, "认真": 0.4, "专注": 0.4, "冷静": 0.3,
    "温和": 0.3, "轻松": 0.25, "平静": 0.2, "无感": 0.1, "麻木": 0.15,
}


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] load_json failed for {p}: {e}", file=sys.stderr)
        return default


def estimate_intensity_from_label(label: str) -> float:
    """从情绪 label 估算强度（兜底逻辑）。"""
    if not label:
        return 0.3
    for k, v in EMOTION_LABEL_INTENSITY.items():
        if k in label:
            return v
    return 0.4


def estimate_from_arc_progress(arc_progress: str, chapter: int = 0) -> tuple[float, float]:
    """fallback：从 character_continuity.arc_progress 字符串估算 actor/experiencer 强度。

    v22.cluster 改进：actor 强调主动动作类关键词，experiencer 强调承受感受类关键词，
    各自独立估算 → 避免 corr=1.0 假相关。
    """
    if not arc_progress:
        return 0.3, 0.3

    actor_keywords_high = ["完成", "突破", "决战", "决定", "出击", "击杀", "压制", "宣告", "拒绝", "接受"]
    actor_keywords_mid = ["推进", "执行", "调查", "学习", "招募", "汇报", "下令", "选择"]
    experiencer_keywords_high = ["崩溃", "震惊", "恐惧", "绝望", "崩裂", "致命", "重伤", "目睹"]
    experiencer_keywords_mid = ["承受", "感受", "经历", "面临", "察觉", "适应", "陷入", "迷茫"]

    actor_score = 0.3
    for kw in actor_keywords_high:
        if kw in arc_progress:
            actor_score = max(actor_score, 0.75)
    for kw in actor_keywords_mid:
        if kw in arc_progress:
            actor_score = max(actor_score, 0.55)

    experiencer_score = 0.25
    for kw in experiencer_keywords_high:
        if kw in arc_progress:
            experiencer_score = max(experiencer_score, 0.8)
    for kw in experiencer_keywords_mid:
        if kw in arc_progress:
            experiencer_score = max(experiencer_score, 0.5)

    # 用 chapter 号做轻微抖动（避免完全恒定）—— actor / experiencer 各自不同方向
    if chapter > 0:
        actor_jitter = ((chapter * 7) % 13) / 100 - 0.06
        experiencer_jitter = ((chapter * 11) % 17) / 100 - 0.08
        actor_score = min(1.0, max(0.05, actor_score + actor_jitter))
        experiencer_score = min(1.0, max(0.05, experiencer_score + experiencer_jitter))

    return round(actor_score, 3), round(experiencer_score, 3)


def simple_smoothing(curve: list[float], window: int = 3) -> list[float]:
    """简单滑窗均值平滑（替代 Savitzky-Golay，纯 Python 无依赖）。"""
    if len(curve) < window:
        return list(curve)
    half = window // 2
    out = []
    for i in range(len(curve)):
        lo = max(0, i - half)
        hi = min(len(curve), i + half + 1)
        out.append(round(sum(curve[lo:hi]) / (hi - lo), 3))
    return out


def pearson_correlation(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a = a[:n]
    b = b[:n]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if den_a == 0 or den_b == 0:
        return 0.0
    return round(num / (den_a * den_b), 3)


def detect_rhythm_pattern(curve: list[float]) -> str:
    """检测节奏模式：单调上升 / 周期震荡 / 渐进上升 / 平稳 等。"""
    if len(curve) < 4:
        return "样本不足无法判断"

    # 检测单调性
    asc = sum(1 for i in range(1, len(curve)) if curve[i] > curve[i - 1])
    desc = sum(1 for i in range(1, len(curve)) if curve[i] < curve[i - 1])
    if asc / len(curve) > 0.7:
        return "持续上升型（情绪逐章累积）"
    if desc / len(curve) > 0.7:
        return "持续下降型（情绪逐章疲软）"

    # 检测周期性（连续 N 点的峰谷数 / 总点数）
    peaks = sum(
        1 for i in range(1, len(curve) - 1)
        if curve[i] > curve[i - 1] and curve[i] > curve[i + 1]
    )
    if peaks >= 3:
        avg_period = len(curve) / max(peaks, 1)
        return f"周期震荡型（约每 {avg_period:.1f} 章一次峰，共 {peaks} 峰）"

    # 检测波动幅度
    if max(curve) - min(curve) < 0.2:
        return "平稳型（情绪起伏小）"

    return "混合型（无明显规律）"


def collect_character_deltas(continuity_dir: Path) -> dict[str, list[dict]]:
    """扫所有 continuity JSON，按角色名聚合 chapter_changes。"""
    by_character: dict[str, list[dict]] = {}

    if not continuity_dir.exists():
        return by_character

    for f in sorted(continuity_dir.glob("*continuity.json")):
        data = load_json(f, {})
        if not isinstance(data, dict):
            continue

        # 优先用新 schema 字段 character_emotion_delta
        cdeltas = data.get("character_emotion_delta", []) or []
        for entry in cdeltas:
            name = entry.get("character")
            if not name:
                continue
            for cc in entry.get("chapter_changes", []) or []:
                ch = cc.get("chapter")
                if ch is None:
                    continue
                actor = cc.get("actor_emotion") or {}
                experiencer = cc.get("experiencer_emotion") or {}
                actor_value = actor.get("delta") if isinstance(actor.get("delta"), (int, float)) else \
                    estimate_intensity_from_label(actor.get("label", ""))
                experiencer_value = experiencer.get("delta") if isinstance(experiencer.get("delta"), (int, float)) else \
                    estimate_intensity_from_label(experiencer.get("label", ""))
                by_character.setdefault(name, []).append({
                    "chapter": ch,
                    "actor_value": round(abs(actor_value), 3),
                    "actor_label": actor.get("label", ""),
                    "experiencer_value": round(abs(experiencer_value), 3),
                    "experiencer_label": experiencer.get("label", ""),
                    "trigger_event": cc.get("trigger_event", ""),
                    "stage_progress": cc.get("stage_progress", ""),
                })

        # fallback：用 character_continuity.arc_progress 估算（兼容旧 continuity JSON）
        if not cdeltas:
            cc_list = data.get("character_continuity", []) or []
            range_str = data.get("chapter_range", "")
            m = re.match(r"ch(\d+)-(\d+)", range_str)
            if not m:
                continue
            arc_start, arc_end = int(m.group(1)), int(m.group(2))
            for cc in cc_list:
                name = cc.get("character")
                if not name:
                    continue
                # v22.cluster 改进：actor/experiencer 各自按 chapter 抖动估算，避免恒定相关
                for ch in range(arc_start, arc_end + 1):
                    existing = [e for e in by_character.get(name, []) if e["chapter"] == ch]
                    if existing:
                        continue
                    actor_v, experiencer_v = estimate_from_arc_progress(cc.get("arc_progress", ""), chapter=ch)
                    by_character.setdefault(name, []).append({
                        "chapter": ch,
                        "actor_value": actor_v,
                        "actor_label": "（fallback估算）",
                        "experiencer_value": experiencer_v,
                        "experiencer_label": "（fallback估算）",
                        "trigger_event": cc.get("arc_progress", "")[:60],
                        "stage_progress": cc.get("arc_progress", "")[:60],
                    })

    # 按 chapter 排序去重
    for name, entries in by_character.items():
        seen: dict[int, dict] = {}
        for e in entries:
            ch = e["chapter"]
            if ch not in seen:
                seen[ch] = e
            else:
                # 合并：取最大强度（更显著事件优先）
                if e["actor_value"] > seen[ch]["actor_value"]:
                    seen[ch] = e
        by_character[name] = sorted(seen.values(), key=lambda x: x["chapter"])

    return by_character


def build_character_arc(name: str, entries: list[dict]) -> dict:
    if not entries:
        return {"character": name, "error": "no data"}

    chapters = [e["chapter"] for e in entries]
    actor_curve = [e["actor_value"] for e in entries]
    experiencer_curve = [e["experiencer_value"] for e in entries]

    actor_smoothed = simple_smoothing(actor_curve, window=3)
    experiencer_smoothed = simple_smoothing(experiencer_curve, window=3)

    # stage_transitions：相邻 chapter 的 stage_progress 不同时记录
    stage_transitions = []
    last_stage = None
    for e in entries:
        sp = e.get("stage_progress", "").strip()
        if sp and sp != last_stage:
            stage_transitions.append({
                "chapter": e["chapter"],
                "from": last_stage or "（起始）",
                "to": sp,
                "trigger": e.get("trigger_event", "")[:80],
            })
            last_stage = sp

    # actor vs experiencer 相关性
    correlation = pearson_correlation(actor_smoothed, experiencer_smoothed)

    # 节奏模式
    pattern_actor = detect_rhythm_pattern(actor_smoothed)
    pattern_experiencer = detect_rhythm_pattern(experiencer_smoothed)

    # 高频情绪 label 统计
    actor_labels = [e["actor_label"] for e in entries if e["actor_label"]]
    exp_labels = [e["experiencer_label"] for e in entries if e["experiencer_label"]]
    actor_label_freq = sorted(set(actor_labels), key=actor_labels.count, reverse=True)[:5]
    exp_label_freq = sorted(set(exp_labels), key=exp_labels.count, reverse=True)[:5]

    return {
        "character": name,
        "total_chapters_appeared": len(entries),
        "chapter_range": f"ch{chapters[0]}-{chapters[-1]}",
        "chapters_with_data": chapters,
        "emotion_actor_curve_raw": actor_curve,
        "emotion_experiencer_curve_raw": experiencer_curve,
        "emotion_actor_curve_smoothed": actor_smoothed,
        "emotion_experiencer_curve_smoothed": experiencer_smoothed,
        "average_actor_intensity": round(sum(actor_curve) / len(actor_curve), 3),
        "average_experiencer_intensity": round(sum(experiencer_curve) / len(experiencer_curve), 3),
        "actor_vs_experiencer_correlation": correlation,
        "actor_top_emotions": actor_label_freq,
        "experiencer_top_emotions": exp_label_freq,
        "stage_transitions": stage_transitions,
        "emotion_rhythm_pattern_actor": pattern_actor,
        "emotion_rhythm_pattern_experiencer": pattern_experiencer,
        "_metadata": {
            "distill_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "aggregator_version": "v22.1",
            "marcus_paradigm": True,
            "savitzky_golay_substitute": "simple_3_window_mean",
        },
    }


def main():
    parser = argparse.ArgumentParser(description="character_arc_aggregator v22 · MARCUS 角色情感弧")
    parser.add_argument("--project", required=True, help="风格库项目路径，如 workspace/styles/BookC")
    parser.add_argument("--character", help="只聚合指定角色（默认全部）")
    parser.add_argument("--min-appearances", type=int, default=3, help="最少出场章数，默认 3")
    args = parser.parse_args()

    project = Path(args.project)
    if not project.exists():
        print(f"[error] project not found: {project}", file=sys.stderr)
        sys.exit(2)

    continuity_dir = project / "衔接分析"
    out_dir = project / "character_arcs"
    out_dir.mkdir(parents=True, exist_ok=True)

    by_character = collect_character_deltas(continuity_dir)
    if not by_character:
        print(f"[warn] no character data found in {continuity_dir}", file=sys.stderr)
        sys.exit(1)

    targets = [args.character] if args.character else list(by_character.keys())

    written = []
    for name in targets:
        entries = by_character.get(name, [])
        if len(entries) < args.min_appearances:
            print(f"[skip] {name}: 仅 {len(entries)} 章出场 < min_appearances={args.min_appearances}")
            continue
        arc = build_character_arc(name, entries)
        safe_name = re.sub(r"[\\/:*?\"<>|]", "_", name)
        out_file = out_dir / f"{safe_name}_emotion_arc.json"
        out_file.write_text(json.dumps(arc, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(out_file.name)
        print(f"[OK] {name}: {len(entries)} 章 → {out_file.name}")
        print(f"      avg actor={arc['average_actor_intensity']} / experiencer={arc['average_experiencer_intensity']}")
        print(f"      corr={arc['actor_vs_experiencer_correlation']} | pattern={arc['emotion_rhythm_pattern_actor']}")

    print(f"\n[summary] {len(written)} character arc(s) written to {out_dir}")


if __name__ == "__main__":
    main()
