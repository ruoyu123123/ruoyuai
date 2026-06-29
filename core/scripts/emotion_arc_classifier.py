# 🔴 2026-06-29 NN主题漂移/情感弧线集成
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emotion_arc_classifier.py — 情感弧线分类（Reagan 六弧型 · VAD + 词典兜底 · 2026-06-29）

复用 nn_vad_bridge.predict_batch() 获取每段 valence → 时序分析 → 分类为 Reagan 六弧型之一。
VAD 桥不可用 → 简单正负面词典兜底。附带 advisory scanner 函数 scan_emotion_arc()。

六种弧型（Reagan et al. 2016, EPJ Data Science · "The emotional arcs of stories"）:
  1. rags_to_riches   — 上升弧（valence 从低到高）
  2. riches_to_rags   — 下降弧（valence 从高到低）
  3. man_in_a_hole    — 下降后回升（V 形）
  4. icarus           — 上升后下降（倒 V 形）
  5. cinderella       — 上升-下降-上升（渐进上行 W 形）
  6. oedipus          — 下降-上升-下降（渐进下行 M 形）

【分类方法】3 段均值 Pearson 相关匹配。将 valence 序列切成 3 等份，每份算均值，
与 6 个理想化模板曲线算 Pearson 相关系数，最高者为分类结果。
简单稳健·无训练·零外部依赖。

【北极星⑤】所有 issue 永远 advisory，绝不进 HARD_GATE_CODES。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ── 简单正负面词典（VAD 桥不可用时的兜底）─────────────────────────────────────

POSITIVE_WORDS: frozenset[str] = frozenset(
    "笑 喜 乐 幸福 开心 高兴 希望 爱 快乐 欣慰 甜 美好 温暖 兴奋 "
    "满足 感激 惊喜 激动 自豪 安心 轻松 畅快 振奋 欢 悦 庆 赞 好 "
    "光明 胜利 成功 甜蜜 拥抱 微笑 大笑 阳光 春天".split()
)

NEGATIVE_WORDS: frozenset[str] = frozenset(
    "哭 伤 悲 痛 恨 怒 怕 惊 恐 死 血 冷 寒 黑暗 绝望 孤独 "
    "愤怒 悲伤 害怕 难过 焦虑 恐惧 痛苦 失望 沮丧 崩溃 压抑 凄凉 "
    "阴 暗 泪 呜 惨 毁 丧 亡 灾 祸 危 险 凶".split()
)


# ── Reagan 六弧型模板（3 段均值 pattern）──────────────────────────────────────
# 每个模板是 [segment1, segment2, segment3] 的理想化 valence 曲线

ARC_TEMPLATES: dict[str, list[float]] = {
    "rags_to_riches":  [0.2, 0.7, 0.9],   # 低→高→高
    "riches_to_rags":  [0.9, 0.3, 0.1],   # 高→低→低
    "man_in_a_hole":   [0.7, 0.1, 0.7],   # 高→低→高（V 形）
    "icarus":          [0.3, 0.9, 0.3],   # 低→高→低（倒 V）
    "cinderella":      [0.2, 0.5, 0.9],   # 低→中→高（渐进上行）
    "oedipus":         [0.9, 0.5, 0.1],   # 高→中→低（渐进下行）
}


# ── 段落切分 ─────────────────────────────────────────────────────────────────

def _split_paragraphs(text: str, min_len: int = 3) -> list[str]:
    """按换行切段，过滤太短的行。"""
    paras: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) >= min_len:
            paras.append(line)
    return paras


# ── 词典兜底 valence ─────────────────────────────────────────────────────────

def _lexicon_valence(text: str) -> float:
    """简单正负面词计数 → 归一化 valence 属于 [0, 1]。0=全负 1=全正 0.5=中性/无匹配。"""
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    total = pos + neg
    if total == 0:
        return 0.5
    return pos / total


# ── 获取 valence 序列 ────────────────────────────────────────────────────────

def _get_valence_sequence(paragraphs: list[str],
                          project_dir: str = None) -> tuple[list[float], str]:
    """获取每段 valence。优先用 VAD 模型，不可用 → 词典兜底。返回 (valences, source)。"""
    try:
        import nn_vad_bridge
        results = nn_vad_bridge.predict_batch(paragraphs)
        # 至少 50% 段有模型结果才视为可用
        model_count = sum(1 for r in results if r is not None)
        if model_count >= len(paragraphs) * 0.5:
            valences: list[float] = []
            for i, r in enumerate(results):
                if r is not None and r.get("valence") is not None:
                    valences.append(float(r["valence"]))
                else:
                    valences.append(_lexicon_valence(paragraphs[i]))
            return valences, "vad_model"
    except Exception:  # noqa: BLE001 默认安全·桥不可用/任何异常 → 词典兜底·绝不崩
        pass
    # 全部走词典
    return [_lexicon_valence(p) for p in paragraphs], "lexicon_fallback"


# ── 统计工具 ─────────────────────────────────────────────────────────────────

def _pearson_correlation(x: list[float], y: list[float]) -> float:
    """Pearson 相关系数。用于模板匹配。"""
    n = len(x)
    if n < 2 or len(y) != n:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / n)
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / n)
    if sx < 1e-9 or sy < 1e-9:
        return 0.0
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
    return cov / (sx * sy)


