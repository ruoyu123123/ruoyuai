#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prose_180_axis_scanner.py — 空间轴 180°一致性 · R24 W12 Batch-LL · P2

【缺口 · 多模态空间一致性 180° 反向 axis flip】
影视/分镜术语 180°-rule：相邻 scene 跨过假想轴会让观众迷失方向。
散文写作长 cluster 内若 scene_A 写「他在 X 北边·面朝南」、scene_B 紧接
「他在 X 南边·面朝北」却无 transition_cue 解释空间切换，读者画面飞镜。
LLM 长 freestyle 写作常忽视此·画面在脑海中翻转。
本 scanner 抽相邻 scene 空间关系三元组（actor / relation / landmark），
若主体相同+关系反向+无 transition_cue → PROSE_AXIS_FLIP advisory。

【做法 · 确定性 · 占位规则 fallback（_placeholder=true）】
  · 段落空行 + 场景切换标志（『*』『---』『◇』『◆』）拆 scene
  · 每 scene 提取空间关系三元组（占位规则·真版 LLM 抽取）：
    - 方向词典：东/南/西/北/上/下/左/右/前/后
    - 关系词典：面朝/背对/面对/在...的(东南西北)边/侧
    - actor = 段首 / 'X 在 Y' / 双引号外 4 字角色 token
  · 同/邻 scene 比对：subject 相同 + relation 反向（north↔south / left↔right）
    且 transition_cue 词典（转过身/走到/绕到/回头/换个角度/此时）窗口缺失
  · → PROSE_AXIS_FLIP advisory

【写入 spatial_axis.json·供 cross-cluster aggregator 复用】
  · _数据库/.cross_chapter_scan/spatial_axis.json append-only
  · 每 cluster 一段 {cluster_id, scenes[]: [{idx, triples[], transitions[]}]}

【三 advisory · 全 advisory shadow】
  · PROSE_AXIS_FLIP                — 相邻 scene 反向无 transition_cue
  · PROSE_AXIS_TRIPLE_EMPTY        — 抽不到任何空间三元组（info）
  · PROSE_AXIS_OK                  — 无 flip（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  PROSE_AXIS_FLIP 绝不进 audit_hub.HARD_GATE_CODES。

env PROSE_180_AXIS_MODE: off / shadow（默认） / active
用法: python prose_180_axis_scanner.py <draft> [--project <root>] [--cluster <key>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_FLIP = "PROSE_AXIS_FLIP"
ISSUE_CODE_TRIPLE_EMPTY = "PROSE_AXIS_TRIPLE_EMPTY"
ISSUE_CODE_OK = "PROSE_AXIS_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 方向词典 + 反向对（占位 _placeholder=true）
_DIRECTIONS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-LL·占位方向词典",
    "_words": ["东", "南", "西", "北", "上", "下", "左", "右", "前", "后"],
    "_opposites": {
        "东": "西", "西": "东", "南": "北", "北": "南",
        "上": "下", "下": "上", "左": "右", "右": "左",
        "前": "后", "后": "前",
    },
}
_RELATIONS = ["面朝", "面对", "背对", "朝向", "望向", "看向", "正对", "侧对"]
_TRANSITION_CUES = ["转过身", "转身", "回头", "绕到", "走到", "回身", "换个方向",
                    "换个角度", "调转", "侧身", "此时", "再看", "此刻"]
_SCENE_SPLITTERS = (r"\n\s*\*\s*\n", r"\n\s*---+\s*\n",
                    r"\n\s*◇+\s*\n", r"\n\s*◆+\s*\n")


