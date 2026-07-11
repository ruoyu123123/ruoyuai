#!/usr/bin/env python3
"""S1 manifest 分层 token 预算测试（2026-07-07 · PlotPilot context_budget_allocator 移植）。

钉死四层契约（零回归优先·build_manifest 是 200+ 测试覆盖的热文件）：
 1. 不触发既有 size 守卫 → 所有注入段逐字节不变（只新增 budget_report 元数据键）
 2. tier 归类全覆盖：build_manifest 每个顶层注入段必须在 SECTION_TIERS 有 tier
    （新段漏归类 → 本文件 test_tier_classification_full_coverage 测红）
 3. 触发守卫 → 分层裁序 T3(留5%地板)→T2→T1、T0 硬上限 40% 裁自身、
    compression_log 留痕（段名/tier/裁前后大小/理由）、最终体积压回上限内
 4. advisory 观测：T0 占比 > 20% 记 constraint_share_warning（只记录绝不裁）
env 覆盖：MANIFEST_BUDGET_HARD_KB / _T0_MAX_RATIO / _T3_MIN_RATIO / _T0_WARN_RATIO。
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import manifest_budget as mb  # noqa: E402
import manifest_compress as mc  # noqa: E402
import scaffold_subsystems as scaf  # noqa: E402


# ============ helpers ============

_BUDGET_ENV_KEYS = (
    "MANIFEST_BUDGET_HARD_KB", "MANIFEST_BUDGET_T0_MAX_RATIO",
    "MANIFEST_BUDGET_T3_MIN_RATIO", "MANIFEST_BUDGET_T0_WARN_RATIO",
)


class _budget_env:
    """临时设 MANIFEST_BUDGET_* env·退出时恢复（不污染其他测试）。"""

    def __init__(self, **kv):
        self.kv = {f"MANIFEST_BUDGET_{k}": str(v) for k, v in kv.items()}

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in _BUDGET_ENV_KEYS}
        for k in _BUDGET_ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(self.kv)

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _size(obj) -> int:
    return len(_dumps(obj).encode("utf-8"))


def _cjk_str(n: int) -> str:
    return "块" * n


def _scaffolded_probe_project(tmp: Path) -> Path:
    """能通过 preflight 的最小真项目（34 骨架 + 填实 cluster_001 brief + fate decision）。"""
    proj = tmp / "budget_probe_book"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    rc = scaf.cmd_emit([str(proj)])
    assert rc == 0, "scaffold emit 应成功"
    scene = {"ch": 1, "title": "开场", "characters": ["林川"],
             "key_events": ["主角进入副本"], "scene_type": ["悬疑"], "goal": "开场"}
    ec = json.loads((db / "事件簇.json").read_text(encoding="utf-8"))
    ec["clusters"] = [{"cluster_id": "cluster_001", "status": "in_progress",
                       "chapter_range": [1, 4], "scope_summary": "主角进入副本发现规则",
                       "scene_storyboard": [scene]}]
    (db / "事件簇.json").write_text(json.dumps(ec, ensure_ascii=False), encoding="utf-8")
    ch = json.loads((db / "人物卡.json").read_text(encoding="utf-8"))
    ch["characters"] = [{"name": "林川", "locked_facts": ["左手有疤"], "voice_pack": {}}]
    (db / "人物卡.json").write_text(json.dumps(ch, ensure_ascii=False), encoding="utf-8")
    mdir = db / ".manifest"
    mdir.mkdir(exist_ok=True)
    (mdir / "ch_001_fate_draw_decision.json").write_text(json.dumps({
        "_schema": "fate_draw_decision_v1", "producer": "auto_fate_draw.py",
        "status": "not_required", "reason": "测试项目无事件池抽签需求", "chapter": 1,
    }, ensure_ascii=False), encoding="utf-8")
    return proj


def _synthetic_manifest(**overrides) -> dict:
    """合成 manifest·全部用真实注入段名（保证 SECTION_TIERS 命中）。"""
    m = {
        "chapter": 1,
        "project": "合成书",
        "hard_constraints": [{"code": "LOCKED_FACT_CONFLICT", "message": "左手有疤不可变"}],
        "event_cluster_context": {"mode": "on", "scope_summary": "主角进入副本"},
        "world_state_snapshot": {"values": {"疫情": 3}},
        "time_snapshot": {"current_time": {"period": "夜"}},
        "rag_relevant_chapters": [],
        "memory_search_results": [],
        "selective_history_retrieval": None,
    }
    m.update(overrides)
    return m


# ============ 1) 不触发守卫 = 逐字节不变 ============

def test_no_trigger_sections_byte_identical():
    """默认 100KB 阈值下小 manifest 绝不裁：pop 掉 budget_report 后与原始逐字节相等。"""
    with _budget_env():
        manifest = _synthetic_manifest()
        baseline = _dumps(copy.deepcopy(manifest))
        out = mb.apply_budget(copy.deepcopy(manifest))
        report = out.pop("budget_report")
        assert _dumps(out) == baseline, "不触发守卫时所有注入段必须逐字节不变"
        assert report["budget"]["triggered"] is False
        assert report["compression_log"] == [], "未触发不得有任何裁剪记录"


def test_build_manifest_attaches_report_and_never_trims_small_project():
    """真项目集成：build_manifest 附 budget_report·小项目不触发·全 manifest 无裁剪痕迹。"""
    with tempfile.TemporaryDirectory() as tmp, _budget_env():
        proj = _scaffolded_probe_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        assert m["preflight"]["passed"] is True, "probe 项目必须过 preflight（否则测的是早退形态）"
        report = m.get("budget_report")
        assert isinstance(report, dict), "manifest 必须始终附 budget_report 元数据"
        assert report["budget"]["triggered"] is False
        assert report["compression_log"] == []
        assert "_budget_trimmed" not in _dumps(m), "不触发守卫绝不能出现裁剪 stub"


# ============ 2) tier 归类全覆盖 ============

def test_tier_classification_full_coverage():
    """build_manifest 每个顶层注入段都必须归 tier——新增段漏归类本测试测红。"""
    with tempfile.TemporaryDirectory() as tmp, _budget_env():
        proj = _scaffolded_probe_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        unknown = set(m.keys()) - set(mb.SECTION_TIERS) - mb.BUDGET_META_KEYS
        assert not unknown, (
            f"发现未归 tier 的 manifest 顶层注入段：{sorted(unknown)}。"
            "请在 core/scripts/manifest_budget.py 的 SECTION_TIERS 归类（T0/T1/T2/T3/META）"
        )
        assert m["budget_report"]["unclassified_sections"] == []


def test_early_return_manifest_keys_also_classified():
    """preflight-fail 早退形态的顶层键同样全部归 tier（裸骨架项目触发早退）。"""
    with tempfile.TemporaryDirectory() as tmp, _budget_env():
        proj = Path(tmp) / "bare_book"
        (proj / "_数据库").mkdir(parents=True)
        scaf.cmd_emit([str(proj)])
        m = bm.build_manifest(proj, 1)
        assert m["preflight"]["passed"] is False, "裸骨架应走早退形态"
        unknown = set(m.keys()) - set(mb.SECTION_TIERS) - mb.BUDGET_META_KEYS
        assert not unknown, f"早退形态存在未归 tier 键：{sorted(unknown)}"


def test_section_tiers_values_all_valid():
    valid = {mb.TIER_T0, mb.TIER_T1, mb.TIER_T2, mb.TIER_T3, mb.TIER_META}
    bad = {k: v for k, v in mb.SECTION_TIERS.items() if v not in valid}
    assert not bad, f"SECTION_TIERS 存在非法 tier 值：{bad}"


def test_unclassified_section_recorded_in_report():
    """未来新段若漏归类：记账不崩·按 T2 兜底·并记入 unclassified_sections 供覆盖测试抓红。"""
    with _budget_env():
        m = _synthetic_manifest()
        m["totally_new_section_xyz"] = {"payload": _cjk_str(20)}
        out = mb.apply_budget(m)
        report = out["budget_report"]
        assert report["unclassified_sections"] == ["totally_new_section_xyz"]
        assert report["sections"]["totally_new_section_xyz"]["tier"] == mb.TIER_T2


# ============ 3) budget_report 记账字段 ============

def test_budget_report_sections_have_tier_bytes_cjk_share():
    with tempfile.TemporaryDirectory() as tmp, _budget_env():
        proj = _scaffolded_probe_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        report = m["budget_report"]
        assert report["sections"], "sections 记账不应为空"
        share_sum = 0.0
        for name, rec in report["sections"].items():
            assert rec["tier"] in {mb.TIER_T0, mb.TIER_T1, mb.TIER_T2, mb.TIER_T3, mb.TIER_META}, name
            assert isinstance(rec["est_bytes"], int) and rec["est_bytes"] > 0, name
            assert isinstance(rec["est_cjk"], int) and rec["est_cjk"] >= 0, name
            assert 0.0 <= rec["share"] <= 1.0, name
            share_sum += rec["share"]
        assert abs(share_sum - 1.0) < 0.02, f"各段 share 之和应≈1，得 {share_sum:.4f}"
        tier_share_sum = sum(t["share"] for t in report["tier_totals"].values())
        assert abs(tier_share_sum - 1.0) < 0.02
        assert report["t0_share"] == report["tier_totals"][mb.TIER_T0]["share"]


# ============ 4) advisory：T0 占比观测（只记录不裁） ============

def test_constraint_share_warning_over_20_percent_records_but_never_trims():
    with _budget_env():
        m = _synthetic_manifest(hard_constraints=[{"message": _cjk_str(500)}])
        baseline = _dumps(copy.deepcopy(m))
        out = mb.apply_budget(m)
        report = out.pop("budget_report")
        assert report["t0_share"] > 0.20
        assert report["constraint_share_warning"], "T0 占比 >20% 必须记 constraint_share_warning"
        assert "注意力坍塌" in report["constraint_share_warning"]
        assert report["compression_log"] == [], "advisory 观测绝不触发裁剪"
        assert _dumps(out) == baseline, "advisory 观测下注入段仍逐字节不变"


def test_no_constraint_share_warning_under_threshold():
    with _budget_env():
        m = _synthetic_manifest(
            hard_constraints=[{"message": "短约束"}],
            event_cluster_context={"scope_summary": _cjk_str(800)},
        )
        report = mb.apply_budget(m)["budget_report"]
        assert report["t0_share"] <= 0.20
        assert report["constraint_share_warning"] is None


def test_warn_ratio_env_override():
    """MANIFEST_BUDGET_T0_WARN_RATIO 可覆盖观测阈值。"""
    m_kwargs = dict(hard_constraints=[{"message": _cjk_str(500)}])
    with _budget_env(T0_WARN_RATIO="0.95"):
        report = mb.apply_budget(_synthetic_manifest(**m_kwargs))["budget_report"]
        assert report["constraint_share_warning"] is None, "阈值提到 95% 后不应告警"
    with _budget_env(T0_WARN_RATIO="0.01"):
        report = mb.apply_budget(_synthetic_manifest(**m_kwargs))["budget_report"]
        assert report["constraint_share_warning"], "阈值压到 1% 后必须告警"


# ============ 5) 触发守卫 → 分层裁剪 ============

def test_env_hard_kb_override_controls_trigger():
    """同一 manifest：默认 100KB 不触发；MANIFEST_BUDGET_HARD_KB=1 触发。"""
    m_kwargs = dict(world_state_snapshot={"desc": _cjk_str(600)})
    with _budget_env():
        report = mb.apply_budget(_synthetic_manifest(**m_kwargs))["budget_report"]
        assert report["budget"]["triggered"] is False
    with _budget_env(HARD_KB="1"):
        report = mb.apply_budget(_synthetic_manifest(**m_kwargs))["budget_report"]
        assert report["budget"]["triggered"] is True
        assert report["budget"]["hard_kb"] == 1.0


def test_trim_order_t3_before_t2():
    """T3（长程记忆）是牺牲位：超预算先裁 T3，T2 够用就不动。"""
    with _budget_env(HARD_KB="6"):
        m = _synthetic_manifest(
            rag_relevant_chapters=[{"chapter": 2, "summary": _cjk_str(3000)}],
            memory_search_results=[{"hit": _cjk_str(3000)}],
            world_state_snapshot={"desc": _cjk_str(300)},
        )
        t2_baseline = _dumps(m["world_state_snapshot"])
        out = mb.apply_budget(m)
        log = out["budget_report"]["compression_log"]
        assert log, "超预算必须有裁剪记录"
        assert log[0]["tier"] == mb.TIER_T3, f"首裁必须落 T3，得 {log[0]}"
        assert all(e["tier"] == mb.TIER_T3 for e in log), (
            f"T3 裁够后不得动 T2/T1：{[(e['tier'], e['section']) for e in log]}")
        assert _dumps(out["world_state_snapshot"]) == t2_baseline, "T2 段应原样保留"


def test_t3_floor_holds_and_recovers_from_t2():
    """T3 已在 5% 地板下 → 不裁 T3（留 floor_hold 痕）·超额转 T2 回收。"""
    with _budget_env(HARD_KB="4"):
        m = _synthetic_manifest(
            rag_relevant_chapters=[{"chapter": 2, "summary": _cjk_str(30)}],  # T3 小于地板
            world_state_snapshot={"desc": _cjk_str(2000)},                    # T2 大头
            character_positions=[{"name": "林川", "pos": _cjk_str(1200)}],    # T2 大头
        )
        t3_baseline = _dumps(m["rag_relevant_chapters"])
        out = mb.apply_budget(m)
        log = out["budget_report"]["compression_log"]
        actions = [e["action"] for e in log]
        assert "t3_floor_hold" in actions, f"T3 触地板必须留 floor_hold 痕：{actions}"
        assert _dumps(out["rag_relevant_chapters"]) == t3_baseline, "地板下的 T3 段不许动"
        assert any(e["tier"] == mb.TIER_T2 and e["action"] != "t3_floor_hold" for e in log), \
            "超额必须转 T2 回收"


def test_t0_hard_cap_trims_t0_itself():
    """T0 超总预算 40% 硬上限 → 裁 T0 自身（其余场景 T0 永不进低层裁剪队列）。"""
    with _budget_env(HARD_KB="4"):
        m = _synthetic_manifest(
            hard_constraints=[{"code": f"C{i}", "message": _cjk_str(120)} for i in range(20)],
        )
        out = mb.apply_budget(m)
        log = out["budget_report"]["compression_log"]
        t0_entries = [e for e in log if e["action"].startswith("t0_cap")]
        assert t0_entries, f"T0 超 40% 上限必须裁 T0 自身：{log}"
        cap = int(4 * 1024 * 0.40)
        t0_bytes = out["budget_report"]["tier_totals"][mb.TIER_T0]["bytes"]
        # tier_totals 含键名开销·给 10% 容差
        assert t0_bytes <= cap * 1.1, f"T0 裁后 {t0_bytes}B 应压回上限 {cap}B 内"


def test_t0_not_trimmed_when_under_cap_even_if_over_budget():
    """总量超预算但 T0 未超 40%：T0 段逐字节不变（裁剪只落 T3/T2/T1）。"""
    with _budget_env(HARD_KB="4"):
        m = _synthetic_manifest(
            hard_constraints=[{"code": "C1", "message": "小约束"}],
            world_state_snapshot={"desc": _cjk_str(2500)},
            rag_relevant_chapters=[{"summary": _cjk_str(1500)}],
        )
        t0_baseline = _dumps(m["hard_constraints"])
        out = mb.apply_budget(m)
        assert out["budget_report"]["compression_log"], "应有低层裁剪发生"
        assert _dumps(out["hard_constraints"]) == t0_baseline, "未超 cap 的 T0 绝不能被裁"
        assert all(e["tier"] != mb.TIER_T0 for e in out["budget_report"]["compression_log"])


def test_compression_log_records_before_after_and_reason():
    with _budget_env(HARD_KB="4"):
        m = _synthetic_manifest(
            world_state_snapshot={"desc": _cjk_str(2500)},
            rag_relevant_chapters=[{"summary": _cjk_str(1500)}],
        )
        log = mb.apply_budget(m)["budget_report"]["compression_log"]
        assert log
        for e in log:
            assert e["section"] and e["tier"] and e["action"] and e["reason"], e
            if e["action"] != "t3_floor_hold":
                assert e["after_bytes"] < e["before_bytes"], f"裁剪必须有真实收益：{e}"


def test_final_size_within_hard_budget_after_trim():
    """裁剪后含 budget_report 元数据的最终体积必须压回硬上限内（stub 兜底保证收敛）。"""
    with _budget_env(HARD_KB="8"):
        m = _synthetic_manifest(
            world_state_snapshot={f"k{i}": _cjk_str(100) for i in range(30)},
            character_positions=[{"pos": _cjk_str(80)} for _ in range(30)],
            rag_relevant_chapters=[{"summary": _cjk_str(80)} for _ in range(30)],
        )
        out = mb.apply_budget(m)
        assert _size(out) <= 8 * 1024, f"裁后总体积 {_size(out)}B 应 <= 8192B"
        assert "_budget_trimmed" in _dumps(out), "深度超预算应出现整段 stub 让位"


def test_meta_sections_never_trimmed():
    """META（身份/机器元数据）永不进裁剪队列。"""
    with _budget_env(HARD_KB="2"):
        m = _synthetic_manifest(
            writer_mode="claude_draft_gemini_polish_v29",
            _cache_layout={"STATIC_99_cacheable": ["deep_writing_dims"], "_doc": _cjk_str(100)},
            world_state_snapshot={"desc": _cjk_str(2000)},
        )
        meta_baseline = (_dumps(m["writer_mode"]), _dumps(m["_cache_layout"]))
        out = mb.apply_budget(m)
        assert out["budget_report"]["compression_log"], "应触发裁剪"
        assert (_dumps(out["writer_mode"]), _dumps(out["_cache_layout"])) == meta_baseline
        assert all(e["tier"] != mb.TIER_META for e in out["budget_report"]["compression_log"])


# ============ 6) 与 manifest_compress 的口径 ============

def test_compressed_version_drops_budget_report():
    """gen-model 用的 compressed 版剥掉 budget_report（人看的记账元数据非创作载荷）。"""
    with _budget_env():
        out = mb.apply_budget(_synthetic_manifest())
        compressed = mc.compress(out)
        assert "budget_report" not in compressed
        assert "budget_report" in mc.DROP_TOP_LEVEL_KEYS


if __name__ == "__main__":
    fails = 0
    for nm in sorted(list(globals())):
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
