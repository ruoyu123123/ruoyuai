"""test_av_judge_order_swap.py — S7 换序双跑一致性协议测试（LongJudgeBench arXiv:2606.01629 · 2026-07-07）。

根因（二轮移植 S7 · 实证）：长文本配对评判位置偏差严重——同一配对换序重判，不一致率可高达 78.7%。
单向单判的配对结论里混着大量「换个呈现顺序就翻转」的假信号。协议 = 换序双跑：同一配对跑正向
（作者真迹在前）+ 反向（仿写在前 · 复用 build_av_judge_prompt 既有 swap 参数 · 判定 prompt 零改动）
两遍，两跑 drift_dims 一致才采纳；不一致 = 弃票（drift_count=None · 无信号 · 绝不折中平均），
消费方 gen_writer.score_candidate 对 av_drift_count=None 走既有「缺 AV 信号」降级路径。

纪律（与 test_av_judge_selfconsist 同款 · 北极星①⑤⑥）：
  · 只测确定性逻辑（env 解析 / 双跑调用次数与方向 / 一致采纳 / 不一致弃票 / 留痕字段 /
    不一致率统计 / env=0 旧行为逐字节）。LLM 调用全 mock（mock call_gen_model · 不实跑 gen-model）。
  · 弃票绝不折中平均 / 并集 / 交集（不一致 = 无信号 · 不造假信号污染飞轮）。
  · env AV_JUDGE_ORDER_SWAP 默认 on（协议级可靠性加固）· =0 单跑与旧行为逐字节一致。
  · 仍 advisory 永不 hard_gate（协议只提升信号可靠性 · 不改判定逻辑）。

覆盖：[A] env 解析（默认 on / 0=off / 值集）；[B] 双跑 2× 调用 + 方向序 + prompt 逐字节复用既有构造；
  [C] 两序一致采纳 / 不一致弃票（无折中）/ 弃票流经 gen_writer 既有 None 降级路径；
  [D] env=0 单跑旧行为逐字节；[E] 不一致率统计（进程内累计 · 出错配对不计入）。
"""
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import av_judge as av  # noqa: E402

_DIM_NAMES = ["词汇选择", "句法", "话语连接词", "语用语气"]


# ════════════════════════════════════════════════════════════════
# mock / env 基础设施（复用 test_av_judge_selfconsist 范式）
# ════════════════════════════════════════════════════════════════

class _P:
    def __init__(self, name="active", model="m", temperature=0.8):
        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = None
        self.api_key = "sk-test"
        self.base_url = "http://localhost/v1"


class _Loader:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_callable_profiles(self):
        return self._profiles

    def get_active_profile(self):
        return self._profiles[0]


class _EnvPin:
    """临时钉住若干 env（None=移除），退出恢复原值——env 在函数内读，无需 reload。"""

    def __init__(self, **kv):
        self.kv = kv
        self.saved = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, old in self.saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
        return False


def _pin_defaults(order_swap=None):
    """钉住协议相关 env：N=3 · 半 swap 关（隔离 S7 面）· AV_JUDGE_ORDER_SWAP 按参数。"""
    return _EnvPin(AV_JUDGE_N_SAMPLES="3", AV_JUDGE_POSITION_SWAP=None,
                   AV_JUDGE_ORDER_SWAP=order_swap)


def _reply(verdicts):
    dims = {n: {"verdict": verdicts.get(n, av.MATCH_VERDICT), "reason": f"{n}理由"}
            for n in _DIM_NAMES}
    return "```json\n" + json.dumps({"dimensions": dims}, ensure_ascii=False) + "\n```"


def _mock_by_direction(fwd_reply, rev_reply):
    """按调用 tag 分方向吐回复（'_rev' 反向跑 · 其余正向）· 记录调用数/tag/prompt。"""
    calls = {"n": 0, "tags": [], "prompts": []}

    def mock(loader, system, user, tag=""):
        calls["n"] += 1
        calls["tags"].append(tag)
        calls["prompts"].append(user)
        r = rev_reply if "_rev" in tag else fwd_reply
        if isinstance(r, Exception):
            raise r
        return r, _P(), 0.1

    return mock, calls


