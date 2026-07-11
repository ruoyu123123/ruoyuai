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
import os
import re
import sys
from datetime import datetime, timezone
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


def _model_arc_progress_scores(arc_progress: str) -> "tuple[float, float] | None":
    """VAD 模型给 arc_progress 原文打分，拆成 actor(偏正向决断)/experiencer(偏负向承受)
    两路强度——两路都从同一 (valence, arousal) 派生但方向相反，避免同源导致 corr=1.0：
    actor = arousal 中正向 valence 的部分（完成/突破/接受类决断动作多中性偏正）；
    experiencer = arousal 中负向 valence 的部分（崩溃/恐惧/绝望类事件多负面高唤醒）。
    模型不可用/未命中 → None，调用方回退关键词词典。
    """
    if not arc_progress or os.environ.get("RUOYU_NN_VAD") != "1":
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch([arc_progress]) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch([arc_progress])
    except Exception:
        return None
    if not preds or not preds[0]:
        return None
    p = preds[0]
    valence, arousal = p.get("valence"), p.get("arousal")
    if valence is None or arousal is None:
        return None
    try:
        valence, arousal = float(valence), float(arousal)
    except (TypeError, ValueError):
        return None
    actor_score = max(0.0, min(1.0, arousal * valence))
    experiencer_score = max(0.0, min(1.0, arousal * (1.0 - valence)))
    return actor_score, experiencer_score


def estimate_from_arc_progress(arc_progress: str, chapter: int = 0) -> tuple[float, float]:
    """fallback：从 character_continuity.arc_progress 字符串估算 actor/experiencer 强度。

    VAD 模型优先（RUOYU_NN_VAD=1）：直接用 arc_progress 原文的 valence/arousal 拆两路；
    模型不可用 → 回退关键词词典（v22.cluster 改进：actor 强调主动动作类关键词，experiencer
    强调承受感受类关键词，各自独立估算 → 避免 corr=1.0 假相关）。
    """
    if not arc_progress:
        return 0.3, 0.3

    model_scores = _model_arc_progress_scores(arc_progress)
    if model_scores is not None:
        actor_score, experiencer_score = model_scores
    else:
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


