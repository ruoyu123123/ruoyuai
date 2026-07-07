# -*- coding: utf-8 -*-
"""A13 切点前瞻熵（arXiv:2604.09854）确定性单元测试（零 LLM / 零联网 / mock 桥）。

覆盖两层：
  · nn_forecast_entropy_bridge —— 条件 surprisal 代理数学口径 / 门控关闭全 None /
    单对缺口守卫（None 侧 / token 差 ≤0 / 空白窗）/ 负值钳 0
  · hook_strength_scanner 集成 —— shadow 默认零行为变化锁（评分/weak/severity/退出语义
    与桥不可用时完全一致）/ off 模式绝不触桥 / 不可用诚实 None / 报告留痕对位

北极星⑤纪律锁：forecast_entropy 是纯观测列（gate_level=advisory·mode 默认 shadow），
本文件同时钉死「新特征绝不改变既有切点评分与结论」。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import hook_strength_scanner as hs  # noqa: E402
import nn_forecast_entropy_bridge as feb  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _stats(mean: float, n: int) -> dict:
    """伪造 nn_surprisal_bridge 单条命中结果（只填桥消费的字段 + 契约必备）。"""
    return {"id": "x", "mean_surprisal": mean, "std_surprisal": 1.0,
            "max_surprisal": mean + 2, "min_surprisal": max(0.0, mean - 2),
            "skewness": 0.0, "kurtosis": 0.0, "token_count": n, "source": "model"}


class _StubSurprisalBridge:
    """替身底层 surprisal 桥：按预置队列回结果·记录调用。"""

    def __init__(self, results=None, enabled=True):
        self.results = results or []
        self._enabled = enabled
        self.calls = []

    def enabled(self):
        return self._enabled

    def predict_batch(self, texts, ids=None, timeout=None):
        self.calls.append(list(texts))
        return list(self.results)


class _StubForecastBridge:
    """替身前瞻熵桥（scanner 集成测试用）：可预置结果 / 关门 / 触发即爆。"""

    def __init__(self, per_cut=None, enabled=True, explode=False):
        self.per_cut = per_cut
        self._enabled = enabled
        self.explode = explode
        self.calls = 0

    def enabled(self):
        if self.explode:
            raise AssertionError("off 模式不允许触桥（连 enabled 都不该问）")
        return self._enabled

    def forecast_entropy_batch(self, pre_tails, post_heads, timeout=None):
        if self.explode:
            raise AssertionError("off 模式不允许触桥")
        self.calls += 1
        if self.per_cut is not None:
            return list(self.per_cut)
        return [None] * len(pre_tails)


def _write_chapter(proj: Path, ch: int, body: str) -> None:
    d = proj / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"第{ch:03d}章.txt").write_text(body, encoding="utf-8")


def _cluster_body(n_paras: int = 20) -> str:
    return "第001章 起\n" + "\n\n".join(
        f"中性第{i}段平铺叙述没有钩子词就是日常描写走路吃饭。" for i in range(n_paras))


# ══════════════════════════════════════════════════════════════════════════
# 桥层：条件 surprisal 数学口径
# ══════════════════════════════════════════════════════════════════════════
def test_bridge_conditional_surprisal_math(monkeypatch):
    """代理指标核心数学：cont_bits = mean_f*n_f - mean_t*n_t，per_token = cont_bits/(n_f-n_t)。
    tail: mean 2.0 × 10 tok = 20 bits；full: mean 3.0 × 20 tok = 60 bits
    → 下文条件 40 bits / 10 tok = 4.0 bits/token。"""
    stub = _StubSurprisalBridge(results=[_stats(2.0, 10), _stats(3.0, 20)])
    monkeypatch.setattr(feb, "_sb", stub)
    out = feb.forecast_entropy_batch(["前文尾巴。"], ["下文开头。"])
    assert len(out) == 1 and out[0] is not None, out
    r = out[0]
    assert r["forecast_entropy_bits_per_token"] == 4.0, r
    assert r["continuation_bits_total"] == 40.0, r
    assert r["continuation_token_count"] == 10 and r["context_token_count"] == 10, r
    assert r["source"] == "surprisal_proxy", r
    # 桥按 [tail, tail+head] 成对送批：full 文本必须是 tail+head 拼接
    assert stub.calls == [["前文尾巴。", "前文尾巴。下文开头。"]], stub.calls


def test_bridge_disabled_returns_all_none(monkeypatch):
    """底层通路门控关（RUOYU_NN_SURPRISAL off 等价）→ 全 None 且零推理调用。"""
    stub = _StubSurprisalBridge(enabled=False)
    monkeypatch.setattr(feb, "_sb", stub)
    out = feb.forecast_entropy_batch(["前文。", "前文二。"], ["下文。", "下文二。"])
    assert out == [None, None], out
    assert stub.calls == [], stub.calls
    assert feb.enabled() is False


def test_bridge_pair_guards_none_and_token_diff(monkeypatch):
    """单对缺口守卫：任一侧打分 None → None；token 差 ≤0 → None；不拖累相邻切点。"""
    stub = _StubSurprisalBridge(results=[
        None, _stats(3.0, 20),          # 对 0：tail 侧 None → None
        _stats(2.0, 10), _stats(3.0, 10),  # 对 1：token 差 0 → None
        _stats(2.0, 10), _stats(2.5, 30),  # 对 2：正常命中
    ])
    monkeypatch.setattr(feb, "_sb", stub)
    out = feb.forecast_entropy_batch(["尾0。", "尾1。", "尾2。"],
                                     ["头0。", "头1。", "头2。"])
    assert out[0] is None and out[1] is None, out
    assert out[2] is not None, out
    # 对 2：cont_bits = 2.5*30 - 2.0*10 = 55 / 20 tok = 2.75
    assert out[2]["forecast_entropy_bits_per_token"] == 2.75, out[2]


def test_bridge_blank_window_skipped_without_inference(monkeypatch):
    """空白前文/下文的切点：预检直接 None，不进推理批次（诚实缺失·不浪费算力）。"""
    stub = _StubSurprisalBridge(results=[_stats(2.0, 10), _stats(3.0, 20)])
    monkeypatch.setattr(feb, "_sb", stub)
    out = feb.forecast_entropy_batch(["", "有前文。", "有前文。"],
                                     ["有下文。", "  \n ", "有下文。"])
    assert out[0] is None and out[1] is None and out[2] is not None, out
    # 只有第 3 对进了批次（2 条文本）
    assert stub.calls and len(stub.calls[0]) == 2, stub.calls


def test_bridge_negative_rounding_clamped_to_zero(monkeypatch):
    """mean round(4) 舍入可造成极小负差：per_token 钳 0（surprisal 恒正·负值只能是舍入）。"""
    # full 总 bits(20.0) < tail 总 bits(20.002) → cont_bits 微负
    stub = _StubSurprisalBridge(results=[_stats(2.0002, 10), _stats(1.0, 20)])
    monkeypatch.setattr(feb, "_sb", stub)
    out = feb.forecast_entropy_batch(["前文。"], ["下文。"])
    assert out[0] is not None, out
    assert out[0]["forecast_entropy_bits_per_token"] == 0.0, out[0]


def test_bridge_input_length_mismatch_all_none(monkeypatch):
    """输入长度失配 → 全 None（不猜对位·不崩）。"""
    stub = _StubSurprisalBridge(results=[_stats(2.0, 10), _stats(3.0, 20)])
    monkeypatch.setattr(feb, "_sb", stub)
    assert feb.forecast_entropy_batch(["a", "b"], ["c"]) == [None, None]
    assert stub.calls == []


# ══════════════════════════════════════════════════════════════════════════
# scanner 集成：shadow 零行为变化锁 / off / 不可用 None / 报告留痕
# ══════════════════════════════════════════════════════════════════════════
def _scan_cluster(proj_body: str, monkeypatch, bridge, mode_env=None):
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        _write_chapter(proj, 1, proj_body)
        monkeypatch.setenv("CLUSTER_MODE", "1")
        if mode_env is None:
            monkeypatch.delenv("HOOK_FORECAST_ENTROPY_MODE", raising=False)
        else:
            monkeypatch.setenv("HOOK_FORECAST_ENTROPY_MODE", mode_env)
        monkeypatch.setattr(hs, "_forecast_bridge", bridge)
        return hs.scan(proj, 1)


def test_scanner_shadow_zero_behavior_change_lock(monkeypatch):
    """🔴 零行为变化锁：桥可用（shadow 出值）与桥不可用两次 scan，
    除 forecast_entropy 外的整份报告逐字段一致——评分/weak/severity/warning/退出语义不受影响。"""
    body = _cluster_body(20)
    vals = [{"forecast_entropy_bits_per_token": 5.1, "continuation_bits_total": 510.0,
             "continuation_token_count": 100, "context_token_count": 120,
             "source": "surprisal_proxy"}] * 4
    r_on = _scan_cluster(body, monkeypatch, _StubForecastBridge(per_cut=vals))
    r_off = _scan_cluster(body, monkeypatch, None)  # 桥不可用（import 失败形态）
    fe_on = r_on.pop("forecast_entropy")
    fe_off = r_off.pop("forecast_entropy")
    assert r_on == r_off, {k: (r_on.get(k), r_off.get(k))
                           for k in set(r_on) | set(r_off)
                           if r_on.get(k) != r_off.get(k)}
    # 观测列本身：一边有值一边诚实 None
    assert fe_on["available"] is True and fe_off["available"] is False
    assert all(x is None for x in fe_off["per_cut"]), fe_off


def test_scanner_off_mode_never_touches_bridge(monkeypatch):
    """HOOK_FORECAST_ENTROPY_MODE=off：绝不触桥（触发即 AssertionError）·mode 留痕 off。"""
    r = _scan_cluster(_cluster_body(20), monkeypatch,
                      _StubForecastBridge(explode=True), mode_env="off")
    fe = r["forecast_entropy"]
    assert fe["mode"] == "off" and fe["per_cut"] == [] and fe["available"] is False, fe


def test_scanner_unavailable_honest_none(monkeypatch):
    """shadow 默认 + 桥门控关：per_cut 与切点一一对位且全 None（诚实缺失·不伪装 0 分）。"""
    r = _scan_cluster(_cluster_body(20), monkeypatch, _StubForecastBridge(enabled=False))
    fe = r["forecast_entropy"]
    n_cuts = r["cluster_pacing"]["pseudo_cuts"]
    assert fe["mode"] == "shadow", fe
    assert len(fe["per_cut"]) == n_cuts and all(x is None for x in fe["per_cut"]), fe
    assert fe["available"] is False


def test_scanner_report_trace_and_alignment(monkeypatch):
    """报告留痕：advisory + 代理口径标注 + per_cut 与 cluster_pacing.cut_indices 等长对位。"""
    vals = [{"forecast_entropy_bits_per_token": float(i + 3),
             "continuation_bits_total": 100.0 * (i + 1),
             "continuation_token_count": 50, "context_token_count": 80,
             "source": "surprisal_proxy"} for i in range(4)]
    r = _scan_cluster(_cluster_body(20), monkeypatch, _StubForecastBridge(per_cut=vals))
    fe = r["forecast_entropy"]
    assert fe["gate_level"] == "advisory", fe
    assert fe["proxy"] == "post_cut_conditional_surprisal", fe
    assert "非真前瞻熵" in fe["note"] and "待真机标定" in fe["note"], fe
    assert len(fe["per_cut"]) == len(r["cluster_pacing"]["cut_indices"]), fe
    assert fe["available"] is True
    # 整份报告可 JSON 序列化（audit_hub 消费契约）
    json.dumps(r, ensure_ascii=False)


def test_scanner_non_cluster_mode_field_present_but_empty(monkeypatch):
    """非 CLUSTER_MODE：无拟切点 → forecast_entropy 字段仍留痕（mode/shadow）但 per_cut 空。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        filler = "他在长街上慢慢走着回想这些天发生的事盘算下一步该怎么走。" * 40
        _write_chapter(proj, 1, f"第001章 风起\n{filler}\n\n"
                                "那人忽然拔刀逼近，竟然不是他写的，门后到底是谁？话没说完……")
        monkeypatch.delenv("CLUSTER_MODE", raising=False)
        monkeypatch.delenv("HOOK_FORECAST_ENTROPY_MODE", raising=False)
        monkeypatch.setattr(hs, "_forecast_bridge", _StubForecastBridge(explode=True))
        r = hs.scan(proj, 1)
    fe = r["forecast_entropy"]
    assert fe["mode"] == "shadow" and fe["per_cut"] == [] and fe["available"] is False, fe


