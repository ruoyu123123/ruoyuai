#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_write_gate.py — 写前 Evolution Gate + 声明式豁免（A2 · 2026-07-07）

借鉴 PlotPilot gate_service（`_temp_research/reference_repos/PlotPilot_api` · research/
open_source_writing_systems_round2.md A2）：一致性栈此前全是**写后**检测——穿帮要等
「写完 20k 字再修」；本 gate 把「当前世界状态/人物卡 × 选中 brief」的校验提前到
**build_manifest 之前**，写前拦截未声明的意外穿帮。

链路位置（cluster-write.plan.json step1）：
    world_evolution_apply_card（brief 落库 事件簇.json）
    → **pre_write_gate（本脚本）**
    → auto_fate_draw → build_manifest

四项检查（数据源均实地核查过真实字段名）：
  ① dead_character（blocking）：brief 结构化出场名单（characters_focus /
     scene_storyboard[].characters/participants/focal_character）含已死亡角色。
     死亡权威源 = 人物卡.json characters[].status（archivist 合约枚举
     alive/dead/missing/unknown·仅 dead 及中文死亡词算死）∪ character_arc_state.json
     （复用 cluster_emergence_engine._dead_actor_names·与 P0-03b 死角色 gate 同源）。
     实地核查：世界状态.json 无标准化死亡记录字段（protagonist_state/factions_state/
     consequence_tracker 均不承载生死），故不臆造该通路。
     brief 正文（scope_summary 等）**提及**死角色 → 只 warning（回忆/复仇动机是
     合法叙事，不上台不拦）。
  ② destroyed_item（blocking）：brief anchor_props 引用已销毁/失去的道具
     （道具.json items[].status ∈ destroyed/lost/已销毁/丢失…；当前 apply_archive
     不写该字段 → 无声明即全过，字段出现即生效，前向契约）。
  ③ locked_fact_conflict（blocking）：brief 文本与锁定事实**恒定数值**直接冲突
     （复用 locked_fact_cross_scene_scanner 的单位集/数字解析/同句锚定——
     北极星铁律同享：只抓恒定量，绝不碰单调递增品级）。锁定事实源 =
     人物卡.characters[].locked_facts ∪ 事件簇.clusters[<当前之前>].locked_facts。
  ④ duplicate_event（warning 只记不拦）：brief scope_summary 与已 completed ME
     （大势卡.json major_events status=="completed"·fate_engine 同口径）或已完成
     cluster scope_summary 高词面重叠（字符 bigram containment ≥ 0.6）。

声明式豁免（北极星⑤ · 创作声明权）：
  brief 可选字段 gate_waivers: [{"type","target","reason"}]（schema 见
  event_cluster_schema.json）。type 支持叙事手法别名（flashback/闪回/
  ambiguous_fate/模糊生死/time_skip/时间跳跃…）。gate 命中且有对应豁免 →
  放行并留痕 waived[]。blocking 类豁免必须带非空 target（防空白支票整类旁路）；
  duplicate_event 可只声明 type。gate 只拦「未声明的意外穿帮」——豁免即创作声明，
  不做二次裁决。

输出（plan expected_outputs 代理产物 · 含 skip 场景恒落盘）：
    _数据库/.wal/cluster_<key>_pre_write_gate.json

退出码：
    0 = pass / warning-only / waived / cluster_001 优雅 skip
    2 = blocking 非空（[FATAL] 走 stderr · 硬停不进 build_manifest）
        —— 写前拒绝，不是审计 issue：**不新增 hard_gate code**（19 码清单零变动）。

用法：
    python core/scripts/pre_write_gate.py <project_root> --next-key <key>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cluster_lookup as cl  # noqa: E402
import cluster_emergence_engine as cee  # noqa: E402  (_dead_actor_names 复用·P0-03b 同源)
import locked_fact_cross_scene_scanner as lfs  # noqa: E402  (恒定数值通路复用)
from atomic_json import load_json  # noqa: E402