def collect_character_deltas_from_chapter_json(
    chapter_dir: Path,
    known_characters: set[str],
    by_character: dict[str, list[dict]],
) -> None:
    """v22.4dim N4：从单章 JSON 的 dim4/dim7/dim8/dim12/golden_passages 文本中
    扫描已知角色名是否出现 → 给现有 by_character 加新的 chapter 数据点（强度按章 dim33 估）。

    增强 fallback 准确度（不只依赖 character_continuity 一个字段）。
    """
    if not chapter_dir.exists() or not known_characters:
        return

    # 把已知角色名按长度降序（避免「HeroC」误匹配「HeroC.CharC5」时只匹中短）
    sorted_chars = sorted(known_characters, key=lambda n: -len(n))

    for f in sorted(chapter_dir.glob("第*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ch = d.get("chapter")
        if not isinstance(ch, int):
            continue

        # 拼接所有可能含角色名的 dim 文本
        text_blob = ""
        for top_key in ("B1_qualitative", "B3_techniques", "C_golden_passages"):
            sub = d.get(top_key, {}) or {}
            if isinstance(sub, dict):
                for v in sub.values():
                    if isinstance(v, str):
                        text_blob += " " + v
                    elif isinstance(v, list):
                        for item in v:
                            text_blob += " " + (json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item))
                    elif isinstance(v, dict):
                        text_blob += " " + json.dumps(v, ensure_ascii=False)
        if not text_blob:
            continue

        # 本章张力 = dim33 emotion_beats 最高 direction 强度
        chapter_intensity = 0.4
        beats = (d.get("B4_narrative_craft", {}) or {}).get("dim33_emotion_beats")
        if isinstance(beats, list) and beats:
            intensities = []
            for b in beats:
                if isinstance(b, dict):
                    direction = str(b.get("direction", ""))
                    if "爆" in direction or "高潮" in direction:
                        intensities.append(0.9)
                    elif "升" in direction:
                        intensities.append(0.6)
                    elif "缓" in direction or "停" in direction:
                        intensities.append(0.3)
                    elif "落差" in direction or "降" in direction:
                        intensities.append(0.4)
            if intensities:
                chapter_intensity = max(intensities)

        # 扫每个已知角色是否出现
        appeared = set()
        for name in sorted_chars:
            if name and name in text_blob:
                # 用完整 name(known_characters 的 canonical key)·不取 split 短名(2026-06-15 审计修)：
                # 原 short=re.split(name)[0] 与 continuity 路径的完整名 key('赵子龙·子龙'/'HeroC.CharC5')
                # 不一致 → 同角色裂成两 key + L227 existing 检查(按完整名 key)查不到 continuity 数据 →
                # 覆盖优先级失效误加重复数据点。统一用完整 canonical name。
                appeared.add(name)

        # 给每个出现的角色加章节数据点（避免覆盖已有的 continuity 数据）
        for name in appeared:
            existing = [e for e in by_character.get(name, []) if e["chapter"] == ch]
            if existing:
                # 已有 continuity 数据，跳过（continuity 优先级高）
                continue
            # actor / experiencer 用本章 dim33 张力 + 章号微抖动
            actor_v = min(1.0, chapter_intensity + ((ch * 7) % 13) / 100 - 0.06)
            experiencer_v = min(1.0, chapter_intensity * 0.9 + ((ch * 11) % 17) / 100 - 0.08)
            by_character.setdefault(name, []).append({
                "chapter": ch,
                "actor_value": round(max(0.05, actor_v), 3),
                "actor_label": "（章 dim 估算）",
                "experiencer_value": round(max(0.05, experiencer_v), 3),
                "experiencer_label": "（章 dim 估算）",
                "trigger_event": f"出现于 ch{ch}",
                "stage_progress": "",
            })


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
        if not isinstance(cdeltas, list):  # consumer tolerant：骨架/弱模型可能写 str("TODO")
            cdeltas = []
        for entry in cdeltas:
            if not isinstance(entry, dict):  # 容错 str/非 dict 元素
                continue
            name = entry.get("character")
            if not name:
                continue
            for cc in entry.get("chapter_changes", []) or []:
                ch = cc.get("chapter")
                if ch is None:
                    continue
                # 强制 int(2026-06-15 审计修)：弱模型/旧 schema 写 chapter:"5"/"ch5"(str)·原样存入
                # 会与 fallback/单章路径(强制 int)的 entries 混入同角色 → 后续 sorted(key=chapter)/
                # chs[-1]-chs[0]/max() 对 str-int 抛 TypeError 整角色崩。统一 int·非数字键跳过。
                try:
                    ch = int(ch)
                except (TypeError, ValueError):
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
            # 兼容多 schema：chapter_range / window
            range_str = data.get("chapter_range") or data.get("window") or ""
            if not isinstance(range_str, str):
                continue
            m = re.match(r"ch?(\d+)[-_]ch?(\d+)", range_str)
            if not m:
                continue
            arc_start, arc_end = int(m.group(1)), int(m.group(2))
            for cc in cc_list:
                # 兼容 cc 是 dict / str
                if isinstance(cc, dict):
                    name = cc.get("character")
                    arc_p = cc.get("arc_progress", "")
                elif isinstance(cc, str):
                    name = cc
                    arc_p = ""
                else:
                    continue
                if not name:
                    continue
                # 保证 arc_p 是 str
                if not isinstance(arc_p, str):
                    arc_p = str(arc_p) if arc_p else ""
                # v22.cluster 改进：actor/experiencer 各自按 chapter 抖动估算，避免恒定相关
                for ch in range(arc_start, arc_end + 1):
                    existing = [e for e in by_character.get(name, []) if e["chapter"] == ch]
                    if existing:
                        continue
                    actor_v, experiencer_v = estimate_from_arc_progress(arc_p, chapter=ch)
                    by_character.setdefault(name, []).append({
                        "chapter": ch,
                        "actor_value": actor_v,
                        "actor_label": "（fallback估算）",
                        "experiencer_value": experiencer_v,
                        "experiencer_label": "（fallback估算）",
                        "trigger_event": arc_p[:60],
                        "stage_progress": arc_p[:60],
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


def compute_stanford_6component(name: str, entries: list[dict], total_book_chapters: int) -> dict:
    """v22.4dim Round2 应用：Stanford 6-component 角色重要度模型（Brahman et al.）

    业界依据：Round 2 调研 inspiration_4dim_sync_round2_2026-05-24.md 子任务 D 发现：
    Stanford 论文用 N/C/I/A/DC/DN 6 维度量化角色重要度，比单一"出场章数"更准。

    映射到我们已有数据：
    - N (Naming)         = 该角色被 mention 的章数 / 全书章数
    - C (Communication)  = 该角色有对话的章数比例（用 stage_progress 含对话信号近似）
    - I (Interiority)    = 该角色心理活动章节比例（从 dim33 emotion_beats 信号近似 = experiencer_value 均值）
    - A (Agency)         = 该角色主动行动比例（actor_value 均值）
    - DC (Direct Char.)  = 该角色被「直接描写性格」的章节占比（从 stage_progress 长度信号近似）
    - DN (Description by Narrator) = 叙述者描写该角色比例（出场密度 × 占比）

    返回 6 维分数 0-1 + 综合重要度 0-1。
    """
    n_chapters = len(entries)
    if n_chapters == 0 or total_book_chapters == 0:
        return {}

    N = round(n_chapters / total_book_chapters, 3)
    # C: 含对话信号的章比例（trigger_event/stage_progress 含「说/答/问/对话/告诉」）
    dialog_keywords = ("说", "答", "问", "告诉", "对话", "宣告", "回答", "提问")
    C = round(sum(1 for e in entries
                  if any(k in e.get("trigger_event", "") + e.get("stage_progress", "") for k in dialog_keywords))
              / n_chapters, 3)
    # I: experiencer 均值
    I = round(sum(e.get("experiencer_value", 0.3) for e in entries) / n_chapters, 3)
    # A: actor 均值
    A = round(sum(e.get("actor_value", 0.3) for e in entries) / n_chapters, 3)
    # DC: stage_progress 长度信号
    DC = round(min(1.0, sum(len(e.get("stage_progress", "")) for e in entries) / (n_chapters * 30)), 3)
    # DN: 出场密度 × N（连续章 / 总章）
    if n_chapters >= 2:
        chs = sorted(e["chapter"] for e in entries)
        density = n_chapters / (chs[-1] - chs[0] + 1)
        DN = round(N * density, 3)
    else:
        DN = N

    importance = round((N + C + I + A + DC + DN) / 6, 3)

    return {
        "stanford_6_component": {
            "N_naming": N,
            "C_communication": C,
            "I_interiority": I,
            "A_agency": A,
            "DC_direct_char": DC,
            "DN_description_by_narrator": DN,
            "overall_importance": importance,
            "_doc": "v22.4dim Stanford 6-component 角色重要度（Brahman et al.）· importance > 0.5 = 主角级 / 0.3-0.5 配角 / < 0.3 路人",
            "tier": "protagonist" if importance > 0.5 else ("supporting" if importance > 0.3 else "minor"),
        }
    }


def build_character_arc(name: str, entries: list[dict], total_book_chapters: int = 0) -> dict:
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

    arc_dict = {
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
            "distill_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "aggregator_version": "v22.4dim.1",
            "marcus_paradigm": True,
            "stanford_6component": True,
            "savitzky_golay_substitute": "simple_3_window_mean",
        },
    }
    # v22.4dim Round 2 应用：Stanford 6-component 角色重要度
    if total_book_chapters > 0:
        arc_dict.update(compute_stanford_6component(name, entries, total_book_chapters))
    return arc_dict


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

    # v22.4dim N4 增强：从单章 JSON 的 dim4/dim8/dim12/golden 文本扫角色名
    chapter_dir = project / "蒸馏进度"
    known_chars = set(by_character.keys())
    before_total = sum(len(v) for v in by_character.values())
    collect_character_deltas_from_chapter_json(chapter_dir, known_chars, by_character)
    after_total = sum(len(v) for v in by_character.values())
    print(f"[info] N4 增强：从单章 JSON 加了 {after_total - before_total} 个数据点（{before_total} → {after_total}）")

    targets = [args.character] if args.character else list(by_character.keys())

    # 估算全书总章数（用于 Stanford 6-component 标准化）
    total_chs = 0
    for entries in by_character.values():
        for e in entries:
            total_chs = max(total_chs, e.get("chapter", 0))

    written = []
    for name in targets:
        entries = by_character.get(name, [])
        if len(entries) < args.min_appearances:
            print(f"[skip] {name}: 仅 {len(entries)} 章出场 < min_appearances={args.min_appearances}")
            continue
        arc = build_character_arc(name, entries, total_book_chapters=total_chs)
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
