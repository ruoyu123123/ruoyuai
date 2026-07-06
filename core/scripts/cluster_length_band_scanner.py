#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cluster_length_band_scanner.py - cluster 草稿长度带 advisory 体检（P2 移植）。

借鉴 LongWriter「长输出长度评估」（出处 research/open_source_writing_systems.md）：
长输出系统需要在下游有人量化「这次到底写了多长」。v27 纯 freestyle 后 writer
不再收任何字数目标、expand 软下限已整体清除——短稿/超长稿风险按架构承诺由
step3 质检下游承接，但检测端此前无人查 cluster 长度。本 scanner 补上这只眼睛：
数草稿 CJK（唯一权威口径 chapter_io.count_cjk），对照健康区间
[12000, 25000]（CLAUDE.md v27 定调）出带外遥测。

  - 低于下限 → CLUSTER_LENGTH_UNDER_BAND（可能撞了差模型/截断/偏短搪塞）
  - 高于上限 → CLUSTER_LENGTH_OVER_BAND（可能失控注水/重复循环）

env:
  CLUSTER_LENGTH_BAND_MODE      三态门 off/shadow/active · 默认 shadow
  CLUSTER_LENGTH_BAND_OVERRIDE  "min,max" 覆盖带宽（如 "8000,20000"）

🔴 北极星⑤红线：本 scanner 是**下游体检不是写作约束**——
  - 结果绝不回流 writer prompt / manifest（字数自然涌现是 v27 纯 freestyle 已定调）；
  - 绝不参与择稿 / 重写决策的自动触发；
  - 绝不 hard_gate（两个 code 永不进 HARD_GATE_CODES）。
短/长只是体检信号，交人 / 审计判断该不该动。确定性零 LLM · advisory。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  CJK 计数唯一权威口径（禁各脚本各算各的）

CODE_UNDER = "CLUSTER_LENGTH_UNDER_BAND"
CODE_OVER = "CLUSTER_LENGTH_OVER_BAND"
DEFAULT_BAND = (12000, 25000)  # CLAUDE.md v27 健康区间（CJK）
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    mode = (os.environ.get("CLUSTER_LENGTH_BAND_MODE") or "shadow").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "shadow"


def _band() -> tuple[int, int, str | None]:
    """解析带宽：env CLUSTER_LENGTH_BAND_OVERRIDE="min,max" 覆盖，非法回默认（带 note）。"""
    raw = (os.environ.get("CLUSTER_LENGTH_BAND_OVERRIDE") or "").strip()
    if not raw:
        return DEFAULT_BAND[0], DEFAULT_BAND[1], None
    try:
        parts = [int(p.strip()) for p in raw.split(",")]
        if len(parts) != 2:
            raise ValueError("need exactly 2 ints")
        lo, hi = parts
        if lo <= 0 or hi <= 0 or lo >= hi:
            raise ValueError("need 0 < min < max")
        return lo, hi, None
    except ValueError:
        return (DEFAULT_BAND[0], DEFAULT_BAND[1],
                f"invalid CLUSTER_LENGTH_BAND_OVERRIDE={raw!r}; fallback to default band")


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    band_min, band_max, band_note = _band()
    out = {
        "scanner": "cluster_length_band",
        "schema_version": "1.0",
        "mode": mode,
        "codes": [CODE_UNDER, CODE_OVER],
        "gate_level": "advisory",
        "band_min": band_min,
        "band_max": band_max,
        "violations": [],
        "verdict": "PASS",
        "warning": None,
    }
    if band_note:
        out["note"] = band_note
    if project_root:
        out["project"] = str(project_root)
    if mode == "off":
        return out

    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as exc:
        out["note"] = f"draft read failed: {str(exc)[:120]}"
        return out
    text = _strip_changes(text)

    cjk = cio.count_cjk(text)
    out["cjk_count"] = cjk

    findings: list[dict] = []
    if cjk < band_min:
        findings.append({
            "code": CODE_UNDER,
            "kind": "cluster_length_under_band",
            "severity": "minor",
            "gate_level": "advisory",
            "cjk_count": cjk,
            "band_min": band_min,
            "band_max": band_max,
            "message": (f"cluster 草稿 CJK={cjk} 低于健康带下限 {band_min}"
                        f"（可能撞了差模型/截断/偏短搪塞·交人/审计判断）"),
        })
    elif cjk > band_max:
        findings.append({
            "code": CODE_OVER,
            "kind": "cluster_length_over_band",
            "severity": "minor",
            "gate_level": "advisory",
            "cjk_count": cjk,
            "band_min": band_min,
            "band_max": band_max,
            "message": (f"cluster 草稿 CJK={cjk} 高于健康带上限 {band_max}"
                        f"（可能失控注水/重复循环·交人/审计判断）"),
        })

    out["finding_count"] = len(findings)
    if findings:
        msg = f"cluster length out of band: cjk={cjk} band=[{band_min},{band_max}]"
        if mode == "active":
            out["violations"] = findings
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] cluster_length_band: {msg}", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(
        description="Cluster draft CJK length band scanner (advisory telemetry, never a writing constraint).")
    parser.add_argument("draft_path")
    parser.add_argument("--project", default=None)
    args = parser.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
