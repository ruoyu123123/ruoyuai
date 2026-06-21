#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""horizontal_cloud_advisor.py — 横云断山插入式松弛弧 advisory · R23 W11 Batch-GG · P1

【缺口 · 古典评点（金圣叹批《水浒》）】横云断山：主线长篇高密度推进时
插入一段他者断流弧（旁人/旁线/旁观视角），让读者注意力短暂离主线，
回主线时张力反弹更强。当前 cluster 长主线无 POV 切换路由 → 单一主线疲劳。

【做法 · 确定性 · 零 LLM/零联网】
  · 触发条件全部满足：
    (1) cluster 主线 arc 占比 > 60%（manifest.event_cluster_context.scope_summary 命中度）
    (2) 连续 ≥4 场景 同一主角 POV（启发：scene_break_re 切片 · 段首主语恒同）
    (3) 情感强度 > 0.7（启发：情绪标点密度 + 短句独行率 z 联合分）
    (4) 无 POV 切换（启发：段首人名出现单一）
  · → 建议中段 35-55% 插 200-500 CJK 他者断流弧
  · 写入下 cluster brief pacing_suggestions（占位 · cluster-save-state 接管时回写）

【两 advisory】
  · HORIZONTAL_CLOUD_RECOMMEND — 主线长 + 单 POV + 高情绪 + 无横云 · 建议中段插入
  · HORIZONTAL_CLOUD_PRESENT   — 检出已有横云断山段（旁人 POV 短段）· info 旁注

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  HORIZONTAL_CLOUD_* 绝不进 audit_hub.HARD_GATE_CODES。

env HORIZONTAL_CLOUD_MODE: off / shadow（默认） / active
用法: python horizontal_cloud_advisor.py <draft> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ISSUE_CODE_RECOMMEND = "HORIZONTAL_CLOUD_RECOMMEND"
ISSUE_CODE_PRESENT = "HORIZONTAL_CLOUD_PRESENT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

ARC_OCCUPANCY_TRIGGER = 0.60
MIN_CONSECUTIVE_SCENES = 4
EMOTION_INTENSITY_TRIGGER = 0.70
INSERT_SUGGEST_LO = 0.35
INSERT_SUGGEST_HI = 0.55
INSERT_SUGGEST_CJK_MIN = 200
INSERT_SUGGEST_CJK_MAX = 500

_SCENE_BREAK_RE = re.compile(r"(?:^|\n)\s*[*＊·]{3,}|^\s*第[一二三四五六七八九十百零\d]+幕|\n{3,}", re.M)
_EMOTION_PUNCT_RE = re.compile(r"[！？…]+")


