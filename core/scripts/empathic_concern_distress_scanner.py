#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""empathic_concern_distress_scanner.py — EC vs PD 二相平衡(advisory · cluster · 2026-06-20 R8 W4 Batch-I)

【缺口】L27 联网调研(Nature Sci Rep 2025 EC/PD + Cognition & Emotion 2020 + Keen Theory of
Narrative Empathy): 共情研究区分 Empathic Concern(EC·对他人苦难的关切+采取行动)与
Personal Distress(PD·自我中心的恐慌/退缩/无能为力)·LLM 在描写苦难/悲剧/牺牲/折磨/绝望
场景时常 PD 过载(主角/旁观者只剩颤抖/瘫坐/不敢看)缺 EC(伸手/上前/守护/尊严保留),
=苦难写成自怜剧本而非荷马式英雄抗争。此前**全系统零检测**。

【做法 · 确定性纯规则正则】:
  1. 按段落空行+场景标志拆场景。
  2. 仅 scene 标签 ∈ {suffering, grief, sacrifice, torment, desperation} 触发
     (无 manifest tag 时启发式:检测苦难关键词 受伤/痛苦/悲伤/牺牲/折磨/绝望/死亡/挣扎/濒死/血)
  3. 在触发场景内分别计 EC / PD 信号词数:
     - EC: 伸手|上前|搀扶|守护|安慰|挡在前面|背起|抱起|相信|不会放弃|绝不抛弃|
            眼神坚定|沉声道|为了|护住|保护
     - PD: 颤抖|发抖|不敢看|不敢面对|后退|退缩|崩溃|无能为力|瘫坐|瘫倒|
            僵住|呆住|心如死灰|麻木|不知所措|捂住眼睛|跪倒|窒息
  4. ec_pd_ratio = EC / (EC + PD)
  5. ratio < 0.4 → advisory("PD 过载·建议保 agency 残留 + 旁观者关切动作")

【北极星② / ⑤ 顾问非法官】advisory · 与 R7 Nummenmaa body map 协同(独立维度) ·
  code EMPATHIC_CONCERN_DISTRESS_IMBALANCE 绝不进 audit_hub.HARD_GATE_CODES。
  env EMPATHIC_CONCERN_MODE: off / shadow(默认) / active。

用法: python empathic_concern_distress_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "EMPATHIC_CONCERN_DISTRESS_IMBALANCE"

SUFFERING_TRIGGER = re.compile(
    r"(受伤|重伤|痛苦|哀伤|悲伤|牺牲|折磨|绝望|死亡|垂死|濒死|挣扎|"
    r"鲜血|流血|惨叫|哀嚎|呜咽|哭泣|抽泣|送别|临终)"
)

EC_SIGNALS = re.compile(
    r"(伸出手|伸手|上前|搀扶|扶起|守护|安慰|挡在前面|挡在身前|背起|抱起|抱住|"
    r"相信|不会放弃|绝不抛弃|沉声道|沉声说|为了[^,，。]{1,8}(必|要|不能|绝不)|"
    r"护住|护着|保护|留下|不会让你|你不会|我们一起|放心|交给我|不要怕)"
)

PD_SIGNALS = re.compile(
    r"(颤抖|发抖|战栗|哆嗦|不敢看|不敢面对|不敢直视|后退|退缩|往后退|"
    r"崩溃|无能为力|无力地|瘫坐|瘫倒|跌坐|跪倒|"
    r"僵住|呆住|愣住|心如死灰|麻木|不知所措|捂住眼睛|捂住脸|捂着嘴|"
    r"窒息|喘不过气|双腿一软|腿一软)"
)

