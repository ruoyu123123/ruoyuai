#!/usr/bin/env python3
"""volume_arc_drift_scanner.py — 卷级大势收敛漂移哨兵（2026-05-29 北极星 P2 · H1/H3-trend）

北极星原则 3「大势已定」：每卷无论小势（走向卡/涟漪）怎么折腾，最后方向都一致。
代码层的收敛靠三道：
  ① build_manifest.volume_convergence_anchor —— writer 始终看到本卷固定终点（模型自己导航）
  ② cluster_emergence 收敛打分维度 —— 涌现的候选 ME 朝未达成 milestone 倾斜（advisory 排序）
  ③ 本哨兵 —— 卷推进过半但里程碑覆盖明显落后时 advisory 告警「注意朝终点收敛」

**严守 advisory 顾问位**（gate_level=advisory，绝不 hard_gate）：只提醒「可能偏离」，
不阻断、不替模型决定怎么收敛——符合原则 5「不干涉模型判断」。

数据源（全防御性，缺则 SKIP exit 0）：
- 大势卡.json volumes[].key_milestones / ending_state（本卷固定终点）
- 事件簇.json clusters[]（vol 归属 + status + scope_summary）
- 故事块摘要.json clusters[]（已写 cluster 的摘要，判断「实际写了什么」）

CLI: python volume_arc_drift_scanner.py <project> [--last-n N]
退出码：0=无漂移/数据不足 · 1=advisory 漂移告警（SC-2）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _load(p: Path, default=None):
    try:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _kw(text: str) -> set:
    """中文 2-gram + 英数 token 关键词集（与 cluster_emergence._keyword_set 同口径思路）。"""
    if not text:
        return set()
    out = set()
    for tok in re.findall(r"[A-Za-z0-9_]+", text):
        if len(tok) >= 2:
            out.add(tok.lower())
    cjk = re.findall(r"[一-鿿]", text)
    for i in range(len(cjk) - 1):
        out.add(cjk[i] + cjk[i + 1])
    return out


def _current_vol(shijianji: dict) -> int | None:
    """取最近在写/已写 cluster 的 vol（已落章优先）。"""
    best = None
    for c in shijianji.get("clusters", []):
        if not isinstance(c, dict):
            continue
        v = c.get("vol")
        if v is None:
            m = re.search(r"V?(\d+)", str(c.get("parent_me") or ""))
            v = int(m.group(1)) if m else None
        cr = c.get("chapter_range")
        landed = isinstance(cr, list) and len(cr) == 2 and isinstance(cr[0], int)
        if v is not None and (landed or c.get("status") in ("done", "in_progress", "进行中", "已完成")):
            if best is None or v > best:
                best = v
    return best


def scan(project_root: Path) -> dict:
    db = project_root / "_数据库"
    dashishi = _load(db / "大势卡.json", {}) or {}
    shijianji = _load(db / "事件簇.json", {"clusters": []}) or {"clusters": []}
    ledger = _load(db / "故事块摘要.json", {"clusters": []}) or {"clusters": []}

    issues = []
    cur_vol = _current_vol(shijianji)
    if cur_vol is None:
        return {"scanner": "volume_arc_drift", "issues": [], "_note": "无在写 cluster，跳过"}

    # 本卷固定终点
    vol_obj = None
    for v in (dashishi.get("volumes") or []):
        if v.get("vol") == cur_vol:
            vol_obj = v
            break
    if not vol_obj:
        return {"scanner": "volume_arc_drift", "issues": [], "_note": f"大势卡无 vol{cur_vol} 终点定义，跳过"}
    milestones = vol_obj.get("key_milestones") or []
    if not isinstance(milestones, list) or not milestones:
        return {"scanner": "volume_arc_drift", "issues": [], "_note": f"vol{cur_vol} 无 key_milestones，跳过"}

    # 本卷已写 cluster（用于「实际写了什么」内容覆盖）
    vol_clusters = [c for c in shijianji.get("clusters", [])
                    if isinstance(c, dict) and (c.get("vol") == cur_vol)]
    written = [c for c in vol_clusters
               if (isinstance(c.get("chapter_range"), list) and len(c.get("chapter_range")) == 2)
               or c.get("status") in ("done", "已完成")]
    # 2026-05-29 复审 W1：progress 用【卷 ME 完成度】而非 cluster 计数——fluid 下 事件簇.json 通常
    # 只含已涌现 cluster（total≈written→progress 恒≈1.0 卷首即假阳性）。ME 池是固定参照系。
    def _me_vol(m):
        v = m.get("vol")
        if isinstance(v, int):
            return v
        mm = re.search(r"(\d+)", str(v or m.get("me_id") or m.get("id") or ""))
        return int(mm.group(1)) if mm else None
    me_pool = dashishi.get("major_events") or dashishi.get("major_events_pool") or []
    vol_mes = [m for m in me_pool if isinstance(m, dict) and _me_vol(m) == cur_vol]
    done_mes = [m for m in vol_mes if m.get("status") == "completed" or m.get("completed_at_ch")]
    total_me = len(vol_mes) or 1
    progress = len(done_mes) / total_me if vol_mes else 0.0
    if not vol_mes:  # 无 ME 池数据 → 退回 cluster 计数（带标记，避免静默假阳性）
        progress = (len(written) / (len(vol_clusters) or 1)) if vol_clusters else 0.0

    # 实际写内容关键词（cluster scope + 账本各 cluster summary 并集）
    written_text = " ".join(str(c.get("scope_summary", "")) for c in written)
    led_by_cid = {}
    for lc in ledger.get("clusters", []):
        if isinstance(lc, dict):
            led_by_cid[str(lc.get("cluster_id"))] = lc
    for c in written:
        lc = led_by_cid.get(str(c.get("cluster_id")))
        if lc:
            chs = lc.get("chapters") or {}
            for rec in (chs.values() if isinstance(chs, dict) else []):
                if isinstance(rec, dict) and rec.get("summary"):
                    written_text += " " + str(rec["summary"])
    written_kw = _kw(written_text)

    # milestone 覆盖率：每个 milestone 与已写内容关键词有重叠即视为「已触及」
    touched = 0
    untouched = []
    for ms in milestones:
        ms_text = ms if isinstance(ms, str) else (ms.get("text") or ms.get("milestone") or json.dumps(ms, ensure_ascii=False))
        ms_kw = _kw(str(ms_text))
        if ms_kw and (ms_kw & written_kw):
            touched += 1
        else:
            untouched.append(str(ms_text)[:40])
    coverage = touched / len(milestones) if milestones else 1.0

    # 漂移判据（advisory）：卷推进过半（≥50%）但 milestone 覆盖明显落后于推进（coverage < progress - 0.25）
    if progress >= 0.5 and coverage < (progress - 0.25):
        issues.append({
            "code": "VOLUME_ARC_DRIFT",
            "gate_level": "advisory",
            "severity": "warning",
            "vol": cur_vol,
            "progress": round(progress, 2),
            "milestone_coverage": round(coverage, 2),
            "untouched_milestones": untouched[:5],
            "msg": (f"⚠️ vol{cur_vol} 已推进 {progress:.0%} 但卷里程碑仅覆盖 {coverage:.0%}，"
                    f"可能偏离大势终点。未触及里程碑：{'；'.join(untouched[:3])}。"
                    f"建议后续 cluster 朝本卷 ending_state 收敛（顾问提示，不限定写法）。"),
        })
    return {"scanner": "volume_arc_drift", "vol": cur_vol,
            "progress": round(progress, 2), "milestone_coverage": round(coverage, 2),
            "issues": issues}


def main():
    ap = argparse.ArgumentParser(description="卷级大势收敛漂移哨兵（advisory）")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)  # 兼容编排器统一调用签名，本哨兵按卷算不用
    args = ap.parse_args()
    project_root = Path(args.project).resolve()
    if not (project_root / "_数据库").exists():
        print(f"[SKIP] 无 _数据库: {project_root}", file=sys.stderr)
        sys.exit(0)
    result = scan(project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result.get("issues") else 0)


if __name__ == "__main__":
    main()
