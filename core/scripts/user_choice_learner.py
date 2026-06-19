"""user_choice_learner.py — 从走向卡选择中学习用户偏好

G2 调研发现: 用户选走向卡(3选1)的偏好信号被丢弃。被否决的 2 个候选
含 stakes_delta/scope_summary/ME 维度 = 隐式偏好金矿。

机制(零 LLM · 确定性 · 软累积):
- 读 emergence 输出的 candidates[] + 用户 choice
- 对比选中 vs 被否决: 选中的维度 +权重, 被否决的不扣分(防负样本陷阱)
- 累积写 用户偏好.json.inferred_behavior (带 confidence + 来源 cluster)

北极星⑤纪律: 纯 advisory · 只记偏好不改约束 · 用户可 /db 查看/否决

接入点: cluster-save-state step11 (cluster_choice_apply 之后跑)
exit 0: 不阻断

用法:
  python core/scripts/user_choice_learner.py <project_root> \
    --chosen <chosen_brief.json> \
    --candidates <emergence_output.json>
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
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


# 从 candidate brief 提取偏好维度
PREFERENCE_DIMS = [
    "stakes_delta",          # 高 vs 低张力
    "is_volume_finale",      # 偏好阶段终章
    "narrative_mode",        # linear vs in_medias_res
]


def _extract_dims(brief: dict) -> dict:
    """从 candidate brief 提取可比较的维度值。"""
    dims = {}
    for k in PREFERENCE_DIMS:
        v = brief.get(k)
        if v is not None:
            dims[k] = v
    # scope_summary 长度 → 偏好详细还是简洁
    scope = brief.get("scope_summary", "")
    if scope:
        dims["scope_length"] = len(scope)
    # scene 数量
    storyboard = brief.get("scene_storyboard", [])
    if storyboard:
        dims["scene_count"] = len(storyboard)
    return dims


def learn_from_choice(
    project_root: Path,
    chosen_brief: dict,
    all_candidates: list[dict],
    source_cluster: str = "",
) -> dict:
    """从用户选择中推断偏好。

    Returns:
        {"updated_dims": N, "new_signals": [...]}
    """
    pref_path = project_root / "_数据库" / "用户偏好.json"
    pref = _load(pref_path)
    pref.setdefault("inferred_behavior", {})
    ib = pref["inferred_behavior"]

    chosen_dims = _extract_dims(chosen_brief)
    chosen_id = chosen_brief.get("cluster_id", "?")

    # 被否决的候选
    rejected = [c for c in all_candidates if c.get("cluster_id") != chosen_id]

    signals = []
    ts = datetime.now().isoformat(timespec="seconds")

    for dim_key, chosen_val in chosen_dims.items():
        # 与被否决候选比较
        rejected_vals = [_extract_dims(r).get(dim_key) for r in rejected]
        rejected_vals = [v for v in rejected_vals if v is not None]
        if not rejected_vals:
            continue

        # 数值比较: 选中的值 vs 被否决的均值
        if isinstance(chosen_val, (int, float)):
            avg_rejected = sum(v for v in rejected_vals if isinstance(v, (int, float))) / len(rejected_vals)
            if abs(avg_rejected) < 1e-9 and abs(chosen_val) < 1e-9:
                continue
            direction = "higher" if chosen_val > avg_rejected else "lower"
            signal = {
                "dim": dim_key,
                "chosen_val": chosen_val,
                "rejected_avg": avg_rejected,
                "direction": direction,
                "source_cluster": source_cluster,
                "ts": ts,
            }
        elif isinstance(chosen_val, str):
            signal = {
                "dim": dim_key,
                "chosen_val": chosen_val,
                "rejected_vals": rejected_vals,
                "source_cluster": source_cluster,
                "ts": ts,
            }
        else:
            continue

        signals.append(signal)

        # 软累积: 只加不减 (防负样本陷阱)
        ib.setdefault(dim_key, {"observations": [], "confidence": 0.0})
        ib[dim_key]["observations"].append(signal)
        # confidence = min(1.0, 观察数 / 10) (10 次观察 → 1.0 满 confidence)
        ib[dim_key]["confidence"] = min(1.0, len(ib[dim_key]["observations"]) / 10.0)

    pref["inferred_behavior"] = ib
    _save(pref_path, pref)

    return {"updated_dims": len(signals), "new_signals": signals}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="从走向卡选择学习用户偏好 (零 LLM · advisory)")
    ap.add_argument("project_root")
    ap.add_argument("--chosen", required=True, help="用户选中的 brief JSON 路径")
    ap.add_argument("--candidates", required=True, help="emergence 全部 candidates JSON 路径")
    ap.add_argument("--source-cluster", default="", help="来源 cluster_id")
    args = ap.parse_args()

    chosen = _load(Path(args.chosen))
    if "answer" in chosen:
        chosen = chosen["answer"]

    cands_data = _load(Path(args.candidates))
    candidates = cands_data.get("candidates", [])
    if not candidates:
        print("[user_choice_learner] 无 candidates,跳过")
        return 0

    result = learn_from_choice(
        Path(args.project_root), chosen, candidates,
        source_cluster=args.source_cluster,
    )
    print(f"[user_choice_learner] 学到 {result['updated_dims']} 个偏好维度")
    for s in result["new_signals"]:
        print(f"  {s['dim']}: {s.get('direction', s.get('chosen_val', '?'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
