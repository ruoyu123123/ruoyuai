#!/usr/bin/env python3
"""preference_ranker.py — pairwise 偏好排序器（BPR 范式 · 纯 python 零依赖 · shadow）

【背景】core/ml/LEARNABLE_BACKLOG.md A4：cluster_emergence_engine.py 每次涌现 2-3 个
candidate brief，用户「从 candidate 里选 1 个」是零成本隐式偏好标签（chosen vs rejected）。
user_choice_learner.py 此前只做「逐维标量均值」——每个维度（stakes_delta/is_volume_finale/
narrative_mode/scope_length/scene_count）独立算 chosen 值 vs rejected 均值的方向，丢失了
候选间的组合信号（比如「finale + 详细 stakes_delta 描述」同时出现才被选中，拆开看每维
单独都不显著）。

【方法】把同一批隐式反馈升级成 pairwise 排序训练——Bayesian Personalized Ranking
(Rendle et al. 2009, arXiv:1205.2618) 的核心 pairwise logistic loss：

    BPR-Opt = Σ_(chosen,rejected) ln σ( x̂(chosen) − x̂(rejected) ) − (λ/2)·‖w‖²

原论文 x̂_uij 是「用户-物品」矩阵分解隐向量的内积（面向无结构化特征的 item id）；这里的
candidate 自带结构化字段（stakes_delta/is_volume_finale/narrative_mode/emergence_score/...），
不需要学 user/item 隐向量，退化成线性判别函数 x̂(c) = w·φ(c) 上的 BPR pairwise logistic
loss（φ = extract_features）——单用户/单项目场景，没有「多用户」需要 per-user embedding。
对 w 求导（σ' = σ·(1-σ)）得到 SGD 更新规则：

    Δx = φ(chosen) − φ(rejected)
    x̂  = w·Δx
    w  ← w + lr · [ (1 − σ(x̂))·Δx − λ·w ]

s(c) = w·φ(c) + b 形式的全局偏置 b 在做差 x̂ = s(chosen) − s(rejected) 时天然抵消，
所以不建模 bias 项。

【北极星⑤纪律】纯 advisory：本模块只产出 weights + score，绝不改变 candidate 的生成/
数量/顺序——涌现候选的排序仍由 cluster_emergence_engine 既有启发式(_score_one_me)决定，
本模块的输出只是附加的「参考分数」字段，供主代理展示走向卡时参考，最终选择权仍在用户。
冷启动（观察数 < COLD_START_MIN_OBSERVATIONS）返回 None，调用方必须优雅处理 None
（等同「还没学出东西」，不是错误）。

env 门控：RUOYU_PREF_RANKER=1 才生效（默认 off，约定同 RUOYU_NN_* 系列——见
adversarial_judge_pair.py 等处 `os.environ.get(...) != "1"` 用法）。

用法：
    import preference_ranker as pr
    weights = pr.train(observations)              # None = 冷启动/数据不足
    s = pr.score(candidate, weights)               # None = weights 为空
    pr.annotate_candidates(project_root, briefs)   # 就地加 preference_score 字段(advisory)

CLI（手动重训/调试）：
    python preference_ranker.py <project_root> [--retrain]
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

ENV_FLAG = "RUOYU_PREF_RANKER"

# ───────────────────────── 训练超参（确定性常量·不做自适应/随机搜索） ─────────────────────────

SEED = 20260703
EPOCHS = 200
LEARNING_RATE = 0.05
L2_REG = 0.01
COLD_START_MIN_OBSERVATIONS = 8  # 观察数(choice 事件数，非展开后的 pair 数) < 此值不训练

PERSIST_FILENAME = ".preference_ranker.json"
SCHEMA = "preference_ranker_v1"


def enabled() -> bool:
    """默认 off，显式 RUOYU_PREF_RANKER=1 才生效（约定同 RUOYU_NN_* 系列）。"""
    return os.environ.get(ENV_FLAG) == "1"


# ───────────────────────── 特征抽取（真实字段来自 cluster_emergence_engine.me_to_cluster_brief） ─────────────────────────

_CJK_RE = re.compile(r"[一-鿿]+")
_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def _keyword_set(text: str) -> frozenset:
    """中文 2-gram + 英文/数字 token（与 cluster_emergence_engine._keyword_set 同算法，独立
    复制一份——preference_ranker 不反向 import cluster_emergence_engine，避免循环依赖）。"""
    if not text:
        return frozenset()
    toks = set()
    for w in _WORD_RE.findall(text):
        toks.add(w.lower())
    for seg in _CJK_RE.findall(text):
        if len(seg) >= 2:
            for i in range(len(seg) - 1):
                toks.add(seg[i:i + 2])
        else:
            toks.add(seg)
    return frozenset(toks)


def candidate_text(candidate: dict) -> str:
    """候选可供关键词比对的文本面：scope_summary + stakes_delta（两者都是 LLM 自由文本，
    schema 见 gen_creative.py 的 major_events_pool 契约 + cluster_emergence_engine.me_to_cluster_brief）。"""
    if not isinstance(candidate, dict):
        return ""
    parts = [str(candidate.get("scope_summary") or ""), str(candidate.get("stakes_delta") or "")]
    return " ".join(p for p in parts if p)


def extract_features(candidate: dict, history_keywords: frozenset = frozenset()) -> dict:
    """从 candidate brief 抽取可训练数值特征。字段来源实地读码（cluster_emergence_engine.py
    :512-591 的 me_to_cluster_brief）：cluster_id/parent_me/scope_summary/_emergence_score/
    _emergence_reasons/status/ME_to_advance/volume/is_volume_finale/stakes_delta/intent/
    narrative_mode/belief_update_intent/event_boundary_sharpness/scene_storyboard/
    anchor_props/foreshadowing_to_plant/premise_blend_card/research_ref。

    history_keywords：可选——用户历史选中候选的关键词集合（build_history_keywords 构建），
    用于算「与用户历史选择的关键词重叠率」。不传则该特征恒 0，不影响其余特征的纯函数性。
    """
    if not isinstance(candidate, dict):
        return {}
    feats: dict = {}

    scope = str(candidate.get("scope_summary") or "")
    feats["scope_length"] = float(len(scope))

    # stakes_delta 是 LLM 自由文本「相对前一小走向的强度增量」（gen_creative.py:403 契约：
    # "相对前一小走向的强度增量（try-fail 递增）"），不是数值——数值化只能走文本量代理。
    stakes = str(candidate.get("stakes_delta") or "")
    feats["stakes_delta_length"] = float(len(stakes))
    feats["stakes_delta_present"] = 1.0 if stakes.strip() else 0.0

    feats["is_volume_finale"] = 1.0 if candidate.get("is_volume_finale") else 0.0

    vol = candidate.get("volume")
    feats["volume"] = float(vol) if isinstance(vol, (int, float)) else 0.0

    # narrative_mode / intent onehot：值域开放（linear/in_medias_res/kishotenketsu_4act，
    # healing/contemplative/iyashikei/zen…），不维护穷举表，未知取值自动变成新特征 key。
    nm = candidate.get("narrative_mode")
    if nm:
        feats[f"narrative_mode_{nm}"] = 1.0
    intent = candidate.get("intent")
    if intent:
        feats[f"intent_{intent}"] = 1.0

    feats["belief_update_intent_update"] = 1.0 if candidate.get("belief_update_intent") == "update" else 0.0
    feats["event_boundary_sharp"] = 1.0 if candidate.get("event_boundary_sharpness") == "sharp" else 0.0

    # 涌现引擎自身启发式分数/理由数（_score_one_me 已加权多信号）当元特征喂给 BPR，
    # 让 pairwise 排序器学会「引擎打分该信多少」，而不是完全从零学。
    score_val = candidate.get("_emergence_score")
    feats["emergence_score"] = float(score_val) if isinstance(score_val, (int, float)) else 0.0
    reasons = candidate.get("_emergence_reasons")
    feats["emergence_reason_count"] = float(len(reasons)) if isinstance(reasons, list) else 0.0

    # candidate 阶段 scene/prop/foreshadow 通常是空雏形（outline-planner 详化后才有值）——
    # 为「用户选定后二次评分」场景预留，候选阶段多为 0 属预期，不是 bug。
    for key, feat_name in (
        ("scene_storyboard", "scene_count"),
        ("anchor_props", "anchor_prop_count"),
        ("foreshadowing_to_plant", "foreshadowing_count"),
        ("ME_to_advance", "me_count"),
    ):
        v = candidate.get(key)
        feats[feat_name] = float(len(v)) if isinstance(v, list) else 0.0

    cand_kw = _keyword_set(candidate_text(candidate))
    if history_keywords and cand_kw:
        feats["history_keyword_overlap"] = len(cand_kw & history_keywords) / len(cand_kw)
    else:
        feats["history_keyword_overlap"] = 0.0

    return feats


def build_history_keywords(observations: list) -> frozenset:
    """从历史 pairwise_observations（user_choice_learner.py 持久化）重建「用户过去选中
    候选」的关键词集合，供 extract_features 的 history_keyword_overlap 特征用。"""
    kws = set()
    for obs in observations or []:
        if isinstance(obs, dict) and obs.get("chosen_text"):
            kws |= _keyword_set(str(obs["chosen_text"]))
    return frozenset(kws)


# ───────────────────────── pairwise BPR 训练 ─────────────────────────

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _build_pairs(observations: list) -> tuple:
    """把每条 choice 记录（chosen_features + rejected_features[]）展开成
    (chosen_features, rejected_features) pair 列表 + 全部出现过的 feature key（排序固定顺序）。"""
    pairs = []
    all_keys = set()
    for obs in observations or []:
        if not isinstance(obs, dict):
            continue
        cf = obs.get("chosen_features")
        rfs = obs.get("rejected_features")
        if not isinstance(cf, dict) or not isinstance(rfs, list):
            continue
        all_keys.update(cf.keys())
        for rf in rfs:
            if not isinstance(rf, dict):
                continue
            all_keys.update(rf.keys())
            pairs.append((cf, rf))
    return pairs, sorted(all_keys)


def train(observations: list, *, epochs: int = EPOCHS, lr: float = LEARNING_RATE,
          l2: float = L2_REG, seed: int = SEED):
    """pairwise logistic (BPR) 训练器。observations = user_choice_learner 累积的
    pairwise_observations 记录列表（每条含 chosen_features + rejected_features[]）。

    观察数（choice 事件数，不是展开后的 pair 数）< COLD_START_MIN_OBSERVATIONS → 冷启动
    不训练，返回 None。确定性：固定 seed，同输入两次调用权重逐位一致。
    """
    if not observations or len(observations) < COLD_START_MIN_OBSERVATIONS:
        return None

    pairs, feature_names = _build_pairs(observations)
    if not pairs or not feature_names:
        return None

    w = {name: 0.0 for name in feature_names}
    rng = random.Random(seed)
    order = list(range(len(pairs)))

    for _epoch in range(epochs):
        rng.shuffle(order)
        for idx in order:
            cf, rf = pairs[idx]
            delta = {name: cf.get(name, 0.0) - rf.get(name, 0.0) for name in feature_names}
            x_hat = sum(w[name] * delta[name] for name in feature_names)
            grad_scale = 1.0 - _sigmoid(x_hat)  # (1 - σ(x̂))
            for name in feature_names:
                w[name] += lr * (grad_scale * delta[name] - l2 * w[name])

    return w


def score(candidate: dict, weights, history_keywords: frozenset = frozenset()):
    """推理：w·φ(candidate)。weights 为空（冷启动/未训练）→ None（调用方顾问性使用）。"""
    if not weights:
        return None
    feats = extract_features(candidate, history_keywords)
    return sum(weights.get(name, 0.0) * val for name, val in feats.items())


# ───────────────────────── 持久化（项目级 _数据库/.preference_ranker.json） ─────────────────────────

def persist_path(project_root) -> Path:
    return Path(project_root) / "_数据库" / PERSIST_FILENAME


def save(project_root, weights: dict, n_observations: int) -> Path:
    path = persist_path(project_root)
    payload = {
        "_schema": SCHEMA,
        "weights": weights,
        "feature_names": sorted(weights.keys()),
        "n_observations": n_observations,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load(project_root):
    """读 .preference_ranker.json；缺失/损坏/无 weights → None（调用方顾问性使用）。"""
    data = _load_json(persist_path(project_root))
    if not isinstance(data, dict) or not isinstance(data.get("weights"), dict):
        return None
    return data


# ───────────────────────── 接线辅助：只加字段不改序 ─────────────────────────

def annotate_candidates(project_root, candidates_briefs: list) -> int:
    """给 candidates_briefs 就地附加 `preference_score`/`preference_rank_hint`（advisory
    参考字段）。绝不改变 candidates_briefs 的生成顺序/数量/其余内容——涌现候选的排序仍由
    cluster_emergence_engine 既有启发式(_score_one_me)决定，这里只加展示层参考信息。

    门控关闭 / 无已训练 weights → no-op，返回 0（candidates_briefs 逐字节不变）。
    返回被打上分数的候选数量（供调用方日志/测试用）。
    """
    if not enabled():
        return 0
    data = load(project_root)
    if not data:
        return 0
    weights = data.get("weights")
    if not weights:
        return 0

    pref = _load_json(Path(project_root) / "_数据库" / "用户偏好.json", {}) or {}
    history_kw = build_history_keywords(pref.get("pairwise_observations", []))

    scored = []
    for c in candidates_briefs:
        if not isinstance(c, dict):
            continue
        s = score(c, weights, history_kw)
        if s is None:
            continue
        c["preference_score"] = round(s, 6)
        scored.append(c)

    # rank_hint：分数降序名次（advisory 展示用，不改 candidates_briefs 列表本身的顺序）
    for rank, c in enumerate(sorted(scored, key=lambda d: -d["preference_score"]), 1):
        c["preference_rank_hint"] = rank
    return len(scored)


# ───────────────────────── CLI（手动重训/调试） ─────────────────────────

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="pairwise 偏好排序器（BPR·手动重训/调试用）")
    ap.add_argument("project_root")
    ap.add_argument("--retrain", action="store_true", help="强制用当前 pairwise_observations 重训并落盘")
    args = ap.parse_args()

    project_root = Path(args.project_root)
    pref = _load_json(project_root / "_数据库" / "用户偏好.json", {}) or {}
    observations = pref.get("pairwise_observations", [])
    print(f"[preference_ranker] pairwise_observations={len(observations)}"
          f"（冷启动阈值={COLD_START_MIN_OBSERVATIONS}）")

    if not args.retrain:
        data = load(project_root)
        if data:
            print(f"[preference_ranker] 已有 weights（trained_at={data.get('trained_at')}，"
                  f"n_observations={data.get('n_observations')}）")
        else:
            print("[preference_ranker] 尚无已训练 weights（--retrain 手动触发）")
        return 0

    weights = train(observations)
    if weights is None:
        print(f"[preference_ranker] 观察数不足（<{COLD_START_MIN_OBSERVATIONS}）或无有效 pair，跳过训练")
        return 0
    path = save(project_root, weights, n_observations=len(observations))
    print(f"[preference_ranker] 训练完成 → {path}（{len(weights)} 维特征）")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
