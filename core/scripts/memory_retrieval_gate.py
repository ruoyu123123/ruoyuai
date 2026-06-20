#!/usr/bin/env python3
"""memory_retrieval_gate.py — 动态记忆检索 gate（脚手架·R7 W2 Batch-E·2026-06-20）

【调研锚 R7-W1】MemGPT / MemoryBank / Reflexion 等长期记忆体系把"全量喂模型"升级为
"按当前 scene 语义检索 top-k 注入"。若渝当前 build_manifest 是确定性全量注入（cache 友好），
本 gate 是**按需挂载的过滤层**：当 CLUSTER 上下文超长（>50k token 预算）且当前 scene 主语义
明确时，从 manifest.dynamic_context 候选块中 gen-model 5 分制评分，取 top-k；否则**fail-safe
退化静态全量注入**（不影响现有流程）。

【北极星纪律】
  · 默认 **off**：`MEMORY_RETRIEVAL_GATE_MODE=off`（不挂载·零回归·零额外 API 成本）
  · 失败必降级：gen-model 调用失败/超时 → 静态全量注入（绝不阻断流水线）
  · 仅过滤候选段：动态段（dynamic_context）；不动作者档/锁定事实/cluster brief 等权威静态段
  · 不引入新 hard_gate（北极星⑤）·不改 build_manifest 主路径
  · cluster 单位：分块以 scene/cluster_brief 主语义为锚（北极星①）

【本批落地范围】**仅脚手架**（环境变量 / config schema / CLI / fail-safe 退化测试）。
真正的 gen-model 评分链路 + build_manifest 接线留下一批（需联调 gen_model_loader 与
manifest 候选段抽取）。

用法（脚手架·当前仅支持 noop pass-through 与 status 探针）：
  python memory_retrieval_gate.py --status
  python memory_retrieval_gate.py --filter <candidates.json> --scene-anchor <scene_summary>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_MODE_ENV = "MEMORY_RETRIEVAL_GATE_MODE"
_TOPK_ENV = "MEMORY_RETRIEVAL_GATE_TOPK"
_BUDGET_ENV = "MEMORY_RETRIEVAL_GATE_BUDGET_TOKENS"

_DEFAULT_TOPK = 6
_DEFAULT_BUDGET = 50000   # token 软上限·超阈值才考虑挂载（北极星⑥：不过度复杂）

_VALID_MODES = ("off", "shadow", "active")


def get_mode() -> str:
    """env: off(默认·零挂载) / shadow(评分但不应用) / active(评分+top-k 应用)。"""
    m = (os.environ.get(_MODE_ENV) or "off").strip().lower()
    return m if m in _VALID_MODES else "off"


def get_topk() -> int:
    try:
        v = int(os.environ.get(_TOPK_ENV) or _DEFAULT_TOPK)
        return max(1, min(64, v))
    except ValueError:
        return _DEFAULT_TOPK


def get_budget_tokens() -> int:
    try:
        v = int(os.environ.get(_BUDGET_ENV) or _DEFAULT_BUDGET)
        return max(1000, v)
    except ValueError:
        return _DEFAULT_BUDGET


@dataclass
class GateDecision:
    """gate 决策对象（advisory · 调用方按 mode 决定是否应用）。"""
    mode: str
    applied: bool
    reason: str
    kept_candidates: list = field(default_factory=list)
    dropped_candidates: list = field(default_factory=list)
    fallback_to_static: bool = False

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "applied": self.applied, "reason": self.reason,
            "kept": [c.get("id") if isinstance(c, dict) else c for c in self.kept_candidates],
            "dropped": [c.get("id") if isinstance(c, dict) else c for c in self.dropped_candidates],
            "fallback_to_static": self.fallback_to_static,
            "topk": get_topk(), "budget_tokens": get_budget_tokens(),
        }


def estimate_tokens(text: str) -> int:
    """粗估 token 数（中文 ≈ 1 字 1 token·英文 4 char ≈ 1 token）·脚手架够用。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    ascii_chars = sum(1 for ch in text if ch.isascii() and not ch.isspace())
    return cjk + ascii_chars // 4


