#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""revision_homogenization_scanner.py — gen_fixer 修订后 SFS 反向下跌哨兵
(advisory · cluster · 2026-06-20 R9 W5 Batch-M · F2)

【缺口】R9 联网调研 (Sudowrite/Novelcrafter user reports + ai_assisted_craft_2026):
作家社区反复报告·LLM 修订 (gen_fixer) 常把作者签名风格压回中性「标准 LLM 腔」=
反向下跌 (revision_homogenization)。此前全系统:
  · SkillOpt   只看 best vs candidate skill 的 acceptance
  · audit_hub  只看 issue 数·不看修订前后风格指纹距离
  · 【修订前后 SFS 距离零检测】

本 scanner 补：对比 pre_fix / post_fix 两份草稿的风格指纹 (function_word 密度 +
句长分布 + 句末标点比)·算 delta_sfs·若 post_fix 风格指纹偏离作者基线 >2.0·
而 pre_fix 距离更近 → 修订把风格压扁 → advisory REVISION_REDUCED_AUTHOR_FIDELITY。

【做法 · 确定性纯规则·零 LLM·零依赖 · 占位 fallback】
  R9 文档明确「PCA-ECDF SFS 完整版未实装·用 R7 W2 占位 fallback」。
  本 scanner 用三维风格指纹做 SFS 占位:
    维度①: function_word_per_1k (15 词 mean·function_word_fingerprint_scanner 对齐)
    维度②: sentence_length_mean
    维度③: sentence_length_pstdev (句长方差)
  每维 z-score (vs 作者档基线 mean/std·无则两草稿互相对照) → L1 距离当 SFS_distance。
  delta_sfs = SFS_distance(pre_fix, baseline) - SFS_distance(post_fix, baseline)
  delta_sfs >= 2.0 (post_fix 离基线更远) → REVISION_REDUCED_AUTHOR_FIDELITY

  无作者档时退「pre_fix vs post_fix 距离 >=2.0」单边阈值 advisory。

  --blind-subset N 实验旗：从 pre_fix/post_fix 各取 N 段抽样比较 (LLM Review 信息
  不对称防偏)·默认 N=None (全文)。

【cluster_index.json 增三字段】(实际写回由调用方处理·本 scanner 仅输出供调用)
  pre_fix_sfs / post_fix_sfs / delta_sfs

【北极星② / ⑤ 顾问非法官】作者档第一权威·全 advisory·code
REVISION_REDUCED_AUTHOR_FIDELITY 绝不进 audit_hub.HARD_GATE_CODES。
env REVISION_HOMOGENIZATION_MODE: off / shadow(默认) / active。

用法：python revision_homogenization_scanner.py <pre_fix> <post_fix> [--project <root>] [--blind-subset N]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path

ISSUE_CODE = "REVISION_REDUCED_AUTHOR_FIDELITY"

# function_word_fingerprint 同款 15 词 (与 style_analyzer.FUNCTION_WORDS 对齐 · 单一真理源)
FUNCTION_WORDS_PATTERN = re.compile(
    r"的|了|着|却|便|竟|倒|只|又|不过|只是|毕竟|但|而|也"
)

SENTENCE_END = re.compile(r"[。！？…]+")
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 500
DELTA_SFS_FLOOR = 2.0  # advisory 触发阈


