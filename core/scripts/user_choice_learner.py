"""user_choice_learner.py — 从走向卡选择中学习用户偏好

G2 调研发现: 用户选走向卡(3选1)的偏好信号被丢弃。被否决的 2 个候选
含 stakes_delta/scope_summary/ME 维度 = 隐式偏好金矿。

机制(零 LLM · 确定性 · 正式状态账本):
- 读 emergence 输出的 candidates[] + 用户 choice
- 对比选中 vs 被否决: 选中的维度 +权重, 被否决的不扣分(防负样本陷阱)
- 累积写 用户偏好.json.inferred_behavior (带 confidence + 来源 cluster)

边界: 只记偏好不改约束，不自动替用户选择；后续走向卡只可把它作为排序/解释依据。

接入点: cluster-save-state step13 after_pause (cluster_choice_apply 之后跑)
退出码: 0 写入成功 / 2 输入缺失、候选为空或 JSON 损坏

🆕 pairwise 偏好排序(BPR·core/ml/LEARNABLE_BACKLOG.md A4)：上面的逐维标量均值只看单维度
方向，丢了候选间的组合信号。RUOYU_PREF_RANKER=1 时(默认 off)，本文件额外把每次 choice 的
chosen/rejected 候选特征向量存进 用户偏好.json.pairwise_observations，观察数达标(≥8)后调
preference_ranker.train() 做 pairwise logistic 训练并落盘 _数据库/.preference_ranker.json。
逐维标量均值逻辑本身不变——两套机制并存、互不覆盖。详见 preference_ranker.py。

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

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _load(p: Path) -> dict:
    if not p.exists():
        raise FileNotFoundError(str(p))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{p} JSON 解析失败: {exc}") from exc


def _load_optional(p: Path) -> dict:
    if not p.exists():
        return {}
    return _load(p)


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
    pref = _load_optional(pref_path)
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

    # 🆕 pairwise 偏好排序(BPR·advisory·RUOYU_PREF_RANKER=1 才生效)：门控关闭时以下整块
    # no-op，pref 内容与门控前逐字节相同 → _save 落盘结果零回归。
    ranker_info: dict = {}
    try:
        import preference_ranker as pr
    except ImportError:
        pr = None
    if pr is not None and pr.enabled():
        history_kw = pr.build_history_keywords(pref.get("pairwise_observations", []))
        pref.setdefault("pairwise_observations", []).append({
            "chosen_features": pr.extract_features(chosen_brief, history_kw),
            "chosen_text": pr.candidate_text(chosen_brief),
            "rejected_features": [pr.extract_features(r, history_kw) for r in rejected],
            "source_cluster": source_cluster,
            "ts": ts,
        })
        observations = pref["pairwise_observations"]
        ranker_info["pairwise_observations_count"] = len(observations)
        weights = pr.train(observations)
        ranker_info["ranker_trained"] = weights is not None
        if weights is not None:
            pr.save(project_root, weights, n_observations=len(observations))

    _save(pref_path, pref)

    return {"updated_dims": len(signals), "new_signals": signals, **ranker_info}


def main() -> int:
    import argparse
    # 创作流程独立 CLI 入口（cluster-save-state plan 直接调用·不经 save_state.main）——
    # 与 audit_hub/save_state 同款 setdefault 默认门控，否则 RUOYU_PREF_RANKER 观察永不积累。
    try:
        import nn_runtime_defaults
        nn_runtime_defaults.enable_creative_nn_defaults()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="从走向卡选择学习用户偏好 (零 LLM · advisory)")
    ap.add_argument("project_root")
    ap.add_argument("--chosen", required=True, help="用户选中的 brief JSON 路径")
    ap.add_argument("--candidates", required=True, help="emergence 全部 candidates JSON 路径")
    ap.add_argument("--source-cluster", default="", help="来源 cluster_id")
    args = ap.parse_args()

    # 🔴 2026-07-08 验证书 e2e 抓出：plan 模板 after_pause_scripts 的调用形式是
    # project-relative 路径（如 `_数据库/.wal/cluster_002_user_choice.json`），
    # 与同一 after_pause_scripts 里 cluster_choice_apply.py 的 --choice 同款约定。
    # 此前本脚本把 --chosen/--candidates 当字面路径（相对 CWD 非相对 project_root）
    # 直接 _load，模板给的相对路径必炸 FileNotFoundError——每本书 save-state 走到
    # step13 都会命中，只是此前从未有 cluster 真正端到端跑到这一步（无测试覆盖，
    # 既有测试全传绝对路径）。与 cluster_choice_apply.py 同规则补 join。
    project_root = Path(args.project_root)
    chosen_path = Path(args.chosen)
    if not chosen_path.is_absolute():
        chosen_path = project_root / chosen_path
    candidates_path = Path(args.candidates)
    if not candidates_path.is_absolute():
        candidates_path = project_root / candidates_path

    chosen = _load(chosen_path)
    if "answer" in chosen:
        chosen = chosen["answer"]

    cands_data = _load(candidates_path)
    candidates = cands_data.get("candidates", [])
    if not candidates:
        raise RuntimeError("brief_candidates 缺少 candidates，无法记录走向卡偏好")
    if not isinstance(candidates, list):
        raise RuntimeError("brief_candidates.candidates 必须是列表")

    result = learn_from_choice(
        project_root, chosen, candidates,
        source_cluster=args.source_cluster,
    )
    print(f"[user_choice_learner] 学到 {result['updated_dims']} 个偏好维度")
    for s in result["new_signals"]:
        print(f"  {s['dim']}: {s.get('direction', s.get('chosen_val', '?'))}")
    if "pairwise_observations_count" in result:
        print(f"[user_choice_learner] pairwise 观察数={result['pairwise_observations_count']}"
              f" ranker_trained={result.get('ranker_trained')}")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[user_choice_learner] FATAL: {exc}", file=sys.stderr)
        raise SystemExit(2)
