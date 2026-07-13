#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trajectory_moral_slope_scanner.py — Cronon 首尾境况斜率·扁平 cluster 检测·shadow

【缺口 · R22 W10 Batch-EE·P1 · 2026-06-21】William Cronon《A Place for Stories》
1992 非虚构叙事道德斜率理论：
  每一段叙事都该有 protagonist_state 首尾差。三类判定：
    clear_progressivist  = Σ|Δ| ≥ 3 且后段 - 前段 > 0（境况上升）
    clear_declensionist  = Σ|Δ| ≥ 3 且后段 - 前段 < 0（境况下降）
    AMBIGUOUS_FLAT_TRAJECTORY = Σ|Δ| < 3 （主角境况扁平·读者无 arc 体验）

  仅 AMBIGUOUS_FLAT_TRAJECTORY 出 advisory（前两类是创作选择不报警）。

【与既有 scanner 显式去重】
  - sentiment_arc_fractal：情感弧 Hurst/ApEn
    本 scanner = 4 维（power/status/relation/threat）量化境况·正交

【做法 · 确定性占位（零 LLM）】
  本占位走启发式词典量化代替 judge 打分（真版本走 judge agent 见 _doc）：
    power_pos    / power_neg    = 力量增/失
    status_pos   / status_neg   = 地位升/降
    relation_pos / relation_neg = 关系亲/疏
    threat_pos   / threat_neg   = 威胁缓/增

  - 首尾各采样 600 CJK
  - 每段命中各维 pos/neg 词频 → 净值 (pos - neg)
  - 差值 Δ = end - start，4 维 Σ|Δ| < 3 → AMBIGUOUS_FLAT_TRAJECTORY
  - reward 信号送 learning_loop（写 _数据库/trajectory_moral_slope_history.json）

【北极星⑤】顾问非法官·全 advisory·env TRAJECTORY_MORAL_SLOPE_MODE
  AMBIGUOUS_FLAT_TRAJECTORY 绝不进 audit_hub.HARD_GATE_CODES。

用法: python trajectory_moral_slope_scanner.py <draft> [--project <root>] [--cluster <id>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "AMBIGUOUS_FLAT_TRAJECTORY"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 4 维 pos/neg lexicon 占位
STATE_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "power": {
        "pos": ["突破", "进阶", "增强", "强大", "学会", "掌握", "获得力量", "升级"],
        "neg": ["重创", "受伤", "虚弱", "失去力量", "残废", "退化", "封印"],
    },
    "status": {
        "pos": ["升迁", "成名", "上位", "登顶", "封赏", "尊崇", "扬名"],
        "neg": ["失势", "贬", "落魄", "声名扫地", "罢免", "降级", "蒙羞"],
    },
    "relation": {
        "pos": ["结盟", "知己", "亲近", "相爱", "拥抱", "和好", "信任"],
        "neg": ["背叛", "决裂", "离别", "诀别", "绝交", "怨恨", "孤立"],
    },
    "threat": {
        "pos": ["脱险", "化解", "安全", "解除", "和平", "撤围"],
        "neg": ["危险", "追杀", "围困", "暗箭", "绝境", "灭顶", "迫近"],
    },
}

SAMPLE_LENGTH_CJK = 600
SIGMA_DELTA_THRESHOLD = 3.0


def _mode() -> str:
    m = (os.environ.get("TRAJECTORY_MORAL_SLOPE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _slice_cjk(text: str, n: int, from_end: bool = False) -> str:
    """从开头或结尾切大约 n 个 CJK 字符（含夹杂英文/标点）"""
    if from_end:
        cnt = 0
        for i in range(len(text) - 1, -1, -1):
            if "一" <= text[i] <= "鿿":
                cnt += 1
            if cnt >= n:
                return text[i:]
        return text
    cnt = 0
    for i, ch in enumerate(text):
        if "一" <= ch <= "鿿":
            cnt += 1
        if cnt >= n:
            return text[:i + 1]
    return text


def _state_score(text: str) -> dict:
    """4 维 (pos - neg) 净值"""
    lex = STATE_LEXICON_PLACEHOLDER
    out = {}
    for dim in ("power", "status", "relation", "threat"):
        pos_hits = sum(text.count(w) for w in lex[dim]["pos"])
        neg_hits = sum(text.count(w) for w in lex[dim]["neg"])
        out[dim] = {"pos": pos_hits, "neg": neg_hits, "net": pos_hits - neg_hits}
    return out


def _classify(deltas: dict) -> tuple[str, float, float]:
    """根据 Σ|Δ| 与 净 Δ 总和分类"""
    total_abs = sum(abs(d) for d in deltas.values())
    net = sum(deltas.values())
    if total_abs < SIGMA_DELTA_THRESHOLD:
        return ("AMBIGUOUS_FLAT_TRAJECTORY", total_abs, net)
    if net > 0:
        return ("clear_progressivist", total_abs, net)
    if net < 0:
        return ("clear_declensionist", total_abs, net)
    return ("AMBIGUOUS_FLAT_TRAJECTORY", total_abs, net)


def _append_history(project_root, cluster_id, label, total_abs, net, deltas):
    if not project_root:
        return
    p = Path(project_root) / "_数据库" / "trajectory_moral_slope_history.json"
    try:
        obj = (json.loads(p.read_text(encoding="utf-8"))
               if p.exists() else {"history": [], "_schema_version": "1.0"})
    except (OSError, json.JSONDecodeError):
        obj = {"history": [], "_schema_version": "1.0"}
    if not isinstance(obj, dict):
        obj = {"history": [], "_schema_version": "1.0"}
    obj.setdefault("history", []).append({
        "cluster": cluster_id or "current",
        "label": label,
        "sigma_abs": round(total_abs, 2),
        "net_delta": round(net, 2),
        "deltas": deltas,
    })
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {"scanner": "trajectory_moral_slope", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < SAMPLE_LENGTH_CJK * 2:
        out["note"] = f"草稿不足首尾各 {SAMPLE_LENGTH_CJK} CJK·跳过"
        return out

    head = _slice_cjk(text, SAMPLE_LENGTH_CJK, from_end=False)
    tail = _slice_cjk(text, SAMPLE_LENGTH_CJK, from_end=True)
    head_state = _state_score(head)
    tail_state = _state_score(tail)
    deltas = {dim: tail_state[dim]["net"] - head_state[dim]["net"]
              for dim in ("power", "status", "relation", "threat")}
    label, total_abs, net = _classify(deltas)
    out["head_state"] = head_state
    out["tail_state"] = tail_state
    out["deltas"] = deltas
    out["sigma_abs"] = round(total_abs, 2)
    out["net_delta"] = round(net, 2)
    out["label"] = label

    _append_history(project_root, cluster_id, label, total_abs, net, deltas)

    if label == "AMBIGUOUS_FLAT_TRAJECTORY":
        msg = (f"主角境况斜率扁平（Σ|Δ|={total_abs:.1f} < {SIGMA_DELTA_THRESHOLD}）·"
               "本 cluster 无 arc 体验·读者收尾感薄")
        if mode == "active":
            out["violations"].append({
                "kind": "ambiguous_flat_trajectory", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "sigma_abs": round(total_abs, 2), "net_delta": round(net, 2),
                "_doc": "Cronon 首尾境况 4 维斜率·铺垫/缓冲型 cluster 可豁免·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] trajectory_moral_slope: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Cronon 首尾境况斜率·扁平 cluster 检测·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
