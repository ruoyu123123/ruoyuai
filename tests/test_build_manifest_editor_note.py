# -*- coding: utf-8 -*-
"""A4 编辑手记回归锁（2026-07-08·二轮移植 A 批 Wave2-M2）。

业界源 PlotPilot context_budget_allocator.py:475-489：「一段自然语言比 8 个 === 分隔符
更容易被 LLM 融入创作」。

  · build_manifest._build_editor_note：把已组装 manifest 的结构块（foreshadowing_summary /
    open_dramatic_questions / protagonist_stress / main_character_arc_stage /
    world_state_snapshot / event_cluster_context.volume_convergence_anchor）确定性模板拼装
    成一段 200-400 字人话手记（软措辞「不必强求」恒在场·advisory）→ manifest["editor_note"]
    （T1 创作载荷）。素材全空 / 拼装异常 → 键不注入（零变化）。零 LLM·同输入同字节。
  · 🔴 双视图纪律：原始结构块**全部保留**（scanner/审计仍消费结构化数据），手记只是
    writer 的人话视图——_build_editor_note 只读不写 manifest。
  · gen_writer._build_editor_note_section：manifest 有 editor_note 时注入手记正文 +
    消费指令（软建议汇总·可自由取舍）；缺失 → ""（零回归）。
"""
import copy
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402
import gen_writer as gw  # noqa: E402
import manifest_budget as mb  # noqa: E402


# ============ 夹具：直接构造已组装 manifest（_build_editor_note 是其纯函数） ============

def _manifest_all_sources() -> dict:
    """六素材源全在场（各字段 schema 与真实 collector 产出一致）。"""
    return {
        "generated_at": "2026-07-08T00:00:00",
        "foreshadowing_summary": {
            "tier1_due_count": 2, "tier2_due_count": 0, "deadlines_due": 1,
            "active_pledges": 0, "hidden_secrets": 3, "pending_secret_count": 3,
            "must_reveal_this_ch": 1,
        },
        "open_dramatic_questions": {
            "gate_level": "advisory", "open_count": 3,
            "open_questions": [{"qid": "q1", "question": "灯下埋的是谁"}],
        },
        "protagonist_stress": {
            "mode": "on", "is_high_stress": True,
            "stress_level": 82, "stress_threshold_break": 100,
        },
        "main_character_arc_stage": {
            "mode": "on",
            "main_characters": [{"character": "沈徊", "current_stage_name": "拒绝召唤",
                                 "stage_description": "想回码头做挑夫"}],
        },
        "world_state_snapshot": {
            "mode": "fluid",
            "ripple_narrative_consequences": [
                {"ch": 1, "text": "旧账", "reason": ""},
                {"ch": 3, "text": "行会开始悬赏水手", "reason": "涟漪"},
            ],
        },
        "event_cluster_context": {
            "mode": "on",
            "volume_convergence_anchor": {"core_conflict": "拿回船契",
                                          "volume_arc": "从挑夫到船主"},
        },
    }


_ALL_SOURCE_NAMES = ["foreshadowing_summary", "open_dramatic_questions",
                     "protagonist_stress", "main_character_arc_stage",
                     "world_state_snapshot", "volume_convergence_anchor"]


# ============ 拼装正确性：各素材源在场/缺席组合 ============

def test_full_materials_assembly():
    """六源全在场：手记含各源人话片段·sources 记满且有序·涟漪取最新一条。"""
    out = bm._build_editor_note(_manifest_all_sources())
    assert isinstance(out, dict)
    note = out["note"]
    assert "2 处伏笔" in note and "1 个秘密" in note and "1 条期限" in note
    assert "灯下埋的是谁" in note                      # 悬置戏剧问题首问
    assert "承压不轻" in note and "82/100" in note     # 主角高压（含数值）
    assert "拒绝召唤" in note and "沈徊" in note        # 弧线阶段
    assert "行会开始悬赏水手" in note                   # 涟漪后果=最新一条
    assert "旧账" not in note                          # 非最新涟漪不进手记
    assert "拿回船契" in note                          # 卷收敛锚 core_conflict 优先
    assert out["sources"] == _ALL_SOURCE_NAMES
    assert out["gate_level"] == "advisory"
    assert out["note_chars"] == len(note)


