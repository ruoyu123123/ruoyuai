"""cross_cluster_offscreen_aggregate.py — 幕后行动落地检查（v19.2 新增）

补 cross_chapter_pattern + continuity 的另一类盲区：**幕后人物行动是否真的发生**。

之前 4 章人物卡里填了 offscreen.actions（顾沉/老周/林晚秋/陆爸），但 writer
不消费、检测体系不验证——反派"消失"，读者感觉不到"反派一直在动"。

本脚本扫两件事：
1. manifest.active_offscreen_actions 中 ch_range 覆盖本章的 action
2. _changes.json self_eval.offscreen_actions_executed（writer 自报落地证据）

报警条件：
- manifest 有 active actions 但 _changes 是空 / 不存在 → warning
- evidence 字段为空或 < 10 字 → advisory
- writer 报了 completed_fully=true 但正文 grep 不到 character aliases → warning（撒谎）

用法：
    python cross_cluster_offscreen_aggregate.py <项目路径> [--last-n 5]

退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path



# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_chapter_dirs(project_root: Path) -> list[tuple[int, Path]]:
    out = []
    for d in project_root.glob("章节/第*章"):
        m = re.match(r"第(\d+)章", d.name)
        if m:
            out.append((int(m.group(1)), d))
    out.sort(key=lambda x: x[0])
    return out


def get_character_aliases(project_root: Path, character_name: str) -> list[str]:
    """从人物卡读 name + name_aliases + id。"""
    chars = load_json(project_root / "_数据库" / "人物卡.json", {"characters": []})
    for c in chars.get("characters", []):
        if c.get("name") == character_name or c.get("id") == character_name:
            aliases = [c.get("name"), c.get("id")] + c.get("name_aliases", [])
            return [a for a in aliases if a]
    return [character_name]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=5)
    args = ap.parse_args()

    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    chapter_dirs = find_chapter_dirs(project_root)
    if not chapter_dirs:
        print("[OK] 无已写章节")
        sys.exit(0)
    chapter_dirs = chapter_dirs[-args.last_n:]

    findings = []
    per_chapter = {}

    for ch, ch_dir in chapter_dirs:
        # 读 manifest
        manifest_path = project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json"
        manifest = load_json(manifest_path, {})
        expected_actions = manifest.get("active_offscreen_actions", [])

        # 读 _changes
        changes_path = ch_dir / f"第{ch:03d}章_changes.json"
        changes = load_json(changes_path, {})
        executed = changes.get("self_eval", {}).get("offscreen_actions_executed", [])

        # 读正文
        text_path = ch_dir / f"第{ch:03d}章.txt"
        text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""

        per_chapter[ch] = {
            "expected_count": len(expected_actions),
            "executed_count": len(executed),
            "expected_chars": list(set(a.get("character") for a in expected_actions)),
            "executed_chars": list(set(e.get("character") for e in executed)),
        }

        # === 检查 1: manifest 有期望但 _changes 是空 ===
        if expected_actions and not executed:
            findings.append({
                "severity": "warning",
                "code": "OFFSCREEN_ACTIONS_NOT_EXECUTED",
                "chapter": ch,
                "metric": {"expected": len(expected_actions), "executed": 0},
                "message": f"ch{ch} manifest 列了 {len(expected_actions)} 条 offscreen action 但 _changes.offscreen_actions_executed 是空",
                "expected_actions": [{"character": a.get("character"), "action": a.get("action", "")[:60]} for a in expected_actions],
                "suggestion": "writer 必须按 visible_to_protagonist 落地至少 1 个 action（POV 切换/物件暗示/对话提及/副作用任选一）",
            })

        # === 检查 2: 执行了的 action 是否在正文中能找到角色 aliases ===
        for ex in executed:
            char_name = ex.get("character", "")
            aliases = get_character_aliases(project_root, char_name)
            evidence = ex.get("evidence", "")
            completed = ex.get("completed_fully", False)

            # evidence 字段太短
            if len(evidence) < 10:
                findings.append({
                    "severity": "advisory",
                    "code": "OFFSCREEN_EVIDENCE_THIN",
                    "chapter": ch,
                    "metric": {"character": char_name, "evidence_len": len(evidence)},
                    "message": f"ch{ch} {char_name} 的 offscreen action evidence 太短 ({len(evidence)} 字)",
                    "suggestion": "evidence 必须摘录正文具体段落/对话作证据，≥10 字",
                })

            # writer 报了 completed_fully 但正文 grep 不到任何 alias
            if completed and not any(a in text for a in aliases):
                findings.append({
                    "severity": "warning",
                    "code": "OFFSCREEN_EXECUTION_LIE",
                    "chapter": ch,
                    "metric": {"character": char_name, "aliases_tried": aliases},
                    "message": f"ch{ch} writer 自报 {char_name} 的 action 已完成，但正文不含其任何 alias",
                    "suggestion": "writer 可能虚报，validator-repair 复核：要么补足正文证据，要么改 completed_fully=false",
                })

        # === 检查 3: 跨章未 done 的 action 累积（堆积告警）===
        # 仅当章是最新一章时检查
        if ch == chapter_dirs[-1][0]:
            # 统计跨章累积未完成的 action
            chars_json = load_json(project_root / "_数据库" / "人物卡.json", {"characters": []})
            backlog = []
            for c in chars_json.get("characters", []):
                if c.get("role") == "主角":
                    continue
                cname = c.get("name") or c.get("id")
                for idx, act in enumerate(c.get("offscreen", {}).get("actions", [])):
                    ch_range = act.get("ch_range", [])
                    if len(ch_range) == 2 and ch_range[1] < ch and not act.get("done"):
                        backlog.append({"character": cname, "idx": idx, "action": act.get("action", "")[:60], "ch_range": ch_range})
            if backlog:
                findings.append({
                    "severity": "advisory",
                    "code": "OFFSCREEN_BACKLOG",
                    "chapter": ch,
                    "metric": {"backlog_count": len(backlog)},
                    "message": f"截至 ch{ch}, 有 {len(backlog)} 条 offscreen action 已过 ch_range 但未标 done",
                    "backlog": backlog,
                    "suggestion": "save-state 应自动标 done，或人工 review 后补 done=true",
                })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "offscreen",
        "scan_ts": ts,
        "chapters_scanned": [ch for ch, _ in chapter_dirs],
        "per_chapter": per_chapter,
        "findings": findings,
        "summary": {
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "total": len(findings),
        },
    }
    out_path = out_dir / f"offscreen_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[cross_cluster_offscreen_aggregate] 扫描章节={[ch for ch, _ in chapter_dirs]}")
    for ch, d in per_chapter.items():
        print(f"  ch{ch}: 期望 {d['expected_count']} action / 实际执行 {d['executed_count']} / 涉及角色: {d['expected_chars']}")
    print()
    print(f"=== 发现 {len(findings)} 项 (warning={report['summary']['warning']} / advisory={report['summary']['advisory']}) ===")
    for f in findings:
        ch_str = f"ch{f.get('chapter', '*')}"
        print(f"  [{f['severity'].upper()}] [{f['code']}] {ch_str} :: {f['message']}")
        print(f"     建议: {f['suggestion']}")
    print()
    print(f"报告: {out_path}")

    if any(f["severity"] == "warning" for f in findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
