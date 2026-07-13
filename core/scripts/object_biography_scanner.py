#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""object_biography_scanner.py — 物件生命传记相位账本（advisory · cluster · 2026-06-20 R13 W6 Batch-R · P1 STRONG · shadow）

【缺口 · R13 STRONG】Kopytoff 1986《物件的文化传记》+ Bill Brown 2003 U.Chicago Press
+ Penn Museum object biography 指南 + Heritage Studies 2023：
  小说叙事里**被命名物件**（剑/玉佩/系统/手机/契约书/储物戒）应有完整生命传记轨迹：
    acquired → in_use → transformed → damaged → lost → recovered → discarded → reentered
  LLM 默认易写「物件首次拿出 → 永远 in_use → 不再变化」=相位塌缩。本 scanner 补检测。

【与既有 scanner 正交】
  - R8 motif_recurrence_scanner  -> 母题密度（不区分相位）
  - R10 power_progression_scanner -> 力量等级（不针对具名物件）
  - R6 anachronism_scanner       -> 时代错位（不查相位）
  - R11 signed_relation_graph    -> 关系签字（人-人不是人-物）
  完全独立维度。

【做法 · 确定性正则 + 句法 cue 打 tag】
  1. named_objects 门槛抽取：
     - 项目 `_数据库/物件登记表.json` / `物件表.json` / `items.json` 之一存在 → 读 `items[].name`
       并按 `plot_critical=true` 或 `mentions>=2` 入选。
     - 缺则草稿正向扫，统计候选具名物件（≥2 次出现 + 至少一处带『的』/动词上下文）。
     - 候选上限 12 个，防止误扫太多通名。
  2. 相位标签字典（中文常见动作 cue）：
       acquired:   {取出, 取来, 获得, 拿到, 得到, 接过, 拾起, 收下, 进货, 入手}
       in_use:     {使用, 用着, 拔出, 持着, 拿着, 握住, 御使, 施展}
       transformed:{化作, 变成, 觉醒, 升级, 进化, 蜕变, 熔铸}
       damaged:    {破损, 碎裂, 断裂, 损坏, 划痕, 缺口}
       lost:       {丢失, 遗失, 不见, 被夺, 失落, 落入}
       recovered:  {找回, 寻回, 取回, 重获, 拿回}
       discarded:  {丢弃, 抛掉, 扔了, 舍弃, 留下}
       reentered:  {重现, 再次出现, 故地重逢, 复归, 重逢}
  3. per-object phase ledger：对每个 named object，
       phase_seq        = 按出现顺序的相位序列（如 [acquired, in_use, in_use, damaged]）
       phase_dwell_dist = 各相位 token 数占比
       phase_silence_gap= 物件首次提及到最后提及之间「无相位 tag」的连续 CJK 字数最长跨度
  4. 输出 4 信号：
       phase_skip_rate           = 缺少完整阶段链的物件比例（仅有 in_use 单档/无 acquired）
       phase_dwell_imbalance     = in_use 占比 > 0.85 的物件比例
       phase_silence_gap         = 最大「无相位 tag」CJK 跨度 / cluster_cjk
       terminal_phase_consistency= cluster 末段相位 vs 全程主导相位一致比例
  5. 全 advisory · 注入 cluster_emergence_engine 的 brief（snapshot 写到
     `_数据库/.object_biography/<cluster>.json`，被 cluster-save-state step 11 读取，
     非本 scanner 直接耦合）

【北极星】②④⑤ cluster 视野·作者档第一权威·全 advisory·默认 shadow
  OBJECT_BIOGRAPHY_THIN 绝不进 audit_hub.HARD_GATE_CODES。

env OBJECT_BIOGRAPHY_MODE: off / shadow(默认) / active
用法: python object_biography_scanner.py <cluster_draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "OBJECT_BIOGRAPHY_THIN"   # 全 advisory · 永不 hard_gate

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 相位 cue 词典（确定性·偏保守宁可漏报）
PHASE_CUES = {
    "acquired":   ["取出", "取来", "获得", "拿到", "得到", "接过", "拾起", "收下", "入手"],
    "in_use":     ["使用", "用着", "拔出", "持着", "拿着", "握住", "施展", "御使", "运转"],
    "transformed":["化作", "变成", "觉醒", "升级", "进化", "蜕变", "熔铸", "重塑"],
    "damaged":    ["破损", "碎裂", "断裂", "损坏", "缺口", "崩裂"],
    "lost":       ["丢失", "遗失", "不见", "被夺", "失落"],
    "recovered":  ["找回", "寻回", "取回", "重获", "拿回"],
    "discarded":  ["丢弃", "抛掉", "扔了", "舍弃"],
    "reentered":  ["重现", "复归", "重逢", "再次出现"],
}
_ALL_CUES = sorted({c for cues in PHASE_CUES.values() for c in cues}, key=len, reverse=True)

