#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""volume_transition_scanner.py — 卷过渡硬重置/钩零命中哨兵（cross-cluster · advisory · 2026-06-20）

【缺口】R7 W2·调研网文卷过渡惯例（卷=阶段触发点·CLAUDE.md 卷阶段结构）：弱模型常在卷过渡处
出三种翻车——
  ① 上卷末 cluster 给的【下卷钩】在下卷首 cluster 零兑现（钩零命中）
  ② 上下卷 cast(角色) 和 setting(地点/世界状态) overlap=0（硬重置·像换了本书）
  ③ 下卷首 cluster 的 scene1 缺新 cast/setting 钩（开篇无锚点）
volume_arc_drift 已查【卷内大势漂移】·本 scanner 与之正交查【卷间过渡缝合】。

【做法 · cross-cluster aggregator（与 volume_arc_drift 同层）】（确定性·零 LLM）：
  · 数据源：事件簇.json（cluster.volume / scope_summary / scene_storyboard / volume_transition_hooks）
       + 故事块摘要.json（已写 cluster 的 cast / setting 摘要）
  · 规则 ①：检测最近一次 vol N→N+1 过渡·上卷末 cluster 若有 volume_transition_hooks.close.hook_text
       而下卷首 cluster scope_summary + scene_storyboard.scene1 覆盖率 < 30% → advisory「下卷钩零命中」。
       默认（无真 embedding 后端）关键词 2-gram 字面重叠算覆盖率；_has_real_embedding_backend()
       真语义后端就绪时改用 hook_text vs 下卷首开篇文本 embedding 余弦相似度替代字面重叠（同义改写
       零容错：hook 写「黑龙将苏醒」下卷首写「巨龙睁开眼」字面零重叠但语义一致），embedding 不可用
       /维度不一致/未配后端 → 回退字面重叠，逐字节零回归。close_hook_match_method 字段标注来源。
  · 规则 ②：cast overlap = |last_vol_cast ∩ next_vol_cast| / |last_vol_cast|
       overlap == 0 → advisory「卷间 cast 硬重置」（卷=阶段触发但角色不该全清空）——精确集合运算，
       非语义模糊判断，不模型化。
  · 规则 ③：next_vol_first_cluster.scene_storyboard.scene1 中：未在上卷出现的新 cast 数 = 0
       且 setting 描述不引入新元素 → advisory「卷首 scene1 缺新钩」——固定 regex 锚词格式匹配，
       非语义模糊判断，不模型化。

【北极星⑤ 顾问非法官】卷间过渡是创作选择（time-skip / 全新副本 / 全新身份合法）·writer 有理由
  可豁免 → 永远 advisory，code VOLUME_TRANSITION_*  **绝不进 audit_hub.HARD_GATE_CODES**。
  env VOLUME_TRANSITION_MODE: off / shadow(默认·只记不判·零回归) / active。

