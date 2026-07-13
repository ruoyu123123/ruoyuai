#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sequel_drought_advisory.py — Swain Scene/Sequel 双单元 sequel 干旱 advisory · 2026-06-21 R20 W9 Batch-BB · P2

【缺口 · R20 影视前置 id 7】Dwight Swain《Techniques of the Selling Writer》：
故事 = scene (目标→冲突→灾难) 与 sequel (反应→困境→决定) 交替推进 ·
LLM 默认连续堆 scene 单元（动作密集）· sequel 长期缺位 → 主角情绪没有缓冲间。

【做法】
  · 输入：cluster brief.json 的 scene_storyboard
  · 校验：每个 scene 的 scene_type=proactive_scene/reactive_sequel（Swain 场景单元类型·
    novel-outline-planner.md ⑤ 节"✅必产"字段，同 causal_connector_scanner 消费的字段体系；
    旧版 unit_type=scene/sequel 已确认是同一份 agent 契约里"新产物不依赖它完成核心合同"的
    探针字段·基本不会被填·2026-07-13 从 unit_type 迁到 scene_type）
  · 计算 max consecutive scene_run（最长连续 proactive_scene 不被 reactive_sequel 打断）
  · 题材门槛：爽文 N=5（容忍长 scene 链）· 严肃 N=3（要 sequel 多）

【两 advisory】
  · SEQUEL_DROUGHT — max_scene_run > N（按题材）
  · SCENE_TYPE_MISSING — scene_type 填充率 < 0.3（占位 missing 不报硬错）

【北极星】②④⑤ advisory shadow · 绝不 hard_gate
  作者档 author_sequel_baseline.max_scene_run = N → 让位

用法: python sequel_drought_advisory.py --project <项目根> [--cluster <cluster_id>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup as cl  # noqa: E402

ISSUE_CODE_DROUGHT = "SEQUEL_DROUGHT"
ISSUE_CODE_MISSING = "SCENE_TYPE_MISSING"

DEFAULT_MAX_RUN_WEBNOVEL = 5
DEFAULT_MAX_RUN_LITERARY = 3
DEFAULT_FILLED_LOW = 0.30

_GENRE_WEBNOVEL = {"爽文", "web_novel", "起点", "qidian", "tomato", "番茄"}
_GENRE_LITERARY = {"严肃", "literary", "纯文学"}


def _mode() -> str:
    m = (os.environ.get("SEQUEL_DROUGHT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _iter_cluster_storyboards(project_root, cluster_id=None):
    """按 cluster_id 精确匹配（cluster_lookup.normalize_cluster_id）定位目标 cluster 的
    scene_storyboard；cluster_id 省略=遍历项目内所有非空 storyboard 的 cluster。"""
    data = _load_json(Path(project_root) / "_数据库" / "事件簇.json", {}) or {}
    target = cl.normalize_cluster_id(cluster_id) if cluster_id else None
    for c in data.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        cid = cl.normalize_cluster_id(c.get("cluster_id"))
        if target and cid != target:
            continue
        sb = c.get("scene_storyboard")
        if isinstance(sb, list) and sb:
            yield cid, sb


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
        st = sc.get("scene_type") if isinstance(sc, dict) else None
        if st == "proactive_scene" or st is None:
            run += 1  # 未标也算 scene 干旱（保守：填了 reactive_sequel 才打断）
            best = max(best, run)
        elif st == "reactive_sequel":
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
    typed = sum(1 for s in storyboard if isinstance(s, dict)
                and s.get("scene_type") in ("proactive_scene", "reactive_sequel"))
    filled_share = round(typed / total, 3) if total else 0.0
    n_scene = sum(1 for s in storyboard if isinstance(s, dict) and s.get("scene_type") == "proactive_scene")
    n_sequel = sum(1 for s in storyboard if isinstance(s, dict) and s.get("scene_type") == "reactive_sequel")
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
        "scene_type_filled": typed,
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
                      "msg": f"scene_type 填充率={filled_share} < {DEFAULT_FILLED_LOW}·outline 缺 Swain 设计"})
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


def scan(project_root, cluster_id=None) -> dict:
    """project/cluster 解析层（仿 causal_connector_scanner.scan 分层）：按 cluster_id 精确定位
    目标 cluster 的 scene_storyboard，调用 advise() 纯函数分析，聚合结果并按 cluster_id 打标签。
    advise() 内部已按 _mode() 自行决定是否写入 violations（shadow 恒空）·本层不重复判断。"""
    mode = _mode()
    out = {"scanner": "sequel_drought_advisory", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    if not project_root or not Path(project_root).exists():
        out["note"] = "无项目根·跳过"
        return out
    all_violations, per_cluster = [], []
    for cid, storyboard in _iter_cluster_storyboards(project_root, cluster_id):
        try:
            rep = advise(storyboard, str(project_root))
        except Exception as e:
            per_cluster.append({"cluster_id": cid, "error": str(e)})
            continue
        for v in rep.get("violations", []):
            all_violations.append({**v, "cluster_id": cid})
        per_cluster.append({"cluster_id": cid, "scenes_total": rep.get("scenes_total")})
    out["per_cluster"] = per_cluster
    if not per_cluster:
        out["note"] = "无标注 scene_storyboard 或未命中目标 cluster"
    out["violations"] = all_violations
    if all_violations:
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = "·".join(v.get("message", "") for v in all_violations)
    out["violations_count"] = len(all_violations)
    return out


def main():
    ap = argparse.ArgumentParser(description="Swain Scene/Sequel drought advisory (shadow)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--cluster", default=None, help="cluster_id（省略=扫所有标注 storyboard 的 cluster）")
    args = ap.parse_args()
    rep = scan(args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
