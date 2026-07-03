"""arc_aggregator.py — v22.cluster 故事块 arc 聚合器（双轨主轨）

把已蒸馏的 continuity JSON + 单章 metrics + 单章 JSON **按 cluster 颗粒度**聚合成 arc。
对齐写作端 ECAS 故事块模式（gen_writer.py --cluster N），避免"蒸馏 3 章固定 / 写作 cluster 可变"颗粒度错位。

业界依据（详见 .research_cache/inspiration_cluster_distill_2026-05-24.md）：
- LumberChunker (EMNLP 2024 · arXiv 2406.17526) — variable-length 比 fixed-N +7.37% DCG@20
- MARCUS 2025 (arXiv 2510.18201) — event-centric character arc，跨整本书聚合
- Multi-Agent TV Arcs 2025 (arXiv 2503.04817) — arc 跨任意 episode，按情节单元自然终结
- Reagan 2016 (EPJ Data Science) — 6 基本情感弧形（保留作 shape classifier）

输入：
    # cluster 模式（推荐 · 主轨）：按 cluster_index.json 聚合
    python arc_aggregator.py --project workspace/styles/<书名> --cluster auto_002
    python arc_aggregator.py --project workspace/styles/<书名> --all-clusters
    # fixed10 模式（章节副轨 · 保留兼容）：每 10 章一段
    python arc_aggregator.py --project workspace/styles/<书名> --arc-end-chapter <N>
    # summary 模式：聚合全部 arc 出 6 形状分布
    python arc_aggregator.py --project workspace/styles/<书名> --mode summary

输出：
    workspace/styles/<书名>/arc_templates/cluster_arc_<cluster_id>.json   # 主轨（cluster 颗粒度）
    workspace/styles/<书名>/arc_templates/arc_<NNN>.json                  # 副轨（固定 10 章）
    workspace/styles/<书名>/arc_templates/arc_summary.json                # summary（覆盖两轨）

调用时机：
    distill-style.md 阶段 1.5（每个 cluster 蒸馏完后触发 cluster 模式 + 全书蒸馏完后跑 --all-clusters）
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


PACING_LABEL_TO_VALUE = {
    "极慢": 0.15,
    "慢": 0.30,
    "平稳": 0.40,
    "中慢": 0.45,
    "中": 0.50,
    "中快": 0.65,
    "快": 0.80,
    "极快": 0.95,
}


REAGAN_SHAPES = {
    "Rags-to-Riches": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    "Tragedy": [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1],
    "Man-in-a-Hole": [0.8, 0.5, 0.3, 0.2, 0.3, 0.4, 0.5, 0.7, 0.8, 0.9],
    "Icarus": [0.2, 0.3, 0.5, 0.7, 0.9, 1.0, 0.9, 0.7, 0.4, 0.2],
    "Cinderella": [0.2, 0.3, 0.5, 0.7, 0.8, 0.5, 0.3, 0.5, 0.7, 0.9],
    "Oedipus": [0.8, 0.7, 0.5, 0.3, 0.1, 0.3, 0.5, 0.6, 0.4, 0.2],
}


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] load_json failed for {p}: {e}", file=sys.stderr)
        return default


def parse_pacing_curve_to_values(curve_str: str, n_chapters: int) -> list[float]:
    """从 continuity.pacing_curve 字符串解析每章节奏数值。

    例：'Ch13中快...Ch14快慢快三段...Ch15慢平稳' → [0.65, 0.65, 0.30]
    取每章首个出现的 pacing label。
    """
    if not curve_str:
        return [0.5] * n_chapters

    out: list[float] = []
    chunks = re.split(r"Ch\d+|→", curve_str)
    chunks = [c.strip() for c in chunks if c.strip()]

    for chunk in chunks:
        matched = 0.5
        # 最长优先匹配（2026-06-15 审计修）：原按 dict 插入顺序·短 label「中」「快」排在含它的
        # 「中快」「极快」之前 → chunk「中快」先命中「中」(0.5)误判中速·应 0.65。对齐 sibling
        # parse_pacing_curve_to_labels 已用的 longest-first。错值会污染 emotion_curve 下游(Reagan/climax)。
        for label, value in sorted(PACING_LABEL_TO_VALUE.items(), key=lambda kv: -len(kv[0])):
            if label in chunk:
                matched = value
                break
        out.append(matched)

    while len(out) < n_chapters:
        out.append(0.5)
    return out[:n_chapters]


def parse_pacing_curve_to_labels(curve_str: str, n_chapters: int) -> list[str]:
    """从 pacing_curve 字符串提每章 label（慢/中/快）。"""
    if not curve_str:
        return ["中"] * n_chapters

    out: list[str] = []
    chunks = re.split(r"Ch\d+|→", curve_str)
    chunks = [c.strip() for c in chunks if c.strip()]

    for chunk in chunks:
        label = "中"
        for k in ("极快", "快", "中快", "中", "中慢", "慢", "极慢", "平稳"):
            if k in chunk:
                label = "快" if "快" in k else ("慢" if "慢" in k or "平稳" in k else "中")
                break
        out.append(label)

    while len(out) < n_chapters:
        out.append("中")
    return out[:n_chapters]


DIRECTION_INTENSITY = {
    "升": 0.6, "缓冲": 0.3, "爆发": 0.95, "高潮": 0.9, "落差": 0.4,
    "降": 0.3, "悬念": 0.55, "炸弹": 0.85, "震惊": 0.85, "崩溃": 0.95,
    "决断": 0.6, "停顿": 0.25, "余韵": 0.35, "起承": 0.4, "转折": 0.7,
    "平静": 0.2, "决战": 0.95, "极限": 0.95,
}


def _walk_nested(obj, key_substring: str):
    """递归在 dict 中找包含 key_substring 的字段，返回首个匹配的 value。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if key_substring in k:
                return v
            res = _walk_nested(v, key_substring)
            if res is not None:
                return res
    return None


