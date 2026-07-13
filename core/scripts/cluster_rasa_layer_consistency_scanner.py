#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cluster_rasa_layer_consistency_scanner.py — Rasa 双层一致 · R25 W13 Batch-MM · P1

【缺口 · Bharata Natyashastra Rasa Theory · 与 Plutchik/VAD/Reagan 正交】
sthāyī = 持续底色 rasa（9 类 navarasa：śṛṅgāra 爱 / hāsya 笑 / karuṇā 悲 /
raudra 怒 / vīra 雄 / bhayānaka 惧 / bībhatsa 厌 / adbhuta 奇 / śānta 静）。
vyabhicāri = 33 类过场情感（占位映射 10-12 标签：harṣa 喜 / cintā 愁 /
vitarka 疑 / smṛti 忆 / vrīḍā 羞 / dhṛti 镇 / āvega 急 / glāni 惫 /
viṣāda 沮 / autsukya 急望 / nirveda 厌世 / mada 醉）。

【做法 · 确定性 · 零 LLM/零联网（占位规则匹配 · _placeholder=true）】
  · 场景拆 = `* / --- / ◇ / ◆` 标记 + 段首时间词兜底；每场景查 sthāyī 词典
    top-1 主导 rasa（命中最多类）；cluster top-1 覆盖率 < 60% → 底色摇摆
  · vyabhicāri 过场标签：全 cluster 命中 10-12 类中 < 4 类 → 过场塌缩单层
  · 作者档 rasa_layer_preferences.dominant_rasa_floor / transient_diversity_floor
    覆盖通用 60% / 4 阈值 · 第一权威

【三 advisory · 全 advisory shadow】
  · RASA_LAYER_DOMINANT_DRIFT   — sthāyī top-1 < floor · 底色摇摆
  · RASA_LAYER_TRANSIENT_FLAT   — vyabhicāri 类别 < floor · 过场塌缩
  · RASA_LAYER_OK               — 两层 OK · info

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  RASA_LAYER_* 绝不进 audit_hub.HARD_GATE_CODES。

env CLUSTER_RASA_LAYER_MODE: off / shadow（默认） / active
用法: python cluster_rasa_layer_consistency_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_DOMINANT_DRIFT = "RASA_LAYER_DOMINANT_DRIFT"
ISSUE_CODE_TRANSIENT_FLAT = "RASA_LAYER_TRANSIENT_FLAT"
ISSUE_CODE_OK = "RASA_LAYER_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 9 navarasa sthāyī 占位词典
_STHAYI_LEX = {
    "_placeholder": True,
    "_doc": "R25 W13 Batch-MM·9 navarasa sthāyī 占位",
    "sringara": ["爱恋", "情意", "缠绵", "心动", "倾心", "钟情", "脉脉"],     # 爱
    "hasya": ["笑声", "哈哈", "调笑", "戏谑", "好笑", "捧腹", "嬉闹"],         # 笑
    "karuna": ["悲痛", "哀伤", "悲泣", "悲恸", "凄然", "泪落", "哀切"],       # 悲
    "raudra": ["怒喝", "震怒", "暴怒", "怒火", "戾气", "盛怒", "暴跳"],       # 怒
    "vira": ["凛然", "豪气", "英武", "壮志", "凌云", "气吞", "无畏"],         # 雄
    "bhayanaka": ["惊惧", "惧色", "胆寒", "畏惧", "毛骨", "颤栗", "心悸"],   # 惧
    "bibhatsa": ["恶心", "腥臭", "厌恶", "作呕", "腐烂", "污浊", "肮脏"],     # 厌
    "adbhuta": ["惊奇", "诧异", "骇然", "瞠目", "震惊", "不解", "称奇"],     # 奇
    "shanta": ["平静", "安宁", "祥和", "禅意", "空寂", "止水", "淡然"],     # 静
}

# 33 类 vyabhicāri 占位映射到 12 规范化标签
_VYABHICARI_LEX = {
    "_placeholder": True,
    "_doc": "R25 W13 Batch-MM·12 vyabhicāri 过场占位",
    "harsa": ["喜悦", "欢喜", "雀跃"],                # 喜
    "cinta": ["忧虑", "心事", "愁绪"],                # 愁
    "vitarka": ["怀疑", "存疑", "疑虑"],              # 疑
    "smrti": ["回忆", "想起", "记起"],                # 忆
    "vrida": ["羞赧", "脸红", "腼腆"],                # 羞
    "dhrti": ["镇定", "从容", "稳如"],                # 镇
    "avega": ["急切", "忙不迭", "匆匆"],              # 急
    "glani": ["疲惫", "疲倦", "倦怠"],                # 惫
    "visada": ["沮丧", "颓丧", "灰心"],               # 沮
    "autsukya": ["急望", "渴望", "盼着"],             # 急望
    "nirveda": ["厌世", "心灰", "万念"],              # 厌世
    "mada": ["陶醉", "迷醉", "醺然"],                 # 醉
}

DOMINANT_FLOOR_DEFAULT = 0.60   # top-1 占比下限
TRANSIENT_FLOOR_DEFAULT = 4     # 过场类别数下限