def _run_pairwise(fwd_reply, rev_reply, order_swap=None, author="AUTHOR_X", replica="REPLICA_Y"):
    """在钉住的 env 下跑 pairwise_drift_count（mock call_gen_model）→ (out, calls)。"""
    mock, calls = _mock_by_direction(fwd_reply, rev_reply)
    orig = av.call_gen_model
    av.call_gen_model = mock
    try:
        with _pin_defaults(order_swap=order_swap):
            out = av.pairwise_drift_count(_Loader([_P()]), author, replica)
    finally:
        av.call_gen_model = orig
    return out, calls


# ════════════════════════════════════════════════════════════════
# [A] env AV_JUDGE_ORDER_SWAP 解析（默认 on · 协议级可靠性加固）
# ════════════════════════════════════════════════════════════════

def test_A_env_default_on():
    """AV_JUDGE_ORDER_SWAP 未设 / 空 / 非法 → on（换序双跑是协议级可靠性加固 · 该默认开）。"""
    for v in (None, "", "garbage", "2"):
        with _EnvPin(AV_JUDGE_ORDER_SWAP=v):
            assert av._order_swap_on() is True, v


def test_A_env_off_and_on_values():
    """0 / off / false / no（大小写不敏感）→ 单跑；1 / on / true / yes → 双跑。"""
    for v in ("0", "off", "OFF", "false", "no"):
        with _EnvPin(AV_JUDGE_ORDER_SWAP=v):
            assert av._order_swap_on() is False, v
    for v in ("1", "on", "true", "yes", "ON"):
        with _EnvPin(AV_JUDGE_ORDER_SWAP=v):
            assert av._order_swap_on() is True, v


# ════════════════════════════════════════════════════════════════
# [B] 双跑 = 2× judge 调用 · 方向序 · prompt 逐字节复用既有构造（判定逻辑零改动）
# ════════════════════════════════════════════════════════════════

def test_B_double_run_makes_2x_calls():
    """双跑调用次数断言：N=3 → 正向 3 + 反向 3 = 6 次 call_gen_model（2× 成本 · 不抠 token）。"""
    r = _reply({})
    out, calls = _run_pairwise(r, r)
    assert calls["n"] == 6
    assert out["error"] is None


def test_B_run_directions_and_tags():
    """前 3 次为正向跑（tag=_fwd · 作者真迹文本在前），后 3 次反向跑（tag=_rev · 仿写文本在前）。"""
    r = _reply({})
    _out, calls = _run_pairwise(r, r)
    assert [("_rev" in t) for t in calls["tags"]] == [False] * 3 + [True] * 3
    assert all(t.startswith("av_judge_bestofn_fwd") for t in calls["tags"][:3])
    assert all(t.startswith("av_judge_bestofn_rev") for t in calls["tags"][3:])
    for p in calls["prompts"][:3]:
        assert p.index("AUTHOR_X") < p.index("REPLICA_Y")   # 正向：作者真迹在前
    for p in calls["prompts"][3:]:
        assert p.index("REPLICA_Y") < p.index("AUTHOR_X")   # 反向：仿写在前


def test_B_prompts_byte_identical_to_existing_builder():
    """双跑 prompt 逐字节复用 build_av_judge_prompt 既有 swap 构造——判定逻辑本身零改动（S7 纪律）。"""
    r = _reply({})
    _out, calls = _run_pairwise(r, r)
    exp_fwd = av.build_av_judge_prompt("AUTHOR_X", "REPLICA_Y", 3000, swap=False)
    exp_rev = av.build_av_judge_prompt("AUTHOR_X", "REPLICA_Y", 3000, swap=True)
    assert all(p == exp_fwd for p in calls["prompts"][:3])
    assert all(p == exp_rev for p in calls["prompts"][3:])


