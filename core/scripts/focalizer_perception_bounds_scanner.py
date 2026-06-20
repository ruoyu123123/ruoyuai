#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""focalizer_perception_bounds_scanner.py — 聚焦人感知边界违例检测（advisory · cluster · 2026-06-20）

【缺口】R7 W2·Bal/Rimmon-Kenan focalization theory + FocalLens 2026：第三人称限知 / 第一人称
叙述里，聚焦人(focalizer)的【感知边界】是写作工艺核心约束——他不能：
  ① 看见自己看不见的（自体不可见词：自己的眼神/自己的脸色/自己的背影/自己的瞳孔）
  ② 描写他不在场的事件（空间不在场：他未参与的远方场景细节）
  ③ 进入他人内心（他人内心：未经表征推断的他人「想/认为/暗忖」）
弱模型常无意识跨这三道边界 → 焦点漂移 / head-hopping / 全知化。R6 pov_consistency_scanner 只
查【scene 内 POV 主导者一致性 + scene 间切换合法性】，**不查感知边界本身的越界**——本 scanner
补『同 POV 内合法但越权』维度，与 R6 显式去重。

【做法 · 确定性纯规则三规则（零 LLM）】：
  规则 ① 自体不可见：检测「自己的+面部/视觉/外观自体词」模式（自己的眼神/脸色/瞳孔/背影/侧脸…）
       —— focalizer 看不见自己的脸·镜像/水面例外不可机械识别故仅 advisory
  规则 ② 他人内心：检测「他人名/他 + 想/认为/觉得/暗忖/盘算/在心里」段（POV 非主角的他人内心）·
       与 R6 pov_consistency 去重原则：R6 看 dominant_pov 信号占比·本 scanner 只看跨界的
       【非聚焦人的内心动词】per_1k
  规则 ③ 空间不在场：暂以「同时…在远处/与此同时…在 N 里外/此刻…千里之外」式标志触发·标记
       advisory 提示空间分裂（无法机械精确判·只识别明显语言标志）

任一规则触发 → advisory（per_1k 或 hit_count）。

【与 R6 pov_consistency 去重】：R6 = scene-level dominant POV 一致性·本 scanner = focalizer
内的感知边界三类越界·两者正交（R6 查【谁主导本 scene】·本 scanner 查【主导者在自己 scene 里有
没有越权】）·共存可叠加。

【北极星⑤ 顾问非法官】感知边界是创作选择（梦境/镜面/全知叙述者刻意越界）·writer 有理由可豁免
  → 永远 advisory，code FOCALIZER_PERCEPTION_OUT_OF_BOUNDS **绝不进 audit_hub.HARD_GATE_CODES**。
  env FOCALIZER_PERCEPTION_BOUNDS_MODE: off / shadow(默认·只记不判·零回归) / active。
  🔬 阈值占位保守（宁可漏报）·待金标准校准。

用法：python focalizer_perception_bounds_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "FOCALIZER_PERCEPTION_OUT_OF_BOUNDS"   # ⚠️ advisory 专用

# 规则 ①：自体不可见词（focalizer 看不见自己的脸 / 眼神 / 背影）
# 「自己的 + 面部/视觉自体词」+ 单独「他/她」 + 面部自体的视觉描述也可能命中（保守，先收）
SELF_INVISIBLE = re.compile(
    r"自己的(眼神|眼眸|眼瞳|脸色|面色|表情|神色|背影|侧脸|嘴角|眉头|瞳孔|后背|后脑勺)"
)

# 规则 ②：他人内心动词（非聚焦人的「想/认为/觉得/暗忖/盘算/在心里/暗道」）
# 注意：focalizer 本人的内心动词合法 → 检测「他人专名 / 他/她 + 内心动词」段
# 简化：仅收『他名 + 心想/暗道/暗忖/盘算/觉得/认为/在心里』五字内邻接模式
_OTHER_NAME_HINT = r"[一-鿿]{2,4}"  # 占位他人名词位
OTHERS_INNER = re.compile(
    rf"({_OTHER_NAME_HINT})(心想|心说|心中暗道|暗道|暗忖|寻思|盘算|在心里(?:想|说)|心里想|心中想)"
)

# 规则 ③：空间不在场（明显语言标志触发）
SPATIAL_ABSENCE = re.compile(
    r"(与此同时[，,].{0,15}(?:在|于)?(?:远处|另一边|另一头|千里之外|城外|山外|宫外|府外)|"
    r"同一时刻[，,].{0,15}(?:远处|另一边|千里之外)|"
    r"此刻[，,].{0,15}(?:千里之外|远在|遥远的))"
)