def _direction_intensity_lexicon(d: str) -> float:
    """单条情绪方向词按 DIRECTION_INTENSITY 词典打分（VAD 不可用时的确定性 fallback）。"""
    matched = 0.3
    for kw, v in DIRECTION_INTENSITY.items():
        if kw in d:
            matched = max(matched, v)
    return matched


def _model_direction_intensity(directions: list) -> "float | None":
    """VAD 模型给情绪方向词打分：用 arousal 近似"强度"（DIRECTION_INTENSITY 本就是
    0.2平静-0.95决战极限的唤醒度量表，与 arousal 语义对齐）。模型不可用/未命中 → None，
    调用方回退词典。"""
    if not directions or os.environ.get("RUOYU_NN_VAD") != "1":
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch(directions) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch(directions)
    except Exception:
        return None
    vals = []
    for p in preds or []:
        if p and p.get("arousal") is not None:
            try:
                vals.append(float(p["arousal"]))
            except (TypeError, ValueError):
                pass
    if not vals:
        return None
    return min(1.0, max(vals))


def extract_chapter_emotion_intensity_detail(chapter_json: dict) -> dict:
    """同 extract_chapter_emotion_intensity，多带 source 字段（model_vad/lexicon_fallback）
    供训练池 / 测试区分证据来源。"""
    # 优先：递归找 dim33（无论嵌套层级）
    beats = _walk_nested(chapter_json, "dim33")
    if isinstance(beats, list) and beats:
        directions = [str(b.get("direction", "")) for b in beats if isinstance(b, dict)]
        if directions:
            non_empty = [d for d in directions if d.strip()]
            model_intensity = _model_direction_intensity(non_empty) if non_empty else None
            if model_intensity is not None:
                return {"intensity": round(model_intensity, 3), "source": "model_vad"}
            intensities = [_direction_intensity_lexicon(d) for d in directions]
            # 用最大值（不是平均）—— 本章情绪最高峰最有代表性
            return {"intensity": min(1.0, max(intensities)), "source": "lexicon_fallback"}

    # fallback：把 beats 当字符串扫高潮关键词
    if beats:
        beat_str = str(beats)
        climax_keywords = ["高潮", "爆发", "炸弹", "震惊", "崩溃", "决战", "极限"]
        score = sum(1 for kw in climax_keywords if kw in beat_str) * 0.25
        if score:
            return {"intensity": min(1.0, 0.3 + score), "source": "lexicon_fallback"}

    # fallback：humor + satisfaction
    humor = _walk_nested(chapter_json, "dim39")
    if isinstance(humor, dict):
        humor_n = humor.get("count", 0)
    elif isinstance(humor, (int, float)):
        humor_n = humor
    else:
        humor_n = 0

    sat = _walk_nested(chapter_json, "dim40")
    sat_n = sat if isinstance(sat, (int, float)) else 0

    raw = 0.3 + min(humor_n, 5) * 0.05 + min(sat_n, 5) * 0.05
    return {"intensity": min(1.0, max(0.0, raw)), "source": "lexicon_fallback"}