def _mode() -> str:
    m = (os.environ.get("PROSE_180_AXIS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_scenes(text: str) -> list:
    """按场景切换标志拆 scene·标志缺失退化按 ≥3 连续空行拆。"""
    pieces = [text]
    for pat in _SCENE_SPLITTERS:
        new = []
        for p in pieces:
            new.extend(re.split(pat, p))
        pieces = new
    # 退化：双空行+段首切场暗示（凌晨/夜晚/三日后）
    if len(pieces) == 1:
        pieces = re.split(r"\n\n(?=(?:凌晨|清晨|傍晚|夜晚|次日|三日|半月|一月)?)", text)
    return [p.strip() for p in pieces if p.strip()]


def _extract_triples(scene_text: str) -> list:
    """从 scene 抽空间关系三元组 (actor, relation, direction|landmark)。

    占位规则识别：
      pattern1: <actor> + <relation> + <direction|landmark>
      pattern2: <actor> 在 <landmark> 的 <direction> 边/侧
    """
    triples = []
    # pattern1: actor + 面朝/背对 + 方向
    for rel in _RELATIONS:
        # 短 actor (2-4 CJK) + relation + 方向词
        pat = (r"([一-龥]{2,4})" + rel
               + r"([" + "".join(_DIRECTIONS["_words"]) + r"])")
        for m in re.finditer(pat, scene_text):
            triples.append({
                "actor": m.group(1),
                "relation": rel,
                "direction": m.group(2),
                "pattern": "p1",
            })
    # pattern2: <actor> 在 <landmark> 的 <direction>
    pat2 = (r"([一-龥]{2,4})在([一-龥]{1,6})的"
            + r"([" + "".join(_DIRECTIONS["_words"]) + r"])(?:边|侧|方|面)")
    for m in re.finditer(pat2, scene_text):
        triples.append({
            "actor": m.group(1),
            "relation": f"在...的{m.group(3)}",
            "direction": m.group(3),
            "landmark": m.group(2),
            "pattern": "p2",
        })
    return triples


def _has_transition_cue(prev_scene_tail: str, next_scene_head: str) -> bool:
    """transition_cue 窗口检测：上 scene 末 80 CJK + 下 scene 头 80 CJK"""
    window = prev_scene_tail[-80:] + " " + next_scene_head[:80]
    return any(cue in window for cue in _TRANSITION_CUES)


def _detect_flips(scenes: list) -> list:
    """相邻 scene 同 actor + 反向 direction + 无 transition_cue → flip"""
    flips = []
    for i in range(len(scenes) - 1):
        ta = _extract_triples(scenes[i])
        tb = _extract_triples(scenes[i + 1])
        if not ta or not tb:
            continue
        for x in ta:
            for y in tb:
                if x["actor"] != y["actor"]:
                    continue
                opp = _DIRECTIONS["_opposites"].get(x["direction"], "")
                if y["direction"] == opp:
                    cue = _has_transition_cue(scenes[i], scenes[i + 1])
                    if not cue:
                        flips.append({
                            "scene_idx_a": i,
                            "scene_idx_b": i + 1,
                            "actor": x["actor"],
                            "dir_a": x["direction"],
                            "dir_b": y["direction"],
                        })
    return flips


def _write_axis_ledger(project_root, cluster_id: str, scenes: list,
                       triples_per_scene: list, flips: list) -> str:
    """写 _数据库/.cross_chapter_scan/spatial_axis.json append-only。"""
    if not project_root or not cluster_id:
        return ""
    try:
        out_dir = Path(project_root) / "_数据库" / ".cross_chapter_scan"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "spatial_axis.json"
        data = {"_schema_version": "1.0", "_placeholder": True, "clusters": {}}
        if out_path.exists():
            try:
                data = json.loads(out_path.read_text(encoding="utf-8"))
                data.setdefault("clusters", {})
            except (OSError, json.JSONDecodeError):
                pass
        data["clusters"][cluster_id] = {
            "scene_count": len(scenes),
            "scenes": [
                {"idx": i, "triples": triples_per_scene[i]}
                for i in range(len(scenes))
            ],
            "flips": flips,
        }
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return str(out_path)
    except OSError:
        return ""


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "prose_180_axis_scanner", "schema_version": "1.0",
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    scenes = _split_scenes(text)
    triples_per_scene = [_extract_triples(s) for s in scenes]
    total_triples = sum(len(t) for t in triples_per_scene)
    flips = _detect_flips(scenes)

    out.update({
        "cjk": cjk,
        "scene_count": len(scenes),
        "triples_total": total_triples,
        "flips": flips,
        "flips_count": len(flips),
    })

    # 写 ledger（仅 active+project_root+cluster_id）
    if mode == "active" and project_root and cluster_id:
        ledger_path = _write_axis_ledger(
            project_root, cluster_id, scenes, triples_per_scene, flips)
        out["ledger_path"] = ledger_path

    flags = []
    if total_triples == 0:
        flags.append({
            "code": ISSUE_CODE_TRIPLE_EMPTY,
            "msg": "整 cluster 抽不到任何空间关系三元组·跳过 axis 一致性",
            "severity": "info",
        })
    elif flips:
        flags.append({
            "code": ISSUE_CODE_FLIP,
            "msg": (f"检出 {len(flips)} 处相邻 scene 同主体反向 axis flip"
                    f"·无 transition_cue·读者画面 180° 反转"),
            "severity": "minor",
        })
    else:
        flags.append({
            "code": ISSUE_CODE_OK,
            "msg": "无 axis flip",
            "severity": "info",
        })

    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "prose_180_axis_scanner",
                "severity": f.get("severity", "minor"),
                "code": f["code"], "message": f["msg"],
                "_doc": "R24 W12 Batch-LL·180° 空间轴·advisory·绝不 hard_gate"})
        out["verdict"] = ("FAIL_MINOR"
                          if any(v["severity"] == "minor" for v in out["violations"])
                          else "PASS")
        out["warning"] = "·".join(f["msg"] for f in flags if f["severity"] == "minor") or None
    elif mode == "shadow":
        minor = [f for f in flags if f["severity"] == "minor"]
        if minor:
            print("[SHADOW] prose_180_axis_scanner: "
                  + "·".join(f["msg"] for f in minor)
                  + " — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="180° 空间轴一致性 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None, help="cluster_id（写 ledger 用）")
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