def test_partial_materials_only_present_sources():
    """缺席组合：只有主角高压在场 → 手记只谈压力·sources 只记该源。"""
    m = {"protagonist_stress": {"mode": "on", "is_high_stress": True,
                                "stress_level": 82, "stress_threshold_break": 100}}
    out = bm._build_editor_note(m)
    assert out is not None
    assert out["sources"] == ["protagonist_stress"]
    assert "承压不轻" in out["note"]
    assert "伏笔" not in out["note"] and "大方向" not in out["note"]


def test_source_gates_off_error_zero_not_collected():
    """各源门槛：off/error 模式、零计数、非高压、anchor 缺方向字段 → 均不产素材句。"""
    m = {
        "foreshadowing_summary": {"tier1_due_count": 0, "must_reveal_this_ch": 0,
                                  "deadlines_due": 0},
        "open_dramatic_questions": None,
        "protagonist_stress": {"mode": "on", "is_high_stress": False, "stress_level": 10},
        "main_character_arc_stage": {"mode": "off"},
        "world_state_snapshot": {"mode": "error", "error": "x"},
        "event_cluster_context": {"mode": "on",
                                  "volume_convergence_anchor": {"key_milestones": ["m1"]}},
    }
    assert bm._build_editor_note(m) is None


def test_empty_and_malformed_manifest_returns_none_no_crash():
    """素材全空 → None（键不注入零变化）；结构块类型畸形 → 不崩溃不注入。"""
    assert bm._build_editor_note({}) is None
    malformed = {
        "foreshadowing_summary": "bad", "open_dramatic_questions": 123,
        "protagonist_stress": ["x"], "main_character_arc_stage": {"mode": "on",
                                                                  "main_characters": "bad"},
        "world_state_snapshot": {"mode": "fluid", "ripple_narrative_consequences": "bad"},
        "event_cluster_context": {"volume_convergence_anchor": "bad"},
    }
    assert bm._build_editor_note(malformed) is None


# ============ 软措辞 + 长度带 ============

def test_soft_wording_always_present():
    """A4 灵魂措辞：「不必强求」软措辞恒在场（全量与单源两形态都要有）。"""
    full = bm._build_editor_note(_manifest_all_sources())
    assert "不必强求" in full["note"]
    sparse = bm._build_editor_note(
        {"open_dramatic_questions": {"open_count": 1,
                                     "open_questions": [{"question": "他为何回头"}]}})
    assert "不必强求" in sparse["note"]


def test_length_band_sparse_padded_to_min():
    """单源素材 → 依序补软措辞收尾句抵下限：200 <= 手记 <= 400。"""
    out = bm._build_editor_note(
        {"protagonist_stress": {"mode": "on", "is_high_stress": True}})
    assert 200 <= out["note_chars"] <= 400
    assert 200 <= len(out["note"]) <= 400


def test_length_band_overlong_trimmed_out_of_band():
    """带外裁剪：六源全给超长素材 → 从尾部整句裁到 <=400·首素材句保留。"""
    m = _manifest_all_sources()
    m["open_dramatic_questions"]["open_questions"][0]["question"] = "他" * 80
    m["world_state_snapshot"]["ripple_narrative_consequences"][-1]["text"] = "灾" * 90
    m["event_cluster_context"]["volume_convergence_anchor"]["core_conflict"] = "契" * 70
    m["main_character_arc_stage"]["main_characters"][0]["stage_description"] = "挑" * 60
    out = bm._build_editor_note(m)
    assert 200 <= out["note_chars"] <= 400
    assert "2 处伏笔" in out["note"]                       # 首素材句（伏笔）保留
    assert out["sources"][0] == "foreshadowing_summary"
    assert out["sources"] == _ALL_SOURCE_NAMES[:len(out["sources"])]  # 尾裁=前缀序不乱


