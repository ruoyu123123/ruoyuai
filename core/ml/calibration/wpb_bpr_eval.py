#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-07 S6 WritingPreferenceBench 外部校准 BPR（二轮移植）
"""wpb_bpr_eval.py — 用 WritingPreferenceBench 600 中文创意写作偏好对
（m-a-p/Writing-Preference-Bench · arXiv:2510.14616）外部校准 preference_ranker（BPR）。

【适配缺口·先说清再测】preference_ranker 的特征面是**涌现候选 brief 的结构化字段**
（stakes_delta/is_volume_finale/narrative_mode/_emergence_score/scene_count…），不是散文。
WPB 的偏好对是两段完整散文 response——映射成 candidate（scope_summary=散文全文）后，
真正被激活的特征只有：
    scope_length（文本长度）+ stakes_delta_present=0 + history_keyword_overlap
    （与训练集内被选中文本的中文 2-gram 重叠率）
其余 structural 特征恒 0。所以本评估测的是「BPR 现有特征面对纯散文偏好的可分辨上限」，
**不是**「BPR 在其原生涌现候选场景的水平」——两者都如实写进报告。

【评估设计】
  E0 零样本（已训练权重直接测）：实地核查全仓无任何 .preference_ranker.json
     （用户偏好.json pairwise_observations=0）→ E0 不可行，报告如实记录。
  E1 可训练性上限（k 折交叉验证）：把 600 对按 seed 洗牌分 k 折，
     每折用其余折训练 BPR（观察= {chosen_features, rejected_features}），
     history_keywords 只从**训练折**的 chosen 文本构建（防泄漏），
     测试折 accuracy = mean[ score(chosen) > score(rejected) ]（打平计 0.5）。
  B1 长度基线：len(chosen) > len(rejected) 视为答对（打平 0.5）——
     实测 WPB 中文集 chosen 更长者仅 336/600，长度基线本身≈0.56。
  分桶：per-tag（51 细类·n≈12 噪声大）+ 4 个粗桶（论文实证单模型跨类目方差
  18.2%-81.8%，不分桶无信息量）。

用法：
  py core/ml/calibration/wpb_bpr_eval.py --data <WP_bench_chinese.json>
     --out <report.json> [--folds 5] [--seed 20260707]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPO / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import preference_ranker as pr  # noqa: E402

DEFAULT_SEED = 20260707
DEFAULT_FOLDS = 5

# 粗桶映射（确定性显式表·未列 tag 落 functional_practical）
_COARSE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("abstract_subculture", ("抽象", "亚文化", "饭圈", "黑话", "嘻哈", "电竞")),
    ("creative_fiction", ("小说", "故事", "童话", "剧本", "角色扮演", "游记", "传记")),
    ("poetry_prose_letters", ("诗歌", "散文", "公开信", "致谢", "悼词", "书评",
                              "博客", "社交媒体")),
]


def coarse_bucket(tag: str) -> str:
    for bucket, keys in _COARSE_RULES:
        if any(k in tag for k in keys):
            return bucket
    return "functional_practical"


def load_pairs(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = []
    for item in data:
        chosen = (item.get("chosen") or {}).get("response") or ""
        rejected = (item.get("rejected") or {}).get("response") or ""
        if not chosen or not rejected:
            continue
        pairs.append({"tag": item.get("tag") or "unknown",
                      "bucket": coarse_bucket(item.get("tag") or ""),
                      "chosen": chosen, "rejected": rejected})
    return pairs


def to_candidate(text: str) -> dict:
    """散文 → BPR candidate 的唯一可行映射（适配缺口见模块 docstring）。"""
    return {"scope_summary": text}


def _accuracy_row(scored: list[tuple[float, float]]) -> dict:
    """scored = [(score_chosen, score_rejected)]。打平计 0.5。"""
    if not scored:
        return {"n": 0, "accuracy": None, "ties": 0}
    wins = sum(1.0 if c > r else (0.5 if c == r else 0.0) for c, r in scored)
    ties = sum(1 for c, r in scored if c == r)
    return {"n": len(scored), "accuracy": round(wins / len(scored), 4), "ties": ties}


def kfold_eval(pairs: list[dict], folds: int, seed: int) -> dict:
    """E1：k 折 CV。返回总 accuracy + 分桶 + per-tag + 特征激活审计。"""
    order = list(range(len(pairs)))
    random.Random(seed).shuffle(order)
    fold_of = {idx: i % folds for i, idx in enumerate(order)}

    per_pair: list[dict] = []          # {tag,bucket,score_c,score_r}
    fold_summaries = []
    active_features: set[str] = set()

    for f in range(folds):
        train_idx = [i for i in range(len(pairs)) if fold_of[i] != f]
        test_idx = [i for i in range(len(pairs)) if fold_of[i] == f]
        history_kw = pr.build_history_keywords(
            [{"chosen_text": pairs[i]["chosen"]} for i in train_idx])
        observations = []
        for i in train_idx:
            observations.append({
                "chosen_features": pr.extract_features(to_candidate(pairs[i]["chosen"]),
                                                       history_kw),
                "rejected_features": [pr.extract_features(to_candidate(pairs[i]["rejected"]),
                                                          history_kw)],
            })
        weights = pr.train(observations)
        if weights is None:
            raise RuntimeError(f"fold {f}: BPR 冷启动拒训（观察数 {len(observations)}）——数据异常")
        scored = []
        for i in test_idx:
            fc = pr.extract_features(to_candidate(pairs[i]["chosen"]), history_kw)
            fr = pr.extract_features(to_candidate(pairs[i]["rejected"]), history_kw)
            active_features |= {k for k, v in fc.items() if v != 0.0}
            active_features |= {k for k, v in fr.items() if v != 0.0}
            sc = sum(weights.get(k, 0.0) * v for k, v in fc.items())
            sr = sum(weights.get(k, 0.0) * v for k, v in fr.items())
            scored.append((sc, sr))
            per_pair.append({"tag": pairs[i]["tag"], "bucket": pairs[i]["bucket"],
                             "score_c": sc, "score_r": sr})
        fold_summaries.append({"fold": f, **_accuracy_row(scored),
                               "weights_nonzero": sum(1 for v in weights.values() if v != 0.0)})

    def group(key: str) -> dict:
        buckets = defaultdict(list)
        for p in per_pair:
            buckets[p[key]].append((p["score_c"], p["score_r"]))
        return {k: _accuracy_row(v) for k, v in sorted(buckets.items())}

    return {
        "overall": _accuracy_row([(p["score_c"], p["score_r"]) for p in per_pair]),
        "per_fold": fold_summaries,
        "per_bucket": group("bucket"),
        "per_tag": group("tag"),
        "active_feature_audit": sorted(active_features),
    }


def length_baseline(pairs: list[dict]) -> dict:
    scored = [(float(len(p["chosen"])), float(len(p["rejected"]))) for p in pairs]
    buckets = defaultdict(list)
    for p, s in zip(pairs, scored):
        buckets[p["bucket"]].append(s)
    return {"overall": _accuracy_row(scored),
            "per_bucket": {k: _accuracy_row(v) for k, v in sorted(buckets.items())}}


def find_project_weights() -> list[str]:
    """E0 前提核查：全仓已训练 BPR 权重文件。"""
    root = _REPO / "workspace"
    if not root.exists():
        return []
    return [str(p) for p in root.rglob(pr.PERSIST_FILENAME)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WritingPreferenceBench 外部校准 BPR（S6）")
    ap.add_argument("--data", required=True, help="WP_bench_chinese.json 路径")
    ap.add_argument("--out", required=True, help="报告 JSON 输出路径")
    ap.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)

    pairs = load_pairs(Path(args.data))
    print(f"[wpb_bpr_eval] {len(pairs)} 对（folds={args.folds} seed={args.seed}）")

    existing = find_project_weights()
    e0 = {"feasible": bool(existing), "found_weight_files": existing,
          "note": ("全仓无已训练 .preference_ranker.json（pairwise_observations=0）——"
                   "零样本外部测试不可行；以下 E1 测的是 BPR 特征面的可训练性上限"
                   if not existing else "存在已训练权重（未在本版实现零样本路径·见报告）")}

    report = {
        "schema": "wpb_bpr_eval_v1",
        "dataset": str(args.data),
        "n_pairs": len(pairs),
        "folds": args.folds,
        "seed": args.seed,
        "adaptation_gap": ("BPR 特征面为涌现候选结构化字段；散文对只能激活 "
                           "scope_length / stakes_delta_present(恒0) / history_keyword_overlap，"
                           "其余特征恒 0——见 active_feature_audit 实证"),
        "E0_zero_shot": e0,
        "E1_kfold": kfold_eval(pairs, args.folds, args.seed),
        "B1_length_baseline": length_baseline(pairs),
        "paper_reference": {"genrm_structured": 0.818, "scalar_head": 0.527,
                            "note": "arXiv:2510.14616：短结构化推理 GenRM 81.8% vs 打分头 52.7%；"
                                    "单模型跨类目方差 18.2%-81.8%"},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    e1 = report["E1_kfold"]
    print(f"[E1] overall accuracy = {e1['overall']['accuracy']}"
          f"（ties={e1['overall']['ties']}）")
    print(f"[B1] length baseline  = {report['B1_length_baseline']['overall']['accuracy']}")
    for b, row in e1["per_bucket"].items():
        print(f"  bucket {b:<24} n={row['n']:<4} acc={row['accuracy']}")
    print(f"[audit] 激活特征 = {e1['active_feature_audit']}")
    print(f"[wpb_bpr_eval] report → {out}")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
