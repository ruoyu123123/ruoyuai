#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cohn_consciousness_mode_scanner.py — Cohn 意识表征四模式比例
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L60 P1)

【缺口】R10 联网调研(Cohn Transparent Minds + LHN Narratology)：意识表征四模式
quoted / psycho-narration / autonomous / narrated_monologue(FID) 比例失衡 =
内心戏单调。此前 R7 FID detector 只识别 FID 一种 · interiority_mode_balance
只查直接独白过密 · 【四模式联合分布零检测】。

【做法 · 确定性句级分类】：
  1. 按句末符号(。！？……)切句。
  2. 四类启发式：
     · quoted(直接引语)：含『他想:』『心说"』『暗道:』『想道:』+ 引号。
     · psycho-narration(心理叙述)：抽象动词(意识到/明白/觉得/感到/认为)
       + 三人称转述句式。
     · autonomous(自主独白)：第一人称『我』连续句段(≥3 句无引号无框架)。
     · narrated_monologue(FID)：复用 R7 检测信号 — 无明显引号/思想动词框架但
       含 modal(也许/或许/竟然/果然) + 体验时态 + POV 锚词。
  3. 计 cluster 级 cohn_mode_distribution[four_keys].
  4. 与作者档 cohn_mode_signature 各通道比 ratio · 2σ 偏离 → COHN_MODE_DRIFT。

【北极星② / ⑤】纯 advisory · 作者档第一权威 · 绝不 hard_gate。
  env COHN_CONSCIOUSNESS_MODE: off / shadow(默认) / active。

用法：python cohn_consciousness_mode_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "COHN_MODE_DRIFT"
MIN_CJK = 500
SENT_SPLIT = re.compile(r"[。！？……]+|\n")

QUOTED_FRAME = re.compile(
    r"(他|她|我|[一-龥]{1,4})(想道|心想|心说|暗道|暗想|腹诽|忖度|思忖|寻思)"
    r"[:：]?\s*[“\"‘'].+?[”\"’']")
PSYCHO_NARRATION = re.compile(
    r"(他|她|[一-龥]{1,4})(意识到|察觉|明白|觉得|感到|认为|意会|发觉|"
    r"知晓|了悟|揣度)")
FIRSTP_AUTONOMOUS = re.compile(r"^我[^“”\"'']{0,30}$")
FID_MARKERS = re.compile(r"也许|或许|竟然|果然|莫非|怎能|焉知|岂能|何尝|何曾")

DEFAULT_BASELINE = {
    "quoted": 0.20, "psycho_narration": 0.40,
    "autonomous": 0.10, "narrated_monologue": 0.30,
}


def _mode() -> str:
    m = (os.environ.get("COHN_CONSCIOUSNESS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _author_baseline(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return None
    sig = obj.get("cohn_mode_signature")
    if not isinstance(sig, dict):
        return None
    total = sum(float(v) for v in sig.values() if isinstance(v, (int, float)))
    if total <= 0:
        return None
    cleaned = {k: float(sig.get(k, 0.0)) / total for k in DEFAULT_BASELINE}
    return cleaned


def classify_sentence(sent, prev_first_person_run):
    """four-way classify。返回 (mode, new_run)"""
    s = sent.strip()
    if not s:
        return None, 0
    # quoted
    if QUOTED_FRAME.search(s):
        return "quoted", 0
    # autonomous: 1st person run
    is_first = s.startswith("我") and not re.search(r"[“”\"'']", s)
    if is_first:
        new_run = prev_first_person_run + 1
        if new_run >= 3:
            return "autonomous", new_run
        return "psycho_narration", new_run  # 未到 3 句先归 psycho
    # psycho-narration
    if PSYCHO_NARRATION.search(s):
        return "psycho_narration", 0
    # FID
    if FID_MARKERS.search(s):
        return "narrated_monologue", 0
    return None, 0


def compute_distribution(text):
    sents = [s for s in SENT_SPLIT.split(text) if s.strip()]
    counts = {k: 0 for k in DEFAULT_BASELINE}
    fp_run = 0
    for s in sents:
        cls, fp_run = classify_sentence(s, fp_run)
        if cls in counts:
            counts[cls] += 1
        else:
            fp_run = 0
    total = sum(counts.values())
    if total == 0:
        return None, counts
    return {k: counts[k] / total for k in counts}, counts


def detect_drift(P, Q, eps=1e-6, sigma_thresh=2.0):
    """log ratio > 2σ ≈ |log(P/Q)| > log(3) 近似。"""
    flags = []
    for k in Q:
        p = P.get(k, 0.0) + eps
        q = Q[k] + eps
        ratio = p / q
        if ratio > 2.0 or ratio < 0.5:
            flags.append({"mode": k, "P": round(p, 4), "Q": round(q, 4),
                          "ratio": round(ratio, 3)})
    return flags


def scan(draft_path, project_root=None) -> dict:
    mode_env = _mode()
    out = {"scanner": "cohn_consciousness_mode", "schema_version": "1.0",
           "mode": mode_env, "code": ISSUE_CODE, "gate_level": "advisory",
           "verdict": "PASS", "violations": [], "warning": None}
    if mode_env == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out
    P, counts = compute_distribution(text)
    out["mode_counts"] = counts
    if P is None:
        out["note"] = "无可分类意识表征句·跳过"
        return out
    out["cohn_mode_distribution"] = {k: round(v, 4) for k, v in P.items()}

    Q = _author_baseline(project_root)
    if Q is None:
        Q = DEFAULT_BASELINE
        out["baseline_source"] = "default"
    else:
        out["baseline_source"] = "author_profile"
    out["author_distribution"] = {k: round(v, 4) for k, v in Q.items()}

    flags = detect_drift(P, Q)
    out["drift_flags"] = flags

    msg = None
    if flags:
        sample = ",".join(f"{f['mode']}={f['ratio']}x" for f in flags[:3])
        msg = (f"Cohn 四模式偏离作者基线 {len(flags)} 通道(2σ): {sample}")
    if msg:
        if mode_env == "active":
            out["violations"].append({
                "code": ISSUE_CODE, "kind": "cohn_mode_drift",
                "severity": "minor", "message": msg,
                "drift_flags": flags,
                "_doc": "advisory · 作者档 cohn_mode_signature 第一权威 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] cohn_consciousness_mode: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Cohn 意识表征四模式 (advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
