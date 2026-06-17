#!/usr/bin/env python3
"""run_cross_cluster_aggregates.py 聚焦确定性回归测试（零 LLM / 零联网 / 零子进程）。

定位：现有 tests/test_cross_cluster_contract.py 已覆盖 SCAN_TIERS 注册表契约、
端到端 subprocess 跑（minimal_5 / full_18）、FATAL exit 2、graceful degrade、
以及 get_cluster_last_ch / chapters_in_last_n_clusters 的「基本」分支。

本文件**不重复**那些，专钉它们尚未覆盖的纯逻辑分支：
  ① get_intensity —— 全新（contract 零覆盖）：缺文件回退 / 解析失败回退 /
     缺 quality_control 段回退 / 正常读取自定义 intensity 值。
  ② get_cluster_last_ch —— 补 contract 未覆盖的 string 形态 chapter_range（"5-9"）
     与无 chapter_range 字段返回 None 的分支。
  ③ chapters_in_last_n_clusters —— 补细分支：目标 cluster 即便 status 不在落章表内也纳入；
     非 dict 条目 / 无 chapter_range 条目被跳过；按末章排序取最近 N 个累计 span；
     下限 4 钳制；string 形态 chapter_range 在此函数**不被支持**（只认 list）。
  ④ tier→scanner 集合组装算法（main L184-189 的纯逻辑）+ minimal_5→core_5 归一，
     直接对 SCAN_TIERS 复算，锁「core_5 ⊂ core_10 ⊂ full_18」的累进契约。

零依赖范式：标准库 only，test_* 无参数，断言失败 raise AssertionError，
文件尾 __main__ 循环打 [OK]/[FAIL]（照 test_cross_cluster_contract.py）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import run_cross_cluster_aggregates as rcca  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _mk_proj(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_prefs(proj: Path, text: str):
    (proj / "_数据库" / "用户偏好.json").write_text(text, encoding="utf-8")


def _write_shijianji(proj: Path, clusters):
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters},
                   ensure_ascii=False),
        encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# ① get_intensity —— 全新覆盖
# ══════════════════════════════════════════════════════════════════════════
def test_get_intensity_missing_file_defaults_full_18():
    """无 用户偏好.json → 默认 full_18。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))  # 不写 prefs
        assert rcca.get_intensity(proj) == "full_18"


def test_get_intensity_reads_custom_value():
    """正常读取 quality_control.cross_chapter_scan_intensity 自定义值。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_prefs(proj, json.dumps(
            {"quality_control": {"cross_chapter_scan_intensity": "core_10"}},
            ensure_ascii=False))
        assert rcca.get_intensity(proj) == "core_10"
        # off 也应原样读出（由 main 决定怎么处理，本函数只取值）
        _write_prefs(proj, json.dumps(
            {"quality_control": {"cross_chapter_scan_intensity": "off"}},
            ensure_ascii=False))
        assert rcca.get_intensity(proj) == "off"


def test_get_intensity_missing_quality_control_section_defaults():
    """有文件但缺 quality_control 段 → 回退 full_18（(prefs.get(...) or {}) 守卫）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_prefs(proj, json.dumps({"other": 1}, ensure_ascii=False))
        assert rcca.get_intensity(proj) == "full_18"
        # quality_control 显式为 null → `or {}` 兜底，仍 full_18（不抛 AttributeError）
        _write_prefs(proj, json.dumps({"quality_control": None}, ensure_ascii=False))
        assert rcca.get_intensity(proj) == "full_18"


def test_get_intensity_missing_intensity_key_defaults():
    """有 quality_control 段但缺 cross_chapter_scan_intensity 键 → 默认 full_18。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_prefs(proj, json.dumps(
            {"quality_control": {"other_knob": True}}, ensure_ascii=False))
        assert rcca.get_intensity(proj) == "full_18"


def test_get_intensity_malformed_json_defaults():
    """损坏 JSON → except 兜底 full_18（不抛、不崩流水线）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_prefs(proj, "{ this is not : valid json ]")
        assert rcca.get_intensity(proj) == "full_18"


