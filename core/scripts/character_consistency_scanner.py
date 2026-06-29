#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN角色网络/共指集成
"""character_consistency_scanner.py — 角色一致性扫描器（整合角色网络 + 共指消解）。

【目标】检测 cluster 草稿中的角色一致性问题，辅助质检流水线。

【检测规则（全部 advisory · 北极星⑤ 顾问非法官）】
  · CHARACTER_VANISH:          角色在前半出现后消失
  · CHARACTER_ORPHAN_DIALOGUE: 对话段无法归属到任何角色
  · CHARACTER_SUDDEN_APPEAR:   角色无引入突然出现（且不在 known_characters 中）
  · CHARACTER_COREF_AMBIGUOUS: 代词指代模糊（多个候选角色）

【默认安全铁律】
  · 依赖的 character_network_extractor 和 nn_coref_bridge 各自有 env 门控
  · 本 scanner 自身 env: CHARACTER_CONSISTENCY_MODE = off / shadow(默认) / active
  · 任何子组件失败 → 跳过该检测·绝不崩

用法：python character_consistency_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ── 常量 ────────────────────────────────────────────────────

ISSUE_CODES = {
    "vanish": "CHARACTER_VANISH",
    "orphan": "CHARACTER_ORPHAN_DIALOGUE",
    "sudden": "CHARACTER_SUDDEN_APPEAR",
    "ambiguous": "CHARACTER_COREF_AMBIGUOUS",
}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 最少 CJK 字数才检测
MIN_CJK = 500


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _mode() -> str:
    m = (os.environ.get("CHARACTER_CONSISTENCY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


# ── 角色列表加载 ────────────────────────────────────────────

def _load_known_characters(project_dir: str | None) -> list[str]:
    """从项目 _数据库/人物.json 或 人物卡.json 读取已知角色名。"""
    if not project_dir:
        return []
    db = Path(project_dir) / "_数据库"
    for fname in ("人物.json", "人物卡.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        chars = obj.get("characters", [])
        names = []
        for c in chars:
            if isinstance(c, dict):
                name = c.get("name", "")
                if name:
                    names.append(name)
            elif isinstance(c, str) and c:
                names.append(c)
        if names:
            return names
    return []


# ── 检测规则 ────────────────────────────────────────────────

def _detect_vanish(network: dict, text: str) -> list[dict]:
    """CHARACTER_VANISH: 角色在前半出现后在后半消失。"""
    violations = []
    chars = network.get("characters", [])
    if not chars or not text:
        return violations

    mid = len(text) // 2
    first_half = text[:mid]
    second_half = text[mid:]

    for char in chars:
        in_first = char in first_half
        in_second = char in second_half
        if in_first and not in_second:
            # 检查是否是主要角色（中心性 > 0.3 才报）
            centrality = network.get("centrality", {}).get(char, 0)
            if centrality >= 0.3:
                violations.append({
                    "code": ISSUE_CODES["vanish"],
                    "gate_level": "advisory",
                    "severity": "minor",
                    "character": char,
                    "centrality": centrality,
                    "message": f"角色「{char}」在前半段有戏份（中心性 {centrality}）"
                               f"但后半段完全消失·建议检查是否遗忘",
                })
    return violations


def _detect_orphan_dialogue(network: dict) -> list[dict]:
    """CHARACTER_ORPHAN_DIALOGUE: 对话段无法归属到任何角色。"""
    violations = []
    orphan_count = network.get("orphan_dialogues", 0)
    total_dialogues = network.get("dialogue_count", 0)

    if total_dialogues > 0 and orphan_count > 0:
        ratio = orphan_count / total_dialogues
        if ratio > 0.3:  # 超过 30% 对话无归属
            violations.append({
                "code": ISSUE_CODES["orphan"],
                "gate_level": "advisory",
                "severity": "minor",
                "orphan_count": orphan_count,
                "total_dialogues": total_dialogues,
                "ratio": round(ratio, 2),
                "message": f"有 {orphan_count}/{total_dialogues} 段对话"
                           f"（{ratio:.0%}）无法归属到任何角色·"
                           f"建议补充说话者标识",
            })
    return violations


def _detect_sudden_appear(network: dict, text: str,
                          known_characters: list[str]) -> list[dict]:
    """CHARACTER_SUDDEN_APPEAR: 角色不在 known_characters 中且无引入突然出现。"""
    violations = []
    chars = network.get("characters", [])
    known_set = set(known_characters)

    for char in chars:
        if char in known_set:
            continue
        # 检查是否有引入（在首次出现位置前 50 字内是否有引导词）
        first_idx = text.find(char)
        if first_idx < 0:
            continue
        intro_window = text[max(0, first_idx - 50):first_idx]
        # 常见引入模式
        has_intro = bool(re.search(
            r"(名叫|叫做|名为|此人|来了|走来|出现|站着|坐着|是|就是)", intro_window))
        if not has_intro:
            centrality = network.get("centrality", {}).get(char, 0)
            if centrality >= 0.2:  # 有一定戏份才报
                violations.append({
                    "code": ISSUE_CODES["sudden"],
                    "gate_level": "advisory",
                    "severity": "minor",
                    "character": char,
                    "centrality": centrality,
                    "message": f"角色「{char}」不在已知角色列表中且无引入描写·"
                               f"突然出现在文本中·建议检查是否需要引入",
                })
    return violations


def _detect_coref_ambiguous(coref_results: list[dict]) -> list[dict]:
    """CHARACTER_COREF_AMBIGUOUS: 代词指代模糊（多个候选角色）。"""
    violations = []
    ambiguous = [r for r in coref_results if r.get("ambiguous")]

    if len(ambiguous) >= 3:  # 至少 3 处模糊才报（偶发不算问题）
        sample_mentions = [r["mention"] for r in ambiguous[:5]]
        violations.append({
            "code": ISSUE_CODES["ambiguous"],
            "gate_level": "advisory",
            "severity": "minor",
            "ambiguous_count": len(ambiguous),
            "total_pronouns": len(coref_results),
            "sample_mentions": sample_mentions,
            "message": f"有 {len(ambiguous)} 处代词指代模糊"
                       f"（如 {'、'.join(sample_mentions[:3])}）·"
                       f"多个候选角色距离相近·建议明确指代",
        })
    return violations


# ── 主函数 ────────────────────────────────────────────────

def scan_character_consistency(draft_text: str, project_dir: str | None = None) -> list[dict]:
    """扫描角色一致性问题。

    调用 character_network_extractor + nn_coref_bridge。
    返回 issue 列表（全部 advisory）。任何子组件失败 → 跳过该检测。
    """
    issues = []

    text = _strip_changes(draft_text)
    if _cjk_count(text) < MIN_CJK:
        return issues

    known_characters = _load_known_characters(project_dir)

    # 1. 角色网络提取
    network = None
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from character_network_extractor import extract_character_network
        network = extract_character_network(text, known_characters, project_dir)
    except Exception as e:  # noqa: BLE001
        print(f"[character_consistency_scanner] 角色网络提取失败·跳过："
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)

    # 2. 共指消解
    coref_results = []
    try:
        from nn_coref_bridge import resolve_coreferences
        coref_results = resolve_coreferences(text, known_characters)
    except Exception as e:  # noqa: BLE001
        print(f"[character_consistency_scanner] 共指消解失败·跳过："
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)

    # 3. 检测规则
    if network and network.get("source") not in ("disabled", "error", "empty_input"):
        issues.extend(_detect_vanish(network, text))
        issues.extend(_detect_orphan_dialogue(network))
        issues.extend(_detect_sudden_appear(network, text, known_characters))

    if coref_results:
        issues.extend(_detect_coref_ambiguous(coref_results))

    return issues


def scan(draft_path: str, project_root: str | None = None) -> dict:
    """audit_hub 兼容接口：返回标准 scanner report。"""
    mode = _mode()
    out = {
        "scanner": "character_consistency",
        "schema_version": "1.0",
        "mode": mode,
        "gate_level": "advisory",
        "violations": [],
        "violations_count": 0,
        "verdict": "PASS",
    }
    if mode == "off":
        return out

    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out

    if _cjk_count(_strip_changes(text)) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    issues = scan_character_consistency(text, project_root)

    if mode == "shadow":
        for iss in issues:
            print(f"[SHADOW] character_consistency: {iss.get('code')} — "
                  f"{iss.get('message', '')[:80]} — 不上报", file=sys.stderr)
        out["shadow_issues_count"] = len(issues)
        return out

    # active mode
    out["violations"] = issues
    out["violations_count"] = len(issues)
    if issues:
        out["verdict"] = "FAIL_MINOR"
    return out


# ── CLI ────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="角色一致性扫描器（整合角色网络 + 共指消解）")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="项目根目录")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()

    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("verdict") != "PASS" else 0)


if __name__ == "__main__":
    main()
