"""writer_truth_check.py — Writer 自评真实性检测 + 回写故事块摘要（v17.5 / B2.1+B2.3+B0.3）

功能：
1. 读章节 txt 末尾的 ---CHANGES_SELF_EVAL--- 段，提取 writer 申报的 applied_style
2. 独立从正文识别 opening_type / ending_line / anchors 等
3. 对比申报 vs 独立提取 → 生成 truth_report
4. 把 applied_style 回写到 故事块摘要[ch].applied_style（B2.1）
5. 把 truth_report 写入 故事块摘要[ch].truth_check

用法：
    python writer_truth_check.py <项目路径> <章节号> [--write-back]
    python writer_truth_check.py <项目路径> --all-history [--write-back]

退出码：
    0  通过
    1  撒谎检测命中（writer 自评与正文不符）
    2  致命错误
"""

import sys
import json
import re
from pathlib import Path

# v18：统一章节读写走 chapter_io
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_chapter_file(project_root: Path, ch: int) -> Path | None:
    """定位章节正文文件。v18：委托 chapter_io.find_body_file。"""
    return cio.find_body_file(project_root, ch)


# 独立识别开头/结尾 type 的简化规则（不依赖 writer 自评）

OPENING_TYPE_PATTERNS = [
    # tuple: (type_name, predicate_fn)
    ("拟声定格", lambda first_lines: bool(re.match(r"^[^\n]{1,12}——", first_lines.split("\n")[0]))
                                    or bool(re.search(r"^(咯|啪|嗒|哒|轰|咚|哗|砰|咳|噗|滋|嘎|吱|咔)——", first_lines))),
    ("纯对话开场", lambda first_lines: first_lines.lstrip().startswith(("\"", "「", "「"))),
    ("时间地点锚点", lambda first_lines: bool(re.match(r"^[^\n]{1,30}(?:点|时|刻|早晨|清晨|夜里|凌晨|下午|傍晚|黄昏)", first_lines))),
    ("人物内心吐槽", lambda first_lines: any(s in first_lines[:200] for s in ["他想", "他笑", "他骂", "她想", "呃……", "他妈"])),
    ("心理铺陈", lambda first_lines: any(s in first_lines[:200] for s in ["他记得", "他在想", "他做梦", "他不知"])),
    ("钩子回音式", lambda first_lines: bool(re.match(r"^[^\n]{1,25}(?:仍然|还在|依旧|又|再)", first_lines))),
    ("动作承接", lambda first_lines: bool(re.match(r"^[^\n]{1,30}(?:推|拉|按|抬|放|拿|走|跑|站|坐|蹲|睁|闭|握|抓|举|挥|甩|扔|扔|跳)", first_lines))),
    ("感官切入", lambda first_lines: any(s in first_lines[:100] for s in ["闻到", "听见", "感到", "触到", "看见"])),
    ("场景型", lambda first_lines: True),  # 兜底
]


def _strip_chapter_title(body: str) -> str:
    """去掉首行 '第NNN章 ...' 标题，返回正文。"""
    lines = body.split("\n")
    out = []
    skipped = False
    for ln in lines:
        if not skipped and re.match(r"^第\d+章", ln.strip()):
            skipped = True
            continue
        out.append(ln)
    return "\n".join(out).lstrip()


def identify_opening_type(body: str) -> str:
    cleaned = _strip_chapter_title(body)
    first_lines = cleaned[:300].strip()
    for type_name, pred in OPENING_TYPE_PATTERNS:
        try:
            if pred(first_lines):
                return type_name
        except Exception:
            continue
    return "场景型"


ENDING_TYPE_PATTERNS = [
    ("拟声硬收", lambda last_lines: bool(re.search(r"(咯|啪|嗒|哒|轰|咚|哗|砰|咳)——\s*$", last_lines.strip()))),
    ("动作留白", lambda last_lines: bool(re.search(r"(放|推|拉|按|举|抬|蹲|站|走|坐|看|闭|睁|握|垂)[^\n]{0,15}[。\.]\s*$", last_lines.strip()))),
    ("对话悬念", lambda last_lines: last_lines.rstrip().endswith(("\"", "」", "？", "?"))),
    ("独立短句", lambda last_lines: len(last_lines.strip().split("\n")[-1]) <= 12),
    ("信息悬念", lambda last_lines: any(s in last_lines[-200:] for s in ["也许", "可能", "或许", "不知道", "不确定"])),
    ("信息炸弹", lambda last_lines: any(s in last_lines[-200:] for s in ["——", "："])),
    ("场景硬收", lambda last_lines: True),
]