def extract_chapter_emotion_intensity(chapter_json: dict) -> float:
    """从单章 JSON 估算本章情绪强度（0-1）。

    兼容字段路径：
    - B4_narrative_craft.dim33_emotion_beats (list of {pct, direction}) — v17 标准 schema
    - qualitative.emotion_beat_map / 情绪节拍图 / dim33 — 旧 schema

    VAD 模型优先（RUOYU_NN_VAD=1 时对 direction 词条打分），词典 fallback（兼容旧调用）。
    """
    return float(extract_chapter_emotion_intensity_detail(chapter_json)["intensity"])


def extract_dim_value(chapter_json: dict, dim_names: list[str], default: float = 0.0) -> float:
    """从单章 JSON 取某维度数值（v22.cluster 升级 · 支持嵌套路径 + dim 数字自动递归）。

    dim_names 接受：
    - 数字关键词（如 "dim28"）→ 递归找含该子串的 key
    - 字段名（如 "scene_pct"）→ 递归找包含
    """
    for name in dim_names:
        v = _walk_nested(chapter_json, name)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            # dim28_scene_vs_summary = {'scene_pct': 0.85} → 取 scene_pct
            for inner_key in ("scene_pct", "total", "count", "mean", "value", "score"):
                if inner_key in v and isinstance(v[inner_key], (int, float)):
                    return float(v[inner_key])
        if isinstance(v, str):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%?", v)
            if m:
                num = float(m.group(1))
                return num / 100 if "%" in v or num > 1.5 else num
    return default


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def match_reagan_shape(curve: list[float]) -> tuple[str, float]:
    """拟合 emotion_curve 到 6 形状选 TOP1 + 置信度（余弦相似度）。"""
    if len(curve) != 10:
        # resample 到 10 点
        if len(curve) < 2:
            return ("Unknown", 0.0)
        curve = resample_to_n(curve, 10)

    best = ("Unknown", -1.0)
    for name, template in REAGAN_SHAPES.items():
        sim = cosine_similarity(curve, template)
        if sim > best[1]:
            best = (name, sim)
    return best


def resample_to_n(curve: list[float], n: int) -> list[float]:
    """简单线性插值 resample 到 n 点。"""
    if len(curve) == n:
        return curve
    out = []
    m = len(curve)
    for i in range(n):
        pos = i * (m - 1) / (n - 1) if n > 1 else 0
        lo = int(pos)
        hi = min(lo + 1, m - 1)
        frac = pos - lo
        out.append(curve[lo] * (1 - frac) + curve[hi] * frac)
    return out


def describe_arc_structure(curve: list[float]) -> str:
    """根据 emotion_curve 形状给出描述性 label。"""
    if not curve:
        return "未知"
    peak = max(curve)
    trough = min(curve)
    peak_idx = curve.index(peak)
    n = len(curve)

    if peak - trough < 0.2:
        return "平稳无明显起伏"

    if peak_idx == n - 1:
        return "持续上升（章末高潮）"
    if peak_idx == 0:
        return "高开后逐渐回落"
    if peak_idx < n // 3:
        return "早爆发后逐渐消化"
    if peak_idx > 2 * n // 3:
        return "缓铺垫-章末大高潮"

    # peak 在中段
    early_avg = sum(curve[: peak_idx]) / max(peak_idx, 1)
    late_avg = sum(curve[peak_idx + 1 :]) / max(n - peak_idx - 1, 1)
    if late_avg < early_avg * 0.7:
        return "升-峰-降（拱形）"
    return "升-峰-缓-再升（双峰倾向）"


def load_cluster_index(project: Path) -> dict | None:
    """v22.cluster：读 cluster_index.json（cluster_segmenter 产出）。"""
    f = project / "cluster_index.json"
    if not f.exists():
        return None
    return load_json(f, None)


