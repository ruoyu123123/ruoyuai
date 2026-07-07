# -*- coding: utf-8 -*-
"""reading-reflector 维度 9「锁定事实语义一致性」合约回归锁（2026-07-07）。

背景：locked_fact 描述类 NLI 通路真机实测（Erlangshen-110M）只能稳判直接改写型矛盾，
多跳实体推理型（锁定「满门尽灭只剩沈昭一人」vs 正文「兄长沈铖推门而入」）contradiction
仅 0.166 恒漏——模型能力边界。补齐方案不是降阈值（误报洪水）也不是换更大模型（成本），
而是把多跳核查作为职责写进 cluster-write step3 每 cluster 必跑的 Claude 系 judge
（novel-reading-reflector）合约=维度 9。本测试锁死该合约三层互补口径，防文档漂移复活盲区。

三层互补分工（勿破坏）：
  数值确定性冲突   → locked_fact_cross_scene_scanner 数值通路（hard_gate）
  直接改写型矛盾   → 同 scanner 描述类 NLI 通路（advisory·LOCKED_FACT_DESCRIPTIVE_MODE）
  多跳实体推理型   → reading-reflector 维度 9（advisory 待裁决项·刻意伏笔可豁免）
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REFLECTOR_MD = _ROOT / ".claude" / "agents" / "novel-reading-reflector.md"
_CLUSTER_WRITE_MD = _ROOT / ".claude" / "commands" / "cluster-write.md"
_SCANNER = _ROOT / "core" / "scripts" / "locked_fact_cross_scene_scanner.py"
_REGISTRY = _ROOT / "core" / "scripts" / "scanner_registry.json"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_reflector_contract_has_dimension_9_locked_fact_semantic():
    """合约必须含维度 9：锁定事实语义一致性（多跳推理），且 9 维口径一致。"""
    src = _read(_REFLECTOR_MD)
    assert "锁定事实语义一致性" in src
    assert "9 大检测维度" in src
    assert "未跑满 9 维" in src
    # 8 维旧口径不得残留（防双口径误导新会话）
    assert "8 大检测维度" not in src
    assert "未跑满 8 维" not in src


def test_reflector_dimension_enum_includes_locked_fact_conflict():
    """输出 schema 的 dimension 枚举必须含「锁定事实语义冲突」。"""
    src = _read(_REFLECTOR_MD)
    assert "锁定事实语义冲突" in src


def test_reflector_duty_spells_out_multihop_and_advisory_discipline():
    """维度 9 职责必须写明：多跳推理定位 + locked_facts 必读 + 待裁决项非判决（刻意伏笔可豁免）。"""
    src = _read(_REFLECTOR_MD)
    assert "多跳" in src
    assert "locked_facts" in src
    assert "待裁决项" in src
    assert "伏笔" in src  # 刻意设计（假死/冒名）豁免路径必须存在——北极星⑤不干涉创作判断


def test_cluster_write_doc_synced_to_9_dims():
    """命令文档 step3 描述与合约同口径（9 维含锁定事实语义冲突）。"""
    src = _read(_CLUSTER_WRITE_MD)
    assert "reflector 9 维" in src
    assert "锁定事实语义冲突" in src
    assert "reflector 8 维" not in src


def test_scanner_capability_boundary_names_reflector_as_multihop_owner():
    """scanner docstring 与 registry 的能力边界记载必须指向 reflector 维度 9 承接方——
    防止未来读到「110M 恒漏多跳」却找不到承接方，或误以为描述类通路全覆盖。"""
    assert "novel-reading-reflector" in _read(_SCANNER)
    assert "reading-reflector" in _read(_REGISTRY)


def test_reflector_dimension_9_surprisal_priority_hint_is_advisory():
    """S4 高熵段优先深查提示（2026-07-07 二轮移植·ConStory arXiv:2603.05890）合约锁：
      · 写明确切报告文件（cluster_<key>_audit.json 的 surprisal_scanner issue /
        _临时/probe/hotspot_<cluster_id>.json 熵探针）——不许含糊「某报告」
      · 无报告（surprisal 默认 off）→ 全量核查如常
      · 表述必须是 advisory 排查顺序提示，不改「9 维全量检查」硬性要求。"""
    src = _read(_REFLECTOR_MD)
    assert "高熵段优先深查" in src
    assert "arXiv:2603.05890" in src
    # 确切文件名（摸底实证：audit_hub 写 cluster_<key>_audit.json·熵探针写 hotspot_*.json）
    assert "cluster_<key>_audit.json" in src
    assert "surprisal_scanner" in src
    assert "hotspot_<cluster_id>.json" in src
    assert "entropy_hotspot_consistency_probe" in src
    # 诚实降级口径 + advisory 边界（不动硬性要求）
    assert "全量核查如常" in src
    assert "不改变「9 维全量检查」" in src
