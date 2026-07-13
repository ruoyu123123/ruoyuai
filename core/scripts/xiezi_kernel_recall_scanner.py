#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xiezi_kernel_recall_scanner.py — 楔子 kernel symbol 末卷召回
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L60 P1)

【缺口】R10 联网调研(Literariness pinghua 楔子 + 红楼梦石头楔子 + 儒林外史
王冕)：传统话本/章回体楔子 xiezi 应满足『时间断层≥10 年+地点断层+独立 character
+含 kernel symbol 命名实体』，且 kernel symbol 末卷召回(全书闭合)。LLM 默认
易写楔子但末卷遗忘召回。此前【0 检测】。

【做法 · 确定性 JSON 驱动】：
  1. 门控：作者档 huaben_zhanghui_pastiche=true 才启用。
  2. cluster_001 时优先校验楔子骨架(advisory)。
  3. 读 _数据库/伏笔表.json 或 .cross_cluster_scan/xiezi_kernel.json 的
     kernel_symbols 列表 (item: {symbol, planted_cluster, must_recall_in_volume}).
  4. 末卷 final cluster (manifest.is_volume_finale=true) 草稿不出现 symbol
     → XIEZI_KERNEL_NOT_RECALLED flag。

【北极星② / ⑤】纯 advisory · 作者档未启用 → skip · 绝不 hard_gate。
  env XIEZI_KERNEL_RECALL_MODE: off / shadow(默认) / active。

用法：python xiezi_kernel_recall_scanner.py <draft_path> [--project <root>]
      [--manifest <ch_manifest.json>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup as cl  # noqa: E402 · 章号⇄cluster_id 唯一权威反查（北极星①·禁字符串后缀判 cluster 身份）
from atomic_json import load_json  # noqa: E402 · 读侧单一真理源（北极星⑥）

ISSUE_CODE = "XIEZI_KERNEL_NOT_RECALLED"
ISSUE_CODE_HOMOLOGY = "XIEZI_HOMOLOGY_THIN"

MIN_CJK = 200


def _mode() -> str:
    m = (os.environ.get("XIEZI_KERNEL_RECALL_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(p: Path):
    return load_json(p)


def _enabled(project_root) -> tuple:
    if not project_root:
        return False, "无 project_root"
    style_p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(style_p) if style_p.exists() else None
    if isinstance(obj, dict):
        if obj.get("huaben_zhanghui_pastiche") is True:
            return True, "huaben_zhanghui_pastiche=true"
        if obj.get("xiezi_kernel_required") is True:
            return True, "xiezi_kernel_required=true"
    return False, "未启用"


def _load_manifest(manifest_path):
    if not manifest_path:
        return None
    p = Path(manifest_path)
    if not p.exists():
        return None
    return _read_json(p)


def _load_kernel_symbols(project_root):
    if not project_root:
        return []
    candidates = [
        Path(project_root) / "_数据库" / ".cross_cluster_scan" /
        "xiezi_kernel.json",
        Path(project_root) / "_数据库" / "伏笔表.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        obj = _read_json(p)
        if isinstance(obj, dict):
            if "kernel_symbols" in obj and isinstance(obj["kernel_symbols"], list):
                return obj["kernel_symbols"]
            if "xiezi_kernel_symbols" in obj and isinstance(
                    obj["xiezi_kernel_symbols"], list):
                return obj["xiezi_kernel_symbols"]
        if isinstance(obj, list):
            return obj
    return []


def _is_final_volume_cluster(manifest):
    if not isinstance(manifest, dict):
        return False
    return bool(manifest.get("is_volume_finale")
                or manifest.get("is_book_finale"))


def check_recall(draft_text, symbols):
    """末卷 cluster 草稿是否提及 kernel symbol。"""
    hits = []
    missing = []
    for sym in symbols:
        if isinstance(sym, dict):
            tok = sym.get("symbol") or sym.get("name") or ""
        else:
            tok = str(sym or "")
        tok = tok.strip()
        if not tok:
            continue
        if tok in draft_text:
            hits.append(tok)
        else:
            missing.append(tok)
    return hits, missing


def check_xiezi_homology(draft_text):
    """cluster_001 楔子骨架弱评：四要素打分。"""
    score = 0
    found = {}
    if re.search(r"\d+\s*年[前后]|多年前|多年后|百年|千年|当年|往昔|追溯", draft_text):
        score += 1
        found["time_break"] = True
    if re.search(r"[山岭城寺洲国乡村庙观殿]|又一处|另一处|那地方|此地", draft_text):
        score += 1
        found["space_break"] = True
    # kernel symbol：含至少一个引号命名物件
    if re.search(r"[“”\"][一-龥]{1,8}[“”\"]|名为[一-龥]{1,6}|"
                 r"号曰[一-龥]{1,6}|名唤[一-龥]{1,6}",
                 draft_text):
        score += 1
        found["kernel_named"] = True
    return score, found


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "xiezi_kernel_recall", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory", "verdict": "PASS",
           "violations": [], "warning": None}
    if mode == "off":
        return out
    enabled, reason = _enabled(project_root)
    if not enabled:
        out["note"] = f"{reason} · 跳过(北极星②)"
        return out
    out["gate_reason"] = reason
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    if len(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out
    manifest = _load_manifest(manifest_path)
    cluster_id = (manifest or {}).get("cluster_id", "") or ""
    is_first = (manifest or {}).get("cluster_index") == 1 or \
        cl.cluster_num(cluster_id) == 1
    is_final = _is_final_volume_cluster(manifest)
    out["cluster_id"] = cluster_id
    out["is_first"] = is_first
    out["is_final"] = is_final

    symbols = _load_kernel_symbols(project_root)
    out["kernel_symbol_count"] = len(symbols)

    msgs = []
    if is_first:
        score, found = check_xiezi_homology(text)
        out["homology_score"] = score
        out["homology_found"] = found
        if score < 2:
            msgs.append({"code": ISSUE_CODE_HOMOLOGY,
                         "message": (f"cluster_001 楔子骨架弱(score={score}/3) · "
                                     f"建议含时间断层+地点断层+kernel symbol 命名")})

    if is_final and symbols:
        hits, missing = check_recall(text, symbols)
        out["recall_hits"] = hits
        out["recall_missing"] = missing
        if missing:
            msgs.append({"code": ISSUE_CODE,
                         "message": (f"末卷 cluster 未召回 {len(missing)} 个 "
                                     f"kernel symbol({','.join(missing[:5])}) · "
                                     f"全书闭合受损")})

    if msgs:
        if mode == "active":
            for m in msgs:
                out["violations"].append({
                    "code": m["code"], "kind": "xiezi_kernel",
                    "severity": "minor", "message": m["message"],
                    "_doc": "advisory · 作者档可豁免 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = "; ".join(m["message"] for m in msgs)
        else:
            for m in msgs:
                print(f"[SHADOW] xiezi_kernel_recall: {m['message']} — 不上报",
                      file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="楔子 kernel symbol 末卷召回 (advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
