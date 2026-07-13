#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""world_register_drift_scanner.py — Le Guin Register Drift Scanner
(advisory · cluster · 2026-06-20 · R8 W4 Batch-F · L18 STRONG)

【缺口】R8 联网调研：Le Guin 1973《From Elfland to Poughkeepsie》+ Substack 2024
Witcher tonal jolt 实证：题材语域（register）漂移 ≠ 时代轴错位（anachronism）。
R6 anachronism_scanner 覆盖『古代场景出现现代物件』时代轴；本 scanner 补正交盲区：
**当代俚语（yyds/破防/老六）/ 现代管理黑话（KPI/OKR/复盘）/ 反向高语域误用（朕/陛下用于
现代/科幻场景）打破题材 register 一致性**——读者能即时感受到「tonal jolt」语气脱节。

【与 R6 anachronism 显式去重】（critical · 必须能并存）：
  - R6 anachronism: 时代轴错位（古代场景出现现代器物/手机/汽车）= 时代背景下不该有的实物
  - L18 register_drift: 语域漂移（当代俚语/工程黑话用于不合 register 的场景）= 用词风格脱节
  - 二者完全正交：anachronism 是「物件存在性」检测，register_drift 是「用词语气」检测。
  - 同 cluster 草稿可同时跑两个 scanner，互不干扰，issue code 各异。

【做法 · 确定性纯规则正则（不依赖 LLM）】：
  1. **tier 解析**（北极星②：作者档第一权威）：
     a. genre = _resolve_genre(project_root)（作者档 genre_tags / 用户偏好.json genre）
     b. register_tier = genre_dimension_packs.json packs[genre].register_tier（pack 路由）
     c. 兜底：直接读 _数据库/世界观.json 或 作者风格.json 的 register_tier 字段
     d. 仍无 → skip（北极星②：未声明语域不擅判）
  2. **lexicon 加载**：lexicons/register_tiers/<tier>.json
     - direction=forward：当代词漂入高语域题材（xianxia/xuanhuan/historical/epic_fantasy）
     - direction=reverse：高语域古风词漂入低语域题材（modern_urban / scifi）
  3. **voice_pack 豁免**：读 _数据库/voice_pack.json voice_meta.allow_register_drift
     - True → skip（戏谑/穿越元小说/meta narrative 主动允许漂移）
  4. **草稿扫描**：剥 CHANGES 段 → CJK 计数 < 500 跳 → 词典命中（分类 + 位置）
  5. **baseline（可选）**：读作者档 author_register_tier_baseline 作 floor（作者自己用过多少）
  6. **阈值**：observed_per_1k > floor + DEFAULT_THRESHOLD（保守默认 0.5/千字）→ advisory

【北极星② / ③ / ⑤ 顾问非法官】tier 由作者档决定（北极星②）· 全 advisory · code
  REGISTER_DRIFT 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤）· 单向只报偏高（北极星③）。
  env REGISTER_DRIFT_MODE: off / shadow（默认·只记不判·零回归）/ active。

用法：python world_register_drift_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "REGISTER_DRIFT"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

