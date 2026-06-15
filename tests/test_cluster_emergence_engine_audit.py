"""cluster_emergence_engine 健壮性审计回归（2026-06 triage worth_fixing）。

钉死 triage L201 修复（cur_vol 收敛维度复活 + 收敛源字段兼容）：

  根因（审计实证）：
    1. `_current_advancing_vol(world_state, character_arc)` 在全部真实生产项目恒返回 0
       —— world_state / character_arc 都无人写 current_vol / advancing_vol（producer 契约债）。
       旧代码 `cur_vol = _current_advancing_vol(...)` → cur_vol=0 → 收敛维度(dim-6)永不加分。
    2. dim-6 的旧源字段 key_milestones / ending_state 当前 producer(gen_creative) 从不写，
       真实 volume schema 用 volume_core_conflict / volume_thread / volume_finale_signal。
       即便复活 cur_vol，只读旧字段仍 no-op。

  修复（北极星⑤克制·纯 advisory 排序·永不崩·最终走向卡仍由用户选）：
    A. cur_vol 兜底用 emerge 已算出的 current_volume（=剩余 ME 最小卷号·权威 _me_volume）。
    B. 收敛源同时捞 ending_state + volume_core_conflict + volume_thread + volume_finale_signal。

  可观测信号：候选 brief 的 _emergence_reasons / scope_summary 含「大势收敛：推进未达成卷里程碑」。

零依赖范式：文件尾 __main__ 循环跑所有 test_·import 被测模块·不引 pytest。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_emergence_engine as cee  # noqa: E402


# ───────────────────── 0. 根因前提：契约债下 _current_advancing_vol 恒 0 ─────────────────────

def test_current_advancing_vol_zero_on_contract_debt():
    """生产实证前提：world_state/character_arc 不写 current_vol → 返回 0（=旧 dim-6 哑火根因）。"""
    assert cee._current_advancing_vol({}, {}) == 0
    # 仅有无关键值也是 0（不是任意 truthy 都算卷号）
    assert cee._current_advancing_vol({"factions_state": {"甲": 5}}, {"arcs": {}}) == 0


def test_current_advancing_vol_explicit_still_wins():
    """显式写了 current_vol 时仍优先返回它（兜底不抢占已有真值）。"""
    assert cee._current_advancing_vol({"current_vol": 2}, {}) == 2
    assert cee._current_advancing_vol({"advancing_vol": "3"}, {}) == 3


# ───────────────────── 测试夹具 ─────────────────────

def _mk_project(tmp: Path, *, major_events: list, volumes: list,
                world_state: dict = None, clusters: list = None) -> Path:
    """造一个最小项目：大势卡(major_events + volumes) / 事件簇 / 世界状态 / arc。"""
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "大势卡.json").write_text(
        json.dumps({"schema_version": "v27", "major_events": major_events,
                    "volumes": volumes}, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters or []},
                   ensure_ascii=False), encoding="utf-8")
    (db / "世界状态.json").write_text(
        json.dumps(world_state or {}, ensure_ascii=False), encoding="utf-8")
    (db / "character_arc_state.json").write_text(
        json.dumps({}, ensure_ascii=False), encoding="utf-8")
    return tmp


def _read_emergence(result) -> dict:
    return json.loads(Path(result["emergence_path"]).read_text(encoding="utf-8"))


def _candidate_by_me(em: dict, me_id: str) -> dict:
    for c in em["candidates"]:
        if c["parent_me"] == me_id:
            return c
    raise AssertionError(f"候选中找不到 ME {me_id}：{[c['parent_me'] for c in em['candidates']]}")


_CONV_REASON = "大势收敛"  # 收敛维度命中的理由标记（dim-6）


# 收敛关键词集中在「玄铁令／立序殿」这种具体名词上（_keyword_set 走中文 2-gram 命中）
_VOL_NEW_FIELDS = [
    {
        "vol": 1,
        "title": "立序殿风波",
        # 🔴 只填【新 producer 实写字段】，故意不给 key_milestones / ending_state
        "volume_core_conflict": "夺回玄铁令镇压立序殿叛乱",
        "volume_thread": "玄铁令的下落贯穿本卷",
        "volume_finale_signal": "立序殿主现身",
    },
]

_VOL_LEGACY_FIELDS = [
    {
        "vol": 1,
        "title": "立序殿风波",
        # 🔴 只填【旧 schema 字段】，验证旧路径不回归
        "key_milestones": ["夺回玄铁令", "镇压立序殿叛乱"],
        "ending_state": "玄铁令归位",
    },
]


# 一个强烈呼应收敛词、一个完全无关——用来看收敛维度有没有把呼应的那个加分
_ME_POOL = [
    {"id": "ME-V1-01", "volume": 1, "title": "无关支线",
     "description": "主角在市集买菜遇到旧识闲聊家常", "status": "pending"},
    {"id": "ME-V1-02", "volume": 1, "title": "玄铁令争夺",
     "description": "围绕玄铁令的下落与立序殿正面冲突", "status": "pending"},
]


# ───────────────────── 1. 核心修复：兜底 current_volume 让收敛维度复活 ─────────────────────

def test_convergence_alive_via_current_volume_fallback_new_fields():
    """world_state 无 current_vol(契约债) + volume 只有新 producer 字段 →
    修复后 cur_vol 兜底用 current_volume(=1)，收敛源捞 volume_core_conflict 等 →
    呼应「玄铁令/立序殿」的候选拿到收敛加分(dim-6 不再哑火)。"""
    with tempfile.TemporaryDirectory() as d:
        # 世界状态故意不写 current_vol —— 复刻所有真实项目的契约债
        root = _mk_project(Path(d), major_events=_ME_POOL, volumes=_VOL_NEW_FIELDS,
                           world_state={"factions_state": {}})
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True, r
        em = _read_emergence(r)
        assert em["current_volume"] == 1
        conv_cand = _candidate_by_me(em, "ME-V1-02")
        reasons_text = " ".join(conv_cand.get("_emergence_reasons") or [])
        # 🔴 修复前：cur_vol=0 → 此 finding 不存在；修复后应出现收敛理由
        assert _CONV_REASON in reasons_text, (
            f"收敛维度应命中(新字段源)，实际理由={conv_cand.get('_emergence_reasons')}")
        # 收敛理由也会透传进 scope_summary（给用户看「为什么涌现」）
        assert _CONV_REASON in conv_cand["scope_summary"]


def test_convergence_alive_legacy_fields_no_regression():
    """旧 schema(key_milestones/ending_state)在兜底 current_volume 下仍能命中收敛
    —— 证明改动只是【新增】新字段源，没有砍掉旧字段（向后兼容）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), major_events=_ME_POOL, volumes=_VOL_LEGACY_FIELDS,
                           world_state={})
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True, r
        em = _read_emergence(r)
        conv_cand = _candidate_by_me(em, "ME-V1-02")
        reasons_text = " ".join(conv_cand.get("_emergence_reasons") or [])
        assert _CONV_REASON in reasons_text, (
            f"旧字段源应仍命中收敛，实际={conv_cand.get('_emergence_reasons')}")