DB_DIR = "_数据库"
EVENT_CLUSTER_FILE = "事件簇.json"
CHARACTER_CARD_FILE = "人物卡.json"
ITEM_FILE = "道具.json"
FATE_FILE = "大势卡.json"

# archivist 合约（.claude/agents/novel-archivist.md）status 枚举 = alive/dead/missing/unknown。
# missing/unknown = 生死模糊 → 不拦（AmbiguousFate 本身就是合法叙事状态）。
# 中文变体防手改库（与 cee._dead_actor_names 的 dead_keywords 对齐）。
_DEAD_STATUS_WORDS = {"dead", "died", "deceased", "死亡", "已死亡", "已死", "牺牲", "去世"}
_DESTROYED_STATUS_WORDS = {
    "destroyed", "lost", "forfeited", "销毁", "已销毁", "损毁", "已损毁", "丢失", "已丢失", "遗失",
}
_COMPLETED_CLUSTER_STATUS = {"completed", "done", "已完成"}

# 豁免 type 归一表：叙事手法声明（[TimeSkip][AmbiguousFate] 范式）→ 对应检查项。
_WAIVER_ALIASES = {
    "dead_character": {
        "dead_character", "deadcharacter", "dead", "ambiguous_fate", "ambiguousfate",
        "flashback", "闪回", "回忆", "梦境", "亡灵", "模糊生死", "死亡角色",
    },
    "destroyed_item": {
        "destroyed_item", "item_destroyed", "destroyed", "道具销毁", "销毁道具", "遗物",
    },
    "locked_fact_conflict": {
        "locked_fact", "locked_fact_conflict", "设定冲突", "time_skip", "timeskip",
        "时间跳跃", "retcon",
    },
    "duplicate_event": {
        "duplicate_event", "重复事件", "echo", "echo_scene", "呼应", "callback",
    },
}
# blocking 类豁免必须点名 target（防空白支票整类旁路）；warning 类可只声明 type。
_BLOCKING_TYPES = {"dead_character", "destroyed_item", "locked_fact_conflict"}

_DUP_CONTAINMENT_THRESHOLD = 0.6
_MIN_BIGRAMS_FOR_DUP = 6

# 出场名单条目规整：截掉括号注释/职务后缀（如「陈默(主角)」「老周/门房」→ 主名）。
_CAST_DELIM_RE = re.compile(r"[（(·/、,，;；:：\s].*$")


# ───────────────────────── io helpers ─────────────────────────
def _load_json(path: Path, default=None):
    return load_json(path, default=default)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def gate_report_path(project_root: Path, cluster_id: str) -> Path:
    return project_root / DB_DIR / ".wal" / f"{cluster_id}_pre_write_gate.json"


# ───────────────────────── brief 取材 ─────────────────────────
def _find_brief(project_root: Path, cluster_id: str) -> dict | None:
    data = _load_json(project_root / DB_DIR / EVENT_CLUSTER_FILE, {})
    clusters = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(clusters, list):
        return None
    for c in clusters:
        if isinstance(c, dict) and cl.normalize_cluster_id(c.get("cluster_id")) == cluster_id:
            return c
    return None


def _canon_cast_name(raw) -> str:
    return _CAST_DELIM_RE.sub("", str(raw or "").strip())


def _brief_cast(brief: dict) -> list[str]:
    """结构化出场名单（上台=拦截依据；文本提及只 warning）。"""
    cast: list[str] = []
    for n in brief.get("characters_focus") or []:
        if isinstance(n, str) and n.strip():
            cast.append(n.strip())
    for scene in brief.get("scene_storyboard") or []:
        if not isinstance(scene, dict):
            continue
        for key in ("characters", "participants"):
            for n in scene.get(key) or []:
                if isinstance(n, str) and n.strip():
                    cast.append(n.strip())
        focal = scene.get("focal_character")
        if isinstance(focal, str) and focal.strip():
            cast.append(focal.strip())
    seen, out = set(), []
    for n in cast:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _brief_text(brief: dict) -> str:
    """brief 全部叙事文本（locked_fact 数值比对 + 提及类 warning 用）。"""
    parts = []
    for key in ("title", "scope_summary"):
        v = brief.get(key)
        if isinstance(v, str):
            parts.append(v)
    for scene in brief.get("scene_storyboard") or []:
        if not isinstance(scene, dict):
            continue
        for key in ("title", "scene_title", "summary", "scene_goal", "scene_purpose"):
            v = scene.get(key)
            if isinstance(v, str):
                parts.append(v)
    return "。".join(parts)


