"""C11 cluster_changes 幂等重放回归测试 — 钉死「N 章 apply 不再 N 倍污染」。

🔴 2026-06-27 C11（约束 C11-CLUSTER-CHANGES-IDEMPOTENT-REPLAY）

根因：split_cluster_changes v1 把整 cluster 的 factual 平铺进每章 _changes.json，apply
流程对 cluster 内 N 章逐章重放 →
  · 时间线 time_log        被 append N 次（save_state.apply_changes）
  · NPC thread responded_count 被 +N（world_evolution_apply_chapter.respond_threads）
  · writer 撒谎检测逐章验 opening/ending/anchors → 平铺申报 vs 各章实际 → 海量假撒谎

🔴 2026-06-28 审计清理B类：原「人物卡 growth_arc 被 append N 次」一项已不再适用——
save_state.apply_changes 不再消费 writer changes.character_changes 写 growth_arc（属 B 类违规：
读 writer 自报 factual 当权威状态源）。角色状态改由 novel-archivist 读正文产 archive →
apply_archive.py 写 人物卡/state_log。本文件原 3 个 growth_arc 幂等用例改为「character_changes
现为 no-op」的新契约用例；time_log（save_state 仍保留 time_advance）/ respond_threads /
撒谎检测 / db_schema_validate 不变量不受影响（growth_arc 不变量仍校验 archive 写入的数据）。

🔴 2026-07 加固批：save_state.apply_changes 对 进度.json 从「缺失静默建新/损坏跳过」改为
RuntimeError 硬停（不兼容不降级）；current_time 记 cluster/chapter_in_cluster 不再记 chapter；
_resolve_cluster 反查不到直接抛错（禁按章号推断）。脚手架相应建 进度.json，新硬契约由
test_apply_changes_requires_progress_json 钉死。

修复后不变量（本文件钉死）：
  1. writer changes.character_changes → 人物卡 不再写 growth_arc（B 类清理·archive 接管）
  2. N 章 apply 后 time_log 累积条目数 == 逻辑时间推进数
  3. 同 cluster N 章呼应同一 thread → responded_count == 1（下个 cluster 真呼应才合法 +1）
  4. cluster 级撒谎检测 lies == 真实违规数（opening 验首章 / ending 验末章 / anchors 验全拼接）
  5. db_schema_validate 幂等不变量为 advisory（污染 → warning · 退出码不变）

「先写失败测试复现 N 倍污染再 fix 转绿」：本文件断言均为 fix 后的正确值，pre-fix 状态
（无 _source_cluster 去重 / 无 responded_by_cluster / 逐章 truth_check）会令断言全红。

零依赖范式：仅标准库；test_* 无参；失败 raise AssertionError；文件尾 __main__ 跑全部。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state as ss  # noqa: E402
import world_evolution_apply_chapter as wac  # noqa: E402
import writer_truth_check as wtc  # noqa: E402
import db_schema_validate as dsv  # noqa: E402


# ═══════════════════════ 公共脚手架 ═══════════════════════

def _wj(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load(root: Path, name: str) -> dict:
    return json.loads((root / "_数据库" / name).read_text(encoding="utf-8"))


def _setup_cluster_project(root: Path, ch_range, cluster_id="cluster_001"):
    """建一个最小项目：事件簇.json 含 chapter_range（让 ch_to_cluster_id 把 N 章归一到同 cluster）。

    chapter_range 必须是 [lo, hi] 两元（ch_to_cluster_id 用 rng[0]<=ch<=rng[1] 判定）。

    🔴 2026-07 加固批新契约：save_state.apply_changes 对 进度.json 不再「缺失即静默建新/
    损坏即跳过」——缺失或损坏直接 RuntimeError 硬停（不降级：required 就是 required）。
    因此最小脚手架必须建 进度.json，否则 apply_changes 抛错（该硬契约由
    test_apply_changes_requires_progress_json 单独钉死）。
    """
    chs = list(ch_range)
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    _wj(db / "事件簇.json", {"clusters": [
        {"cluster_id": cluster_id, "chapter_range": [chs[0], chs[-1]]}]})
    _wj(db / "进度.json", {"schema_version": "v2", "completed": 0, "current": 1,
                           "cluster_blueprint": {}})
    return root


def _write_parsed(root: Path, ch: int, changes: dict) -> None:
    """直写 .wal/第{ch}章_parsed.json（apply_changes 的输入·绕过 cmd_parse 走纯落地路径）。"""
    _wj(root / "_数据库" / ".wal" / f"第{ch}章_parsed.json",
        {"strategy": "test", "changes": changes})


def _write_body(root: Path, ch: int, body: str) -> None:
    p = root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _write_changes_json(root: Path, ch: int, changes: dict) -> None:
    _wj(root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", changes)


# ═══════════════════════ 1. character_changes → growth_arc 已下线（B 类清理新契约） ═══════════════════════

def test_writer_character_changes_no_longer_writes_growth_arc():
    """🔴 2026-06-28 审计清理B类：writer changes.character_changes 不再写 人物卡.growth_arc。
    N 章重放同一份 character_changes → 人物卡 角色条目完全不变（无 growth_arc 字段被创建）。
    角色状态改由 novel-archivist → apply_archive.py 写（archive 权威·非 writer 自报）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_cluster_project(Path(d), range(1, 5))
        _wj(root / "_数据库" / "人物卡.json",
            {"schema_version": "v2", "characters": [{"id": "lin", "name": "林潜", "role": "主角"}]})
        cc = {"name": "林潜", "field": "心境", "from": "怯懦", "to": "决绝", "trigger": "目睹灭门"}
        for ch in (1, 2, 3, 4):  # 平铺：每章同一份 character_changes
            _write_parsed(root, ch, {"character_changes": [cc]})
            assert ss.apply_changes(root, ch) == 0
        char = _load(root, "人物卡.json")["characters"][0]
        assert "growth_arc" not in char, f"character_changes 不应再写 growth_arc，实得 {char.get('growth_arc')}"


