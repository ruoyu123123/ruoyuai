"""clock_engine 专属回归测试 — 锁核心确定性逻辑。

与 tests/test_engine_schema_compat.py（已覆盖 list_active 四套 schema + tick_chapter 不机械推进
+ dashboard 计数）**正交不重复**。本文件聚焦尚未覆盖的核心逻辑：

  · tick_event 显式事件触发（minor_event:glob 推进 + 满格写 status=triggered）
  · spawn 动态创建（自动 ID 递增 / 文件不存在自初始化 / since_cluster 反查兜底）
  · _match_tick_on glob 匹配语义（chapter_end / event_type:pattern）
  · _clock_progress 三方向归一（up / countdown down / max<=0 兜底 99）
  · _write_back_progress 倒计时回写 current 递减
  · list_active urgency 三档边界（urgent / approaching / normal）
  · 错误兜底（时钟表.json 不存在 → error）
  · CLI main() 退出码（满格 1 / 健康 0 / 缺参 2）

零依赖标准库测试约定：无参数 test_*，断言失败 raise AssertionError。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import clock_engine as ce  # noqa: E402

_SCRIPT_PATH = str(_SCRIPTS / "clock_engine.py")


def _mk_project(tmp: Path, data: dict | None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if data is not None:
        (db / "时钟表.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tmp


def _read_clocks(root: Path) -> dict:
    return json.loads((root / "_数据库" / "时钟表.json").read_text(encoding="utf-8"))


# ═══════════════════════════════ _match_tick_on glob ═══════════════════════════════

def test_match_tick_on_exact_and_glob():
    """tick_on 匹配：精确 event_type 命中 / event_type:glob 用 fnmatch 匹配 value / 不匹配返回 False。"""
    assert ce._match_tick_on(["chapter_end"], "chapter_end") is True
    assert ce._match_tick_on(["minor_event:fight*"], "minor_event", "fight_boss") is True
    assert ce._match_tick_on(["minor_event:fight*"], "minor_event", "talk_scene") is False
    # event_type 段不匹配（glob 的左半 type 不等）→ 即便 value 像也不命中
    assert ce._match_tick_on(["minor_event:fight*"], "fate_event", "fight_boss") is False
    # 精确名 fate_event:E1（value 当字面 glob）
    assert ce._match_tick_on(["fate_event:E1"], "fate_event", "E1") is True
    assert ce._match_tick_on([], "chapter_end") is False


# ═══════════════════════════════ _clock_progress 三方向归一 ═══════════════════════════════

def test_clock_progress_up_direction():
    """正计时 v21 ticks/max → (ticks, max, "up")。"""
    assert ce._clock_progress({"ticks": 2, "max": 10}) == (2, 10, "up")
    # 字段缺省回退：current_segments → current → 0
    assert ce._clock_progress({"current_segments": 3, "max_segments": 18}) == (3, 18, "up")
    assert ce._clock_progress({"current": 4, "target": 8}) == (4, 8, "up")


def test_clock_progress_countdown_direction():
    """倒计时（trigger_at_zero）→ elapsed=initial-current 升到 initial，direction="down"。"""
    # initial=7 current=1 → elapsed=6 满格距 max=7 仅 1
    assert ce._clock_progress({"initial": 7, "current": 1, "trigger_at_zero": "x"}) == (6, 7, "down")
    # initial=12 current=12 → elapsed=0（远未触发）
    assert ce._clock_progress({"initial": 12, "current": 12, "trigger_at_zero": "y"}) == (0, 12, "down")


def test_clock_progress_max_zero_fallback_99():
    """max <= 0 兜底 99 防除零。"""
    ticks, max_v, direction = ce._clock_progress({"current": 0, "target": 0})
    assert max_v == 99 and direction == "up"


# ═══════════════════════════════ _write_back_progress 倒计时回写 ═══════════════════════════════

def test_write_back_progress_countdown_decrements_current():
    """倒计时回写：new_ticks 是 elapsed，current = max - elapsed（递减语义）。"""
    c = {"id": "c1", "initial": 7, "current": 7, "trigger_at_zero": "boom"}
    # elapsed=2 / max=7 → current 回写 5
    ce._write_back_progress(c, 2, 7, "down")
    assert c["current"] == 5
    # elapsed 超 max → 钳到 0 不为负
    ce._write_back_progress(c, 99, 7, "down")
    assert c["current"] == 0


def test_write_back_progress_up_writes_correct_field():
    """正计时回写选对字段：有 ticks 写 ticks / 否则 current_segments / 否则 current。"""
    c1 = {"ticks": 0, "max": 5}
    ce._write_back_progress(c1, 3, 5, "up")
    assert c1["ticks"] == 3
    c2 = {"current_segments": 0, "max_segments": 18}
    ce._write_back_progress(c2, 4, 18, "up")
    assert c2["current_segments"] == 4
    c3 = {"current": 0, "target": 8}  # 诡异 story_clock
    ce._write_back_progress(c3, 2, 8, "up")
    assert c3["current"] == 2


# ═══════════════════════════════ tick_event 显式事件触发 ═══════════════════════════════

def test_tick_event_glob_advances_and_triggers():
    """tick_event 按 tick_on=minor_event:glob 推进；满格写 status=triggered 并返回 triggered。"""
    data = {"clocks": [{
        "clock_id": "CK_001", "label": "反派耐心", "ticks": 0, "max": 2,
        "tick_on": ["minor_event:fight*"], "tick_per_event": 1, "status": "active",
        "trigger_on_max": "ME_X", "category": "antagonist",
    }]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        r1 = ce.tick_event(root, 5, "minor_event", "fight_boss")
        assert len(r1["ticked"]) == 1
        assert r1["ticked"][0]["new"] == 1
        assert r1["triggered"] == []  # 1/2 未满
        after1 = _read_clocks(root)
        assert after1["clocks"][0]["ticks"] == 1
        assert after1["clocks"][0].get("status") == "active"
        # 再触发一次 → 满 2/2
        r2 = ce.tick_event(root, 6, "minor_event", "fight_boss")
        assert len(r2["triggered"]) == 1
        assert r2["triggered"][0]["clock_id"] == "CK_001"
        assert r2["triggered"][0]["trigger_on_max"] == "ME_X"
        assert r2["triggered"][0]["category"] == "antagonist"
        after2 = _read_clocks(root)
        assert after2["clocks"][0]["status"] == "triggered"
        assert after2["clocks"][0]["triggered_at_ch"] == 6


def test_tick_event_non_matching_value_does_not_advance():
    """tick_on 的 glob 不匹配事件 value → 不推进。"""
    data = {"clocks": [{
        "clock_id": "CK_001", "label": "x", "ticks": 0, "max": 5,
        "tick_on": ["minor_event:duel*"], "tick_per_event": 1, "status": "active",
    }]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        r = ce.tick_event(root, 1, "minor_event", "chitchat")
        assert r["ticked"] == [] and r["triggered"] == []
        assert _read_clocks(root)["clocks"][0]["ticks"] == 0


def test_tick_event_countdown_writes_back_decrement():
    """显式 tick_on 的倒计时 clock：tick_per_event=2 → current 递减 2。"""
    data = {"clocks": [{
        "id": "c1", "name": "倒计时", "initial": 5, "current": 5,
        "trigger_at_zero": "boom", "tick_on": ["chapter_end"], "tick_per_event": 2, "status": "active",
    }]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        r = ce.tick_chapter(root, 1)
        assert len(r["ticked"]) == 1
        # elapsed 0→2，max=5，remaining=3
        assert r["ticked"][0]["remaining"] == 3
        assert _read_clocks(root)["clocks"][0]["current"] == 3  # 5 - 2


# ═══════════════════════════════ spawn 动态创建 ═══════════════════════════════

def test_spawn_auto_increments_id_and_init_file():
    """spawn 文件不存在自初始化；clock_id 自动 CK_001、CK_002 递增；since_cluster 兜底 cluster_NNN。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)  # 故意不建任何文件
        r1 = ce.spawn(root, 3, {"label": "首个钟", "max": 5})
        assert r1["created"] == "CK_001"
        assert r1["label"] == "首个钟"
        after1 = _read_clocks(root)
        assert [c["clock_id"] for c in after1["clocks"]] == ["CK_001"]
        ck = after1["clocks"][0]
        # 默认字段落齐
        assert ck["max"] == 5 and ck["ticks"] == 0
        assert ck["tick_on"] == ["chapter_end"]
        assert ck["status"] == "active"
        # cluster_lookup 无 事件簇.json → 反查失败兜底 cluster_003（章号 3）
        assert ck["since_cluster"] == "cluster_003"
        # 第二个 → CK_002
        r2 = ce.spawn(root, 4, {"label": "第二个"})
        assert r2["created"] == "CK_002"
        after2 = _read_clocks(root)
        assert [c["clock_id"] for c in after2["clocks"]] == ["CK_001", "CK_002"]