# ───────────────────────── ① 死亡角色 ─────────────────────────
def _dead_registry(project_root: Path) -> list[dict]:
    """[{id, name, source}]。人物卡 status ∪ character_arc_state（cee 同源 helper）。"""
    dead: list[dict] = []
    cards = (_load_json(project_root / DB_DIR / CHARACTER_CARD_FILE, {}) or {}).get("characters") or []
    for c in cards:
        if not isinstance(c, dict):
            continue
        status = str(c.get("status") or "").strip().lower()
        if status in _DEAD_STATUS_WORDS or any(w in status for w in _DEAD_STATUS_WORDS):
            dead.append({"id": str(c.get("id") or ""), "name": str(c.get("name") or ""),
                         "source": f"{CHARACTER_CARD_FILE}.status={c.get('status')}"})
    known = {d["name"] for d in dead if d["name"]}
    for name in sorted(cee._dead_actor_names(project_root)):
        if name not in known:
            dead.append({"id": "", "name": name, "source": "character_arc_state.json"})
    return [d for d in dead if d["name"] or d["id"]]


def check_dead_characters(brief: dict, dead: list[dict]) -> tuple[list, list]:
    """返回 (blocking findings, warning findings)。"""
    findings, warnings = [], []
    cast = _brief_cast(brief)
    text = _brief_text(brief)
    matched_names = set()
    for entry in cast:
        canon = _canon_cast_name(entry)
        for d in dead:
            if (canon and (canon == d["name"] or canon == d["id"])) or \
                    (entry and entry == d["id"]):
                findings.append({
                    "type": "dead_character",
                    "target": d["name"] or d["id"],
                    "detail": f"brief 出场名单含已死亡角色「{d['name'] or d['id']}」"
                              f"（名单条目「{entry}」· 死亡记录源: {d['source']}）",
                    "blocking": True,
                })
                matched_names.add(d["name"])
    for d in dead:
        name = d["name"]
        if name and len(name) >= 2 and name not in matched_names and name in text:
            warnings.append({
                "type": "dead_character_mention",
                "target": name,
                "detail": f"brief 文本提及已死亡角色「{name}」（未上台·回忆/动机类提及合法·只记不拦）",
                "blocking": False,
            })
    return findings, warnings


# ───────────────────────── ② 销毁道具 ─────────────────────────
def _destroyed_items(project_root: Path) -> list[dict]:
    items = (_load_json(project_root / DB_DIR / ITEM_FILE, {}) or {}).get("items") or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        status = str(it.get("status") or "").strip().lower()
        if status and (status in _DESTROYED_STATUS_WORDS
                       or any(w in status for w in _DESTROYED_STATUS_WORDS)):
            out.append({"id": str(it.get("id") or ""), "name": str(it.get("name") or ""),
                        "status": it.get("status")})
    return out


def check_destroyed_items(brief: dict, destroyed: list[dict]) -> list:
    findings = []
    props = [p for p in (brief.get("anchor_props") or []) if isinstance(p, str)]
    for prop in props:
        for it in destroyed:
            hit_name = it["name"] and it["name"] in prop
            hit_id = it["id"] and len(it["id"]) > 2 and it["id"] in prop
            if hit_name or hit_id:
                findings.append({
                    "type": "destroyed_item",
                    "target": it["name"] or it["id"],
                    "detail": f"brief anchor_props「{prop}」引用已销毁/失去的道具"
                              f"「{it['name'] or it['id']}」（道具.json status={it['status']}）",
                    "blocking": True,
                })
    return findings