def _mode() -> str:
    m = (os.environ.get("HORIZONTAL_CLOUD_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_scope_summary(manifest_path) -> str:
    if not manifest_path:
        return ""
    try:
        mf = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        ec = mf.get("event_cluster_context") if isinstance(mf, dict) else None
        if isinstance(ec, dict):
            s = ec.get("scope_summary")
            if isinstance(s, str):
                return s
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def _read_protagonist(project_root, manifest_path) -> str:
    if manifest_path:
        try:
            mf = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            ec = mf.get("event_cluster_context") if isinstance(mf, dict) else None
            if isinstance(ec, dict):
                cf = ec.get("characters_focus") or []
                if isinstance(cf, list) and cf:
                    first = cf[0]
                    if isinstance(first, str) and first.strip():
                        return first.strip()
                    if isinstance(first, dict):
                        for k in ("name", "label"):
                            v = first.get(k)
                            if isinstance(v, str) and v.strip():
                                return v.strip()
        except (OSError, json.JSONDecodeError):
            pass
    if project_root:
        p = Path(project_root) / "_数据库" / "人物.json"
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
                chars = obj.get("characters") if isinstance(obj, dict) else None
                if isinstance(chars, list):
                    for c in chars:
                        if isinstance(c, dict) and c.get("role") in ("主角", "protagonist"):
                            n = c.get("name")
                            if isinstance(n, str):
                                return n.strip()
            except (OSError, json.JSONDecodeError):
                pass
    return ""


def _split_scenes(text: str) -> list[str]:
    parts = _SCENE_BREAK_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _scene_lead_subject(scene_text: str) -> str:
    """启发：scene 段首前 20 CJK 取最频繁人名 surface"""
    head = scene_text[:120]
    counts: Counter = Counter()
    for m in re.finditer(r"[一-鿿]{2,4}", head):
        counts[m.group(0)] += 1
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _arc_occupancy(text: str, scope_summary: str, protagonist: str) -> float:
    """启发：主线关键词在文本中的密度"""
    keys = []
    if protagonist:
        keys.append(protagonist)
    if scope_summary:
        for m in re.finditer(r"[一-鿿]{2,4}", scope_summary[:200]):
            keys.append(m.group(0))
    keys = list({k for k in keys if len(k) >= 2})[:10]
    if not keys:
        return 0.0
    cjk = _cjk_count(text)
    if cjk == 0:
        return 0.0
    hits = sum(text.count(k) for k in keys)
    return min(1.0, hits / (cjk / 100))


def _emotion_intensity(text: str) -> float:
    """归一启发：情绪标点密度 + 短句独行率 联合分"""
    cjk = _cjk_count(text)
    if cjk == 0:
        return 0.0
    punct_density = len(_EMOTION_PUNCT_RE.findall(text)) / (cjk / 1000)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        short_solo = sum(1 for l in lines if _cjk_count(l) <= 12) / len(lines)
    else:
        short_solo = 0.0
    # 归一：punct_density / 30 + short_solo（粗启发）
    score = min(1.0, (punct_density / 30.0) * 0.6 + short_solo * 0.4)
    return score


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "horizontal_cloud_advisor", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 2000:
        out["note"] = "草稿太短·跳过"
        return out

    scope_summary = _read_scope_summary(manifest_path)
    protagonist = _read_protagonist(project_root, manifest_path)
    scenes = _split_scenes(text)
    scene_subjects = [_scene_lead_subject(s) for s in scenes] if scenes else []

    # (1) arc 占比
    arc_occ = _arc_occupancy(text, scope_summary, protagonist)
    # (2) 连续同 POV 场景 ≥ 4
    if scene_subjects:
        max_streak = cur = 1
        for i in range(1, len(scene_subjects)):
            if scene_subjects[i] and scene_subjects[i] == scene_subjects[i - 1]:
                cur += 1
                if cur > max_streak:
                    max_streak = cur
            else:
                cur = 1
    else:
        max_streak = 0
    # (3) 情感强度
    emo = _emotion_intensity(text)
    # (4) 无 POV 切换：场景主语唯一
    distinct_subjects = len({s for s in scene_subjects if s})

    cond_arc = arc_occ > ARC_OCCUPANCY_TRIGGER
    cond_streak = max_streak >= MIN_CONSECUTIVE_SCENES
    cond_emo = emo > EMOTION_INTENSITY_TRIGGER
    cond_pov = distinct_subjects <= 1

    out.update({
        "cjk": cjk,
        "scenes_count": len(scenes),
        "arc_occupancy": round(arc_occ, 3),
        "max_pov_streak": max_streak,
        "emotion_intensity": round(emo, 3),
        "distinct_subjects": distinct_subjects,
        "conditions": {
            "arc_long": {"hit": cond_arc, "threshold": ARC_OCCUPANCY_TRIGGER},
            "single_pov_streak": {"hit": cond_streak, "threshold": MIN_CONSECUTIVE_SCENES},
            "high_emotion": {"hit": cond_emo, "threshold": EMOTION_INTENSITY_TRIGGER},
            "no_pov_switch": {"hit": cond_pov},
        },
        "insert_suggest_range": [INSERT_SUGGEST_LO, INSERT_SUGGEST_HI],
        "insert_suggest_cjk": [INSERT_SUGGEST_CJK_MIN, INSERT_SUGGEST_CJK_MAX],
    })

    all_cond = cond_arc and cond_streak and cond_emo and cond_pov

    flags = []
    if all_cond:
        flags.append({
            "code": ISSUE_CODE_RECOMMEND,
            "msg": (f"主线长({arc_occ:.2f})+连续 {max_streak} 场景同 POV+情感强度 {emo:.2f}+单 POV·"
                    f"建议中段 {int(INSERT_SUGGEST_LO*100)}-{int(INSERT_SUGGEST_HI*100)}% "
                    f"插 {INSERT_SUGGEST_CJK_MIN}-{INSERT_SUGGEST_CJK_MAX} CJK 他者断流弧"),
            "severity": "minor",
        })
        # 占位：写入下 cluster brief pacing_suggestions
        if mode == "active" and project_root:
            try:
                clusters_path = Path(project_root) / "_数据库" / "事件簇.json"
                if clusters_path.exists():
                    obj = json.loads(clusters_path.read_text(encoding="utf-8"))
                    cs = obj.get("clusters") if isinstance(obj, dict) else None
                    if isinstance(cs, list) and cs:
                        # 找 status=pending 的 first cluster 写 pacing_suggestions
                        for c in cs:
                            if isinstance(c, dict) and c.get("status") in (None, "pending", "未启动"):
                                ps = c.setdefault("pacing_suggestions", [])
                                if isinstance(ps, list):
                                    note = {"source": "horizontal_cloud_advisor",
                                            "insert_range_pct": [INSERT_SUGGEST_LO, INSERT_SUGGEST_HI],
                                            "insert_cjk_range": [INSERT_SUGGEST_CJK_MIN, INSERT_SUGGEST_CJK_MAX],
                                            "_doc": "横云断山·R23 W11 Batch-GG·advisory"}
                                    if note not in ps:
                                        ps.append(note)
                                break
                        clusters_path.write_text(
                            json.dumps(obj, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            except (OSError, json.JSONDecodeError) as e:
                out["note"] = f"pacing_suggestions 写失败：{str(e)[:120]}"
    elif distinct_subjects >= 2 and cond_arc:
        flags.append({
            "code": ISSUE_CODE_PRESENT,
            "msg": f"检出已有横云断山（场景主语 {distinct_subjects}）",
            "severity": "info",
        })

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "horizontal_cloud", "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "金圣叹评水浒横云断山·R23 W11 Batch-GG·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] horizontal_cloud: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="横云断山 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