def aggregate_cluster(project: Path, cluster_id: str) -> dict:
    """v22.cluster：按 cluster 颗粒度聚合 arc（主轨）。"""
    ci = load_cluster_index(project)
    if not ci:
        return {"error": "cluster_index.json not found · 请先跑 cluster_segmenter.py"}
    target = None
    for c in ci.get("clusters", []):
        if c.get("cluster_id") == cluster_id:
            target = c
            break
    if not target:
        return {"error": f"cluster_id={cluster_id} 不在 cluster_index 中"}

    arc_start, arc_end = target["chapter_range"]
    arc_size = arc_end - arc_start + 1
    data = _aggregate_chapter_range(project, arc_start, arc_end, arc_size)
    # cluster 模式额外字段
    data["cluster_id"] = cluster_id
    data["chapters_count"] = target["chapters_count"]
    data["estimated_words"] = target["estimated_words"]
    data["boundary_reason"] = target["boundary_reason"]
    data["mode"] = "cluster"
    # 替换 arc_id 为 cluster 命名
    data["arc_id"] = f"cluster_arc_{cluster_id}"

    # v22.4dim N3：mid_checkpoint 期望张力（writer 每 3000 字 checkpoint 时对照）
    data["mid_checkpoint_target_tensions"] = compute_mid_checkpoint_tensions(
        emotion_curve=data["emotion_curve_normalized"],
        chapter_words=load_chapter_wordcounts_for_range(project, arc_start, arc_end),
        cluster_total_words=target["estimated_words"],
        checkpoint_interval=3000,
    )
    return data


def load_chapter_wordcounts_for_range(project: Path, arc_start: int, arc_end: int) -> list[int]:
    """快速从 蒸馏进度 单章 JSON 读字数（按 ch 顺序）。"""
    out = []
    chapter_dir = project / "蒸馏进度"
    for ch in range(arc_start, arc_end + 1):
        wc = 3000  # 默认兜底
        for pat in (f"第{ch}章.json", f"ch{ch}.json"):
            f = chapter_dir / pat
            if not f.exists():
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                cand = d.get("word_count") or _walk_nested(d, "total_chars") or _walk_nested(d, "cjk_chars")
                if isinstance(cand, (int, float)) and cand > 0:
                    wc = int(cand)
                    break
            except (json.JSONDecodeError, OSError):
                continue
        out.append(wc)
    return out


def compute_mid_checkpoint_tensions(
    emotion_curve: list[float],
    chapter_words: list[int],
    cluster_total_words: int,
    checkpoint_interval: int = 3000,
) -> list[dict]:
    """v22.4dim N3：cluster 字数轴上每 checkpoint_interval 字一个张力期望点。

    使用：writer 在每个 mid_checkpoint 处 self-audit 时对照本字数点的期望张力。
    业界依据：ECAS schema 已含 mid_checkpoints 字段（默认每 3000 字一个），本字段
    给每个 checkpoint 配上期望张力，让 writer 知道"写到 6000 字时情绪应该有多激烈"。
    """
    if not emotion_curve or not chapter_words or cluster_total_words <= 0:
        return []

    # 累积字数：cumulative[i] = 前 i+1 章总字数
    cumulative = []
    s = 0
    for w in chapter_words:
        s += w
        cumulative.append(s)

    actual_total = cumulative[-1] if cumulative else cluster_total_words
    checkpoints = []
    pos = checkpoint_interval
    while pos < actual_total:
        # 找 pos 所在章
        ch_idx = next((i for i, c in enumerate(cumulative) if c >= pos), len(cumulative) - 1)
        # 在该章内的位置百分比
        ch_start_words = cumulative[ch_idx - 1] if ch_idx > 0 else 0
        ch_len = chapter_words[ch_idx] if ch_idx < len(chapter_words) else 1
        in_chapter_pct = (pos - ch_start_words) / max(ch_len, 1)
        # 取当前章张力（如果不是第一章，与上一章插值过渡）
        cur_tension = emotion_curve[ch_idx] if ch_idx < len(emotion_curve) else emotion_curve[-1]
        if ch_idx > 0 and in_chapter_pct < 0.3:
            # 接近章首：用上一章末与本章首插值（平滑过渡）
            prev_tension = emotion_curve[ch_idx - 1]
            t = prev_tension * (0.3 - in_chapter_pct) / 0.3 + cur_tension * in_chapter_pct / 0.3
            cur_tension = round(t, 3)
        checkpoints.append({
            "at_word": pos,
            "in_chapter_relative": ch_idx + 1,    # 本 cluster 第几章（1-based）
            "in_chapter_pct": round(in_chapter_pct, 2),
            "expected_tension": round(cur_tension, 3),
        })
        pos += checkpoint_interval

    return checkpoints


