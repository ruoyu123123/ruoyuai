#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""narratee_address_scanner.py — 受述者(narratee)称谓一致性(advisory · cluster · 2026-06-20 R8 W4 Batch-I)

【缺口】L28 联网调研(Phelan Ideal Narratee Poetics Today 2022): 元小说/破壁叙述中
narratee(叙述者面向的虚构受述者)的称谓应稳定一致(如全篇『亲爱的读者』vs 全篇『诸位看官』
vs 全篇『你』)·混用会撕裂叙述层。LLM 在破壁段落常随机切换『读者』『你』『诸位』『各位』
=narratee 身份漂浮。此前**全系统零检测**。

【做法 · 确定性纯规则】:
  1. 作者档读 narratee_registry:
     - primary: 期望主要 narratee 称谓(如 "亲爱的读者")
     - allowed_addresses: 允许使用的称谓列表
     - min_consistency: primary 占所有 narratee 称谓的最低占比(默认 0.8)
     无 narratee_registry → skip(北极星②)
  2. 抓所有 narratee 称谓命中:
     『亲爱的读者』『读者』『读者们』『读者朋友』『看官』『诸位看官』『列位』
     『诸位』『各位』『你』式破壁第二人称(『你以为』『你也许』『你不知道』『你猜』)
  3. 计算 primary_ratio = primary 命中 / 总 narratee 命中。
     primary_ratio < min_consistency → advisory("narratee 漂浮")
  4. allowed_addresses 之外的称谓命中 → advisory("非册命称谓")

【北极星② / ⑤ 顾问非法官】advisory · 与 L25 metalepsis 关联(narratee 越界归本 scanner) ·
  code NARRATEE_DRIFT 绝不进 audit_hub.HARD_GATE_CODES。
  env NARRATEE_ADDRESS_MODE: off / shadow(默认) / active。

用法: python narratee_address_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "NARRATEE_DRIFT"

# narratee 称谓正则(分类)
NARRATEE_FORMS = {
    "亲爱的读者": re.compile(r"亲爱的读者"),
    "读者": re.compile(r"读者(们|朋友)?"),
    "看官": re.compile(r"(诸位|列位)?看官"),
    "诸位": re.compile(r"(诸位|列位|各位)(?!看官)"),
    "你_breakwall": re.compile(r"(你以为|你也许|你或许|你不知道|你猜|你会发现|你可知)"),
}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("NARRATEE_ADDRESS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_registry(project_root):
    """读 narratee_registry。无 → None(skip)。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for path in [db / "作者风格.json", db / "用户偏好.json"]:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        reg = obj.get("narratee_registry")
        if isinstance(reg, dict) and reg.get("primary"):
            return reg
    return None


def count_forms(text: str) -> dict:
    return {k: len(rx.findall(text)) for k, rx in NARRATEE_FORMS.items()}


def _classify_form(primary: str) -> str:
    """primary 字符串归一化到 NARRATEE_FORMS 的 key。"""
    p = primary.strip()
    if "亲爱" in p:
        return "亲爱的读者"
    if "看官" in p:
        return "看官"
    if "读者" in p:
        return "读者"
    if any(w in p for w in ["诸位", "列位", "各位"]):
        return "诸位"
    if p == "你" or "你" in p:
        return "你_breakwall"
    return p


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "narratee_address", "schema_version": "1.0", "mode": mode,
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

    reg = _read_registry(project_root)
    out["registry"] = reg
    if reg is None:
        out["note"] = "无 narratee_registry·skip(北极星②:作者未声明 narratee 不擅判)"
        return out

    primary = str(reg.get("primary") or "").strip()
    allowed = [str(a).strip() for a in (reg.get("allowed_addresses") or [primary])]
    min_consistency = float(reg.get("min_consistency") or 0.8)

    counts = count_forms(draft)
    total = sum(counts.values())
    out["form_counts"] = counts
    out["total_narratee_hits"] = total

    if total == 0:
        out["note"] = "正文无 narratee 称谓命中·跳过"
        return out

    primary_key = _classify_form(primary)
    primary_count = counts.get(primary_key, 0)
    primary_ratio = round(primary_count / total, 3)
    out["primary_key"] = primary_key
    out["primary_count"] = primary_count
    out["primary_ratio"] = primary_ratio
    out["min_consistency"] = min_consistency

    allowed_keys = {_classify_form(a) for a in allowed}
    out_of_registry = []
    for k, n in counts.items():
        if n > 0 and k not in allowed_keys:
            out_of_registry.append({"form": k, "count": n})
    out["out_of_registry"] = out_of_registry

    msgs = []
    if primary_ratio < min_consistency:
        msgs.append(f"narratee 漂浮: primary='{primary}' 占比 {primary_ratio} < {min_consistency}"
                    f" (总称谓 {total} 次·primary 仅 {primary_count} 次)")
    if out_of_registry:
        terms = "、".join(f"{r['form']}({r['count']})" for r in out_of_registry[:4])
        msgs.append(f"出现 {len(out_of_registry)} 种非册命称谓: {terms}")

    if msgs:
        msg = "·".join(msgs)
        if mode == "active":
            out["violations"].append({
                "kind": "narratee_drift", "severity": "minor",
                "message": msg, "primary_ratio": primary_ratio,
                "min_consistency": min_consistency,
                "out_of_registry": out_of_registry,
                "_doc": "narratee 一致性工艺·与 L25 metalepsis 关联防双计·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] narratee_address: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="narratee 称谓一致性(advisory · 元叙事)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 narratee_registry")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