def test_cut_indices_exposed_and_consistent():
    """scan_cluster_hook_pacing 新增 cut_indices：与 pseudo_cuts/scores 等长·段数守卫返回空表。"""
    paras = [f"中性第{i}段平铺叙述。" for i in range(24)]
    r = hs.scan_cluster_hook_pacing(paras, n_pseudo_cuts=4)
    assert len(r["cut_indices"]) == r["pseudo_cuts"] == len(r["scores"]), r
    assert all(isinstance(i, int) and 2 <= i < 24 for i in r["cut_indices"]), r
    guard = hs.scan_cluster_hook_pacing(["一", "二", "三"], n_pseudo_cuts=4)
    assert guard["cut_indices"] == [] and guard["pseudo_cuts"] == 0, guard


def test_default_env_is_shadow_and_gate_off_by_default():
    """默认保守锁：mode 默认 shadow（非 active 概念）；真桥门控默认关（RUOYU_NN_SURPRISAL 未开）。"""
    assert hs._forecast_entropy_mode() in ("shadow", "off")
    if "HOOK_FORECAST_ENTROPY_MODE" not in os.environ:
        assert hs._forecast_entropy_mode() == "shadow"
    if os.environ.get("RUOYU_NN_SURPRISAL") != "1":
        assert feb.enabled() is False