用法：python volume_transition_scanner.py <project> [--last-n N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


HOOK_MISS_CODE = "VOLUME_TRANSITION_HOOK_MISS"
HARD_RESET_CODE = "VOLUME_TRANSITION_HARD_RESET"
EMPTY_OPEN_CODE = "VOLUME_TRANSITION_EMPTY_OPEN"

HOOK_COVERAGE_FLOOR = 0.30   # < 30% 关键词重叠 = 钩零命中
HARD_RESET_FLOOR = 0.0       # cast overlap == 0 = 硬重置


def _mode() -> str:
    m = (os.environ.get("VOLUME_TRANSITION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。

    与 topic_drift_scanner._has_real_embedding_backend 同口径（本仓约定：每个消费
    embedding 的 scanner 自带一份，不互相 import）。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


def _embed_or_none(text: str):
    """真语义 embedding；embedding_store 不可用/编码异常/空文本 → None（调用方回退字面 bigram）。"""
    if not text or not str(text).strip():
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import compute_embedding
        emb = compute_embedding(text)
        return emb if emb else None
    except Exception:
        return None


def _cosine_or_none(v1, v2) -> "float | None":
    """维度不一致/任一为 None → None（调用方回退字面 bigram，不误判）。"""
    if v1 is None or v2 is None or len(v1) != len(v2):
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import cosine_similarity
        return cosine_similarity(v1, v2)
    except Exception:
        return None


def _load(p: Path, default=None):
    try:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _kw(text: str) -> set:
    """中文 2-gram + 英数 token 关键词集（与 volume_arc_drift._kw 同口径）。"""
    if not text:
        return set()
    out = set()
    for tok in re.findall(r"[A-Za-z0-9_]+", str(text)):
        if len(tok) >= 2:
            out.add(tok.lower())
    for seg in re.findall(r"[一-鿿]+", str(text)):
        for i in range(len(seg) - 1):
            out.add(seg[i:i + 2])
    return out


def _cluster_vol(c: dict):
    """与 volume_arc_drift._cluster_vol 同口径。"""
    v = c.get("vol")
    if v is None:
        v = c.get("volume")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    m = re.search(r"V?(\d+)", str(c.get("parent_me") or ""))
    return int(m.group(1)) if m else None


def _cluster_status_done(c: dict) -> bool:
    if c.get("status") in ("done", "已完成"):
        return True
    cr = c.get("chapter_range")
    return isinstance(cr, list) and len(cr) == 2 and isinstance(cr[0], int)


def _extract_cast(c: dict, ledger_by_cid: dict) -> set:
    """从 cluster + 摘要里抽 cast 名集（防御性·缺则空集）。"""
    cast = set()
    raw_cast = c.get("cast") or c.get("characters") or []
    if isinstance(raw_cast, list):
        for n in raw_cast:
            if isinstance(n, str) and n.strip():
                cast.add(n.strip())
            elif isinstance(n, dict) and n.get("name"):
                cast.add(n["name"])
    # 退到摘要
    lc = ledger_by_cid.get(str(c.get("cluster_id"))) or {}
    if isinstance(lc, dict):
        led_cast = lc.get("cast") or lc.get("characters") or []
        if isinstance(led_cast, list):
            for n in led_cast:
                if isinstance(n, str) and n.strip():
                    cast.add(n.strip())
    return cast


def _close_hook(c: dict) -> str:
    """读上卷末 cluster 的 volume_transition_hooks.close.hook_text（若有）。"""
    h = c.get("volume_transition_hooks") or {}
    if isinstance(h, dict):
        close = h.get("close") or {}
        if isinstance(close, dict):
            t = close.get("hook_text") or close.get("text")
            if isinstance(t, str):
                return t
    return ""


def _scene1_text(c: dict) -> str:
    """从 cluster scene_storyboard[0] 抽文本（防御）。"""
    sb = c.get("scene_storyboard") or []
    if isinstance(sb, list) and sb:
        s0 = sb[0]
        if isinstance(s0, dict):
            return " ".join(str(s0.get(k, "")) for k in ("summary", "scene_summary", "beat", "purpose", "setting"))
        if isinstance(s0, str):
            return s0
    return str(c.get("scope_summary") or "")


def scan(project_root: Path) -> dict:
    mode = _mode()
    out = {"scanner": "volume_transition", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "issues": [], "warning": None, "verdict": "PASS"}
    if mode == "off":
        return out
    db = project_root / "_数据库"
    if not db.exists():
        out["note"] = "无 _数据库·skip"
        return out
    shijianji = _load(db / "事件簇.json", {"clusters": []}) or {"clusters": []}
    ledger = _load(db / "故事块摘要.json", {"clusters": []}) or {"clusters": []}
    ledger_by_cid = {str(x.get("cluster_id")): x
                     for x in (ledger.get("clusters") or []) if isinstance(x, dict)}

    clusters = [c for c in (shijianji.get("clusters") or []) if isinstance(c, dict)]
    if not clusters:
        out["note"] = "无 cluster·skip"
        return out

    # 找过渡点：相邻已写 vol N → vol N+1
    by_vol = {}
    for c in clusters:
        v = _cluster_vol(c)
        if v is None:
            continue
        by_vol.setdefault(v, []).append(c)
    vols_sorted = sorted(by_vol.keys())
    if len(vols_sorted) < 2:
        out["note"] = "卷数 < 2·无过渡点·skip"
        return out

    # 取最近一次相邻过渡（last vol_done & next vol 首 cluster 存在）
    transition_found = None
    for i in range(len(vols_sorted) - 1):
        last_v, next_v = vols_sorted[i], vols_sorted[i + 1]
        if next_v - last_v != 1:
            continue
        last_done = [c for c in by_vol[last_v] if _cluster_status_done(c)]
        next_any = by_vol[next_v]
        if last_done and next_any:
            transition_found = (last_v, next_v, last_done, next_any)

    if not transition_found:
        out["note"] = "无可用相邻卷过渡（last_vol 无 done · 或下卷无 cluster）·skip"
        return out
    last_v, next_v, last_done, next_any = transition_found
    out["last_vol"] = last_v
    out["next_vol"] = next_v

    last_finale = last_done[-1]
    next_first = next_any[0]
    out["last_finale_cluster_id"] = last_finale.get("cluster_id")
    out["next_first_cluster_id"] = next_first.get("cluster_id")

    issues = []

    # 规则 ①：下卷钩零命中
    close_hook = _close_hook(last_finale)
    if close_hook:
        open_text = _scene1_text(next_first) + " " + str(next_first.get("scope_summary") or "")
        # 真语义后端就绪 → hook_text vs 下卷首开篇文本 embedding 余弦相似度替代字面重叠；
        # 否则（默认）保留关键词 2-gram 重叠原样不动
        close_hook_match_method = "bigram_keyword_overlap"
        coverage = None
        if _has_real_embedding_backend():
            sim = _cosine_or_none(_embed_or_none(close_hook), _embed_or_none(open_text))
            if sim is not None:
                coverage = sim
                close_hook_match_method = "embedding_cosine"
        if coverage is None:   # 语义路径不可用（未配后端/编码失败/维度不一致）→ 回退字面重叠
            hook_kw = _kw(close_hook)
            open_kw = _kw(open_text)
            coverage = (len(hook_kw & open_kw) / len(hook_kw)) if hook_kw else 1.0
        out["close_hook_coverage"] = round(coverage, 2)
        out["close_hook_match_method"] = close_hook_match_method
        if coverage < HOOK_COVERAGE_FLOOR:
            issues.append({
                "code": HOOK_MISS_CODE, "gate_level": "advisory", "severity": "minor",
                "coverage": round(coverage, 2),
                "match_method": close_hook_match_method,
                "msg": (f"⚠️ vol{last_v}→vol{next_v} 过渡：上卷末钩 close.hook_text 覆盖率 "
                        f"{coverage:.0%} < {HOOK_COVERAGE_FLOOR:.0%}·下卷首零兑现·建议下卷首"
                        f" scene1 回应上卷末钩 keywords"),
            })

    # 规则 ②：cast 硬重置
    last_cast = set()
    for c in last_done:
        last_cast |= _extract_cast(c, ledger_by_cid)
    next_cast = _extract_cast(next_first, ledger_by_cid)
    overlap = len(last_cast & next_cast)
    cast_overlap_ratio = (overlap / len(last_cast)) if last_cast else None
    out["cast_overlap_ratio"] = (round(cast_overlap_ratio, 2)
                                  if cast_overlap_ratio is not None else None)
    out["last_vol_cast_size"] = len(last_cast)
    out["next_first_cast_size"] = len(next_cast)
    if last_cast and next_cast and overlap == 0:
        issues.append({
            "code": HARD_RESET_CODE, "gate_level": "advisory", "severity": "minor",
            "overlap": 0,
            "msg": (f"⚠️ vol{last_v}→vol{next_v} 过渡：cast overlap=0（上卷 {len(last_cast)} 角色 / "
                    f"下卷首 {len(next_cast)} 角色全无重叠）·像换了本书·建议至少 1 个延续角色"),
        })

    # 规则 ③：卷首 scene1 缺新钩
    new_cast_in_first = next_cast - last_cast
    scene1_text = _scene1_text(next_first)
    has_setting_anchor = bool(re.search(
        r"(进入|来到|踏入|抵达|新的|从未|第一次见|第一次到|第一次进|陌生的)", scene1_text))
    out["new_cast_in_open"] = len(new_cast_in_first)
    out["has_setting_anchor"] = has_setting_anchor
    if next_cast and len(new_cast_in_first) == 0 and not has_setting_anchor and scene1_text:
        issues.append({
            "code": EMPTY_OPEN_CODE, "gate_level": "advisory", "severity": "minor",
            "msg": (f"⚠️ vol{last_v}→vol{next_v} 过渡：下卷首 scene1 既无新 cast 也无 setting 锚词·"
                    f"卷开篇可能缺新钩·建议引入新角色 / 新地点 / 新世界状态信号"),
        })

    out["issues"] = issues
    if issues:
        if mode == "active":
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = "·".join(i["msg"] for i in issues)
        else:
            for i in issues:
                print(f"[SHADOW] volume_transition: {i['msg']} — 不上报", file=sys.stderr)
            out["issues"] = []   # shadow: 不暴露 issue 给 audit_hub
    return out


def main():
    ap = argparse.ArgumentParser(description="卷过渡硬重置/钩零命中哨兵(advisory · cross-cluster)")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)   # 兼容编排器统一签名
    args = ap.parse_args()
    project_root = Path(args.project).resolve()
    result = scan(project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result.get("issues") else 0)


if __name__ == "__main__":
    main()
