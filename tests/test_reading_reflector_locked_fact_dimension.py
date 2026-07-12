# -*- coding: utf-8 -*-
"""reading-reflector 维度 9「锁定事实语义一致性」合约回归锁。

locked_fact 描述类 NLI 通路（Erlangshen-110M）只能稳判直接改写型矛盾，多跳实体推理型
（锁定「满门尽灭只剩沈昭一人」vs 正文「兄长沈铖推门而入」）contradiction 仅 0.166 恒漏
——模型能力边界，降阈值只会换来误报洪水。多跳核查因此作为职责写进 cluster-write step3
每 cluster 必跑的 Claude 系 judge（novel-reading-reflector）合约=维度 9。
本测试锁死该合约三层互补口径，防文档漂移复活盲区。

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
    """合约必须含维度 9：锁定事实语义一致性（多跳推理），且 10 维口径一致。"""
    src = _read(_REFLECTOR_MD)
    assert "锁定事实语义一致性" in src
    assert "10 大检测维度" in src
    assert "未跑满 10 维" in src
    # 8/9 维旧口径不得残留（防双口径误导新会话）
    assert "8 大检测维度" not in src
    assert "未跑满 8 维" not in src
    assert "9 大检测维度" not in src
    assert "未跑满 9 维" not in src


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


def test_cluster_write_doc_synced_to_10_dims():
    """命令文档 step3 描述与合约同口径（10 维含锁定事实语义冲突 + 悬置线推进性）。"""
    src = _read(_CLUSTER_WRITE_MD)
    assert "reflector 10 维" in src
    assert "锁定事实语义冲突" in src
    assert "悬置线推进性" in src
    assert "reflector 8 维" not in src
    assert "reflector 9 维" not in src


def test_scanner_capability_boundary_names_reflector_as_multihop_owner():
    """scanner docstring 与 registry 的能力边界记载必须指向 reflector 维度 9 承接方——
    防止未来读到「110M 恒漏多跳」却找不到承接方，或误以为描述类通路全覆盖。"""
    assert "novel-reading-reflector" in _read(_SCANNER)
    assert "reading-reflector" in _read(_REGISTRY)


def test_reflector_dimension_9_surprisal_priority_hint_is_advisory():
    """高熵段优先深查提示（ConStory arXiv:2603.05890）合约锁：
      · 写明确切报告文件（cluster_<key>_audit.json 的 surprisal_scanner issue /
        _临时/probe/hotspot_<cluster_id>.json 熵探针）——不许含糊「某报告」
      · 无报告（surprisal 默认 off）→ 全量核查如常
      · 表述必须是 advisory 排查顺序提示，不改「10 维全量检查」硬性要求。"""
    src = _read(_REFLECTOR_MD)
    assert "高熵段优先深查" in src
    assert "arXiv:2603.05890" in src
    # 确切文件名（audit_hub 写 cluster_<key>_audit.json·熵探针写 hotspot_*.json）
    assert "cluster_<key>_audit.json" in src
    assert "surprisal_scanner" in src
    assert "hotspot_<cluster_id>.json" in src
    assert "entropy_hotspot_consistency_probe" in src
    # 诚实降级口径 + advisory 边界（不动硬性要求）
    assert "全量核查如常" in src
    assert "不改变「10 维全量检查」" in src
    assert "不改变「9 维全量检查」" not in src


# ── 维度 10「悬置线推进性」合约锁（AI_NovelGenerator consistency_checker 推进性思想）──


def test_reflector_dimension_10_neglected_thread_present_in_enum_and_body():
    """维度 10 在场：schema dimension 枚举含「悬置线推进性」，且正文有编号章节。"""
    src = _read(_REFLECTOR_MD)
    assert "悬置线推进性" in src
    assert "### 10. 悬置线推进性" in src
    # 枚举行同步（防只加章节不加枚举的半截合约）
    assert "锁定事实语义冲突 | 悬置线推进性" in src


def test_reflector_dimension_10_data_sources_use_real_field_names():
    """数据源实名：subplot_threads.json 与 大势卡.json 的字段名必须实名不臆造
    （subplot_progress_update.py 维护 threads[].last_cluster/status；
    大势卡 major_events[].volume/status/prerequisites；事件簇 cluster 带 parent_me）。"""
    src = _read(_REFLECTOR_MD)
    assert "subplot_threads.json" in src
    assert "大势卡.json" in src
    assert "last_cluster" in src
    assert "major_events" in src
    assert "parent_me" in src
    assert "ME_to_advance" in src
    assert "subplot_progress_update" in src  # 与机械层分工必须点名，防双报


def test_reflector_dimension_10_is_waivable_pacing_hint_not_error():
    """北极星⑤口径：维度 10 是推进性提示（节奏问题非错误·慢热铺陈合法·writer 可豁免·
    severity 默认 low·绝不 hard_gate）。"""
    src = _read(_REFLECTOR_MD)
    assert "节奏问题不是错误" in src
    assert "慢热铺陈" in src
    assert "推进性提示" in src
    assert "severity 默认 low" in src
    assert "绝不升 hard_gate" in src


def test_reflector_dimension_10_degrade_path_when_last_touched_missing():
    """last_cluster 缺失时的诚实退化口径必须存在：退化为「本块零提及且 status==active」，
    不得臆造 gap；账本文件缺失 → 记 skip 原因不得假装核查过。"""
    src = _read(_REFLECTOR_MD)
    assert "退化口径" in src
    assert "零提及" in src
    assert "维度 10 记 skip 原因" in src


# ── 维度 8「塑料感」StoryScope 结构层 AI tell 子清单合约锁 ──────────────────────
# （StoryScope arXiv:2604.03136·304 叙事结构特征纯结构 F1=93.2%。真作者金标准基线
#   ——惊悚乐园/遮天/人生长恨水长东各 2-3 章亲读判读——证伪宽口径「主题直给/出现
#   脸谱化反派」（辰东卷首格言宣讲是作者签名、爽文欺凌龙套单义是体裁常态），
#   只收 4 条窄口径。
#   本组测试锁死：不加新维度（仍 10 维）+ 4 条窄口径 bullet 在场 + 弃用口径留痕 +
#   让位口径（体裁常态/作者档第一权威）在场 + 递进扁平归维度 5 防双报。）


def test_reflector_dimension_8_storyscope_sublist_present_no_new_dimension():
    """StoryScope 子清单必须挂在维度 8（塑料感）下，不得新增第 11 维。"""
    src = _read(_REFLECTOR_MD)
    assert "StoryScope" in src
    assert "arXiv:2604.03136" in src
    assert "### 8. 塑料感" in src
    # 10 维口径不变——严禁借 A8 扩维
    assert "### 11." not in src
    assert "11 大检测维度" not in src
    assert "10 大检测维度" in src


def test_reflector_dimension_8_four_narrow_tells_present():
    """基线证实「真作者低、AI 高」的 4 条窄口径必须逐条在场。"""
    src = _read(_REFLECTOR_MD)
    assert "场景末主题明示宣讲" in src
    assert "关键人物道德全员单义化" in src
    assert "结局收束过净" in src
    assert "零时间复杂度" in src


def test_reflector_dimension_8_wide_features_discarded_with_reason():
    """基线证伪的宽口径必须留弃用痕迹（防未来复活「主题直给=AI」的矫枉过正）：
    真网文宽口径天然偏高——卷首格言宣讲是作者签名、爽文龙套单义是体裁常态。"""
    src = _read(_REFLECTOR_MD)
    assert "弃用" in src
    assert "金标准基线" in src
    # 窄化口径的关键限定词：不是「有宣讲就报」「有脸谱反派就报」
    assert "卷首格言" in src
    assert "不算命中" in src
    assert "全员" in src


def test_reflector_dimension_8_yields_to_author_profile_and_genre_norm():
    """让位口径必须在场：体裁常态/作者档第一权威优先——该作者显著风格自动让位不报；
    命中也是 advisory 待裁决项 writer 可豁免（北极星⑤不干涉创作判断）。"""
    src = _read(_REFLECTOR_MD)
    assert "体裁常态/作者档第一权威优先" in src
    assert "显著风格" in src
    assert "让位不报" in src
    assert "writer 可豁免" in src


def test_reflector_dimension_8_flat_escalation_routed_to_dimension_5():
    """事件递进扁平（StoryScope 特征之一）归维度 5 节奏感既有职责，维度 8 明示防双报。"""
    src = _read(_REFLECTOR_MD)
    assert "事件递进扁平" in src
    assert "归维度 5 节奏感" in src
    assert "别双报" in src


def test_cluster_write_doc_synced_dimension_8_storyscope_marker():
    """命令文档维度行与合约同口径：塑料感注明含 StoryScope 结构层 AI tell 子清单 + 让位口径。"""
    src = _read(_CLUSTER_WRITE_MD)
    assert "StoryScope 结构层 AI tell 子清单" in src
    assert "体裁常态与作者档优先可让位" in src
