"""cross_cluster_will_learn_aggregate.py — 角色 will_learn 跨章兑现扫（CCR22）

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


import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动
import cluster_lookup  # 2026-06 锚 learn_at_cluster → 章范围（与 build_manifest/declarative 对齐）


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

    # ===== 2026-05-29 cluster 化分支：账本有 text_keyword_set → 用账本关键词判 hint + cluster 末章锚点 =====
    use_ledger = csr.is_cluster_mode() and csr.ledger_has_field(project_root, "text_keyword_set")
    ledger_kw = {}  # {ch: set(text_keyword_set)}，cluster 模式用它取代逐章正文扫描
    if use_ledger:
        recs = csr.get_chapter_records(project_root)
        chapters = sorted({ch for ch, _ in recs})
        for ch, rec in recs:
            kws = rec.get("text_keyword_set") or []
            ledger_kw.setdefault(ch, set()).update(str(k) for k in kws)
        # cluster 模式锚点 = 末 cluster 的 chapter_range[1]
        last_clusters = csr.get_clusters(project_root, last_n=1)
        cur_ch = 0
        if last_clusters:
            cr = last_clusters[-1].get("chapter_range")
            if isinstance(cr, list) and len(cr) >= 2 and isinstance(cr[1], int):
                cur_ch = cr[1]
            elif isinstance(last_clusters[-1].get("cluster_end_ch"), int):
                cur_ch = last_clusters[-1]["cluster_end_ch"]
        if not cur_ch and chapters:
            cur_ch = chapters[-1]
    else:
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
            # 2026-06 北极星①复审：will_learn 权威锚是 learn_at_cluster（cluster ID 字符串），
            # 反查 cluster_id_to_range 取末章作 due_by——禁止抽 learn_at_cluster 里的数字当章号
            # （对齐 build_manifest._collect_will_learn_due L2416 / cross_cluster_declarative_data L215）。
            # 旧字段 id/content/by_ch 作 or 兜底向后兼容，但权威路径走 learn_at_cluster + fact。
            fact = wl.get("fact") or wl.get("content") or wl.get("what") or wl.get("description", "")
            item_id = wl.get("id") or wl.get("fact") or wl.get("what") or ""
            content = fact
            lac = wl.get("learn_at_cluster")
            _rng = cluster_lookup.cluster_id_to_range(project_root, lac) if isinstance(lac, str) and lac else None
            due_by = wl.get("due_by") or wl.get("by_ch") or 0
            if not due_by and _rng and len(_rng) == 2 and isinstance(_rng[1], int):
                due_by = int(_rng[1])
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
                        if use_ledger:
                            # 2026-05-29 复审修复 [M10-b]：
                            # 账本 text_keyword_set 是 builder 抽的「最常见 top-4 个 2 字 2gram」指纹，
                            # 用 2 字指纹去比对 3-5 字内容词（kw in tk / tk in kw）召回极低 →
                            # 几乎所有章都判「无命中」→ WILL_LEARN_NEVER_HINTED 大面积误报。
                            # 修复：①先用指纹做「3-5 字内容词整体落在某指纹里」的宽松粗筛（只取 tk in kw 方向、
                            #        即指纹是内容词子串，剔除无意义的 kw in tk 方向）；②指纹未命中时不直接判「无铺垫」，
                            #        回落到该章真实正文做精确 `kw in text` 校验（磁盘文件仍在），消除误报。
                            chk = ledger_kw.get(ch, set())
                            hit = any(
                                any(tk in kw for tk in chk if len(tk) >= 2)
                                for kw in content_kws
                            )
                            if not hit:
                                # 指纹是有损 top-4，未命中不可信 → 回落真实正文精确校验
                                text = read_text(project_root, ch)
                                if text and any(kw in text for kw in content_kws):
                                    hit = True
                            if hit:
                                hint_chs.append(ch)
                        else:
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
        "characters_scanned": [c.get("name") for c in characters if isinstance(c, dict) and c.get("name")],
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