def identify_ending_type(body: str) -> str:
    last_lines = body[-300:]
    for type_name, pred in ENDING_TYPE_PATTERNS:
        try:
            if pred(last_lines):
                return type_name
        except Exception:
            continue
    return "场景硬收"


def extract_first_line(body: str) -> str:
    cleaned = _strip_chapter_title(body)
    for line in cleaned.split("\n"):
        line = line.strip()
        if line:
            return line
    return ""


def extract_last_line(body: str) -> str:
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    return lines[-1] if lines else ""


def truth_check_chapter(project_root: Path, ch: int) -> dict:
    """单章撒谎检测。v18：正文走 cio.read_body()，自评走 cio.read_changes()。"""
    if not find_chapter_file(project_root, ch) and not cio.changes_path(project_root, ch).is_file():
        return {"ch": ch, "error": f"找不到章节 txt"}

    body = cio.read_body(project_root, ch)
    changes = cio.read_changes(project_root, ch)
    factual = changes.get("factual") or {}
    self_eval = changes.get("self_eval") or {}

    # writer 自评（self_eval 段优先，回退 factual 根字段——兼容旧稿）
    applied_writer = self_eval.get("applied_style") or factual.get("applied_style") or {}
    declared_open = applied_writer.get("opening_type", "")
    declared_open_line = applied_writer.get("opening_line", "")
    declared_end = applied_writer.get("ending_type", "")
    declared_end_line = applied_writer.get("ending_line", "")
    declared_anchors = applied_writer.get("anchors_hit", [])

    # 独立提取
    actual_first = extract_first_line(body)
    actual_last = extract_last_line(body)
    detected_open_type = identify_opening_type(body)
    detected_end_type = identify_ending_type(body)

    # 锚点验证：每个 declared_anchor 是否真在正文出现
    anchors_truth = []
    for a in declared_anchors:
        appears = a in body
        anchors_truth.append({"anchor": a, "in_body": appears})

    # opening_line 真实性
    opening_line_match = (
        declared_open_line in actual_first[:200]
        or actual_first[:30] in declared_open_line[:30]
    ) if declared_open_line else None
    # ending_line 真实性
    ending_line_match = (
        declared_end_line in body[-300:]
        or actual_last[:30] in declared_end_line[:30]
    ) if declared_end_line else None

    # type 匹配（独立提取 == 自评）
    open_type_match = declared_open == detected_open_type if declared_open else None
    end_type_match = declared_end == detected_end_type if declared_end else None

    # 撒谎指数
    lies = []
    if opening_line_match is False:
        lies.append({"field": "opening_line", "declared": declared_open_line[:40], "actual": actual_first[:40]})
    if ending_line_match is False:
        lies.append({"field": "ending_line", "declared": declared_end_line[:40], "actual": actual_last[:40]})
    missing_anchors = [a["anchor"] for a in anchors_truth if not a["in_body"]]
    if missing_anchors:
        lies.append({"field": "anchors_hit", "missing": missing_anchors})

    return {
        "ch": ch,
        "declared_opening_type": declared_open,
        "detected_opening_type": detected_open_type,
        "opening_type_match": open_type_match,
        "declared_ending_type": declared_end,
        "detected_ending_type": detected_end_type,
        "ending_type_match": end_type_match,
        "opening_line_match": opening_line_match,
        "ending_line_match": ending_line_match,
        "anchors_truth": anchors_truth,
        "lies_detected": lies,
        "lie_count": len(lies),
        "writer_applied_style_raw": applied_writer,
    }


