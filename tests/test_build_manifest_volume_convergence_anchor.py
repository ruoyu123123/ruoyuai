# -*- coding: utf-8 -*-
"""卷收敛锚 v28 字段回归锁（2026-07-08·验证书 e2e 抓出）。

根因：`gen_creative_volume_arc` emit 的大势卡卷层是 v28 卷=阶段 schema
（volume_core_conflict / volume_thread / volume_finale_signal），而
`_build_volume_convergence_anchor` 原键表只认旧名（core_conflict/ending_state/...）
→ 新书（进度.volumes 空·大势卡只有 v28 字段）收敛锚静默 None——北极星③
「大势已定」软牵引失效，writer 看不到本卷要收束到哪。

修法：键表加 v28 三字段 + volume_core_conflict → core_conflict 别名。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402


class _S:
    """最小 DatabaseScanner 桩：只实现 _build_volume_convergence_anchor 用到的 load()。"""

    def __init__(self, grand_trend=None, progress=None):
        self._gt = grand_trend or {}
        self._prog = progress or {}

    def load(self, name, default=None):
        if name == "大势卡":
            return self._gt
        if name == "进度":
            return self._prog
        return default if default is not None else {}


_V28_VOL = {"vol": 1, "phase": "新手村阶段", "title": "临时工阴差",
            "volume_core_conflict": "并购乡野怨灵的香火神格完成第一桶金套现",
            "volume_thread": "一本空白阴阳对赌协议",
            "volume_finale_signal": "信仰套现触发国安红色警报"}


def test_v28_only_volume_fields_produce_anchor():
    """新书形态：大势卡只有 v28 卷层字段 + 进度.volumes 空 → 锚必须非 None。"""
    anchor = bm._build_volume_convergence_anchor(
        _S(grand_trend={"volumes": [_V28_VOL]}), {"vol": 1})
    assert anchor is not None, "v28 卷层字段被收敛锚忽略（北极星③软牵引静默失效）"
    assert anchor["volume_core_conflict"] == _V28_VOL["volume_core_conflict"]
    assert anchor["volume_thread"] == _V28_VOL["volume_thread"]
    # 别名：老消费方读 core_conflict 也要能拿到卷核心任务
    assert anchor["core_conflict"] == _V28_VOL["volume_core_conflict"]


def test_old_fields_still_win_over_alias():
    """旧字段仍第一优先：显式 core_conflict 存在时不被 v28 别名覆盖。"""
    vol = dict(_V28_VOL)
    vol["core_conflict"] = "旧口径核心冲突"
    anchor = bm._build_volume_convergence_anchor(
        _S(grand_trend={"volumes": [vol]}), {"vol": 1})
    assert anchor["core_conflict"] == "旧口径核心冲突"
    assert anchor["volume_core_conflict"] == _V28_VOL["volume_core_conflict"]


def test_no_volume_match_returns_none():
    anchor = bm._build_volume_convergence_anchor(
        _S(grand_trend={"volumes": [_V28_VOL]}), {"vol": 9})
    assert anchor is None


def test_vol_derived_from_parent_me():
    """cluster 无 vol 时从 parent_me（ME-V1-1）推卷号。"""
    anchor = bm._build_volume_convergence_anchor(
        _S(grand_trend={"volumes": [_V28_VOL]}), {"parent_me": "ME-V1-1"})
    assert anchor is not None
    assert anchor["core_conflict"] == _V28_VOL["volume_core_conflict"]
