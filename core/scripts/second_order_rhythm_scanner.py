#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""second_order_rhythm_scanner.py — 二阶句长节奏动力学（advisory · cluster · 2026-06-16）

【缺口】第二轮穷尽核查 wh2h5dqdt #3·prose_rhythm 探针7（pstdev）是 **permutation-invariant**（洗牌
句序值不变）·provably 看不见时序/聚簇结构。二阶 Δ²方差是 **order-sensitive**（[短短长长]与[短长短长]
同 σ 但二阶差分形态不同）·补探针7 时序盲区。对标 DivEye（AI 系统性压低 structural variance·含二阶时序）。

【做法 · 确定性 + 🔴金标准】：
  句长序列二阶差分方差 / 句长方差（归一化去一阶 std）= Δ²var/var（纯二阶节奏涨落形态）。
  🔴 金标准（2026-06-16·6 作者各 cluster）：真作者 Δ²var/var μ=4.65-5.41 极集中（跨作者稳定·二阶
  节奏是普适指纹非作者特异·印证 DivEye）·各作者 cluster min 3.78 → 通用 FLOOR 3.0（< 全作者 min·留
  余量·真作者绝不误报）。cluster Δ²var/var < 3.0 = 句长缓变/二阶平滑（缺真作者句长跳变节奏·AI 平滑
  特征·探针7 看不见）→ 报。**var=0（纯匀速·探针7 管）→ None 不报（与探针7 解耦防双计数）**。单边·偏高
  不报（北极星③·二阶涨落大是真作者节奏）。

【为何通用 FLOOR 非作者自适应】二阶节奏塌缩是 AI 系统性特征（DivEye·跨作者跨模型普适·匀速/平滑=AI
  腔·非作者风格维度）·检测「比所有真作者都平滑」（< 全作者 min 3.78）= 确定 AI 平滑非作者风格·符合
  北极星⑤（检测 AI 腔非卡作者风格）。

【北极星⑤】二阶节奏是文体·永远 advisory·code SECOND_ORDER_RHYTHM_FLAT **绝不进 HARD_GATE_CODES**。
  env SECOND_ORDER_RHYTHM_MODE: off / shadow（默认·只记不判·检测力待 gen-model 草稿验证再 active）/ active。

用法：python second_order_rhythm_scanner.py <draft_path> [--manifest m.json] [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

ISSUE_CODE = "SECOND_ORDER_RHYTHM_FLAT"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES
SECOND_ORDER_FLOOR = 3.0   # 金标准:真作者 Δ²var/var 各 cluster min 3.78 → 3.0 留余量
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("SECOND_ORDER_RHYTHM_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk  # noqa: E402 字数口径单一真理源


def _sentence_lengths(text: str) -> list:
    """句长序列（与 prose_rhythm 同口径：段内按句末符切·cjk≥2 计）。"""
    body = [l.strip() for l in text.split("\n") if l.strip()
            and not re.match(r"^第\d+章", l.strip()) and not l.strip().startswith("【")]
    lens = []
    for p in body:
        for s in re.split(r"(?<=[。！？…])", p):
            s = s.strip()
            if _cjk(s) >= 2:
                lens.append(_cjk(s))
    return lens


def second_diff_var_normed(lens: list):
    """Δ²方差 / 句长方差（归一化去一阶）。var=0（纯匀速）→ None（与探针7 解耦）。len<4 → None。"""
    if len(lens) < 4:
        return None
    var = statistics.pvariance(lens)
    if var == 0:
        return None   # 纯匀速 → 探针7 管·此处不报（解耦防双计数）
    d1 = [lens[i + 1] - lens[i] for i in range(len(lens) - 1)]
    d2 = [d1[i + 1] - d1[i] for i in range(len(d1) - 1)]
    if len(d2) < 2:
        return None
    return round(statistics.pvariance(d2) / var, 2)


def scan(draft_path, manifest_path=None, project_root=None) -> dict:
    """二阶句长节奏动力学回查。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {
        "scanner": "second_order_rhythm",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 北极星⑤ · 绝不 hard_gate
        "warning": None,
        "violations": [],
        "verdict": "PASS",
    }
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    lens = _sentence_lengths(draft)
    if len(lens) < 10:
        out["note"] = "句子太少（<10）·二阶节奏不可估·跳过"
        return out

    sdv = second_diff_var_normed(lens)
    out["second_diff_var_normed"] = sdv
    out["sentence_count"] = len(lens)
    if sdv is None:
        out["note"] = "句长方差为 0（纯匀速·探针7 管）或样本不足·二阶不报（解耦）"
        return out

    # 单边下尾：Δ²var/var < FLOOR = 句长缓变/二阶平滑（探针7 看不见的时序塌缩）·偏高不报（北极星③）
    if sdv < SECOND_ORDER_FLOOR:
        msg = (f"二阶句长节奏 Δ²var/var={sdv} < {SECOND_ORDER_FLOOR}（真作者基线 μ≈5·min 3.78）"
               f"·句长缓变/二阶平滑（缺真作者的句长跳变节奏·AI 平滑腔·探针7 的 std 看不见此时序塌缩）")
        if mode == "active":
            out["violations"].append({
                "kind": "second_order_rhythm_flat", "severity": "minor",
                "message": msg, "second_diff_var_normed": sdv, "floor": SECOND_ORDER_FLOOR,
                "_doc": "二阶节奏是文体·平滑有时合理→advisory 待裁决·Δ²var/var 是 order-sensitive 时序指标·"
                        "与探针7(permutation-invariant std)正交·真节奏质量留 judge/作者（金标准:跨作者 min 3.78）",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] second_order_rhythm: {msg} — 不上报判决", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="二阶句长节奏动力学回查(advisory·DivEye 适配)")
    ap.add_argument("draft_path")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参（保留）")
    ap.add_argument("--project", default=None, help="兼容 audit_hub 传参（二阶节奏用通用基线·跨作者稳定）")
    args = ap.parse_args()
    report = scan(args.draft_path, args.manifest, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·恒 exit 0（不阻断·北极星⑤）·active 有 warning 才 exit 1
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