def test_writer_new_entities_no_longer_registers_character():
    """🔴 2026-06-28 审计清理B类：writer changes.new_entities.characters 不再注册进 人物卡。
    新角色改由 archive（apply_archive.py）建卡。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_cluster_project(Path(d), range(1, 5))
        _wj(root / "_数据库" / "人物卡.json", {"schema_version": "v2", "characters": []})
        _write_parsed(root, 1, {"new_entities": {"characters": [{"name": "陌生剑客", "role": "配角"}]}})
        assert ss.apply_changes(root, 1) == 0
        assert _load(root, "人物卡.json")["characters"] == [], "new_entities 不应再注册新角色"


def test_apply_changes_requires_progress_json():
    """🔴 2026-07 加固批回归锁：进度.json 缺失 → apply_changes RuntimeError 硬停。

    历史：旧实现「不存在则 progress = {} 静默建新 / 损坏则跳过进度更新」，属降级容忍——
    已按「不兼容不降级」整体清除。本用例钉死新契约防复活：缺 进度.json 不允许静默建新库。
    """
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        _wj(db / "事件簇.json", {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 4]}]})
        # 故意不建 进度.json
        _write_parsed(root, 1, {"time_advance": {"elapsed": "一日", "key_events": ["启程"]}})
        try:
            ss.apply_changes(root, 1)
        except RuntimeError as e:
            assert "进度.json" in str(e), f"报错应指明 进度.json，实得 {e}"
        else:
            raise AssertionError("进度.json 缺失时 apply_changes 应 RuntimeError 硬停（禁静默建新）")
        assert not (db / "进度.json").exists(), "硬停路径不得顺手创建 进度.json（防降级复活）"


# ═══════════════════════ 2. time_log 幂等去重 ═══════════════════════

def test_time_log_no_n_fold_pollution():
    """同 cluster 4 章重放同一份 time_advance → time_log 只留 1 条。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_cluster_project(Path(d), range(1, 5))
        _wj(root / "_数据库" / "时间线.json", {"current_time": {"period": "黎明", "cluster": "cluster_001"}, "time_log": []})
        ta = {"elapsed": "三日", "key_events": ["渡江", "夜袭"], "period": "深夜"}
        for ch in (1, 2, 3, 4):
            _write_parsed(root, ch, {"time_advance": ta})
            assert ss.apply_changes(root, ch) == 0
        tl = _load(root, "时间线.json")["time_log"]
        assert len(tl) == 1, f"time_log 期望 1 条，实得 {len(tl)} 条 → N 倍污染未修"
        assert tl[0]["_source_cluster"] == "cluster_001"
        assert tl[0]["key_events"] == ["渡江", "夜袭"]
        cur = _load(root, "时间线.json")["current_time"]
        assert cur["cluster"] == "cluster_001"
        assert "chapter" not in cur


