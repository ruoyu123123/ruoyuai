#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""function_word_fingerprint_scanner.py — 功能词指纹偏离作者基线（advisory · cluster · 2026-06-16）

【缺口】第二轮穷尽核查 wh2h5dqdt #2·function_word 是 SFS 评分**最高权重维**（style_analyzer weight=3.0·
蒸馏/复刻最看重它）·但写作时 0 回查（grep 各 scanner + build_manifest 全 0 命中）。producer 链完整
（FUNCTION_WORDS 15 词 → func_word_freq → consolidate function_word_fingerprint_per_1000）·写作端无人守。

【做法 · 确定性 + 🔴金标准防矫枉过正】：
  cluster 综合 function_word 密度（15 词 count 之和 per 1k CJK）vs 作者档 15 词 mean 之和（base）。
  🔴 金标准校准（2026-06-16·6 作者各 cluster 实测）：综合 function_word cluster/base 全作者最小 0.86
  （极稳·内容无关文体计量指纹·比情绪标点 0.44 稳得多）→ FLOOR_RATIO 0.6（< 0.86 留 0.26 余量·真作者
  绝不误报）。只报「综合 function_word < 作者 base×0.6」（偏少=虚词使用/文体扁平偏离）。偏高永不报
  （北极星③·功能词多是作者风格）。无作者档基线 → skip（不臆造·北极星②第一权威）。

【北极星⑤ 顾问非法官】功能词使用是文体选择·永远 advisory·code FUNCTION_WORD_FINGERPRINT_DRIFT
  **绝不进 audit_hub.HARD_GATE_CODES**。env FUNCTION_WORD_FINGERPRINT_MODE: off / shadow（默认·只记不判·
  检测力待 gen-model 草稿验证再 active）/ active。

用法：python function_word_fingerprint_scanner.py <draft_path> [--manifest m.json] [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 单一真理源：从 style_analyzer import FUNCTION_WORDS（15 词）·勿复制（critic 强调）·import 失败退硬编码
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from style_analyzer import FUNCTION_WORDS  # noqa: E402
except Exception:
    FUNCTION_WORDS = ["的", "了", "着", "却", "便", "竟", "倒", "只", "又",
                      "不过", "只是", "毕竟", "但", "而", "也"]

ISSUE_CODE = "FUNCTION_WORD_FINGERPRINT_DRIFT"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES
FW_FLOOR_RATIO = 0.6   # 金标准:综合 function_word cluster/base 全作者最小 0.86 → 0.6 留 0.26 余量
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("FUNCTION_WORD_FINGERPRINT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _author_fw_base(project_root):
    """作者档 15 词（FUNCTION_WORDS）mean 之和=综合基线（北极星②第一权威）。缺/全 0 → None（skip）。"""
    if not project_root:
        return None
    d = _load(Path(project_root) / "_数据库" / "作者风格.json")
    fw = (d.get("quantitative", {}) or {}).get("function_word_fingerprint_per_1000", {}) or {}
    if not isinstance(fw, dict):
        return None
    s = 0.0
    for v in fw.values():
        if isinstance(v, dict):
            m = v.get("mean")
            if isinstance(m, (int, float)):
                s += m
        elif isinstance(v, (int, float)):
            s += v
    return s if s > 0 else None


def scan(draft_path, manifest_path=None, project_root=None) -> dict:
    """function_word 综合指纹偏离回查。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {
        "scanner": "function_word_fingerprint",
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
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短（<500 CJK）·function_word 密度不可估·跳过"
        return out

    base = _author_fw_base(project_root)
    if base is None:
        out["note"] = "无作者档 function_word 基线（北极星②第一权威）·skip 不臆造"
        return out

    k = cjk / 1000.0
    comb_density = round(sum(draft.count(w) for w in FUNCTION_WORDS) / k, 2)
    floor = round(base * FW_FLOOR_RATIO, 2)
    out["comb_fw_density_per_1k"] = comb_density
    out["author_fw_base"] = round(base, 2)
    out["floor"] = floor
    out["cjk_count"] = cjk

    # 单边下尾：只报「综合 function_word 严重偏少」（< base×0.6）·偏高永不报（北极星③·功能词多是作者风格）
    over = comb_density < floor
    if over:
        msg = (f"功能词综合密度 {comb_density}/千字 < {floor}（作者基线 {round(base, 2)}×{FW_FLOOR_RATIO}）"
               f"·虚词使用偏离作者文体（function_word 是 SFS 最高权重指纹·偏少=文体扁平/翻译腔）")
        if mode == "active":
            out["violations"].append({
                "kind": "function_word_fingerprint_drift", "severity": "minor",
                "message": msg, "comb_density": comb_density, "floor": floor,
                "author_base": round(base, 2),
                "_doc": "功能词使用是文体选择·偏离有时合理→advisory 待裁决·综合密度是可算半边粗糙哨兵·"
                        "真文体质量留 judge/作者（金标准：综合 15 词 cluster/base 最差 0.86·0.6 留余量）",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] function_word_fingerprint: {msg} — 不上报判决", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="功能词指纹偏离作者基线回查(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参（保留）")
    ap.add_argument("--project", default=None, help="读作者 function_word 基线（北极星②第一权威）")
    args = ap.parse_args()
    report = scan(args.draft_path, args.manifest, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·恒 exit 0（不阻断·北极星⑤）·active 有 warning 才 exit 1
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
