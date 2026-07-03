"""cross_cluster_continuity_aggregate.py — 跨章衔接扫描（v19.1 新增 · 2026-07-01 NN 连贯性模型接入）

补 cross_cluster_pattern_aggregate 的盲区：**章节衔接质量**。
分布均衡 scan_pattern 看节奏，衔接 scan_continuity 看连贯。

扫 4 维度：
1. cliffhanger 回应度    — 前章 ending 是否在后章首段被回应（悬念断章例外）
2. 时间跳跃未交代       — 章间时间跳跃 ≥8h 必须有过渡说明
3. 物件持续性断层       — 关键物件（主角获得的 chekhov_gun）连续 ≥2 章未提及
4. 情绪/认知断层        — 前后章 summary.emotion 差 ≥4 且开篇无桥接

【2026-07-01 NN 连贯性模型接入（scanner-NN 升级批）】
维度 1/2 本质是「文本对是否自然衔接」判断，原先只能靠关键词/n-gram 字面重叠做代理指标，
对同义改写零容错。现优先调用已训练部署的 coherence_binary 模型（经 nn_coherence_bridge.
predict_pairs · 文本对衔接连贯性）：
  · 维度 1 cliffhanger：前章 ending_line vs 后章首段 → 模型衔接连贯性分替代关键词重叠比例
  · 维度 2 时间跳跃：前章尾段 vs 后章首段 → 模型 is_coherent 替代 plot_nodes 关键词命中检测
  · 维度 3 物件持续性：本质是「实体是否被提及」的词面存在性核对，不是「文本衔接自然度」
    问题——coherence 模型答不出"这段文本有没有提到某个具体物件"，硬套只会引入噪声，故
    不模型化，保留确定性别名匹配（北极星⑤·不强行给不适配的子任务套模型）
模型不可用（RUOYU_NN_COHERENCE 未开 / venv 或 checkpoint 缺失 / subprocess 失败）时维度 1/2
逐字节回退原确定性逻辑——若渝必须「无 NN 也能跑」，模型路径是"加"上去的不是"换"掉的
（零回归）。所有维度输出统一加 source 字段（"model"|"heuristic"）标注证据来源。

输出：
- 报告 JSON 写到 _数据库/.cross_chapter_scan/continuity_<timestamp>.json
- 终端打印每对相邻章衔接质量
- 触发告警时给具体建议

用法：
    python cross_cluster_continuity_aggregate.py <项目路径> [--last-n 10]

退出码: 0 健康 / 1 advisory / 2 严重断层
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path



# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动
# 🔴 2026-06-27 SYS-5 ①：extract_keywords 上移 continuity_keywords（单一来源），
# builder 预算 cliffhanger_resonance_next 与本 scanner 回退重算须用同一套关键词逻辑才可比。
from continuity_keywords import extract_keywords  # noqa: E402 · re-export 保持 cc.extract_keywords 可用

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_protagonist(project_root: Path) -> str | None:
    """从 人物卡.json 读 role==主角/protagonist 的角色名（照 relationship_evaluator
    范式·取代旧硬编码 "陆衍"）。兼容 {"characters":[...]} 与 {name:{...}} 两形态·
    读不到 fallback 第一个角色。"""
    cards = load_json(project_root / "_数据库" / "人物卡.json", None)
    if not isinstance(cards, dict):
        return None
    chars = cards.get("characters")
    if isinstance(chars, list):
        for c in chars:
            if isinstance(c, dict) and (c.get("role") in ("主角", "protagonist") or c.get("is_protagonist")):
                return c.get("name")
        for c in chars:
            if isinstance(c, dict) and c.get("name"):
                return c.get("name")
        return None
    for name, info in cards.items():
        if isinstance(info, dict) and (info.get("role") in ("主角", "protagonist") or info.get("is_protagonist")):
            return name
    return next(iter(cards.keys()), None)


def find_chapter_dirs(project_root: Path) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for d in project_root.glob("章节/第*章"):
        m = re.match(r"第(\d+)章", d.name)
        if m:
            out.append((int(m.group(1)), d))
    out.sort(key=lambda x: x[0])
    return out


def read_chapter_text(ch_dir: Path, ch: int) -> str | None:
    for f in ch_dir.glob(f"第{ch:03d}章.txt"):
        return f.read_text(encoding="utf-8")
    for f in ch_dir.glob(f"第{ch}章*.txt"):
        return f.read_text(encoding="utf-8")
    return None


def read_changes(ch_dir: Path, ch: int) -> dict | None:
    for f in ch_dir.glob(f"第{ch:03d}章_changes.json"):
        return load_json(f, None)
    return None


# ============================================================
# NN 连贯性模型接入（2026-07-01 scanner-NN 升级批 · 与 coherence_scanner.py 同款接入范式）
# 维度 1（cliffhanger）+ 维度 2（时间跳跃过渡）本质都是「文本对是否自然衔接」判断，优先交给
# 已训练部署的 coherence_binary 模型；不可用/未启用 → 调用方回退各自原确定性逻辑，不崩主流水线。
# ============================================================

SCENE_WINDOW_CHARS = 600   # ending/前后场景窗口尺寸（cliffhanger head 与 time-gap 前后场景共用）


def _load_coherence_bridge():
    """延迟导入 nn_coherence_bridge。不可用（未安装/被禁）→ None（调用方回退确定性逻辑，不崩）。

    返回 (predict_pairs, enabled) 或 None。RUOYU_FEATURE_STORE=1 时 predict_pairs 优先走
    FeatureStore 缓存（复用 coherence_scanner 已建立的缓存 key），未开/失败则直连 bridge。
    """
    try:
        from nn_coherence_bridge import predict_pairs as _bridge_predict_pairs, enabled
    except ImportError:
        return None

    def predict_pairs(pairs):
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            if feature_store_enabled():
                return FeatureStore.get().compute_coherence_pairs(pairs)
        except Exception:  # noqa: BLE001 feature store 失败 → 退 bridge，绝不影响 scanner
            pass
        return _bridge_predict_pairs(pairs)

    return predict_pairs, enabled


def _predict_pairs_safe(bridge, pairs: list) -> list:
    """批量调用 predict_pairs；任何异常/输出条数失配 → 全 None（不崩主流水线）。"""
    if not pairs:
        return []
    predict_pairs, _enabled = bridge
    try:
        results = predict_pairs(pairs)
    except Exception as e:  # noqa: BLE001 bridge 任何意外 → 全降级
        print(f"[cross_cluster_continuity_aggregate] predict_pairs 异常·回退确定性逻辑："
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return [None] * len(pairs)
    if not isinstance(results, list) or len(results) != len(pairs):
        print("[cross_cluster_continuity_aggregate] predict_pairs 输出条数失配·回退确定性逻辑",
              file=sys.stderr)
        return [None] * len(pairs)
    return results


def _valid_pair_result(r) -> "dict | None":
    """校验单条 predict_pairs 结果形状（防缓存/上游产出畸形）；不合规 → None（回退确定性逻辑）。"""
    if not isinstance(r, dict):
        return None
    score = r.get("coherence_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    return r


def _batch_model_results(coherence_bridge, pair_slots: "list[tuple[str, str] | None]") -> "list[dict | None]":
    """给定「每个位置的候选文本对（None=本位置不需要模型）」，一次性批量调用 predict_pairs
    （只对非 None 位置发请求，摊薄 subprocess+模型加载开销），结果按原顺序映射回填。

    bridge 为 None 或没有任何候选 → 全 None，且**不发起任何调用**（零开销）。
    """
    out: "list[dict | None]" = [None] * len(pair_slots)
    if coherence_bridge is None:
        return out
    idxs = [j for j, p in enumerate(pair_slots) if p is not None]
    if not idxs:
        return out
    batch_results = _predict_pairs_safe(coherence_bridge, [pair_slots[j] for j in idxs])
    for j, res in zip(idxs, batch_results):
        out[j] = _valid_pair_result(res)
    return out


# ===== 维度 1: cliffhanger 回应度 =====
# extract_keywords 已上移 continuity_keywords（SYS-5 ①），见顶部 import（cc.extract_keywords 仍可用）。


def scan_cliffhanger_resonance(prev_changes: dict, next_text: str, next_ch_dir: Path,
                                protagonist: str | None = None,
                                model_result: "dict | None" = None) -> dict:
    """前章 ending_line + ending_type vs 后章首段：NN 连贯性模型优先，关键词重叠回退。

    model_result：调用方（main）批量算好的 predict_pairs 结果
    （{"coherence_score","is_coherent","source":"model"}）；None（模型未启用/不可用/本对不
    适用）→ 逐字节回退原关键词重叠逻辑——模型路径是"加"上去的，不是"换"掉的（零回归）。
    """
    if not prev_changes:
        return {"score": -1, "reason": "前章 changes 缺失，跳过", "source": "heuristic"}
    se = prev_changes.get("self_eval", {})
    applied = se.get("applied_style", {})
    ending_type = applied.get("ending_type", "")
    ending_line = applied.get("ending_line", "")

    if ending_type in ("悬念断章",):
        return {"score": 1.0, "reason": "悬念断章，物理承接 OK", "exempt": True, "source": "heuristic"}

    if not ending_line:
        return {"score": -1, "reason": "前章 ending_line 未声明", "source": "heuristic"}

    # 后章首 300 字
    head = next_text[:SCENE_WINDOW_CHARS]

    if model_result is not None:
        return {
            "score": round(float(model_result["coherence_score"]), 2),
            "ending_type": ending_type,
            "ending_line_preview": ending_line[:50],
            "overlap_keywords": [],
            "reason": "NN 连贯性模型判定前章 ending 与后章首段衔接连贯性",
            "source": "model",
        }

    ending_kw = extract_keywords(ending_line + " " + ending_type, protagonist=protagonist)
    head_kw = extract_keywords(head, protagonist=protagonist)
    if not ending_kw:
        return {"score": -1, "reason": "ending_line 关键词不足", "source": "heuristic"}

    overlap = ending_kw & head_kw
    score = len(overlap) / max(len(ending_kw), 1)
    return {
        "score": round(score, 2),
        "ending_type": ending_type,
        "ending_line_preview": ending_line[:50],
        "overlap_keywords": list(overlap),
        "reason": "前章 ending 关键词与后章首段重叠度",
        "source": "heuristic",
    }


def scan_cliffhanger_resonance_ledger(prev_rec: dict, next_ch_dir: Path,
                                       model_result: "dict | None" = None) -> dict:
    """2026-05-29 cluster 化：账本预算了前章 cliffhanger_resonance_next（与下一章 head
    的重叠分）时，直接取用，省去 ending_line 关键词重扫。

    model_result 命中（NN 连贯性模型）→ 优先替代账本预算分；None（模型未启用/不可用）→
    逐字节回退账本预算分（零回归）。
    """
    score = prev_rec.get("cliffhanger_resonance_next")
    ending_type = prev_rec.get("ending_type", "")
    ending_line = prev_rec.get("ending_line", "")
    if ending_type in ("悬念断章",):
        return {"score": 1.0, "reason": "悬念断章，物理承接 OK", "exempt": True, "source": "heuristic"}

    if model_result is not None:
        return {
            "score": round(float(model_result["coherence_score"]), 2),
            "ending_type": ending_type,
            "ending_line_preview": ending_line[:50],
            "overlap_keywords": [],
            "reason": "NN 连贯性模型判定前章 ending 与后章首段衔接连贯性（cluster 摘要驱动）",
            "source": "model",
        }

    if not isinstance(score, (int, float)):
        return {"score": -1, "reason": "账本无 cliffhanger_resonance_next", "source": "heuristic"}
    return {
        "score": round(float(score), 2),
        "ending_type": ending_type,
        "ending_line_preview": ending_line[:50],
        "overlap_keywords": [],
        "reason": "账本预算的前章 ending 与后章首段重叠度（cluster 摘要驱动）",
        "source": "heuristic",
    }


# ===== 维度 2: 时间跳跃 =====

TIME_KEYWORDS = {
    "凌晨": 2, "早上": 8, "上午": 10, "中午": 12, "下午": 15,
    "傍晚": 18, "晚上": 20, "晚间": 21, "夜里": 23,
    "周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7,
}


def scan_time_gap(prev_changes: dict, next_changes: dict) -> dict:
    """简易时间跳跃检测。"""
    if not prev_changes or not next_changes:
        return {"detected": False, "reason": "changes 缺失"}

    prev_time = prev_changes.get("factual", {}).get("time_advance", {})
    next_time = next_changes.get("factual", {}).get("time_advance", {})
    prev_events = prev_time.get("key_events", [])
    next_events = next_time.get("key_events", [])
    prev_end = prev_events[-1] if prev_events else prev_time.get("period", "")
    next_start = next_events[0] if next_events else next_time.get("period", "")

    # 提取周日期
    prev_day = next((d for k, d in TIME_KEYWORDS.items() if k.startswith("周") and k in str(prev_end)), None)
    next_day = next((d for k, d in TIME_KEYWORDS.items() if k.startswith("周") and k in str(next_start)), None)

    if prev_day and next_day:
        day_gap = (next_day - prev_day) % 7
        if day_gap >= 2:
            return {
                "detected": True,
                "gap_days": day_gap,
                "prev_time_end": str(prev_end)[:40],
                "next_time_start": str(next_start)[:40],
                "reason": f"章间跳跃 {day_gap} 天，需在后章开篇有过渡说明",
            }
    return {"detected": False, "prev_end": str(prev_end)[:40], "next_start": str(next_start)[:40]}


# 后章 plot_nodes 关键词命中即视为"已交代过渡"（NN 连贯性模型不可用时的回退逻辑）
TRANSITION_KEYWORDS = ("过渡", "周末", "回忆", "醒来", "睡了")


def check_time_transition(next_plots: list, model_result: "dict | None" = None) -> dict:
    """判断后章开篇是否已对检测到的时间跳跃给出过渡说明。

    model_result：main() 批量算好的 NN 连贯性结果（前章尾段 vs 后章首段衔接连贯性）；
    None（模型未启用/不可用）→ 逐字节回退 plot_nodes 关键词命中检测——与改前 main() 内联
    逻辑完全一致（零回归·模型路径是"加"上去的不是"换"掉的）。
    """
    if model_result is not None:
        is_coherent = model_result.get("is_coherent")
        if is_coherent is None:
            is_coherent = model_result.get("coherence_score", 0) >= 0.5
        return {"has_transition": bool(is_coherent), "source": "model"}
    has_transition = any(
        any(kw in str(p).lower() for kw in TRANSITION_KEYWORDS)
        for p in next_plots
    )
    return {"has_transition": has_transition, "source": "heuristic"}


# ===== 维度 3: 物件持续性 =====

def _build_aliases(name: str) -> list[str]:
    """从完整物件名提取核心别名（短名/括号内的标识/关键词）。"""
    aliases = [name]
    # 提取括号内的内容
    m = re.search(r"[（(]([^）)]+)[）)]", name)
    if m:
        aliases.append(m.group(1))
    # 去括号的核心名
    core = re.sub(r"[（(].+?[）)]", "", name).strip()
    if core and core != name:
        aliases.append(core)
    # 关键短语（首/末 2-3 字）
    if len(name) >= 4:
        aliases.append(name[:3])
        aliases.append(name[-3:])
    # 2026-06-15 去硬编码：原此处硬编码特定旧书物件名"废票/灵格/铁皮盒/VIP/暗码表/笔记本/
    # 邮件/PDF/股票/持股/0.001"·只对那本书有效(北极星⑥清硬编码 + ①不绑特定书)。上方括号/
    # 去括号核心/首末 3 字通用提取对任意书物件名都工作(如"废票（彩票）"经去括号已得"废票")·
    # 删硬编码补丁不引入过宽匹配削弱检测。
    return list(set(aliases))


def scan_object_continuity(all_changes: dict[int, dict], all_texts: dict[int, str], current_ch: int) -> list[dict]:
    """关键物件（chekhovs_gun）连续 ≥2 章未提及。

    【2026-07-01 NN 接入评估结论：本维度不接 coherence 模型】本质是「某实体是否被提及」
    的词面存在性核对，不是「两段文本是否自然衔接」的问题——coherence_binary 模型衡量的是
    文本对的衔接连贯度，答不出"这段文本有没有提到某个具体物件"，硬套只会引入噪声（北极星
    ⑤·不强行给不适配的子任务套模型）。因此保留确定性别名匹配，只补 source 字段与维度 1/2
    输出对齐（本维度恒为 "heuristic"）。
    """
    findings = []
    if current_ch < 3:
        return findings

    # 收集 ch1~current 的所有关键物件转移给主角
    key_items: dict[str, dict] = {}
    for ch in range(1, current_ch + 1):
        ch_changes = all_changes.get(ch)
        if not ch_changes:
            continue
        for it in ch_changes.get("factual", {}).get("item_transfers", []):
            name = it.get("item", "")
            if not name:
                continue
            if name not in key_items:
                key_items[name] = {"first_ch": ch, "last_seen_ch": ch, "aliases": _build_aliases(name)}
            else:
                key_items[name]["last_seen_ch"] = ch

    # 对每章正文 + changes 内所有文本字段做提及检查
    for ch in range(1, current_ch + 1):
        ch_text = all_texts.get(ch, "")
        ch_changes_str = json.dumps(all_changes.get(ch, {}), ensure_ascii=False)
        combined = ch_text + "\n" + ch_changes_str
        for name, info in key_items.items():
            if info["last_seen_ch"] >= ch:
                continue
            # 用 aliases 任一命中即算提及
            for alias in info["aliases"]:
                if alias in combined:
                    info["last_seen_ch"] = ch
                    break

    # 检查每个 key_item 是否 ≥2 章未提及
    for name, info in key_items.items():
        gap = current_ch - info["last_seen_ch"]
        if gap >= 2 and info["first_ch"] <= current_ch - 2:
            findings.append({
                "item": name,
                "first_ch": info["first_ch"],
                "last_seen_ch": info["last_seen_ch"],
                "gap": gap,
                "current_ch": current_ch,
                "source": "heuristic",
            })
    return findings


# ===== 维度 4: 情绪断层 =====

def read_emotion(project_root: Path, ch: int) -> int | None:
    summary_path = project_root / "_数据库" / ".wal" / f"第{ch:03d}章_summary.json"
    if not summary_path.exists():
        return None
    data = load_json(summary_path, {})
    # 🔴 2026-06-17 守卫：emotion 可能是 dict{value,trend} 或裸标量(int/float)
    # （cluster_summary_builder 两种形态）→ 原 .get 链对标量崩 AttributeError。对齐 bug-hunt 批。
    _emo = data.get("emotion", {})
    if isinstance(_emo, dict):
        return _emo.get("value")
    return _emo if isinstance(_emo, (int, float)) and not isinstance(_emo, bool) else None


def scan_emotion_gap(project_root: Path, prev_ch: int, next_ch: int, ledger_by_ch: dict | None = None) -> dict:
    # 2026-05-29 cluster 化：账本有 emotion_value → 用账本；否则回退读 WAL summary。
    def _emo(ch):
        if ledger_by_ch is not None:
            rec = ledger_by_ch.get(ch)
            if rec is not None and isinstance(rec.get("emotion_value"), (int, float)):
                return rec["emotion_value"]
        return read_emotion(project_root, ch)
    e1 = _emo(prev_ch)
    e2 = _emo(next_ch)
    if e1 is None or e2 is None:
        return {"detected": False, "reason": "summary 缺失"}
    diff = abs(e1 - e2)
    return {
        "detected": diff >= 5,
        "prev_emotion": e1,
        "next_emotion": e2,
        "diff": diff,
    }


# ===== 主流程 =====

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    chapter_dirs = find_chapter_dirs(project_root)
    if len(chapter_dirs) < 2:
        print("[OK] 章节 <2，无衔接可扫")
        sys.exit(0)
    chapter_dirs = chapter_dirs[-args.last_n:]
    protagonist = get_protagonist(project_root)  # 动态主角名 → extract_keywords stop（取代硬编码陆衍·北极星⑥）

    all_changes: dict[int, dict] = {}
    all_texts: dict[int, str] = {}
    for ch, d in chapter_dirs:
        all_texts[ch] = read_chapter_text(d, ch) or ""
        all_changes[ch] = read_changes(d, ch) or {}

    # ===== 2026-05-29 cluster 化：账本有任一 continuity 字段 → 维度 1/2/4 摘要驱动 =====
    # cliffhanger 维度账本无 cliffhanger_resonance_next 时该维度单独回退正文逻辑；
    # 维度 3（物件持续性）依赖正文别名匹配，账本无对应文本字段 → 始终走磁盘。
    ledger_by_ch = None
    if csr.is_cluster_mode() and (
        csr.ledger_has_field(project_root, "time_advance")
        or csr.ledger_has_field(project_root, "ending_type")
        or csr.ledger_has_field(project_root, "emotion_value")
        or csr.ledger_has_field(project_root, "cliffhanger_resonance_next")
    ):
        ledger_by_ch = {ch: rec for ch, rec in csr.get_chapter_records(project_root)}

    def _changes_like(ch: int) -> dict:
        """把账本 ChapterRecord 包成 scan_time_gap 期望的 {factual:{time_advance,plot_nodes}} 形态。"""
        rec = (ledger_by_ch or {}).get(ch)
        if rec is None:
            return all_changes.get(ch, {})
        return {"factual": {
            "time_advance": rec.get("time_advance", {}) or {},
            "plot_nodes": rec.get("plot_nodes", []) or [],
        }}

    # ===== 2026-07-01 NN 连贯性模型批量预算（scanner-NN 升级批）=====
    # cliffhanger 回应度（维度 1）+ 时间跳跃过渡（维度 2）本质都是「文本对是否自然衔接」判断，
    # 模型可用时一次性批量算完（摊薄 subprocess+模型加载开销），逐对扫描时直接查表；模型未
    # 启用/不可用 → 两张表全 None，两维度分别回退各自原确定性逻辑，逐字节零回归。
    coherence_bridge = _load_coherence_bridge()
    coherence_on = False
    if coherence_bridge is not None:
        try:
            coherence_on = bool(coherence_bridge[1]())
        except Exception:
            coherence_on = False

    pair_count = len(chapter_dirs) - 1
    cliff_pair_texts: "list[tuple[str, str] | None]" = [None] * pair_count
    time_pair_texts: "list[tuple[str, str] | None]" = [None] * pair_count
    time_gap_cache: "list[dict | None]" = [None] * pair_count

    for i in range(pair_count):
        prev_ch, _prev_d = chapter_dirs[i]
        next_ch, _next_d = chapter_dirs[i + 1]

        # --- 维度 1 候选文本对：ending_line/ending_type 优先取账本（与下方实际扫描同源）---
        prev_ledger_rec = (ledger_by_ch or {}).get(prev_ch)
        if prev_ledger_rec is not None and isinstance(prev_ledger_rec.get("cliffhanger_resonance_next"), (int, float)):
            ending_type = prev_ledger_rec.get("ending_type", "")
            ending_line = prev_ledger_rec.get("ending_line", "")
        else:
            se = all_changes.get(prev_ch, {}).get("self_eval", {})
            applied = se.get("applied_style", {})
            ending_type = applied.get("ending_type", "")
            ending_line = applied.get("ending_line", "")
        next_head = all_texts.get(next_ch, "")[:SCENE_WINDOW_CHARS]
        if coherence_on and ending_type not in ("悬念断章",) and ending_line and next_head.strip():
            cliff_pair_texts[i] = (ending_line, next_head)

        # --- 维度 2 候选文本对：先探测时间跳跃（确定性·下方主循环复用同一份结果）---
        tg = scan_time_gap(_changes_like(prev_ch), _changes_like(next_ch))
        time_gap_cache[i] = tg
        if tg.get("detected") and coherence_on:
            prev_tail = all_texts.get(prev_ch, "")[-SCENE_WINDOW_CHARS:]
            next_head_full = all_texts.get(next_ch, "")[:SCENE_WINDOW_CHARS]
            if prev_tail.strip() and next_head_full.strip():
                time_pair_texts[i] = (prev_tail, next_head_full)

    cliff_model_results = _batch_model_results(coherence_bridge, cliff_pair_texts)
    time_model_results = _batch_model_results(coherence_bridge, time_pair_texts)

    findings = []
    pairwise = []

    # 逐对相邻章扫
    for i in range(pair_count):
        prev_ch, prev_d = chapter_dirs[i]
        next_ch, next_d = chapter_dirs[i + 1]

        # 维度 1: cliffhanger（model_result 命中 → 模型衔接连贯性分；None → 回退关键词重叠/账本预算分）
        prev_ledger_rec = (ledger_by_ch or {}).get(prev_ch)
        if prev_ledger_rec is not None and isinstance(prev_ledger_rec.get("cliffhanger_resonance_next"), (int, float)):
            cliff = scan_cliffhanger_resonance_ledger(prev_ledger_rec, next_d, model_result=cliff_model_results[i])
        else:
            cliff = scan_cliffhanger_resonance(all_changes.get(prev_ch, {}), all_texts.get(next_ch, ""), next_d,
                                                protagonist=protagonist, model_result=cliff_model_results[i])
        if not cliff.get("exempt") and cliff.get("score", -1) >= 0 and cliff["score"] < 0.2:
            findings.append({
                "dimension": "cliffhanger",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "CLIFFHANGER_NOT_RESONATED",
                "from_ch": prev_ch,
                "to_ch": next_ch,
                "metric": {"resonance_score": cliff["score"], "ending_type": cliff.get("ending_type"),
                           "ending_preview": cliff.get("ending_line_preview"), "source": cliff.get("source", "heuristic")},
                "message": f"ch{prev_ch}→ch{next_ch}: 前章 ending ({cliff.get('ending_type')}) 未在后章首段被回应（重叠度 {cliff['score']:.0%}）",
                "suggestion": f"后章首段 ≤300 字内必须回应前章 ending 关键词；当前 ending_line='{cliff.get('ending_line_preview', '')}'",
            })

        # 维度 2: 时间跳跃（has_transition 优先模型判断；model_result=None 回退 plot_nodes 关键词命中）
        time_gap = time_gap_cache[i]
        transition_source = None
        if time_gap.get("detected"):
            next_plots = _changes_like(next_ch).get("factual", {}).get("plot_nodes", [])
            transition = check_time_transition(next_plots, model_result=time_model_results[i])
            transition_source = transition["source"]
            if not transition["has_transition"]:
                findings.append({
                    "dimension": "time_gap",
                    "severity": "warning",
                    "gate_level": "advisory",
                    "code": "TIME_JUMP_UNEXPLAINED",
                    "from_ch": prev_ch,
                    "to_ch": next_ch,
                    "metric": {**time_gap, "source": transition_source},
                    "message": f"ch{prev_ch}→ch{next_ch}: 时间跳跃 {time_gap['gap_days']} 天，后章 plot_nodes 无过渡说明",
                    "suggestion": "后章开篇加 1-2 段过渡说明（周末做了什么/如何消化前章震撼）",
                })

        # 维度 4: 情绪断层（未变动·不在本次 NN 接入范围）
        emo_gap = scan_emotion_gap(project_root, prev_ch, next_ch, ledger_by_ch)
        if emo_gap.get("detected"):
            findings.append({
                "dimension": "emotion",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "EMOTION_DISCONTINUITY",
                "from_ch": prev_ch,
                "to_ch": next_ch,
                "metric": emo_gap,
                "message": f"ch{prev_ch}→ch{next_ch}: 情绪值跳跃 {emo_gap['diff']}（{emo_gap['prev_emotion']}→{emo_gap['next_emotion']}）",
                "suggestion": "断层 ≥5 时建议加 emotional bridge 段（回忆/独白/动作过渡）",
            })

        pairwise.append({
            "from_ch": prev_ch,
            "to_ch": next_ch,
            "cliffhanger_score": cliff.get("score", -1),
            "cliffhanger_exempt": cliff.get("exempt", False),
            "cliffhanger_source": cliff.get("source", "heuristic"),
            "time_gap_days": time_gap.get("gap_days", 0),
            "time_transition_source": transition_source,
            "emotion_diff": emo_gap.get("diff", 0),
        })

    # 维度 3: 物件持续性（针对最新章）
    last_ch = chapter_dirs[-1][0]
    obj_findings = scan_object_continuity(all_changes, all_texts, last_ch)
    for of in obj_findings:
        # gap 越大越严重(取代硬编码特定旧书物件名"废票/铁皮盒/VIP/暗码表/日记本"·北极星⑥清
        # 硬编码 + ①不绑特定书)：关键物件连续 ≥5 章未提及 = 被遗忘风险高 → warning，否则 advisory。
        sev = "warning" if of.get("gap", 0) >= 5 else "advisory"
        findings.append({
            "dimension": "object_continuity",
            "severity": sev,
            "gate_level": "advisory",
            "code": "OBJECT_CONTINUITY_BROKEN",
            "metric": of,
            "message": f"关键物件「{of['item']}」自 ch{of['first_ch']} 出现，ch{of['last_seen_ch']} 后连续 {of['gap']} 章未提及",
            "suggestion": "至少每 3 章提及一次，或在 _changes.json item_locations 显式标注当前位置",
        })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "continuity",
        "scan_ts": ts,
        "chapters_scanned": [ch for ch, _ in chapter_dirs],
        "pairwise": pairwise,
        "findings": findings,
        "summary": {
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "total": len(findings),
        },
    }
    out_path = out_dir / f"continuity_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 打印
    print(f"[cross_cluster_continuity_aggregate] 扫描章节={[ch for ch, _ in chapter_dirs]}")
    print()
    print("=== 相邻章衔接 pairwise ===")
    for p in pairwise:
        cliff = "exempt" if p["cliffhanger_exempt"] else (f"{p['cliffhanger_score']:.0%}" if p["cliffhanger_score"] >= 0 else "N/A")
        print(f"  ch{p['from_ch']}→ch{p['to_ch']}: cliffhanger={cliff} time_gap={p['time_gap_days']}天 emotion_diff={p['emotion_diff']}")
    print()
    print(f"=== 发现 {len(findings)} 项 (warning={report['summary']['warning']} / advisory={report['summary']['advisory']}) ===")
    for f in findings:
        loc = f.get('from_ch') and f"ch{f['from_ch']}→ch{f['to_ch']}" or "*"
        print(f"  [{f['severity'].upper()}] [{f['code']}] {loc} :: {f['message']}")
        print(f"     建议: {f['suggestion']}")
    print()
    print(f"报告: {out_path}")

    if any(f["severity"] == "warning" for f in findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
