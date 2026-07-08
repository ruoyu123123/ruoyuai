"""judge_reports_archive.py — JudgeReport 归档脚本（cluster-only public CLI）

从多个来源汇集 judge 信号，组装成统一 JudgeReport schema 存盘：
1. `_数据库/.audit/ch_{ch:03d}_audit.json`     ← validator + 4 scanner 程序化分数
2. `_数据库/.wal/第{ch:03d}章_reflection.json`  ← reflector 经验记录
3. `_数据库/.wal/第{ch:03d}章_summary.json`     ← summarizer 摘要
4. `章节/第{ch:03d}章/第{ch:03d}章_changes.json` ← writer self_eval + factual
5. `章节/第{ch:03d}章/第{ch:03d}章.repair.json` ← validator-repair 修复痕迹

存盘位置：
- `_数据库/.judge_reports/ch_{ch:03d}_{judge_id}.json`（独立文件，便于 grep / consensus）
- 同时 append 摘要到 `故事块摘要.json[ch].judge_reports[]`（轻量索引）

为 meta-judge / judge_consensus 建数据基础。

用法：python judge_reports_archive.py <项目路径> --cluster <key> [--dry-run]
退出码：0 成功 / 1 部分缺失 / 2 致命

2026-07-05 链路收敛：公开 CLI 只允许 `--cluster`，禁止位置参单章归档。
`_archive_one_chapter` 仅供 cluster 展开和单元测试内部复用，不作为创作入口。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 2026-05-29 cluster 化：cluster 模式归档时把每章 judge_score/waivers 写进
# chapters[ch]，并把 cluster 级综合 judge_grade 写进 cluster 顶层。统一走
# cluster_summary_store（唯一原子写入器），不裸写 故事块摘要.json。
# 防御性 import：账本写入器缺失不影响逐章归档主流程。
try:
    import cluster_summary_store as _css  # noqa: E402
except Exception:  # pragma: no cover - 防御性
    _css = None


# grade ⇄ 数值映射（A 最优 → 高分；用于算 cluster 级综合 judge_grade）
_GRADE_TO_SCORE = {"A": 95.0, "B": 82.0, "C": 68.0, "D": 50.0}
_SCORE_TO_GRADE = [(90.0, "A"), (78.0, "B"), (60.0, "C"), (0.0, "D")]


def _grade_to_score(grade) -> float | None:
    """把字母 grade 转成 0-100 分；N/A / 非法返回 None（不计入综合）。"""
    if not isinstance(grade, str):
        return None
    return _GRADE_TO_SCORE.get(grade.strip().upper())


def _score_to_grade(score: float) -> str:
    for threshold, g in _SCORE_TO_GRADE:
        if score >= threshold:
            return g
    return "D"


def _aggregate_cluster_grade(chapter_grades: list[str]) -> str | None:
    """各章 grade 算 cluster 级综合评级：取众数，平票时取中位（偏严）。

    chapter_grades 全空返回 None（无 judge 信号 → 不写 judge_grade）。
    """
    valid = [g for g in chapter_grades if isinstance(g, str) and g.strip().upper() in _GRADE_TO_SCORE]
    if not valid:
        return None
    valid = [g.strip().upper() for g in valid]
    counts = Counter(valid)
    top = counts.most_common()
    # 众数唯一 → 直接取
    if len(top) == 1 or top[0][1] > top[1][1]:
        return top[0][0]
    # 平票 → 按数值算中位再映射回 grade（偏严：A/C 平票判 B）
    scores = sorted(_GRADE_TO_SCORE[g] for g in valid)
    mid = scores[len(scores) // 2] if len(scores) % 2 else (scores[len(scores) // 2 - 1] + scores[len(scores) // 2]) / 2
    return _score_to_grade(mid)


def _chapter_judge_signals(valid_judges: dict) -> tuple[float | None, str | None, list]:
    """从本章 valid_judges 提取 (judge_score, judge_grade, waivers)。

    judge_grade 优先取 audit-hub（程序化最权威），否则取任一有 grade 的 report。
    judge_score 由该 grade 映射数值。waivers 汇总各 report 的 waivers。
    """
    grade = None
    audit = valid_judges.get("audit-hub")
    if audit and isinstance(audit.get("overall_grade"), str) and _grade_to_score(audit["overall_grade"]) is not None:
        grade = audit["overall_grade"].strip().upper()
    else:
        for r in valid_judges.values():
            g = r.get("overall_grade")
            if isinstance(g, str) and _grade_to_score(g) is not None:
                grade = g.strip().upper()
                break
    score = _grade_to_score(grade) if grade else None
    waivers = []
    for r in valid_judges.values():
        w = r.get("waivers")
        if isinstance(w, list):
            waivers.extend(w)
    return score, grade, waivers


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_writer_self_eval_report(changes: dict, ch: int) -> dict | None:
    """从 _changes.json 提取 writer 自评信号。"""
    se = changes.get("self_eval", {})
    if not se:
        return None
    applied = se.get("applied_style", {})
    return {
        "judge_id": "writer-self-eval",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": "N/A",  # writer 不自评 grade
        "confidence": 1.0,
        "specific_findings": {
            "opening_type": applied.get("opening_type"),
            "ending_type": applied.get("ending_type"),
            "anchors_hit_count": len(applied.get("anchors_hit", [])),
            "core_techniques_count": len(applied.get("core_techniques_applied", [])),
            "subtext_count": applied.get("subtext_count", 0),
            "hooks_count": applied.get("hooks_count", 0),
            "waivers": se.get("waivers", []),
            "continuity_check": se.get("continuity_check"),
            "offscreen_actions_executed": se.get("offscreen_actions_executed", []),
        },
        "uncertainty_flags": [],
        "waivers": se.get("waivers", []),
    }


def build_validator_report_from_audit(audit: dict, ch: int) -> dict | None:
    """从 audit_hub 报告组装 validator 维度的 JudgeReport。"""
    if not audit:
        return None
    summary = audit.get("summary", {})
    fatal = summary.get("fatal", 0)
    error = summary.get("error", 0)
    warning = summary.get("warning", 0)
    waived = summary.get("waived", 0)
    grade = "A" if fatal == 0 and error == 0 else "B" if fatal == 0 and error <= 2 else "C" if fatal == 0 else "D"
    return {
        "judge_id": "audit-hub-aggregator",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": grade,
        "confidence": 0.9,
        "specific_findings": {
            "fatal": fatal,
            "error": error,
            "warning": warning,
            "waived": waived,
            "verdict": audit.get("verdict"),
            "issues_summary": [{"code": i.get("code"), "severity": i.get("severity"), "dimension": i.get("dimension")} for i in audit.get("issues", [])[:10]],
            "auto_fixed": [a.get("code") for a in audit.get("auto_fixed", [])],
            "pending_agent": [{"code": p.get("code"), "agent": p.get("agent")} for p in audit.get("pending_agent", [])],
        },
        "uncertainty_flags": [],
        "waivers": [],
    }


def build_summarizer_report(summary_data: dict, ch: int) -> dict | None:
    if not summary_data:
        return None
    # 🔴 2026-06-17 守卫：emotion 可能是 dict{value,trend} 或裸标量(int/float) → 原 .get 链对标量崩。对齐 bug-hunt 批。
    _emo = summary_data.get("emotion", {})
    _emo_d = _emo if isinstance(_emo, dict) else {}
    _emo_scalar = _emo if (isinstance(_emo, (int, float)) and not isinstance(_emo, bool)) else None
    return {
        "judge_id": "summarizer",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": "N/A",
        "confidence": 0.9,
        "specific_findings": {
            "summary_words": summary_data.get("summary_words"),
            "key_details_count": len(summary_data.get("key_details", [])),
            "emotion_value": _emo_d.get("value", _emo_scalar),
            "emotion_trend": _emo_d.get("trend"),
        },
        "uncertainty_flags": [],
        "waivers": [],
    }


def build_reflector_report(reflection: dict, ch: int) -> dict | None:
    if not reflection:
        return None
    entries = reflection.get("entries", [])
    success = [e for e in entries if e.get("category") == "success"]
    failure = [e for e in entries if e.get("category") == "failure"]
    return {
        "judge_id": "reflector",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": "N/A",
        "confidence": 0.85,
        "specific_findings": {
            "new_success_patterns": len(success),
            "new_failure_patterns": len(failure),
            "experience_ids": [e.get("id") for e in entries],
            "note": reflection.get("note", "")[:120],
        },
        "uncertainty_flags": [],
        "waivers": [],
    }


def build_truth_check_report(ch_summary_entry: dict, ch: int) -> dict | None:
    tc = ch_summary_entry.get("truth_check")
    if not tc:
        return None
    return {
        "judge_id": "writer-truth-check",
        "schema_version": "1.0",
        "chapter": ch,
        "overall_grade": "B" if tc.get("lie_count", 0) >= 1 else "A",
        "confidence": 0.95,
        "specific_findings": tc,
        "uncertainty_flags": [],
        "waivers": [],
    }


def _resolve_chapter_range(project_root: Path, cluster_key: str) -> list[int]:
    """v26: 从 事件簇.json.clusters[N].chapter_range 取章节范围。"""
    import json as _json
    shi_path = project_root / "_数据库" / "事件簇.json"
    if not shi_path.exists():
        return []
    try:
        shi = _json.loads(shi_path.read_text(encoding="utf-8"))
        for c in shi.get("clusters", []):
            cid = c.get("cluster_id", "").replace("cluster_", "")
            target = cluster_key.replace("cluster_", "")
            if cid == target:
                cr = c.get("chapter_range") or []
                if isinstance(cr, list) and len(cr) == 2:
                    return list(range(cr[0], cr[1] + 1))
    except Exception:
        pass
    return []


def _archive_one_chapter(project_root: Path, ch: int, dry_run: bool, cluster_id: str | None = None) -> dict:
    """对单章跑归档。返回 {ch, written, judges, grade, score, waivers}.

    公开 CLI 不支持单章入口；本函数只供 cluster 展开和测试复用。传入 cluster_id
    时，额外把本章 judge_score/waivers 通过 cluster_summary_store.patch_chapter
    写进 chapters[ch]（v2 cluster 账本契约）。
    """
    db = project_root / "_数据库"

    # 收集源数据
    audit = load_json(db / ".audit" / f"ch_{ch:03d}_audit.json", {})
    reflection = load_json(db / ".wal" / f"第{ch:03d}章_reflection.json", {})
    summary = load_json(db / ".wal" / f"第{ch:03d}章_summary.json", {})
    changes = load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})
    # 2026-05-30 北极星复审：v2 账本 ch_entry 在 clusters[].chapters[str(ch)]（顶层无 chapters）→ 原恒
    # 取空 → writer-truth-check judge 信号永久缺失。先读旧顶层兼容，再回退 clusters[].chapters。
    ch_summary_full = load_json(db / "故事块摘要.json", {})
    ch_entry = next((c for c in ch_summary_full.get("chapters", []) if isinstance(c, dict) and c.get("ch") == ch), {})
    if not ch_entry:
        for _c in ch_summary_full.get("clusters", []) or []:
            if isinstance(_c, dict):
                _r = (_c.get("chapters") or {}).get(str(ch))
                if isinstance(_r, dict):
                    ch_entry = {**_r, "ch": ch}
                    break

    judges = {
        "audit-hub": build_validator_report_from_audit(audit, ch),
        "writer-self-eval": build_writer_self_eval_report(changes, ch),
        "summarizer": build_summarizer_report(summary, ch),
        "reflector": build_reflector_report(reflection, ch),
        "writer-truth-check": build_truth_check_report(ch_entry, ch),
    }
    valid_judges = {k: v for k, v in judges.items() if v is not None}
    print(f"[judge_reports_archive] ch{ch}: 汇集 {len(valid_judges)} 个 judge 信号")

    archive_dir = db / ".judge_reports"
    archive_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for judge_id, report in valid_judges.items():
        archive_path = archive_dir / f"ch_{ch:03d}_{judge_id}.json"
        if dry_run:
            print(f"  [DRY] would write: {archive_path}")
        else:
            save_json(archive_path, report)
            written.append(judge_id)
        grade = report.get("overall_grade", "?")
        conf = report.get("confidence", "?")
        print(f"  [{judge_id}] grade={grade} confidence={conf}")

    # 累积 judge_reports 摘要到账本（2026-05-30 北极星复审：原 ch_entry 在 v2 是 clusters 回退副本 +
    # save_json 整文件覆盖 → 写回无效 + 并发覆盖 builder 风险。改 cluster_summary_store.patch_chapter
    # 原子合并进 clusters[].chapters[str(ch)]）。judge 报告本体已落 .judge_reports/，此处仅补账本摘要。
    if not dry_run and valid_judges and _css is not None:
        _summaries = [{
            "judge_id": jid, "grade": r.get("overall_grade"),
            "confidence": r.get("confidence"),
            "ts": datetime.now().isoformat(timespec="seconds"),
        } for jid, r in valid_judges.items()]
        try:
            import cluster_lookup as _cl  # 局部别名，不遮蔽模块级 _css
            _cid = _cl.ch_to_cluster_id(project_root, ch)
            if _cid:
                _css.patch_chapter(project_root, _cid, ch, {"judge_reports": _summaries})
                print(f"  [OK] 故事块摘要 ch{ch}.judge_reports 已更新（{len(_summaries)} 条摘要）")
        except Exception as _e:
            print(f"  [warn] judge_reports 摘要落账本失败（不影响 .judge_reports/ 落盘）: {_e}")

    # 2026-05-29 cluster 化：提取本章 judge_score/grade/waivers
    score, grade, waivers = _chapter_judge_signals(valid_judges)

    # cluster 模式：把每章 judge_score/waivers 写进 v2 cluster 账本 chapters[ch]
    # （走 cluster_summary_store 原子写入器，不裸写 故事块摘要.json）。
    if cluster_id and not dry_run and _css is not None and valid_judges:
        ch_patch = {}
        if score is not None:
            ch_patch["judge_score"] = score
        if waivers:
            ch_patch["waivers"] = waivers
        if ch_patch:
            try:
                _css.patch_chapter(project_root, cluster_id, ch, ch_patch)
                print(f"  [OK] cluster 账本 chapters[{ch}] 写入 judge_score={score} waivers={len(waivers)}")
            except Exception as e:  # pragma: no cover - 防御性
                print(f"  [WARN] cluster 账本 chapters[{ch}] 写入失败（跳过不崩）: {e}")

    return {"ch": ch, "written": len(written), "judges": list(valid_judges.keys()),
            "grade": grade, "score": score, "waivers": waivers}


def main():
    ap = argparse.ArgumentParser(description="judge_reports_archive · cluster-only public CLI")
    ap.add_argument("project")
    # 保留隐藏位置参只用于给旧调用返回清晰错误；不再作为公开入口执行。
    ap.add_argument("_deprecated_chapter", nargs="?", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--cluster", type=str, default=None,
                    help="cluster key (e.g. '001' 或 'cluster_001') · 自动展开本 cluster 全部章节归档")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)

    if args._deprecated_chapter is not None:
        print("[FATAL] 位置参单章归档已废弃；必须指定 --cluster <key> 走 cluster-save-state 主链路", file=sys.stderr)
        return 2

    if not args.cluster:
        print("[FATAL] 必须指定 --cluster <key> 走 cluster-save-state 主链路", file=sys.stderr)
        return 2

    chapters = _resolve_chapter_range(project_root, args.cluster)
    if not chapters:
        print(f"[FATAL] cluster {args.cluster} 未在 事件簇.json 找到 chapter_range", file=sys.stderr)
        return 2
    cluster_id = "cluster_" + args.cluster.replace("cluster_", "")
    print(f"[judge_reports_archive · cluster mode] {cluster_id} 展开 {len(chapters)} 章: {chapters}")
    results = [_archive_one_chapter(project_root, ch, args.dry_run, cluster_id=cluster_id) for ch in chapters]

    # 算 cluster 级综合 judge_grade（各章 grade 众数/中位）→ upsert_cluster 写顶层
    chapter_grades = [r.get("grade") for r in results if r.get("grade")]
    cluster_grade = _aggregate_cluster_grade(chapter_grades)
    if not args.dry_run and _css is not None and cluster_grade is not None:
        try:
            _css.upsert_cluster(project_root, cluster_id, {"judge_grade": cluster_grade})
            print(f"[OK] cluster 账本 {cluster_id}.judge_grade = {cluster_grade}"
                  f"（来自 {len(chapter_grades)} 章 grades: {chapter_grades}）")
        except Exception as e:  # pragma: no cover - 防御性
            print(f"[WARN] cluster 账本 judge_grade 写入失败（跳过不崩）: {e}")
    elif cluster_grade is None:
        print(f"[WARN] cluster {cluster_id} 无有效 judge grade（各章 judge 信号缺失），跳过 judge_grade 写入")

    print(f"\n[OK] cluster {args.cluster} judge_reports 归档完成 {len(chapters)} 章")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    # 公开 CLI 的所有失败码由 main() 返回，__main__ 负责透传给 shell。
    sys.exit(main())