def _mode() -> str:
    m = (os.environ.get("REVISION_HOMOGENIZATION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _fingerprint(text: str) -> dict | None:
    """三维风格指纹·返回 {function_word_per_1k, sent_len_mean, sent_len_pstdev}"""
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        return None
    fw_count = len(FUNCTION_WORDS_PATTERN.findall(text))
    fw_per_1k = fw_count / (cjk / 1000.0)

    sentences = [s.strip() for s in SENTENCE_END.split(text) if s.strip()]
    if not sentences:
        return None
    lens = [_cjk_count(s) for s in sentences if _cjk_count(s) > 0]
    if not lens or len(lens) < 2:
        return {"function_word_per_1k": fw_per_1k,
                "sent_len_mean": float(lens[0] if lens else 0),
                "sent_len_pstdev": 0.0,
                "cjk_total": cjk, "sentence_count": len(lens)}
    return {
        "function_word_per_1k": fw_per_1k,
        "sent_len_mean": statistics.mean(lens),
        "sent_len_pstdev": statistics.pstdev(lens),
        "cjk_total": cjk,
        "sentence_count": len(lens),
    }


def _author_baseline(project_root):
    """读作者档·三维 baseline {function_word_per_1k_mean,std, sent_len_mean,std, sent_len_pstdev_mean,std}"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    fw = obj.get("function_word_fingerprint_per_1000")
    sl_mean = obj.get("sentence_length_mean") or obj.get("sentence_length_mean_baseline")
    sl_std = obj.get("sentence_length_pstdev") or obj.get("sentence_length_pstdev_baseline")

    base = {}
    if isinstance(fw, dict):
        v = fw.get("mean") or fw.get("median")
        s = fw.get("std") or fw.get("stdev")
        if isinstance(v, (int, float)) and isinstance(s, (int, float)) and s > 0:
            base["fw_mean"] = float(v)
            base["fw_std"] = float(s)
    if isinstance(sl_mean, dict):
        v = sl_mean.get("mean")
        s = sl_mean.get("std") or sl_mean.get("stdev")
        if isinstance(v, (int, float)) and isinstance(s, (int, float)) and s > 0:
            base["len_mean"] = float(v)
            base["len_std"] = float(s)
    if isinstance(sl_std, dict):
        v = sl_std.get("mean")
        s = sl_std.get("std") or sl_std.get("stdev")
        if isinstance(v, (int, float)) and isinstance(s, (int, float)) and s > 0:
            base["pstdev_mean"] = float(v)
            base["pstdev_std"] = float(s)
    if not base:
        return None
    return base


def _sfs_distance_to_baseline(fp, base) -> float | None:
    """三维 L1 z-distance·baseline 缺字段则跳过该维。返回 None 表示无可比维度。"""
    if not fp or not base:
        return None
    dims = []
    if "fw_mean" in base and "fw_std" in base:
        dims.append(abs(fp["function_word_per_1k"] - base["fw_mean"]) / base["fw_std"])
    if "len_mean" in base and "len_std" in base:
        dims.append(abs(fp["sent_len_mean"] - base["len_mean"]) / base["len_std"])
    if "pstdev_mean" in base and "pstdev_std" in base:
        dims.append(abs(fp["sent_len_pstdev"] - base["pstdev_mean"]) / base["pstdev_std"])
    if not dims:
        return None
    return sum(dims)


def _sfs_distance_pair(fp_a, fp_b) -> float:
    """两草稿互相对比·L1 of three dims (无 baseline 时的退路)·绝对差除以 max(b,1) 归一"""
    if not fp_a or not fp_b:
        return 0.0
    dims = [
        abs(fp_a["function_word_per_1k"] - fp_b["function_word_per_1k"]) / max(1.0, abs(fp_b["function_word_per_1k"])),
        abs(fp_a["sent_len_mean"] - fp_b["sent_len_mean"]) / max(1.0, fp_b["sent_len_mean"]),
        abs(fp_a["sent_len_pstdev"] - fp_b["sent_len_pstdev"]) / max(1.0, fp_b["sent_len_pstdev"]),
    ]
    return sum(dims)


def _blind_subset(text: str, n: int) -> str:
    """从草稿抽 N 段做 blind-subset SFS (LLM Review 信息不对称)·段长保留。"""
    paragraphs = [p for p in text.split("\n") if p.strip()]
    if n <= 0 or n >= len(paragraphs):
        return text
    step = max(1, len(paragraphs) // n)
    picked = paragraphs[::step][:n]
    return "\n".join(picked)


def scan(pre_fix_path, post_fix_path, project_root=None, blind_subset=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "revision_homogenization",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "verdict": "PASS",
        "violations": [],
        "warning": None,
    }
    if mode == "off":
        return out
    try:
        pre_text = Path(pre_fix_path).read_text(encoding="utf-8")
        post_text = Path(post_fix_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out

    if blind_subset and blind_subset > 0:
        pre_text = _blind_subset(pre_text, blind_subset)
        post_text = _blind_subset(post_text, blind_subset)
        out["blind_subset_n"] = blind_subset

    pre_fp = _fingerprint(pre_text)
    post_fp = _fingerprint(post_text)
    if not pre_fp or not post_fp:
        out["note"] = "草稿太短·无法算指纹"
        return out
    out["pre_fix_fingerprint"] = {k: round(v, 3) if isinstance(v, float) else v
                                  for k, v in pre_fp.items()}
    out["post_fix_fingerprint"] = {k: round(v, 3) if isinstance(v, float) else v
                                   for k, v in post_fp.items()}

    base = _author_baseline(project_root)
    pre_sfs = None
    post_sfs = None
    if base:
        pre_sfs = _sfs_distance_to_baseline(pre_fp, base)
        post_sfs = _sfs_distance_to_baseline(post_fp, base)
        out["baseline_source"] = "author_profile"
    if pre_sfs is None or post_sfs is None:
        # 退路: 两草稿对比 (无 baseline)
        pair_dist = _sfs_distance_pair(post_fp, pre_fp)
        pre_sfs = 0.0
        post_sfs = pair_dist
        out["baseline_source"] = "pair_fallback"
    out["pre_fix_sfs"] = round(pre_sfs, 3)
    out["post_fix_sfs"] = round(post_sfs, 3)
    delta_sfs = post_sfs - pre_sfs
    out["delta_sfs"] = round(delta_sfs, 3)

    msg = None
    if delta_sfs >= DELTA_SFS_FLOOR:
        msg = (f"gen_fixer 修订后 SFS_distance({round(post_sfs,2)}) 比修订前"
               f"({round(pre_sfs,2)}) 增大 Δ={round(delta_sfs,2)} (>= {DELTA_SFS_FLOOR})·"
               f"修订把作者签名风格压扁 (revision_homogenization)")
    if msg:
        if mode == "active":
            out["violations"].append({
                "code": ISSUE_CODE,
                "kind": "revision_homogenization",
                "severity": "minor",
                "message": msg,
                "delta_sfs": round(delta_sfs, 3),
                "pre_fix_sfs": round(pre_sfs, 3),
                "post_fix_sfs": round(post_sfs, 3),
                "_doc": "F2 revision SFS 反向下跌·建议放回 pre_fix 风格 + 重审 fixer prompt·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] revision_homogenization: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="gen_fixer 修订前后 SFS 反向下跌哨兵 (advisory · shadow)")
    ap.add_argument("pre_fix_path")
    ap.add_argument("post_fix_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--blind-subset", type=int, default=None,
                    help="实验旗·从两份草稿各抽 N 段做 SFS (LLM Review 信息不对称)")
    args, _ = ap.parse_known_args()
    rep = scan(args.pre_fix_path, args.post_fix_path,
               project_root=args.project, blind_subset=args.blind_subset)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
