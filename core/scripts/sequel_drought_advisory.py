#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sequel_drought_advisory.py — Swain Scene/Sequel 双单元 sequel 干旱 advisory · 2026-06-21 R20 W9 Batch-BB · P2

【缺口 · R20 影视前置 id 7】Dwight Swain《Techniques of the Selling Writer》：
故事 = scene (目标→冲突→灾难) 与 sequel (反应→困境→决定) 交替推进 ·
LLM 默认连续堆 scene 单元（动作密集）· sequel 长期缺位 → 主角情绪没有缓冲间。

【做法 · 占位】
  · 输入：cluster brief.json 的 scene_storyboard
  · 校验：每个 scene 是否标 unit_type=scene/sequel
  · 计算 max consecutive scene_run（最长连续 scene 不被 sequel 打断）
  · 题材门槛：爽文 N=5（容忍长 scene 链）· 严肃 N=3（要 sequel 多）

【两 advisory】
  · SEQUEL_DROUGHT — max_scene_run > N（按题材）
  · UNIT_TYPE_MISSING — unit_type 填充率 < 0.3（占位 missing 不报硬错）

【北极星】②④⑤ advisory shadow · 绝不 hard_gate
  作者档 author_sequel_baseline.max_scene_run = N → 让位
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE_DROUGHT = "SEQUEL_DROUGHT"
ISSUE_CODE_MISSING = "UNIT_TYPE_MISSING"

DEFAULT_MAX_RUN_WEBNOVEL = 5
DEFAULT_MAX_RUN_LITERARY = 3
DEFAULT_FILLED_LOW = 0.30

_GENRE_WEBNOVEL = {"爽文", "web_novel", "起点", "qidian", "tomato", "番茄"}
_GENRE_LITERARY = {"严肃", "literary", "纯文学"}


def _mode() -> str:
    m = (os.environ.get("SEQUEL_DROUGHT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_storyboard(path: str) -> list[dict]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(obj, dict):
        if isinstance(obj.get("scene_storyboard"), list):
            return obj["scene_storyboard"]
        clusters = obj.get("clusters") or []
        for c in clusters if isinstance(clusters, list) else []:
            if isinstance(c, dict) and isinstance(c.get("scene_storyboard"), list):
                return c["scene_storyboard"]
        return []
    if isinstance(obj, list):
        return obj
    return []


def _read_genre_tags(project_root) -> set[str]:
    if not project_root:
        return set()
    db = Path(project_root) / "_数据库"
    tags: set[str] = set()
    for fname in ("用户偏好.json", "作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        raw = obj.get("genre_tags") or obj.get("genre") or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            for t in raw:
                if isinstance(t, str):
                    tags.add(t)
    return tags


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = Path(project_root) / "_数据库" / fname
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(obj, dict) and isinstance(obj.get("author_sequel_baseline"), dict):
                return obj["author_sequel_baseline"]
    return None


def _max_scene_run(storyboard: list[dict]) -> int:
    run = 0
    best = 0
    for sc in storyboard:
        ut = sc.get("unit_type") if isinstance(sc, dict) else None
        if ut == "scene" or ut is None:
            # None 也视作 scene（保守：填了 sequel 才打断）
            if ut == "scene":
                run += 1
            else:
                run += 1  # 未标也算 scene 干旱
            best = max(best, run)
        elif ut == "sequel":
            run = 0
    return best


def advise(storyboard: list[dict], project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "sequel_drought_advisory", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "_storyboard_placeholder": True}
    if mode == "off":
        return out
    total = len(storyboard or [])
    if total == 0:
        out["note"] = "scene_storyboard 为空·跳过"
        return out
    typed = sum(1 for s in storyboard if isinstance(s, dict) and s.get("unit_type") in ("scene", "sequel"))
    filled_share = round(typed / total, 3) if total else 0.0
    n_scene = sum(1 for s in storyboard if isinstance(s, dict) and s.get("unit_type") == "scene")
    n_sequel = sum(1 for s in storyboard if isinstance(s, dict) and s.get("unit_type") == "sequel")
    max_run = _max_scene_run(storyboard)

    genre_tags = _read_genre_tags(project_root)
    baseline = _read_author_baseline(project_root)
    is_webnovel = bool(genre_tags & _GENRE_WEBNOVEL)
    is_literary = bool(genre_tags & _GENRE_LITERARY)
    if is_webnovel:
        max_run_thr = DEFAULT_MAX_RUN_WEBNOVEL
        genre_class = "webnovel"
    elif is_literary:
        max_run_thr = DEFAULT_MAX_RUN_LITERARY
        genre_class = "literary"
    else:
        max_run_thr = DEFAULT_MAX_RUN_WEBNOVEL
        genre_class = "neutral_default_webnovel"
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        if isinstance(baseline.get("max_scene_run"), int):
            max_run_thr = baseline["max_scene_run"]
            baseline_source = "author_profile"

    out.update({
        "scenes_total": total,
        "unit_type_filled": typed,
        "filled_share": filled_share,
        "n_scene": n_scene,
        "n_sequel": n_sequel,
        "scene_sequel_ratio": round(n_scene / n_sequel, 3) if n_sequel else None,
        "max_scene_run": max_run,
        "max_run_threshold": max_run_thr,
        "genre_class": genre_class,
        "genre_tags": sorted(genre_tags),
        "baseline_source": baseline_source,
    })

    flags = []
    if filled_share < DEFAULT_FILLED_LOW:
        flags.append({"code": ISSUE_CODE_MISSING,
                      "msg": f"unit_type 填充率={filled_share} < {DEFAULT_FILLED_LOW}·outline 缺 Swain 设计"})
    if filled_share >= DEFAULT_FILLED_LOW and max_run > max_run_thr:
        flags.append({"code": ISSUE_CODE_DROUGHT,
                      "msg": f"max_scene_run={max_run} > {max_run_thr} ({genre_class})·"
                             f"连续 scene 单元过多·sequel 反应缓冲缺失"})

    out["flags"] = flags
    if flags and mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "sequel_drought_advisory", "severity": "minor",
                "code": f["code"], "message": f["msg"],
                "_doc": "Swain Scene/Sequel · advisory · 绝不 hard_gate"})
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = "·".join(f["msg"] for f in flags)
    elif flags:
        print(f"[SHADOW] sequel_drought: {'·'.join(f['msg'] for f in flags)} — 不上报",
              file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Swain Scene/Sequel drought advisory (shadow)")
    ap.add_argument("storyboard_path")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    try:
        sb = _load_storyboard(args.storyboard_path)
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": f"加载失败：{e}"}, ensure_ascii=False))
        sys.exit(2)
    rep = advise(sb, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
