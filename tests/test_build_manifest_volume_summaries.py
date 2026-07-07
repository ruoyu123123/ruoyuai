# -*- coding: utf-8 -*-
"""S10 消费端回归锁（2026-07-07·Ex3 摘要金字塔）：build_manifest 注入已闭合卷卷级摘要。

生产端=save_state --apply-volume-summary（apply 硬校验保证只有真闭合卷入账）；
消费端=_collect_volume_summaries_digest → manifest["volume_summaries_digest"]（T3 长程记忆）。
金字塔语义：writer 对历史卷读一条 300-500 字卷摘要（换粒度），当前卷仍走细粒度通道。
volume_summaries 缺失/空 = 老项目常态 → 键不注入（零行为变化）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402
import manifest_budget as mb  # noqa: E402


class _S:
    """最小 DatabaseScanner 桩：只实现 _collect_volume_summaries_digest 用到的 load()。"""

    def __init__(self, summary_doc):
        self._doc = summary_doc

    def load(self, name, default=None):
        if name == "故事块摘要":
            return self._doc
        return default if default is not None else {}


def test_digest_absent_when_no_volume_summaries():
    """老项目常态：无 volume_summaries 键 / 空列表 → None（键不注入·零行为变化）。"""
    assert bm._collect_volume_summaries_digest(_S({})) is None
    assert bm._collect_volume_summaries_digest(_S({"volume_summaries": []})) is None
    assert bm._collect_volume_summaries_digest(_S({"volume_summaries": "bad"})) is None


def test_digest_injects_closed_volume_summary():
    doc = {"volume_summaries": [{
        "volume": 1,
        "summary": "卷一：主角踏入育新中学，识破首个规则陷阱，以肋骨为价换得离场资格。",
        "source": ["cluster_001", "cluster_002", "cluster_009"],
        "generated_at_cluster": "cluster_010",
    }]}
    out = bm._collect_volume_summaries_digest(_S(doc))
    assert isinstance(out, list) and len(out) == 1
    assert out[0]["volume"] == 1
    assert "育新中学" in out[0]["summary"]
    assert out[0]["clusters"] == "cluster_001~cluster_009"


def test_digest_skips_malformed_entries():
    """缺 summary 的坏条目跳过；全坏 → None。"""
    doc = {"volume_summaries": [{"volume": 1}, {"volume": 2, "summary": ""}]}
    assert bm._collect_volume_summaries_digest(_S(doc)) is None
    doc2 = {"volume_summaries": [{"volume": 1}, {"volume": 2, "summary": "好卷", "source": ["cluster_003"]}]}
    out = bm._collect_volume_summaries_digest(_S(doc2))
    assert len(out) == 1 and out[0]["volume"] == 2
    assert out[0]["clusters"] == "cluster_003~cluster_003"


def test_digest_registered_as_t3_in_budget_tiers():
    """S1 预算契约：新段必须归 tier——volume_summaries_digest = T3 长程记忆（漏归测红）。"""
    assert mb.SECTION_TIERS.get("volume_summaries_digest") == mb.TIER_T3


def test_build_manifest_wires_collector():
    """接线锁：build_manifest 源码把 collector 挂进 manifest 组装（防孤儿函数）。"""
    src = (_ROOT / "core" / "scripts" / "build_manifest.py").read_text(encoding="utf-8")
    assert '"volume_summaries_digest": _collect_volume_summaries_digest(s)' in src
