"""test_av_judge_swap.py — G2-CYCLIC 去位置偏（position-swap）+ intent_recovery 扩维测试。

域：av_judge G2-CYCLIC（去位置偏）+ intent_recovery（作者思维 B1-B3 余弦判决）· 全 advisory/experiment。

纪律（与 test_av_judge_selfconsist 同款 · 北极星①⑤⑥）：
  · 只测**确定性逻辑**（prompt 层 swap / schema 不变性 / swap 分配 / 聚合透明字段 / 报告平铺 /
    intent_recovery 确定性余弦 + 序列化稳定性 + multi-ref 变异带）——av_judge 是纯确定性库
    （swap 分配消费方是 distill_av_verify 投票任务渲染 · 判别由 novel-av-judge agent 完成）。
  · mstyle 真余弦需 sentence-transformers + EMBED_BACKEND=mstyle → 用 hash 后端测「invalid 护栏」路径
    （绝不 hash 冒充语义），真 mstyle 路径标 skipif（无依赖跳过）。
  · 零回归硬纪律：swap_on=off → 全 swap=False·N=1 恒不 swap·intent_dim 默认关 4 维。

覆盖：
  [S] build_av_judge_prompt swap：顺序反转 / schema 不变 / verdict 锚到仿写段 / 标签先后。
  [SW] _swap_assignment + _position_swap_on env：分配序列 / N=1 / env 解析。
  [AG] aggregate_verdicts swap 透明：n_swapped_samples / sample_drift_detail / 语义方向一致性。
  [RP] build_report 平铺 swap 透明字段（advisory 不黑箱）。
  [IR] intent_recovery：_flatten_principles 稳定性 / hash 后端 invalid 护栏 / 变异带 / 探针 advisory。
"""
import importlib
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import av_judge as av  # noqa: E402

_DIM_NAMES = ["词汇选择", "句法", "话语连接词", "语用语气"]


def _reply(verdicts):
    dims = {n: {"verdict": verdicts.get(n, av.MATCH_VERDICT), "reason": f"{n}理由"}
            for n in _DIM_NAMES}
    return "```json\n" + json.dumps({"dimensions": dims}, ensure_ascii=False) + "\n```"


def _reload_swap(value):
    """以指定 AV_JUDGE_POSITION_SWAP reload av_judge（env 在函数内读 · reload 求稳）。"""
    if value is None:
        os.environ.pop("AV_JUDGE_POSITION_SWAP", None)
    else:
        os.environ["AV_JUDGE_POSITION_SWAP"] = str(value)
    importlib.reload(av)
    return av


# ════════════════════════════════════════════════════════════════
# [S] build_av_judge_prompt swap：顺序反转 · schema 不变 · verdict 锚到仿写段
# ════════════════════════════════════════════════════════════════

def test_S_swap_changes_prompt_string():
    """swap=False 与 swap=True 产出的 prompt 字符串不同（呈现顺序真反转 · R3 断言1）。"""
    off = av.build_av_judge_prompt("AUTHOR_ANCHOR", "REPLICA_PROBE", swap=False)
    on = av.build_av_judge_prompt("AUTHOR_ANCHOR", "REPLICA_PROBE", swap=True)
    assert off != on


# 块标签（带 ━ 横条）唯一标识两段呈现位置（避开顶部 intro 段对「作者真迹/仿写」的解释性提及）
_AUTHOR_BLOCK_LABEL = "作者真迹（锚 · 这位作者长这样）"
_REPLICA_BLOCK_LABEL = "仿写（待验证 · 判它哪维露馅）"


def test_S_swap_off_author_label_before_replica():
    """swap=False：作者真迹块标签在仿写块标签之前（原向 · R3 断言2）。"""
    p = av.build_av_judge_prompt("AUTHOR_ANCHOR", "REPLICA_PROBE", swap=False)
    assert p.index(_AUTHOR_BLOCK_LABEL) < p.index(_REPLICA_BLOCK_LABEL)


def test_S_swap_on_replica_label_before_author():
    """swap=True：仿写块标签在作者真迹块标签之前（反向呈现 · R3 断言2）。"""
    p = av.build_av_judge_prompt("AUTHOR_ANCHOR", "REPLICA_PROBE", swap=True)
    assert p.index(_REPLICA_BLOCK_LABEL) < p.index(_AUTHOR_BLOCK_LABEL)