def aggregate_arc(project: Path, arc_end: int, arc_size: int = 10) -> dict:
    """副轨（固定 10 章）模式 · 保留向后兼容。"""
    arc_start = arc_end - arc_size + 1
    data = _aggregate_chapter_range(project, arc_start, arc_end, arc_size)
    data["mode"] = "fixed10"
    return data


def _aggregate_chapter_range(project: Path, arc_start: int, arc_end: int, arc_size: int) -> dict:
    """核心聚合逻辑（cluster / fixed10 共用）。"""
    arc_id_num = f"{arc_end:03d}"

    # 收集 chapter JSON
    chapter_dir = project / "蒸馏进度"
    continuity_dir = project / "衔接分析"

    chapter_jsons: list[dict] = []
    for ch in range(arc_start, arc_end + 1):
        # 兼容两种命名：第N章.json / chN.json
        for pattern in (f"第{ch}章.json", f"ch{ch}.json"):
            f = chapter_dir / pattern
            if f.exists():
                data = load_json(f, {})
                if data:
                    chapter_jsons.append(data)
                break

    # 收集 continuity JSON（覆盖 arc 范围）
    continuity_files: list[Path] = []
    continuity_jsons: list[dict] = []
    for f in continuity_dir.glob("*continuity.json") if continuity_dir.exists() else []:
        m = re.match(r"ch(\d+)_(\d+)_continuity\.json", f.name)
        if not m:
            continue
        from_ch = int(m.group(1))
        to_ch = int(m.group(2))
        if from_ch >= arc_start and to_ch <= arc_end:
            continuity_files.append(f)
            data = load_json(f, {})
            if data:
                continuity_jsons.append(data)

    # 拼接 pacing_curve（兼容多 schema：旧 v17 用 pacing_curve / 新版用 window_arc）
    def _safe_pacing_str(c):
        # 优先 pacing_curve；兼容 window_arc / voice_arc 字段
        for key in ("pacing_curve", "window_arc", "voice_arc"):
            v = c.get(key)
            if isinstance(v, str):
                return v
            if isinstance(v, dict):
                # 把 dict 转成 str（便于关键词扫描）
                return json.dumps(v, ensure_ascii=False)
            if isinstance(v, list):
                return " ".join(str(x) for x in v)
        return ""
    pacing_combined = " ".join(_safe_pacing_str(c) for c in continuity_jsons)
    pacing_values = parse_pacing_curve_to_values(pacing_combined, arc_size)
    pacing_labels = parse_pacing_curve_to_labels(pacing_combined, arc_size)

    # 单章情绪强度叠加
    emotion_curve = []
    for i, ch_j in enumerate(chapter_jsons):
        base = pacing_values[i] if i < len(pacing_values) else 0.5
        intensity = extract_chapter_emotion_intensity(ch_j)
        emotion_curve.append(round(min(1.0, base * 0.5 + intensity * 0.5), 3))

    while len(emotion_curve) < arc_size:
        emotion_curve.append(0.5)

    # 单章 dim 抽取
    scene_summary_ratio = [
        round(extract_dim_value(ch_j, ["scene_pct", "scene_ratio", "场景占比", "dim28"], 0.7), 3)
        for ch_j in chapter_jsons
    ]
    while len(scene_summary_ratio) < arc_size:
        scene_summary_ratio.append(0.7)

    event_density = [
        int(extract_dim_value(ch_j, ["conflict_density", "冲突密度", "dim37"], 1))
        + int(extract_dim_value(ch_j, ["hooks_count", "钩子总数", "dim29"], 2)) // 2
        for ch_j in chapter_jsons
    ]
    while len(event_density) < arc_size:
        event_density.append(2)

    kicker_count = [
        int(extract_dim_value(ch_j, ["hooks_count", "钩子总数", "dim29"], 3))
        for ch_j in chapter_jsons
    ]
    while len(kicker_count) < arc_size:
        kicker_count.append(3)

    # 高潮位置
    climax_idx = emotion_curve.index(max(emotion_curve)) if emotion_curve else arc_size // 2

    # 6 形状拟合
    shape_name, shape_conf = match_reagan_shape(emotion_curve)

    # 角色弧聚合（从 continuity.character_continuity，兼容 dict / str）
    character_arc = []
    char_seen: dict[str, dict] = {}
    for c in continuity_jsons:
        for cc in c.get("character_continuity", []) or []:
            if isinstance(cc, dict):
                name = cc.get("character", "")
                arc_progress = cc.get("arc_progress", "")
            elif isinstance(cc, str):
                name = cc
                arc_progress = ""
            else:
                continue
            if not name:
                continue
            if name not in char_seen:
                char_seen[name] = {
                    "character": name,
                    "stage_from": arc_progress[:30],
                    "stage_to": arc_progress[:30],
                    "key_turning_chapter": arc_start,
                }
            char_seen[name]["stage_to"] = arc_progress[:30]
    character_arc = list(char_seen.values())[:5]

    # 伏笔聚合
    # consumer tolerant：foreshadowing 可能被抗截断骨架/弱模型写成 str("TODO") → 非 dict 当空（不崩聚合）
    def _fore_len(c, key):
        fo = c.get("foreshadowing")
        return len((fo.get(key) or [])) if isinstance(fo, dict) else 0
    fs_planted = sum(_fore_len(c, "planted") for c in continuity_jsons)
    fs_resolved = sum(_fore_len(c, "resolved") for c in continuity_jsons)

    # v22.4dim Round 2 应用：Sudowrite tension dial 1-11
    sudowrite_dial = [round(1 + 10 * v) for v in emotion_curve[:arc_size]]

    return {
        "arc_id": f"arc_{arc_id_num}",
        "chapter_range": f"ch{arc_start}-{arc_end}",
        "emotion_curve_normalized": emotion_curve[:arc_size],
        "sudowrite_tension_dial_1_11": sudowrite_dial,    # v22.4dim Round 2: 直观档位（business standard）
        "pacing_labels": pacing_labels[:arc_size],
        "scene_summary_ratio_per_chapter": scene_summary_ratio[:arc_size],
        "event_density_per_chapter": event_density[:arc_size],
        "kicker_count_per_chapter": kicker_count[:arc_size],
        "climax_chapter_index": climax_idx,
        "climax_chapter_number": arc_start + climax_idx,
        "arc_structure_label": describe_arc_structure(emotion_curve),
        "matched_reagan_shape": shape_name,
        "matched_reagan_shape_confidence": round(shape_conf, 3),
        "foreshadowing_planted_in_arc": fs_planted,
        "foreshadowing_resolved_in_arc": fs_resolved,
        "character_arc_summary_in_arc": character_arc,
        "_metadata": {
            "distill_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "source_continuity_files": [f.name for f in continuity_files],
            "source_chapter_jsons_count": len(chapter_jsons),
            "aggregator_version": "v22.4dim.1",
        },
    }


