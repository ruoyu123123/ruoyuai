"""causal_verifier.py — Trace2Skill 因果验证层 (MAPE-K Analyze 升级)

F1 调研 TOP1: arXiv:2603.25158 Trace2Skill 双分析师范式
- 旧: self_heal 靠指纹计数 (≥3 recurring / ≥5 known)
- 新: 计数达标后加因果验证: "这个错误真是这个原因导致的吗?"

机制 (确定性 · 零 LLM · 基于 incidents + kb 已有数据):
1. 读 self_heal_kb.json 的 recurring+ patterns
2. 对每个 pattern 做 3 个因果检验:
   a. 时序一致性: 该错误出现在被推断原因之后 (incidents 时间戳)
   b. 重现稳定性: 同一指纹的 incidents 是否在同一脚本/同一位置 (location 收敛)
   c. 修复后消失: 如果 kb 有 resolved 标记,后续 incidents 是否真的不再出现
3. 综合 → causal_confidence: high/medium/low
4. 写回 kb pattern 的 causal_verified + causal_confidence

只影响 severity 分级 (advisory): causal_confidence=high 的 pattern
优先被 adaptive_runner 关注, 不改 hard_gate。

接入: self_heal_engine --ingest 之后跑
exit 0: 不阻断

用法:
  python core/scripts/causal_verifier.py <project_root>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_incidents(project_root: Path) -> list[dict]:
    """读 incidents.jsonl 全量。"""
    runtime = project_root / "core" / "claude-home" / "runtime"
    inc_path = runtime / "incidents.jsonl"
    if not inc_path.exists():
        return []
    out = []
    for line in inc_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def verify_pattern(pattern: dict, incidents: list[dict]) -> dict:
    """对单个 kb pattern 做 3 项因果检验。

    Returns:
        {"causal_verified": True/False, "causal_confidence": "high"/"medium"/"low",
         "checks": {...}}
    """
    sig = pattern.get("signature", "")
    count = pattern.get("count", 0)

    # 找同指纹的所有 incidents
    matching = [i for i in incidents if i.get("signature", "") == sig]

    if not matching:
        return {
            "causal_verified": False,
            "causal_confidence": "low",
            "checks": {"matching_incidents": 0},
        }

    # 检验 A: 时序一致性 — incidents 时间戳是否递增(非乱序)
    timestamps = []
    for inc in matching:
        ts = inc.get("ts", inc.get("timestamp", ""))
        if ts:
            timestamps.append(ts)
    ts_monotonic = all(
        timestamps[i] <= timestamps[i + 1]
        for i in range(len(timestamps) - 1)
    ) if len(timestamps) >= 2 else True

    # 检验 B: 重现稳定性 — location 字段是否收敛(>70% 相同)
    locations = [inc.get("location", inc.get("loc", "")) for inc in matching]
    locations = [l for l in locations if l]
    if locations:
        from collections import Counter
        most_common_loc, most_common_count = Counter(locations).most_common(1)[0]
        loc_convergence = most_common_count / len(locations)
    else:
        loc_convergence = 0.0

    # 检验 C: 修复后消失 — 如果 pattern 有 resolved_at, 之后还有同指纹 incident?
    resolved_at = pattern.get("resolved_at", "")
    post_resolve_count = 0
    if resolved_at:
        post_resolve_count = sum(
            1 for inc in matching
            if inc.get("ts", inc.get("timestamp", "")) > resolved_at
        )

    # 综合判定
    score = 0
    if ts_monotonic:
        score += 1
    if loc_convergence >= 0.7:
        score += 1
    if resolved_at and post_resolve_count == 0:
        score += 1
    elif not resolved_at:
        score += 0.5  # 未修复=无法验证,半分

    if score >= 2.5:
        confidence = "high"
    elif score >= 1.5:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "causal_verified": score >= 1.5,
        "causal_confidence": confidence,
        "checks": {
            "matching_incidents": len(matching),
            "ts_monotonic": ts_monotonic,
            "loc_convergence": round(loc_convergence, 2),
            "post_resolve_count": post_resolve_count,
            "score": score,
        },
    }


def run(project_root: Path) -> dict:
    """对 kb 里所有 recurring+ patterns 做因果验证。"""
    runtime = project_root / "core" / "claude-home" / "runtime"
    kb_path = runtime / "self_heal_kb.json"
    kb = _load(kb_path)
    patterns = kb.get("patterns", {})

    if not patterns:
        return {"verified": 0, "total": 0}

    incidents = _load_incidents(project_root)
    verified_count = 0

    for sig, pat in patterns.items():
        severity = pat.get("severity", "new")
        if severity not in ("recurring", "known", "critical"):
            continue  # 只验证计数达标的

        result = verify_pattern(pat, incidents)
        pat["causal_verified"] = result["causal_verified"]
        pat["causal_confidence"] = result["causal_confidence"]
        pat["causal_checks"] = result["checks"]
        pat["causal_verified_at"] = datetime.now().isoformat(timespec="seconds")
        if result["causal_verified"]:
            verified_count += 1

    _save(kb_path, kb)
    total = sum(1 for p in patterns.values() if p.get("severity") in ("recurring", "known", "critical"))
    return {"verified": verified_count, "total": total}


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python causal_verifier.py <project_root>", file=sys.stderr)
        return 2
    r = run(Path(sys.argv[1]))
    print(f"[causal_verifier] {r['verified']}/{r['total']} patterns 因果验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
