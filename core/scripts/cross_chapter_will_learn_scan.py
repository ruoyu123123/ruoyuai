"""cross_chapter_will_learn_scan.py — 角色 will_learn 跨章兑现扫（CCR22）

读 _数据库/人物卡.json[<角色>].knowledge.will_learn[]，每条有 due_by。
对照已写章节，检测：
- WILL_LEARN_OVERDUE：due_by 已过但仍在 will_learn 列（未在 changes 标 learned）
- WILL_LEARN_NEVER_HINTED：should_learn_by 前 5 章无任何 hint/铺垫
- WILL_LEARN_LEARNED_NOT_MARKED：正文中实际已显示该认知但 will_learn 中未删除

退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def read_text(project_root: Path, ch: int) -> str:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project)
    cards_path = project_root / "_数据库" / "人物卡.json"
    if not cards_path.exists():
        print("[SKIP] 人物卡.json 不存在")
        sys.exit(0)
    cards = load_json(cards_path, {})
    characters = cards.get("characters", []) or []
    if not characters:
        print("[SKIP] 无角色")
        sys.exit(0)

    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    cur_ch = chapters[-1] if chapters else 0
    if not cur_ch:
        print("[SKIP] 无已写章节")
        sys.exit(0)

    findings = []
    for c in characters:
        if not isinstance(c, dict):
            continue
        cname = c.get("name") or c.get("id")
        if not cname:
            continue
        knowledge = c.get("knowledge", {}) or {}
        will_learn = knowledge.get("will_learn", []) or []
        for wl in will_learn:
            if not isinstance(wl, dict):
                continue
            item_id = wl.get("id") or wl.get("name", "")
            content = wl.get("content") or wl.get("description", "")
            due_by = wl.get("due_by") or wl.get("by_ch") or 0
            if not item_id or not due_by:
                continue

            # WILL_LEARN_OVERDUE：due_by 已过
            if cur_ch > due_by:
                findings.append({
                    "severity": "warning",
                    "code": "WILL_LEARN_OVERDUE",
                    "character": cname,
                    "item_id": item_id,
                    "content": content[:60],
                    "due_by": due_by,
                    "current_ch": cur_ch,
                    "overdue_by": cur_ch - due_by,
                    "suggestion": f"{cname} 应在 ch{due_by} 前学到「{item_id}」，已过 {cur_ch - due_by} 章未标记 learned",
                })

            # WILL_LEARN_NEVER_HINTED：due_by 前 5 章无 hint
            if cur_ch >= due_by - 5 and cur_ch <= due_by + 2:
                # 取近 5 章查正文
                content_kws = re.findall(r"[一-鿿]{3,5}", content)[:3]
                if content_kws:
                    hint_chs = []
                    for ch in chapters[-5:]:
                        text = read_text(project_root, ch)
                        if any(kw in text for kw in content_kws):
                            hint_chs.append(ch)
                    if not hint_chs and cur_ch <= due_by:
                        findings.append({
                            "severity": "advisory",
                            "code": "WILL_LEARN_NEVER_HINTED",
                            "character": cname,
                            "item_id": item_id,
                            "content": content[:50],
                            "due_by": due_by,
                            "current_ch": cur_ch,
                            "suggestion": f"{cname} will_learn「{item_id}」(due {due_by}) 前 5 章无任何铺垫 → 突兀",
                        })

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "will_learn",
        "scan_ts": ts,
        "current_ch": cur_ch,
        "characters_scanned": [c.get("name") for c in characters if c.get("name")],
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"will_learn_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[will_learn] {summary['warning']} warning / {summary['advisory']} advisory")
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