def aggregate_summary(project: Path) -> dict:
    """聚合该书所有 arc（cluster + fixed10 两轨）→ arc_summary.json。"""
    arc_dir = project / "arc_templates"
    if not arc_dir.exists():
        return {"error": "no arc_templates dir"}

    cluster_arcs = []
    fixed_arcs = []
    for f in sorted(arc_dir.glob("*.json")):
        if f.name == "arc_summary.json":
            continue
        data = load_json(f, {})
        if not data:
            continue
        if f.name.startswith("cluster_arc_"):
            cluster_arcs.append(data)
        elif f.name.startswith("arc_"):
            fixed_arcs.append(data)

    arcs = cluster_arcs or fixed_arcs   # 优先用 cluster 主轨
    if not arcs:
        return {"error": "no arc files found"}

    shape_counts: dict[str, int] = {}
    for a in arcs:
        s = a.get("matched_reagan_shape", "Unknown")
        shape_counts[s] = shape_counts.get(s, 0) + 1

    def _arc_len(a):
        # arc 实际章数(climax_chapter_index 是对此长度 emotion_curve 的下标)：cluster arc 变长
        # (实测 2/3/4 章·非固定 10)·原硬编码 /10 使高潮位置百分比失真甚至 >100%(2026-06-15 审计修)。
        # 优先 chapters_count·次 chapter_range('chX-chY')解析·都无 fallback 10。
        nc = a.get("chapters_count")
        if isinstance(nc, int) and nc > 0:
            return nc
        cr = a.get("chapter_range")
        if isinstance(cr, str):
            nums = re.findall(r"\d+", cr)
            if len(nums) == 2:
                return max(int(nums[1]) - int(nums[0]) + 1, 1)
        return 10
    avg_climax_pct = sum(
        a.get("climax_chapter_index", 5) / max(_arc_len(a) - 1, 1) for a in arcs
    ) / len(arcs)

    return {
        "primary_track": "cluster" if cluster_arcs else "fixed10",
        "total_arcs": len(arcs),
        "cluster_track_arcs": len(cluster_arcs),
        "fixed10_track_arcs": len(fixed_arcs),
        "chapter_coverage": f"ch1-{arcs[-1].get('chapter_range', '').split('-')[-1]}",
        "reagan_shape_distribution": shape_counts,
        "most_common_shape": max(shape_counts, key=shape_counts.get),
        "average_climax_position_pct": round(avg_climax_pct, 3),
        "average_foreshadowing_planted_per_arc": round(
            sum(a.get("foreshadowing_planted_in_arc", 0) for a in arcs) / len(arcs), 2
        ),
        "average_foreshadowing_resolved_per_arc": round(
            sum(a.get("foreshadowing_resolved_in_arc", 0) for a in arcs) / len(arcs), 2
        ),
        "_metadata": {
            "summary_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "aggregator_version": "v22.cluster.1",
            "_doc": "primary_track=cluster 表示已用 cluster_segmenter 切分。两轨并存时优先 cluster。",
        },
    }