# ════════════════════════════════════════════════════════════════
# [C] 两序一致采纳 / 不一致弃票（无折中）/ 弃票走 gen_writer 既有 None 路径
# ════════════════════════════════════════════════════════════════

def test_C_consistent_both_orders_adopted():
    """两序结论一致（同判「词汇选择」走味）→ 采纳 · order_consistency=consistent · 留痕两跑明细。"""
    r = _reply({"词汇选择": av.DRIFT_VERDICT})
    out, _ = _run_pairwise(r, r)
    assert out["drift_count"] == 1
    assert out["drift_dims"] == ["词汇选择"]
    assert out["error"] is None
    assert out["order_consistency"] == av.ORDER_CONSISTENT
    assert out["order_runs"]["forward"]["drift_dims"] == ["词汇选择"]
    assert out["order_runs"]["reversed"]["drift_dims"] == ["词汇选择"]
    # 采纳后维度明细 / 方差透明字段照旧透出（消费方契约不变）
    assert out["dimensions"]["词汇选择"]["drift"] is True
    assert out["n_valid_samples"] == 3


def test_C_inconsistent_abstains_no_averaging():
    """两序结论不一致（正向判 2 维走味 · 反向判 0 维）→ 弃票：drift_count=None（绝不折中成 1）·
    drift_dims 空 · error=None（不是失败，是无信号）· order_consistency=inconsistent。"""
    fwd = _reply({"词汇选择": av.DRIFT_VERDICT, "句法": av.DRIFT_VERDICT})
    rev = _reply({})
    out, calls = _run_pairwise(fwd, rev)
    assert calls["n"] == 6                      # 双跑真跑满
    assert out["drift_count"] is None           # 弃票 ≠ 平均（不是 1）≠ 并集（不是 2）
    assert out["drift_dims"] == []
    assert out["dimensions"] == {}              # 不透出任一跑的维度明细当真信号
    assert out["error"] is None                 # 无信号 ≠ 出错
    assert out["order_consistency"] == av.ORDER_INCONSISTENT
    # 留痕：两跑各自结论可复盘（透明不黑箱）
    assert out["order_runs"]["forward"]["drift_dims"] == ["词汇选择", "句法"]
    assert out["order_runs"]["reversed"]["drift_dims"] == []


def test_C_dim_set_mismatch_is_inconsistent_even_same_count():
    """一致性判据是维度**集合**：两跑各判 1 维但维不同（数相同）→ 仍不一致弃票（巧合≠一致）。"""
    fwd = _reply({"词汇选择": av.DRIFT_VERDICT})
    rev = _reply({"句法": av.DRIFT_VERDICT})
    out, _ = _run_pairwise(fwd, rev)
    assert out["drift_count"] is None
    assert out["order_consistency"] == av.ORDER_INCONSISTENT


# （v29：score_candidate 随 best-of-N 家族删除 · 原 test_C_abstain_flows_gen_writer_existing_none_path
#   测的 gen_writer 择优集成路径已不存在 · av_judge 本体弃票行为由本文件其余测试覆盖。）

def test_D_env0_single_run_byte_identical_old_behavior():
    """AV_JUDGE_ORDER_SWAP=0 → 单跑：3 次调用 · 旧 tag av_judge_bestofn · prompt 逐字节同旧构造 ·
    结果核心字段同旧契约 · 仅多 order_consistency=single_run 留痕（无 order_runs/stats 字段）。"""
    r = _reply({"词汇选择": av.DRIFT_VERDICT})
    out, calls = _run_pairwise(r, r, order_swap="0")
    assert calls["n"] == 3                                    # 单跑 · 不双倍
    assert calls["tags"] == [f"av_judge_bestofn_sc{i}" for i in (1, 2, 3)]  # 旧 tag 不变
    exp = av.build_av_judge_prompt("AUTHOR_X", "REPLICA_Y", 3000, swap=False)
    assert all(p == exp for p in calls["prompts"])            # prompt 逐字节同旧
    # 旧契约字段逐项一致
    assert out["drift_count"] == 1
    assert out["drift_dims"] == ["词汇选择"]
    assert out["parse_ok"] is True
    assert out["error"] is None
    assert out["n_valid_samples"] == 3
    assert out["unstable_dims"] == []
    # S7 仅加留痕 · 不加双跑字段
    assert out["order_consistency"] == av.ORDER_SINGLE_RUN
    assert "order_runs" not in out
    assert "order_swap_stats" not in out