def test_time_log_distinct_advances_both_kept():
    """同 cluster 内两次不同时间推进（elapsed 不同）→ 两条都留。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_cluster_project(Path(d), range(1, 5))
        _wj(root / "_数据库" / "时间线.json", {"current_time": {"period": "黎明", "cluster": "cluster_001"}, "time_log": []})
        _write_parsed(root, 1, {"time_advance": {"elapsed": "一日", "key_events": ["启程"]}})
        ss.apply_changes(root, 1)
        _write_parsed(root, 2, {"time_advance": {"elapsed": "三日", "key_events": ["抵达"]}})
        ss.apply_changes(root, 2)
        tl = _load(root, "时间线.json")["time_log"]
        assert len(tl) == 2, f"两次不同推进应各留 1 条，实得 {len(tl)}"


# ═══════════════════════ 3. respond_threads cluster 级幂等 ═══════════════════════

def test_respond_threads_responded_count_once_per_cluster():
    """同 cluster 4 章重放同一 thread_responded → responded_count == 1（非 4）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _setup_cluster_project(Path(d), range(1, 5))
        db = root / "_数据库"
        _wj(db / "世界状态.json", {
            "schema_version": "v20.1", "current_world_time": {"ch": 1},
            "active_npc_threads": [
                {"thread_id": "NT_001", "npc_id": "老登记员",
                 "expected_responses": 3, "outcome_if_complete": "真相浮现"}]})
        _wj(db / "涟漪规则.json", {"ripple_rules": []})
        results = [wac.respond_threads(root, ch, ["NT_001"]) for ch in (1, 2, 3, 4)]
        world = _load(root, "世界状态.json")
        t = next(t for t in world["active_npc_threads"] if t["thread_id"] == "NT_001")
        assert t["responded_count"] == 1, f"responded_count 期望 1，实得 {t['responded_count']} → N 倍污染未修"
        assert t["responded_by_cluster"] == ["cluster_001"]
        # 首章真响应，后 3 章幂等跳过
        assert results[0]["responded"] == ["NT_001"]
        assert results[1]["skipped_idempotent"] == ["NT_001"]
        assert results[2]["responded"] == []


def test_respond_threads_next_cluster_legit_increment():
    """下个 cluster 真正再呼应同一 thread → responded_count 合法 +1（不被跨 cluster 误幂等）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        _wj(db / "事件簇.json", {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 2]},
            {"cluster_id": "cluster_002", "chapter_range": [3, 4]},
        ]})
        _wj(db / "世界状态.json", {
            "schema_version": "v20.1", "current_world_time": {"ch": 1},
            "active_npc_threads": [
                {"thread_id": "NT_001", "npc_id": "老登记员",
                 "expected_responses": 3, "outcome_if_complete": "真相浮现"}]})
        _wj(db / "涟漪规则.json", {"ripple_rules": []})
        for ch in (1, 2):  # cluster_001 内重放 → 计 1 次
            wac.respond_threads(root, ch, ["NT_001"])
        for ch in (3, 4):  # cluster_002 内再呼应 → 合法计第 2 次
            wac.respond_threads(root, ch, ["NT_001"])
        world = _load(root, "世界状态.json")
        t = next(t for t in world["active_npc_threads"] if t["thread_id"] == "NT_001")
        assert t["responded_count"] == 2, f"两 cluster 各 1 次 = 2，实得 {t['responded_count']}"
        assert set(t["responded_by_cluster"]) == {"cluster_001", "cluster_002"}


def test_respond_threads_single_call_unchanged():
    """单章直跑（无 cluster 映射）行为不变 → responded_count == 1（向后兼容旧单测语义）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        _wj(db / "世界状态.json", {
            "schema_version": "v20.1", "active_npc_threads": [
                {"thread_id": "NT_001", "expected_responses": 2}]})
        _wj(db / "涟漪规则.json", {"ripple_rules": []})
        r = wac.respond_threads(root, 5, ["NT_001"])  # 无事件簇 → fallback 单章身份
        assert r["responded_count"] == 1
        assert r["completed"] == []