def _classify_from_segments(segment_means: list[float]) -> tuple[str, float]:
    """用 Pearson 相关系数匹配 Reagan 六弧型模板。返回 (arc_type, confidence)。"""
    best_arc = "rags_to_riches"
    best_corr = -2.0
    for arc_name, template in ARC_TEMPLATES.items():
        corr = _pearson_correlation(segment_means, template)
        if corr > best_corr:
            best_corr = corr
            best_arc = arc_name
    # confidence = (corr + 1) / 2 映射到 [0, 1]
    confidence = max(0.0, min(1.0, (best_corr + 1.0) / 2.0))
    return best_arc, round(confidence, 3)


# ── 弧线分类 ─────────────────────────────────────────────────────────────────

def classify_emotion_arc(draft_text: str,
                         project_dir: str = None) -> dict:
    """情感弧线分类。返回弧型 + 置信度 + valence 曲线 + 分段信息。

    VAD 桥不可用 → 词典兜底（source="lexicon_fallback"）。
    段落太少（< 6）→ 返回 arc_type="insufficient_data"。
    """
    if not draft_text:
        return {"arc_type": "insufficient_data", "confidence": 0.0,
                "valence_curve": [], "segments": [], "segment_means": [],
                "source": "none", "n_paragraphs": 0,
                "reason": "空文本"}

    paras = _split_paragraphs(draft_text)
    if len(paras) < 6:
        return {"arc_type": "insufficient_data", "confidence": 0.0,
                "valence_curve": [], "segments": [], "segment_means": [],
                "source": "none", "n_paragraphs": len(paras),
                "reason": f"段落太少（{len(paras)} < 6）"}

    valences, source = _get_valence_sequence(paras, project_dir)

    # ── 切 3 等份，算均值 ──
    n = len(valences)
    seg_size = n // 3
    remainder = n % 3
    boundaries: list[tuple[int, int]] = []
    start = 0
    for i in range(3):
        end = start + seg_size + (1 if i < remainder else 0)
        boundaries.append((start, end))
        start = end

    segments: list[dict] = []
    segment_means: list[float] = []
    for seg_start, seg_end in boundaries:
        seg_vals = valences[seg_start:seg_end]
        avg = sum(seg_vals) / len(seg_vals) if seg_vals else 0.5
        segments.append({
            "start": seg_start,
            "end": seg_end - 1,
            "avg_valence": round(avg, 4),
            "n_paragraphs": len(seg_vals),
        })
        segment_means.append(avg)

    arc_type, confidence = _classify_from_segments(segment_means)

    return {
        "arc_type": arc_type,
        "confidence": confidence,
        "valence_curve": [round(v, 4) for v in valences],
        "segments": segments,
        "segment_means": [round(m, 4) for m in segment_means],
        "source": source,
        "n_paragraphs": n,
    }


# ── advisory scanner ─────────────────────────────────────────────────────────

def scan_emotion_arc(draft_text: str,
                     project_dir: str = None) -> list[dict]:
    """情感弧线 advisory scanner。返回 issue 列表。

    检测规则（全部 advisory）:
      · EMOTION_ARC_FLAT     — valence 方差极低（< 0.005）→ 情感无波动
      · EMOTION_ARC_MISMATCH — 置信度低（< 0.55）→ 弧型不明确
    """
    result = classify_emotion_arc(draft_text, project_dir)
    if result["arc_type"] == "insufficient_data":
        return []

    issues: list[dict] = []
    valences = result["valence_curve"]

    # ── 规则 1: EMOTION_ARC_FLAT ──────────────────────────────────────────
    if len(valences) >= 6:
        mean_v = sum(valences) / len(valences)
        variance = sum((v - mean_v) ** 2 for v in valences) / len(valences)
        if variance < 0.005:
            issues.append({
                "code": "EMOTION_ARC_FLAT",
                "gate_level": "advisory",
                "severity": "minor",
                "variance": round(variance, 6),
                "arc_type": result["arc_type"],
                "confidence": result["confidence"],
                "source": result["source"],
                "message": (f"情感弧线平坦（valence 方差 {variance:.4f} < 0.005）→ "
                            f"情感无波动·缺乏起伏"),
            })

    # ── 规则 2: EMOTION_ARC_MISMATCH ──────────────────────────────────────
    if result["confidence"] < 0.55:
        issues.append({
            "code": "EMOTION_ARC_MISMATCH",
            "gate_level": "advisory",
            "severity": "minor",
            "confidence": result["confidence"],
            "arc_type": result["arc_type"],
            "source": result["source"],
            "message": (f"情感弧型不明确（{result['arc_type']}·"
                        f"置信度 {result['confidence']:.2f} < 0.55）→ "
                        f"弧线混乱·读者难以感受到情感轨迹"),
        })

    return issues


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台强制 UTF-8（与 nn_vad_bridge 一致）
    ap = argparse.ArgumentParser(
        description="情感弧线分类（Reagan 六弧型 · VAD + 词典兜底）")
    ap.add_argument("draft_path", help="草稿文件路径")
    ap.add_argument("--project", default=None, help="项目根目录")
    ap.add_argument("--scan", action="store_true",
                    help="scanner 模式（输出 issue 列表）")
    args = ap.parse_args()

    text = Path(args.draft_path).read_text(encoding="utf-8")
    if args.scan:
        issues = scan_emotion_arc(text, args.project)
        print(json.dumps(issues, ensure_ascii=False, indent=2))
        sys.exit(1 if issues else 0)
    else:
        result = classify_emotion_arc(text, args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)


if __name__ == "__main__":
    main()