def write_back(project_root: Path, report: dict):
    """B2.1 回写 applied_style + truth_check 到故事块摘要。

    2026-05-29 复审修复（L2）：v2 cluster 化后 故事块摘要.json = {clusters:[{chapters:{ch:rec}}]}，
    旧实现写顶层 chapters[] list 会与 v2 clusters[] 并存冲突（污染账本数据模型）。
    改走 cluster_summary_store.patch_chapter：用 cluster_lookup.ch_to_cluster_id 由章号反查
    所属 cluster，把 truth_check / applied_style 合并进 clusters[].chapters[ch] 章记录，
    原子写 + 深合并（不丢 builder 已写的其它预算字段）。

    cluster 反查不到（fluid 未回填 chapter_range）→ 不强写顶层 chapters[]（禁止污染 v2 账本），
    仅打印告警并跳过回写（truth_report 仍由 main 打印 / 退出码体现，不丢检测结论）。
    """
    from datetime import datetime as _dt

    ch = report["ch"]

    # 组装本章 truth_check patch
    chapter_patch: dict = {
        "truth_check": {
            "opening_type_match": report.get("opening_type_match"),
            "ending_type_match": report.get("ending_type_match"),
            "opening_line_match": report.get("opening_line_match"),
            "ending_line_match": report.get("ending_line_match"),
            "lie_count": report.get("lie_count"),
            "lies_detected": report.get("lies_detected"),
        },
        "_truth_check_last_by": "writer_truth_check",
        "_truth_check_at": _dt.now().isoformat(timespec="seconds"),
    }
    if report.get("writer_applied_style_raw"):
        chapter_patch["applied_style"] = report["writer_applied_style_raw"]

    # 章号 → cluster_id（SC-5：禁止 f"cluster_{ch:03d}" 机械拼接，用 ch_to_cluster_id 反查）
    try:
        import cluster_lookup as _cl
        import cluster_summary_store as _css
    except Exception as e:  # 模块缺失 → 不污染账本，跳过回写
        print(f"[WARN] ch{ch} truth_check 回写跳过：cluster 工具不可用（{e}）", file=sys.stderr)
        return

    cluster_id = _cl.ch_to_cluster_id(project_root, ch)
    if not cluster_id:
        print(f"[WARN] ch{ch} truth_check 回写跳过：章号未落入任何 cluster 的 chapter_range"
              f"（fluid 未回填）· 不写顶层 chapters[] 以免污染 v2 账本", file=sys.stderr)
        return

    # 原子 + 深合并落账（cluster_summary_store 内部 with_file_lock 防并发丢更新）
    _css.patch_chapter(project_root, cluster_id, ch, chapter_patch)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    write = "--write-back" in args
    if "--all-history" in args:
        # 扫描所有已写章节
        # 2026-05-29 复审修复（L2）：v2 账本 = {clusters:[{chapters:{ch:rec}}]}，
        # 顶层 chapters 已废弃。优先从 clusters[].chapters 拍平章号；旧顶层 chapters
        # （list/dict）仍兼容回退。
        summary = load_json(project_root / "_数据库" / "故事块摘要.json", {})
        chs: list[int] = []
        clusters = summary.get("clusters")
        if isinstance(clusters, list):
            for c in clusters:
                if not isinstance(c, dict):
                    continue
                for k in (c.get("chapters") or {}).keys():
                    try:
                        chs.append(int(k))
                    except (ValueError, TypeError):
                        pass
        if not chs:
            chapters = summary.get("chapters", [])
            if isinstance(chapters, dict):
                chs = [int(k) for k in chapters.keys()]
            else:
                chs = [c.get("ch") for c in chapters if isinstance(c, dict) and c.get("ch")]
        target_chs = sorted(set(c for c in chs if isinstance(c, int)))
    else:
        target_chs = [int(args[1])]

    total_lies = 0
    for ch in target_chs:
        report = truth_check_chapter(project_root, ch)
        if "error" in report:
            print(f"ch {ch}: {report['error']}")
            continue
        print(f"\n== ch {ch} 撒谎检测 ==")
        print(f"  opening_type: 自评={report['declared_opening_type']!r}  独立={report['detected_opening_type']!r}  match={report['opening_type_match']}")
        print(f"  ending_type:  自评={report['declared_ending_type']!r}  独立={report['detected_ending_type']!r}  match={report['ending_type_match']}")
        print(f"  opening_line match: {report['opening_line_match']}")
        print(f"  ending_line match: {report['ending_line_match']}")
        anchors_summary = [(a["anchor"], a["in_body"]) for a in report["anchors_truth"]]
        print(f"  anchors_hit: {anchors_summary}")
        if report["lies_detected"]:
            print(f"  🔴 撒谎：{report['lie_count']} 条")
            for lie in report["lies_detected"]:
                print(f"    - {lie}")
            total_lies += report["lie_count"]
        else:
            print(f"  ✅ 无撒谎")
        if write:
            write_back(project_root, report)

    print(f"\n[Total] {len(target_chs)} 章 / 共 {total_lies} 条撒谎")
    sys.exit(1 if total_lies > 0 else 0)


if __name__ == "__main__":
    main()