def test_deterministic_same_input_same_bytes():
    """确定性：同输入两次拼装逐字节相同（零 LLM 纯模板·无时间/随机源）。"""
    a = bm._build_editor_note(copy.deepcopy(_manifest_all_sources()))
    b = bm._build_editor_note(copy.deepcopy(_manifest_all_sources()))
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == \
           json.dumps(b, ensure_ascii=False, sort_keys=True)


# ============ 双视图纪律：结构块保留 + 只读 ============

def test_structural_blocks_not_mutated():
    """_build_editor_note 只读不写：调用前后 manifest 逐字节不变（人话视图不吃结构块）。"""
    m = _manifest_all_sources()
    before = copy.deepcopy(m)
    bm._build_editor_note(m)
    assert m == before


def test_structural_blocks_preserved_and_wired_in_source():
    """结构块保留锁 + 接线锁：manifest 组装源码里 6 个结构块注入原样在场，
    editor_note 为条件附加键（素材空不注入）且在 apply_budget 前挂上。"""
    src = (_ROOT / "core" / "scripts" / "build_manifest.py").read_text(encoding="utf-8")
    for structural in ('"foreshadowing_summary": foreshadow_summary',
                       '"open_dramatic_questions": _collect_open_dramatic_questions',
                       '"protagonist_stress": _collect_protagonist_stress',
                       '"main_character_arc_stage": _collect_main_character_arc_stage',
                       '"world_state_snapshot": _collect_world_state_snapshot',
                       '"volume_convergence_anchor": _soften_convergence_anchor'):
        assert structural in src, f"双视图纪律破坏：结构块 {structural} 从组装中消失"
    assert 'manifest["editor_note"] = _editor_note' in src
    assert "_editor_note = _build_editor_note(manifest)" in src
    assert src.index('manifest["editor_note"]') < src.index(
        "return manifest_budget.apply_budget(manifest)")


def test_tier_registered_t1():
    """S1 预算契约：editor_note 归 T1 创作载荷（漏归 → test_manifest_budget 覆盖测试测红）。"""
    assert mb.SECTION_TIERS.get("editor_note") == mb.TIER_T1


# ============ gen_writer 消费指令 ============

def test_gen_writer_section_present_with_editor_note():
    """manifest 有 editor_note → 段含手记正文 + 消费指令（软建议汇总·可自由取舍·advisory）。"""
    note = bm._build_editor_note(_manifest_all_sources())
    sec = gw._build_editor_note_section(Path("Z:/不存在/ch_001.json"),
                                        {"editor_note": note})
    assert "编辑手记" in sec
    assert "软建议汇总" in sec and "可自由取舍" in sec   # 消费指令措辞
    assert "advisory" in sec                            # 北极星⑤
    assert note["note"] in sec                          # 手记正文全量注入


def test_gen_writer_section_absent_without_editor_note():
    """键缺席（旧 manifest / 素材空）/ note 空 / 类型畸形 → ""（不注入·零回归）。"""
    p = Path("Z:/不存在.json")
    assert gw._build_editor_note_section(p, {}) == ""
    assert gw._build_editor_note_section(p, {"editor_note": {"note": "  "}}) == ""
    assert gw._build_editor_note_section(p, {"editor_note": "bad"}) == ""


def test_gen_writer_wired_into_both_prompt_branches():
    """接线锁：指令随段出现——editor_note 段挂进 build_prompt 两个 join 分支
    （ctx reorder active + off/shadow 回退），builder 吃预载 manifest。"""
    src = (_ROOT / "core" / "scripts" / "gen_writer.py").read_text(encoding="utf-8")
    assert "_build_editor_note_section(manifest_path, _manifest_dict)" in src
    assert src.count("{editor_note_block}") == 2
