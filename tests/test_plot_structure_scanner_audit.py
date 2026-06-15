"""plot_structure_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 中 file==plot_structure_scanner.py 的唯一修复
（北极星⑤克制：advisory 检测器·确定性数据投影健壮性·不干涉模型创作判断）：

  · [L105] scan_beat cluster 分支路由：
    旧代码 `next((k for k in cluster_beats.keys() if cluster_beats.get(k)), None)` 取
    「第一个非空 cluster 的 beat 列表」，无视当前实际评估的 cluster——cluster 模式下
    audit_hub 用虚拟 ch=9000 跑任何 cluster（ch 无法标识 cluster），真实 cluster 身份经
    CLUSTER_ID env 透传（audit_hub.py:1000）。后果：对 cluster_002+ 报的
    cluster_id_evaluated / beats_declared 全是别的 cluster（通常 cluster_001）的——一个
    带权威外观（cluster_id_evaluated 字段 + writer-facing advisory）实则错的诊断。
    修复：读 CLUSTER_ID env + 归一化定位正确 cluster_beats key，与兄弟 scanner
    golden_three_scanner.py:286-288 同款；取不到再回退首个非空 key 并标 routing_fallback。

守护点：
  1. CLUSTER_ID=cluster_002 时路由到 cluster_002（旧代码返回 cluster_001 = bug）；
  2. CLUSTER_ID=cluster_001 时路由到 cluster_001（无回归）；
  3. 归一化容错入参形态 "001"/"cluster_001"/"1" 都路由到 cluster_001；
  4. 正确路由时 routing_fallback=False；CLUSTER_ID 指向无 beat 的 cluster → 回退首个非空 +
     routing_fallback=True（让消费者知道结果非权威）；
  5. CLUSTER_ID 缺失（env 未设）→ 回退首个非空 key，routing_fallback=False（无 env 不算回退误导）；
  6. cluster_beats 全空 → cluster_id_key=None + warning，不崩；
  7. 非 cluster 模式（CLUSTER_MODE!=1）走 chapter 视野，不受本修复影响（无回归）。

跑法：PYTHONIOENCODING=utf-8 python tests/test_plot_structure_scanner_audit.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import plot_structure_scanner as pss  # noqa: E402


# ============================================================
# fixture：在临时项目落 beat_map.json，受控设置 CLUSTER_MODE/CLUSTER_ID 跑 scan_beat
# ============================================================

@contextmanager
def _env(**kv):
    """临时设置/清除环境变量（None=删除），退出后恢复原值。"""
    old = {}
    for k, v in kv.items():
        old[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _beats(*names):
    """构造一个 beat 列表（cluster_beats[key] 的值形态：[{'beat': name}, ...]）。"""
    return [{"beat": n} for n in names]


def _scan_cluster(cluster_beats, cluster_id_env, *, ch=9000):
    """把 cluster_beats 写进临时项目的 beat_map.json，设 CLUSTER_MODE=1 + 给定 CLUSTER_ID，
    返回 scan_beat(project, ch) 的报告 dict。cluster_id_env=None → 不设 CLUSTER_ID env。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "beat_map.json").write_text(
            json.dumps({"cluster_beats": cluster_beats}, ensure_ascii=False),
            encoding="utf-8",
        )
        with _env(CLUSTER_MODE="1", CLUSTER_ID=cluster_id_env):
            return pss.scan_beat(proj, ch)


# 两个 cluster 各自的 beat 列表，内容刻意不同——用以区分到底路由到了哪个 cluster
_CB_TWO = {
    "cluster_001": _beats("开场钩子", "建置", "催化剂"),
    "cluster_002": _beats("中点", "坏人逼近", "一无所有"),
}


# ============================================================
# [L105] 核心：cluster 模式按 CLUSTER_ID 路由到正确 cluster，而非永远取第一个非空
# ============================================================

def test_routes_to_cluster_002_not_first_nonempty():
    """[L105 核心] CLUSTER_ID=cluster_002 → 必须报 cluster_002 的 beat；
    旧代码取第一个非空 key = cluster_001（bug：带权威外观的错误诊断）。"""
    rep = _scan_cluster(_CB_TWO, "cluster_002")
    assert rep["cluster_mode"] is True, rep
    assert rep["cluster_id_evaluated"] == "cluster_002", rep
    assert rep["beats_declared_count"] == 3, rep
    assert "中点" in rep["beats_declared"], rep
    # 关键：绝不能串到 cluster_001 的 beat
    assert "开场钩子" not in rep["beats_declared"], rep
    assert rep["routing_fallback"] is False, rep


def test_routes_to_cluster_001():
    """CLUSTER_ID=cluster_001 → 报 cluster_001 的 beat（无回归）。"""
    rep = _scan_cluster(_CB_TWO, "cluster_001")
    assert rep["cluster_id_evaluated"] == "cluster_001", rep
    assert "开场钩子" in rep["beats_declared"], rep
    assert "中点" not in rep["beats_declared"], rep
    assert rep["routing_fallback"] is False, rep


def test_normalize_bare_number_and_padded_forms():
    """CLUSTER_ID 形态容错：'001' / '1' / 'cluster_001' 都归一到 cluster_001（与 golden_three 同款）。"""
    for form in ("001", "1", "cluster_001"):
        rep = _scan_cluster(_CB_TWO, form)
        assert rep["cluster_id_evaluated"] == "cluster_001", (form, rep)
        assert rep["routing_fallback"] is False, (form, rep)


