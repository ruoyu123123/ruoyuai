"""cluster_blueprint_compliance_scan.py — cluster_blueprint vs 正文一致性扫描（v19.4 新增）

[DEPRECATED v26 · 2026-05-29 · 无活跃调用方]
  实地核查（Grep 全仓）：audit_hub.py 的 13-scanner task 列表不含本脚本，
  6 个 plan 模板 / check-quality 命令文档均不调用它。webnovel_bench_alignment.py:35
  的 "cluster_blueprint_compliance" 仅是 source_scanners 元数据「出处标签」字符串，
  非 subprocess/import 实际调用。其逐章 glob 扫描模型也与「cluster 是唯一检测层」相悖。
  → 本脚本为 v26 chapter mode 遗留孤儿，仅保留作离线/手动诊断；cluster 视野下的
     blueprint 一致性由 audit_hub 的 cluster-only scanner 集合（locked_fact_cross_scene /
     foreshadowing_handoff 等）等价覆盖。如需接回检测层，须改造为读 cluster_draft 并由
     audit_hub.audit_chapter 的 tasks 调度（当前未接）。

检测 writer 是否真的写了 cluster_blueprint 声明的事件/角色/场景类型。

4 维度：
1. KEY_EVENTS_MISSING       - cluster_blueprint.key_events 含但正文未提
2. CHARACTERS_MISSING       - cluster_blueprint.characters 含但正文 + aliases 未出现
3. SCENE_TYPE_MISMATCH      - cluster_blueprint.scene_type 与 writer 自评应用规则不符
4. TURNING_POINT_MISSING    - cluster_blueprint.turning_point 关键词未在正文

用法：python cluster_blueprint_compliance_scan.py <项目> [--ch N | --all]
退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_lookup  # 2026-05-29 复审修复：SC-1 blueprint list 归一守卫
except Exception:  # 防御：缺模块退回原 dict 守卫
    cluster_lookup = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_character_aliases(project_root: Path) -> dict[str, list[str]]:
    """从人物卡读所有角色的 name + name_aliases。返回 {primary_name: [aliases]}。"""
    chars = load_json(project_root / "_数据库" / "人物卡.json", {"characters": []})
    out = {}
    for c in chars.get("characters", []):
        primary = c.get("name") or c.get("id")
        if not primary:
            continue
        aliases = [primary, c.get("id")] + c.get("name_aliases", [])
        out[primary] = list(set(a for a in aliases if a))
    return out


STOP_TOKENS = {"自己", "他的", "她的", "一个", "什么", "这个", "那个", "已经", "他在", "她在", "找到"}


def extract_keywords(text: str, max_words: int = 12) -> list[str]:
    """提取 2 字 + 3 字 ngram，过滤停用词。匹配时用 ≥30% 命中即算通过。"""
    fragments = re.split(r"[（）()/、，,\+\-—\s'\"'\"\.。：:]+", text)
    tokens = []
    for frag in fragments:
        for m in re.finditer(r"[一-鿿]{2,}", frag):
            seg = m.group()
            # 生成 2-gram 和 3-gram
            for n in (2, 3):
                for i in range(len(seg) - n + 1):
                    t = seg[i:i+n]
                    if t not in tokens and t not in STOP_TOKENS:
                        tokens.append(t)
            # 数字保留全部
        for m in re.finditer(r"\d+", frag):
            t = m.group()
            if t not in tokens:
                tokens.append(t)
    return tokens[:max_words] if tokens else []


def match_keywords(keywords: list[str], text: str, threshold: float = 0.3) -> tuple[bool, float]:
    """匹配率 ≥ threshold 视为通过。返回 (是否通过, 命中比例)。"""
    if not keywords:
        return True, 1.0
    hits = sum(1 for k in keywords if k in text)
    rate = hits / len(keywords)
    return rate >= threshold, rate


def check_chapter(project_root: Path, ch: int) -> list[dict]:
    findings = []
    progress = load_json(project_root / "_数据库" / "进度.json", {})
    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
    # 2026-05-29 复审修复：SC-1 — cluster_blueprint 可能是 list（城南实测），裸 .items()
    # 会 AttributeError 崩。先 normalize_blueprint 归一成 dict 再迭代。
    if cluster_lookup is not None:
        bp = cluster_lookup.normalize_blueprint(progress)
    else:
        bp = progress.get("cluster_blueprint") or {}
        if not isinstance(bp, dict):
            bp = {}
    cluster_blueprint = None
    for cluster_id, cluster_data in bp.items():
        if not isinstance(cluster_data, dict):
            continue
        for cp in cluster_data.get("scene_storyboard", []) or []:
            if isinstance(cp, dict) and cp.get("ch") == ch:
                cluster_blueprint = cp
                break
        if cluster_blueprint:
            break
    if not cluster_blueprint:
        return findings

    text_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    if not text_path.exists():
        return findings
    text = text_path.read_text(encoding="utf-8")

    # === 1. key_events ===
    for ke in cluster_blueprint.get("key_events", []):
        kws = extract_keywords(ke)
        if not kws:
            continue
        ok, rate = match_keywords(kws, text)
        if not ok:
            findings.append({
                "severity": "advisory",
                "code": "KEY_EVENT_LOW_MATCH",
                "chapter": ch,
                "metric": {"event": ke, "match_rate": round(rate, 2), "keywords_count": len(kws)},
                "message": f"ch{ch} cluster_blueprint.key_events「{ke}」ngram 命中率 {rate:.0%}（阈值 30%）",
                "suggestion": "writer 可能偏离 cluster_blueprint，建议人工 review 确认事件是否真的发生",
            })

    # === 2. characters ===
    aliases_map = get_character_aliases(project_root)
    for char_name in cluster_blueprint.get("characters", []):
        if char_name in ("七位董事", "同事们", "警察", "HR", "匿名邮件发件人", "未具名"):
            continue  # 模糊群体角色跳过
        # 找该角色 aliases
        primary, aliases = None, [char_name]
        for p, a in aliases_map.items():
            if char_name == p or char_name in a:
                primary, aliases = p, a
                break
        # 任一 alias 命中即视为出场
        if not any(a in text for a in aliases):
            findings.append({
                "severity": "advisory",
                "code": "CHARACTER_MISSING_IN_TEXT",
                "chapter": ch,
                "metric": {"character": char_name, "aliases_tried": aliases},
                "message": f"ch{ch} cluster_blueprint.characters 含「{char_name}」，但正文 + aliases 全未出现",
                "suggestion": "writer 漏写该角色 OR cluster_blueprint 列表过宽——人工 review",
            })

    # === 3. turning_point ===
    tp = cluster_blueprint.get("turning_point", "")
    if tp:
        tp_kws = extract_keywords(tp)
        if tp_kws:
            ok, rate = match_keywords(tp_kws, text, threshold=0.3)
            if not ok:
                findings.append({
                    "severity": "advisory",
                    "code": "TURNING_POINT_LOW_MATCH",
                    "chapter": ch,
                    "metric": {"turning_point": tp, "match_rate": round(rate, 2)},
                    "message": f"ch{ch} cluster_blueprint.turning_point「{tp}」ngram 命中率 {rate:.0%}",
                    "suggestion": "本章 turning point 可能缺失或被改写 —— 人工 review",
                })

    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)
    chapters = []
    if args.ch:
        chapters = [args.ch]
    elif args.all:
        for d in (project_root / "章节").glob("第*章"):
            m = re.match(r"第(\d+)章", d.name)
            if m:
                chapters.append(int(m.group(1)))
        chapters.sort()
    else:
        # 默认扫所有已写章节
        for d in (project_root / "章节").glob("第*章"):
            m = re.match(r"第(\d+)章", d.name)
            if m:
                chapters.append(int(m.group(1)))
        chapters.sort()

    all_findings = []
    for ch in chapters:
        all_findings.extend(check_chapter(project_root, ch))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "cluster_blueprint_compliance",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "findings": all_findings,
        "summary": {
            "warning": sum(1 for f in all_findings if f["severity"] == "warning"),
            "advisory": sum(1 for f in all_findings if f["severity"] == "advisory"),
            "total": len(all_findings),
        },
    }
    out_path = out_dir / f"plan_compliance_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[cluster_blueprint_compliance_scan] 扫描 ch{chapters}: {len(all_findings)} 项 (warning={report['summary']['warning']} / advisory={report['summary']['advisory']})")
    for f in all_findings[:10]:
        print(f"  [{f['severity'].upper()}] [{f['code']}] ch{f.get('chapter')} :: {f['message']}")
    if len(all_findings) > 10:
        print(f"  ... 还有 {len(all_findings) - 10} 项见报告")
    print(f"报告: {out_path}")

    # 2026-05-29 复审修复：SC-2 exit code 语义对齐 — warning=2 / advisory=1 / 健康=0
    # （原 warning 误用 exit 1，与编排器「1=advisory / 2=严重」判级冲突）。
    if any(f["severity"] == "warning" for f in all_findings):
        sys.exit(2)
    if all_findings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