# ══════════════════════════════════════════════════════════════════════════
# ② get_cluster_last_ch —— 补 string 形态 + 无 range 分支
# ══════════════════════════════════════════════════════════════════════════
def test_get_cluster_last_ch_string_range_form():
    """chapter_range 为字符串 "5-9" 形态 → 解析取末章 9（contract 只测 list 形态）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_shijianji(proj, [
            {"cluster_id": "cluster_001", "chapter_range": "1-4"},
            {"cluster_id": "cluster_002", "chapter_range": "5-9"},
        ])
        assert rcca.get_cluster_last_ch(proj, "002") == 9
        assert rcca.get_cluster_last_ch(proj, "cluster_001") == 4


def test_get_cluster_last_ch_no_range_returns_none():
    """匹配到 cluster 但它无 chapter_range（既非 list 也非含 '-' 的 str）→ None。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_shijianji(proj, [
            {"cluster_id": "cluster_002", "title": "无区间"},  # 无 chapter_range
        ])
        assert rcca.get_cluster_last_ch(proj, "002") is None


def test_get_cluster_last_ch_missing_file_returns_none():
    """无 事件簇.json → None。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))  # 不写文件
        assert rcca.get_cluster_last_ch(proj, "002") is None


# ══════════════════════════════════════════════════════════════════════════
# ③ chapters_in_last_n_clusters —— 补细分支
# ══════════════════════════════════════════════════════════════════════════
def test_window_target_included_even_if_status_unknown():
    """目标 cluster 即便 status 不在 landed_statuses 内也纳入（它正在被处理）。
    构造：前一个 cluster 是 candidate（status 不落账，且非目标）→ 被排除；
    目标 cluster_002 status='草稿'（不在表内）→ 因 is_target 仍纳入。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_shijianji(proj, [
            {"cluster_id": "cluster_001", "status": "candidate",
             "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "status": "草稿",
             "chapter_range": [4, 8]},  # span = 5
        ])
        # candidate(001) 非目标且 status 不落账 → 排除；只剩目标 002 span=5
        assert rcca.chapters_in_last_n_clusters(proj, "002", 2) == 5


def test_window_excludes_unlanded_non_target_cluster():
    """非目标 + status 不在落章表 → 被排除，不计入窗口累计。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_shijianji(proj, [
            {"cluster_id": "cluster_001", "status": "已完成",
             "chapter_range": [1, 5]},   # landed, span 5
            {"cluster_id": "cluster_002", "status": "planned",
             "chapter_range": [6, 99]},  # 非目标 + 未落章 → 排除（哪怕 span 巨大）
            {"cluster_id": "cluster_003", "status": "in_progress",
             "chapter_range": [6, 8]},   # landed/target, span 3
        ])
        # 取最近 2 个落章 cluster：003(span3) + 001(span5) = 8；002 被排除
        assert rcca.chapters_in_last_n_clusters(proj, "003", 2) == 8


def test_window_recent_n_selection_by_hi_chapter():
    """按末章排序取最近 N 个累计 span（不是简单取前 N / 全量）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_shijianji(proj, [
            {"cluster_id": "cluster_001", "status": "已完成", "chapter_range": [1, 4]},   # span 4, hi 4
            {"cluster_id": "cluster_002", "status": "已完成", "chapter_range": [5, 10]},  # span 6, hi 10
            {"cluster_id": "cluster_003", "status": "已完成", "chapter_range": [11, 13]}, # span 3, hi 13
        ])
        # 最近 2 个（按 hi 排序末尾）= 003(3) + 002(6) = 9；001 被挤出
        assert rcca.chapters_in_last_n_clusters(proj, "003", 2) == 9
        # n=3 全取 = 4+6+3 = 13
        assert rcca.chapters_in_last_n_clusters(proj, "003", 3) == 13


