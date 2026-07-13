#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""red_herring_recall_scanner.py — 红鲱鱼揭底召回 scanner（advisory · cluster · 2026-06-20 R9 W5 Batch-N P1）

【缺口】R9 W5 联网调研对偶 R2 setup：好作者在 reveal/twist/climax_reveal 时不仅 payoff
真伏笔·还会显式否决/拆穿 red herring（误导项）让读者『啊原来那不是』。LLM 默认产
干净 reveal 只揭谜底·留下大量散漫红鲱鱼 dangling = 读者觉得『被白白引诱了一程』。
此前 foreshadowing_handoff 查 promise→payoff·**无 scanner 查 red herring 在揭底节点
是否被显式 debunk**。

【做法 · 确定性零依赖（依赖 _数据库/伏笔表.json 或 线索.json）】：
  1. 触发门控：仅在 manifest.beat_signal/scene_storyboard.beat_tags 含 reveal / twist /
     climax_reveal 时跑。否则 skip（reveal beat 是触发点）。
  2. 读 _数据库/伏笔表.json 的 red_herrings 列表（item 字段含 id / surface_text /
     setup_cluster / debunked_cluster）。无字段或空 → skip（北极星②：作者未声明就别擅判）。
  3. 收集本 cluster『未被 debunked』的 red_herrings（debunked_cluster 为空或 > current_cluster）。
  4. 在 cluster 草稿正文里扫 surface_text 是否被『显式提及+否决』
     （surface_text 出现 + 同段附近 NEGATION_MARKERS 之一 = debunked）。
  5. dangling = surface_text 完全未提及 / 提及但 0 否决标志。
  6. dangling 数 ≥ 1 → advisory RED_HERRING_DANGLING（建议 reveal 时显式拆穿误导）。

【北极星② / ⑤ 顾问非法官】red_herring 是作者档显式声明产物·设计感强的故事不要求 100% 召回
  ·全 advisory，code RED_HERRING_DANGLING **绝不进 audit_hub.HARD_GATE_CODES**。
  env RED_HERRING_RECALL_MODE: off / shadow(默认) / active。

用法：python red_herring_recall_scanner.py <draft_path> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE = "RED_HERRING_DANGLING"  # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# Reveal beat 触发关键词（manifest.beat_signal/scene_storyboard.beat_tags）
REVEAL_BEAT_TAGS = {"reveal", "twist", "climax_reveal", "midpoint_reveal",
                    "final_reveal", "denouement", "揭底", "反转", "真相"}

# 否决/debunk 标志词（red herring 被拆穿）
NEGATION_MARKERS = re.compile(
    r"(并非|不是|根本不是|从来不是|压根不是|其实|实际上|真相是|"
    r"误会|搞错了|认错了|想错了|猜错了|不对|错了|"
    r"原来不是|这才不是|这并不是|和.{0,5}无关|与.{0,5}无关)")

# 局部窗口：surface 出现位置 ±60 字内出现 NEGATION_MARKERS 视为 debunked
LOCAL_WINDOW = 60