# 单向阈值（保守占位·待金标准校准）
SELF_INVISIBLE_FLOOR_PER_1K = 0.5   # 自体不可见 per_1k 超此 = 频繁越界
OTHERS_INNER_FLOOR_PER_1K = 1.0     # 他人内心 per_1k 超此 = head-hopping 倾向
SPATIAL_ABSENCE_HIT_FLOOR = 2       # 空间不在场命中 2+ 处 = 焦点空间漂移

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("FOCALIZER_PERCEPTION_BOUNDS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_focalizer_names(project_root):
    """读人物卡找主角名（聚焦人默认主角）+ 所有角色名集合（用于他人内心识别）。"""
    if not project_root:
        return None, set()
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return None, set()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, set()
    chars = obj.get("characters", []) if isinstance(obj, dict) else []
    all_names = set()
    protag = None
    for c in chars:
        if not isinstance(c, dict):
            continue
        n = c.get("name", "")
        if n:
            all_names.add(n)
            for a in (c.get("aliases") or []):
                if a:
                    all_names.add(a)
        if c.get("role") == "主角" and not protag:
            protag = n
    return protag, all_names


def detect_violations(text: str, focalizer=None, all_names=None):
    """三规则各产 hits 列表。返回 {self_invisible, others_inner, spatial_absence}。"""
    text = _strip_changes(text)
    self_inv = [{"term": m.group(0), "pos": m.start()} for m in SELF_INVISIBLE.finditer(text)]

    others_inner_hits = []
    for m in OTHERS_INNER.finditer(text):
        name = m.group(1)
        verb = m.group(2)
        # 滤掉聚焦人自己 + 滤掉常见非名词（『他』『她』等代词其实指代 focalizer 可能合法 → 保守过滤掉）
        if focalizer and name == focalizer:
            continue
        if name in ("他想", "她想", "心中", "心里", "暗中"):
            continue
        # 只收已注册角色名（防误收『时候想』『地方想』等假阳性）
        if all_names and name not in all_names:
            continue
        others_inner_hits.append({"name": name, "verb": verb, "pos": m.start()})

    spatial_hits = [{"trigger": m.group(0)[:30], "pos": m.start()}
                    for m in SPATIAL_ABSENCE.finditer(text)]
    return {
        "self_invisible": self_inv,
        "others_inner": others_inner_hits,
        "spatial_absence": spatial_hits,
    }


def scan(draft_path, project_root=None) -> dict:
    """聚焦人感知边界三规则。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "focalizer_perception_bounds", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    focalizer, all_names = _load_focalizer_names(project_root)
    out["focalizer"] = focalizer
    hits = detect_violations(draft, focalizer=focalizer, all_names=all_names)

    si_per_1k = round(len(hits["self_invisible"]) / (cjk / 1000.0), 2)
    oi_per_1k = round(len(hits["others_inner"]) / (cjk / 1000.0), 2)
    sa_count = len(hits["spatial_absence"])
    out["self_invisible_per_1k"] = si_per_1k
    out["others_inner_per_1k"] = oi_per_1k
    out["spatial_absence_count"] = sa_count
    out["self_invisible_samples"] = [h["term"] for h in hits["self_invisible"][:5]]
    out["others_inner_samples"] = [f"{h['name']}{h['verb']}" for h in hits["others_inner"][:5]]
    out["spatial_absence_samples"] = [h["trigger"] for h in hits["spatial_absence"][:3]]

    msgs = []
    if si_per_1k > SELF_INVISIBLE_FLOOR_PER_1K:
        msgs.append(f"自体不可见越界（{si_per_1k}/千字 > {SELF_INVISIBLE_FLOOR_PER_1K}·聚焦人看不见自己的脸/眼/背影）")
    if oi_per_1k > OTHERS_INNER_FLOOR_PER_1K:
        msgs.append(f"他人内心越界（{oi_per_1k}/千字 > {OTHERS_INNER_FLOOR_PER_1K}·限知 POV 不能进他人内心）")
    if sa_count >= SPATIAL_ABSENCE_HIT_FLOOR:
        msgs.append(f"空间不在场越界（{sa_count} 处 ≥ {SPATIAL_ABSENCE_HIT_FLOOR}·聚焦人未在场的远方场景）")
    msg = "·".join(msgs) if msgs else None

    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "focalizer_perception_out_of_bounds", "severity": "minor",
                "message": msg, "focalizer": focalizer,
                "self_invisible_per_1k": si_per_1k,
                "others_inner_per_1k": oi_per_1k,
                "spatial_absence_count": sa_count,
                "_doc": "感知边界三规则·梦境/镜面/全知刻意越界可豁免·与 R6 pov_consistency 正交·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] focalizer_perception_bounds: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="聚焦人感知边界违例检测(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读人物卡定 focalizer + 角色名集")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
