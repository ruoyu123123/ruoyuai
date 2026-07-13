#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""first_encounter_anchor_scanner.py — Shklovsky 先体感后命名 (advisory · cluster · 2026-06-20)

【缺口】R8 W4 Batch-H · L23 联网调研：Shklovsky 1917《Art as Device》提出 ostranenie/陌生化
原理 (Tolstoy《Kholstomer》以马视角看人类制度·让熟物变新)·Suvin SF 估隔 estrangement·Gilliam
defamiliarization——新世界元素首次登场应『先体感 (sensory anchor) 后命名 (label)』而非反过来。
诡秘之主等 IP 拆书亦反复印证。LLM 默认偏 label-first ("这是X")=直贴标签丢失陌生化奇异感。

【做法 · 确定性纯规则正则 (不依赖 LLM)】
  1. 读 manifest.first_encounter_targets (build_manifest 自动从 world_glossary 减往期已现术语；
     无 manifest / 字段缺 → 退到从世界观.json entries 提取 title/keywords 当 fallback target 集)。
  2. 对每个 target 在 cluster 草稿中首次出现位置 ±80 CJK 窗口内匹配定义模板：
        X[是叫为称]+Y / 这[就便]?[是叫]Y / 名[叫为]Y / 它[就便]?[是叫]Y
     命中模板 + target 紧贴 Y 位 → label-first advisory。
  3. genre 门控豁免：LitRPG/horror_game/rule_anomaly 题材 (系统流面板/规则块本就直陈) skip。
  4. 输出 label_first_ratio = label_first_targets / total_targets·偏高 advisory。

【北极星 ②/⑤ 顾问非法官】先体感后命名是创作工艺·作者档第一权威·code FIRST_ENCOUNTER_LABEL_FIRST
  绝不进 audit_hub.HARD_GATE_CODES。env FIRST_ENCOUNTER_ANCHOR_MODE: off / shadow (默认) / active。

用法: python first_encounter_anchor_scanner.py <draft_path> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "FIRST_ENCOUNTER_LABEL_FIRST"   # advisory · 绝不进 HARD_GATE_CODES

# label-first 定义模板：X 是/叫/为/称 + target / 这[就便]是/叫 + target / 它[就便]是/叫 + target /
# 名叫/名为 + target
_LABEL_TEMPLATES_BEFORE = [
    re.compile(r"(?:这|那|它|他|她)(?:就|便)?[是叫]([^\s，。！？]{1,15})"),
    re.compile(r"叫(?:做|作)?([^\s，。！？]{1,15})"),
    re.compile(r"名[为叫]([^\s，。！？]{1,15})"),
    re.compile(r"被称(?:为|作)([^\s，。！？]{1,15})"),
    re.compile(r"称之为([^\s，。！？]{1,15})"),
]