def test_spawn_skips_non_ck_ids_when_numbering():
    """已有非 CK_ 前缀 clock 存在时，自动编号忽略它们，从 CK_001 起。"""
    data = {"clocks": [{"clock_id": "clk_legacy", "label": "旧", "ticks": 0, "max": 3}]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        r = ce.spawn(root, 1, {"label": "新增"})
        assert r["created"] == "CK_001"  # clk_legacy 不参与编号
        ids = [c["clock_id"] for c in _read_clocks(root)["clocks"]]
        assert ids == ["clk_legacy", "CK_001"]


# ═══════════════════════════════ list_active urgency 三档边界 ═══════════════════════════════

def test_list_active_urgency_boundaries():
    """urgency：remaining<=2 urgent / remaining<=max*0.3 approaching / 否则 normal。"""
    data = {"clocks": [
        {"clock_id": "CK_U", "label": "u", "ticks": 9, "max": 10, "status": "active"},  # rem 1 → urgent
        {"clock_id": "CK_A", "label": "a", "ticks": 7, "max": 10, "status": "active"},  # rem 3 = 10*0.3 → approaching
        {"clock_id": "CK_N", "label": "n", "ticks": 0, "max": 10, "status": "active"},  # rem 10 → normal
    ]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        r = ce.list_active(root, 1)
        by_id = {c["clock_id"]: c for c in r["active_clocks"]}
        assert by_id["CK_U"]["urgency"] == "urgent"
        assert by_id["CK_A"]["urgency"] == "approaching"
        assert by_id["CK_N"]["urgency"] == "normal"
        # 排序：remaining 升序 → urgent(1) 在最前
        assert r["active_clocks"][0]["clock_id"] == "CK_U"
        assert r["total_active"] == 3


def test_list_active_surfaces_visible_to_writer_field():
    """🔴 2026-06-28 写手信息隔离：list_active 透出 visible_to_writer / is_surprise。
    无字段的旧时钟默认 visible_to_writer=True（明线·向后兼容）；显式暗线时钟原样透出。"""
    data = {"clocks": [
        # 旧时钟无 visible_to_writer 字段 → 默认 True（明线）
        {"clock_id": "CK_OLD", "label": "明线", "ticks": 1, "max": 5, "status": "active"},
        # 显式暗线时钟（producer 标记）
        {"clock_id": "CK_DARK", "label": "暗线", "ticks": 1, "max": 5, "status": "active",
         "visible_to_writer": False, "is_surprise": True, "trigger_on_max": "ME_SECRET"},
    ]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        r = ce.list_active(root, 1)
        by_id = {c["clock_id"]: c for c in r["active_clocks"]}
        # 旧时钟：默认 True 向后兼容
        assert by_id["CK_OLD"]["visible_to_writer"] is True
        assert by_id["CK_OLD"]["is_surprise"] is False
        # 暗线时钟：原样透出供 build_manifest 过滤 trigger_on_max
        assert by_id["CK_DARK"]["visible_to_writer"] is False
        assert by_id["CK_DARK"]["is_surprise"] is True


def test_spawn_writes_visibility_fields():
    """🔴 2026-06-28 写手信息隔离：spawn 默认明线（visible_to_writer=True / is_surprise=False）；
    producer 显式标暗线时可写入。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        ce.spawn(root, 1, {"label": "默认明线", "max": 5})
        ce.spawn(root, 1, {"label": "暗线惊喜", "max": 5,
                           "visible_to_writer": False, "is_surprise": True})
        clocks = {c["label"]: c for c in _read_clocks(root)["clocks"]}
        assert clocks["默认明线"]["visible_to_writer"] is True
        assert clocks["默认明线"]["is_surprise"] is False
        assert clocks["暗线惊喜"]["visible_to_writer"] is False
        assert clocks["暗线惊喜"]["is_surprise"] is True


def test_list_active_excludes_triggered_status():
    """status=triggered 的 clock 非 active → 不进 list_active。"""
    data = {"clocks": [
        {"clock_id": "CK_A", "label": "a", "ticks": 1, "max": 5, "status": "active"},
        {"clock_id": "CK_T", "label": "t", "ticks": 5, "max": 5, "status": "triggered"},
    ]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        r = ce.list_active(root, 1)
        ids = [c["clock_id"] for c in r["active_clocks"]]
        assert ids == ["CK_A"]
        assert r["total_active"] == 1


# ═══════════════════════════════ 错误兜底 ═══════════════════════════════

def test_missing_clock_file_returns_error():
    """时钟表.json 不存在 → tick/tick_event/list/dashboard 返回 error（不崩）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)  # 无文件
        assert "error" in ce.tick_chapter(root, 1)
        assert "error" in ce.tick_event(root, 1, "minor_event", "x")
        assert "error" in ce.list_active(root, 1)
        assert "error" in ce.dashboard(root)


# ═══════════════════════════════ CLI main() 退出码 ═══════════════════════════════

def test_cli_exit_code_triggered_is_1():
    """CLI tick 让 clock 满格 → 退出码 1（有 clock 触发）；dashboard → 0；缺 ch → 2。"""
    data = {"clocks": [{
        "clock_id": "CK_001", "label": "a", "ticks": 1, "max": 2,
        "tick_on": ["chapter_end"], "tick_per_event": 1, "status": "active", "trigger_on_max": "ME_X",
    }]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        # tick 让 1→2 满格 → 退出码 1
        p = subprocess.run([sys.executable, _SCRIPT_PATH, str(root), "tick", "1"],
                           capture_output=True, text=True)
        assert p.returncode == 1, p.stderr
        # dashboard → 退出码 0
        p = subprocess.run([sys.executable, _SCRIPT_PATH, str(root), "dashboard"],
                           capture_output=True, text=True)
        assert p.returncode == 0
        # tick 缺 ch → 退出码 2
        p = subprocess.run([sys.executable, _SCRIPT_PATH, str(root), "tick"],
                           capture_output=True, text=True)
        assert p.returncode == 2
        # spawn 缺 --json → 退出码 2
        p = subprocess.run([sys.executable, _SCRIPT_PATH, str(root), "spawn", "1"],
                           capture_output=True, text=True)
        assert p.returncode == 2


def test_cli_exit_code_healthy_is_0():
    """CLI tick 未触发任何 clock → 退出码 0（健康）。"""
    data = {"clocks": [{
        "clock_id": "CK_001", "label": "a", "ticks": 0, "max": 10,
        "tick_on": ["chapter_end"], "tick_per_event": 1, "status": "active", "trigger_on_max": "ME_X",
    }]}
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), data)
        p = subprocess.run([sys.executable, _SCRIPT_PATH, str(root), "tick", "1"],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        p = subprocess.run([sys.executable, _SCRIPT_PATH, str(root), "list", "1"],
                           capture_output=True, text=True)
        assert p.returncode == 0
