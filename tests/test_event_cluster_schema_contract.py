# -*- coding: utf-8 -*-
"""event_cluster_schema clusters[] 声明对账回归锁。

clusters[].items 是 strict（additionalProperties:false）——真实链路读/写的字段若漏声明，
schema 与数据现实脱节（休眠炸弹：任何按 schema 做的校验/生成都会拒真机数据）。

锁两类字段必须声明：
  ① build_manifest 从 clusters[] 消费的全部字段（event_cluster_context / current_scene /
     _build_volume_convergence_anchor / _collect_author_signature_slots 等）
  ② 真实链路写回 clusters[] 的字段（apply_archive 的 locked_facts/throughline_progress、
     splitter 输出层回填的 chapter_range、outline-planner 的 brief 元数据）

另锁北极星⑤：narrative_pov_mode 等 advisory 工艺字段不得设 enum 硬锁。
"""
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "core" / "claude-home" / "schemas" / "event_cluster_schema.json"


def _cluster_props() -> dict:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    return schema["properties"]["clusters"]["items"]["properties"]


# ① build_manifest 从 clusters[] 项读的字段（grep c.get(...) / cluster.get(...) 对账 2026-07）
_MANIFEST_READ_FIELDS = (
    "cluster_id", "parent_me", "scope_summary", "scenes_estimated", "status",
    "research_ref", "narrative_mode", "narrative_pov_mode", "climax_hint_scene_index",
    "scene_storyboard", "anchor_props", "olfactory_anchors",
    "foreshadowing_to_plant", "foreshadowing_to_callback", "ME_to_advance",
    "throughline_focus", "characters_focus", "hub_locations",
    "chapter_range", "title", "vol", "volume", "is_volume_finale", "stakes_delta",
    "belief_update_intent", "event_boundary_sharpness", "intended_burst_type",
    "author_signature_slots",
)

# ② 真实链路写回 clusters[] 的字段（producer 见各字段 description）
_CHAIN_WRITTEN_FIELDS = (
    "locked_facts", "throughline_progress", "source_contract",
    "user_choices_required", "detailed_cluster", "in_medias_res_note",
    "mode", "next_step", "_schema", "draft_path", "output_segments",
    "gate_waivers", "spawned_at", "completed_at",
)

# 北极星⑤：advisory 工艺字段不硬锁取值（enum 只留给真格式契约如 status/narrative_mode）
_ADVISORY_NO_ENUM_FIELDS = (
    "narrative_pov_mode", "belief_update_intent",
    "event_boundary_sharpness", "intended_burst_type", "stakes_delta",
)


def test_all_manifest_read_fields_declared():
    """build_manifest 消费的每个 cluster 字段都必须在 strict schema 里声明。"""
    props = _cluster_props()
    missing = [f for f in _MANIFEST_READ_FIELDS if f not in props]
    assert not missing, (
        f"event_cluster_schema clusters[].properties 漏声明 build_manifest 消费字段：{missing}\n"
        "strict(additionalProperties:false) 下漏声明 = schema 拒真机数据（休眠炸弹）")


def test_all_chain_written_fields_declared():
    """apply_archive/splitter/outline-planner 写回的字段都必须在 strict schema 里声明。"""
    props = _cluster_props()
    missing = [f for f in _CHAIN_WRITTEN_FIELDS if f not in props]
    assert not missing, (
        f"event_cluster_schema clusters[].properties 漏声明真实链路写回字段：{missing}")


def test_advisory_craft_fields_have_no_enum_hard_lock():
    """北极星⑤：advisory 工艺字段（POV 分类/爆点类/边界锐度等）不得设 enum 硬锁。"""
    props = _cluster_props()
    offenders = [f for f in _ADVISORY_NO_ENUM_FIELDS
                 if f in props and "enum" in props[f]]
    assert not offenders, (
        f"advisory 工艺字段被 enum 硬锁（干涉模型创作判断·北极星⑤）：{offenders}\n"
        "常见取值写进 description 供参考，不进 enum")


def test_clusters_items_remain_strict():
    """对账修法 = 补声明，不是放开 additionalProperties（strict 契约保留）。"""
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    items = schema["properties"]["clusters"]["items"]
    assert items.get("additionalProperties") is False, (
        "clusters[].items 必须保持 additionalProperties:false——漏字段走补声明，不走放开 strict")


def test_narrative_pov_mode_description_lists_common_values():
    """narrative_pov_mode 的 description 必须给出五个常见取值（供 planner/writer 参考）。"""
    desc = _cluster_props()["narrative_pov_mode"].get("description", "")
    for v in ("first_present", "first_retro_consonant", "first_retro_dissonant",
              "third_limited", "third_omniscient"):
        assert v in desc, f"narrative_pov_mode description 缺常见取值 {v}"


if __name__ == "__main__":
    import sys
    fails = 0
    for nm in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[nm]()
            print(f"  [OK] {nm}")
        except Exception as e:
            fails += 1
            print(f"  [FAIL] {nm}: {e}")
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