# ═══════════════════════ 4. cluster 级撒谎检测：lies == 真实违规数 ═══════════════════════

_OPEN_LINE = "他睁开眼时，天花板正在滴水。"
_END_LINE = "门外传来第二声敲门。"


def _build_truth_project(root: Path, anchors):
    """建 4 章 cluster：opening 在 ch1 / ending 在 ch4 / 锚点散落 ch2/ch3。"""
    _setup_cluster_project(root, range(1, 5))
    _write_body(root, 1, f"第001章 滴水\n{_OPEN_LINE}\n屋子里一片狼藉。\n")
    _write_body(root, 2, "第002章 钥匙\n他在抽屉底摸到一把铜钥匙。\n冰凉。\n")
    _write_body(root, 3, "第003章 痕\n墙上有一道焦痕，蜿蜒到天花板。\n")
    _write_body(root, 4, f"第004章 敲门\n他屏住呼吸。\n{_END_LINE}\n")
    applied_style = {
        "opening_type": "动作承接", "opening_line": _OPEN_LINE,
        "ending_type": "对话悬念", "ending_line": _END_LINE,
        "anchors_hit": anchors,
    }
    # 平铺：每章 _changes.json 都带同一份 applied_style（truth_check_cluster 读首章）
    for ch in (1, 2, 3, 4):
        _write_changes_json(root, ch, {"factual": {}, "self_eval": {"applied_style": applied_style}})


def test_cluster_truth_check_zero_lies_when_legit():
    """opening 验首章 / ending 验末章 / anchors（散落 ch2,ch3）验全拼接 → 0 撒谎。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _build_truth_project(root, anchors=["铜钥匙", "焦痕"])
        report = wtc.truth_check_cluster(root, [1, 2, 3, 4])
        assert report["lie_count"] == 0, f"合法 cluster 应 0 撒谎，实得 {report['lies_detected']}"
        assert report["opening_line_match"] is True
        assert report["ending_line_match"] is True
        assert all(a["in_body"] for a in report["anchors_truth"])


def test_cluster_truth_check_counts_only_real_violation():
    """1 个真不存在的 anchor → 恰好 1 条撒谎（lies == 真实违规数，无平铺假阳）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _build_truth_project(root, anchors=["铜钥匙", "幽灵船"])  # 幽灵船 全 cluster 不存在
        report = wtc.truth_check_cluster(root, [1, 2, 3, 4])
        assert report["lie_count"] == 1, f"期望恰 1 条（仅缺失 anchor），实得 {report['lies_detected']}"
        assert report["lies_detected"][0]["field"] == "anchors_hit"
        assert report["lies_detected"][0]["missing"] == ["幽灵船"]


def test_per_chapter_would_false_positive_but_cluster_clean():
    """证明逐章范式的假阳：ch2 单章验 opening_line（cluster 开篇）必不匹配；cluster 级则干净。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _build_truth_project(root, anchors=["铜钥匙", "焦痕"])
        # 旧逐章范式：拿 cluster 开篇 opening_line 去验 ch2 章首 → 假 opening_line_match=False
        per_ch2 = wtc.truth_check_chapter(root, 2)
        assert per_ch2["opening_line_match"] is False  # 这正是 C11 要消除的逐章误报
        # cluster 级：opening 只对 ch1 验 → 干净
        cluster_rep = wtc.truth_check_cluster(root, [1, 2, 3, 4])
        assert cluster_rep["opening_line_match"] is True
        assert cluster_rep["lie_count"] == 0


# ═══════════════════════ 5. db_schema_validate 幂等不变量（advisory） ═══════════════════════

def test_invariant_flags_growth_arc_pollution_advisory():
    """污染的 growth_arc（同 _source_cluster 同事件 2 条）→ 触发 advisory warning。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        _wj(db / "人物卡.json", {"characters": [{"name": "林潜", "growth_arc": [
            {"ch": 1, "key_change": "心境: A → B", "trigger": "t", "_source_cluster": "cluster_001"},
            {"ch": 2, "key_change": "心境: A → B", "trigger": "t", "_source_cluster": "cluster_001"},
        ]}]})
        warns = dsv.check_idempotency_invariants(db)
        assert any("IDEMPOTENCY_GROWTH_ARC" in w for w in warns), warns