def test_cluster_010_routes_with_zero_padding():
    """两位以上 cluster：CLUSTER_ID=cluster_010 / '10' 都路由到 cluster_010（int 格式化 :03d 正确）。"""
    cb = {"cluster_001": _beats("a"), "cluster_010": _beats("x", "y")}
    for form in ("cluster_010", "10", "010"):
        rep = _scan_cluster(cb, form)
        assert rep["cluster_id_evaluated"] == "cluster_010", (form, rep)
        assert rep["beats_declared_count"] == 2, (form, rep)
        assert rep["routing_fallback"] is False, (form, rep)


# ============================================================
# [L105] 回退语义：CLUSTER_ID 指向无 beat / 缺失时如何标 routing_fallback
# ============================================================

def test_clusterid_present_but_no_beats_falls_back_and_flags():
    """CLUSTER_ID=cluster_003（cluster_beats 里没有/为空）→ 回退取第一个非空 key，
    且 routing_fallback=True（让消费者知道结果非该 cluster 权威，不被权威外观误导）。"""
    rep = _scan_cluster(_CB_TWO, "cluster_003")
    # 回退到第一个非空（dict 插入序：cluster_001）
    assert rep["cluster_id_evaluated"] == "cluster_001", rep
    assert rep["routing_fallback"] is True, rep


def test_clusterid_target_empty_list_falls_back():
    """CLUSTER_ID 指向的 cluster 存在但 beat 列表为空 → 视为无 beat → 回退 + flag。"""
    cb = {"cluster_001": _beats("a", "b"), "cluster_002": []}
    rep = _scan_cluster(cb, "cluster_002")
    assert rep["cluster_id_evaluated"] == "cluster_001", rep
    assert rep["routing_fallback"] is True, rep


def test_missing_clusterid_env_falls_back_without_flag():
    """CLUSTER_ID 未设（env 缺失）→ 回退取第一个非空 key，但 routing_fallback=False
    （无 env 透传不算「该被路由却没路由」，不向消费者发误导信号）。"""
    rep = _scan_cluster(_CB_TWO, None)
    assert rep["cluster_id_evaluated"] in ("cluster_001", "cluster_002"), rep
    assert rep["routing_fallback"] is False, rep


def test_empty_clusterid_string_falls_back_without_flag():
    """CLUSTER_ID='' (空串) 等价于缺失 → 回退不 flag。"""
    rep = _scan_cluster(_CB_TWO, "")
    assert rep["cluster_id_evaluated"] in ("cluster_001", "cluster_002"), rep
    assert rep["routing_fallback"] is False, rep


# ============================================================
# 健壮性：cluster_beats 全空 / 缺失 → 不崩，给 warning
# ============================================================

def test_all_clusters_empty_no_crash_warns():
    """cluster_beats 全空 → cluster_id_key=None + warning（不崩）。"""
    rep = _scan_cluster({"cluster_001": [], "cluster_002": []}, "cluster_002")
    assert rep["cluster_id_evaluated"] is None, rep
    assert rep["beats_declared_count"] == 0, rep
    assert rep["warning"] is not None, rep
    assert rep["routing_fallback"] is False, rep  # 无任何非空 key 可回退 → 非 fallback


def test_missing_beat_map_no_crash():
    """beat_map.json 缺失 → load_json 兜底 {} → cluster_beats={} → 不崩，warning。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        with _env(CLUSTER_MODE="1", CLUSTER_ID="cluster_001"):
            rep = pss.scan_beat(proj, 9000)
        assert rep["cluster_mode"] is True, rep
        assert rep["cluster_id_evaluated"] is None, rep
        assert rep["warning"] is not None, rep


# ============================================================
# 非 cluster 模式：chapter 视野不受本修复影响（无回归）
# ============================================================

def test_chapter_mode_unaffected():
    """CLUSTER_MODE 未设 → 走 chapter 视野（返回 percent_position/expected_beat），
    不含 cluster_mode/routing_fallback 字段（本修复零侵入 chapter 路径）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "进度.json").write_text(
            json.dumps({"total_chapters_planned": 200}, ensure_ascii=False), encoding="utf-8"
        )
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {}}, ensure_ascii=False), encoding="utf-8"
        )
        # 确保 CLUSTER_MODE 不为 "1"
        with _env(CLUSTER_MODE=None, CLUSTER_ID=None):
            rep = pss.scan_beat(proj, 10)
        assert "cluster_mode" not in rep, rep
        assert "routing_fallback" not in rep, rep
        assert "expected_beat" in rep, rep
        assert rep["chapter"] == 10, rep


# ============================================================
# 与 golden_three_scanner 同款归一化（一致性对账，若可 import）
# ============================================================

def test_normalization_matches_golden_three_pattern():
    """本修复的归一化 `id.replace('cluster_','').lstrip('0') or '0'` 与
    golden_three_scanner.py:287 同款——同形态输入得同 cluster。用公开行为对账。"""
    cb = {"cluster_001": _beats("a"), "cluster_002": _beats("b", "c")}
    # cluster_002 族（"2"/"002"/"cluster_002"）都路由到 cluster_002
    for form in ("2", "002", "cluster_002"):
        rep = _scan_cluster(cb, form)
        assert rep["cluster_id_evaluated"] == "cluster_002", (form, rep)
        assert rep["routing_fallback"] is False, (form, rep)


# ============================================================
# 零依赖 __main__ runner
# ============================================================

if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"[OK] {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed"
          + (f", {failed} FAILED" if failed else ""))
    sys.exit(1 if failed else 0)