# ───────────────────────── ③ 锁定事实恒定数值冲突 ─────────────────────────
def _gather_locked_facts(project_root: Path, current_num: int) -> list[tuple[str, str]]:
    """[(角色名, fact)]。人物卡.locked_facts ∪ 事件簇.clusters[<当前>].locked_facts。"""
    pairs: list[tuple[str, str]] = []
    cards = (_load_json(project_root / DB_DIR / CHARACTER_CARD_FILE, {}) or {}).get("characters") or []
    id_map = {}
    for c in cards:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "")
        if c.get("id"):
            id_map[str(c["id"])] = name
        for lf in c.get("locked_facts") or []:
            if isinstance(lf, dict) and lf.get("fact") and name:
                pairs.append((name, str(lf["fact"])))
    data = _load_json(project_root / DB_DIR / EVENT_CLUSTER_FILE, {}) or {}
    for cluster in data.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        num = cl.cluster_num(cl.normalize_cluster_id(cluster.get("cluster_id")))
        if num is None or num >= current_num:
            continue  # 只取当前 cluster 之前已写块锁下的事实
        for lf in cluster.get("locked_facts") or []:
            if not isinstance(lf, dict) or not lf.get("fact"):
                continue
            subject = str(lf.get("subject") or "")
            name = id_map.get(subject, subject)
            if name:
                pairs.append((name, str(lf["fact"])))
    return pairs


def check_locked_fact_conflicts(project_root: Path, brief: dict, current_num: int) -> list:
    """恒定数值通路（复用 locked_fact_cross_scene_scanner 单位集/解析/同句锚定）。

    fact 声明「N<恒定单位>」且 brief 文本同角色同句出现 M<同单位>（M≠N）→ blocking。
    北极星铁律同享：单位集只收恒定量（保底「岁」·项目 locked_fact_units.json opt-in），
    单调递增品级被 lfs._MONOTONIC_BLOCKLIST 兜底剔除——升阶合法成长永不拦。
    """
    text = _brief_text(brief)
    if not text:
        return []
    units = lfs._load_unit_set(project_root)
    unit_re = lfs._make_unit_re(units)
    findings = []
    reported = set()
    for name, fact in _gather_locked_facts(project_root, current_num):
        if name not in text or (name, fact) in reported:
            continue
        fact_body = fact[len(name):] if fact.startswith(name) else fact
        for fact_m in unit_re.finditer(fact_body):
            fact_unit = fact_m.group(2)
            fact_val = lfs._cn_to_int(fact_m.group(1))
            if fact_val is None:
                continue
            for pos, ctx_num, ctx_unit in lfs.extract_numeric_facts_near(text, name, unit_re):
                if ctx_unit != fact_unit:
                    continue
                ctx_val = lfs._cn_to_int(ctx_num)
                if ctx_val is not None and ctx_val != fact_val:
                    findings.append({
                        "type": "locked_fact_conflict",
                        "target": name,
                        "detail": f"brief 与锁定事实直接冲突：「{fact}」声明 "
                                  f"{fact_val}{fact_unit}，brief 写「{ctx_num}{ctx_unit}」"
                                  f"（…{text[max(0, pos - 20):pos + 20]}…）",
                        "blocking": True,
                    })
                    reported.add((name, fact))
                    break
            if (name, fact) in reported:
                break
    return findings