def test_invariant_flags_responded_count_pollution():
    """responded_count(3) > 去重 cluster 数(1) → 触发 advisory。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        _wj(db / "世界状态.json", {"active_npc_threads": [
            {"thread_id": "NT_001", "responded_count": 3, "responded_by_cluster": ["cluster_001"]}]})
        warns = dsv.check_idempotency_invariants(db)
        assert any("IDEMPOTENCY_RESPONDED_COUNT" in w for w in warns), warns


def test_invariant_clean_data_no_warning():
    """干净库（每 cluster 1 条 / responded_count==cluster 数）→ 无幂等 warning。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        _wj(db / "人物卡.json", {"characters": [{"name": "林潜", "growth_arc": [
            {"ch": 1, "key_change": "心境: A → B", "trigger": "t", "_source_cluster": "cluster_001"},
            {"ch": 3, "key_change": "心境: B → C", "trigger": "t", "_source_cluster": "cluster_002"},
        ]}]})
        _wj(db / "时间线.json", {"time_log": [
            {"ch": 1, "elapsed": "一日", "key_events": ["x"], "_source_cluster": "cluster_001"}]})
        _wj(db / "世界状态.json", {"active_npc_threads": [
            {"thread_id": "NT_001", "responded_count": 2,
             "responded_by_cluster": ["cluster_001", "cluster_002"]}]})
        warns = dsv.check_idempotency_invariants(db)
        assert not any("IDEMPOTENCY_" in w for w in warns), f"干净库不应报幂等 warning：{warns}"


def _scaffold_min_valid_db(db: Path):
    """建齐 13 schema 规则要求的最小合法 JSON（collection 类型正确 → 0 schema 错误）。

    隔离不变量测试：让退出码只受 schema errors 影响，从而验证 advisory 不变量不翻 exit。
    """
    db.mkdir(parents=True, exist_ok=True)
    files = {
        "人物卡.json": {"schema_version": "v2", "characters": []},
        "伏笔表.json": {"schema_version": "v2", "promises": []},
        "故事块摘要.json": {"schema_version": "v2", "clusters": []},
        "进度.json": {"schema_version": "v2", "book_title": "X", "current_cluster": "cluster_001",
                      "cluster_blueprint": {}},
        "场景规则.json": {"schema_version": "v2", "scene_types": {}},
        "写作经验.json": {"schema_version": "v2", "success_patterns": []},
        "地图.json": {"schema_version": "v2", "locations": []},
        "关系.json": {"schema_version": "v2", "relationships": []},
        "事件表.json": {"schema_version": "v2", "pending_events": []},
        "时间线.json": {"schema_version": "v2", "world_clock_events": [], "time_log": []},
        "道具.json": {"schema_version": "v2", "items": []},
    }
    for name, data in files.items():
        _wj(db / name, data)


def test_invariant_advisory_does_not_change_exit_code():
    """污染数据 + 零 schema 错误 + --strict → CLI 仍退出 0（advisory 不变量不翻 exit 语义）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        _scaffold_min_valid_db(db)
        # 注入幂等污染：responded_count 远超去重 cluster 数
        _wj(db / "世界状态.json", {"active_npc_threads": [
            {"thread_id": "NT_001", "responded_count": 9, "responded_by_cluster": ["cluster_001"]}]})
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        cp = subprocess.run(
            [sys.executable, str(_SCRIPTS / "db_schema_validate.py"), str(root), "--strict"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        out = (cp.stdout or "") + (cp.stderr or "")
        assert cp.returncode == 0, f"advisory invariant must not flip exit code (got rc={cp.returncode})"
        assert "IDEMPOTENCY_RESPONDED_COUNT" in out
        assert "[错误]" not in out, "should have zero schema errors so exit code isolates the advisory"


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