# 体感锚定标志 (前置窗口若已有这些·不算 label-first)：
# 感官词 + 形容词 + 反应词·先建立体感再命名才算合规
SENSORY_ANCHOR = re.compile(
    r"(看见|看到|望见|瞧见|瞥见|发觉|感觉|察觉|"
    r"听见|听到|嗅到|闻到|尝到|碰到|触到|摸到|"
    r"飘来|传来|涌来|袭来|扑面|刺鼻|刺眼|"
    r"奇怪|古怪|诡异|陌生|从未见过|从未听过|未曾听闻|不知名|说不上来)"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_GENRE_EXEMPT = {"horror_game", "rule_anomaly", "litrpg"}


def _mode() -> str:
    m = (os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _resolve_genre(project_root) -> str | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for path, key in [(db / "作者风格.json", "genre_tags"),
                       (db / "用户偏好.json", "genre")]:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        v = obj.get(key)
        if isinstance(v, list) and v:
            return str(v[0]).strip().lower()
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def _load_targets_from_manifest(manifest_path) -> list[str]:
    if not manifest_path:
        return []
    try:
        obj = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(obj, dict):
        return []
    raw = obj.get("first_encounter_targets")
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if isinstance(t, str) and t.strip()]
    return []


def _load_targets_from_worldview(project_root) -> list[str]:
    """fallback：从 世界观.json entries 抽 title/keywords 当 target 集。"""
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "世界观.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(obj, dict):
        return []
    targets: list[str] = []
    entries = obj.get("entries") or []
    if isinstance(entries, list):
        for e in entries:
            if not isinstance(e, dict):
                continue
            t = e.get("title")
            if isinstance(t, str) and t.strip():
                targets.append(t.strip())
            kws = e.get("keywords") or []
            if isinstance(kws, list):
                for k in kws:
                    if isinstance(k, str) and k.strip():
                        targets.append(k.strip())
    # 去重保留顺序
    seen, out = set(), []
    for t in targets:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _is_label_first(text: str, target: str, window: int = 80) -> dict:
    """判断 target 首次出现 ±80 字窗口是否为 label-first。

    返回 {first_pos, label_first, has_sensory_anchor, window_text}。
    """
    pos = text.find(target)
    if pos < 0:
        return {"found": False}
    lo = max(0, pos - window)
    hi = min(len(text), pos + len(target) + window)
    window_text = text[lo:hi]
    before = text[lo:pos]
    # 前置体感锚定（在 target 出现前的窗口里检测）
    has_sensory = bool(SENSORY_ANCHOR.search(before))
    # label-first：target 紧贴定义模板 Y 位
    # 窗口前段含 X是/这是/叫做 + target → label-first
    label_first = False
    for rx in _LABEL_TEMPLATES_BEFORE:
        for m in rx.finditer(window_text):
            y = m.group(1).strip()
            if y == target or y.startswith(target) or target.startswith(y):
                # 命中且在 target 出现位之前/同位
                if lo + m.start() <= pos + len(target):
                    label_first = True
                    break
        if label_first:
            break
    return {
        "found": True, "first_pos": pos, "label_first": label_first,
        "has_sensory_anchor": has_sensory,
        "window_preview": window_text[:120],
    }


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "first_encounter_anchor", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
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
        out["note"] = "草稿太短·跳过"
        return out

    genre = _resolve_genre(project_root)
    out["genre"] = genre
    if genre in _GENRE_EXEMPT:
        out["note"] = f"{genre} 题材豁免 (系统流面板/规则块本就直陈)·跳过"
        return out

    targets = _load_targets_from_manifest(manifest_path)
    targets_source = "manifest"
    if not targets:
        targets = _load_targets_from_worldview(project_root)
        targets_source = "worldview_fallback"
    out["targets_source"] = targets_source
    out["targets_count"] = len(targets)
    if not targets:
        out["note"] = "无 first_encounter_targets·跳过 (北极星②：无设定不擅判)"
        return out

    # 限制最多扫 30 个 target (防 cluster 同时塞百条术语炸算力)
    targets = targets[:30]
    results = []
    label_first_count = 0
    found_count = 0
    for tg in targets:
        r = _is_label_first(draft, tg)
        if not r.get("found"):
            continue
        found_count += 1
        results.append({"target": tg, **r})
        if r.get("label_first") and not r.get("has_sensory_anchor"):
            label_first_count += 1

    out["targets_found"] = found_count
    if found_count == 0:
        out["note"] = "草稿未出现任何 target·跳过"
        return out

    ratio = round(label_first_count / max(found_count, 1), 3)
    out["label_first_count"] = label_first_count
    out["label_first_ratio"] = ratio
    out["sample"] = [r for r in results if r.get("label_first") and not r.get("has_sensory_anchor")][:5]

    msg = None
    # 阈值 0.5：超过一半 target 在首次出现都没有体感锚定
    if ratio > 0.5 and label_first_count >= 2:
        msg = (f"first-encounter label-first 偏高：{label_first_count}/{found_count} "
               f"个 target ({ratio:.1%}) 首次出现就被直贴定义模板 (X 是/叫 Y) 而无前置感官铺垫·"
               f"建议先用 1-2 句感官印象 (看见/听见/嗅到 + 形容词) 锚定再点名 (Shklovsky 陌生化)")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "first_encounter_label_first", "severity": "minor",
                "message": msg, "ratio": ratio,
                "label_first_count": label_first_count, "targets_found": found_count,
                "_doc": ("Shklovsky 陌生化·先体感后命名是工艺 advisory·LitRPG/系统流可豁免·"
                         "绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] first_encounter_anchor: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Shklovsky 先体感后命名检测 (advisory · cluster)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 genre / world_glossary fallback")
    ap.add_argument("--manifest", default=None, help="读 first_encounter_targets")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
