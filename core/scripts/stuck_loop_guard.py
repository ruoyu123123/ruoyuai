"""stuck_loop_guard.py — 卡死循环守卫 + 逃生通道（v23 Layer 0）

业界 2026 共识（Antigravity loop break / Wink arxiv 2602.17037 / BerriAI
self-improving-agent）：「**检测了不拦截 = 等于没检测**」。

我们已有 retry 计数散在 3 个脚本（user_experience / evolution_canary /
agent_drift_monitor）但**全是事后统计**，没有运行时强制中断 ——
AI 卡在同一个错误里反复"自洽地说改好了"，没人按下停止键。本守卫就是那只手。

【4 类信号 + 强制中断】

1. RETRY_THRASHING        — 同章节 audit retry ≥ 3 次
2. UNCHANGED_FINDING      — 相邻两次 audit 同 finding code 重叠率 ≥ 0.8（"修了等于没修"）
3. WRITER_OUTPUT_LOOP     — 同章节相邻两版正文 Jaccard 相似度 > 0.85
4. SCRIPT_REPEAT_FAIL     — 同一脚本同一错误连续 2 次（通过 --record-error 累积）

命中任一 → 写 escalation report 到 _数据库/.escalations/<ts>.json + exit 2 +
打印「STOP — Human intervention required」到 stderr。

【关键设计 · 为什么纯规则不调 LLM】
self-judge / LLM-as-critic 会被同源 prompt 污染（Self-Correction Bench 64.5%
盲点率）。纯规则层是 sibling 监督的最简形式 —— 它不可能被它要监督的对象用
"自然语言巧言令色"绕过。

【与现有系统的边界】
- 不动 plan_tracker / WAL 任何状态（只读 audit 报告 + 章节正文）
- 不修复任何问题（escalate-only，把"修"的决策权交还给人）
- 自身防御性 exit 0：load JSON 出错 / 路径不存在 → 跳过该 detector，绝不让本守卫
  本身成为新的故障点

【CLI】
  python stuck_loop_guard.py <project>                  # 扫所有活跃章
  python stuck_loop_guard.py <project> --ch N           # 指定章
  python stuck_loop_guard.py <project> --status         # 只读现状，不写状态
  python stuck_loop_guard.py <project> --reset          # 清空 stuck_state（手动 unstick 后）
  python stuck_loop_guard.py <project> --record-error <script> <error_code>
                                                        # 外部脚本失败时上报，累积到信号 4

【输入】
- <project>/_数据库/.audit/ch_NNN_audit*.json    （audit_hub 报告）
- <project>/_数据库/.judge_reports/ch_NNN_*.json （judge consensus 报告）
- <project>/章节/第NNN章/*.txt                    （writer 输出版本）

【输出】
- <project>/_数据库/.stuck_state.json            （持久化跨调用累积状态）
- <project>/_数据库/.escalations/escalation_<ts>.json （命中信号 → 人工待办）

【退出码】
  0 = 健康（无信号命中）
  1 = advisory（接近阈值，提醒但不拦截）
  2 = 卡死 = STOP（必须人工介入才能继续）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


# ============================================================
# 阈值（业界共识 + 我们的网文工坊场景调校）
# ============================================================
RETRY_THRASHING_THRESHOLD = 3        # 同章 audit ≥ 3 次 = 卡
RETRY_THRASHING_ADVISORY = 2         # 2 次 = 提醒
UNCHANGED_OVERLAP_RATIO = 0.8        # 相邻两次 finding code 重叠 ≥ 80% = 假修
WRITER_LOOP_JACCARD = 0.85           # 相邻两版正文 Jaccard > 0.85 = loop
SCRIPT_REPEAT_FAIL_THRESHOLD = 2     # 同脚本同错 ≥ 2 次 = 卡

NGRAM_SIZE = 4                       # writer 输出对比用 4-gram（汉字粒度，平衡精度/速度）

# ============================================================
# 工具函数
# ============================================================

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def chinese_ngrams(text: str, n: int = NGRAM_SIZE) -> set:
    """提取汉字 n-gram。剥掉空白和标点，避免格式差异污染相似度。"""
    if not text:
        return set()
    cleaned = re.sub(r"[\s　\W_]+", "", text, flags=re.UNICODE)
    if len(cleaned) < n:
        return set()
    return {cleaned[i:i + n] for i in range(len(cleaned) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def find_chapters(project_root: Path) -> list[int]:
    """扫项目下所有 第NNN章 目录。"""
    chs = []
    ch_dir = project_root / "章节"
    if not ch_dir.exists():
        return chs
    for d in ch_dir.glob("第*章"):
        m = re.match(r"第(\d+)章", d.name)
        if m:
            chs.append(int(m.group(1)))
    return sorted(chs)


def latest_active_chapter(project_root: Path) -> int | None:
    """找最近被改过的章节（mtime 最近）。"""
    ch_dir = project_root / "章节"
    if not ch_dir.exists():
        return None
    dirs = [d for d in ch_dir.glob("第*章") if d.is_dir()]
    if not dirs:
        return None
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    m = re.match(r"第(\d+)章", dirs[0].name)
    return int(m.group(1)) if m else None


# ============================================================
# 4 类信号检测
# ============================================================

def detect_retry_thrashing(project_root: Path, ch: int) -> dict | None:
    """信号 1: 同章节 audit retry ≥ N 次。

    数据源：_数据库/.audit/ch_NNN_audit*.json + .judge_reports/ch_NNN_*.json
    """
    audit_dir = project_root / "_数据库" / ".audit"
    judge_dir = project_root / "_数据库" / ".judge_reports"

    audit_files = list(audit_dir.glob(f"ch_{ch:03d}_audit*.json")) if audit_dir.exists() else []
    judge_files = list(judge_dir.glob(f"ch_{ch:03d}_*.json")) if judge_dir.exists() else []
    n = len(audit_files) + len(judge_files)

    if n >= RETRY_THRASHING_THRESHOLD:
        return {
            "signal": "RETRY_THRASHING",
            "severity": "stuck",
            "ch": ch,
            "retry_count": n,
            "threshold": RETRY_THRASHING_THRESHOLD,
            "evidence_files": [str(f.relative_to(project_root)) for f in (audit_files + judge_files)[:5]],
            "explanation": (
                f"第{ch}章已审 {n} 次（阈值 {RETRY_THRASHING_THRESHOLD}）。AI 反复"
                f"自洽地说『改好了』，但每轮都开新一次审 = 卡在同一个洞里出不来。"
            ),
            "human_action": "Read 最新 audit 报告，**人工**判断是放过还是真修；不要再让 AI 自审",
        }
    elif n >= RETRY_THRASHING_ADVISORY:
        return {
            "signal": "RETRY_THRASHING",
            "severity": "advisory",
            "ch": ch,
            "retry_count": n,
            "threshold": RETRY_THRASHING_THRESHOLD,
            "explanation": f"第{ch}章已审 {n} 次，接近阈值 {RETRY_THRASHING_THRESHOLD}",
        }
    return None


def detect_unchanged_finding(project_root: Path, ch: int) -> dict | None:
    """信号 2: 相邻两次 audit 的 issue code 集合重叠率 ≥ 0.8 = 假修。

    数据源：_数据库/.audit/ch_NNN_audit*.json
    """
    audit_dir = project_root / "_数据库" / ".audit"
    if not audit_dir.exists():
        return None
    files = sorted(audit_dir.glob(f"ch_{ch:03d}_audit*.json"),
                   key=lambda p: p.stat().st_mtime)
    if len(files) < 2:
        return None

    latest_two = files[-2:]
    code_sets = []
    for f in latest_two:
        d = load_json(f, {})
        codes = set()
        for issue in (d.get("issues") or []):
            c = issue.get("code")
            if c and not issue.get("waived"):
                codes.add(c)
        code_sets.append(codes)

    a, b = code_sets
    if not a or not b:
        return None
    overlap = len(a & b) / len(a | b) if (a | b) else 0
    if overlap >= UNCHANGED_OVERLAP_RATIO:
        return {
            "signal": "UNCHANGED_FINDING",
            "severity": "stuck",
            "ch": ch,
            "overlap_ratio": round(overlap, 2),
            "threshold": UNCHANGED_OVERLAP_RATIO,
            "previous_codes": sorted(a),
            "latest_codes": sorted(b),
            "evidence_files": [str(f.relative_to(project_root)) for f in latest_two],
            "explanation": (
                f"第{ch}章相邻两次审同样 {len(a & b)} 个 issue code，重叠 {overlap:.0%} ≥ "
                f"{UNCHANGED_OVERLAP_RATIO:.0%}。AI『修』了一轮但问题原样残留 = 自欺。"
            ),
            "human_action": "人工 Read 两个 audit 报告 diff，判断是 AI 没改还是 audit 规则本身有 bug",
        }
    return None


def detect_writer_output_loop(project_root: Path, ch: int) -> dict | None:
    """信号 3: 同章节相邻两版正文 Jaccard 相似度 > 0.85 = writer 在原地踏步。

    数据源：章节/第NNN章/*.txt（按 mtime 排序最新 2 个）
    """
    ch_dir = project_root / "章节" / f"第{ch:03d}章"
    if not ch_dir.exists():
        return None
    txts = sorted([p for p in ch_dir.glob("*.txt")],
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if len(txts) < 2:
        return None

    latest = txts[0].read_text(encoding="utf-8", errors="ignore")
    previous = txts[1].read_text(encoding="utf-8", errors="ignore")
    ng_a = chinese_ngrams(latest)
    ng_b = chinese_ngrams(previous)
    sim = jaccard(ng_a, ng_b)
    if sim > WRITER_LOOP_JACCARD:
        return {
            "signal": "WRITER_OUTPUT_LOOP",
            "severity": "stuck",
            "ch": ch,
            "jaccard": round(sim, 3),
            "threshold": WRITER_LOOP_JACCARD,
            "latest_file": str(txts[0].relative_to(project_root)),
            "previous_file": str(txts[1].relative_to(project_root)),
            "explanation": (
                f"第{ch}章相邻两版 4-gram Jaccard = {sim:.2%}（阈值 {WRITER_LOOP_JACCARD:.0%}）。"
                f"writer 改了一版但产出几乎一样 = LLM 卡在同一个采样路径上。"
            ),
            "human_action": "换 prompt / 换温度 / 换模型 / 跳过本章 — 不要再让同一个 writer 重跑",
        }
    return None


def detect_script_repeat_fail(state: dict) -> list[dict]:
    """信号 4: 同脚本同错累积 ≥ N 次。状态来自 --record-error 外部上报。"""
    findings = []
    err_log = state.get("script_errors", {})
    for key, info in err_log.items():
        if info.get("count", 0) >= SCRIPT_REPEAT_FAIL_THRESHOLD:
            script, code = key.split("::", 1) if "::" in key else (key, "?")
            findings.append({
                "signal": "SCRIPT_REPEAT_FAIL",
                "severity": "stuck",
                "script": script,
                "error_code": code,
                "count": info["count"],
                "threshold": SCRIPT_REPEAT_FAIL_THRESHOLD,
                "first_seen": info.get("first_seen"),
                "last_seen": info.get("last_seen"),
                "explanation": (
                    f"{script} 报同样错误 [{code}] {info['count']} 次。AI 反复试同样的修复 = "
                    f"它看不见自己用错策略了。"
                ),
                "human_action": "Read 错误日志，**换策略**：换模型 / 改 prompt / 临时跳过该 scanner",
            })
    return findings


# ============================================================
# Escalation 报告 + 状态持久化
# ============================================================

def write_escalation(project_root: Path, findings: list[dict]) -> Path:
    """命中卡死信号 → 写人工待办报告。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    esc_dir = project_root / "_数据库" / ".escalations"
    out_path = esc_dir / f"escalation_{ts}.json"
    payload = {
        "schema_version": "1.0",
        "escalation_ts": datetime.now().isoformat(timespec="seconds"),
        "trigger": "stuck_loop_guard",
        "verdict": "STOP_HUMAN_INTERVENTION_REQUIRED",
        "findings": findings,
        "next_steps": [
            "1. Read 上面每个 finding 的 evidence_files",
            "2. 判断是 AI 真的卡死，还是检测规则有 bug",
            "3. 如真卡死：换 prompt/模型/温度，或人工接管这章",
            "4. 修复后跑 `python stuck_loop_guard.py <project> --reset` 解锁",
        ],
        "_warning": "不要让 AI 自己说『已修复』就放过 —— Self-Correction Blind Spot 64.5% 适用",
    }
    save_json(out_path, payload)
    return out_path


def load_state(project_root: Path) -> dict:
    return load_json(project_root / "_数据库" / ".stuck_state.json", default={}) or {}


def save_state(project_root: Path, state: dict):
    save_json(project_root / "_数据库" / ".stuck_state.json", state)


# ============================================================
# 主流程
# ============================================================

def scan_chapter(project_root: Path, ch: int) -> list[dict]:
    """对单章跑 3 个章节级 detector。"""
    findings = []
    for detector in (detect_retry_thrashing, detect_unchanged_finding, detect_writer_output_loop):
        try:
            r = detector(project_root, ch)
            if r:
                findings.append(r)
        except Exception as e:
            # 任一 detector 出错都不阻断 —— 守卫本身不能成为新故障点
            print(f"[stuck_loop_guard] detector {detector.__name__} 异常: {e}", file=sys.stderr)
    return findings


def record_error(project_root: Path, script: str, error_code: str):
    """外部脚本失败时调用，累积信号 4。"""
    state = load_state(project_root)
    errs = state.setdefault("script_errors", {})
    key = f"{script}::{error_code}"
    now = datetime.now().isoformat(timespec="seconds")
    if key in errs:
        errs[key]["count"] += 1
        errs[key]["last_seen"] = now
    else:
        errs[key] = {"count": 1, "first_seen": now, "last_seen": now}
    save_state(project_root, state)
    print(f"[stuck_loop_guard] recorded {key} count={errs[key]['count']}")


def reset_state(project_root: Path):
    sp = project_root / "_数据库" / ".stuck_state.json"
    if sp.exists():
        sp.unlink()
        print(f"[stuck_loop_guard] 已清空 {sp}")
    else:
        print(f"[stuck_loop_guard] {sp} 不存在，无需清理")


def main():
    ap = argparse.ArgumentParser(description="卡死循环守卫 v23 Layer 0")
    ap.add_argument("project", help="项目根路径（如 workspace/novels/<book>）")
    ap.add_argument("--ch", type=int, help="只扫指定章")
    ap.add_argument("--status", action="store_true", help="只读现状，不写状态文件")
    ap.add_argument("--reset", action="store_true", help="清空 stuck_state（手动 unstick 后用）")
    ap.add_argument("--record-error", nargs=2, metavar=("SCRIPT", "ERROR_CODE"),
                    help="外部脚本上报错误：累积到信号 4 的计数")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"[stuck_loop_guard] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(3)

    if args.reset:
        reset_state(project_root)
        sys.exit(0)

    if args.record_error:
        record_error(project_root, args.record_error[0], args.record_error[1])
        sys.exit(0)

    # 扫描信号 1-3（章节级）
    findings = []
    if args.ch is not None:
        findings.extend(scan_chapter(project_root, args.ch))
    else:
        # 全项目活跃章扫
        active = latest_active_chapter(project_root)
        if active is not None:
            findings.extend(scan_chapter(project_root, active))
        # 也扫最近 5 章看是否有积压未处理的卡死
        all_chs = find_chapters(project_root)
        for ch in all_chs[-5:]:
            if active is not None and ch == active:
                continue
            findings.extend(scan_chapter(project_root, ch))

    # 扫描信号 4（脚本累积错误，跨章）
    state = load_state(project_root)
    findings.extend(detect_script_repeat_fail(state))

    # 分类汇总
    stuck = [f for f in findings if f.get("severity") == "stuck"]
    advisory = [f for f in findings if f.get("severity") == "advisory"]

    summary = {
        "stuck": len(stuck),
        "advisory": len(advisory),
        "total": len(findings),
    }
    print(f"[stuck_loop_guard] {summary['stuck']} 卡死信号 / {summary['advisory']} 提醒")
    for f in findings[:6]:
        sig = f.get("signal", "?")
        sev = f.get("severity", "?").upper()
        ch = f.get("ch", f.get("script", "?"))
        exp = f.get("explanation", "")[:100]
        print(f"  [{sev}] {sig} (ch/script={ch}): {exp}")

    # 命中 stuck → 写 escalation report + exit 2
    if stuck and not args.status:
        out_path = write_escalation(project_root, stuck)
        print(f"\n  ⛔ STOP — Human intervention required", file=sys.stderr)
        print(f"  报告: {out_path}", file=sys.stderr)
        print(f"  修复后跑: python stuck_loop_guard.py {args.project} --reset", file=sys.stderr)
        sys.exit(2)

    if stuck and args.status:
        print(f"\n  ⛔ [--status 只读模式] 检测到 {len(stuck)} 个卡死信号，但未写 escalation")
        sys.exit(2)

    if advisory:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