SCENE_BREAKS = re.compile(
    r"(另一边|与此同时|片刻后|不多时|半晌后|次日|翌日|清晨|入夜|当晚)"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 苦难标签集合(matches L27 spec)
SUFFERING_TAGS = {"suffering", "grief", "sacrifice", "torment", "desperation"}


def _mode() -> str:
    m = (os.environ.get("EMPATHIC_CONCERN_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_scene_tags(project_root, ch_id=None):
    """读 manifest.scene_storyboard[].tags 集合。无 → None。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    # 优先 .manifest/ch_XXX.json
    if ch_id:
        mp = db / ".manifest" / f"ch_{ch_id:03d}.json"
        if mp.exists():
            try:
                obj = json.loads(mp.read_text(encoding="utf-8"))
                tags = set()
                for sc in (obj.get("scene_storyboard") or []):
                    for t in (sc.get("tags") or []):
                        tags.add(str(t).strip().lower())
                return tags or None
            except (json.JSONDecodeError, OSError):
                pass
    return None


def split_scenes(text: str) -> list:
    blocks = re.split(r"\n\s*\n", text)
    scenes = []
    for blk in blocks:
        blk = blk.strip()
        if not blk:
            continue
        m = SCENE_BREAKS.search(blk)
        if m and m.start() > 80:
            scenes.append(blk[:m.start()].strip())
            scenes.append(blk[m.start():].strip())
        else:
            scenes.append(blk)
    return [s for s in scenes if _cjk_count(s) >= 80]


def is_suffering_scene(scene_text: str, manifest_tags=None) -> bool:
    """场景是否触发(manifest tag 优先·否则启发式)。"""
    if manifest_tags and (manifest_tags & SUFFERING_TAGS):
        # manifest 标了苦难 → 整 cluster 触发
        return True
    # 启发式:场景内 suffering trigger 词 ≥3
    return len(SUFFERING_TRIGGER.findall(scene_text)) >= 3


def compute_ec_pd(scene_text: str) -> tuple:
    ec = len(EC_SIGNALS.findall(scene_text))
    pd = len(PD_SIGNALS.findall(scene_text))
    return ec, pd


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "empathic_concern_distress", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    manifest_tags = _read_scene_tags(project_root)
    out["manifest_tags"] = sorted(manifest_tags) if manifest_tags else None
    scenes = split_scenes(draft)
    out["scene_count"] = len(scenes)

    per_scene = []
    triggered = []
    for i, sc in enumerate(scenes):
        if not is_suffering_scene(sc, manifest_tags):
            per_scene.append({"i": i, "trigger": False})
            continue
        ec, pd = compute_ec_pd(sc)
        total = ec + pd
        ratio = round(ec / total, 3) if total > 0 else None
        per_scene.append({"i": i, "trigger": True, "ec": ec, "pd": pd, "ratio": ratio})
        if ratio is not None:
            triggered.append({"i": i, "ec": ec, "pd": pd, "ratio": ratio})

    out["triggered_scenes"] = len(triggered)
    if not triggered:
        out["note"] = "无苦难/悲剧 trigger 场景·跳过"
        return out

    low_ratio = [t for t in triggered if t["ratio"] is not None and t["ratio"] < 0.4]
    out["per_scene"] = per_scene
    out["low_ratio_scenes"] = len(low_ratio)

    msg = None
    if low_ratio:
        worst = min(low_ratio, key=lambda x: x["ratio"])
        msg = (f"{len(low_ratio)}/{len(triggered)} 个苦难场景 EC/PD 比 < 0.4 "
               f"(最低 {worst['ratio']}·ec={worst['ec']}/pd={worst['pd']})·"
               f"PD 过载=自怜风险·建议加 agency 残留(主角仍挣扎/保留尊严)+"
               f"旁观者关切动作(伸手/守护/挡在前面)")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "empathic_concern_distress", "severity": "minor",
                "message": msg, "low_ratio_scenes": low_ratio[:5],
                "triggered_scenes": len(triggered),
                "_doc": "EC vs PD 二相平衡·苦难场景工艺·与 Nummenmaa body map 协同·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] empathic_concern_distress: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="EC vs PD 二相平衡(advisory · 苦难场景)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 manifest.scene_storyboard.tags")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