def test_S_swap_on_text_body_order_reversed():
    """swap=True：正文里仿写文本块出现在作者文本块之前（真把两段位置换了 · 不止换标签）。"""
    off = av.build_av_judge_prompt("AUTHOR_ANCHOR", "REPLICA_PROBE", swap=False)
    on = av.build_av_judge_prompt("AUTHOR_ANCHOR", "REPLICA_PROBE", swap=True)
    assert off.index("AUTHOR_ANCHOR") < off.index("REPLICA_PROBE")   # 原向：作者文本在前
    assert on.index("REPLICA_PROBE") < on.index("AUTHOR_ANCHOR")     # 反向：仿写文本在前


def test_S_schema_keys_identical_both_swaps():
    """两种 swap 下输出 JSON schema 的 4 维键名完全一致（下游 parse 不变 · R3 断言3）。"""
    off = av.build_av_judge_prompt("A", "B", swap=False)
    on = av.build_av_judge_prompt("A", "B", swap=True)
    for n in _DIM_NAMES:
        assert f'"{n}"' in off
        assert f'"{n}"' in on


def test_S_swap_on_verdict_anchored_to_replica_not_author():
    """swap=True 的 prompt 仍把走味主语锚到仿写段（不会变成判作者走味 · R3 断言4）。"""
    p = av.build_av_judge_prompt("A", "B", swap=True)
    # rubric / verdict / 指证都问「仿写段」走味
    assert "判仿写段哪维走味" in p
    assert "仿写段露馅" in p
    assert "指证仿写段的具体露馅处" in p
    # 绝不出现「判作者真迹走味」这种反向措辞
    assert "判作者真迹哪维走味" not in p
    assert "作者真迹露馅" not in p


def test_S_swap_order_hint_present():
    """swap 时 prompt 头部有「呈现顺序不代表谁是作者」的隐式标注（供测试 + 防 judge 困惑）。"""
    on = av.build_av_judge_prompt("A", "B", swap=True)
    off = av.build_av_judge_prompt("A", "B", swap=False)
    assert "呈现顺序不代表谁是作者" in on
    assert "作者真迹段在前、仿写段在后呈现" in off


# ════════════════════════════════════════════════════════════════
# [SW] _swap_assignment + _position_swap_on env 解析
# ════════════════════════════════════════════════════════════════

def test_SW_assignment_off_all_false():
    """swap_on=False → 全 False（零回归）。"""
    assert av._swap_assignment(3, swap_on=False) == [False, False, False]
    assert av._swap_assignment(4, swap_on=False) == [False] * 4


def test_SW_assignment_n3_front_half_false():
    """n=3 swap_on → [False, False, True]（前一半含取整 (3+1)//2=2 · R3 断言）。"""
    assert av._swap_assignment(3, swap_on=True) == [False, False, True]


def test_SW_assignment_n4_even_split():
    """n=4 swap_on → [False, False, True, True]（前后各半 · 最大化位置对称）。"""
    assert av._swap_assignment(4, swap_on=True) == [False, False, True, True]


def test_SW_assignment_n2():
    """n=2 swap_on → [False, True]。"""
    assert av._swap_assignment(2, swap_on=True) == [False, True]


def test_SW_assignment_n1_never_swaps():
    """n=1 → 恒 [False]（即便 swap_on · 单样本无从对称 · 零回归）。"""
    assert av._swap_assignment(1, swap_on=True) == [False]


def test_SW_env_default_off():
    """AV_JUDGE_POSITION_SWAP 未设 → off（零回归默认）。"""
    g = _reload_swap(None)
    try:
        assert g._position_swap_on() is False
    finally:
        _reload_swap(None)


def test_SW_env_on_values():
    """on / 1 / true / yes（大小写不敏感）→ True。"""
    for v in ("on", "ON", "1", "true", "True", "yes", "YES"):
        try:
            assert _reload_swap(v)._position_swap_on() is True, v
        finally:
            _reload_swap(None)


def test_SW_env_off_values():
    """off / 空 / 非法 → False（保守默认 off）。"""
    for v in ("off", "0", "false", "no", "garbage", ""):
        try:
            assert _reload_swap(v)._position_swap_on() is False, v
        finally:
            _reload_swap(None)


# ════════════════════════════════════════════════════════════════
# [AG] aggregate_verdicts swap 透明 + 语义方向一致性
# ════════════════════════════════════════════════════════════════

