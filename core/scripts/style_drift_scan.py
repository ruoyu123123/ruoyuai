"""style_drift_scan.py — 跨章风格漂移扫描（v17.5 / P3.3）

扫描历史章节的实际锚点频率、opening/ending type 分布，与蒸馏库 anchor_strategy
对比。发现违规（如"绯红月光每3章不超过2次"被违反）则告警。

用法：
    python style_drift_scan.py <项目路径> [--last-n 10] [--strict]

输出：
    锚点频率表 + 违规清单 + 建议
"""

import sys
import json
import re
from pathlib import Path
from collections import Counter, defaultdict


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_chapter_files(project_root: Path) -> list[tuple[int, Path]]:
    """返回 [(ch_num, path), ...] 排序后的章节文件列表。"""
    out = []
    # 嵌套布局优先
    for d in project_root.glob("章节/第*章"):
        m = re.match(r"第(\d+)章", d.name)
        if m:
            ch = int(m.group(1))
            for f in d.glob(f"第{ch:03d}章*.txt"):
                out.append((ch, f))
                break
            else:
                for f in d.glob(f"第{ch}章*.txt"):
                    out.append((ch, f))
                    break
    # 平铺布局兜底
    if not out:
        for f in project_root.glob("第*章*.txt"):
            m = re.match(r"第(\d+)章", f.name)
            if m:
                out.append((int(m.group(1)), f))
    out.sort(key=lambda x: x[0])
    return out


def scan_anchor_frequency(chapters: list[tuple[int, Path]],
                          anchors_to_track: list[str]) -> dict[str, dict[int, int]]:
    """返回 {anchor: {ch_num: count}}。"""
    result = {a: {} for a in anchors_to_track}
    for ch, path in chapters:
        text = path.read_text(encoding="utf-8")
        # 截到 CHANGES 前
        body_end = text.find("---CHANGES")
        body = text[:body_end] if body_end > 0 else text
        for a in anchors_to_track:
            cnt = body.count(a)
            result[a][ch] = cnt
    return result


def check_strategy_violations(freq: dict, anchor_strategy: list[dict]) -> list[dict]:
    """对照 anchor_strategy 检查每个 anchor 的实际使用是否违规。"""
    violations = []
    for entry in anchor_strategy:
        name = entry.get("元素", "")
        strategy = entry.get("策略", "")
        if not name or name not in freq:
            continue
        ch_counts = freq[name]

        # 解析"每 N 章不超过 M 次"
        m = re.search(r"每\s*(\d+)\s*章不超过\s*(\d+)\s*次", strategy)
        if m:
            window = int(m.group(1))
            limit = int(m.group(2))
            sorted_chs = sorted(ch_counts.keys())
            for i in range(len(sorted_chs)):
                ch = sorted_chs[i]
                window_chs = [c for c in sorted_chs if ch - window + 1 <= c <= ch]
                window_count = sum(ch_counts[c] for c in window_chs)
                if window_count > limit:
                    violations.append({
                        "anchor": name,
                        "rule": strategy,
                        "violation": f"ch {window_chs[0]}-{ch} 共 {window_count} 次（超 {limit}）",
                        "severity": "🔴 strict" if window_count > limit + 2 else "🟡 mild",
                    })

        # 解析"每章 le N 次"或"每章不超过 N 次"
        m2 = re.search(r"每章\s*(?:le|不超过)\s*(\d+)\s*次", strategy)
        if m2:
            limit = int(m2.group(1))
            for ch, cnt in ch_counts.items():
                if cnt > limit:
                    violations.append({
                        "anchor": name,
                        "rule": strategy,
                        "violation": f"ch {ch} 单章 {cnt} 次（超 {limit}）",
                        "severity": "🟡 mild",
                    })
    return violations


def scan_opening_types_distribution(chapters: list[tuple[int, Path]], summaries: list[dict]) -> dict:
    """对照故事块摘要里的 applied_style.opening_type，看实际分布 vs 蒸馏分布。"""
    ch_to_opening = {}
    for s in summaries:
        ch = s.get("ch")
        op = s.get("applied_style", {}).get("opening_type")
        if ch and op:
            ch_to_opening[ch] = op
    counter = Counter(ch_to_opening.values())
    return {
        "ch_to_type": ch_to_opening,
        "type_counts": dict(counter),
        "total": len(ch_to_opening),
    }


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    last_n = 10
    strict = "--strict" in args
    for i, a in enumerate(args):
        if a == "--last-n" and i + 1 < len(args):
            last_n = int(args[i + 1])

    db = project_root / "_数据库"
    style = load_json(db / "作者风格.json", {})
    anchor_strategy = (
        style.get("cross_chapter_diversity", {}).get("env_anchor_high_risk_elements", [])
    )
    summaries = load_json(db / "故事块摘要.json", {}).get("chapters", [])

    chapters = find_chapter_files(project_root)
    if not chapters:
        print(f"[FATAL] 在 {project_root} 找不到章节文件")
        sys.exit(2)
    if last_n and last_n > 0:
        chapters = chapters[-last_n:]

    print(f"[Style Drift Scan] 项目：{project_root.name}")
    print(f"  扫描章节：{chapters[0][0]}-{chapters[-1][0]} 共 {len(chapters)} 章")
    print(f"  锚点策略：{len(anchor_strategy)} 条")

    anchors = [e.get("元素", "") for e in anchor_strategy if e.get("元素")]
    freq = scan_anchor_frequency(chapters, anchors)

    # 输出频率表
    print(f"\n[锚点频率（最近 {len(chapters)} 章）]")
    for a in anchors:
        ch_counts = freq.get(a, {})
        total = sum(ch_counts.values())
        ch_with_anchor = sum(1 for c in ch_counts.values() if c > 0)
        if total > 0:
            print(f"  {a}: 共 {total} 次（出现于 {ch_with_anchor}/{len(chapters)} 章）")

    # 违规检查
    violations = check_strategy_violations(freq, anchor_strategy)
    print(f"\n[违规清单] {len(violations)} 条")
    for v in violations:
        print(f"  {v['severity']} {v['anchor']}: {v['violation']}（规则：{v['rule']}）")

    # opening type 分布
    op_dist = scan_opening_types_distribution(chapters, summaries)
    print(f"\n[Opening type 实际分布（{op_dist['total']} 章已记录）]")
    for t, c in op_dist["type_counts"].items():
        print(f"  {t}: {c} 次 ({c/op_dist['total']*100:.0f}%)")

    # 检查连续 3 章同型违规
    op_violations = []
    ch_to_type = op_dist["ch_to_type"]
    sorted_chs = sorted(ch_to_type.keys())
    for i in range(len(sorted_chs) - 2):
        c1, c2, c3 = sorted_chs[i], sorted_chs[i+1], sorted_chs[i+2]
        if c2 == c1 + 1 and c3 == c2 + 1:
            t1, t2, t3 = ch_to_type[c1], ch_to_type[c2], ch_to_type[c3]
            if t1 == t2 == t3:
                op_violations.append(f"ch {c1}-{c3} 连续 3 章同型 '{t1}' 违反 opening_rule")
    if op_violations:
        print(f"\n[Opening 轮拿违规] {len(op_violations)} 条")
        for v in op_violations:
            print(f"  🔴 {v}")

    if strict and (violations or op_violations):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