# ───────────────────────── ④ 重复事件嫌疑 ─────────────────────────
def _bigrams(s: str) -> set:
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _containment(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if min(len(ba), len(bb)) < _MIN_BIGRAMS_FOR_DUP:
        return 0.0
    return len(ba & bb) / min(len(ba), len(bb))


def check_duplicate_events(project_root: Path, brief: dict, current_num: int) -> list:
    """warning 只记不拦（呼应/回环是合法叙事技法·词面重叠只是嫌疑信号）。"""
    scope = str(brief.get("scope_summary") or "")
    if not scope:
        return []
    corpus: list[tuple[str, str]] = []
    fate = _load_json(project_root / DB_DIR / FATE_FILE, {}) or {}
    pool = fate.get("major_events") or []
    for me in pool:
        if isinstance(me, dict) and me.get("status") == "completed":
            text = str(me.get("title") or me.get("description") or me.get("summary") or "")
            if text:
                corpus.append((f"ME:{me.get('id') or '?'}", text))
    data = _load_json(project_root / DB_DIR / EVENT_CLUSTER_FILE, {}) or {}
    for cluster in data.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        cid = cl.normalize_cluster_id(cluster.get("cluster_id"))
        num = cl.cluster_num(cid)
        if num is None or num >= current_num:
            continue
        if str(cluster.get("status") or "") in _COMPLETED_CLUSTER_STATUS:
            summary = str(cluster.get("scope_summary") or "")
            if summary:
                corpus.append((f"cluster:{cid}", summary))
    warnings = []
    for ref, text in corpus:
        score = _containment(scope, text)
        if score >= _DUP_CONTAINMENT_THRESHOLD:
            warnings.append({
                "type": "duplicate_event",
                "target": ref,
                "detail": f"brief scope_summary 与已完成事件 {ref} 高词面重叠"
                          f"（bigram containment={score:.2f} ≥ {_DUP_CONTAINMENT_THRESHOLD}）"
                          f"·重复事件嫌疑·只记不拦",
                "blocking": False,
                "similarity": round(score, 3),
            })
    return warnings


# ───────────────────────── 声明式豁免 ─────────────────────────
def _normalize_waiver_type(raw: str) -> str | None:
    key = str(raw or "").strip().lower().replace("-", "_")
    for canonical, aliases in _WAIVER_ALIASES.items():
        if key == canonical or key in aliases:
            return canonical
    return None


def _validate_waivers(raw_waivers) -> tuple[list[dict], list[dict]]:
    """→ (合法豁免[含 _canonical_type], 非法豁免留痕)。"""
    valid, invalid = [], []
    for w in raw_waivers or []:
        if not isinstance(w, dict):
            invalid.append({"waiver": w, "why": "非对象"})
            continue
        canonical = _normalize_waiver_type(w.get("type"))
        reason = str(w.get("reason") or "").strip()
        target = str(w.get("target") or "").strip()
        if canonical is None:
            invalid.append({"waiver": w, "why": f"未知 type「{w.get('type')}」"})
            continue
        if not reason:
            invalid.append({"waiver": w, "why": "缺创作理由 reason（声明必须落字）"})
            continue
        if canonical in _BLOCKING_TYPES and not target:
            invalid.append({"waiver": w, "why": "blocking 类豁免必须点名 target（禁整类空白支票）"})
            continue
        valid.append({**w, "_canonical_type": canonical})
    return valid, invalid


def _waiver_matches(waiver: dict, finding: dict) -> bool:
    ftype = finding["type"]
    canonical = waiver["_canonical_type"]
    if canonical != ftype and not (canonical == "dead_character"
                                   and ftype == "dead_character_mention"):
        return False
    wt = str(waiver.get("target") or "").strip()
    ft = str(finding.get("target") or "").strip()
    if not wt:
        return ftype == "duplicate_event"  # warning 类允许 type-only
    return bool(ft) and (wt == ft or wt in ft or ft in wt)


def apply_waivers(findings: list[dict], waivers: list[dict]) -> tuple[list, list]:
    """命中且有对应豁免 → 移入 waived（放行留痕）。返回 (剩余, waived)。"""
    remaining, waived = [], []
    for f in findings:
        hit = next((w for w in waivers if _waiver_matches(w, f)), None)
        if hit is None:
            remaining.append(f)
        else:
            waived.append({**f, "waived_by": {
                "type": hit.get("type"), "target": hit.get("target"),
                "reason": hit.get("reason"),
            }})
    return remaining, waived


# ───────────────────────── main ─────────────────────────
def run_gate(project_root: Path, cluster_id: str) -> dict:
    num = cl.cluster_num(cluster_id)
    brief = _find_brief(project_root, cluster_id)
    report = {
        "_schema": "pre_write_gate_v1",
        "tool": "pre_write_gate",
        "cluster_id": cluster_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "skipped": False,
        "blocking": [],
        "warnings": [],
        "waived": [],
        "invalid_waivers": [],
        "verdict": "pass",
        "_doc": "写前 Evolution Gate（A2·PlotPilot gate_service 范式）·blocking=写前拒绝非审计 issue"
                "（不新增 hard_gate code）·豁免=创作声明权（北极星⑤）",
    }
    if brief is None:
        report["blocking"] = [{
            "type": "brief_missing",
            "target": cluster_id,
            "detail": f"{EVENT_CLUSTER_FILE} 中找不到 {cluster_id} 的 brief——"
                      f"world_evolution_apply_card 应先落库（流水线顺序契约破损）",
            "blocking": True,
        }]
        report["verdict"] = "blocked"
        return report

    waivers, invalid = _validate_waivers(brief.get("gate_waivers"))
    report["invalid_waivers"] = invalid
    report["waivers_declared"] = len(waivers)

    dead_findings, dead_mentions = check_dead_characters(brief, _dead_registry(project_root))
    item_findings = check_destroyed_items(brief, _destroyed_items(project_root))
    fact_findings = check_locked_fact_conflicts(project_root, brief, num or 0)
    dup_warnings = check_duplicate_events(project_root, brief, num or 0)

    blocking, waived_b = apply_waivers(dead_findings + item_findings + fact_findings, waivers)
    warnings, waived_w = apply_waivers(dead_mentions + dup_warnings, waivers)

    report["blocking"] = blocking
    report["warnings"] = warnings
    report["waived"] = waived_b + waived_w
    report["verdict"] = "blocked" if blocking else "pass"
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="写前 Evolution Gate（cluster_002+ · build_manifest 之前）")
    ap.add_argument("project_root")
    ap.add_argument("--next-key", required=True, dest="next_key")
    args = ap.parse_args(argv)

    project_root = Path(args.project_root)
    if not project_root.exists():
        sys.stderr.write(f"[FATAL] pre_write_gate: 项目路径不存在: {project_root}\n")
        return 2
    cluster_id = cl.normalize_cluster_id(args.next_key)
    if cluster_id is None:
        sys.stderr.write(f"[FATAL] pre_write_gate: 无法解析 --next-key「{args.next_key}」\n")
        return 2

    if cl.cluster_num(cluster_id) == 1:
        # cluster_001 首块无 brief 选择场景 → 优雅 skip（报告仍落盘满足 plan expected_outputs）
        report = {
            "_schema": "pre_write_gate_v1",
            "tool": "pre_write_gate",
            "cluster_id": cluster_id,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "skipped": True,
            "skip_reason": "cluster_001 首块无前置走向卡/brief 选择场景·写前 gate 不适用",
            "blocking": [], "warnings": [], "waived": [], "invalid_waivers": [],
            "verdict": "skipped",
        }
        _write_json(gate_report_path(project_root, cluster_id), report)
        print(f"[pre_write_gate] {cluster_id} 首块 skip（报告已留痕）")
        return 0

    report = run_gate(project_root, cluster_id)
    _write_json(gate_report_path(project_root, cluster_id), report)

    if report["blocking"]:
        for f in report["blocking"]:
            sys.stderr.write(f"[FATAL] pre_write_gate {f['type']}: {f['detail']}\n")
        sys.stderr.write(
            f"[FATAL] pre_write_gate: {cluster_id} 写前拦截 {len(report['blocking'])} 项——"
            f"修正 brief 或在 brief.gate_waivers 声明叙事手法豁免后重跑\n")
        return 2

    bits = []
    if report["waived"]:
        bits.append(f"{len(report['waived'])} 项声明豁免放行")
    if report["warnings"]:
        bits.append(f"{len(report['warnings'])} 项 warning 留痕")
    print(f"[pre_write_gate] {cluster_id} PASS" + (f"（{'·'.join(bits)}）" if bits else ""))
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