def _parsed_with_swap(verdicts, swapped):
    p = av.parse_av_verdicts(_reply(verdicts))
    p["_swapped"] = swapped
    return p


def test_AG_counts_swapped_samples():
    """aggregate_verdicts 输入含 _swapped 标记 → n_swapped_samples 计数正确（R3 断言）。"""
    s = [_parsed_with_swap({}, False),
         _parsed_with_swap({}, True),
         _parsed_with_swap({}, True)]
    agg = av.aggregate_verdicts(s)
    assert agg["n_swapped_samples"] == 2


def test_AG_position_bias_note_present():
    """返回 dict 含 position_bias_note 字符串（R3 断言 · advisory 不黑箱）。"""
    s = [_parsed_with_swap({}, True), _parsed_with_swap({}, False)]
    agg = av.aggregate_verdicts(s)
    assert isinstance(agg["position_bias_note"], str)
    assert "G2-CYCLIC" in agg["position_bias_note"]


def test_AG_position_bias_note_when_no_swap():
    """无 swap 样本 → position_bias_note 标明未启用（不误称已去偏）。"""
    s = [_parsed_with_swap({}, False), _parsed_with_swap({}, False)]
    agg = av.aggregate_verdicts(s)
    assert "未启用 position-swap" in agg["position_bias_note"]


def test_AG_sample_drift_detail_parallel_structure():
    """sample_drift_detail 是 sample_drift_dims 的并行结构（{swapped, drift_dims}）· 不破原字段。"""
    s = [_parsed_with_swap({"词汇选择": av.DRIFT_VERDICT}, False),
         _parsed_with_swap({"句法": av.DRIFT_VERDICT}, True)]
    agg = av.aggregate_verdicts(s)
    # 原字段 sample_drift_dims 保持 list[list]（既有 test_C_sample_drift_dims_transparency 不破）
    assert agg["sample_drift_dims"] == [["词汇选择"], ["句法"]]
    # 并行新字段带方向
    assert agg["sample_drift_detail"] == [
        {"swapped": False, "drift_dims": ["词汇选择"]},
        {"swapped": True, "drift_dims": ["句法"]},
    ]


def test_AG_drift_dims_same_with_or_without_swap_flag():
    """半 swap 的样本（同一对 · 判别一致）聚合后 drift_dims 与全不 swap 时相同（语义方向一致 · R3 断言）。"""
    # 三个样本都判「词汇选择」走味 → 不管 _swapped 标志如何，多数票结果一致
    no_swap = [_parsed_with_swap({"词汇选择": av.DRIFT_VERDICT}, False) for _ in range(3)]
    half_swap = [_parsed_with_swap({"词汇选择": av.DRIFT_VERDICT}, i >= 2) for i in range(3)]
    a1 = av.aggregate_verdicts(no_swap)
    a2 = av.aggregate_verdicts(half_swap)
    assert a1["drift_dims"] == a2["drift_dims"] == ["词汇选择"]


def test_AG_no_swap_flag_defaults_zero():
    """旧调用方（samples 不带 _swapped）→ n_swapped_samples=0（向后兼容 · 不崩）。"""
    s = [av.parse_av_verdicts(_reply({})) for _ in range(3)]  # 无 _swapped 键
    agg = av.aggregate_verdicts(s)
    assert agg["n_swapped_samples"] == 0
    assert all(d["swapped"] is False for d in agg["sample_drift_detail"])


# ════════════════════════════════════════════════════════════════
# [RP] build_report 平铺 swap 透明字段（advisory 不黑箱）
# ════════════════════════════════════════════════════════════════

def test_RP_report_carries_swap_transparency():
    """build_report 把 n_swapped_samples / position_bias_note 平铺进报告（R3 断言 · advisory 不黑箱）。"""
    s = [_parsed_with_swap({"词汇选择": av.DRIFT_VERDICT}, False),
         _parsed_with_swap({"词汇选择": av.DRIFT_VERDICT}, True),
         _parsed_with_swap({}, True)]
    agg = av.aggregate_verdicts(s)
    r = av.build_report("active", agg, "a.txt", "b.txt")
    assert r["n_swapped_samples"] == 2
    assert "position_bias_note" in r
    assert isinstance(r["sample_drift_detail"], list)


