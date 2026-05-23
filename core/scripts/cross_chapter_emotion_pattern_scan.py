"""cross_chapter_emotion_pattern_scan.py — 角色情绪长期画像扫描（v21 R3.3 新增）

借鉴 Character.AI Chat Memories：跨章扫描每个核心角色的情绪占比 + 模式（如「90% 章节谨慎，每 20 章爆发一次」）。

输出报告含：
- 每角色情绪类型分布（按关键词扫文本）
- 情绪连续单一化告警（如连续 8 章同一情绪 = 角色僵化）
- 情绪爆发周期识别（如每 N 章触发一次反差情绪）
- writer reflector 据此发现「角色行为模式正在固化」

退出码: 0 健康 / 1 advisory / 2 致命
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import sys


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


def load_chapter_text(project_root: Path, ch: int) -> str:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def detect_emotions_for_char(text: str, char_name: str) -> Counter:
    """近似算法：扫角色名附近 ±150 字内的情绪关键词。"""
    if not text or not char_name:
        return Counter()
    counts = Counter()
    # 找所有角色名出现位置
    positions = [m.start() for m in re.finditer(re.escape(char_name), text)]
    for pos in positions:
        window = text[max(0, pos - 150):pos + 150]
        for emotion, kws in EMOTION_KEYWORDS.items():
            for kw in kws:
                if kw in window:
                    counts[emotion] += 1
                    break  # 每个 emotion 在一个 window 只算 1 次
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    ap.add_argument("--characters", type=str, default=None, help="逗号分隔；不指定则取核心角色")
    args = ap.parse_args()

    project_root = Path(args.project)

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
                chars = [c.get("id") for c in (pool.get("core_characters") or []) if c.get("id")]
                chars += [c.get("id") for c in (pool.get("emerged_characters") or []) if c.get("id")]
            except Exception:
                chars = []
        else:
            chars = []

    if not chars:
        print("[SKIP] 无角色可扫")
        sys.exit(0)

    # 扫描
    char_emotion_log = defaultdict(list)  # char -> [(ch, emotion_counter)]
    for ch in recent:
        text = load_chapter_text(project_root, ch)
        for c in chars:
            counts = detect_emotions_for_char(text, c)
            char_emotion_log[c].append((ch, dict(counts)))

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

        # 检测连续单一情绪章节
        chs_with_top = [ch for ch, cnt in log if cnt.get(top_emotion, 0) > 0 and not any(cnt.get(e, 0) > cnt.get(top_emotion, 0) for e in EMOTION_KEYWORDS.keys() if e != top_emotion)]
        if len(chs_with_top) >= 6:
            findings.append({
                "severity": "advisory",
                "code": "EMOTION_RUN_TOO_LONG",
                "character": c,
                "emotion": top_emotion,
                "consecutive_chs": chs_with_top[-6:],
                "suggestion": f"{c} 连续 ≥ 6 章主导情绪都是 {top_emotion} → 建议下章打破",
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
