"""cross_cluster_emotion_pattern_aggregate.py — 角色情绪长期画像扫描（v21 R3.3 新增）

借鉴 Character.AI Chat Memories：跨章扫描每个核心角色的情绪占比 + 模式（如「90% 章节谨慎，每 20 章爆发一次」）。

输出报告含：
- 每角色情绪类型分布（按关键词扫文本）
- 情绪连续单一化告警（如连续 8 章同一情绪 = 角色僵化）
- 情绪爆发周期识别（如每 N 章触发一次反差情绪）
- writer reflector 据此发现「角色行为模式正在固化」

退出码: 0 健康 / 1 advisory / 2 致命

【🔴 2026-07-01 emotion_vad 模型集成】detect_emotions_for_char 的 ±150 字窗口判断优先用
emotion_vad 模型(RoBERTa 微调·held-out meanCCC 0.80)的 valence 连续值最近邻分类到 7 类之一
(锚点复用 emotion_curve_rescan_scanner.EMOTION_VALENCE 同源数值)，替换关键词命中判断；
模型不可用/该窗未命中 → 该窗回退关键词扫描(与旧逻辑逐字节一致·零回归)。分布/单一化/连续段
判定数学部分不变。env RUOYU_NN_VAD 门控(默认 off)；main() 报告加 vad_source 字段
(model_vad/lexicon_fallback/mixed/not_applicable_ledger_mode)供训练数据归因。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import sys


import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动

# 6 大情绪类型 + 关键词
EMOTION_KEYWORDS = {
    "calm": ["平静", "镇定", "冷静", "想清楚", "权衡", "压抑", "克制"],
    "anxious": ["紧张", "担心", "忐忑", "心跳", "屏住呼吸", "汗"],
    "angry": ["愤怒", "怒", "咬牙", "拳头", "怒吼", "暴怒"],
    "sad": ["悲伤", "难过", "心痛", "酸", "泪", "黯然", "哽咽"],
    "joyful": ["高兴", "笑", "兴奋", "欣喜", "畅快"],
    "curious": ["好奇", "疑惑", "怎么", "为什么", "纳闷", "想知道"],
    "fearful": ["恐惧", "害怕", "战栗", "毛骨悚然", "不寒而栗", "颤抖"],
}

# 🔴 2026-07-01 emotion_vad 模型集成：valence 锚点(0=最负·1=最正·0.5=中性)，供模型 valence
# 最近邻分类到上面 7 类之一。数值与 emotion_curve_rescan_scanner.EMOTION_VALENCE 同源
# (该文件反向 import 本文件的 EMOTION_KEYWORDS，为避免循环 import 在此单独定义同一份数值)。
_EMOTION_VALENCE_ANCHOR = {
    "joyful": 1.0, "calm": 0.6, "curious": 0.55,
    "anxious": 0.35, "angry": 0.28, "fearful": 0.2, "sad": 0.12,
}


def _nearest_emotion_by_valence(valence: float) -> str:
    """模型 valence 最近邻分类到 EMOTION_KEYWORDS 7 类之一(复用同一套类目体系)。"""
    return min(_EMOTION_VALENCE_ANCHOR, key=lambda e: abs(_EMOTION_VALENCE_ANCHOR[e] - valence))


def _model_window_valences(windows: list) -> list:
    """emotion_vad 模型批量取每个窗口 valence(RUOYU_NN_VAD=1 门控·一次 subprocess·摊薄加载)。

    命中 → float；未命中/模型不可用 → None(调用方回退关键词扫描·零回归)。保序一一对应。"""
    n = len(windows)
    if n == 0 or _os.environ.get("RUOYU_NN_VAD") != "1":
        return [None] * n
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        try:
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch(windows) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch(windows)
    except Exception:
        return [None] * n
    if not preds or len(preds) != n:
        return [None] * n
    out = []
    for p in preds:
        if p and p.get("valence") is not None:
            try:
                out.append(float(p["valence"]))
            except (TypeError, ValueError):
                out.append(None)
        else:
            out.append(None)
    return out


def load_chapter_text(project_root: Path, ch: int) -> str:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def detect_emotions_for_char_detail(text: str, char_name: str) -> dict:
    """近似算法(detail 版)：扫角色名附近 ±150 字窗口 → counts + source 归因。

    🔴 2026-07-01 emotion_vad 模型集成：每窗先试模型 valence(RUOYU_NN_VAD=1)→最近邻分类到
    EMOTION_KEYWORDS 7 类之一；模型不可用/该窗未命中 → 回退关键词扫描(与旧逻辑逐字节一致·
    零回归)。source 字段(model_vad/lexicon_fallback/mixed/none)供训练数据归因。
    """
    empty = {"counts": Counter(), "source": "none",
             "model_window_count": 0, "lexicon_window_count": 0}
    if not text or not char_name:
        return empty
    # 找所有角色名出现位置
    positions = [m.start() for m in re.finditer(re.escape(char_name), text)]
    if not positions:
        return empty
    windows = [text[max(0, pos - 150):pos + 150] for pos in positions]
    model_valences = _model_window_valences(windows)
    counts = Counter()
    model_hits = 0
    for window, mval in zip(windows, model_valences):
        if mval is not None:
            counts[_nearest_emotion_by_valence(mval)] += 1
            model_hits += 1
            continue
        for emotion, kws in EMOTION_KEYWORDS.items():
            for kw in kws:
                if kw in window:
                    counts[emotion] += 1
                    break  # 每个 emotion 在一个 window 只算 1 次
    lexicon_hits = len(windows) - model_hits
    if model_hits == len(windows):
        source = "model_vad"
    elif model_hits == 0:
        source = "lexicon_fallback"
    else:
        source = "mixed"
    return {"counts": counts, "source": source,
            "model_window_count": model_hits, "lexicon_window_count": lexicon_hits}


def detect_emotions_for_char(text: str, char_name: str) -> Counter:
    """近似算法：扫角色名附近 ±150 字内的情绪(兼容旧调用签名·cluster_summary_builder.py 消费)。"""
    return detect_emotions_for_char_detail(text, char_name)["counts"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    ap.add_argument("--characters", type=str, default=None, help="逗号分隔；不指定则取核心角色")
    args = ap.parse_args()

    project_root = Path(args.project)

    char_emotion_log = defaultdict(list)  # char -> [(ch, emotion_dict)]
    recent: list[int] = []
    chars: list[str] = []
    # 🔴 2026-07-01 emotion_vad 模型集成：source 归因计数(仅非 ledger 磁盘扫描路径会累积)
    vad_model_windows = 0
    vad_lexicon_windows = 0

    # ===== 2026-05-29 cluster 化分支：账本有 char_emotion_counts → 取预算逐章情绪计数 =====
    # --last-n 在 cluster 模式语义为「最后 N 个 cluster 的章」；不再逐章扫文本
    use_ledger = (
        csr.is_cluster_mode()
        and csr.ledger_has_field(project_root, "char_emotion_counts")
    )
    if use_ledger:
        recs = csr.get_chapter_records(project_root, last_n_clusters=args.last_n)
        # 角色范围：命令行指定优先，否则取账本里出现过的全部角色
        if args.characters:
            chars = [c.strip() for c in args.characters.split(",") if c.strip()]
        else:
            seen = set()
            for _ch, rec in recs:
                for c in (rec.get("char_emotion_counts") or {}).keys():
                    seen.add(c)
            chars = sorted(seen)
        if not chars:
            print("[SKIP] 无角色可扫")
            sys.exit(0)
        for ch, rec in recs:
            recent.append(ch)
            cec = rec.get("char_emotion_counts") or {}
            for c in chars:
                counts = cec.get(c) or {}
                # 只保留已知情绪类型，与 detect_emotions_for_char 同口径
                clean = {em: int(n) for em, n in counts.items() if em in EMOTION_KEYWORDS}
                char_emotion_log[c].append((ch, clean))
        recent = sorted(set(recent))
        if not recent:
            print("[SKIP] cluster 账本无 char_emotion_counts 记录")
            sys.exit(0)
    else:
        # ===== 原逐章磁盘逻辑（非 cluster 模式 / 账本缺字段 → 零回归）=====
        # 章节列表
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        recent = chapters[-args.last_n:]

        # 角色列表
        if args.characters:
            chars = [c.strip() for c in args.characters.split(",") if c.strip()]
        else:
            pool_path = project_root / "_数据库" / "角色池.json"
            if pool_path.exists():
                try:
                    pool = json.loads(pool_path.read_text(encoding="utf-8"))
                    # 🔴 2026-06-28 角色池schema统一canonical：core/emerged（不兼容）
                    chars = [c.get("id") for c in (pool.get("core") or []) if isinstance(c, dict) and c.get("id")]
                    chars += [c.get("id") for c in (pool.get("emerged") or []) if isinstance(c, dict) and c.get("id")]
                except Exception:
                    chars = []
            else:
                chars = []

        if not chars:
            print("[SKIP] 无角色可扫")
            sys.exit(0)

        # 扫描
        for ch in recent:
            text = load_chapter_text(project_root, ch)
            for c in chars:
                detail = detect_emotions_for_char_detail(text, c)
                char_emotion_log[c].append((ch, dict(detail["counts"])))
                vad_model_windows += detail["model_window_count"]
                vad_lexicon_windows += detail["lexicon_window_count"]

    # 🔴 2026-07-01 emotion_vad 模型集成：整轮 source 归因(供训练数据溯源)。
    # ledger 路径不做文本扫描(账本已有预算好的计数)→ 与 model/lexicon 归因无关，单独标记。
    if use_ledger:
        vad_source = "not_applicable_ledger_mode"
    else:
        vad_total = vad_model_windows + vad_lexicon_windows
        if vad_total == 0:
            vad_source = "none"
        elif vad_model_windows == vad_total:
            vad_source = "model_vad"
        elif vad_model_windows == 0:
            vad_source = "lexicon_fallback"
        else:
            vad_source = "mixed"

    # 分析模式
    findings = []
    for c, log in char_emotion_log.items():
        # 计算总分布
        total_counts = Counter()
        for _, cnt in log:
            for em, n in cnt.items():
                total_counts[em] += n
        if not total_counts:
            continue
        total = sum(total_counts.values())
        distribution = {em: round(n / total, 2) for em, n in total_counts.items()}

        # 检测单一化
        top_emotion, top_count = total_counts.most_common(1)[0]
        top_pct = top_count / total
        if top_pct > 0.7 and len(recent) >= 5:
            findings.append({
                "severity": "advisory",
                "code": "EMOTION_FLATLINE",
                "character": c,
                "top_emotion": top_emotion,
                "top_pct": round(top_pct, 2),
                "distribution": distribution,
                "suggestion": f"{c} 近 {len(recent)} 章 {round(top_pct*100)}% 是 {top_emotion} → 角色情绪僵化，下章应有反差",
            })

        # 检测连续单一情绪章节（真·连续段：按章号排序后取相邻 run 最长段，而非全窗口计数）
        chs_with_top = sorted(ch for ch, cnt in log if cnt.get(top_emotion, 0) > 0 and not any(cnt.get(e, 0) > cnt.get(top_emotion, 0) for e in EMOTION_KEYWORDS.keys() if e != top_emotion))
        longest_run: list[int] = []
        cur_run: list[int] = []
        for ch in chs_with_top:
            if cur_run and ch == cur_run[-1] + 1:
                cur_run.append(ch)
            else:
                cur_run = [ch]
            if len(cur_run) > len(longest_run):
                longest_run = list(cur_run)
        if len(longest_run) >= 6:
            findings.append({
                "severity": "advisory",
                "code": "EMOTION_RUN_TOO_LONG",
                "character": c,
                "emotion": top_emotion,
                "consecutive_chs": longest_run,
                "suggestion": f"{c} 连续 {len(longest_run)} 章主导情绪都是 {top_emotion} → 建议下章打破",
            })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "emotion_pattern",
        "scan_ts": ts,
        "chapters_scanned": recent,
        "characters_scanned": chars,
        "char_emotion_log": {c: log for c, log in char_emotion_log.items()},
        "findings": findings,
        # 🔴 2026-07-01 emotion_vad 模型集成：source 归因(model_vad/lexicon_fallback/mixed/
        # not_applicable_ledger_mode)供训练数据溯源
        "vad_source": vad_source,
        "vad_model_window_count": vad_model_windows,
        "vad_lexicon_window_count": vad_lexicon_windows,
    }
    out_path = out_dir / f"emotion_pattern_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[emotion_pattern] {len(recent)} 章 × {len(chars)} 角色: {len(findings)} 项发现")
    for f in findings[:5]:
        print(f"  [{f['severity'].upper()}] {f['character']}: {f['suggestion']}")
    print(f"报告: {out_path}")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
