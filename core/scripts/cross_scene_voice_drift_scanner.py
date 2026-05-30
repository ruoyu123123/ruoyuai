#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_scene_voice_drift_scanner.py — cluster 内同角色跨场景 voice 漂移检测

v2 cluster 化方案 Phase 3（2026-05-28）·
之前 voice 漂移由 novel-voice-checker agent 检测，本 scanner 是机械层补丁，
快速检出同角色在 cluster 不同 scene 的 voice 偏差（句长 / catchphrase / banned_phrases）。

输出 issue code: VOICE_DRIFT_CROSS_SCENE
gate_level: advisory（声音漂移 = 工艺类，writer 有理由可豁免）

用法：python cross_scene_voice_drift_scanner.py <project> <cluster_draft_path>
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict


def load(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def split_scenes(text: str, min_scene_len: int = 500) -> list[str]:
    """启发式切场景：按 \n---\n 或 \n\n\n 分隔；不够则按段数硬切。"""
    parts = re.split(r"\n---+\n|\n\n\n+", text)
    parts = [p.strip() for p in parts if p.strip() and len(p) >= min_scene_len]
    return parts if parts else [text]


# 2026-05-30 北极星复审：原正则只含 ASCII " + 「」，漏 U+201C/U+201D 弯引号（项目正文实际用弯引号）
# → scanner 抽不到对话整体空转。补全弯引号（遵 feedback_dialogue_quote_unicode_distinction）。
DIALOGUE_RE = re.compile('["“「『]([^"”」』\n]{1,300})["”」』]')
SPEAKER_PATTERN = re.compile(r'([一-鿿]{2,4})(?:说道?|道|问道?|答道?|笑道?|骂道?|喊道?|嘀咕|开口|说)')


def extract_dialogues_by_speaker(scene_text: str, known_aliases: dict[str, str]) -> dict:
    """从场景文本提取每个角色的对话样本。
    aliases: alias → canonical_name 映射"""
    by_speaker = defaultdict(list)
    lines = scene_text.split("\n")
    last_speaker = None
    for line in lines:
        # 先看本行有没有 speaker tag
        m = SPEAKER_PATTERN.search(line)
        if m:
            name = m.group(1)
            canonical = known_aliases.get(name, name)
            last_speaker = canonical
        # 抽对话
        for q in DIALOGUE_RE.findall(line):
            if last_speaker:
                by_speaker[last_speaker].append(q)
    return dict(by_speaker)


def compute_voice_metrics(dialogues: list[str]) -> dict:
    """对一组对话计算 voice 指纹。"""
    if not dialogues:
        return None
    total_chars = sum(len(d) for d in dialogues)
    return {
        "count": len(dialogues),
        "avg_len": total_chars / len(dialogues),
        "max_len": max(len(d) for d in dialogues),
        "has_ellipsis_ratio": sum(1 for d in dialogues if "…" in d or "..." in d) / len(dialogues),
        "has_question_ratio": sum(1 for d in dialogues if "?" in d or "？" in d) / len(dialogues),
    }


# ── L4·D4 voice 区分度 + D8 口癖一致性（2026-05-31 · 主代理手动实现）──────────
# 全 advisory · env VOICE_D4D8_MODE 默认 shadow（只记不进 warning/exit · 回归0）
# 零依赖纯统计（不用 embedding · 避 hash backend 假语义）· 砍掉项 D6情绪弧/D10情绪直陈不做
VOICE_TICS = ("啊", "呢", "吧", "嘛", "呗", "啦", "哈", "咯", "喔", "哦",
              "嗯", "唉", "哼", "咦", "嘞", "咧", "呐", "罢")


def _d4d8_mode() -> str:
    """VOICE_D4D8_MODE：默认 shadow（只记不进顶层 warning）· {shadow, active, off}· 非法回退 shadow。"""
    m = (os.environ.get("VOICE_D4D8_MODE") or "shadow").strip().lower()
    return m if m in ("shadow", "active", "off") else "shadow"


def _voice_fingerprint(dialogues: list[str]) -> dict | None:
    """角色对白聚合 voice 指纹（零依赖统计 · D4/D8 共用）。"""
    if not dialogues:
        return None
    n = len(dialogues)
    total_chars = sum(len(d) for d in dialogues) or 1
    tic_counts = {t: 0 for t in VOICE_TICS}
    for d in dialogues:
        for t in VOICE_TICS:
            tic_counts[t] += d.count(t)
    return {
        "avg_len": sum(len(d) for d in dialogues) / n,
        "ellipsis_ratio": sum(1 for d in dialogues if "…" in d or "..." in d) / n,
        "question_ratio": sum(1 for d in dialogues if "?" in d or "？" in d) / n,
        "exclaim_ratio": sum(1 for d in dialogues if "!" in d or "！" in d) / n,
        "tic_rate": {t: tic_counts[t] / total_chars * 1000 for t in VOICE_TICS},
        "_n": n,
    }


def _fingerprint_distance(a: dict, b: dict) -> float:
    """两角色 voice 指纹归一距离（0=同质 · 越大越区分）。"""
    dims = []
    m = (a["avg_len"] + b["avg_len"]) / 2 or 1
    dims.append(min(abs(a["avg_len"] - b["avg_len"]) / m, 1.0))
    for k in ("ellipsis_ratio", "question_ratio", "exclaim_ratio"):
        dims.append(abs(a[k] - b[k]))
    tic_l1 = sum(abs(a["tic_rate"][t] - b["tic_rate"][t]) for t in VOICE_TICS)
    dims.append(min(tic_l1 / 10.0, 1.0))
    return sum(dims) / len(dims)


def compute_d4_distinctiveness(char_all_dialogues: dict) -> dict:
    """D4：角色间 voice 区分度（两两距离均值过低=角色说话同质化 · advisory）。"""
    chars = [(name, _voice_fingerprint(qs)) for name, qs in char_all_dialogues.items()]
    chars = [(n, f) for n, f in chars if f and f["_n"] >= 3]
    if len(chars) < 2:
        return {"applicable": False, "reason": "少于2个有足够对白(≥3句)的角色"}
    pairs = []
    for i in range(len(chars)):
        for j in range(i + 1, len(chars)):
            d = _fingerprint_distance(chars[i][1], chars[j][1])
            pairs.append((chars[i][0], chars[j][0], round(d, 3)))
    dists = [p[2] for p in pairs]
    mean_dist = sum(dists) / len(dists)
    low_pairs = [{"a": a, "b": b, "dist": d} for a, b, d in pairs if d < 0.08]
    return {
        "applicable": True,
        "char_count": len(chars),
        "mean_pairwise_distance": round(mean_dist, 3),
        "low_distinctiveness_pairs": low_pairs[:5],
        "low_distinctiveness": mean_dist < 0.10,
    }


def compute_d8_tic_consistency(char_scene_tics: dict) -> dict:
    """D8：同角色跨场景口癖一致性（某场景显著口癖另一场景几乎消失=不一致 · advisory）。"""
    issues = []
    for name, scene_tics in char_scene_tics.items():
        valid = [(idx, tr) for idx, tr in scene_tics if tr]
        if len(valid) < 2:
            continue
        for t in VOICE_TICS:
            rates = [tr.get(t, 0.0) for _, tr in valid]
            mx, mn = max(rates), min(rates)
            if mx >= 2.0 and mn < 0.3:
                issues.append({"character": name, "tic": t,
                               "max_rate": round(mx, 2), "min_rate": round(mn, 2)})
    return {"inconsistent_tics": issues[:8], "count": len(issues)}


def scan(project_root: Path, draft_path: Path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")
    scenes = split_scenes(text)

    # 加载人物卡 aliases
    cards = load(project_root / "_数据库" / "人物卡.json").get("characters", [])
    aliases = {}
    for c in cards:
        name = c.get("name", "")
        aliases[name] = name
        for a in c.get("aliases", []) or []:
            aliases[a] = name

    # 每个 scene 提取每个角色的对话
    scene_metrics = []  # [{scene_idx, by_speaker: {name: metrics}}]
    char_all_dialogues = defaultdict(list)   # D4: 角色 → 跨场景全部对白
    char_scene_tics = defaultdict(list)      # D8: 角色 → [(scene_idx, tic_rate)]
    for i, scene in enumerate(scenes):
        sd = extract_dialogues_by_speaker(scene, aliases)
        scene_metrics.append({
            "scene_idx": i,
            "by_speaker": {name: compute_voice_metrics(qs) for name, qs in sd.items() if qs},
        })
        for name, qs in sd.items():
            if qs:
                char_all_dialogues[name].extend(qs)
                fp = _voice_fingerprint(qs)
                char_scene_tics[name].append((i, fp["tic_rate"] if fp else {}))

    # 跨场景检测每个角色的 voice 漂移
    drift_issues = []
    # 收集每个角色在所有场景的 metrics
    by_char = defaultdict(list)  # name → [(scene_idx, metrics)]
    for sm in scene_metrics:
        for name, met in sm["by_speaker"].items():
            if met:
                by_char[name].append((sm["scene_idx"], met))

    for name, entries in by_char.items():
        if len(entries) < 2:
            continue  # 出场 <2 场景，无法对比
        # 比较 avg_len 偏差（>50% 偏差视为漂移）
        lens = [e[1]["avg_len"] for e in entries]
        if max(lens) > 0:
            mean_len = sum(lens) / len(lens)
            for scene_idx, met in entries:
                deviation = abs(met["avg_len"] - mean_len) / max(mean_len, 1)
                if deviation > 0.5 and met["count"] >= 3:
                    drift_issues.append({
                        "character": name,
                        "scene_idx": scene_idx,
                        "avg_len_this_scene": round(met["avg_len"], 1),
                        "avg_len_cluster_mean": round(mean_len, 1),
                        "deviation_pct": round(deviation * 100, 1),
                        "sample_count": met["count"],
                        "type": "avg_dialogue_length_drift",
                    })

    # D4/D8（2026-05-31）· env VOICE_D4D8_MODE 默认 shadow（只挂字段不改 warning/exit · 回归0）
    mode = _d4d8_mode()
    d4 = compute_d4_distinctiveness(char_all_dialogues) if mode != "off" else None
    d8 = compute_d8_tic_consistency(char_scene_tics) if mode != "off" else None
    base_warning = (
        f"⚠️ {len(drift_issues)} 处跨场景 voice 漂移嫌疑（句长偏差 >50%）"
        if drift_issues else None
    )
    d4d8_warning = None
    if mode == "active":   # 仅 active 把 D4/D8 升进 advisory warning（shadow 只挂字段不改判决）
        bits = []
        if d4 and d4.get("low_distinctiveness"):
            bits.append(f"角色voice区分度低(两两均距{d4['mean_pairwise_distance']})")
        elif d4 and d4.get("low_distinctiveness_pairs"):
            bits.append(f"{len(d4['low_distinctiveness_pairs'])}对角色说话同质化")
        if d8 and d8.get("count"):
            bits.append(f"{d8['count']}处角色口癖跨场景不一致")
        if bits:
            d4d8_warning = "；".join(bits)
    final_warning = "；".join([w for w in (base_warning, d4d8_warning) if w]) or None

    return {
        "schema_version": "1.1",
        "scanner": "cross_scene_voice_drift_scanner",
        "gate_level": "advisory",
        "cluster_mode": True,
        "scenes_scanned": len(scenes),
        "characters_with_voice_sample": len(by_char),
        "drift_issues_count": len(drift_issues),
        "drift_issues": drift_issues[:10],
        "d4_voice_distinctiveness": d4,
        "d8_tic_consistency": d8,
        "d4d8_mode": mode,
        "warning": final_warning,
        "severity": "warning" if final_warning else "info",
    }


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    project = Path(args[0]).resolve()
    draft = Path(args[1]).resolve()
    report = scan(project, draft)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "_fatal" in report:
        sys.exit(2)  # 与 locked_fact/pov/foreshadowing_handoff 兄弟 scanner 一致：数据缺失≠干净通过
    if report.get("warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
