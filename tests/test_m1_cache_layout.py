"""M1 cache 铁律守卫测试（2026-06-15·确定性·前瞻约束）。

记忆调研 M1：B1 importance 重排会破坏 prompt cache prefix（裸 reorder 动 STATIC/SEMI 段 →
cache miss → 成本反升）。补盘核实 B1 重排落点（selective_history/pending_secrets/hard_constraints）
本就在 DYNAMIC_30 段。本测试锁定这个契约：importance 重排涉及的字段必须在 DYNAMIC，不得外溢
STATIC/SEMI（保 prompt cache 60-90% 节省·前瞻防未来 B1 接入 build_manifest 重排时破 cache）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402


def _layout():
    return bm._build_cache_layout()


def test_b1_rerank_fields_in_dynamic():
    """M1 铁律：B1 importance 重排涉及字段必须在 DYNAMIC_30（不外溢 STATIC/SEMI 破 cache prefix）。"""
    layout = _layout()
    dynamic = set(layout["DYNAMIC_30_cacheable"])
    static = set(layout["STATIC_99_cacheable"])
    semi90 = set(layout.get("SEMI_STATIC_90_cacheable_v22", []))
    semi70 = set(layout.get("SEMI_STATIC_70_cacheable", []))
    # 补盘核实的 B1 importance 重排/检索落点（含 last_seen/recurrence/检索排序·每章变）
    rerank_fields = ["selective_history_retrieval", "pending_secrets_to_reveal", "hard_constraints"]
    for f in rerank_fields:
        assert f in dynamic, f"M1 违反：{f} 应在 DYNAMIC_30（importance 重排不得外溢破 cache）"
        assert f not in static, f"M1 违反：{f} 误入 STATIC（重排会破 99% cache prefix）"
        assert f not in semi90 and f not in semi70, f"M1 违反：{f} 误入 SEMI（重排会破 cache）"


def test_cache_tiers_no_overlap():
    """cache 分层互斥（同字段不在多层·否则 prefix 顺序歧义破 cache）。"""
    layout = _layout()
    static = set(layout["STATIC_99_cacheable"])
    semi90 = set(layout.get("SEMI_STATIC_90_cacheable_v22", []))
    semi70 = set(layout.get("SEMI_STATIC_70_cacheable", []))
    dynamic = set(layout["DYNAMIC_30_cacheable"])
    assert not (static & dynamic), "STATIC 与 DYNAMIC 不得重叠"
    assert not (static & semi70), "STATIC 与 SEMI70 不得重叠"
    assert not (semi70 & dynamic), "SEMI70 与 DYNAMIC 不得重叠"


def test_static_fields_are_stable_distill():
    """STATIC 段应是全书不变的蒸馏/作者风格字段（cache 99% 前提·B1 绝不重排它们）。"""
    static = set(_layout()["STATIC_99_cacheable"])
    for f in ["author_rhythm_signature", "author_decision_principles", "deep_writing_dims"]:
        assert f in static, f"{f} 应在 STATIC（全书不变·重排才真破 cache）"