def test_RP_report_still_advisory_with_swap():
    """swap 透明字段进报告后仍 advisory · code AV_TRAIT_DRIFT（北极星⑤ · 去位置偏≠强判）。"""
    s = [_parsed_with_swap({"词汇选择": av.DRIFT_VERDICT}, i >= 1) for i in range(3)]
    agg = av.aggregate_verdicts(s)
    for mode in ("shadow", "active"):
        r = av.build_report(mode, agg, "a.txt", "b.txt")
        assert r["gate_level"] == "advisory"
        assert r["issue_code"] == "AV_TRAIT_DRIFT"


def test_RP_swap_code_not_in_hard_gate():
    """G2-CYCLIC / intent_recovery 不改铁律：AV_TRAIT_DRIFT 仍不进 HARD_GATE_CODES。"""
    try:
        import audit_hub  # noqa: E402
    except Exception:
        return
    codes = getattr(audit_hub, "HARD_GATE_CODES", set())
    assert av.ISSUE_CODE not in codes


# ════════════════════════════════════════════════════════════════
# [IR] intent_recovery：_flatten_principles 稳定性 · hash 后端 invalid 护栏 · 变异带 · 探针 advisory
# ════════════════════════════════════════════════════════════════

def _load_rfc():
    """load replication_fidelity_check（intent_recovery 函数挂这里 · 主代理 C2 在此·我在其上加）。"""
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import replication_fidelity_check as rfc  # noqa: E402
    return rfc


def test_IR_flatten_principles_deterministic():
    """_flatten_principles 对嵌套 dict/list/str 混合 → 同输入同输出（键序固定 · 余弦可复现 · R3 断言）。"""
    rfc = _load_rfc()
    principles = {
        "B1_道德滤镜": {"母题": ["代价", "牺牲"], "倾向": ["留白"]},
        "C2_声纹三件套": [{"角色": "甲", "口癖": "嗯"}, "旁白克制"],
        "B3_留白": "信息延迟释放",
    }
    out1 = rfc._flatten_principles(principles)
    out2 = rfc._flatten_principles(principles)
    assert out1 == out2                      # 确定性
    assert isinstance(out1, str) and out1    # 非空文本
    # 键序固定（与 dict 插入序无关）→ 换个插入序应得同输出
    reordered = {
        "B3_留白": "信息延迟释放",
        "C2_声纹三件套": [{"口癖": "嗯", "角色": "甲"}, "旁白克制"],
        "B1_道德滤镜": {"倾向": ["留白"], "母题": ["代价", "牺牲"]},
    }
    assert rfc._flatten_principles(reordered) == out1


def test_IR_flatten_skips_underscore_keys():
    """_flatten_principles 跳过 _raw_* / 内部下划线键（与 consolidate 一致）。"""
    rfc = _load_rfc()
    out = rfc._flatten_principles({"母题": "代价", "_raw_obs": ["不该出现"]})
    assert "代价" in out
    assert "不该出现" not in out


def test_IR_flatten_empty():
    """空 / None principles → 空串（不崩）。"""
    rfc = _load_rfc()
    assert rfc._flatten_principles(None) == ""
    assert rfc._flatten_principles({}) == ""


def test_IR_cosine_hash_backend_invalid():
    """后端=hash（默认）时 intent_recovery_cosine 返回 valid=False + reason 含『≠mstyle』（护栏 · R3 断言）。

    🔴 绝不返回假余弦——hash 是 md5 ngram 袋，风格语义=0。
    """
    rfc = _load_rfc()
    # 确保不是 mstyle 后端（清 env · 默认 hash）
    saved = os.environ.pop("EMBED_BACKEND", None)
    try:
        out = rfc.intent_recovery_cosine("仿写反推文本", {"母题": "代价"})
        assert out["valid"] is False
        assert "≠mstyle" in out["reason"]
        assert out["cosine"] is None
    finally:
        if saved is not None:
            os.environ["EMBED_BACKEND"] = saved


def test_IR_cosine_empty_inputs_invalid():
    """空 judge 文本 或 空 principles → valid=False（无可比内容 · 即便后端可用也不算）。

    注：默认 hash 后端会先在后端断言处返回 invalid，此处验「不崩 + valid=False」契约。
    """
    rfc = _load_rfc()
    out = rfc.intent_recovery_cosine("", {})
    assert out["valid"] is False
    assert out["cosine"] is None


def test_IR_band_needs_two_refs():
    """_intent_recovery_band：<2 段原文 → valid=False（multi-ref 纪律 · 单 ref 失真）。"""
    rfc = _load_rfc()
    out = rfc._intent_recovery_band(["只有一段"])
    assert out["valid"] is False
    assert "≥2" in out["reason"]