DEFAULT_THRESHOLD = 0.5         # 默认 per_1k 上浮阈值（baseline + 0.5/千字）
TIER_DIR = Path(__file__).resolve().parent / "lexicons" / "register_tiers"
PACKS_FILE = (Path(__file__).resolve().parent.parent
              / "claude-home" / "templates" / "genre_dimension_packs.json")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("REGISTER_DRIFT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_genre(project_root) -> str | None:
    """genre 解析（作者档 genre_tags[0] > 用户偏好.genre）。无 → None。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for path, key in [(db / "作者风格.json", "genre_tags"),
                       (db / "用户偏好.json", "genre")]:
        obj = _read_json(path) if path.exists() else None
        if not isinstance(obj, dict):
            continue
        v = obj.get(key)
        if isinstance(v, list) and v:
            return str(v[0]).strip().lower()
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def _resolve_register_tier(project_root, genre) -> str | None:
    """register_tier 解析三级：
      1) genre_dimension_packs.json packs[genre].register_tier（pack 路由）
      2) _数据库/世界观.json register_tier
      3) _数据库/作者风格.json register_tier
    全无 → None（北极星②：未声明语域不擅判）。
    """
    # 1) pack 路由
    if genre:
        packs = _read_json(PACKS_FILE)
        if isinstance(packs, dict):
            pack = packs.get("packs", {}).get(genre, {})
            if isinstance(pack, dict):
                t = pack.get("register_tier")
                if isinstance(t, str) and t.strip():
                    return t.strip().lower()
    # 2) 世界观.json
    if project_root:
        db = Path(project_root) / "_数据库"
        for path in (db / "世界观.json", db / "作者风格.json"):
            obj = _read_json(path) if path.exists() else None
            if isinstance(obj, dict):
                t = obj.get("register_tier")
                if isinstance(t, str) and t.strip():
                    return t.strip().lower()
    return None


def _load_lexicon(tier: str) -> dict | None:
    """加载 tier 词典。无 → None。"""
    if not tier:
        return None
    path = TIER_DIR / f"{tier}.json"
    if not path.exists():
        return None
    return _read_json(path)


def _voice_pack_allows_drift(project_root) -> bool:
    """voice_pack.json voice_meta.allow_register_drift = True → 豁免。"""
    if not project_root:
        return False
    vp = _read_json(Path(project_root) / "_数据库" / "voice_pack.json")
    if not isinstance(vp, dict):
        return False
    meta = vp.get("voice_meta")
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("allow_register_drift"))


def _read_baseline(project_root) -> float | None:
    """读作者档 author_register_tier_baseline.drift_per_1k 作 floor（作者自身用过多少）。
    无 → None。"""
    if not project_root:
        return None
    sp = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(sp) if sp.exists() else None
    if not isinstance(obj, dict):
        return None
    base = obj.get("author_register_tier_baseline")
    if not isinstance(base, dict):
        return None
    v = base.get("drift_per_1k")
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _flatten_terms(lex: dict) -> list:
    """词典 → [(term, category), ...]，按词长降序（防短词吃长词）。"""
    out = []
    dt = lex.get("drift_terms", {})
    if not isinstance(dt, dict):
        return out
    for cat, terms in dt.items():
        if not isinstance(terms, list):
            continue
        for t in terms:
            if isinstance(t, str) and t.strip():
                out.append((t.strip(), cat))
    out.sort(key=lambda x: len(x[0]), reverse=True)
    return out


def detect_drift(text: str, lex: dict) -> list:
    """扫词典命中（去 overlap 防短词吃长词）。返回 [{term,category,pos}, ...]。"""
    text = _strip_changes(text)
    terms = _flatten_terms(lex)
    if not terms:
        return []
    hits = []
    occupied = [False] * len(text)
    for term, cat in terms:
        if not term:
            continue
        # 用 re.escape 防特殊字符（KPI/OKR 等纯字母不影响，俚语含数字字母混合也安全）
        pat = re.compile(re.escape(term))
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            if any(occupied[s:e]):
                continue
            for i in range(s, e):
                occupied[i] = True
            hits.append({"term": term, "category": cat, "pos": s})
    hits.sort(key=lambda h: h["pos"])
    return hits


def scan(draft_path, project_root=None) -> dict:
    """L18 Le Guin register drift 检测。永远 advisory（北极星②/⑤）。"""
    mode = _mode()
    out = {"scanner": "world_register_drift", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    out["cjk_count"] = cjk
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    # voice_pack 豁免（戏谑 / 穿越元小说允许 register 漂移）
    if _voice_pack_allows_drift(project_root):
        out["note"] = "voice_pack.voice_meta.allow_register_drift=True·豁免（戏谑/穿越元 meta）"
        out["voice_pack_exempt"] = True
        return out

    # tier 解析
    genre = _resolve_genre(project_root)
    tier = _resolve_register_tier(project_root, genre)
    out["genre"] = genre
    out["register_tier"] = tier
    if not tier:
        out["note"] = "未声明 register_tier·跳过（北极星②：作者未标语域不擅判）"
        return out

    lex = _load_lexicon(tier)
    if not lex:
        out["note"] = f"无 tier 词典（tier={tier}）·跳过"
        return out
    out["direction"] = lex.get("direction", "forward")

    hits = detect_drift(draft, lex)
    per_1k = round(len(hits) / (cjk / 1000.0), 3) if cjk else 0.0
    out["drift_count"] = len(hits)
    out["per_1k"] = per_1k
    out["sample_terms"] = hits[:8]
    cat_counts = {}
    for h in hits:
        cat_counts[h["category"]] = cat_counts.get(h["category"], 0) + 1
    out["category_counts"] = cat_counts

    # baseline（作者档 floor）+ 默认阈值
    baseline = _read_baseline(project_root)
    floor = (baseline if baseline is not None else 0.0) + DEFAULT_THRESHOLD
    out["baseline_per_1k"] = baseline
    out["threshold"] = floor

    msg = None
    if per_1k > floor:
        sample = "、".join(sorted({h["term"] for h in hits})[:6])
        direction = "当代俚语/工程黑话漂入" if out["direction"] == "forward" else "高语域古风词漂入"
        msg = (f"Le Guin register drift：{direction}{tier} 题材语域 "
               f"{len(hits)} 处（{per_1k}/千字 > floor {floor:.2f}·{sample} 等）·"
               f"建议核对场景 register 一致性（与 R6 anachronism 时代物件检测正交）")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "register_drift", "severity": "minor",
                "message": msg, "per_1k": per_1k, "threshold": floor,
                "register_tier": tier, "direction": out["direction"],
                "sample_terms": hits[:8], "category_counts": cat_counts,
                "_doc": ("Le Guin tonal consistency·register 漂移 advisory 可豁免"
                         "（voice_pack.voice_meta.allow_register_drift=True 戏谑/穿越元）·"
                         "与 R6 anachronism 时代轴正交·绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判
            print(f"[SHADOW] register_drift: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Le Guin register drift 语域漂移检测(advisory · cluster · "
                    "与 R6 anachronism 正交)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 tier 门控")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