# 默认通用通名（候选排除·避免误识为具名物件）
_GENERIC_NAMES = {
    "东西", "物品", "东西们", "物件", "玩意", "玩意儿", "东西们",
    "手", "脚", "脸", "眼", "心", "口", "嘴", "话", "声音",
}

# 候选具名物件正则：≥2 CJK + 后接「的 / 是 / 被 / 已 / ，/ 。/ 、」或前接动词
# 仅作候选源——经 mentions>=2 + cue 窗口才入选
_OBJ_PATTERN = re.compile(r"([一-鿿]{2,4})(?=[，。、；！？「」“”的是被已])")


def _mode() -> str:
    m = (os.environ.get("OBJECT_BIOGRAPHY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_named_objects_from_db(project_root) -> list[dict]:
    """从 _数据库/物件登记表.json / 物件表.json / items.json 读 named_objects。
    门槛：plot_critical=true 或 mentions>=2 入选。"""
    if not project_root:
        return []
    db = Path(project_root) / "_数据库"
    candidates = [db / "物件登记表.json", db / "物件表.json", db / "items.json"]
    for path in candidates:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        items = obj.get("items") if isinstance(obj.get("items"), list) else []
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or it.get("id")
            if not isinstance(name, str) or len(name.strip()) < 1:
                continue   # 登记表已是作者声明·允许 1 字（剑/弓/伞）
            mentions = it.get("mentions") or 0
            critical = bool(it.get("plot_critical", False))
            if critical or (isinstance(mentions, (int, float)) and mentions >= 2):
                out.append({"name": name.strip(), "plot_critical": critical,
                            "source": "registry"})
        if out:
            return out
    return []


def _harvest_candidates_from_text(text: str, top_n: int = 12) -> list[dict]:
    """无登记表 → 草稿正向扫·宁可漏报。
    候选 = 出现 ≥2 次 + 长度 2-4 + 排除通名 + 至少一处临近 phase cue 窗口（±25 CJK）。"""
    counts: dict[str, int] = {}
    for m in _OBJ_PATTERN.finditer(text):
        nm = m.group(1)
        if nm in _GENERIC_NAMES:
            continue
        counts[nm] = counts.get(nm, 0) + 1
    out = []
    for nm, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n < 2:
            continue
        # 必须至少一处与 phase cue 共现（窗口 ±25 CJK）
        idx = 0
        co_occ = False
        while True:
            i = text.find(nm, idx)
            if i < 0:
                break
            window = text[max(0, i - 25): i + len(nm) + 25]
            if any(cue in window for cue in _ALL_CUES):
                co_occ = True
                break
            idx = i + len(nm)
        if not co_occ:
            continue
        out.append({"name": nm, "plot_critical": False, "source": "text_harvest"})
        if len(out) >= top_n:
            break
    return out


def _phase_sequence(text: str, obj_name: str) -> list[tuple[int, str]]:
    """对每次 obj 出现，在 ±30 CJK 窗口查 phase cue。返回 [(pos, phase), ...]。"""
    out = []
    idx = 0
    while True:
        i = text.find(obj_name, idx)
        if i < 0:
            break
        window = text[max(0, i - 30): i + len(obj_name) + 30]
        chosen = None
        # 按相位优先级搜（acquired/transformed/damaged/lost/recovered/discarded/reentered > in_use 兜底）
        for phase in ("acquired", "transformed", "damaged", "lost",
                      "recovered", "discarded", "reentered", "in_use"):
            for cue in PHASE_CUES[phase]:
                if cue in window:
                    chosen = phase
                    break
            if chosen:
                break
        out.append((i, chosen or "untagged"))
        idx = i + len(obj_name)
    return out


def _per_object_signals(text: str, obj: dict) -> dict:
    seq = _phase_sequence(text, obj["name"])
    tagged = [p for _, p in seq if p != "untagged"]
    if not seq:
        return {"name": obj["name"], "mentions": 0, "phase_seq": [],
                "in_use_ratio": 0.0, "missing_acquired": True,
                "silence_gap_cjk": 0, "terminal_phase": None,
                "dominant_phase": None}
    mentions = len(seq)
    # silence gap = 相邻 obj 提及之间的 max CJK 距离（用全文截取近似）
    silence_gap = 0
    for a, b in zip(seq, seq[1:]):
        # a/b: (pos, phase)
        between = text[a[0] + len(obj["name"]): b[0]]
        cj = _cjk_count(between)
        # 该段内若没有任何 phase cue 触达 → 视作 silence
        if not any(cue in between for cue in _ALL_CUES) and cj > silence_gap:
            silence_gap = cj
    # 占比
    from collections import Counter
    cnt = Counter(p for _, p in seq if p != "untagged")
    total_tagged = sum(cnt.values()) or 1
    in_use_ratio = cnt.get("in_use", 0) / total_tagged
    dominant = cnt.most_common(1)[0][0] if cnt else None
    terminal_phase = next((p for _, p in reversed(seq) if p != "untagged"), None)
    missing_acquired = "acquired" not in {p for _, p in seq}
    # 缺完整阶段链 = 只剩 in_use 单档（mentions ≥3 但 tagged set ⊆ {in_use, untagged}）
    phase_skip = (mentions >= 3 and
                  set(cnt.keys()).issubset({"in_use"}) and
                  cnt.get("in_use", 0) >= 2)
    return {
        "name": obj["name"], "mentions": mentions,
        "phase_seq": [p for _, p in seq],
        "in_use_ratio": round(in_use_ratio, 3),
        "missing_acquired": missing_acquired,
        "phase_skip": phase_skip,
        "silence_gap_cjk": int(silence_gap),
        "terminal_phase": terminal_phase,
        "dominant_phase": dominant,
    }


def _write_snapshot(project_root, cluster_id: str, payload: dict) -> str | None:
    if not project_root:
        return None
    try:
        snap_dir = Path(project_root) / "_数据库" / ".object_biography"
        snap_dir.mkdir(parents=True, exist_ok=True)
        target = snap_dir / f"{cluster_id}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        return str(target)
    except OSError:
        return None


def scan(draft_path, project_root=None, cluster_id: str = "cluster_unknown") -> dict:
    mode = _mode()
    out = {"scanner": "object_biography", "schema_version": "1.0",
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    named = _load_named_objects_from_db(project_root)
    if not named:
        named = _harvest_candidates_from_text(text)
    if not named:
        out["note"] = "无候选具名物件·跳过（北极星②：未登记物件不擅判）"
        out["named_objects_count"] = 0
        return out

    per_obj = [_per_object_signals(text, obj) for obj in named]
    # 仅保留 mentions ≥2 的有效物件
    per_obj = [o for o in per_obj if o["mentions"] >= 2]
    if not per_obj:
        out["note"] = "候选物件均 mentions<2·跳过"
        out["named_objects_count"] = 0
        return out

    n_obj = len(per_obj)
    # 4 信号
    phase_skip_rate = sum(1 for o in per_obj if o["phase_skip"]) / n_obj
    phase_dwell_imbalance = sum(1 for o in per_obj if o["in_use_ratio"] > 0.85) / n_obj
    max_gap = max((o["silence_gap_cjk"] for o in per_obj), default=0)
    phase_silence_gap = round(max_gap / max(cjk, 1), 4)
    # terminal_phase_consistency = terminal == dominant 的比例
    terminal_phase_consistency = (
        sum(1 for o in per_obj
            if o["terminal_phase"] and o["dominant_phase"]
            and o["terminal_phase"] == o["dominant_phase"]) / n_obj
    )

    out["named_objects_count"] = n_obj
    out["per_object"] = per_obj
    out["phase_skip_rate"] = round(phase_skip_rate, 3)
    out["phase_dwell_imbalance"] = round(phase_dwell_imbalance, 3)
    out["phase_silence_gap"] = phase_silence_gap
    out["terminal_phase_consistency"] = round(terminal_phase_consistency, 3)

    # snapshot 落盘（供 cluster_emergence_engine 下卷读）
    snap_payload = {
        "cluster_id": cluster_id, "scanner": "object_biography",
        "named_objects_count": n_obj,
        "phase_skip_rate": out["phase_skip_rate"],
        "phase_dwell_imbalance": out["phase_dwell_imbalance"],
        "phase_silence_gap": out["phase_silence_gap"],
        "terminal_phase_consistency": out["terminal_phase_consistency"],
        "per_object": per_obj,
    }
    snap_path = _write_snapshot(project_root, cluster_id, snap_payload)
    if snap_path:
        out["snapshot_path"] = snap_path

    # 触发条件（占位阈值·shadow 默认）
    flags = []
    if phase_skip_rate >= 0.40:
        flags.append(f"phase_skip_rate={phase_skip_rate:.2f}≥0.40（物件仅 in_use 单档）")
    if phase_dwell_imbalance >= 0.50:
        flags.append(f"phase_dwell_imbalance={phase_dwell_imbalance:.2f}≥0.50（in_use 占比过高）")
    if phase_silence_gap >= 0.40:
        flags.append(f"phase_silence_gap={phase_silence_gap:.2f}≥0.40（物件长时间沉默）")

    if flags:
        msg = "·".join(flags)
        if mode == "active":
            out["violations"].append({
                "kind": "object_biography", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "phase_skip_rate": out["phase_skip_rate"],
                "phase_dwell_imbalance": out["phase_dwell_imbalance"],
                "phase_silence_gap": out["phase_silence_gap"],
                "named_objects_count": n_obj,
                "_doc": "Kopytoff 1986·物件传记相位·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] object_biography: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="物件生命传记相位账本 (advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-id", default="cluster_unknown")
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, cluster_id=args.cluster_id)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