def test_unrelated_candidate_no_convergence_bonus():
    """与卷收敛词毫无重叠的候选不应被误加收敛分（守住 advisory 不乱报·防假阳）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), major_events=_ME_POOL, volumes=_VOL_NEW_FIELDS,
                           world_state={})
        r = cee.emerge_next_cluster(root, "cluster_001")
        em = _read_emergence(r)
        unrelated = _candidate_by_me(em, "ME-V1-01")  # 买菜闲聊·无收敛词
        reasons_text = " ".join(unrelated.get("_emergence_reasons") or [])
        assert _CONV_REASON not in reasons_text, (
            f"无关候选不该有收敛理由，实际={unrelated.get('_emergence_reasons')}")


def test_no_volumes_block_does_not_crash():
    """大势卡无 volumes 块时收敛维度安全降级（milestone_kw 为空·不崩·仍出候选）。
    守北极星⑤：advisory 维度缺数据时静默让位，绝不阻断涌现。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "大势卡.json").write_text(
            json.dumps({"schema_version": "v27", "major_events": _ME_POOL},
                       ensure_ascii=False), encoding="utf-8")  # 没有 volumes 键
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": []}, ensure_ascii=False), encoding="utf-8")
        (db / "世界状态.json").write_text("{}", encoding="utf-8")
        (db / "character_arc_state.json").write_text("{}", encoding="utf-8")
        r = cee.emerge_next_cluster(Path(d), "cluster_001")
        assert r["ok"] is True, r
        em = _read_emergence(r)
        # 仍有候选（涟漪/arc/faction 维度照常）·收敛维度只是没贡献而已
        assert em["candidates"]


# ───────────────────── 零依赖 runner ─────────────────────

def _run_all():
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"[OK] {fn.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