def main():
    parser = argparse.ArgumentParser(description="arc_aggregator v22.cluster · 双轨 arc 聚合（cluster 主 + fixed10 副）")
    parser.add_argument("--project", required=True, help="风格库项目路径，如 workspace/styles/BookC")
    parser.add_argument("--cluster", help="cluster_id（如 auto_002），cluster 主轨模式")
    parser.add_argument("--all-clusters", action="store_true", help="聚合 cluster_index.json 中的全部 cluster")
    parser.add_argument("--arc-end-chapter", type=int, help="fixed10 副轨：arc 末章号（如 10）")
    parser.add_argument("--arc-size", type=int, default=10, help="fixed10 副轨 arc 大小，默认 10 章")
    parser.add_argument("--mode", choices=["single", "summary"], default="single")
    args = parser.parse_args()

    project = Path(args.project)
    if not project.exists():
        print(f"[error] project not found: {project}", file=sys.stderr)
        sys.exit(2)

    arc_dir = project / "arc_templates"
    arc_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "summary":
        result = aggregate_summary(project)
        out = arc_dir / "arc_summary.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] arc_summary: {out} | primary_track={result.get('primary_track')} | total={result.get('total_arcs')}")
        return

    # cluster 模式（推荐主轨）
    if args.all_clusters:
        ci = load_cluster_index(project)
        if not ci:
            print(f"[error] cluster_index.json not found · 请先跑 cluster_segmenter.py", file=sys.stderr)
            sys.exit(2)
        written = 0
        for c in ci.get("clusters", []):
            cid = c["cluster_id"]
            result = aggregate_cluster(project, cid)
            if "error" in result:
                print(f"[skip] {cid}: {result['error']}", file=sys.stderr)
                continue
            out = arc_dir / f"cluster_arc_{cid}.json"
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            written += 1
        print(f"[OK] {written} cluster arcs written to {arc_dir}")
        return

    if args.cluster:
        result = aggregate_cluster(project, args.cluster)
        if "error" in result:
            print(f"[error] {result['error']}", file=sys.stderr)
            sys.exit(2)
        out = arc_dir / f"cluster_arc_{args.cluster}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] cluster_arc: {out}")
        print(f"      chapter_range: {result['chapter_range']} ({result['chapters_count']}章)")
        print(f"      emotion_curve: {result['emotion_curve_normalized']}")
        print(f"      shape: {result['matched_reagan_shape']} (conf={result['matched_reagan_shape_confidence']})")
        return

    # fixed10 副轨（保留向后兼容）
    if args.arc_end_chapter is None:
        print("[error] 必须指定 --cluster / --all-clusters / --arc-end-chapter / --mode summary 之一", file=sys.stderr)
        sys.exit(2)

    result = aggregate_arc(project, args.arc_end_chapter, args.arc_size)
    out = arc_dir / f"arc_{args.arc_end_chapter:03d}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] arc written: {out}")
    print(f"      emotion_curve: {result['emotion_curve_normalized']}")
    print(f"      shape: {result['matched_reagan_shape']} (conf={result['matched_reagan_shape_confidence']})")
    print(f"      climax: ch{result['climax_chapter_number']}")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