def filter_candidates(candidates: list,
                      scene_anchor: str | None = None,
                      *,
                      topk: int | None = None,
                      budget_tokens: int | None = None,
                      _score_fn=None) -> GateDecision:
    """对候选段过滤·返回 GateDecision。

    脚手架版本：
      · off → fallback_to_static=True · applied=False · 全保留
      · shadow → 调 _score_fn 但不改主路径 · applied=False · 全保留 · kept 记得分
      · active → 调 _score_fn · 按 budget+topk 应用 · 失败降级 fallback_to_static
    """
    mode = get_mode()
    tk = topk if topk is not None else get_topk()
    budget = budget_tokens if budget_tokens is not None else get_budget_tokens()

    if not isinstance(candidates, list) or not candidates:
        return GateDecision(mode=mode, applied=False, reason="no_candidates",
                            fallback_to_static=True)

    if mode == "off":
        return GateDecision(mode=mode, applied=False, reason="mode_off",
                            kept_candidates=list(candidates), fallback_to_static=True)

    total_tokens = sum(estimate_tokens(c.get("text", "") if isinstance(c, dict) else str(c))
                       for c in candidates)
    if total_tokens <= budget:
        return GateDecision(mode=mode, applied=False, reason="under_budget",
                            kept_candidates=list(candidates), fallback_to_static=True)

    # 评分（脚手架）：默认 _score_fn=None → 按 candidates 原序当作 advisory 排名（保守 fallback）
    try:
        if _score_fn is not None:
            scored = list(_score_fn(candidates, scene_anchor or ""))
        else:
            scored = [(c, 0.0) for c in candidates]
    except Exception as e:    # noqa: BLE001
        return GateDecision(mode=mode, applied=False,
                            reason=f"score_fn_failed:{type(e).__name__}",
                            kept_candidates=list(candidates), fallback_to_static=True)

    # 排序：分数从高到低（同分保持原序·稳定排序）
    indexed = [(i, c, s) for i, (c, s) in enumerate(scored)]
    indexed.sort(key=lambda t: (-t[2], t[0]))
    kept = [c for _, c, _ in indexed[:tk]]
    dropped = [c for _, c, _ in indexed[tk:]]

    if mode == "shadow":
        return GateDecision(mode=mode, applied=False,
                            reason="shadow_no_apply",
                            kept_candidates=list(candidates),
                            dropped_candidates=[],
                            fallback_to_static=True)

    # active：真应用 top-k
    return GateDecision(mode=mode, applied=True,
                        reason="topk_applied",
                        kept_candidates=kept,
                        dropped_candidates=dropped,
                        fallback_to_static=False)


def status() -> dict:
    """探针：返回当前 gate 配置（默认 off·零回归）。"""
    return {
        "mode": get_mode(),
        "topk": get_topk(),
        "budget_tokens": get_budget_tokens(),
        "env": {
            _MODE_ENV: os.environ.get(_MODE_ENV) or "(unset · default=off)",
            _TOPK_ENV: os.environ.get(_TOPK_ENV) or f"(unset · default={_DEFAULT_TOPK})",
            _BUDGET_ENV: os.environ.get(_BUDGET_ENV) or f"(unset · default={_DEFAULT_BUDGET})",
        },
        "_scaffold_note": ("R7 W2 Batch-E 脚手架·gen-model 评分链路 + build_manifest 接线"
                           "留下一批·当前 off=零回归"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="memory retrieval gate (脚手架·R7 W2 Batch-E)")
    ap.add_argument("--status", action="store_true", help="显示当前 gate 配置")
    ap.add_argument("--filter", help="候选段 JSON 文件路径（list of {id, text}）")
    ap.add_argument("--scene-anchor", default="", help="当前 scene 主语义锚（评分输入）")
    args = ap.parse_args(argv)

    if args.status or not args.filter:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0

    try:
        candidates = json.loads(Path(args.filter).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[error] 候选段读取失败: {e}", file=sys.stderr)
        return 2
    if not isinstance(candidates, list):
        print(f"[error] 候选段须为 list, got {type(candidates).__name__}", file=sys.stderr)
        return 2
    decision = filter_candidates(candidates, args.scene_anchor)
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