def test_D_env0_error_path_unchanged():
    """env=0 全失败 → 旧降级契约不变：drift_count=None + error 非空（+ single_run 留痕）。"""
    boom = av.GenModelExhaustedError([("p", "x")])
    out, calls = _run_pairwise(boom, boom, order_swap="0")
    assert calls["n"] == 3
    assert out["drift_count"] is None
    assert out["error"]
    assert out["order_consistency"] == av.ORDER_SINGLE_RUN


# ════════════════════════════════════════════════════════════════
# [E] 不一致率统计（进程内累计 · 进 trace 供飞轮观察 judge 可靠性）
# ════════════════════════════════════════════════════════════════

def test_E_inconsistency_rate_accumulates():
    """跑 1 个一致配对 + 1 个不一致配对 → pairs_total=2 · pairs_inconsistent=1 · rate=0.5，
    且统计随每次返回的 order_swap_stats 留痕（进 trace · 飞轮观察 judge 可靠性）。"""
    av.reset_order_swap_stats()
    try:
        r = _reply({"句法": av.DRIFT_VERDICT})
        out1, _ = _run_pairwise(r, r)                          # 一致
        assert out1["order_swap_stats"] == {
            "pairs_total": 1, "pairs_consistent": 1, "pairs_inconsistent": 0,
            "inconsistency_rate": 0.0}
        out2, _ = _run_pairwise(_reply({"句法": av.DRIFT_VERDICT}), _reply({}))  # 不一致
        assert out2["order_swap_stats"] == {
            "pairs_total": 2, "pairs_consistent": 1, "pairs_inconsistent": 1,
            "inconsistency_rate": 0.5}
        assert av.order_swap_stats()["inconsistency_rate"] == 0.5
    finally:
        av.reset_order_swap_stats()


def test_E_error_pair_not_counted_and_short_circuits():
    """正向整跑失败 → 短路（反向 0 调用 · 只烧 3 次）· 出错配对不计入不一致率（未完成判定≠不一致）·
    order_consistency=None（非三态 · 判定未完成）。"""
    av.reset_order_swap_stats()
    try:
        boom = av.GenModelExhaustedError([("p", "x")])
        out, calls = _run_pairwise(boom, _reply({}))
        assert calls["n"] == 3                                # 正向 3 次失败即短路
        assert out["drift_count"] is None
        assert out["error"]
        assert out["order_consistency"] is None
        assert av.order_swap_stats() == {
            "pairs_total": 0, "pairs_consistent": 0, "pairs_inconsistent": 0,
            "inconsistency_rate": None}
    finally:
        av.reset_order_swap_stats()


def test_E_stats_reset():
    """reset_order_swap_stats 清零（测试/批次隔离）· 空统计 rate=None（不臆造 0）。"""
    av.reset_order_swap_stats()
    s = av.order_swap_stats()
    assert s["pairs_total"] == 0
    assert s["inconsistency_rate"] is None


# ════════════════════════════════════════════════════════════════
# [F] 协议不改铁律：仍 advisory · AV_TRAIT_DRIFT 不进 HARD_GATE_CODES
# ════════════════════════════════════════════════════════════════

def test_F_protocol_keeps_advisory_no_hard_gate():
    """S7 换序双跑只提升配对信号可靠性 · 不引入新判决：AV_TRAIT_DRIFT 仍不进 HARD_GATE_CODES。"""
    try:
        import audit_hub
    except Exception:
        return
    codes = getattr(audit_hub, "HARD_GATE_CODES", set())
    assert av.ISSUE_CODE not in codes
