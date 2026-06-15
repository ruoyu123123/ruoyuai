#!/usr/bin/env python3
"""volume_arc_drift_scanner 卷号字段 + 关键词 修复测试（2026-06-15 Workflow 审计 confirmed）。

北极星③大势漂移哨兵的 3 个 confirmed bug：
- L97 vol_clusters 只读 c.get("vol")·但真实 event 簇 cluster 存 "volume"/"parent_me" 无 "vol"
  → 本卷 cluster 全被过滤 → coverage 恒 0 → 每卷过半误报 VOLUME_ARC_DRIFT(verify 隔离实验铁证)。
  → 抽 _cluster_vol(vol→volume→parent_me 回退) 共用。
- L104 _me_vol 只读 "vol"·ME 权威卷字段是 "volume" → 对齐 cluster_emergence._me_volume。
- L48 _kw 单字滑窗跨标点拼假 bigram(胜利。反派→利反) → 段内 2-gram。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import volume_arc_drift_scanner as v  # noqa: E402


def test_cluster_vol_reads_volume_and_parent_me():
    """🔴 _cluster_vol：vol → volume → parent_me 正则回退（真实 cluster 存 volume/parent_me 无 vol）。"""
    assert v._cluster_vol({"vol": 1}) == 1
    assert v._cluster_vol({"volume": 2}) == 2, "真实 cluster 的 volume 字段应读到"
    assert v._cluster_vol({"parent_me": "ME-V3-02"}) == 3, "parent_me 正则回退"
    assert v._cluster_vol({"volume": "2"}) == 2, "数字字符串 volume"
    assert v._cluster_vol({}) is None, "无卷号字段 → None"
    # vol 优先于 volume（向后兼容旧字段）
    assert v._cluster_vol({"vol": 1, "volume": 9}) == 1


def test_kw_no_cross_boundary_bigram():
    """🔴 _kw 段内 2-gram：不跨标点/边界拼假 bigram。"""
    kw = v._kw("胜利。反派失败")
    assert "利反" not in kw, "不该跨句标点拼出「利反」桥接词"
    assert "胜利" in kw and "反派" in kw and "失败" in kw, "段内 bigram 应保留"
    # 英数 token 仍正常
    assert "abc" in v._kw("abc 测试") or "测试" in v._kw("abc 测试")


def test_current_vol_uses_cluster_vol_fallback():
    """_current_vol 复用 _cluster_vol：volume 键的已写 cluster 能算出 cur_vol（原只读 vol 算不出）。"""
    sj = {"clusters": [
        {"volume": 1, "chapter_range": [1, 3], "status": "已完成"},
        {"volume": 2, "chapter_range": [4, 6], "status": "in_progress"},
    ]}
    assert v._current_vol(sj) == 2, "volume 键的最高已写卷应为 2（原只读 vol 会返 None）"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
