#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""active_character_wm_load_scanner.py — Cowan WM 3-5 槽位活跃角色(R18 W7 Batch-U·P2)

【缺口·2026-06-21·Cowan 2001/2024 magical number 4±1 + Miller 7±2 +
arxiv 2604.* WM narrative 2026】
工作记忆(Working Memory)在 Cowan 实证下保持容量为 3-5 项(中位 4)。
单场景同时活跃角色超 5 → 读者认知超载·读得头晕。本 scanner 算每场景
活跃角色集合大小·>5 报 COGNITIVE_OVERLOAD advisory。

【与既有 scanner 显式去重】
  - R9 cast_economy(introduce_burst·配角经济·每章新登场角色密度)是工艺账本
    本 scanner = 单场景共时活跃数(WM 心理学)·正交
  - group_dialogue_balance(对话发言均衡)·正交不同维度

【活跃角色判定】
  - 场景按 \n\n 或显式『# scene_*』标记切分
  - 从 _数据库/角色池.json 读 emerged characters name + aliases
  - 场景内首次出现的命名角色 ∈ 活跃集合
  - 兜底：若无角色池·按场景内连续 4 字非地点中文短串聚类(粗略)

【北极星⑤】顾问非法官·全 advisory·env ACTIVE_CHARACTER_WM_MODE
  COGNITIVE_OVERLOAD 绝不 hard_gate。作者档 quantitative.wm_load_tolerance
  可旁路(适配群像题材如水浒/红楼)。

用法: python active_character_wm_load_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "COGNITIVE_OVERLOAD"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

DEFAULT_WM_THRESHOLD = 5     # Cowan 上限
MIN_CJK = 400
MIN_SCENE_CJK = 200


def _mode() -> str:
    m = (os.environ.get("ACTIVE_CHARACTER_WM_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_characters(project_root):
    """从 _数据库/角色池.json 读 core/emerged 角色名 + aliases。返回 set。"""
    names = set()
    if not project_root:
        return names
    p = Path(project_root) / "_数据库" / "角色池.json"
    if not p.exists():
        return names
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return names
    # 🔴 2026-06-28 角色池schema统一canonical：只读 core/emerged（不兼容·删多 key 兜底）
    sources = []
    for k in ("core", "emerged"):
        v = data.get(k)
        if isinstance(v, list):
            sources.extend(v)
    for entry in sources:
        if isinstance(entry, dict):
            for fk in ("name", "姓名", "id"):
                n = entry.get(fk)
                if isinstance(n, str) and len(n) >= 2:
                    names.add(n)
            aliases = entry.get("aliases") or entry.get("别名") or []
            if isinstance(aliases, list):
                for a in aliases:
                    if isinstance(a, str) and len(a) >= 2:
                        names.add(a)
        elif isinstance(entry, str) and len(entry) >= 2:
            names.add(entry)
    return names


def _load_tolerance(project_root):
    """读作者档 quantitative.wm_load_tolerance(int)·缺省 5。"""
    if not project_root:
        return DEFAULT_WM_THRESHOLD
    for fname in ("作者风格_FINAL.json", "作者风格.json"):
        p = Path(project_root) / "_数据库" / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        q = data.get("quantitative") or {}
        v = q.get("wm_load_tolerance")
        if isinstance(v, int) and 3 <= v <= 12:
            return v
    return DEFAULT_WM_THRESHOLD


def _split_scenes(text: str):
    """按空行段聚合·≥200 CJK 视作一场景。简化：用 \n\n\n 或字数阈值。"""
    # 尝试显式 scene marker
    if re.search(r"^#+\s*scene", text, re.M | re.I):
        return [s.strip() for s in re.split(r"^#+\s*scene[^\n]*\n",
                                             text, flags=re.M | re.I)
                if s.strip()]
    # 按 3 个换行切·兜底按 1500 CJK 切
    parts = [p.strip() for p in re.split(r"\n{3,}", text) if p.strip()]
    if len(parts) <= 1:
        # 按 CJK 数硬切
        chunks = []
        buf = []
        cur = 0
        for line in text.split("\n"):
            buf.append(line)
            cur += _cjk_count(line)
            if cur >= 1500:
                chunks.append("\n".join(buf))
                buf = []
                cur = 0
        if buf:
            chunks.append("\n".join(buf))
        return chunks
    return parts


def _scene_active_chars(scene: str, name_set):
    found = set()
    for n in name_set:
        if n in scene:
            found.add(n)
    return found


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "active_character_wm_load", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    names = _load_characters(project_root)
    threshold = _load_tolerance(project_root)
    out["author_threshold"] = threshold
    if not names:
        out["note"] = "无角色池·skip(简化版不做兜底 NER 防误报)"
        return out

    scenes = _split_scenes(text)
    scene_reports = []
    overload_scenes = []
    for idx, sc in enumerate(scenes):
        if _cjk_count(sc) < MIN_SCENE_CJK:
            continue
        actives = _scene_active_chars(sc, names)
        scene_reports.append({"scene_idx": idx, "active_count": len(actives),
                              "actives": sorted(actives)})
        if len(actives) > threshold:
            overload_scenes.append(idx)
    out["scenes"] = scene_reports
    out["metrics"] = {
        "scenes_examined": len(scene_reports),
        "overload_scenes": len(overload_scenes),
        "threshold": threshold,
    }

    if overload_scenes:
        msg = (f"{len(overload_scenes)} 个场景活跃角色超 Cowan 上限 {threshold}·"
               f"建议拆场景或先消解部分角色·idx={overload_scenes[:6]}")
        if mode == "active":
            out["violations"].append({
                "kind": "cognitive_overload",
                "severity": "minor", "code": ISSUE_CODE,
                "message": msg, "metrics": out["metrics"],
                "_doc": "Cowan WM 3-5 槽位·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] active_character_wm: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Cowan WM 活跃角色限制·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
