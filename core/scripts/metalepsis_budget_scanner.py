#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""metalepsis_budget_scanner.py — 元叙事越界预算(advisory · cluster · 2026-06-20 R8 W4 Batch-I)

【缺口】L25 联网调研(Pier Metalepsis LHN 2014 + MasterClass + 全知读者视角 + 马良系统流 marker):
系统流/元小说/无限流题材核心工艺『元叙事越界』(metalepsis = 叙述层/故事层之间的越界——
旁白对读者说话/系统提示打破第四面墙/角色感知到自己是故事)此前**全系统零检测**。
LLM 默认要么完全不写(爽文化退化)要么滥用(每段都【系统提示】插入)。

【做法 · 确定性纯规则正则(不依赖 LLM)】:
  1. 作者档/genre pack 读 `metalepsis_budget`:
     - type: none(禁用·爽文/玄幻默认) / rhetorical(口头对读者评点) /
       ontological(本体越界·角色穿越叙事层) / mixed
     - target_per_cluster: 整数·期望每 cluster 元叙事 marker 数(none=0)
     - marker_style: ["bracket"="【】", "triangle"="▶", "system_prompt"="[系统]", "narratee"="对读者说话"]
     - allowed_speakers: ["narrator", "system", "protagonist"] · 限定哪些角色能越界
  2. 检测 marker 命中:
     - 排版: 【...】 / ▶... / [系统] / 系统提示: / [SYSTEM] / ◆
     - 关键词: 亲爱的读者 / 看官 / 诸位读者 / 列位 / 你们 (对读者集体称谓)
     - 第二人称对读者: 你以为 / 你也许 / 你或许会问 / 你猜
  3. 偏离判定:
     - type=none 时出现任何 marker → advisory
     - type=rhetorical/ontological/mixed 时:
       - |actual - target| / max(target, 1) > 0.5 → advisory
     - ontological 切换(出现【系统】式 marker)后窗口 ≤300 字内必须有"闭合"标记
       (回到正文/角色继续说话/场景动作恢复) → 否则 advisory("元叙事窗口未闭合")
  4. 与 R8 L28 narratee_registry 关联防双计:当 type=none 且只命中"亲爱的读者"
     式 narratee 越界,本 scanner 只标 marker_type=narratee 不重复算 ontological。

【北极星② / ⑤ 顾问非法官】题材声明由作者档决定 · metalepsis 是题材工艺 advisory ·
  code METALEPSIS_BUDGET_DRIFT 绝不进 audit_hub.HARD_GATE_CODES。
  env METALEPSIS_BUDGET_MODE: off / shadow(默认) / active。

用法: python metalepsis_budget_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "METALEPSIS_BUDGET_DRIFT"

# marker 检测正则(分类·便于关联 R8 L28 narratee 防双计)
MARKER_PATTERNS = {
    "bracket": re.compile(r"【[^】]{1,80}】"),
    "triangle": re.compile(r"▶[^▶\n]{1,80}"),
    "system_prompt": re.compile(r"(\[系统[\]:：]|系统提示[：:]|\[SYSTEM\]|◆[^◆\n]{1,40})"),
    "narratee": re.compile(
        r"(亲爱的读者|看官|诸位读者|列位看官|你们这些|你以为|你也许|你或许|你猜)"),
}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
# ontological 切换后窗口未闭合判定:正文恢复的 cue
_RESUME_CUES = re.compile(r"[。！？”』\n]")


def _mode() -> str:
    m = (os.environ.get("METALEPSIS_BUDGET_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_budget(project_root):
    """读 metalepsis_budget 字段(作者档优先 → 用户偏好 → 世界观)。
    无 → 返回 None(skip)。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    candidates = [
        db / "作者风格.json",
        db / "用户偏好.json",
        db / "世界观.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        v = obj.get("metalepsis_budget")
        if isinstance(v, dict) and v.get("type"):
            return v
    return None


def detect_markers(text: str) -> list:
    """返回 [{kind, term, pos}, ...] 按位置排序。"""
    hits = []
    for kind, rx in MARKER_PATTERNS.items():
        for m in rx.finditer(text):
            hits.append({"kind": kind, "term": m.group(0)[:60], "pos": m.start()})
    hits.sort(key=lambda h: h["pos"])
    return hits


def _check_ontological_window(text: str, hits: list, window: int = 300) -> list:
    """对 ontological 切换(bracket/system_prompt/triangle marker)
    检 ±window 字内是否恢复正文(>=2 个 ， 或 。/！/？/换行 视为恢复)。"""
    unresolved = []
    ontological_kinds = {"bracket", "system_prompt", "triangle"}
    for h in hits:
        if h["kind"] not in ontological_kinds:
            continue
        end = h["pos"] + len(h.get("term", ""))
        tail = text[end:end + window]
        # 恢复 cue 计数:至少需要 2 个句末/换行(否则视为停留元叙事层)
        cues = len(_RESUME_CUES.findall(tail))
        if cues < 2:
            unresolved.append({"pos": h["pos"], "term": h["term"],
                               "window_cues": cues, "kind": h["kind"]})
    return unresolved


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "metalepsis_budget", "schema_version": "1.0", "mode": mode,
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

    budget = _read_budget(project_root)
    out["budget"] = budget
    if budget is None:
        out["note"] = "无 metalepsis_budget 字段·跳过(北极星②:作者未声明不擅判)"
        return out

    btype = str(budget.get("type") or "").strip().lower()
    target = int(budget.get("target_per_cluster") or 0)
    hits = detect_markers(draft)
    out["marker_count"] = len(hits)
    out["sample_markers"] = hits[:8]
    kind_counts = {}
    for h in hits:
        kind_counts[h["kind"]] = kind_counts.get(h["kind"], 0) + 1
    out["kind_counts"] = kind_counts

    msgs = []
    # type=none 时任何 marker 都偏离预算
    if btype == "none" and len(hits) > 0:
        msgs.append(f"type=none 但出现 {len(hits)} 处元叙事 marker"
                    f"({', '.join(sorted({h['kind'] for h in hits}))})·建议清除")
    # rhetorical/ontological/mixed 时按 target 比例
    elif btype in ("rhetorical", "ontological", "mixed"):
        deviation = abs(len(hits) - target) / max(target, 1)
        if deviation > 0.5:
            msgs.append(f"metalepsis marker 数 {len(hits)} 偏离 target_per_cluster"
                        f" {target}(偏差 {round(deviation, 2)} > 0.5)·建议向预算靠拢")

    # ontological 窗口闭合检测(rhetorical 不强制·只 ontological/mixed 且有 marker 时检)
    if btype in ("ontological", "mixed") and hits:
        unresolved = _check_ontological_window(draft, hits)
        if unresolved:
            msgs.append(f"{len(unresolved)} 处 ontological 切换后 300 字内未明确闭合"
                        f"(回到正文动作)·读者会被卡在元层无法回故事")
            out["unresolved_windows"] = unresolved[:5]

    if msgs:
        msg = "·".join(msgs)
        if mode == "active":
            out["violations"].append({
                "kind": "metalepsis_budget", "severity": "minor",
                "message": msg, "marker_count": len(hits),
                "target_per_cluster": target, "budget_type": btype,
                "kind_counts": kind_counts,
                "_doc": "元叙事是系统流/元小说工艺·作者档第一权威·advisory 可豁免·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] metalepsis_budget: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="元叙事越界预算(advisory · 系统流/元小说)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 metalepsis_budget 门控")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
