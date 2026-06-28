#!/usr/bin/env python3
"""reconcile.py — /reconcile 程序驱动脚本（2026-06-22 G2 P0b 实装）

把 /reconcile 5 step plan 从「NOT-YET 空壳模板」落为「确定性 driver + gen-model 修正」
端到端可执行命令。修复的根因：reconcile.md 命令文档明文提到调 `gen_fixer.py
--mode validator-repair`，但「识别变更 / 影响半径 / 跨章一致性 / 报告」整条链路
从来没有任何 .py 实体 —— 主代理只能照 md 散文手抓 grep + 推断，program-driven
M3 orchestrator 把它判 NOT-YET 拒绝。本脚本补缺：把 5 个 step 落成 5 个 mode。

【五个 mode】
  --mode detect-changes   从 spec.json（或 CLI 参数）规范化 locked_fact 变更声明，写
                          {workspace}/change.json（含 type/target/field/before/after/
                          target_card_path）。**只识别变更**·不动正文不动档案。
  --mode compute-radius   读 change.json·Grep 所有 章节/第*章/*.txt（物理章权威清单）
                          + Read 人物卡.locked_facts·分等级（高=直接命中 before 关键词
                          / 中=间接同义词 / 低=仅档案）·写 {workspace}/radius.json。
  --mode patch            读 radius.json·对每个受影响章节调 gen_fixer.py --mode
                          validator-repair（gen-model · advisory · 失败不阻断）·
                          写 {workspace}/patch_log.json（per-章 ok/skipped/失败）。
                          策略默认 strategy=A（全部自动修复）·--strategy B/C/D 可控。
  --mode audit            读 radius.json · 调 audit_hub.py 或 locked_fact_cross_scene_scanner
                          对 patch 后章节做跨章一致性验证·写 {workspace}/audit.json。
  --mode propagation-check 🔴 W5：扫受影响章节·统计正文/changes 里 before 旧值残留·列『未传播
                          章节』→ {workspace}/propagation_debt.json（advisory·只标债务绝不改正文）。
  --mode report           汇总 step1-4 + 传播校验 · 输出 {workspace}/reconcile_report.json
                          （summary/changed_files/skipped/audit_verdict/propagation_debt/git_tag）。

【workspace 选择】
  --reconcile-id ID  默认 "current"·plan 单次 run 用 "current" 覆盖式写
                     `{project_root}/_数据库/.reconcile/<id>/`。多次 reconcile 想保历史
                     传时间戳 ID·workspace 隔离·report 写各自目录。

【seed spec.json 形态（detect-changes 输入）】
  方式 A：调用前主代理/GUI 把 spec 落到 `{project_root}/_数据库/.reconcile/{id}/spec.json`：
    {"change_type": "character_field",
     "target": "克莱恩",
     "field": "occupation",
     "before": "店员",
     "after": "侦探"}
  方式 B：reconcile.py --mode detect-changes --change-type character_field \\
                       --target 克莱恩 --field occupation --before 店员 --after 侦探

【北极星纪律】
- 全 advisory shadow · hard_gate 12 码不变（gen_fixer 修复后 audit_hub 仍可能报
  hard_gate · 但本命令本身不引入新 hard_gate；只产顾问建议）。
- 作者档第一权威（gen_fixer validator-repair 内部已尊重作者档·本脚本不二次注入）。
- cluster 为单位 · 章节只是格式：影响半径以「章节 txt 文件」物理粒度报告（reconcile
  传统就是章粒度修复 · 不变更主轨）。

【真 API · 不省钱（feedback_real_api_tests_no_economize）】
- patch mode 真发 gen_fixer 调用 · 不 dry-run · 不缩 max_tokens。
- 单 reconcile run 视影响章节数烧 $1-5；测试时可 --max-chapters 限规模。

【退出码】
  0 = 成功 · 1 = 局部失败（advisory · 走 step）· 2 = fatal（spec 缺失 / 项目不存在）
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from log_util import get_logger
    logger = get_logger(__name__)
except Exception:  # noqa: BLE001 — log_util 异常不阻断 reconcile
    import logging
    logger = logging.getLogger("reconcile")


SCHEMA_VERSION = 1
REPORT_NAME = "reconcile_report.json"


# ============ workspace 工具 ============
def _resolve_project(project: str) -> Path:
    p = Path(project).resolve()
    if not p.is_dir():
        raise SystemExit(f"[FATAL] 项目路径不存在: {p}")
    return p


def _workspace_dir(project_root: Path, reconcile_id: str) -> Path:
    ws = project_root / "_数据库" / ".reconcile" / reconcile_id
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读 JSON 失败 {path}: {e}")
        return None


# ============ step 1 detect-changes ============
def _seed_spec_from_args(args) -> dict:
    """CLI 参数 → 规范 spec dict。"""
    return {
        "change_type": args.change_type or "character_field",
        "target": args.target or "",
        "field": args.field or "",
        "before": args.before or "",
        "after": args.after or "",
        "_seed": "cli_args",
    }


def detect_changes(project_root: Path, workspace: Path, args) -> int:
    """规范化 locked_fact 变更声明 · 写 workspace/change.json。

    输入优先级（任一存在即可）：
      1. workspace/spec.json（GUI / 主代理预先落盘 · 推荐）
      2. CLI args --change-type/--target/--field/--before/--after（脚本测试用）

    输出 change.json schema:
      {schema_version, reconcile_id, change_type, target, field, before, after,
       target_card_path, detected_at, _seed}
    """
    spec_path = workspace / "spec.json"
    spec: dict | None = _read_json(spec_path)
    if not spec:
        if not (args.target or args.before):
            print(f"[FATAL] detect-changes 缺 spec：未找到 {spec_path}·CLI 也没传 "
                  f"--target/--before/--after", file=sys.stderr)
            return 2
        spec = _seed_spec_from_args(args)

    target = (spec.get("target") or "").strip()
    before = (spec.get("before") or "").strip()
    if not target and not before:
        print("[FATAL] spec 缺关键字段：target/before 至少一个非空", file=sys.stderr)
        return 2

    # 试着定位 target 在人物卡.json 里的 card path（供后续 step 校验/更新）
    cards_path = project_root / "_数据库" / "人物卡.json"
    target_card_idx = -1
    target_card_name = ""
    if cards_path.exists():
        try:
            cards_doc = json.loads(cards_path.read_text(encoding="utf-8-sig"))
            for i, c in enumerate(cards_doc.get("characters", []) or []):
                if target and (c.get("name") == target or c.get("id") == target
                               or target in (c.get("name_aliases") or [])):
                    target_card_idx = i
                    target_card_name = c.get("name") or target
                    break
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"人物卡读失败（不阻断）: {e}")

    change = {
        "schema_version": SCHEMA_VERSION,
        "reconcile_id": args.reconcile_id,
        "change_type": spec.get("change_type") or "character_field",
        "target": target,
        "target_card_index": target_card_idx,
        "target_card_name": target_card_name,
        "field": (spec.get("field") or "").strip(),
        "before": before,
        "after": (spec.get("after") or "").strip(),
        "detected_at": datetime.now().isoformat(timespec="seconds"),
        "_seed": spec.get("_seed", "spec_json"),
    }
    out = workspace / "change.json"
    _write_json(out, change)
    print(f"[OK] detect-changes · target={change['target']} field={change['field']} "
          f"before='{change['before'][:30]}' → after='{change['after'][:30]}' "
          f"card_idx={target_card_idx}", file=sys.stderr)
    print(f"     report: {out}", file=sys.stderr)
    return 0


# ============ step 2 compute-radius ============
_IMPACT_HIGH = "high"
_IMPACT_MEDIUM = "medium"
_IMPACT_LOW = "low"


def _list_chapter_files(project_root: Path) -> list[Path]:
    """物理章节清单（reconcile.md §2 权威源纪律：以物理目录为权威）。"""
    chap_dir = project_root / "章节"
    if not chap_dir.exists():
        return []
    out: list[Path] = []
    for d in sorted(chap_dir.glob("第*章")):
        # 排除虚拟 ch>=9000（audit_hub cluster mode 占位）
        m = re.search(r"第(\d+)章", d.name)
        if m and int(m.group(1)) >= 9000:
            continue
        if d.is_dir():
            txt = d / f"{d.name}.txt"
            if txt.exists():
                out.append(txt)
    return out


def _scan_chapter(chapter_path: Path, before: str, target: str) -> dict:
    """单章扫描·返回 impact_level + matched_lines + sample。

    high   = 正文出现 before 关键词（直接描写）
    medium = 正文出现 target 名字但不出现 before（间接提及·context 可能涉及变更）
    low    = changes.json 中提及（仅档案）
    none   = 全无关
    """
    try:
        body = chapter_path.read_text(encoding="utf-8")
    except OSError as e:
        return {"impact_level": "none", "_read_error": str(e),
                "matched_lines": [], "sample": ""}

    matched_lines: list[int] = []
    sample = ""
    if before and before in body:
        # 抓所有命中行号
        for i, line in enumerate(body.splitlines(), 1):
            if before in line:
                matched_lines.append(i)
                if not sample:
                    sample = line.strip()[:200]
        return {"impact_level": _IMPACT_HIGH,
                "matched_lines": matched_lines[:50], "sample": sample}

    if target and target in body:
        for i, line in enumerate(body.splitlines(), 1):
            if target in line:
                matched_lines.append(i)
                if not sample:
                    sample = line.strip()[:200]
                if len(matched_lines) >= 5:
                    break
        return {"impact_level": _IMPACT_MEDIUM,
                "matched_lines": matched_lines, "sample": sample}

    # 低影响：changes.json 提及
    changes_path = chapter_path.parent / f"{chapter_path.stem}_changes.json"
    if changes_path.exists():
        try:
            ctext = changes_path.read_text(encoding="utf-8-sig")
            if target and target in ctext:
                return {"impact_level": _IMPACT_LOW,
                        "matched_lines": [], "sample": "changes.json 提及"}
        except OSError:
            pass
    return {"impact_level": "none", "matched_lines": [], "sample": ""}


def compute_radius(project_root: Path, workspace: Path, args) -> int:
    """读 change.json · 扫所有章节 · 写 radius.json。"""
    change_path = workspace / "change.json"
    change = _read_json(change_path)
    if not change:
        print(f"[FATAL] compute-radius: change.json 不存在·先跑 --mode detect-changes",
              file=sys.stderr)
        return 2

    chapters = _list_chapter_files(project_root)
    affected: list[dict] = []
    counts = {_IMPACT_HIGH: 0, _IMPACT_MEDIUM: 0, _IMPACT_LOW: 0, "none": 0}
    for ch_path in chapters:
        result = _scan_chapter(ch_path, change.get("before", ""),
                               change.get("target", ""))
        lvl = result["impact_level"]
        counts[lvl] = counts.get(lvl, 0) + 1
        if lvl != "none":
            rel = ch_path.relative_to(project_root)
            affected.append({
                "chapter_path": str(rel).replace("\\", "/"),
                "chapter_name": ch_path.stem,
                "impact_level": lvl,
                "matched_lines": result["matched_lines"],
                "sample": result["sample"],
            })

    radius = {
        "schema_version": SCHEMA_VERSION,
        "reconcile_id": args.reconcile_id,
        "change_summary": {
            "target": change.get("target"),
            "field": change.get("field"),
            "before": change.get("before"),
            "after": change.get("after"),
        },
        "total_chapters_scanned": len(chapters),
        "impact_distribution": counts,
        "affected_count": len(affected),
        "affected_chapters": affected,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = workspace / "radius.json"
    _write_json(out, radius)
    print(f"[OK] compute-radius · scanned={len(chapters)} "
          f"high={counts[_IMPACT_HIGH]} medium={counts[_IMPACT_MEDIUM]} "
          f"low={counts[_IMPACT_LOW]}", file=sys.stderr)
    print(f"     report: {out}", file=sys.stderr)
    return 0


# ============ step 3 patch ============
def _build_validator_repair_brief(chapter_rel: str, matched_lines: list[int],
                                  change: dict) -> dict:
    """造 gen_fixer validator-repair brief（schema version=1）。

    把 reconcile 的 fact 变更声明翻译成 violation 单条·issue=fact_conflict·
    fix_hint 给明确替换方向。
    """
    before = change.get("before", "")
    after = change.get("after", "")
    target = change.get("target", "")
    field = change.get("field", "")
    fix_hint = (
        f"将与「{target} 的 {field} = {before}」相关的描写改为「{after}」。"
        f"保留段落节奏 · 自然衔接前后文 · 不机械替换字面词，需要重写句子让逻辑顺畅。"
    )
    ls = matched_lines[0] if matched_lines else 1
    le = matched_lines[-1] if matched_lines else ls
    return {
        "version": 1,
        "chapter_path": chapter_rel,
        "violations": [
            {
                "line_start": ls,
                "line_end": le,
                "issue": f"fact_conflict · {target}.{field} 变更未同步（{before}→{after}）",
                "fix_hint": fix_hint,
                "original": f"目标关键词「{before}」（章节命中行 "
                            f"{','.join(str(x) for x in matched_lines[:10])}）",
            }
        ],
        "_reconcile_meta": {
            "target": target,
            "field": field,
            "before": before,
            "after": after,
        },
    }


def _patch_one_chapter(project_root: Path, workspace: Path, change: dict,
                      affected: dict, dry_run: bool = False) -> dict:
    """对单章调 gen_fixer validator-repair。返回 {ok, mode, exit, stderr_tail}。"""
    chapter_rel = affected["chapter_path"]
    brief = _build_validator_repair_brief(
        chapter_rel, affected.get("matched_lines", []), change)
    brief_path = workspace / "briefs" / f"{Path(chapter_rel).stem}_brief.json"
    _write_json(brief_path, brief)

    fixer = _HERE / "gen_fixer.py"
    if not fixer.exists():
        return {"ok": False, "exit": -1, "stderr_tail": f"gen_fixer.py 不存在: {fixer}"}

    cmd = [
        sys.executable, str(fixer),
        "--project", str(project_root),
        "--mode", "validator-repair",
        "--brief", str(brief_path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    logger.info(f"[reconcile] patch ← {chapter_rel}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=900)  # 15min/章兜底（gen-model 改写需要时间）
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "exit": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-1500:],
            "stderr_tail": (proc.stderr or "")[-1500:],
            "brief_path": str(brief_path.relative_to(project_root)
                              ).replace("\\", "/"),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit": 124, "stderr_tail": "subprocess timeout 900s"}
    except (OSError, ValueError) as e:
        return {"ok": False, "exit": -1, "stderr_tail": f"{type(e).__name__}: {e}"}


_STRATEGY_LEVELS = {
    "A": {_IMPACT_HIGH, _IMPACT_MEDIUM, _IMPACT_LOW},
    "B": {_IMPACT_HIGH},
    "C": {_IMPACT_HIGH, _IMPACT_MEDIUM},  # 逐章确认在 GUI 层做·脚本视为 high+medium
    "D": set(),  # 只更新档案不动正文
}


def patch_chapters(project_root: Path, workspace: Path, args) -> int:
    """读 radius.json · 按 strategy 对受影响章节调 gen_fixer · 写 patch_log.json。"""
    radius = _read_json(workspace / "radius.json")
    change = _read_json(workspace / "change.json")
    if not radius or not change:
        print(f"[FATAL] patch: radius.json/change.json 缺失·先跑前序 step",
              file=sys.stderr)
        return 2

    strategy = (args.strategy or "A").upper()
    levels = _STRATEGY_LEVELS.get(strategy, _STRATEGY_LEVELS["A"])
    max_chapters = int(args.max_chapters or 0) or None

    affected = [a for a in radius.get("affected_chapters", [])
                if a.get("impact_level") in levels]
    if max_chapters is not None:
        affected = affected[:max_chapters]

    results: list[dict] = []
    ok_count = 0
    fail_count = 0
    started = time.time()
    for a in affected:
        r = _patch_one_chapter(project_root, workspace, change, a,
                               dry_run=args.dry_run)
        r["chapter_path"] = a["chapter_path"]
        r["impact_level"] = a["impact_level"]
        results.append(r)
        if r["ok"]:
            ok_count += 1
        else:
            fail_count += 1

    log = {
        "schema_version": SCHEMA_VERSION,
        "reconcile_id": args.reconcile_id,
        "strategy": strategy,
        "dry_run": args.dry_run,
        "total_attempted": len(affected),
        "ok_count": ok_count,
        "fail_count": fail_count,
        "elapsed_sec": round(time.time() - started, 2),
        "results": results,
        "patched_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = workspace / "patch_log.json"
    _write_json(out, log)
    print(f"[OK] patch · strategy={strategy} attempted={len(affected)} "
          f"ok={ok_count} fail={fail_count}", file=sys.stderr)
    print(f"     log: {out}", file=sys.stderr)
    # 全失败 → exit 1（advisory · plan 仍 step_complete · feedback_real_api_tests_no_economize
    # patch 真烧钱 · 整体失败要让用户看到非 0）
    if affected and ok_count == 0 and not args.dry_run:
        return 1
    return 0


# ============ step 4 audit ============
def audit_consistency(project_root: Path, workspace: Path, args) -> int:
    """跨章一致性验证·对 patch_log ok 的章节做 locked_fact 复查。

    路径 1（默认）：调 locked_fact_cross_scene_scanner.scan() 对受影响 cluster
                  做跨场景检查（cluster 视野 · 与北极星④对齐）。
    路径 2（fallback）：对 patch_log 里 ok 的章节做轻量 grep——after 关键词存在性
                       + before 关键词残留计数（patch 失败时残留 = consistency 风险）。
    本脚本不阻断 · 全 advisory · 把 verdict 写进 audit.json。
    """
    radius = _read_json(workspace / "radius.json")
    patch_log = _read_json(workspace / "patch_log.json")
    change = _read_json(workspace / "change.json")
    if not radius or not change:
        print(f"[FATAL] audit: radius.json/change.json 缺失", file=sys.stderr)
        return 2

    before = change.get("before", "")
    after = change.get("after", "")
    target = change.get("target", "")

    per_chapter: list[dict] = []
    residual_before_count = 0
    new_after_count = 0
    for a in radius.get("affected_chapters", []):
        rel = a.get("chapter_path")
        full = project_root / rel
        if not full.exists():
            per_chapter.append({"chapter_path": rel, "_skip": "not_found"})
            continue
        try:
            body = full.read_text(encoding="utf-8")
        except OSError:
            per_chapter.append({"chapter_path": rel, "_skip": "read_error"})
            continue
        residual = body.count(before) if before else 0
        new_hits = body.count(after) if after else 0
        residual_before_count += residual
        new_after_count += new_hits
        per_chapter.append({
            "chapter_path": rel,
            "impact_level": a.get("impact_level"),
            "residual_before_count": residual,
            "new_after_count": new_hits,
            "target_still_present": bool(target and target in body),
        })

    # 调 locked_fact_cross_scene_scanner（cluster 视野·北极星④）—— 失败降级
    cross_scene_result: dict | None = None
    try:
        import locked_fact_cross_scene_scanner as lfx  # noqa: E402
        # 找第一个有 cluster_draft 的 cluster 跑 scanner（轻量）
        draft_dirs = sorted((project_root / "章节").glob("cluster_*_draft"))
        for dd in draft_dirs:
            cluster_key = dd.name.replace("cluster_", "").replace("_draft", "")
            draft_path = dd / f"cluster_{cluster_key}_draft.txt"
            if draft_path.exists():
                cross_scene_result = lfx.scan(project_root, draft_path)
                cross_scene_result["_cluster_key"] = cluster_key
                break
    except Exception as e:  # noqa: BLE001
        cross_scene_result = {"_skip": f"scanner_load_failed: {type(e).__name__}: {e}"}

    verdict = "pass"
    if patch_log:
        if patch_log.get("fail_count", 0) > 0:
            verdict = "partial_fail"
        if patch_log.get("dry_run"):
            verdict = "dry_run"
    # 残留 before 比预期多 → 标 needs_review
    if patch_log and not patch_log.get("dry_run"):
        if residual_before_count > 0 and patch_log.get("ok_count", 0) > 0:
            verdict = "needs_review"

    audit = {
        "schema_version": SCHEMA_VERSION,
        "reconcile_id": args.reconcile_id,
        "verdict": verdict,
        "change_summary": {
            "target": target, "before": before, "after": after,
        },
        "per_chapter": per_chapter,
        "residual_before_count": residual_before_count,
        "new_after_count": new_after_count,
        "cross_scene_scanner": cross_scene_result,
        "audited_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = workspace / "audit.json"
    _write_json(out, audit)
    print(f"[OK] audit · verdict={verdict} residual_before={residual_before_count} "
          f"new_after={new_after_count}", file=sys.stderr)
    print(f"     report: {out}", file=sys.stderr)
    return 0


# ============ propagation-check（🔴 2026-06-27 W5）============
def propagation_check(project_root: Path, workspace: Path, args) -> int:
    """🔴 2026-06-27 W5：传播债务校验（advisory · 只标债务 · 绝不改正文）。

    根因（completeness critic 揪出的零约束裸奔环节）：/reconcile 改设定（locked_fact/
    人物卡/世界观）后，没有任何机器校验旧值是否真正传播到受影响章节——patch 可能失败/
    跳过/用户选 strategy D 只更新档案，旧值（before）仍残留在历史章节正文/changes
    （propagation debt），下次写作 build_manifest 仍读到旧描述 → 穿帮无人察觉。

    本 mode 确定性扫受影响章节范围（radius.json），统计每章正文 + changes.json 里 before
    旧值的残留次数：
      · 残留 > 0 → 列入『未传播章节』(un_propagated)
      · 残留 = 0 → 已传播(propagated)
    产 propagation_debt.json 顾问清单给主代理 / GUI 看。

    北极星⑤（不干涉模型创作判断）：调和是 advisory，传播决策权在模型/用户——本 mode
    **绝不自动改正文**，只标债务。永远返回 0（不阻断·配合 plan `?` 容忍前缀双保险）。
    """
    change = _read_json(workspace / "change.json")
    radius = _read_json(workspace / "radius.json")
    if not change or not radius:
        print("[FATAL] propagation-check: change.json/radius.json 缺失·先跑前序 step",
              file=sys.stderr)
        return 2

    before = (change.get("before") or "").strip()
    after = change.get("after", "")
    target = change.get("target", "")

    patch_log = _read_json(workspace / "patch_log.json") or {}
    strategy = patch_log.get("strategy", "?")
    dry_run = bool(patch_log.get("dry_run"))

    # 纯新增设定（无 before 旧值）→ 无残留可判定 · advisory skip（绝不误标）
    if not before:
        debt = {
            "schema_version": SCHEMA_VERSION,
            "reconcile_id": args.reconcile_id,
            "gate_level": "advisory",
            "skipped": "no_before_value",
            "note": "变更无 before 旧值（纯新增设定）·无残留可查·传播校验跳过",
            "change_summary": {"target": target, "before": before, "after": after},
            "un_propagated_count": 0,
            "un_propagated_chapters": [],
            "propagated_count": 0,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_json(workspace / "propagation_debt.json", debt)
        print("[OK] propagation-check · skipped(no before value)·无残留可查（advisory）",
              file=sys.stderr)
        return 0

    un_propagated: list[dict] = []
    propagated: list[dict] = []
    for a in radius.get("affected_chapters", []):
        rel = a.get("chapter_path")
        full = project_root / rel
        body_residual = 0
        changes_residual = 0
        if full.exists():
            try:
                body_residual = full.read_text(encoding="utf-8").count(before)
            except OSError:
                pass
        changes_path = full.parent / f"{full.stem}_changes.json"
        if changes_path.exists():
            try:
                changes_residual = changes_path.read_text(
                    encoding="utf-8-sig").count(before)
            except OSError:
                pass
        rec = {
            "chapter_path": rel,
            "chapter_name": a.get("chapter_name"),
            "impact_level": a.get("impact_level"),
            "body_residual": body_residual,
            "changes_residual": changes_residual,
        }
        if (body_residual + changes_residual) > 0:
            un_propagated.append(rec)
        else:
            propagated.append(rec)

    debt = {
        "schema_version": SCHEMA_VERSION,
        "reconcile_id": args.reconcile_id,
        "gate_level": "advisory",
        "change_summary": {"target": target, "before": before, "after": after},
        "strategy": strategy,
        "dry_run": dry_run,
        "affected_count": len(radius.get("affected_chapters", [])),
        "un_propagated_count": len(un_propagated),
        "un_propagated_chapters": un_propagated,
        "propagated_count": len(propagated),
        "advisory": (
            f"{len(un_propagated)} 章旧值『{before[:20]}』仍残留（propagation debt·未传播·"
            f"主代理/用户决定是否补 patch 或归档为已知债务）"
            if un_propagated else "全部受影响章节旧值已清·传播完成"),
        "_note": ("advisory·只标债务不改正文（北极星⑤调和决策权在模型/用户）·"
                  "strategy=D 只更新档案时残留属用户主动选择的已知债务·非穿帮"),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = workspace / "propagation_debt.json"
    _write_json(out, debt)
    print(f"[OK] propagation-check · un_propagated={len(un_propagated)} "
          f"propagated={len(propagated)}（advisory·只标债务不改正文）", file=sys.stderr)
    if un_propagated:
        head = ", ".join(str(c["chapter_name"]) for c in un_propagated[:8])
        print(f"     未传播章节（旧值残留）: {head}", file=sys.stderr)
    print(f"     report: {out}", file=sys.stderr)
    return 0


# ============ step 5 report ============
def generate_report(project_root: Path, workspace: Path, args) -> int:
    """汇总 step1-4 + 传播校验 · 写 reconcile_report.json。"""
    change = _read_json(workspace / "change.json")
    radius = _read_json(workspace / "radius.json")
    patch_log = _read_json(workspace / "patch_log.json")
    audit = _read_json(workspace / "audit.json")
    propagation = _read_json(workspace / "propagation_debt.json")  # 🔴 W5（tolerant·可缺）

    summary = {
        "schema_version": SCHEMA_VERSION,
        "command": "reconcile",
        "reconcile_id": args.reconcile_id,
        "project_root": str(project_root),
        "workspace": str(workspace.relative_to(project_root)).replace("\\", "/"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "modules": {
            "step1_change": change or {"_missing": True},
            "step2_radius_summary": (
                {"total_chapters_scanned": radius.get("total_chapters_scanned"),
                 "affected_count": radius.get("affected_count"),
                 "impact_distribution": radius.get("impact_distribution")}
                if radius else {"_missing": True}
            ),
            "step3_patch_summary": (
                {"strategy": patch_log.get("strategy"),
                 "dry_run": patch_log.get("dry_run"),
                 "total_attempted": patch_log.get("total_attempted"),
                 "ok_count": patch_log.get("ok_count"),
                 "fail_count": patch_log.get("fail_count"),
                 "elapsed_sec": patch_log.get("elapsed_sec")}
                if patch_log else {"_missing": True}
            ),
            "step4_audit_summary": (
                {"verdict": audit.get("verdict"),
                 "residual_before_count": audit.get("residual_before_count"),
                 "new_after_count": audit.get("new_after_count"),
                 "cross_scene_verdict": (
                     audit.get("cross_scene_scanner", {}).get("gate_level")
                     if isinstance(audit.get("cross_scene_scanner"), dict) else None)}
                if audit else {"_missing": True}
            ),
            # 🔴 W5：传播债务校验摘要（advisory·绝不翻转 verdict·只让主代理看见未传播章节）
            "propagation_summary": (
                {"gate_level": "advisory",
                 "un_propagated_count": propagation.get("un_propagated_count"),
                 "propagated_count": propagation.get("propagated_count"),
                 "advisory": propagation.get("advisory"),
                 "skipped": propagation.get("skipped")}
                if propagation else {"_missing": True}
            ),
        },
        # 🔴 W5：未传播章节名单（顶层 advisory·主代理据此决定补 patch / 归档已知债务）
        "propagation_debt_chapters": (
            [c.get("chapter_name") for c in propagation.get("un_propagated_chapters", [])]
            if propagation else []
        ),
        "_program_driven_note": (
            "/reconcile v28 program-driven (2026-06-22 G2 P0b) · 5 step "
            "全 advisory shadow · 作者档第一权威 · hard_gate 12 码不变 · "
            "patch 走 gen_fixer validator-repair（gen-model）"),
    }

    # verdict 综合
    if patch_log and patch_log.get("fail_count", 0) > 0:
        summary["verdict"] = "partial_fail"
    elif audit and audit.get("verdict") in ("needs_review",):
        summary["verdict"] = "needs_review"
    elif audit and audit.get("verdict") == "dry_run":
        summary["verdict"] = "dry_run"
    elif patch_log and patch_log.get("ok_count", 0) > 0:
        summary["verdict"] = "pass"
    else:
        summary["verdict"] = "no_op"

    out = workspace / REPORT_NAME
    _write_json(out, summary)
    print(f"[OK] report · verdict={summary['verdict']}", file=sys.stderr)
    print(f"     report: {out}", file=sys.stderr)
    return 0


# ============ CLI ============
def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="/reconcile 程序驱动脚本（5 mode）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("project", help="项目路径（workspace/novels/<书名>）")
    ap.add_argument("--mode", required=True,
                    choices=["detect-changes", "compute-radius", "patch",
                             "audit", "propagation-check", "report"],
                    help="选择本次跑的 reconcile mode")
    ap.add_argument("--reconcile-id", default="current",
                    help="reconcile workspace 子目录名·默认 'current'·覆盖式写")
    # detect-changes 的 CLI 参数（备用通道·spec.json 优先）
    ap.add_argument("--change-type", default="",
                    help="变更类型 · character_field/world_rule/location/...")
    ap.add_argument("--target", default="", help="变更目标实体（如角色名）")
    ap.add_argument("--field", default="", help="字段名（如 occupation/appearance）")
    ap.add_argument("--before", default="", help="变更前值")
    ap.add_argument("--after", default="", help="变更后值")
    # patch 的 strategy
    ap.add_argument("--strategy", default="A", choices=list(_STRATEGY_LEVELS),
                    help="A=全部自动 / B=只 high / C=high+medium / D=只档案不动正文")
    ap.add_argument("--max-chapters", default=0,
                    help="单次 patch 最多处理多少章·0=不限")
    ap.add_argument("--dry-run", action="store_true",
                    help="patch mode 不真发 gen_fixer·只构造 brief")
    return ap


def main() -> int:
    ap = build_argparser()
    args = ap.parse_args()
    project_root = _resolve_project(args.project)
    workspace = _workspace_dir(project_root, args.reconcile_id)

    mode = args.mode
    print(f"[reconcile] mode={mode} project={project_root} workspace={workspace}",
          file=sys.stderr)
    if mode == "detect-changes":
        return detect_changes(project_root, workspace, args)
    if mode == "compute-radius":
        return compute_radius(project_root, workspace, args)
    if mode == "patch":
        return patch_chapters(project_root, workspace, args)
    if mode == "audit":
        return audit_consistency(project_root, workspace, args)
    if mode == "propagation-check":  # 🔴 W5
        return propagation_check(project_root, workspace, args)
    if mode == "report":
        return generate_report(project_root, workspace, args)
    return 2


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    sys.exit(main())
