#!/usr/bin/env python3
"""self_heal_engine.py — MAPE-K **Analyze + Knowledge + Reflect** 层（2026-05-30 自学习能力）

读 runtime/incidents.jsonl（posttooluse_runtime_monitor 产出）→ 错误指纹复发计数
→ 自愈知识库 self_heal_kb.json（指纹 → 根因猜测 → 推荐动作 → Reflexion lesson）。
供 adaptive_runner（Plan/Execute）+ 主代理 + step_completion_monitor 查询。

命令：
  --ingest              增量读 incidents.jsonl，复发计数，升级知识库（复用 learning_loop ≥3/≥5 复发范式）
  --suggest <sig|type>  查知识库给某错误的推荐动作（adaptive_runner / 主代理用 · 输出 JSON）
  --dashboard           运行时健康全景（总量 / top 复发 / known / regression / 未解决）
  --resolve <sig>       标记某错误已修复（人工反馈，停止再报；再次出现 → 标 regression 重新激活）
  --emit-lessons        把 known 级 pattern 写进 lessons/runtime_lessons.md（Reflexion 经验沉淀）

北极星边界：本引擎只**学运行时报错 + 给规避建议**（advisory），**绝不自改任何脚本逻辑**
（自改代码风险见 Gödel Agent 已知短板）。推荐动作供人 / adaptive_runner 参考，不触碰 hard_gate
一致性 / 契约 / 穿帮逻辑。所有写盘走原子写 + Tolerant Reader 容错读。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]   # 系统根：runtime/ 是系统级（跨小说项目，脚本报错与项目无关）
RECURRING_THRESHOLD = 3   # 与 learning_loop 升级阈值一致
KNOWN_THRESHOLD = 5
MAX_RAW_SAMPLES = 3

# 错误类型 → (根因猜测, 推荐动作, severity)。severity 给 adaptive_runner 选 Plan：
#   adapt=脚本自适应可解 / retry=重试 / degrade=降级 / missing_step=缺步补全 / escalate=升人
ACTION_MAP = {
    "KeyError":            ("上游 JSON 缺字段 / schema 变更", "Tolerant Reader：改 .get(k, default) 容错读；核对上游子系统 JSON schema 版本", "adapt"),
    "IndexError":          ("列表越界 / 空数据", "访问前判空 + 兜底默认；核对数据是否被截断", "adapt"),
    "AttributeError":      ("None 值 / 对象类型不符", "isinstance 守卫 + None 兜底", "adapt"),
    "TypeError":           ("类型不符（str 当 dict / None 参与运算）", "isinstance 守卫 + 类型归一；核对上游产出格式", "adapt"),
    "FileNotFoundError":   ("前置 step 产出缺失 / 路径错", "缺步嫌疑：触发 step_completion_monitor 补跑前置；核对路径", "missing_step"),
    "JSONDecodeError":     ("JSON 文件损坏 / 写入中断", "fail-loud 跳过不覆盖（呼应 save_state 防清空）；从 Git 快照恢复", "degrade"),
    "ValueError":          ("数值解析失败 / 非法入参", "入参校验 + 兜底；核对数据格式", "adapt"),
    "ZeroDivisionError":   ("分母为 0", "除前判 0 兜底", "adapt"),
    "TimeoutError":        ("gen-model / 子进程超时", "transient：重试（指数退避）+ 熔断器；超时降级跳过 advisory", "retry"),
    "ConnectionError":     ("外部 API 网络抖动", "transient：重试 + 熔断器降级", "retry"),
    "NameError":           ("未定义变量 / 缺 import（代码 bug）", "⚠️ 脚本 bug：人工修复（缺 import / 拼写）", "escalate"),
    "ImportError":         ("缺依赖 / 模块路径错（代码 bug）", "⚠️ 脚本 bug：检查 import 与依赖", "escalate"),
    "ModuleNotFoundError": ("缺依赖 / 模块路径错", "⚠️ 脚本 bug：检查 import 与依赖安装", "escalate"),
    "UnicodeDecodeError":  ("编码不符（中文 / 弯引号）", "指定 encoding='utf-8'；弯引号 codepoint 校验（呼应 dialogue_quote）", "adapt"),
    "FATAL":               ("脚本自报致命", "查脚本 [FATAL] 上下文定位根因", "escalate"),
    "CRASH":               ("scanner / 编排器自报崩溃", "查崩溃脚本；advisory scanner 崩溃可降级跳过", "degrade"),
}
DEFAULT_ACTION = ("未知根因", "人工诊断 raw 上下文", "escalate")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _runtime_dir(project_root: Path) -> Path:
    d = project_root / "core" / "claude-home" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _kb_path(project_root: Path) -> Path:
    return _runtime_dir(project_root) / "self_heal_kb.json"


def _incidents_path(project_root: Path) -> Path:
    return _runtime_dir(project_root) / "incidents.jsonl"


def load_kb(project_root: Path) -> dict:
    """Tolerant Reader：损坏 / 缺失都返回空骨架，绝不崩。"""
    p = _kb_path(project_root)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("patterns", {})
                data.setdefault("_ingest_offset", 0)
                data.setdefault("_total_incidents", 0)
                return data
        except Exception as e:
            print(f"⚠️ self_heal_kb.json 损坏（{e}），重建空库（旧库不覆盖删除，仅旁置 .corrupt）", file=sys.stderr)
            try:
                p.rename(p.with_suffix(".json.corrupt"))
            except Exception:
                pass
    return {"schema_version": "v1.self_heal", "updated_at": _now(),
            "patterns": {}, "_ingest_offset": 0, "_total_incidents": 0}


def save_kb(project_root: Path, kb: dict) -> Path:
    kb["updated_at"] = _now()
    p = _kb_path(project_root)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return p


def _classify(error_type: str):
    return ACTION_MAP.get(error_type, DEFAULT_ACTION)


def _make_lesson(pat: dict) -> str:
    """Reflexion 风格自然语言经验（known 级）。"""
    return (f"脚本 {pat.get('script') or '?'} 在 {pat.get('location') or '?'} 反复抛 "
            f"{pat.get('error_type')}（已 {pat['count']} 次）。根因可能：{pat.get('root_cause_hint')}。"
            f"下次规避：{pat.get('recommended_action')}")


def cmd_ingest(project_root: Path) -> int:
    kb = load_kb(project_root)
    inc_path = _incidents_path(project_root)
    if not inc_path.exists():
        print("[ingest] 无 incidents.jsonl（运行时尚无报错记录），跳过。")
        return 0

    offset = kb.get("_ingest_offset", 0)
    size = inc_path.stat().st_size
    if size < offset:   # 文件被轮转 / 截断 → 从头读
        print(f"[ingest] incidents.jsonl 被截断（size {size} < offset {offset}），重置 offset=0", file=sys.stderr)
        offset = 0

    new_count = upgraded = regressions = 0
    with open(inc_path, "r", encoding="utf-8") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                inc = json.loads(line)
            except Exception:
                continue
            sig = inc.get("signature") or f"{inc.get('script','')}::{inc.get('error_type','')}"
            if not sig.strip(":"):
                continue
            new_count += 1
            kb["_total_incidents"] = kb.get("_total_incidents", 0) + 1
            pat = kb["patterns"].get(sig)
            if pat is None:
                pat = {"signature": sig, "script": inc.get("script", ""),
                       "error_type": inc.get("error_type", ""), "location": inc.get("location", ""),
                       "message_sample": inc.get("message", ""), "count": 0,
                       "first_seen": inc.get("ts", _now()), "last_seen": inc.get("ts", _now()),
                       "status": "observed", "raw_samples": []}
                kb["patterns"][sig] = pat
            # regression：已标 resolved 的错误又出现 = 修了又犯（重要信号）
            if pat.get("status") == "resolved":
                pat["status"] = "regression"
                pat["regression_at"] = inc.get("ts", _now())
                regressions += 1
            pat["count"] += 1
            pat["last_seen"] = inc.get("ts", pat.get("last_seen"))
            if inc.get("message"):
                pat["message_sample"] = inc["message"]
            raw = inc.get("raw")
            if raw and raw not in pat["raw_samples"]:
                pat["raw_samples"] = (pat["raw_samples"] + [raw])[-MAX_RAW_SAMPLES:]
    kb["_ingest_offset"] = inc_path.stat().st_size   # 增量游标推进到文件末尾

    # 升级 status + 填根因 / 推荐动作 / lesson
    for sig, pat in kb["patterns"].items():
        if pat.get("status") in ("resolved",):
            continue
        prev = pat.get("status")
        c = pat["count"]
        rc, act, sev = _classify(pat.get("error_type", ""))
        pat["root_cause_hint"], pat["recommended_action"], pat["severity"] = rc, act, sev
        if pat.get("status") == "regression":
            pat["confidence"] = 0.95
        elif c >= KNOWN_THRESHOLD:
            pat["status"] = "known"
            pat["confidence"] = 0.9
            pat["lesson"] = _make_lesson(pat)
        elif c >= RECURRING_THRESHOLD:
            pat["status"] = "recurring"
            pat["confidence"] = round(0.5 + 0.1 * (c - RECURRING_THRESHOLD), 2)
        else:
            pat["status"] = "observed"
            pat["confidence"] = round(0.3 * c, 2)
        if prev != pat["status"] and pat["status"] in ("recurring", "known"):
            upgraded += 1

    save_kb(project_root, kb)
    active = sum(1 for p in kb["patterns"].values() if p.get("status") in ("recurring", "known", "regression"))
    print(f"[ingest] 新增 incident {new_count} 条 · 升级 {upgraded} · regression {regressions} · "
          f"活跃 pattern {active} / 总 {len(kb['patterns'])} · 累计 incident {kb['_total_incidents']}")
    if regressions:
        print(f"🔴 [regression] {regressions} 个已标修复的错误又复发 —— 修复未生效，需复查！", file=sys.stderr)
    return 0


def cmd_suggest(project_root: Path, query: str) -> int:
    kb = load_kb(project_root)
    pats = kb.get("patterns", {})
    hit = pats.get(query)
    if hit is None:   # 按 error_type 模糊匹配，取 count 最高
        cands = [p for p in pats.values() if p.get("error_type") == query or query in p.get("signature", "")]
        hit = max(cands, key=lambda p: p.get("count", 0)) if cands else None
    if hit is None:
        rc, act, sev = _classify(query)   # 未见过 → 按类型给通用建议
        print(json.dumps({"known": False, "query": query, "root_cause_hint": rc,
                          "recommended_action": act, "severity": sev}, ensure_ascii=False))
        return 0
    print(json.dumps({"known": True, "signature": hit.get("signature"), "status": hit.get("status"),
                      "count": hit.get("count"), "root_cause_hint": hit.get("root_cause_hint"),
                      "recommended_action": hit.get("recommended_action"),
                      "severity": hit.get("severity"), "confidence": hit.get("confidence"),
                      "lesson": hit.get("lesson", "")}, ensure_ascii=False))
    return 0


def cmd_resolve(project_root: Path, sig: str) -> int:
    kb = load_kb(project_root)
    pat = kb.get("patterns", {}).get(sig)
    if not pat:
        print(f"[resolve] 未找到指纹 {sig}", file=sys.stderr)
        return 1
    pat["status"] = "resolved"
    pat["resolved_at"] = _now()
    save_kb(project_root, kb)
    print(f"[resolve] {sig} 标记为已修复（再次出现将标 regression）")
    return 0


def cmd_dashboard(project_root: Path) -> int:
    kb = load_kb(project_root)
    pats = kb.get("patterns", {})
    by_status = {}
    for p in pats.values():
        by_status.setdefault(p.get("status", "?"), []).append(p)
    print("=" * 60)
    print(f"运行时自愈知识库 dashboard · 累计 incident {kb.get('_total_incidents', 0)} · pattern {len(pats)}")
    print(f"  更新于 {kb.get('updated_at')}")
    print("-" * 60)
    for st in ("regression", "known", "recurring", "observed", "resolved"):
        lst = by_status.get(st, [])
        if not lst:
            continue
        mark = "🔴" if st == "regression" else ("⚠️" if st in ("known", "recurring") else "·")
        print(f"{mark} {st}: {len(lst)}")
        for p in sorted(lst, key=lambda x: -x.get("count", 0))[:5]:
            print(f"    [{p.get('count')}x] {p.get('signature')} → {p.get('recommended_action', '')[:50]}")
    print("=" * 60)
    return 0


def cmd_emit_lessons(project_root: Path) -> int:
    kb = load_kb(project_root)
    known = [p for p in kb.get("patterns", {}).values()
             if p.get("status") in ("known", "regression") and p.get("lesson")]
    if not known:
        print("[emit-lessons] 暂无 known 级 pattern，跳过。")
        return 0
    lessons_dir = project_root / "core" / "claude-home" / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    out = lessons_dir / "runtime_lessons.md"
    lines = ["# 运行时报错自学习 lessons（self_heal_engine 自动沉淀）",
             "", f"> 自动生成于 {_now()} · 数据源 runtime/self_heal_kb.json · 仅 known/regression 级",
             "> 这是 advisory 经验（怎么避免重复运行时报错），不是 hard_gate。", ""]
    for p in sorted(known, key=lambda x: -x.get("count", 0)):
        lines += [f"## {p.get('signature')}（{p.get('count')}x · {p.get('status')}）",
                  f"- **现象**：{p.get('error_type')} @ {p.get('location') or '?'}，样例 `{p.get('message_sample', '')[:80]}`",
                  f"- **为什么**：{p.get('root_cause_hint')}",
                  f"- **怎么用**：{p.get('recommended_action')}", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[emit-lessons] 写入 {len(known)} 条 known lesson → {out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="MAPE-K Analyze+Knowledge+Reflect 层")
    ap.add_argument("--project-root", default=None, help="runtime 根（默认系统根 REPO_ROOT · 仅测试 override）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ingest", action="store_true")
    g.add_argument("--suggest", metavar="SIG|TYPE")
    g.add_argument("--resolve", metavar="SIG")
    g.add_argument("--dashboard", action="store_true")
    g.add_argument("--emit-lessons", action="store_true")
    args = ap.parse_args()
    root = Path(args.project_root).resolve() if args.project_root else REPO_ROOT

    if args.ingest:
        return cmd_ingest(root)
    if args.suggest:
        return cmd_suggest(root, args.suggest)
    if args.resolve:
        return cmd_resolve(root, args.resolve)
    if args.dashboard:
        return cmd_dashboard(root)
    if args.emit_lessons:
        return cmd_emit_lessons(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