def test_window_floor_4_clamp():
    """单个短 cluster（span < 4）→ 累计被 max(4, ...) 钳到 4。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_shijianji(proj, [
            {"cluster_id": "cluster_001", "status": "已完成",
             "chapter_range": [1, 2]},  # span 2
        ])
        assert rcca.chapters_in_last_n_clusters(proj, "001", 1) == 4


def test_window_skips_non_dict_and_no_range_entries():
    """clusters 列表里混入非 dict 条目 / 无 chapter_range 的 dict → 全部跳过不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_shijianji(proj, [
            "我是脏数据字符串",                                   # 非 dict → skip
            {"cluster_id": "cluster_x", "status": "已完成"},      # 无 chapter_range → skip
            {"cluster_id": "cluster_001", "status": "已完成",
             "chapter_range": [1, 6]},                            # 唯一有效, span 6
        ])
        assert rcca.chapters_in_last_n_clusters(proj, "001", 2) == 6


def test_window_string_range_not_supported_returns_none():
    """与 get_cluster_last_ch 不同：本函数只认 list 形态 chapter_range，
    全是 string 形态 → 无有效条目 → landed 为空 → 返回 None（钉死这一行为差异）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))
        _write_shijianji(proj, [
            {"cluster_id": "cluster_001", "status": "已完成", "chapter_range": "1-6"},
        ])
        assert rcca.chapters_in_last_n_clusters(proj, "001", 2) is None


def test_window_missing_file_returns_none():
    """无 事件簇.json → None（调用方回退 --last-n 默认）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d))  # 不写文件
        assert rcca.chapters_in_last_n_clusters(proj, "001", 2) is None


# ══════════════════════════════════════════════════════════════════════════
# ④ tier→scanner 集合组装算法（main L184-189 纯逻辑复算）
# ══════════════════════════════════════════════════════════════════════════
def _scanners_for(intensity: str):
    """复刻 main 内 L176-189 的集合组装逻辑（含 minimal_5→core_5 归一）。"""
    if intensity == "minimal_5":
        intensity = "core_5"
    scanners = list(rcca.SCAN_TIERS["core_5"])
    if intensity in ("core_10", "full_18"):
        scanners.extend(rcca.SCAN_TIERS["core_10_extra"])
    if intensity == "full_18":
        scanners.extend(rcca.SCAN_TIERS["full_18_extra"])
    return scanners


def test_tier_set_composition_is_cumulative():
    """core_5 ⊂ core_10 ⊂ full_18 的累进契约：
    core_5=5 个、core_10=10 个、full_18=全集合，且每级是上一级的真超集。"""
    s5 = _scanners_for("core_5")
    s10 = _scanners_for("core_10")
    s18 = _scanners_for("full_18")
    assert len(s5) == 5, s5
    assert len(s10) == 10, s10
    assert set(s5).issubset(set(s10)), "core_10 不是 core_5 的超集"
    assert set(s10).issubset(set(s18)), "full_18 不是 core_10 的超集"
    assert len(s18) == len(s5) + len(rcca.SCAN_TIERS["core_10_extra"]) \
        + len(rcca.SCAN_TIERS["full_18_extra"])
    # 无重复（组装不会把同一 scanner 跑两遍）
    assert len(s18) == len(set(s18)), "full_18 组装出重复 scanner"


def test_tier_minimal_5_normalizes_to_core_5():
    """minimal_5 是 core_5 的对外别名 → 组装出与 core_5 完全相同的集合。"""
    assert _scanners_for("minimal_5") == _scanners_for("core_5")


def test_tier_core_5_excludes_extra_scanners():
    """core_5 不含 core_10_extra / full_18_extra 任何 scanner（紧急快速路径只跑最关键 5 个）。"""
    s5 = set(_scanners_for("core_5"))
    assert s5.isdisjoint(set(rcca.SCAN_TIERS["core_10_extra"]))
    assert s5.isdisjoint(set(rcca.SCAN_TIERS["full_18_extra"]))
    # 且恰好是 SCANNERS_WITH_CH 的超集（fate_drift 在 core_5 内 → --ch 分支可命中）
    assert rcca.SCANNERS_WITH_CH.issubset(s5)


# ──────────────────────────────────────────────────────────────────────────
# 零依赖 runner
# ──────────────────────────────────────────────────────────────────────────
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