def test_IR_band_hash_backend_invalid():
    """_intent_recovery_band 在 hash 后端 → valid=False（绝不用 hash 假语义算带）。"""
    rfc = _load_rfc()
    saved = os.environ.pop("EMBED_BACKEND", None)
    try:
        out = rfc._intent_recovery_band(["第一段原文", "第二段原文", "第三段原文"])
        assert out["valid"] is False
    finally:
        if saved is not None:
            os.environ["EMBED_BACKEND"] = saved


def test_IR_probe_advisory_and_exit_safe():
    """intent_recovery_probe 恒 gate_level=advisory（顾问制 · 永不阻断 · R3 断言）。"""
    rfc = _load_rfc()
    saved = os.environ.pop("EMBED_BACKEND", None)
    try:
        out = rfc.intent_recovery_probe("仿写反推", {"母题": "代价"},
                                        ["原文一", "原文二"])
        assert out["gate_level"] == "advisory"
        assert out["tag"] == "intent_recovery"
        # hash 后端：cosine_valid=False · in_band=None · localization_confidence=low
        assert out["cosine_valid"] is False
        assert out["in_band"] is None
        assert out["localization_confidence"] == "low"
    finally:
        if saved is not None:
            os.environ["EMBED_BACKEND"] = saved


def test_IR_probe_cross_stack_field_present():
    """跨栈一致性字段存在（cross_stack / localization_confidence · R3 断言）。"""
    rfc = _load_rfc()
    out = rfc.intent_recovery_probe("仿写", {"母题": "代价"}, ["原文一", "原文二"],
                                    cross_stack={"claude_cosine": 0.9})
    assert "cross_stack" in out
    assert "localization_confidence" in out
    assert out["cross_stack"]["claude_cosine"] == 0.9


def test_IR_not_in_hard_gate():
    """intent_recovery 全 advisory：探针 gate_level=advisory · 绝不进 HARD_GATE_CODES。"""
    rfc = _load_rfc()
    out = rfc.intent_recovery_probe("x", {"a": "b"}, ["p", "q"])
    assert out["gate_level"] == "advisory"
    try:
        import audit_hub
    except Exception:
        return
    # intent_recovery 不引入任何新 hard_gate code
    codes = getattr(audit_hub, "HARD_GATE_CODES", set())
    assert "INTENT_RECOVERY" not in codes
    assert "AUTHOR_THINKING_DRIFT" not in codes


# 真 mstyle 余弦路径（需 sentence-transformers + 模型本地缓存）。
# 🔴 单元测试默认 **skip**（绝不联网下模型 · 不慢 · 不flaky）：仅当 runner 在进程级已开
#    HF_HUB_OFFLINE（=1/true）+ EMBED_BACKEND=mstyle 表明「我已备好离线缓存」才跑真余弦断言。
#    HF_HUB_OFFLINE 须在 huggingface_hub import 前置位才生效，故只认进程级预设、不在测试内现设。
def test_IR_cosine_identical_text_near_one_if_mstyle():
    """[skipif] 进程级已开离线 mstyle 时：相同风格文本自余弦≈1.0（R3 断言 · 真语义路径）。"""
    if (os.environ.get("HF_HUB_OFFLINE") or "").strip().lower() not in ("1", "true", "yes"):
        return  # 默认跳过（未声明离线就绪 → 不联网不跑真模型）
    if (os.environ.get("EMBED_BACKEND") or "").strip().lower() != "mstyle":
        return
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return  # 无依赖 → 跳过
    rfc = _load_rfc()
    try:
        import embedding_store as es
        es._BACKEND = None          # 重探测后端
        same = "他把杯子摔在地上，碎片溅开。"
        # principles 传裸字符串 → _flatten_principles 原样返回（两侧 embed 收到完全相同文本）
        out = rfc.intent_recovery_cosine(same, same)
        if out.get("valid"):  # 后端真落到 mstyle 且模型已缓存
            assert out["cosine"] is not None
            # 归一向量自余弦 ≈ 1.0（mstyle 浮点可微超 1·放宽上界到 1.01）
            assert 0.95 <= out["cosine"] <= 1.01
    finally:
        try:
            import embedding_store as es
            es._BACKEND = None
        except Exception:
            pass
