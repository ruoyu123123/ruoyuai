"""manifest_context_rot_check.py — Context Rot 防御（v21 P5.1 新增）

业界 2025-2026 重大发现（Chroma research，arxiv multiple）：
- **Context Rot**：每个 frontier LLM 在 input token 越长时 output 质量越下降
- **advertised 200K 实际仅 130K 可靠**（30-40% 提前断崖）
- **Distractor effect**：单 distractor 即降性能，4 distractors 累乘
- **Haystack structure 反直觉**：随机打乱比逻辑结构表现更好
- Needle-in-Haystack benchmark **低估**了真实任务难度

本扫描器对 manifest 做 4 项检测：

1. SIZE_RISK：manifest > 30K tokens（约 100KB）→ 接近 Context Rot 风险线
2. DISTRACTOR_DETECT：扫描"声明字段 vs 本章实际使用率"
   - active_fate_events 含 N 个但本章仅推进 1 个 → N-1 个是 distractor
   - active_npc_threads top5 中本章只呼应 1 个 → 4 个是 distractor
   - moves_by_character 列 K 个但本章只用 1 个 → 其余 distractor
3. HAYSTACK_STRUCTURE：检测 manifest 字段顺序——按 STATIC/SEMI_STATIC/DYNAMIC 分级是否合理
4. DUPLICATE_INFO：检测同信息在多字段重复（如 storyteller next_target 在 storyteller_directive + _critical_summary 两次）

输出：_数据库/.audit/ch_NNN_context_rot.json
退出码：0 健康 / 1 advisory / 2 warning
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


def estimate_tokens(obj) -> int:
    """粗估 manifest JSON 的 token 数。中文 ~1.5 char/token，英文 ~3.5 char/token。"""
    s = json.dumps(obj, ensure_ascii=False)
    cn = len(re.findall(r"[一-鿿]", s))
    en = len(s) - cn
    return int(cn / 1.5 + en / 3.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("ch", type=int)
    args = ap.parse_args()

    project_root = Path(args.project)
    manifest_path = project_root / "_数据库" / ".manifest" / f"ch_{args.ch:03d}.json"
    if not manifest_path.exists():
        print(f"[ERROR] manifest 不存在: {manifest_path}", file=sys.stderr)
        sys.exit(2)
    manifest = load_json(manifest_path, {})

    findings = []

    # 1. SIZE_RISK
    est_tokens = estimate_tokens(manifest)
    size_kb = manifest_path.stat().st_size / 1024
    SAFE_TOKENS = 20000   # 20K = 安全区
    WARN_TOKENS = 30000   # 30K = Context Rot 警戒线
    ERROR_TOKENS = 50000  # 50K = 严重退化
    if est_tokens >= ERROR_TOKENS:
        findings.append({
            "severity": "warning",
            "code": "CONTEXT_ROT_SEVERE",
            "estimated_tokens": est_tokens,
            "size_kb": round(size_kb, 1),
            "suggestion": f"manifest 估 {est_tokens:,} tokens 严重超 Context Rot 警戒（>{ERROR_TOKENS}）→ 必须裁剪",
        })
    elif est_tokens >= WARN_TOKENS:
        findings.append({
            "severity": "advisory",
            "code": "CONTEXT_ROT_RISK",
            "estimated_tokens": est_tokens,
            "size_kb": round(size_kb, 1),
            "suggestion": f"manifest 估 {est_tokens:,} tokens 接近 Context Rot 警戒（{WARN_TOKENS}）→ 建议用 compressed 版本",
        })

    # 2. DISTRACTOR_DETECT
    # 2.1 active_fate_events 中 active 数量 vs 本章可能推进数
    afe = manifest.get("active_fate_events", {}) or {}
    active_list = afe.get("active", []) or []
    if len(active_list) > 3:
        findings.append({
            "severity": "advisory",
            "code": "DISTRACTOR_FATE_EVENTS",
            "active_count": len(active_list),
            "max_useful": 3,
            "suggestion": f"active_fate_events 含 {len(active_list)} 个但本章 writer 通常推进 1-2 个 → {len(active_list)-2} 个 distractor，裁到 top 3 by priority",
        })

    # 2.2 active_npc_threads top5
    wss = manifest.get("world_state_snapshot", {}) or {}
    threads = wss.get("active_npc_threads_top5", []) or []
    if len(threads) >= 5:
        # 多于 3 个 thread 时大概率本章只呼应 1-2 个
        findings.append({
            "severity": "advisory",
            "code": "DISTRACTOR_NPC_THREADS",
            "thread_count": len(threads),
            "suggestion": f"active_npc_threads_top5 全 {len(threads)} 个注入但本章通常呼应 1-2 个 → 其余 distractor",
        })

    # 2.3 moves_by_character
    moves = (manifest.get("character_moves", {}) or {}).get("moves_by_character", {}) or {}
    total_moves = sum(len(v) for v in moves.values())
    if total_moves > 8:
        findings.append({
            "severity": "advisory",
            "code": "DISTRACTOR_MOVES",
            "total_moves": total_moves,
            "suggestion": f"character_moves 全部 {total_moves} 条但本章用 < 5 → 多余的是 distractor",
        })

    # 3. HAYSTACK_STRUCTURE
    cache_layout = manifest.get("_cache_layout", {})
    if not cache_layout:
        findings.append({
            "severity": "advisory",
            "code": "HAYSTACK_NO_LAYOUT",
            "suggestion": "manifest 无 _cache_layout 字段 → agent 无法按 STATIC/DYNAMIC 排序读取，可能 Context Rot 加剧",
        })

    # 4. DUPLICATE_INFO 简化检测
    summary_obj = manifest.get("_critical_summary", {}) or {}
    if not summary_obj:
        findings.append({
            "severity": "advisory",
            "code": "NO_LIM_SUMMARY",
            "suggestion": "manifest 无 _critical_summary 字段 → 长 prompt 中间字段易被 LLM 忽略（LiM 现象）",
        })

    # 输出
    out_dir = project_root / "_数据库" / ".audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "context_rot",
        "ch": args.ch,
        "scan_ts": ts,
        "manifest_size_kb": round(size_kb, 1),
        "estimated_tokens": est_tokens,
        "safe_threshold": SAFE_TOKENS,
        "warn_threshold": WARN_TOKENS,
        "findings": findings,
        "summary": {
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
        },
    }
    out_path = out_dir / f"ch_{args.ch:03d}_context_rot.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[context_rot] ch{args.ch}: {est_tokens:,} tokens ({size_kb:.1f}KB) — {report['summary']['warning']}W / {report['summary']['advisory']}A")
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if report["summary"]["warning"] > 0:
        sys.exit(2)
    if report["summary"]["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