def _mode() -> str:
    m = (os.environ.get("CLUSTER_RASA_LAYER_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _split_scenes(text: str) -> list:
    """场景拆 = 标记行 + 段首时间词兜底。"""
    marker_re = re.compile(r"^\s*(\*{2,}|-{3,}|◇{2,}|◆{2,})\s*$", re.MULTILINE)
    scenes = marker_re.split(text)
    # 过滤标记片段
    scenes = [s.strip() for s in scenes
              if s and not re.match(r"^\s*(\*{2,}|-{3,}|◇{2,}|◆{2,})\s*$", s)]
    # 兜底：若全文未拆出多个场景，按双段间隔粗切
    if len(scenes) <= 1:
        parts = re.split(r"\n\s*\n", text)
        scenes = [p.strip() for p in parts if p.strip()]
    return scenes


def _load_thresholds(project_root) -> tuple:
    """读 _数据库/作者风格.json · rasa_layer_preferences · 作者档第一权威。"""
    dom = DOMINANT_FLOOR_DEFAULT
    trans = TRANSIENT_FLOOR_DEFAULT
    author_owned = False
    if not project_root:
        return (dom, trans, author_owned)
    try:
        p = Path(project_root) / "_数据库" / "作者风格.json"
        if not p.exists():
            return (dom, trans, author_owned)
        prof = json.loads(p.read_text(encoding="utf-8"))
        pref = prof.get("rasa_layer_preferences") or {}
        if "dominant_rasa_floor" in pref:
            dom = float(pref["dominant_rasa_floor"])
            author_owned = True
        if "transient_diversity_floor" in pref:
            trans = int(pref["transient_diversity_floor"])
            author_owned = True
        return (dom, trans, author_owned)
    except Exception:
        return (dom, trans, author_owned)


def _classify_scene_sthayi(scene_text: str) -> tuple:
    """场景 sthāyī top-1 类（命中最多类）。返回 (label or None, counts dict)。"""
    counts = {}
    for label, words in _STHAYI_LEX.items():
        if label.startswith("_"):
            continue
        c = sum(scene_text.count(w) for w in words)
        if c > 0:
            counts[label] = c
    if not counts:
        return (None, counts)
    top = max(counts.items(), key=lambda kv: kv[1])
    return (top[0], counts)


def _detect_vyabhicari(text: str) -> dict:
    """全 cluster vyabhicāri 类别命中。返回 {label: count} 仅命中类。"""
    hits = {}
    for label, words in _VYABHICARI_LEX.items():
        if label.startswith("_"):
            continue
        c = sum(text.count(w) for w in words)
        if c > 0:
            hits[label] = c
    return hits


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "cluster_rasa_layer_consistency_scanner", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": True,
    }
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 800:
        out["note"] = "草稿太短·跳过"
        return out

    scenes = _split_scenes(text)
    scene_labels = []
    scene_reports = []
    for i, s in enumerate(scenes):
        label, counts = _classify_scene_sthayi(s)
        scene_reports.append({"scene_idx": i, "cjk": _cjk_count(s),
                              "sthayi_top1": label, "_counts": counts})
        if label:
            scene_labels.append(label)

    label_freq = {}
    for lab in scene_labels:
        label_freq[lab] = label_freq.get(lab, 0) + 1
    total_classified = sum(label_freq.values())
    top1_label, top1_count = (None, 0)
    if label_freq:
        top1_label, top1_count = max(label_freq.items(), key=lambda kv: kv[1])
    top1_ratio = round(top1_count / max(1, total_classified), 4) if total_classified else 0.0

    vyab_hits = _detect_vyabhicari(text)
    transient_diversity = len(vyab_hits)

    dom_floor, trans_floor, author_owned = _load_thresholds(project_root)

    out.update({
        "cjk": cjk,
        "scene_count": len(scenes),
        "scene_reports": scene_reports,
        "sthayi_label_freq": label_freq,
        "sthayi_top1": top1_label,
        "sthayi_top1_ratio": top1_ratio,
        "vyabhicari_hits": vyab_hits,
        "transient_diversity": transient_diversity,
        "thresholds": {"dominant_rasa_floor": dom_floor,
                       "transient_diversity_floor": trans_floor,
                       "author_owned": author_owned},
    })

    flags = []
    # 只在有足够场景分类时评 sthāyī floor（防卡通用阈值）
    if total_classified >= 2 and top1_ratio < dom_floor:
        flags.append({"code": ISSUE_CODE_DOMINANT_DRIFT, "severity": "minor",
                      "msg": (f"sthāyī top-1={top1_label} 占比 {top1_ratio}"
                              f" < floor {dom_floor}·底色摇摆")})
    if transient_diversity < trans_floor:
        flags.append({"code": ISSUE_CODE_TRANSIENT_FLAT, "severity": "minor",
                      "msg": (f"vyabhicāri 过场类别 {transient_diversity}"
                              f" < floor {trans_floor}·塌缩单层")})
    if not flags:
        flags.append({"code": ISSUE_CODE_OK, "severity": "info",
                      "msg": (f"sthāyī top-1={top1_label} 占 {top1_ratio} ·"
                              f" vyabhicāri 类 {transient_diversity} · 两层 OK")})

    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "cluster_rasa_layer_consistency",
                "severity": f["severity"],
                "code": f["code"], "message": f["msg"],
                "_doc": "R25 W13 Batch-MM·Bharata Rasa 双层·advisory·绝不 hard_gate",
            })
        minor = [f for f in flags if f["severity"] == "minor"]
        out["verdict"] = "FAIL_MINOR" if minor else "PASS"
        out["warning"] = "·".join(f["msg"] for f in minor) or None
    elif mode == "shadow":
        minor = [f for f in flags if f["severity"] == "minor"]
        if minor:
            print("[SHADOW] cluster_rasa_layer: "
                  + "·".join(f["msg"] for f in minor) + " — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="cluster Rasa 双层一致 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