def _mode() -> str:
    m = (os.environ.get("RED_HERRING_RECALL_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(path: Path):
    return load_json(path)


def _read_manifest(manifest_path):
    if not manifest_path:
        return {}
    obj = _read_json(Path(manifest_path))
    return obj if isinstance(obj, dict) else {}


def _is_reveal_beat(manifest: dict) -> bool:
    """检查 manifest 是否进入 reveal beat 触发态。"""
    if not isinstance(manifest, dict):
        return False
    # 1. 显式 beat_signal
    sig = manifest.get("beat_signal") or manifest.get("current_beat")
    if isinstance(sig, str) and any(t in sig.lower() for t in REVEAL_BEAT_TAGS):
        return True
    if isinstance(sig, list):
        for s in sig:
            if isinstance(s, str) and any(t in s.lower() for t in REVEAL_BEAT_TAGS):
                return True
    # 2. scene_storyboard 含 beat_tags
    sb = manifest.get("scene_storyboard")
    if isinstance(sb, list):
        for sc in sb:
            if not isinstance(sc, dict):
                continue
            tags = sc.get("beat_tags") or sc.get("beats") or []
            if isinstance(tags, str):
                tags = [tags]
            for t in tags or []:
                if isinstance(t, str) and any(b in t.lower() for b in REVEAL_BEAT_TAGS):
                    return True
    # 3. flag 字段
    if manifest.get("is_reveal_beat") is True:
        return True
    if manifest.get("is_climax_reveal") is True:
        return True
    return False


def _read_red_herrings(project_root) -> list:
    """读 _数据库/伏笔表.json 或 线索.json 的 red_herrings 列表。"""
    if not project_root:
        return []
    db = Path(project_root) / "_数据库"
    for fname in ("伏笔表.json", "伏笔.json", "线索.json"):
        p = db / fname
        if not p.exists():
            continue
        obj = _read_json(p)
        if not isinstance(obj, dict):
            continue
        rh = obj.get("red_herrings")
        if isinstance(rh, list) and rh:
            return rh
    return []


def _cluster_id_num(cluster_id) -> int:
    if not cluster_id:
        return 0
    m = re.search(r"(\d+)", str(cluster_id))
    return int(m.group(1)) if m else 0


def _is_unresolved(rh: dict, current_cluster_id) -> bool:
    """判断 red_herring 在本 cluster 仍未 debunked。"""
    if not isinstance(rh, dict):
        return False
    debunked = rh.get("debunked_cluster")
    if not debunked:
        return True
    # 已在更早的 cluster debunked → 不必再检查
    cur = _cluster_id_num(current_cluster_id)
    deb = _cluster_id_num(debunked)
    return not (deb and cur and deb < cur)


def _surface_check(text: str, surface: str) -> dict:
    """在正文里查 surface_text。返回 {mentioned, debunked, mention_count}。"""
    if not surface:
        return {"mentioned": False, "debunked": False, "mention_count": 0}
    mentions = [m.start() for m in re.finditer(re.escape(surface), text)]
    if not mentions:
        return {"mentioned": False, "debunked": False, "mention_count": 0}
    debunked = False
    for pos in mentions:
        window = text[max(0, pos - LOCAL_WINDOW): pos + len(surface) + LOCAL_WINDOW]
        if NEGATION_MARKERS.search(window):
            debunked = True
            break
    return {"mentioned": True, "debunked": debunked, "mention_count": len(mentions)}


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "red_herring_recall", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out

    manifest = _read_manifest(manifest_path)
    if not _is_reveal_beat(manifest):
        out["note"] = "非 reveal/twist beat·跳过(red herring 召回只在揭底节点检)"
        return out

    red_herrings = _read_red_herrings(project_root)
    if not red_herrings:
        out["note"] = "无 red_herrings 声明·跳过(北极星②:作者未声明就别擅判)"
        return out

    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out

    cluster_id = (manifest.get("cluster_id") or manifest.get("cluster_key") or "")
    out["cluster_id"] = cluster_id

    dangling, debunked_now, total_unresolved = [], [], 0
    for rh in red_herrings:
        if not _is_unresolved(rh, cluster_id):
            continue
        total_unresolved += 1
        surface = (rh.get("surface_text") if isinstance(rh, dict) else None) or ""
        rh_id = (rh.get("id") if isinstance(rh, dict) else None) or surface[:20]
        info = _surface_check(draft, surface)
        if info["debunked"]:
            debunked_now.append({"id": rh_id, "surface_text": surface,
                                 "mention_count": info["mention_count"]})
        else:
            dangling.append({
                "id": rh_id, "surface_text": surface,
                "mentioned": info["mentioned"],
                "mention_count": info["mention_count"],
                "setup_cluster": rh.get("setup_cluster") if isinstance(rh, dict) else None,
            })

    out["red_herrings_unresolved"] = total_unresolved
    out["debunked_in_cluster"] = debunked_now
    out["dangling_count"] = len(dangling)
    out["dangling_sample"] = dangling[:5]

    if dangling:
        names = "、".join(d.get("surface_text", d.get("id", ""))[:12]
                          for d in dangling[:3])
        msg = (f"Reveal 节点 {len(dangling)} 个 red_herring 未显式拆穿（{names} 等）·"
               f"建议在揭底段显式否决误导(如『并非XX』/『其实不是』)·"
               f"避免读者觉得被白引诱一程")
        violation = {
            "code": ISSUE_CODE, "kind": "red_herring_dangling",
            "severity": "minor", "message": msg,
            "dangling_count": len(dangling),
            "dangling": dangling[:10],
            "_doc": "advisory·设计感强故事可不要求 100% 召回·绝不 hard_gate",
        }
        if mode == "active":
            out["violations"].append(violation)
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] red_herring_recall: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Red Herring Recall-at-Reveal(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
